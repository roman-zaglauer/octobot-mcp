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
    _StartSpec,
    _current_bot_data_mode_spec,
    _data_files_mode_spec,
    _run_backtest_watcher,
    get_job_result,
    start_backtest,
)
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


# --- INV-1: lock release in finally, even on a hard mid-watch exception -----------


@pytest.mark.anyio
async def test_lock_is_released_after_a_forced_exception_and_a_second_job_can_then_acquire_it():
    store = JobStore()
    job = store.create_job("backtest")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json="Backtesting started")

    client = httpx.AsyncClient(base_url="http://octobot.example", transport=httpx.MockTransport(handler))
    fake_sio = _FakeSocketIOClient([RuntimeError("simulated hard Socket.IO failure")])

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
async def test_get_job_result_rejects_compare_strategies_jobs():
    store = JobStore()
    job = store.create_job("compare_strategies")
    store.start(job.job_id)
    store.complete(job.job_id, result={})

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("get_job_result must never call OctoBot")

    ctx = make_ctx(httpx.MockTransport(handler), jobs=store)

    with pytest.raises(ToolError) as exc_info:
        await get_job_result(ctx, job_id=job.job_id)
    assert "CompareStrategiesResultUnsupportedError" in str(exc_info.value)


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
