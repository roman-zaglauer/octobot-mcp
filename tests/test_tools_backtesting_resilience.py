"""Unit tests for milestone 14's bounded watcher-loop resilience
(docs/tasklist.md item 14) -- `_watch_until_done`'s per-tick retry around
`channel.request_status()`, bounded by
`octobot_mcp.tools.backtesting.MAX_CONSECUTIVE_WATCHER_TICK_FAILURES`.

Reuses `_FakeSocketIOClient`/`_factory`/`_data_files_spec` from
`test_tools_backtesting.py` (this project's established convention, see
e.g. `test_tools_backtesting_progress_notifications.py`) and drives the
REAL `_run_backtest_watcher`/`_watch_until_done` code path throughout --
never a rewritten shortcut -- per this milestone's own explicit
instruction. `caplog` asserts on the spec's Observability section
"log line per watcher loop error (not just terminal failure)" requirement.
"""

import logging

import httpx2 as httpx
import pytest

from octobot_mcp.jobs import JobStore
from octobot_mcp.tools.backtesting import MAX_CONSECUTIVE_WATCHER_TICK_FAILURES, _run_backtest_watcher
from tests.test_tools_backtesting import _FakeSocketIOClient, _data_files_spec, _factory


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _start_and_report_handler(request: httpx.Request) -> httpx.Response:
    if request.method == "POST":
        return httpx.Response(200, json="Backtesting started")
    return httpx.Response(200, json={"report": {}, "trades": []})


@pytest.mark.anyio
async def test_one_transient_tick_failure_is_absorbed_and_the_job_still_completes(caplog):
    """Below the bound: a single hiccup is logged, retried, and the job
    recovers on the very next tick -- it never fails."""
    store = JobStore()
    job = store.create_job("backtest")
    client = httpx.AsyncClient(base_url="http://octobot.example", transport=httpx.MockTransport(_start_and_report_handler))
    fake_sio = _FakeSocketIOClient(
        [
            RuntimeError("simulated transient Socket.IO hiccup"),
            {"status": "finished", "progress": 100, "errors": 0},
        ]
    )

    with caplog.at_level(logging.WARNING, logger="octobot_mcp.tools.backtesting"):
        await _run_backtest_watcher(
            client,
            store,
            job.job_id,
            _data_files_spec(),
            socketio_client_factory=_factory(fake_sio),
            interval_seconds=0,
        )

    completed = store.get(job.job_id)
    assert completed.state == "completed"
    assert completed.errors == []  # the transient hiccup is logged, not recorded as a job error.
    assert store.backtest_execution_lock.locked() is False
    assert "simulated transient Socket.IO hiccup" in caplog.text
    assert "1/3" in caplog.text or f"1/{MAX_CONSECUTIVE_WATCHER_TICK_FAILURES}" in caplog.text


@pytest.mark.anyio
async def test_failures_just_under_the_bound_all_recover_and_the_job_completes(caplog):
    """Below the bound: MAX - 1 consecutive hiccups, still recovers."""
    store = JobStore()
    job = store.create_job("backtest")
    client = httpx.AsyncClient(base_url="http://octobot.example", transport=httpx.MockTransport(_start_and_report_handler))
    fake_sio = _FakeSocketIOClient(
        [RuntimeError("simulated transient hiccup")] * (MAX_CONSECUTIVE_WATCHER_TICK_FAILURES - 1)
        + [{"status": "finished", "progress": 100, "errors": 0}]
    )

    with caplog.at_level(logging.WARNING, logger="octobot_mcp.tools.backtesting"):
        await _run_backtest_watcher(
            client,
            store,
            job.job_id,
            _data_files_spec(),
            socketio_client_factory=_factory(fake_sio),
            interval_seconds=0,
        )

    completed = store.get(job.job_id)
    assert completed.state == "completed"
    assert store.backtest_execution_lock.locked() is False
    # One log line per failed tick.
    assert caplog.text.count("simulated transient hiccup") == MAX_CONSECUTIVE_WATCHER_TICK_FAILURES - 1


@pytest.mark.anyio
async def test_sustained_failures_at_the_bound_eventually_fail_the_job(caplog):
    """At/beyond the bound: a genuinely dead connection (every tick fails,
    never recovers) still eventually fails the job -- INV-1's own
    guarantee (lock released, job reaches a terminal state) is preserved,
    just no longer on the FIRST failure."""
    store = JobStore()
    job = store.create_job("backtest")
    client = httpx.AsyncClient(base_url="http://octobot.example", transport=httpx.MockTransport(_start_and_report_handler))
    fake_sio = _FakeSocketIOClient(
        [RuntimeError("simulated sustained Socket.IO outage")] * MAX_CONSECUTIVE_WATCHER_TICK_FAILURES
    )

    with caplog.at_level(logging.WARNING, logger="octobot_mcp.tools.backtesting"):
        await _run_backtest_watcher(
            client,
            store,
            job.job_id,
            _data_files_spec(),
            socketio_client_factory=_factory(fake_sio),
            interval_seconds=0,
        )

    failed = store.get(job.job_id)
    assert failed.state == "failed"
    assert "simulated sustained Socket.IO outage" in failed.errors[0]
    assert store.backtest_execution_lock.locked() is False
    # One log line per failed tick, including the final one that gave up.
    assert caplog.text.count("simulated sustained Socket.IO outage") == MAX_CONSECUTIVE_WATCHER_TICK_FAILURES


@pytest.mark.anyio
async def test_a_successful_tick_resets_the_consecutive_failure_counter():
    """MAX - 1 failures, then a success, then MAX - 1 failures again, then a
    success -- never crosses the bound at any point because each success
    resets the counter, so the job still completes."""
    store = JobStore()
    job = store.create_job("backtest")
    client = httpx.AsyncClient(base_url="http://octobot.example", transport=httpx.MockTransport(_start_and_report_handler))
    below_bound = MAX_CONSECUTIVE_WATCHER_TICK_FAILURES - 1
    responses = (
        [RuntimeError("hiccup batch 1") for _ in range(below_bound)]
        + [{"status": "computing", "progress": 50, "errors": 0}]
        + [RuntimeError("hiccup batch 2") for _ in range(below_bound)]
        + [{"status": "finished", "progress": 100, "errors": 0}]
    )
    fake_sio = _FakeSocketIOClient(responses)

    await _run_backtest_watcher(
        client,
        store,
        job.job_id,
        _data_files_spec(),
        socketio_client_factory=_factory(fake_sio),
        interval_seconds=0,
    )

    completed = store.get(job.job_id)
    assert completed.state == "completed"
    assert store.backtest_execution_lock.locked() is False
