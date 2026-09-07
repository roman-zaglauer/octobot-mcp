"""Unit tests for the backtest watcher + `start_backtest`/`get_job_result` (milestone 8).

The HTTP layer is mocked via `httpx2.MockTransport` (same convention as
every other tool test in this project); the Socket.IO layer is mocked via
`_FakeSocketIOClient` below, a minimal stand-in matching the exact
`socketio.AsyncClient` surface `octobot_mcp.tools.backtesting` actually
calls (`on`/`connect`/`emit`/`disconnect`) -- no real network/Socket.IO
connection is ever made in this file. `interval_seconds=0` is passed to
`_run_backtest_watcher` throughout so these tests run fast regardless of
the real `WATCHER_INTERVAL_SECONDS` (1.5s) constant.
"""

import asyncio
import json
from typing import Any

import httpx2 as httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from octobot_mcp.jobs import JobStore
from octobot_mcp.tools.backtesting import (
    MAX_CONSECUTIVE_WATCHER_TICK_FAILURES,
    _StartSpec,
    _build_diff,
    _current_bot_data_mode_spec,
    _data_files_mode_spec,
    _run_backtest_watcher,
    _run_compare_strategies,
    compare_strategies,
    get_job_result,
    start_backtest,
)
from octobot_mcp.tools.jobs import get_job_status
from tests.test_tools_shared import make_ctx


@pytest.fixture
def anyio_backend():
    return "asyncio"


class _FakeSocketIOClient:
    """Minimal fake matching the `on`/`connect`/`emit`/`disconnect` surface
    `_BacktestingStatusChannel`/`_watch_until_done` actually call.

    `responses` is a list consumed one-per-`emit` call: each item is either
    a plain dict (the `backtesting_status` payload the fake "server"
    replies with, delivered synchronously to the registered handler before
    `emit` returns) or a zero-arg callable returning one (so a test can run
    a side effect, e.g. simulating a concurrent `cancel_job` call, exactly
    when a particular tick's request is made) or an `Exception` instance
    (raised from `emit` itself, simulating a hard Socket.IO-layer failure).
    """

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self._handler = None
        self.connected = False
        self.disconnected = False
        self.connect_calls: list[tuple[str, list[str] | None, float | None]] = []
        self.emit_count = 0

    def on(self, event: str, handler=None, namespace: str | None = None) -> None:
        assert event == "backtesting_status"
        self._handler = handler

    async def connect(self, url: str, namespaces: list[str] | None = None, wait_timeout: float | None = None) -> None:
        self.connected = True
        self.connect_calls.append((url, namespaces, wait_timeout))

    async def emit(self, event: str, namespace: str | None = None) -> None:
        assert event == "backtesting_status"
        self.emit_count += 1
        item = self._responses.pop(0)
        if callable(item):
            item = item()
        if isinstance(item, Exception):
            raise item
        assert self._handler is not None
        self._handler(item)

    async def disconnect(self) -> None:
        self.disconnected = True


def _factory(fake: _FakeSocketIOClient):
    return lambda: fake


def _sequential_factory(fakes: list[_FakeSocketIOClient]):
    """A `socketio_client_factory` returning the next fake in `fakes` on
    each call -- one fake per child, in submission order (milestone 10's
    `compare_strategies` tests)."""
    iterator = iter(fakes)
    return lambda: next(iterator)


def _http_handler(routes: dict[tuple[str, str], Any]):
    """Build a `httpx2.MockTransport` handler dispatching on `(method, path)`.

    Each value in `routes` is either an `httpx2.Response` or a zero-arg
    callable returning one, so a test can assert on the request (e.g. its
    JSON body/query params) before responding.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        value = routes[key]
        return value(request) if callable(value) else value

    return handler


def _data_files_spec(**overrides: Any) -> _StartSpec:
    defaults = dict(
        action_type="start_backtesting",
        extra_params={"run_on_common_part_only": True},
        body={"files": ["some_file.data"]},
        requested_source=None,
    )
    defaults.update(overrides)
    return _StartSpec(**defaults)


# --- _run_backtest_watcher: successful start -> running -> completed -------------


@pytest.mark.anyio
async def test_watcher_drives_a_job_through_running_to_completed_with_progress_updates():
    store = JobStore()
    job = store.create_job("backtest")

    def start_handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/backtesting"
        assert dict(request.url.params)["action_type"] == "start_backtesting"
        assert dict(request.url.params)["source"]  # a uuid4 was generated
        assert json.loads(request.content) == {"files": ["some_file.data"]}
        return httpx.Response(200, json="Backtesting started")

    fake_sio = _FakeSocketIOClient(
        [
            {"status": "computing", "progress": 10, "errors": 0},
            {"status": "computing", "progress": 55, "errors": 0},
            {"status": "finished", "progress": 100, "errors": 0},
        ]
    )

    def combined_handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if request.method == "POST":
            return start_handler(request)
        assert request.method == "GET"
        assert params["update_type"] == "backtesting_report"
        assert params["source"]
        return httpx.Response(200, json={"report": {"final_balance": 1234.5}, "trades": [{"id": 1}]})

    client = httpx.AsyncClient(base_url="http://octobot.example", transport=httpx.MockTransport(combined_handler))

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
    assert completed.progress_percent == 100.0
    assert completed.result == {"report": {"final_balance": 1234.5}, "trades": [{"id": 1}]}
    assert list(completed.samples) == [
        (pytest.approx(completed.samples[0][0]), 10.0),
        (pytest.approx(completed.samples[1][0]), 55.0),
        (pytest.approx(completed.samples[2][0]), 100.0),
    ]
    assert completed.octobot_run_source is not None
    assert fake_sio.connected is True
    assert fake_sio.disconnected is True
    # The lock is released once the watcher finishes.
    assert store.backtest_execution_lock.locked() is False


@pytest.mark.anyio
async def test_watcher_dedups_identical_consecutive_progress_samples():
    store = JobStore()
    job = store.create_job("backtest")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json="Backtesting started")
        return httpx.Response(200, json={"report": {}, "trades": []})

    client = httpx.AsyncClient(base_url="http://octobot.example", transport=httpx.MockTransport(handler))
    fake_sio = _FakeSocketIOClient(
        [
            {"status": "computing", "progress": 20, "errors": 0},
            {"status": "computing", "progress": 20, "errors": 0},  # unchanged -- not a new sample
            {"status": "finished", "progress": 100, "errors": 0},
        ]
    )

    await _run_backtest_watcher(
        client,
        store,
        job.job_id,
        _data_files_spec(),
        socketio_client_factory=_factory(fake_sio),
        interval_seconds=0,
    )

    completed = store.get(job.job_id)
    assert [progress for _, progress in completed.samples] == [20.0, 100.0]


@pytest.mark.anyio
async def test_watcher_records_octobots_own_backtesting_error_count():
    store = JobStore()
    job = store.create_job("backtest")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json="Backtesting started")
        return httpx.Response(200, json={"report": {}, "trades": []})

    client = httpx.AsyncClient(base_url="http://octobot.example", transport=httpx.MockTransport(handler))
    fake_sio = _FakeSocketIOClient(
        [
            {"status": "computing", "progress": 10, "errors": 2},
            {"status": "finished", "progress": 100, "errors": 2},
        ]
    )

    await _run_backtest_watcher(
        client,
        store,
        job.job_id,
        _data_files_spec(),
        socketio_client_factory=_factory(fake_sio),
        interval_seconds=0,
    )

    completed = store.get(job.job_id)
    assert completed.errors == ["OctoBot reported 2 error(s) during this backtesting run."]


# --- _run_backtest_watcher: start failure -----------------------------------------


@pytest.mark.anyio
async def test_watcher_fails_the_job_immediately_when_the_start_action_fails():
    store = JobStore()
    job = store.create_job("backtest")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json="An backtesting is already running")

    client = httpx.AsyncClient(base_url="http://octobot.example", transport=httpx.MockTransport(handler))
    socketio_calls = []

    def factory():
        socketio_calls.append(1)
        raise AssertionError("socketio should never be reached when the start action fails")

    await _run_backtest_watcher(
        client, store, job.job_id, _data_files_spec(), socketio_client_factory=factory, interval_seconds=0
    )

    failed = store.get(job.job_id)
    assert failed.state == "failed"
    assert "An backtesting is already running" in failed.errors[0]
    assert socketio_calls == []
    assert store.backtest_execution_lock.locked() is False


@pytest.mark.anyio
async def test_watcher_raises_missing_exchange_id_error_text_verbatim_on_that_specific_500():
    store = JobStore()
    job = store.create_job("backtest")
    explanation = (
        "Invalid exchange id, this might be due to a recent restart. "
        "Refresh this page if the error persists"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json=explanation)

    client = httpx.AsyncClient(base_url="http://octobot.example", transport=httpx.MockTransport(handler))

    await _run_backtest_watcher(
        client,
        store,
        job.job_id,
        _StartSpec(
            action_type="start_backtesting_with_current_bot_data",
            extra_params={},
            body={"exchange_id": "does-not-exist"},
            requested_source=None,
        ),
        socketio_client_factory=lambda: (_ for _ in ()).throw(AssertionError("unreachable")),
        interval_seconds=0,
    )

    failed = store.get(job.job_id)
    assert failed.state == "failed"
    # Exact match (not just a substring check): `MissingExchangeIdError`'s
    # message is the raw explanation text with no added prefix, unlike the
    # generic `BacktestStartFailedError` path, which wraps it -- this
    # proves the specific-error branch was actually taken.
    assert failed.errors[0] == explanation


# --- _run_backtest_watcher: cancel-while-running ----------------------------------


@pytest.mark.anyio
async def test_watcher_honors_cancel_requested_and_calls_stop_backtesting():
    store = JobStore()
    job = store.create_job("backtest")
    stop_calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and dict(request.url.params).get("action_type") == "start_backtesting":
            return httpx.Response(200, json="Backtesting started")
        if request.method == "POST" and dict(request.url.params).get("action_type") == "stop_backtesting":
            stop_calls.append(1)
            return httpx.Response(200, json="Backtesting is stopping")
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    client = httpx.AsyncClient(base_url="http://octobot.example", transport=httpx.MockTransport(handler))

    def _request_a_cancel_then_report_progress():
        # Simulates a concurrent cancel_job(job.job_id) call landing
        # between two watcher ticks.
        store.cancel(job.job_id)
        return {"status": "computing", "progress": 30, "errors": 0}

    fake_sio = _FakeSocketIOClient([_request_a_cancel_then_report_progress])

    await _run_backtest_watcher(
        client,
        store,
        job.job_id,
        _data_files_spec(),
        socketio_client_factory=_factory(fake_sio),
        interval_seconds=0,
    )

    cancelled = store.get(job.job_id)
    assert cancelled.state == "cancelled"
    assert cancelled.result is None  # no report fetched
    assert stop_calls == [1]
    assert store.backtest_execution_lock.locked() is False


@pytest.mark.anyio
async def test_watcher_cancels_a_job_that_was_cancelled_while_still_queued_for_the_lock():
    store = JobStore()
    job = store.create_job("backtest")
    # Simulate cancel_job being called while the job is still queued --
    # JobStore.cancel() transitions it straight to "cancelled" without
    # ever touching the lock (milestone 7 behavior, unchanged).
    store.cancel(job.job_id)
    assert store.get(job.job_id).state == "cancelled"

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no OctoBot call should happen for an already-cancelled job")

    client = httpx.AsyncClient(base_url="http://octobot.example", transport=httpx.MockTransport(handler))

    await _run_backtest_watcher(
        client,
        store,
        job.job_id,
        _data_files_spec(),
        socketio_client_factory=lambda: (_ for _ in ()).throw(AssertionError("unreachable")),
        interval_seconds=0,
    )

    assert store.get(job.job_id).state == "cancelled"
    assert store.backtest_execution_lock.locked() is False


@pytest.mark.anyio
async def test_watcher_reconciles_a_queued_to_cancelled_race_during_the_in_flight_start_action():
    """Regression test (milestone 10's own judge review, reproduced live via
    `compare_strategies`' cancel-propagation poller): `JobStore.cancel()`'s
    "queued" branch can force-transition the job straight to "cancelled"
    WHILE `_post_start_action`'s HTTP call is still in flight -- and that
    call can still go on to succeed. If `_run_backtest_watcher` didn't
    reconcile this, it would silently proceed into `_watch_until_done` on a
    record whose `cancel_requested` will never be set (the queued branch
    sets `state` directly, not that flag), leaving OctoBot's real backtest
    running unstopped, the lock held for its entire duration, and the
    record permanently (and misleadingly) `"cancelled"`.
    """
    store = JobStore()
    job = store.create_job("backtest")
    stop_calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if params.get("action_type") == "start_backtesting":
            # Simulate a concurrent cancel_job(job.job_id)/cancel-propagation
            # call landing exactly while this start request is in flight --
            # by the time this handler returns success, the record has
            # already been force-cancelled straight from "queued".
            store.cancel(job.job_id)
            return httpx.Response(200, json="Backtesting started")
        if params.get("action_type") == "stop_backtesting":
            stop_calls.append(1)
            return httpx.Response(200, json="Backtesting is stopping")
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    client = httpx.AsyncClient(base_url="http://octobot.example", transport=httpx.MockTransport(handler))

    def socketio_factory():
        raise AssertionError(
            "must never reach Socket.IO -- the race must be reconciled (stop "
            "called) before _watch_until_done is ever entered"
        )

    await _run_backtest_watcher(
        client,
        store,
        job.job_id,
        _data_files_spec(),
        socketio_client_factory=socketio_factory,
        interval_seconds=0,
    )

    # (a) the live-but-orphaned OctoBot run is actually told to stop.
    assert stop_calls == [1]
    # (c) the record isn't silently left "cancelled" with no acknowledgment
    # that a live run had to be stopped out from under it -- it also isn't
    # re-failed/re-mutated by this reconciliation path.
    final = store.get(job.job_id)
    assert final.state == "cancelled"
    assert final.result is None
    # (b) the lock is released promptly -- never held for a phantom
    # "watch" of a run this function correctly chose not to watch.
    assert store.backtest_execution_lock.locked() is False


# --- INV-1: lock release in finally, even on a hard mid-watch exception -----------


@pytest.mark.anyio
async def test_lock_is_released_after_a_forced_exception_and_a_second_job_can_then_acquire_it():
    """A SUSTAINED Socket.IO failure (every tick fails, not just one) still
    eventually fails the job and releases the lock (INV-1) -- milestone 14's
    bounded per-tick retry (`MAX_CONSECUTIVE_WATCHER_TICK_FAILURES`) absorbs
    a lone transient hiccup instead of failing the job on the very first one
    (see `tests/test_tools_backtesting_resilience.py` for that recovery
    case), so this test's fake now fails MAX_CONSECUTIVE_WATCHER_TICK_FAILURES
    times in a row -- the same "genuinely broken, not transient" scenario
    the original test intended to force -- rather than just once."""
    store = JobStore()
    job = store.create_job("backtest")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json="Backtesting started")

    client = httpx.AsyncClient(base_url="http://octobot.example", transport=httpx.MockTransport(handler))
    fake_sio = _FakeSocketIOClient(
        [RuntimeError("simulated hard Socket.IO failure")] * MAX_CONSECUTIVE_WATCHER_TICK_FAILURES
    )

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
    assert "simulated hard Socket.IO failure" in failed.errors[0]
    assert store.backtest_execution_lock.locked() is False

    # A second job can still acquire the lock and complete normally --
    # the first job's crash never left it stuck.
    second_job = store.create_job("backtest")

    def second_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json="Backtesting started")
        return httpx.Response(200, json={"report": {}, "trades": []})

    second_client = httpx.AsyncClient(
        base_url="http://octobot.example", transport=httpx.MockTransport(second_handler)
    )
    second_fake_sio = _FakeSocketIOClient([{"status": "finished", "progress": 100, "errors": 0}])

    await asyncio.wait_for(
        _run_backtest_watcher(
            second_client,
            store,
            second_job.job_id,
            _data_files_spec(),
            socketio_client_factory=_factory(second_fake_sio),
            interval_seconds=0,
        ),
        timeout=5,
    )

    assert store.get(second_job.job_id).state == "completed"


# --- start_backtest: returns immediately, does not await the watcher -------------


@pytest.mark.anyio
async def test_start_backtest_returns_queued_immediately_without_awaiting_the_watcher(monkeypatch):
    import octobot_mcp.tools.backtesting as backtesting_module

    watcher_started = asyncio.Event()
    release_watcher = asyncio.Event()

    async def _slow_fake_watcher(client, store, job_id, spec, **kwargs):
        watcher_started.set()
        await release_watcher.wait()

    monkeypatch.setattr(backtesting_module, "_run_backtest_watcher", _slow_fake_watcher)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("start_backtest itself must never call OctoBot directly")

    ctx = make_ctx(httpx.MockTransport(handler))
    store = ctx.request_context.lifespan_context.jobs

    result = await start_backtest(ctx, mode="data_files", files=["some_file.data"])

    assert result["state"] == "queued"
    assert result["job_id"]
    # Give the background task a chance to actually start running, then
    # let it finish and await it explicitly (rather than leaving it
    # dangling at test teardown).
    await asyncio.wait_for(watcher_started.wait(), timeout=5)
    release_watcher.set()
    task = store._watcher_tasks.get(result["job_id"])
    if task is not None:
        await asyncio.wait_for(task, timeout=5)


@pytest.mark.anyio
async def test_start_backtest_rejects_an_unknown_mode_without_creating_a_job():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no OctoBot call should happen for a rejected mode")

    ctx = make_ctx(httpx.MockTransport(handler))

    with pytest.raises(ToolError) as exc_info:
        await start_backtest(ctx, mode="not_a_real_mode")
    assert "BacktestStartFailedError" in str(exc_info.value)


@pytest.mark.anyio
async def test_start_backtest_rejects_data_files_mode_with_no_files():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no OctoBot call should happen for a rejected input")

    ctx = make_ctx(httpx.MockTransport(handler))

    with pytest.raises(ToolError) as exc_info:
        await start_backtest(ctx, mode="data_files", files=[])
    assert "BacktestStartFailedError" in str(exc_info.value)


# --- get_job_result ----------------------------------------------------------------


@pytest.mark.anyio
async def test_get_job_result_returns_report_and_trades_for_a_completed_backtest_job():
    store = JobStore()
    job = store.create_job("backtest")
    store.start(job.job_id)
    store.complete(job.job_id, result={"report": {"final_balance": 42}, "trades": [{"id": 1}]})

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("get_job_result must never call OctoBot -- it reads the stored result only")

    ctx = make_ctx(httpx.MockTransport(handler), jobs=store)
    result = await get_job_result(ctx, job_id=job.job_id)

    assert result == {"report": {"final_balance": 42}, "trades": [{"id": 1}]}


@pytest.mark.anyio
async def test_get_job_result_raises_job_not_completed_naming_the_current_state():
    store = JobStore()
    job = store.create_job("backtest")  # still queued

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("get_job_result must never call OctoBot")

    ctx = make_ctx(httpx.MockTransport(handler), jobs=store)

    with pytest.raises(ToolError) as exc_info:
        await get_job_result(ctx, job_id=job.job_id)
    assert "JobNotCompletedError" in str(exc_info.value)
    assert "queued" in str(exc_info.value)


@pytest.mark.anyio
async def test_get_job_result_raises_job_not_found_for_unknown_id():
    store = JobStore()

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("get_job_result must never call OctoBot")

    ctx = make_ctx(httpx.MockTransport(handler), jobs=store)

    with pytest.raises(ToolError) as exc_info:
        await get_job_result(ctx, job_id="does-not-exist")
    assert "JobNotFoundError" in str(exc_info.value)


@pytest.mark.anyio
async def test_get_job_result_returns_comparison_and_diff_for_a_completed_compare_strategies_job():
    # Milestone 8 left this a placeholder (CompareStrategiesResultUnsupportedError);
    # milestone 10 replaces it with the real behavior -- result is already
    # fully computed and stored by _run_compare_strategies, so get_job_result
    # just passes it through.
    store = JobStore()
    job = store.create_job("compare_strategies")
    store.start(job.job_id)
    stored_result = {
        "comparison": [{"config_label": "config_1", "job_id": "child-1", "state": "completed", "errors": [],
                         "report": {"bot_report": {"profitability": {"binance": 1.0}}}, "trades": []}],
        "diff": {"report.bot_report.profitability.binance": {"config_1": 1.0}},
    }
    store.complete(job.job_id, result=stored_result)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("get_job_result must never call OctoBot")

    ctx = make_ctx(httpx.MockTransport(handler), jobs=store)
    result = await get_job_result(ctx, job_id=job.job_id)

    assert result == {"comparison": stored_result["comparison"], "diff": stored_result["diff"]}


# --- request-building helpers: the auto_stop/reset_tentacle_config query-param quirk ---


def test_data_files_spec_omits_auto_stop_and_reset_tentacle_config_when_false():
    # Verified against source (controllers/backtesting.py): these are read
    # via `flask.request.args.get(key, False)`, so sending an explicit
    # "false" string would still be truthy -- only omitting the key
    # entirely reproduces OctoBot's own `False` default. See this module's
    # docstring for the full explanation.
    spec = _data_files_mode_spec(
        files=["f.data"],
        start_timestamp=None,
        end_timestamp=None,
        enable_logs=False,
        source=None,
        auto_stop=False,
        run_on_common_part_only=True,
        reset_tentacle_config=False,
    )
    assert spec.action_type == "start_backtesting"
    assert spec.extra_params == {"run_on_common_part_only": True}
    assert spec.body == {"files": ["f.data"]}


def test_data_files_spec_includes_auto_stop_and_reset_tentacle_config_when_true():
    spec = _data_files_mode_spec(
        files=["f.data"],
        start_timestamp=1000,
        end_timestamp=2000,
        enable_logs=True,
        source="my-source",
        auto_stop=True,
        run_on_common_part_only=False,
        reset_tentacle_config=True,
    )
    assert spec.extra_params == {
        "run_on_common_part_only": False,
        "auto_stop": True,
        "reset_tentacle_config": True,
    }
    assert spec.body == {
        "files": ["f.data"],
        "start_timestamp": 1000,
        "end_timestamp": 2000,
        "enable_logs": True,
    }
    assert spec.requested_source == "my-source"


def test_data_files_spec_rejects_an_empty_files_list():
    with pytest.raises(Exception) as exc_info:
        _data_files_mode_spec(
            files=[],
            start_timestamp=None,
            end_timestamp=None,
            enable_logs=False,
            source=None,
            auto_stop=False,
            run_on_common_part_only=True,
            reset_tentacle_config=False,
        )
    assert "BacktestStartFailedError" in type(exc_info.value).__name__


# --- current_bot_data mode request construction (open question #6, resolved) -----


def test_current_bot_data_spec_only_includes_explicitly_provided_fields():
    spec = _current_bot_data_mode_spec(
        data_source=None,
        exchange_id=None,
        exchange_type=None,
        profile_id=None,
        name=None,
        start_timestamp=None,
        end_timestamp=None,
        enable_logs=False,
        source=None,
        auto_stop=False,
        reset_tentacle_config=False,
    )
    assert spec.action_type == "start_backtesting_with_current_bot_data"
    assert spec.body == {}
    assert spec.extra_params == {}


def test_current_bot_data_spec_includes_every_field_when_provided():
    spec = _current_bot_data_mode_spec(
        data_source="current_bot_data",
        exchange_id="exch-123",
        exchange_type="spot",
        profile_id="daily_trading",
        name="my run",
        start_timestamp=1000,
        end_timestamp=2000,
        enable_logs=True,
        source="my-source",
        auto_stop=True,
        reset_tentacle_config=True,
    )
    assert spec.body == {
        "data_source": "current_bot_data",
        "exchange_id": "exch-123",
        "exchange_type": "spot",
        "profile_id": "daily_trading",
        "name": "my run",
        "start_timestamp": 1000,
        "end_timestamp": 2000,
        "enable_logs": True,
    }
    assert spec.extra_params == {"auto_stop": True, "reset_tentacle_config": True}
    assert spec.requested_source == "my-source"


@pytest.mark.anyio
async def test_start_backtest_current_bot_data_mode_end_to_end_success():
    seen_start_request: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if request.method == "POST":
            seen_start_request["params"] = params
            seen_start_request["body"] = json.loads(request.content)
            return httpx.Response(200, json="Backtesting started")
        assert request.method == "GET"
        return httpx.Response(200, json={"report": {"trading_mode": "DailyTradingMode"}, "trades": []})

    client = httpx.AsyncClient(base_url="http://octobot.example", transport=httpx.MockTransport(handler))
    store = JobStore()
    fake_sio = _FakeSocketIOClient([{"status": "finished", "progress": 100, "errors": 0}])

    spec = _current_bot_data_mode_spec(
        data_source="current_bot_data",
        exchange_id="exch-123",
        exchange_type=None,
        profile_id=None,
        name=None,
        start_timestamp=None,
        end_timestamp=None,
        enable_logs=False,
        source=None,
        auto_stop=False,
        reset_tentacle_config=False,
    )
    job = store.create_job("backtest")

    await _run_backtest_watcher(
        client, store, job.job_id, spec, socketio_client_factory=_factory(fake_sio), interval_seconds=0
    )

    assert seen_start_request["params"]["action_type"] == "start_backtesting_with_current_bot_data"
    assert "run_on_common_part_only" not in seen_start_request["params"]
    assert "auto_stop" not in seen_start_request["params"]
    assert seen_start_request["body"] == {"data_source": "current_bot_data", "exchange_id": "exch-123"}
    completed = store.get(job.job_id)
    assert completed.state == "completed"
    assert completed.result == {"report": {"trading_mode": "DailyTradingMode"}, "trades": []}


@pytest.mark.anyio
async def test_start_backtest_dispatches_current_bot_data_mode_via_the_public_tool():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("start_backtest itself must never call OctoBot directly")

    ctx = make_ctx(httpx.MockTransport(handler))
    store = ctx.request_context.lifespan_context.jobs
    result = await start_backtest(
        ctx,
        mode="current_bot_data",
        exchange_id="exch-123",
        data_source="current_bot_data",
    )
    assert result["state"] == "queued"
    assert result["job_id"]
    # Let the background watcher task run to completion (it will fail fast
    # since `ctx`'s mock transport always raises) rather than leaving it
    # dangling at test teardown.
    task = store._watcher_tasks.get(result["job_id"])
    if task is not None:
        await asyncio.wait_for(task, timeout=5)
    assert store.get(result["job_id"]).state == "failed"


# --- compare_strategies (milestone 10) ---------------------------------------------


def _labeled_data_files_spec(config: dict[str, Any]) -> _StartSpec:
    return _data_files_mode_spec(
        files=config["files"],
        start_timestamp=None,
        end_timestamp=None,
        enable_logs=False,
        source=config["source"],
        auto_stop=False,
        run_on_common_part_only=True,
        reset_tentacle_config=False,
    )


def _no_call_ctx(store: JobStore | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"no OctoBot call expected here: {request.method} {request.url}")

    return make_ctx(httpx.MockTransport(handler), jobs=store)


@pytest.mark.anyio
async def test_compare_strategies_rejects_fewer_than_2_configs_and_creates_no_jobs():
    ctx = _no_call_ctx()
    store = ctx.request_context.lifespan_context.jobs

    with pytest.raises(ToolError) as exc_info:
        await compare_strategies(ctx, configs=[{"mode": "data_files", "files": ["a.data"]}])

    assert "CompareStrategiesStartFailedError" in str(exc_info.value)
    assert store.list_jobs() == []


@pytest.mark.anyio
async def test_compare_strategies_rejects_an_invalid_config_naming_its_index_and_creates_no_jobs():
    ctx = _no_call_ctx()
    store = ctx.request_context.lifespan_context.jobs

    with pytest.raises(ToolError) as exc_info:
        await compare_strategies(
            ctx,
            configs=[{"mode": "data_files", "files": ["a.data"]}, {"mode": "not_a_real_mode"}],
        )

    assert "CompareStrategiesStartFailedError" in str(exc_info.value)
    assert "configs[1]" in str(exc_info.value)
    assert store.list_jobs() == []


@pytest.mark.anyio
async def test_compare_strategies_rejects_an_unrecognized_field_in_one_config():
    ctx = _no_call_ctx()
    store = ctx.request_context.lifespan_context.jobs

    with pytest.raises(ToolError) as exc_info:
        await compare_strategies(
            ctx,
            configs=[
                {"mode": "data_files", "files": ["a.data"]},
                {"mode": "data_files", "files": ["b.data"], "not_a_real_field": 1},
            ],
        )

    assert "CompareStrategiesStartFailedError" in str(exc_info.value)
    assert "configs[1]" in str(exc_info.value)
    assert store.list_jobs() == []


@pytest.mark.anyio
async def test_compare_strategies_dispatches_via_the_public_tool_and_returns_queued_with_sub_job_count():
    ctx = _no_call_ctx()
    store = ctx.request_context.lifespan_context.jobs

    result = await compare_strategies(
        ctx,
        configs=[
            {"mode": "data_files", "files": ["a.data"]},
            {"mode": "data_files", "files": ["b.data"]},
        ],
    )

    assert result["state"] == "queued"
    assert result["sub_job_count"] == 2
    parent = store.get(result["job_id"])
    assert parent.kind == "compare_strategies"
    assert len(parent.child_job_ids) == 2

    task = store._watcher_tasks.get(result["job_id"])
    if task is not None:
        await asyncio.wait_for(task, timeout=5)


@pytest.mark.anyio
async def test_run_compare_strategies_submits_children_sequentially_through_the_shared_lock():
    store = JobStore()
    call_order: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if request.method == "POST":
            call_order.append(("start", params["source"]))
            return httpx.Response(200, json="Backtesting started")
        call_order.append(("report", params["source"]))
        return httpx.Response(
            200, json={"report": {"bot_report": {"profitability": {"binance": 1.0}}}, "trades": []}
        )

    client = httpx.AsyncClient(base_url="http://octobot.example", transport=httpx.MockTransport(handler))
    fakes = [
        _FakeSocketIOClient([{"status": "finished", "progress": 100, "errors": 0}]),
        _FakeSocketIOClient([{"status": "finished", "progress": 100, "errors": 0}]),
    ]

    configs = [
        {"mode": "data_files", "files": ["a.data"], "source": "child-1"},
        {"mode": "data_files", "files": ["b.data"], "source": "child-2"},
    ]
    specs = [_labeled_data_files_spec(c) for c in configs]
    child_ids = [store.create_job("backtest").job_id for _ in specs]
    parent = store.create_job("compare_strategies", child_job_ids=child_ids)

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

    # child-2's start action is only ever requested AFTER child-1's own
    # report has already been fetched -- proves strictly sequential
    # submission through the shared lock, never interleaved/parallel.
    assert call_order == [
        ("start", "child-1"),
        ("report", "child-1"),
        ("start", "child-2"),
        ("report", "child-2"),
    ]
    assert store.get(parent.job_id).state == "completed"
    assert [store.get(child_id).state for child_id in child_ids] == ["completed", "completed"]
    assert store.backtest_execution_lock.locked() is False


@pytest.mark.anyio
async def test_run_compare_strategies_builds_comparison_and_diff_from_real_shaped_reports():
    # Reports modeled on the REAL captured shape (get_job_result's own
    # docstring, milestone 8's live smoke test): deeply nested under
    # "bot_report", keyed by exchange -- not a flat dict. child-2 reports
    # one extra numeric field ("market_average_profitability") that
    # child-1 doesn't -- must be excluded from the diff's common key set.
    reports = {
        "child-1": {
            "bot_report": {
                "starting_portfolio": {"binance": {"BTC": {"available": 10.0, "total": 10.0}}},
                "profitability": {"binance": -15.22},
                "reference_market": "EUR",
                "trading_mode": "DailyTradingMode",
            },
            "chart_identifiers": [{"exchange_name": "binance", "symbol": "BTC/EUR"}],
            "errors_count": 0,
        },
        "child-2": {
            "bot_report": {
                "starting_portfolio": {"binance": {"BTC": {"available": 10.0, "total": 10.0}}},
                "profitability": {"binance": 3.5},
                "market_average_profitability": {"binance": -1.0},
                "reference_market": "EUR",
                "trading_mode": "DailyTradingMode",
            },
            "chart_identifiers": [{"exchange_name": "binance", "symbol": "BTC/EUR"}],
            "errors_count": 0,
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if request.method == "POST":
            return httpx.Response(200, json="Backtesting started")
        return httpx.Response(200, json={"report": reports[params["source"]], "trades": []})

    client = httpx.AsyncClient(base_url="http://octobot.example", transport=httpx.MockTransport(handler))
    fakes = [
        _FakeSocketIOClient([{"status": "finished", "progress": 100, "errors": 0}]),
        _FakeSocketIOClient([{"status": "finished", "progress": 100, "errors": 0}]),
    ]

    configs = [
        {"mode": "data_files", "files": ["a.data"], "source": "child-1", "label": "baseline"},
        {"mode": "data_files", "files": ["b.data"], "source": "child-2", "label": "variant"},
    ]
    specs = [_labeled_data_files_spec(c) for c in configs]
    store = JobStore()
    child_ids = [store.create_job("backtest").job_id for _ in specs]
    parent = store.create_job("compare_strategies", child_job_ids=child_ids)

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

    result = await get_job_result(_no_call_ctx(store), job_id=parent.job_id)

    assert [entry["config_label"] for entry in result["comparison"]] == ["baseline", "variant"]
    assert result["comparison"][0]["job_id"] == child_ids[0]
    assert result["comparison"][0]["report"] == reports["child-1"]
    assert result["comparison"][0]["state"] == "completed"

    diff = result["diff"]
    assert diff["report.bot_report.profitability.binance"] == {"baseline": -15.22, "variant": 3.5}
    assert diff["report.bot_report.starting_portfolio.binance.BTC.available"] == {
        "baseline": 10.0,
        "variant": 10.0,
    }
    assert diff["report.errors_count"] == {"baseline": 0.0, "variant": 0.0}
    # Only child-2 reported this field -- not common to every successful
    # child, so it must be excluded from the diff entirely.
    assert "report.bot_report.market_average_profitability.binance" not in diff
    # Non-numeric leaves (strings) and list contents never appear as keys.
    assert not any("reference_market" in key or "chart_identifiers" in key or "trading_mode" in key for key in diff)


@pytest.mark.anyio
async def test_run_compare_strategies_isolates_a_failing_child_and_still_completes_the_parent():
    store = JobStore()

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if request.method == "POST":
            if params["source"] == "child-1":
                return httpx.Response(500, json="simulated OctoBot start failure")
            return httpx.Response(200, json="Backtesting started")
        return httpx.Response(
            200, json={"report": {"bot_report": {"profitability": {"binance": 1.0}}}, "trades": []}
        )

    client = httpx.AsyncClient(base_url="http://octobot.example", transport=httpx.MockTransport(handler))
    fake_child_2 = _FakeSocketIOClient([{"status": "finished", "progress": 100, "errors": 0}])

    def factory():
        # child-1 fails before ever reaching Socket.IO -- only child-2 should
        # ever call this factory.
        return fake_child_2

    configs = [
        {"mode": "data_files", "files": ["a.data"], "source": "child-1"},
        {"mode": "data_files", "files": ["b.data"], "source": "child-2"},
    ]
    specs = [_labeled_data_files_spec(c) for c in configs]
    child_ids = [store.create_job("backtest").job_id for _ in specs]
    parent = store.create_job("compare_strategies", child_job_ids=child_ids)

    await _run_compare_strategies(
        client, store, parent.job_id, child_ids, configs, specs, socketio_client_factory=factory, interval_seconds=0
    )

    # One bad config doesn't abort the batch: child-2 still ran normally,
    # and the parent still reaches "completed" despite child-1's failure.
    assert store.get(parent.job_id).state == "completed"
    assert store.get(child_ids[0]).state == "failed"
    assert store.get(child_ids[1]).state == "completed"

    result = await get_job_result(_no_call_ctx(store), job_id=parent.job_id)
    assert result["comparison"][0]["state"] == "failed"
    assert result["comparison"][0]["report"] is None
    assert result["comparison"][0]["trades"] is None
    assert result["comparison"][0]["errors"]  # OctoBot's own failure text recorded
    assert result["comparison"][1]["state"] == "completed"
    # Only 1 child actually completed -- nothing to diff.
    assert result["diff"] is None


@pytest.mark.anyio
async def test_run_compare_strategies_cancellation_mid_run_stops_current_child_and_skips_the_rest():
    store = JobStore()

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if request.method == "POST" and params.get("action_type") == "start_backtesting":
            return httpx.Response(200, json="Backtesting started")
        if request.method == "POST" and params.get("action_type") == "stop_backtesting":
            return httpx.Response(200, json="Backtesting is stopping")
        raise AssertionError(f"unexpected request during cancellation: {request.method} {request.url}")

    client = httpx.AsyncClient(base_url="http://octobot.example", transport=httpx.MockTransport(handler))

    configs = [
        {"mode": "data_files", "files": ["a.data"], "source": "child-1"},
        {"mode": "data_files", "files": ["b.data"], "source": "child-2"},
    ]
    specs = [_labeled_data_files_spec(c) for c in configs]
    child_ids = [store.create_job("backtest").job_id for _ in specs]
    parent = store.create_job("compare_strategies", child_job_ids=child_ids)

    def _cancel_parent_then_report_progress():
        # Simulates a concurrent cancel_job(parent.job_id) call landing
        # while child-1 is running.
        store.cancel(parent.job_id)
        return {"status": "computing", "progress": 10, "errors": 0}

    fake_child_1 = _FakeSocketIOClient(
        [_cancel_parent_then_report_progress, {"status": "computing", "progress": 20, "errors": 0}]
    )
    factory_calls = []

    def factory():
        factory_calls.append(1)
        if len(factory_calls) == 1:
            return fake_child_1
        raise AssertionError("child-2 must never start once the parent has been cancelled")

    await _run_compare_strategies(
        client,
        store,
        parent.job_id,
        child_ids,
        configs,
        specs,
        socketio_client_factory=factory,
        interval_seconds=0,
        cancel_poll_interval_seconds=0,
    )

    assert store.get(child_ids[0]).state == "cancelled"
    # child-2 was still "queued" (never touched the lock) -- cancelled
    # directly, the cheap path, never started.
    assert store.get(child_ids[1]).state == "cancelled"
    assert store.get(parent.job_id).state == "cancelled"
    assert store.backtest_execution_lock.locked() is False


# --- _build_diff (pure, direct unit tests) ------------------------------------------


def _completed_entry(job_id: str, label: str, report: dict[str, Any]) -> dict[str, Any]:
    return {"config_label": label, "job_id": job_id, "state": "completed", "errors": [], "report": report, "trades": []}


def test_build_diff_is_none_with_fewer_than_2_successful_children():
    assert _build_diff([]) is None
    assert _build_diff([_completed_entry("a", "a", {"x": 1.0})]) is None
    assert _build_diff(
        [
            _completed_entry("a", "a", {"x": 1.0}),
            {"config_label": "b", "job_id": "b", "state": "failed", "errors": ["boom"], "report": None, "trades": None},
        ]
    ) is None


def test_build_diff_only_includes_keys_common_to_every_successful_child():
    entries = [
        _completed_entry("a", "a", {"shared": 1.0, "only_a": 2.0}),
        _completed_entry("b", "b", {"shared": 3.0, "only_b": 4.0}),
    ]
    diff = _build_diff(entries)
    # _build_diff always flattens under a "report." prefix (its callers'
    # entries' "report" key is the whole nested OctoBot report).
    assert diff == {"report.shared": {"a": 1.0, "b": 3.0}}


def test_build_diff_excludes_non_numeric_and_bool_leaves():
    entries = [
        _completed_entry("a", "a", {"value": 1.0, "label": "x", "flag": True, "nested": {"num": 5}}),
        _completed_entry("b", "b", {"value": 2.0, "label": "y", "flag": False, "nested": {"num": 6}}),
    ]
    diff = _build_diff(entries)
    assert diff == {"report.value": {"a": 1.0, "b": 2.0}, "report.nested.num": {"a": 5.0, "b": 6.0}}
