"""Unit tests for the historical data collection tools (milestone 15,
docs/tasklist.md item 15, ADR-0007/ADR-0008).

Mirrors `tests/test_tools_backtesting.py`'s conventions throughout: the
HTTP layer is mocked via `httpx2.MockTransport`; the Socket.IO layer is
mocked via a local `_FakeSocketIOClient` (same shape as that module's own,
duplicated rather than imported -- this project's established per-module
duplication convention -- but bound to the `data_collector_status` event
name); `interval_seconds=0` throughout so these tests run fast.

`DATA_COLLECTOR_HTML`/`tests/fixtures/data_collector_valid.html` is a real,
trimmed capture of the live test instance's `/data_collector` response
(OctoBot 2.1.1, 3 real rows -- one `is_full` single-symbol row, one
non-`is_full` multi-symbol row, one `is_full` multi-symbol row),
independently re-verified against a fresh live fetch this session (see
`octobot_mcp.tools.data_collection`'s own module docstring) -- not a
hand-authored guess. The broken-fixture tests mutate specific substrings
of that same real snapshot, mirroring `test_tools_profiles.py`'s NFR-12
test structure for NFR-13's identical acceptance criterion.
"""

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from octobot_mcp.jobs import JobStore
from octobot_mcp.tools.backtesting import _run_backtest_watcher, get_job_result
from octobot_mcp.tools.data_collection import (
    MAX_CONSECUTIVE_WATCHER_TICK_FAILURES,
    _build_start_body,
    _convert_data_collection_progress,
    _parse_data_collector_html,
    _run_data_collection_watcher,
    delete_data_file,
    get_available_timeframes_for_collection,
    import_data_file,
    list_data_files,
    start_data_collection,
)
from tests.test_tools_shared import make_ctx

FIXTURES_DIR = Path(__file__).parent / "fixtures"
DATA_COLLECTOR_HTML = (FIXTURES_DIR / "data_collector_valid.html").read_text()

_FULL_SINGLE_FILE = "ExchangeHistoryDataCollector_1788790606.2241962.data"
_PARTIAL_FILE = "ExchangeHistoryDataCollector_1788790555.8571877.data"
_FULL_MULTI_FILE = "ExchangeHistoryDataCollector_1788790669.382438.data"


@pytest.fixture
def anyio_backend():
    return "asyncio"


class _FakeSocketIOClient:
    """Minimal fake matching the `on`/`connect`/`emit`/`disconnect` surface
    `_DataCollectorStatusChannel`/`_watch_data_collection_until_done`
    actually call -- see `tests/test_tools_backtesting.py`'s identically-
    purposed fake for the full behavior this mirrors (duplicated, not
    imported: bound to a different event name)."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self._handler = None
        self.connected = False
        self.disconnected = False
        self.emit_count = 0

    def on(self, event: str, handler=None, namespace: str | None = None) -> None:
        assert event == "data_collector_status"
        self._handler = handler

    async def connect(self, url: str, namespaces: list[str] | None = None, wait_timeout: float | None = None) -> None:
        self.connected = True

    async def emit(self, event: str, namespace: str | None = None) -> None:
        assert event == "data_collector_status"
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


def _request(**overrides: Any) -> dict[str, Any]:
    defaults = dict(
        exchange="binance",
        symbols=["BTC/USDT"],
        time_frames=["1h"],
        start_timestamp=None,
        end_timestamp=None,
    )
    defaults.update(overrides)
    return defaults


def _step(current_step: int, total_steps: int, current_step_percent: int = 0, status: str = "collecting"):
    return {
        "status": status,
        "progress": {
            "current_step": current_step,
            "total_steps": total_steps,
            "current_step_percent": current_step_percent,
        },
    }


_FINISHED = {"status": "finished", "progress": {"current_step": 0, "total_steps": 0, "current_step_percent": 0}}


# --- _convert_data_collection_progress: pure-function unit tests ------------


def test_convert_progress_returns_zero_when_total_steps_is_zero_or_missing():
    assert _convert_data_collection_progress({"current_step": 0, "total_steps": 0, "current_step_percent": 0}) == 0.0
    assert _convert_data_collection_progress({}) == 0.0


def test_convert_progress_at_first_step_with_1_indexing():
    # current_step=1 (1-indexed, live-confirmed), 0% into it, 30 total steps.
    assert _convert_data_collection_progress(
        {"current_step": 1, "total_steps": 30, "current_step_percent": 0}
    ) == pytest.approx(0.0)


def test_convert_progress_at_the_last_step_reaches_close_to_100():
    # current_step == total_steps (live-confirmed max), 0% into the final step.
    result = _convert_data_collection_progress({"current_step": 30, "total_steps": 30, "current_step_percent": 0})
    assert result == pytest.approx((29 * 100) / 30)


def test_convert_progress_mid_step_percent_is_incorporated():
    result = _convert_data_collection_progress({"current_step": 2, "total_steps": 4, "current_step_percent": 50})
    assert result == pytest.approx(((1 * 100) + 50) / 4)


def test_convert_progress_clamps_defensively_even_if_step_exceeded_total():
    # Defense-in-depth per ADR-0007, even though live-confirmed to never happen.
    result = _convert_data_collection_progress({"current_step": 99, "total_steps": 10, "current_step_percent": 0})
    assert result == 100.0


# --- _build_start_body: the live-verified always-include-keys quirk --------


def test_build_start_body_always_includes_time_frames_and_timestamps_even_when_none():
    body = _build_start_body("binance", ["BTC/USDT"], None, None, None)
    assert set(body.keys()) == {"exchange", "symbols", "time_frames", "startTimestamp", "endTimestamp"}
    assert body["time_frames"] is None
    assert body["startTimestamp"] is None
    assert body["endTimestamp"] is None


def test_build_start_body_uses_octobots_own_camel_case_timestamp_keys():
    body = _build_start_body("binance", ["BTC/USDT"], ["1h"], 1000, 2000)
    assert body["startTimestamp"] == 1000
    assert body["endTimestamp"] == 2000
    assert "start_timestamp" not in body
    assert "end_timestamp" not in body


# --- _run_data_collection_watcher: successful start -> running -> completed ------


@pytest.mark.anyio
async def test_watcher_drives_a_job_through_running_to_completed_with_progress_updates():
    store = JobStore()
    job = store.create_job("data_collection")

    def start_handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/data_collector"
        assert dict(request.url.params)["action_type"] == "start_collector"
        body = json.loads(request.content)
        assert body == {
            "exchange": "binance",
            "symbols": ["BTC/USDT"],
            "time_frames": ["1h"],
            "startTimestamp": None,
            "endTimestamp": None,
        }
        return httpx.Response(200, json="Historical data collection started.")

    client = httpx.AsyncClient(base_url="http://octobot.example", transport=httpx.MockTransport(start_handler))
    fake_sio = _FakeSocketIOClient(
        [
            _step(1, 2, 0),
            _step(2, 2, 0),
            _FINISHED,
        ]
    )

    await _run_data_collection_watcher(
        client,
        store,
        job.job_id,
        _request(),
        socketio_client_factory=_factory(fake_sio),
        interval_seconds=0,
    )

    completed = store.get(job.job_id)
    assert completed.state == "completed"
    assert completed.progress_percent == 100.0  # store.complete() forces this regardless of the last tick.
    assert completed.result == {
        "exchange": "binance",
        "symbols": ["BTC/USDT"],
        "time_frames": ["1h"],
        "start_timestamp": None,
        "end_timestamp": None,
        "message": "Historical data collection started.",
    }
    assert fake_sio.connected is True
    assert fake_sio.disconnected is True
    assert store.data_collection_execution_lock.locked() is False


@pytest.mark.anyio
async def test_watcher_never_records_an_error_from_the_status_payload():
    """Unlike backtesting's `errors` field, this payload has no equivalent
    -- `error_count` is always passed 0 (ADR-0007 decision point 4)."""
    store = JobStore()
    job = store.create_job("data_collection")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json="Historical data collection started.")

    client = httpx.AsyncClient(base_url="http://octobot.example", transport=httpx.MockTransport(handler))
    fake_sio = _FakeSocketIOClient([_step(1, 1, 0), _FINISHED])

    await _run_data_collection_watcher(
        client, store, job.job_id, _request(), socketio_client_factory=_factory(fake_sio), interval_seconds=0
    )

    assert store.get(job.job_id).errors == []


# --- _run_data_collection_watcher: start failure ----------------------------


@pytest.mark.anyio
async def test_watcher_fails_the_job_immediately_when_the_start_action_fails():
    store = JobStore()
    job = store.create_job("data_collection")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Please select an exchange.")

    client = httpx.AsyncClient(base_url="http://octobot.example", transport=httpx.MockTransport(handler))

    await _run_data_collection_watcher(
        client,
        store,
        job.job_id,
        _request(exchange=""),
        socketio_client_factory=lambda: (_ for _ in ()).throw(AssertionError("unreachable")),
        interval_seconds=0,
    )

    failed = store.get(job.job_id)
    assert failed.state == "failed"
    assert "Please select an exchange." in failed.errors[0]
    assert "DataCollectionStartFailedError" in failed.errors[0] or "Please select an exchange." in failed.errors[0]
    assert store.data_collection_execution_lock.locked() is False


# --- _run_data_collection_watcher: cancel-while-running ---------------------


@pytest.mark.anyio
async def test_watcher_honors_cancel_requested_and_calls_stop_collector():
    store = JobStore()
    job = store.create_job("data_collection")
    stop_calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if request.method == "POST" and params.get("action_type") == "start_collector":
            return httpx.Response(200, json="Historical data collection started.")
        if request.method == "POST" and params.get("action_type") == "stop_collector":
            stop_calls.append(1)
            return httpx.Response(200, json="Data collector stopped")
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    client = httpx.AsyncClient(base_url="http://octobot.example", transport=httpx.MockTransport(handler))

    def _request_a_cancel_then_report_progress():
        store.cancel(job.job_id)
        return _step(1, 5, 0)

    fake_sio = _FakeSocketIOClient([_request_a_cancel_then_report_progress])

    await _run_data_collection_watcher(
        client, store, job.job_id, _request(), socketio_client_factory=_factory(fake_sio), interval_seconds=0
    )

    cancelled = store.get(job.job_id)
    assert cancelled.state == "cancelled"
    assert cancelled.result is None
    assert stop_calls == [1]
    assert store.data_collection_execution_lock.locked() is False


@pytest.mark.anyio
async def test_watcher_cancels_a_job_that_was_cancelled_while_still_queued_for_the_lock():
    store = JobStore()
    job = store.create_job("data_collection")
    store.cancel(job.job_id)
    assert store.get(job.job_id).state == "cancelled"

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no OctoBot call should happen for an already-cancelled job")

    client = httpx.AsyncClient(base_url="http://octobot.example", transport=httpx.MockTransport(handler))

    await _run_data_collection_watcher(
        client,
        store,
        job.job_id,
        _request(),
        socketio_client_factory=lambda: (_ for _ in ()).throw(AssertionError("unreachable")),
        interval_seconds=0,
    )

    assert store.get(job.job_id).state == "cancelled"
    assert store.data_collection_execution_lock.locked() is False


@pytest.mark.anyio
async def test_watcher_reconciles_a_queued_to_cancelled_race_during_the_in_flight_start_action():
    """Mirrors `test_tools_backtesting.py`'s identically-purposed regression
    test exactly -- see that test's own docstring for the full race
    explanation, which applies unchanged here."""
    store = JobStore()
    job = store.create_job("data_collection")
    stop_calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if params.get("action_type") == "start_collector":
            store.cancel(job.job_id)
            return httpx.Response(200, json="Historical data collection started.")
        if params.get("action_type") == "stop_collector":
            stop_calls.append(1)
            return httpx.Response(200, json="Data collector stopped")
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    client = httpx.AsyncClient(base_url="http://octobot.example", transport=httpx.MockTransport(handler))

    def socketio_factory():
        raise AssertionError("must never reach Socket.IO -- the race must be reconciled first")

    await _run_data_collection_watcher(
        client,
        store,
        job.job_id,
        _request(),
        socketio_client_factory=socketio_factory,
        interval_seconds=0,
    )

    assert stop_calls == [1]
    final = store.get(job.job_id)
    assert final.state == "cancelled"
    assert final.result is None
    assert store.data_collection_execution_lock.locked() is False


# --- INV-1: lock release in finally, even on a hard mid-watch exception -----


@pytest.mark.anyio
async def test_lock_is_released_after_a_forced_exception_and_a_second_job_can_then_acquire_it():
    store = JobStore()
    job = store.create_job("data_collection")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json="Historical data collection started.")

    client = httpx.AsyncClient(base_url="http://octobot.example", transport=httpx.MockTransport(handler))
    fake_sio = _FakeSocketIOClient(
        [RuntimeError("simulated hard Socket.IO failure")] * MAX_CONSECUTIVE_WATCHER_TICK_FAILURES
    )

    await _run_data_collection_watcher(
        client, store, job.job_id, _request(), socketio_client_factory=_factory(fake_sio), interval_seconds=0
    )

    failed = store.get(job.job_id)
    assert failed.state == "failed"
    assert "simulated hard Socket.IO failure" in failed.errors[0]
    assert store.data_collection_execution_lock.locked() is False

    second_job = store.create_job("data_collection")

    def second_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json="Historical data collection started.")

    second_client = httpx.AsyncClient(
        base_url="http://octobot.example", transport=httpx.MockTransport(second_handler)
    )
    second_fake_sio = _FakeSocketIOClient([_FINISHED])

    await asyncio.wait_for(
        _run_data_collection_watcher(
            second_client,
            store,
            second_job.job_id,
            _request(),
            socketio_client_factory=_factory(second_fake_sio),
            interval_seconds=0,
        ),
        timeout=5,
    )

    assert store.get(second_job.job_id).state == "completed"


# --- NFR-15: two independent locks -------------------------------------------


@pytest.mark.anyio
async def test_data_collection_lock_is_independent_from_backtest_lock():
    store = JobStore()
    assert store.data_collection_execution_lock is not store.backtest_execution_lock


@pytest.mark.anyio
async def test_a_running_backtest_does_not_block_a_concurrent_data_collection():
    """NFR-15's acceptance criterion: a concurrent start_backtest +
    start_data_collection pair does not block on each other's lock."""
    store = JobStore()

    # Hold backtest_execution_lock for the whole test, simulating an
    # in-flight backtest.
    async with store.backtest_execution_lock:
        job = store.create_job("data_collection")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json="Historical data collection started.")

        client = httpx.AsyncClient(base_url="http://octobot.example", transport=httpx.MockTransport(handler))
        fake_sio = _FakeSocketIOClient([_FINISHED])

        await asyncio.wait_for(
            _run_data_collection_watcher(
                client, store, job.job_id, _request(), socketio_client_factory=_factory(fake_sio), interval_seconds=0
            ),
            timeout=5,
        )

        assert store.get(job.job_id).state == "completed"


@pytest.mark.anyio
async def test_a_running_data_collection_does_not_block_a_concurrent_backtest():
    from tests.test_tools_backtesting import _data_files_spec

    store = JobStore()

    async with store.data_collection_execution_lock:
        job = store.create_job("backtest")

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                return httpx.Response(200, json="Backtesting started")
            return httpx.Response(200, json={"report": {}, "trades": []})

        client = httpx.AsyncClient(base_url="http://octobot.example", transport=httpx.MockTransport(handler))

        class _FakeBacktestSio:
            def __init__(self):
                self._handler = None

            def on(self, event, handler=None, namespace=None):
                self._handler = handler

            async def connect(self, url, namespaces=None, wait_timeout=None):
                pass

            async def emit(self, event, namespace=None):
                self._handler({"status": "finished", "progress": 100, "errors": 0})

            async def disconnect(self):
                pass

        fake_sio = _FakeBacktestSio()

        await asyncio.wait_for(
            _run_backtest_watcher(
                client, store, job.job_id, _data_files_spec(), socketio_client_factory=lambda: fake_sio,
                interval_seconds=0,
            ),
            timeout=5,
        )

        assert store.get(job.job_id).state == "completed"


@pytest.mark.anyio
async def test_two_concurrent_data_collections_do_serialize_via_the_shared_lock():
    store = JobStore()
    order: list[str] = []

    first_job = store.create_job("data_collection")
    second_job = store.create_job("data_collection")

    release_first = asyncio.Event()

    def first_handler(request: httpx.Request) -> httpx.Response:
        order.append("first_start")
        return httpx.Response(200, json="Historical data collection started.")

    first_client = httpx.AsyncClient(base_url="http://octobot.example", transport=httpx.MockTransport(first_handler))

    class _BlockingSio:
        def on(self, event, handler=None, namespace=None):
            self._handler = handler

        async def connect(self, url, namespaces=None, wait_timeout=None):
            pass

        async def emit(self, event, namespace=None):
            await release_first.wait()
            self._handler({"status": "finished", "progress": {"current_step": 0, "total_steps": 0, "current_step_percent": 0}})

        async def disconnect(self):
            pass

    def second_handler(request: httpx.Request) -> httpx.Response:
        order.append("second_start")
        return httpx.Response(200, json="Historical data collection started.")

    second_client = httpx.AsyncClient(
        base_url="http://octobot.example", transport=httpx.MockTransport(second_handler)
    )
    second_fake_sio = _FakeSocketIOClient([_FINISHED])

    first_task = asyncio.create_task(
        _run_data_collection_watcher(
            first_client, store, first_job.job_id, _request(), socketio_client_factory=_BlockingSio, interval_seconds=0
        )
    )
    await asyncio.sleep(0.05)  # let the first task acquire the lock and start
    second_task = asyncio.create_task(
        _run_data_collection_watcher(
            second_client,
            store,
            second_job.job_id,
            _request(),
            socketio_client_factory=_factory(second_fake_sio),
            interval_seconds=0,
        )
    )
    await asyncio.sleep(0.05)
    # The second job must not have started yet -- it's queued on the lock.
    assert "second_start" not in order
    release_first.set()
    await asyncio.wait_for(asyncio.gather(first_task, second_task), timeout=5)
    assert order == ["first_start", "second_start"]


# --- start_data_collection: returns immediately, does not await the watcher ------


@pytest.mark.anyio
async def test_start_data_collection_returns_queued_immediately_without_awaiting_the_watcher(monkeypatch):
    import octobot_mcp.tools.data_collection as data_collection_module

    watcher_started = asyncio.Event()
    release_watcher = asyncio.Event()

    async def _slow_fake_watcher(client, store, job_id, request, **kwargs):
        watcher_started.set()
        await release_watcher.wait()

    monkeypatch.setattr(data_collection_module, "_run_data_collection_watcher", _slow_fake_watcher)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("start_data_collection itself must never call OctoBot directly")

    ctx = make_ctx(httpx.MockTransport(handler))
    store = ctx.request_context.lifespan_context.jobs

    result = await start_data_collection(ctx, exchange="binance", symbols=["BTC/USDT"])

    assert result["state"] == "queued"
    assert result["job_id"]
    await asyncio.wait_for(watcher_started.wait(), timeout=5)
    release_watcher.set()
    task = store._watcher_tasks.get(result["job_id"])
    if task is not None:
        await asyncio.wait_for(task, timeout=5)


@pytest.mark.anyio
async def test_start_data_collection_rejects_empty_symbols_without_creating_a_job():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no OctoBot call should happen for a rejected input")

    ctx = make_ctx(httpx.MockTransport(handler))
    store = ctx.request_context.lifespan_context.jobs

    with pytest.raises(ToolError) as exc_info:
        await start_data_collection(ctx, exchange="binance", symbols=[])
    assert "DataCollectionStartFailedError" in str(exc_info.value)
    assert store.list_jobs() == []


# --- get_job_result: the new data_collection branch (ADR-0007 decision point 5) --


@pytest.mark.anyio
async def test_get_job_result_returns_the_stored_result_unchanged_for_a_completed_data_collection_job():
    store = JobStore()
    job = store.create_job("data_collection")
    store.start(job.job_id)
    result = {
        "exchange": "binance",
        "symbols": ["BTC/USDT"],
        "time_frames": ["1h"],
        "start_timestamp": None,
        "end_timestamp": None,
        "message": "Historical data collection started.",
    }
    store.complete(job.job_id, result=result)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("get_job_result must never call OctoBot")

    ctx = make_ctx(httpx.MockTransport(handler), jobs=store)
    returned = await get_job_result(ctx, job_id=job.job_id)

    assert returned == result


# --- list_data_files: Tier-B scrape adapter (ADR-0008) ----------------------


def _html_handler(html: str, *, status_code: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/data_collector"
        return httpx.Response(status_code, text=html)

    return handler


@pytest.mark.anyio
async def test_list_data_files_extracts_all_fields_for_every_row():
    result = await list_data_files(make_ctx(httpx.MockTransport(_html_handler(DATA_COLLECTOR_HTML))))
    files_by_name = {f["file"]: f for f in result["files"]}
    assert set(files_by_name) == {_FULL_SINGLE_FILE, _PARTIAL_FILE, _FULL_MULTI_FILE}

    full_single = files_by_name[_FULL_SINGLE_FILE]
    assert full_single["exchange"] == "binance"
    assert full_single["symbols"] == ["SOL/USDT"]
    assert full_single["time_frames"] == ["1d"]
    assert full_single["is_full"] is True
    assert full_single["start_date"] == "08 August 2026"
    assert full_single["end_date"] == "07 September 2026"
    assert full_single["date"] is None
    assert full_single["candles_length"] is None

    partial = files_by_name[_PARTIAL_FILE]
    assert partial["symbols"] == ["ETH/USDT", "ADA/USDT"]
    assert partial["time_frames"] == ["1M", "1w"]
    assert partial["is_full"] is False
    assert partial["date"] == "07 September 2026 at 14:15:55"
    assert partial["candles_length"] == 456
    assert partial["start_date"] is None
    assert partial["end_date"] is None

    full_multi = files_by_name[_FULL_MULTI_FILE]
    assert full_multi["symbols"] == ["ETH/USDT", "ADA/USDT", "XRP/USDT", "LTC/USDT"]
    assert full_multi["is_full"] is True


@pytest.mark.anyio
async def test_list_data_files_returns_an_empty_list_when_the_table_exists_but_has_no_rows():
    empty_html = DATA_COLLECTOR_HTML[: DATA_COLLECTOR_HTML.index("<tbody>") + len("<tbody>")] + "</tbody></table></body></html>"
    result = await list_data_files(make_ctx(httpx.MockTransport(_html_handler(empty_html))))
    assert result["files"] == []


@pytest.mark.anyio
async def test_list_data_files_raises_on_missing_table():
    broken_html = DATA_COLLECTOR_HTML.replace('id="dataFilesTable"', 'id="dataFilesTableRenamed"')
    with pytest.raises(ToolError) as exc_info:
        await list_data_files(make_ctx(httpx.MockTransport(_html_handler(broken_html))))
    assert "DataFileScrapeContractBrokenError" in str(exc_info.value)


@pytest.mark.anyio
async def test_list_data_files_raises_when_a_row_is_missing_a_cell():
    broken_html = DATA_COLLECTOR_HTML.replace("<td>binance</td>", "", 1)
    with pytest.raises(ToolError) as exc_info:
        await list_data_files(make_ctx(httpx.MockTransport(_html_handler(broken_html))))
    assert "DataFileScrapeContractBrokenError" in str(exc_info.value)


@pytest.mark.anyio
async def test_list_data_files_raises_when_the_delete_button_data_file_attribute_is_missing():
    broken_html = DATA_COLLECTOR_HTML.replace(f'data-file="{_FULL_SINGLE_FILE}"', "")
    with pytest.raises(ToolError) as exc_info:
        await list_data_files(make_ctx(httpx.MockTransport(_html_handler(broken_html))))
    assert "DataFileScrapeContractBrokenError" in str(exc_info.value)


@pytest.mark.anyio
async def test_list_data_files_raises_when_visible_filename_disagrees_with_delete_button():
    broken_html = DATA_COLLECTOR_HTML.replace(
        f"<td>{_FULL_SINGLE_FILE}</td>", "<td>a_totally_different_file.data</td>"
    )
    with pytest.raises(ToolError) as exc_info:
        await list_data_files(make_ctx(httpx.MockTransport(_html_handler(broken_html))))
    assert "DataFileScrapeContractBrokenError" in str(exc_info.value)


@pytest.mark.anyio
async def test_list_data_files_raises_on_duplicate_file_names():
    broken_html = DATA_COLLECTOR_HTML.replace(_PARTIAL_FILE, _FULL_SINGLE_FILE)
    with pytest.raises(ToolError) as exc_info:
        await list_data_files(make_ctx(httpx.MockTransport(_html_handler(broken_html))))
    assert "DataFileScrapeContractBrokenError" in str(exc_info.value)


@pytest.mark.anyio
async def test_list_data_files_raises_when_a_full_row_date_has_no_to_separator():
    broken_html = DATA_COLLECTOR_HTML.replace(
        "08 August 2026 to 07 September 2026", "08 August 2026 only", 1
    )
    with pytest.raises(ToolError) as exc_info:
        await list_data_files(make_ctx(httpx.MockTransport(_html_handler(broken_html))))
    assert "DataFileScrapeContractBrokenError" in str(exc_info.value)


@pytest.mark.anyio
async def test_list_data_files_raises_when_a_non_full_candle_count_is_not_an_integer():
    broken_html = DATA_COLLECTOR_HTML.replace("<td>456</td>", "<td>not-a-number</td>", 1)
    with pytest.raises(ToolError) as exc_info:
        await list_data_files(make_ctx(httpx.MockTransport(_html_handler(broken_html))))
    assert "DataFileScrapeContractBrokenError" in str(exc_info.value)


@pytest.mark.anyio
async def test_list_data_files_never_returns_a_partial_list_on_broken_scrape():
    """Mirrors `test_list_profiles_never_returns_empty_or_partial_list_on_broken_scrape`'s
    intent exactly (NFR-13's own acceptance criterion)."""
    broken_html = DATA_COLLECTOR_HTML.replace('id="dataFilesTable"', 'id="dataFilesTableRenamed"')
    with pytest.raises(ToolError):
        result_that_should_never_exist = None
        try:
            result_that_should_never_exist = await list_data_files(
                make_ctx(httpx.MockTransport(_html_handler(broken_html)))
            )
        finally:
            assert result_that_should_never_exist is None
        raise AssertionError("unreachable")


# --- delete_data_file: confirm-gated (ADR-0003/FR-25) -----------------------


@pytest.mark.anyio
async def test_delete_data_file_refuses_without_confirm():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no OctoBot call should happen without confirm=true")

    ctx = make_ctx(httpx.MockTransport(handler))
    result = await delete_data_file(ctx, file=_FULL_SINGLE_FILE)

    assert result["confirmed"] is False
    assert result["action"] == "delete_data_file"
    assert result["target"] == _FULL_SINGLE_FILE
    assert "confirm=true" in result["message"]


@pytest.mark.anyio
async def test_delete_data_file_succeeds_when_confirmed():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert dict(request.url.params)["action_type"] == "delete_data_file"
        assert json.loads(request.content) == _FULL_SINGLE_FILE
        return httpx.Response(200, json=f"{_FULL_SINGLE_FILE} deleted")

    ctx = make_ctx(httpx.MockTransport(handler))
    result = await delete_data_file(ctx, file=_FULL_SINGLE_FILE, confirm=True)

    assert result == {"file": _FULL_SINGLE_FILE, "deleted": True, "message": f"{_FULL_SINGLE_FILE} deleted"}


@pytest.mark.anyio
async def test_delete_data_file_raises_when_octobot_rejects_the_delete():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Can't delete does_not_exist.data (file can't be found)")

    ctx = make_ctx(httpx.MockTransport(handler))
    with pytest.raises(ToolError) as exc_info:
        await delete_data_file(ctx, file="does_not_exist.data", confirm=True)
    assert "DataFileDeleteRejectedError" in str(exc_info.value)
    assert "does_not_exist.data" in str(exc_info.value)


# --- import_data_file: not gated (ADR-0003/FR-26) ---------------------------


@pytest.mark.anyio
async def test_import_data_file_succeeds_and_returns_the_newly_appeared_file():
    new_file = "ExchangeHistoryDataCollector_9999999999.1234567.data"
    added_html = DATA_COLLECTOR_HTML.replace(
        "</tbody>",
        f"""
<tr class="selectable_datafile" data-file-type="exchange">
<td>DOT/USDT</td>
<td data-order="1">01 January 2026 at 00:00:00</td>
<td>10</td>
<td>binance</td>
<td>1h</td>
<td>{new_file}</td>
<td class="text-center">
<a class="btn btn-outline-primary delete_data_file waves-effect" data-file="{new_file}" data-toggle="tooltip">
<i class="fas fa-trash"></i>
</a>
</td>
</tr>
</tbody>""",
    )

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            calls["n"] += 1
            html = DATA_COLLECTOR_HTML if calls["n"] == 1 else added_html
            return httpx.Response(200, text=html)
        assert request.method == "POST"
        assert dict(request.url.params)["action_type"] == "import_data_file"
        return httpx.Response(200, text=added_html)

    ctx = make_ctx(httpx.MockTransport(handler))
    import base64

    result = await import_data_file(ctx, file_base64=base64.b64encode(b"fake data").decode(), filename=new_file)

    assert result == {"filename": new_file, "imported": True, "message": "Historical data collection file saved"}


@pytest.mark.anyio
async def test_import_data_file_raises_when_no_new_file_appears():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text=DATA_COLLECTOR_HTML)
        return httpx.Response(200, text=DATA_COLLECTOR_HTML)  # unchanged -- OctoBot silently rejected it.

    ctx = make_ctx(httpx.MockTransport(handler))
    import base64

    with pytest.raises(ToolError) as exc_info:
        await import_data_file(ctx, file_base64=base64.b64encode(b"garbage").decode(), filename="garbage.data")
    assert "DataFileImportFailedError" in str(exc_info.value)


@pytest.mark.anyio
async def test_import_data_file_rejects_invalid_base64():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no OctoBot call should happen for invalid base64")

    ctx = make_ctx(httpx.MockTransport(handler))
    with pytest.raises(ToolError) as exc_info:
        await import_data_file(ctx, file_base64="not-valid-base64!!!", filename="x.data")
    assert "DataFileImportFailedError" in str(exc_info.value)


# --- get_available_timeframes_for_collection --------------------------------


@pytest.mark.anyio
async def test_get_available_timeframes_for_collection_passes_through_octobots_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/data_collector"
        params = dict(request.url.params)
        assert params["action_type"] == "available_timeframes_list"
        assert params["exchange"] == "binance"
        return httpx.Response(200, json=["1m", "5m", "1h", "1d"])

    ctx = make_ctx(httpx.MockTransport(handler))
    result = await get_available_timeframes_for_collection(ctx, exchange="binance")

    assert result == {"time_frames": ["1m", "5m", "1h", "1d"]}
