"""Unit tests for milestone 11's opportunistic MCP progress notifications
(docs/tasklist.md item 11) -- the token-capture/attach/dedup/failure-isolation
wiring in `octobot_mcp.jobs`/`octobot_mcp.tools.backtesting`.

These tests use `make_ctx` (extended for this milestone with optional
`progress_token`/`session` parameters -- see `tests/test_tools_shared.py`)
and a small `_FakeSession` fake matching the one `ServerSession` method this
project's code actually calls (`send_progress_notification`), the same
convention this file's sibling `test_tools_backtesting.py` already
established for `_FakeSocketIOClient`/`httpx.MockTransport`. They verify
this project's OWN decision logic (token extraction, dedup, combined-progress
computation, failure isolation) in isolation from the real MCP wire protocol.

Whether the SDK itself actually DELIVERS a notification sent this way to a
real client -- the harder, protocol-level question this milestone's own
"not a bespoke mock" verification requirement is about -- is covered
separately in `tests/test_server_progress_notifications_integration.py`,
which drives real tool calls through a real `mcp.Client`/`MCPServer` pair and
asserts on the actual `ProgressNotification` messages that arrive.
"""

import asyncio
from typing import Any

import httpx2 as httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from octobot_mcp.jobs import JobStore
from octobot_mcp.tools.backtesting import (
    _build_progress_notifier,
    _run_backtest_watcher,
    _run_compare_strategies,
    compare_strategies,
    get_job_result,
    start_backtest,
)
from octobot_mcp.tools.jobs import get_job_status
from tests.test_tools_backtesting import _FakeSocketIOClient, _factory, _sequential_factory
from tests.test_tools_shared import make_ctx


@pytest.fixture
def anyio_backend():
    return "asyncio"


class _FakeSession:
    """Fake matching the one `ServerSession` method this project's code
    actually calls: `send_progress_notification(progress_token, progress,
    total, message, related_request_id=None)`.

    `always_raise=True` simulates any real-world send failure (a
    disconnected client, an SDK-level error, ...) -- this project's own
    `_build_progress_notifier` must swallow it unconditionally (milestone
    11's own explicit "never fail the job/watcher over this" requirement).
    """

    def __init__(self, *, always_raise: bool = False) -> None:
        self.calls: list[tuple[Any, float, float | None, str | None]] = []
        self._always_raise = always_raise

    async def send_progress_notification(
        self,
        progress_token: Any,
        progress: float,
        total: float | None = None,
        message: str | None = None,
        related_request_id: Any = None,
    ) -> None:
        if self._always_raise:
            raise RuntimeError("simulated notification-send failure (client disconnected, SDK error, ...)")
        self.calls.append((progress_token, progress, total, message))


def _always_raising_http_handler(request: httpx.Request) -> httpx.Response:
    raise AssertionError("no OctoBot call is expected before a background watcher task even starts")


# --- _build_progress_notifier: the notifier itself is best-effort ------------------


@pytest.mark.anyio
async def test_build_progress_notifier_sends_via_the_captured_session_and_token():
    session = _FakeSession()
    ctx = make_ctx(httpx.MockTransport(_always_raising_http_handler), progress_token="tok-1", session=session)

    notifier = _build_progress_notifier(ctx, "tok-1")
    await notifier(42.0, 100.0, "42.0% complete")

    assert session.calls == [("tok-1", 42.0, 100.0, "42.0% complete")]


@pytest.mark.anyio
async def test_build_progress_notifier_swallows_send_failures():
    session = _FakeSession(always_raise=True)
    ctx = make_ctx(httpx.MockTransport(_always_raising_http_handler), progress_token="tok-1", session=session)

    notifier = _build_progress_notifier(ctx, "tok-1")
    await notifier(42.0, 100.0, "42.0% complete")  # must not raise


# --- start_backtest: attaching the token/notifier (or not) ------------------------


@pytest.mark.anyio
async def test_start_backtest_attaches_progress_token_and_notifier_when_supplied():
    session = _FakeSession()
    ctx = make_ctx(httpx.MockTransport(_always_raising_http_handler), progress_token="tok-abc", session=session)
    store = ctx.request_context.lifespan_context.jobs

    result = await start_backtest(ctx, mode="data_files", files=["a.data"])

    job = store.get(result["job_id"])
    assert job.progress_token == "tok-abc"
    assert job.progress_notifier is not None

    # Let the background watcher run to completion (it fails fast: the mock
    # transport always raises) rather than leaving it dangling.
    task = store._watcher_tasks.get(result["job_id"])
    if task is not None:
        await asyncio.wait_for(task, timeout=5)


@pytest.mark.anyio
async def test_start_backtest_without_progress_token_leaves_notifier_unset():
    ctx = make_ctx(httpx.MockTransport(_always_raising_http_handler))
    store = ctx.request_context.lifespan_context.jobs

    result = await start_backtest(ctx, mode="data_files", files=["a.data"])

    job = store.get(result["job_id"])
    assert job.progress_token is None
    assert job.progress_notifier is None

    task = store._watcher_tasks.get(result["job_id"])
    if task is not None:
        await asyncio.wait_for(task, timeout=5)


# --- start_backtest end-to-end: notifications fire as progress changes, deduped ---


@pytest.mark.anyio
async def test_start_backtest_sends_notifications_as_progress_changes_and_dedups(monkeypatch):
    import octobot_mcp.tools.backtesting as backtesting_module

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json="Backtesting started")
        return httpx.Response(200, json={"report": {}, "trades": []})

    fake_sio = _FakeSocketIOClient(
        [
            {"status": "computing", "progress": 10, "errors": 0},
            {"status": "computing", "progress": 10, "errors": 0},  # unchanged -- must not renotify
            {"status": "computing", "progress": 55, "errors": 0},
            {"status": "finished", "progress": 100, "errors": 0},
        ]
    )
    real_run_backtest_watcher = backtesting_module._run_backtest_watcher

    async def _fast_run_backtest_watcher(client, store, job_id, spec, **kwargs):
        kwargs.setdefault("socketio_client_factory", _factory(fake_sio))
        kwargs["interval_seconds"] = 0
        return await real_run_backtest_watcher(client, store, job_id, spec, **kwargs)

    monkeypatch.setattr(backtesting_module, "_run_backtest_watcher", _fast_run_backtest_watcher)

    session = _FakeSession()
    ctx = make_ctx(httpx.MockTransport(handler), progress_token="tok-1", session=session)
    store = ctx.request_context.lifespan_context.jobs

    result = await start_backtest(ctx, mode="data_files", files=["a.data"])
    task = store._watcher_tasks.get(result["job_id"])
    assert task is not None
    await asyncio.wait_for(task, timeout=5)

    assert store.get(result["job_id"]).state == "completed"
    # Exactly 3 notifications -- the duplicate 10 -> 10 tick never renotifies.
    assert [call[:3] for call in session.calls] == [
        ("tok-1", 10.0, 100.0),
        ("tok-1", 55.0, 100.0),
        ("tok-1", 100.0, 100.0),
    ]
    assert session.calls[0][3] == "10.0% complete"


@pytest.mark.anyio
async def test_start_backtest_without_token_sends_no_notifications_but_still_completes(monkeypatch):
    import octobot_mcp.tools.backtesting as backtesting_module

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json="Backtesting started")
        return httpx.Response(200, json={"report": {}, "trades": []})

    fake_sio = _FakeSocketIOClient(
        [
            {"status": "computing", "progress": 10, "errors": 0},
            {"status": "finished", "progress": 100, "errors": 0},
        ]
    )
    real_run_backtest_watcher = backtesting_module._run_backtest_watcher

    async def _fast_run_backtest_watcher(client, store, job_id, spec, **kwargs):
        kwargs.setdefault("socketio_client_factory", _factory(fake_sio))
        kwargs["interval_seconds"] = 0
        return await real_run_backtest_watcher(client, store, job_id, spec, **kwargs)

    monkeypatch.setattr(backtesting_module, "_run_backtest_watcher", _fast_run_backtest_watcher)

    # No progress_token/session at all -- the common case. If the watcher's
    # on_tick hook ever touched ctx.session without a token, this would
    # blow up (make_ctx's fake session defaults to None); it must not.
    ctx = make_ctx(httpx.MockTransport(handler))
    store = ctx.request_context.lifespan_context.jobs

    result = await start_backtest(ctx, mode="data_files", files=["a.data"])
    task = store._watcher_tasks.get(result["job_id"])
    assert task is not None
    await asyncio.wait_for(task, timeout=5)

    completed = store.get(result["job_id"])
    assert completed.state == "completed"
    assert completed.progress_percent == 100.0

    # Polling is completely unaffected by the absence of a token.
    status = await get_job_status(make_ctx(httpx.MockTransport(handler), jobs=store), job_id=result["job_id"])
    assert status["state"] == "completed"
    assert status["progress_percent"] == 100.0


# --- failure isolation: a notification-send failure never fails the job/watcher ---


@pytest.mark.anyio
async def test_notification_send_failure_never_fails_the_job_and_polling_still_works(monkeypatch):
    import octobot_mcp.tools.backtesting as backtesting_module

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json="Backtesting started")
        return httpx.Response(200, json={"report": {}, "trades": []})

    fake_sio = _FakeSocketIOClient(
        [
            {"status": "computing", "progress": 10, "errors": 0},
            {"status": "computing", "progress": 55, "errors": 0},
            {"status": "finished", "progress": 100, "errors": 0},
        ]
    )
    real_run_backtest_watcher = backtesting_module._run_backtest_watcher

    async def _fast_run_backtest_watcher(client, store, job_id, spec, **kwargs):
        kwargs.setdefault("socketio_client_factory", _factory(fake_sio))
        kwargs["interval_seconds"] = 0
        return await real_run_backtest_watcher(client, store, job_id, spec, **kwargs)

    monkeypatch.setattr(backtesting_module, "_run_backtest_watcher", _fast_run_backtest_watcher)

    session = _FakeSession(always_raise=True)
    ctx = make_ctx(httpx.MockTransport(handler), progress_token="tok-1", session=session)
    store = ctx.request_context.lifespan_context.jobs

    result = await start_backtest(ctx, mode="data_files", files=["a.data"])
    task = store._watcher_tasks.get(result["job_id"])
    assert task is not None
    await asyncio.wait_for(task, timeout=5)  # must not raise, and must not hang

    completed = store.get(result["job_id"])
    assert completed.state == "completed"
    assert completed.progress_percent == 100.0
    assert session.calls == []  # every attempted send raised -- none recorded

    # get_job_status polling is completely unaffected by every notification
    # attempt having failed.
    status = await get_job_status(make_ctx(httpx.MockTransport(handler), jobs=store), job_id=result["job_id"])
    assert status["state"] == "completed"


# --- compare_strategies: parent notifications report COMBINED progress ------------


@pytest.mark.anyio
async def test_compare_strategies_notifies_parent_with_combined_progress_not_raw_child_progress(monkeypatch):
    import octobot_mcp.tools.backtesting as backtesting_module

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json="Backtesting started")
        return httpx.Response(200, json={"report": {}, "trades": []})

    fakes = [
        _FakeSocketIOClient(
            [
                {"status": "computing", "progress": 40, "errors": 0},
                {"status": "finished", "progress": 100, "errors": 0},
            ]
        ),
        _FakeSocketIOClient([{"status": "finished", "progress": 100, "errors": 0}]),
    ]
    real_run_compare_strategies = backtesting_module._run_compare_strategies

    async def _fast_run_compare_strategies(client, store, parent_id, child_ids, configs, specs, **kwargs):
        kwargs.setdefault("socketio_client_factory", _sequential_factory(fakes))
        kwargs["interval_seconds"] = 0
        return await real_run_compare_strategies(client, store, parent_id, child_ids, configs, specs, **kwargs)

    monkeypatch.setattr(backtesting_module, "_run_compare_strategies", _fast_run_compare_strategies)

    session = _FakeSession()
    ctx = make_ctx(httpx.MockTransport(handler), progress_token="tok-parent", session=session)
    store = ctx.request_context.lifespan_context.jobs

    result = await compare_strategies(
        ctx,
        configs=[
            {"mode": "data_files", "files": ["a.data"]},
            {"mode": "data_files", "files": ["b.data"]},
        ],
    )
    task = store._watcher_tasks.get(result["job_id"])
    assert task is not None
    await asyncio.wait_for(task, timeout=5)

    assert store.get(result["job_id"]).state == "completed"

    progresses = [call[1] for call in session.calls]
    # Combined progress formula: ((completed_children * 100) + current_child_progress) / total_children.
    # child-1 @ 40 (0 completed):   (0 + 40) / 2  = 20.0
    # child-1 @ 100, still "running" at hook time (0 completed): (0 + 100) / 2 = 50.0
    # child-2 @ 100, finished, child-1 now completed (1 completed): (100 + 100) / 2 = 100.0
    assert progresses == [20.0, 50.0, 100.0]
    # The raw single-child percentage (40) is never what gets reported.
    assert 40.0 not in progresses
    assert all(call[0] == "tok-parent" for call in session.calls)
    assert all(call[2] == 100.0 for call in session.calls)
    assert "combined" in session.calls[0][3]


@pytest.mark.anyio
async def test_compare_strategies_without_progress_token_sends_no_notifications():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json="Backtesting started")
        return httpx.Response(200, json={"report": {}, "trades": []})

    client = httpx.AsyncClient(base_url="http://octobot.example", transport=httpx.MockTransport(handler))
    fakes = [
        _FakeSocketIOClient([{"status": "finished", "progress": 100, "errors": 0}]),
        _FakeSocketIOClient([{"status": "finished", "progress": 100, "errors": 0}]),
    ]
    configs = [
        {"mode": "data_files", "files": ["a.data"], "source": "child-1"},
        {"mode": "data_files", "files": ["b.data"], "source": "child-2"},
    ]
    from octobot_mcp.tools.backtesting import _data_files_mode_spec

    specs = [
        _data_files_mode_spec(
            files=c["files"],
            start_timestamp=None,
            end_timestamp=None,
            enable_logs=False,
            source=c["source"],
            auto_stop=False,
            run_on_common_part_only=True,
            reset_tentacle_config=False,
        )
        for c in configs
    ]
    store = JobStore()
    child_ids = [store.create_job("backtest").job_id for _ in specs]
    parent = store.create_job("compare_strategies", child_job_ids=child_ids)
    # No `set_progress_notifier` call at all -- exactly what happens when the
    # original `compare_strategies` call carried no `progressToken`.

    await _run_compare_strategies(
        client,
        store,
        parent.job_id,
        child_ids,
        configs,
        specs,
        socketio_client_factory=_sequential_factory(fakes),
        interval_seconds=0,
    )

    assert store.get(parent.job_id).state == "completed"
    result = await get_job_result(make_ctx(httpx.MockTransport(handler), jobs=store), job_id=parent.job_id)
    assert len(result["comparison"]) == 2


@pytest.mark.anyio
async def test_start_backtest_rejects_bad_input_without_touching_ctx_session_at_all():
    # A synchronous validation failure (unknown mode) must never even look
    # at ctx.session -- there is no job, and therefore nothing to notify.
    ctx = make_ctx(httpx.MockTransport(_always_raising_http_handler), progress_token="tok-1", session=None)

    with pytest.raises(ToolError):
        await start_backtest(ctx, mode="not_a_real_mode")
