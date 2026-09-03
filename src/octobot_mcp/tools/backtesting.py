"""Backtest job wiring: `start_backtest`, `get_job_result`, and the
Socket.IO progress watcher (milestone 8, docs/tasklist.md item 8).

This module is what finally makes `octobot_mcp.jobs.JobStore` (milestone
7's pure state-machine scaffolding) talk to a real OctoBot instance:
`start_backtest` submits a real backtest run and spawns a background
watcher task; that watcher drives the job record through
`queued -> running -> completed|failed|cancelled` by polling OctoBot's
Socket.IO `/backtesting` namespace on a fixed interval; `get_job_result`
reads the final report back out once a job is `completed`.
`cancel_job`/`get_job_status`/`list_jobs` themselves are unchanged
(`octobot_mcp.tools.jobs`) -- this module's watcher is simply what now
*honors* the `cancel_requested` flag that milestone 7's `cancel_job`
already knew how to set.

## Step 0: Socket.IO compatibility check (ADR-0002's risk table, done first)

Per this milestone's own explicit first task, before writing any watcher
code: connected a real `python-socketio` `AsyncClient()` to the live test
instance (`http://192.168.178.100:5001`, `GET /api/ping` confirmed it was
up, `GET /api/version` -> `"OctoBot 2.1.1"`), targeting the `/backtesting`
namespace, and proved the exact round trip ADR-0002/this milestone
describes: `sio.emit("backtesting_status", namespace="/backtesting")`
(**no payload, and NOT `python-socketio`'s `call()`/ack-callback
pattern** -- confirmed against source, `websockets/backtesting.py`'s
`on_backtesting_status` calls plain `flask_socketio.emit(...)`, never an
ack-style callback) is answered by the server emitting an event of the
SAME name (`backtesting_status`) back on the same namespace, payload shape
`{"status": ..., "progress": ..., "errors": ...}` exactly as ADR-0002
already documented from source. **Result: `python-socketio` 5.16.4 (the
current PyPI release at the time of this check) round-tripped cleanly on
the very first attempt** -- Engine.IO protocol v4 (`EIO=4`), no
version/protocol mismatch, no pin adjustment beyond a conservative
`>=5.11,<6` floor in `pyproject.toml` was needed. Two things confirmed
along the way, both relevant to this module's design below:
- Connecting to the namespace **itself** triggers one `backtesting_status`
  push from the server before any explicit `emit` is even sent (confirmed
  against source: `BacktestingNamespace.on_connect` calls
  `self.on_backtesting_status()` itself). This module's watcher never
  relies on that connect-time push specifically -- `_BacktestingStatusChannel`
  below only resolves its response future from whatever
  `backtesting_status` event arrives next after ITS OWN explicit `emit`,
  so the extra connect-time push is harmless (it just arrives one tick
  "early" relative to the watcher's own request/response pairing, well
  within OctoBot's fundamentally broadcast-style event semantics -- there
  is no per-request correlation id in this protocol at all).
- With no backtest running, the live instance reported
  `{"status": "not started", "progress": 0, "errors": 0}` -- confirming
  `status` values beyond `starting|computing|finished` (ADR-0002's
  research) genuinely occur, exactly as ADR-0002's context section already
  anticipated ("status will likely be something other than 'computing' if
  no backtest is currently running").

## Open question #6 (`current_bot_data` mode field values) -- RESOLVED against source

Traced `models/backtesting.py::start_backtesting_using_current_bot_data`/
`_start_backtesting` and `models/trading.py::ensure_valid_exchange_id` on
the `OctoBot-Tentacles` `master` branch (the same package this project's
prior milestones already verified `controllers/backtesting.py` against).
Confirmed, not guessed:
- `exchange_id`: validated via
  `octobot_trading.api.get_exchange_manager_from_exchange_id(exchange_id)`
  (a `KeyError` there is what `ensure_valid_exchange_id` turns into
  `MissingExchangeIdError` below) -- this is the SAME internal exchange id
  this project's own `octobot_mcp.tools.exchanges.get_exchange_details`
  tool already returns as its `exchange_id` field (`GET
  /api/first_exchange_details`). **Only validated when not `None`** --
  see the `exchange_id`-vs-`data_source` interaction note on
  `start_backtest`'s own docstring below for why omitting it is not
  always safe despite the route treating it as optional.
- `exchange_type` (OctoBot's own body key; this project's `mode="current_bot_data"`
  input keeps the same name): passed through `_parse_trading_type`, whose
  accepted values (confirmed against `octobot_trading.enums.
  ExchangeTypes`/`FutureContractType` and `octobot_commons.constants.
  USE_CURRENT_PROFILE`) are `None`/omitted (defaults to "use the current
  profile's own trading type"), `"use_current_profile"`, `"spot"`,
  `"inverse_perpetual"`, `"linear_perpetual"`, or `"margin"` -- any other
  string raises a `RuntimeError` inside OctoBot, surfaced here as a
  generic `BacktestStartFailedError` (there is no dedicated error code for
  this specific case in the spec). **One value is accepted by
  `_parse_trading_type` but not usable in practice**: `"option"` maps to
  `trading_enums.OptionContractType.LINEAR_PERPETUAL`, but that enum
  (confirmed against `octobot_trading.enums.OptionContractType`) only
  defines `INVERSE_EXPIRABLE`/`LINEAR_EXPIRABLE` -- no `LINEAR_PERPETUAL`
  member exists, so this branch would itself crash inside OctoBot with an
  `AttributeError` if ever reached. Not documented as a supported value
  above for that reason.
- `data_source`: confirmed to be **either** the literal string
  `"current_bot_data"` (`models.backtesting.CURRENT_BOT_DATA`, also the
  controller's own default when this key is omitted from the JSON body)
  -- meaning "use a live snapshot of the bot's own current exchange data,
  collected via `exchange_id`" -- **or any other string, which OctoBot
  treats as a literal data file name** (`files = [data_source]`), making
  that case behave exactly like `data_files` mode's single-file case
  despite going through the `current_bot_data` route/action. Both
  behaviors are implemented exactly as confirmed.

## Design choices made in this module (documented per the milestone's own instructions)

- **Per-job Socket.IO connect/disconnect, not one long-lived shared
  client.** `_watch_until_done` opens a fresh `socketio.AsyncClient()` when
  a job starts watching and disconnects it (in a `finally`) the moment
  that job's watch loop ends, for any reason. Chosen over a long-lived
  shared client because OctoBot itself only ever runs one backtest at a
  time (ADR-0002) -- a per-job connection has no idle-reconnect/staleness
  state to manage across the potentially-long gaps between backtests, at
  the cost of a handshake per job, which is negligible next to a
  minutes-long backtest.
- **The watcher task reference lives on `JobStore`, not this module.**
  `start_backtest` returns immediately without awaiting the watcher
  (the entire point of the async job pattern, ADR-0002); a bare
  `asyncio.create_task(...)` result with nothing referencing it is a
  well-known asyncio footgun (silent GC mid-flight), so
  `JobStore.track_watcher_task` (see its own docstring) is what keeps that
  reference alive for the job's lifetime and logs any exception that
  somehow escapes this module's own try/except/finally.
- **A discovered OctoBot query-param quirk, verified against source**:
  `controllers/backtesting.py` reads `auto_stop`/`reset_tentacle_config`
  as `flask.request.args.get(key, False)` -- since Flask query args are
  always strings, sending EITHER `auto_stop=true` OR `auto_stop=false`
  produces a non-empty (hence truthy) string either way; only OMITTING the
  key entirely yields OctoBot's own Python `False` default. This is
  different from `run_on_common_part_only`, whose controller-side check
  (`.get(key, "true") == "true"`) correctly distinguishes an explicit
  `"true"`/`"false"` string. This module therefore only ever includes
  `auto_stop`/`reset_tentacle_config` in the query string when the caller
  wants them `True`; `run_on_common_part_only` is always sent explicitly.

## Open question #3 (`get_backtesting_report`'s `report` schema) -- RESOLVED live

See `get_job_result`'s own docstring below for the real payload shape
captured against the live instance during this milestone's smoke test: a
real `data_files`-mode backtest run to completion, `DailyTradingMode`
profile, `BTC/EUR` on `binance`, ~26 watcher ticks over ~26s.

No `from __future__ import annotations` here: see
`octobot_mcp.tools._shared.with_error_handling`'s docstring for why
postponed annotations would silently break this module's `Context`
injection.
"""

import asyncio
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx2 as httpx
import socketio
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context

from octobot_mcp.client import request_json
from octobot_mcp.context import get_client, get_job_store
from octobot_mcp.eta import WATCHER_INTERVAL_SECONDS
from octobot_mcp.errors import (
    BacktestStartFailedError,
    CompareStrategiesResultUnsupportedError,
    JobNotCompletedError,
    JobNotFoundError,
    MissingExchangeIdError,
    OctoBotAPIError,
    OctoBotClientError,
    OctoBotConnectionError,
    OctoBotServerError,
    OctoBotTimeoutError,
)
from octobot_mcp.jobs import TERMINAL_STATES, JobStore
from octobot_mcp.tools._shared import with_error_handling

#: NFR-2: the watcher's fixed polling interval, named so it is easy to
#: tune independently of everything else. Deliberately far coarser than
#: OctoBot's own 50ms UI cadence (ADR-0002) -- see the module docstring.
#: Re-exported here from `octobot_mcp.eta` (milestone 9, docs/tasklist.md
#: item 9) rather than defined locally: that module's staleness check
#: (`STALENESS_MULTIPLE * WATCHER_INTERVAL_SECONDS`) must always use the
#: exact same interval this watcher loop actually ticks on, so there is a
#: single canonical definition rather than two hand-kept-in-sync copies.

#: NFR-5: Socket.IO connect timeout (distinct from `client.py`'s 10s REST
#: timeout, which governs plain HTTP calls only).
SOCKETIO_CONNECT_TIMEOUT_SECONDS = 5.0

#: How long one `backtesting_status` request/response round trip may take
#: before this module treats it as a hard error (spec's watcher step 6) --
#: generous relative to the 1.5s tick interval; OctoBot's own frontend
#: polls this same event every 50ms, so a healthy server responds far
#: faster than this in practice.
SOCKETIO_STATUS_TIMEOUT_SECONDS = 5.0

_BACKTESTING_PATH = "/backtesting"
_BACKTESTING_NAMESPACE = "/backtesting"

#: The exact literal text of OctoBot's own `MissingExchangeId.EXPLANATION`
#: (confirmed against source, `Services/Interfaces/web_interface/errors.py`)
#: -- matched verbatim to tell this specific failure apart from any other
#: `start_backtesting_with_current_bot_data` failure, both of which
#: otherwise arrive as an indistinguishable HTTP 500.
_MISSING_EXCHANGE_ID_EXPLANATION = (
    "Invalid exchange id, this might be due to a recent restart. "
    "Refresh this page if the error persists"
)

#: Sent on the stop-backtesting/report-fetch calls for the same reason as
#: `octobot_mcp.tools.profiles`' identically-named constant: OctoBot's
#: global error handler only renders a readable one-line JSON error
#: string (rather than a full HTML page) when the *request's* own
#: Content-Type was `application/json`. Duplicated here rather than
#: imported from `profiles.py`: this project's established convention is
#: one small per-module copy of this kind of tiny adapter-local helper
#: rather than a shared cross-domain utility (see e.g. `profiles.py`'s own
#: `_request_raw`/`_extract_error_text`, which milestone 3-6 already
#: duplicated locally rather than factoring out).
_JSON_ERROR_HINT_HEADERS = {"Content-Type": "application/json"}


def _job_not_found(job_id: str) -> JobNotFoundError:
    """Build `JobNotFoundError` with the same wording as `octobot_mcp.tools.jobs`'s
    identically-purposed private helper (small intentional duplication, see
    that module and this one's own docstrings for why)."""
    return JobNotFoundError(
        f"No job found with id {job_id!r}. If the MCP server has restarted since "
        "this job was created, its in-memory record no longer exists -- this is "
        "expected, not a bug (the job store does not persist across restarts)."
    )


async def _request_raw(client: httpx.AsyncClient, method: str, path: str, **kwargs: Any) -> httpx.Response:
    """One request without decoding JSON, classifying only transport failures.

    `octobot_mcp.client.request_json` can't be reused for the start/stop
    backtesting actions: depending on the outcome, OctoBot returns a bare
    JSON string, an HTML error page, or plain error text with a misleading
    `Content-Type: application/json` header -- never a single uniform
    shape `request_json` could decode. Mirrors `octobot_mcp.tools.profiles`'
    identically-named/purposed helper (duplicated, not imported -- see this
    module's docstring).
    """
    try:
        return await client.request(method, path, **kwargs)
    except httpx.TimeoutException as exc:
        raise OctoBotTimeoutError(f"Request to OctoBot timed out: {method} {path}") from exc
    except httpx.HTTPError as exc:
        raise OctoBotConnectionError(f"Could not reach OctoBot: {method} {path} ({exc})") from exc


def _extract_error_text(response: httpx.Response) -> str:
    """Best-effort extraction of OctoBot's own error text from a non-2xx response.

    Mirrors `octobot_mcp.tools.profiles`'s identically-named/purposed
    helper (duplicated, not imported -- see this module's docstring).
    """
    try:
        body = response.json()
    except ValueError:
        return response.text.strip() or f"HTTP {response.status_code} with an empty body"
    if isinstance(body, dict) and "text" in body:
        return str(body["text"])
    return str(body)


@dataclass(frozen=True)
class _StartSpec:
    """Everything `_run_backtest_watcher` needs to submit one backtest start action.

    Built by `_data_files_mode_spec`/`_current_bot_data_mode_spec` from
    `start_backtest`'s validated arguments *before* any job is created or
    any background task spawned, so a bad call (unknown `mode`, missing
    `files`) fails immediately and synchronously rather than leaving a
    dangling queued job behind.
    """

    action_type: str
    extra_params: dict[str, Any]
    body: dict[str, Any]
    requested_source: str | None


def _data_files_mode_spec(
    *,
    files: list[str] | None,
    start_timestamp: int | None,
    end_timestamp: int | None,
    enable_logs: bool,
    source: str | None,
    auto_stop: bool,
    run_on_common_part_only: bool,
    reset_tentacle_config: bool,
) -> _StartSpec:
    """`mode="data_files"`: `start_backtesting` [V] -- see module docstring's route table."""
    if not files:
        raise BacktestStartFailedError(
            "start_backtest(mode='data_files') requires a non-empty 'files' list "
            "(exact data file name(s), e.g. as listed in OctoBot's own "
            "'/backtesting' page's data-files table -- there is currently no MCP "
            "tool exposing this list; it must be known out of band)."
        )
    body: dict[str, Any] = {"files": files}
    if start_timestamp is not None:
        body["start_timestamp"] = start_timestamp
    if end_timestamp is not None:
        body["end_timestamp"] = end_timestamp
    if enable_logs:
        body["enable_logs"] = enable_logs
    extra_params: dict[str, Any] = {"run_on_common_part_only": run_on_common_part_only}
    if auto_stop:
        extra_params["auto_stop"] = True
    if reset_tentacle_config:
        extra_params["reset_tentacle_config"] = True
    return _StartSpec(
        action_type="start_backtesting",
        extra_params=extra_params,
        body=body,
        requested_source=source,
    )


def _current_bot_data_mode_spec(
    *,
    data_source: str | None,
    exchange_id: str | None,
    exchange_type: str | None,
    profile_id: str | None,
    name: str | None,
    start_timestamp: int | None,
    end_timestamp: int | None,
    enable_logs: bool,
    source: str | None,
    auto_stop: bool,
    reset_tentacle_config: bool,
) -> _StartSpec:
    """`mode="current_bot_data"`: `start_backtesting_with_current_bot_data` [V] -- see module docstring's Q6 resolution."""
    body: dict[str, Any] = {}
    if data_source is not None:
        body["data_source"] = data_source
    if exchange_id is not None:
        body["exchange_id"] = exchange_id
    if exchange_type is not None:
        body["exchange_type"] = exchange_type
    if profile_id is not None:
        body["profile_id"] = profile_id
    if name is not None:
        body["name"] = name
    if start_timestamp is not None:
        body["start_timestamp"] = start_timestamp
    if end_timestamp is not None:
        body["end_timestamp"] = end_timestamp
    if enable_logs:
        body["enable_logs"] = enable_logs
    extra_params: dict[str, Any] = {}
    if auto_stop:
        extra_params["auto_stop"] = True
    if reset_tentacle_config:
        extra_params["reset_tentacle_config"] = True
    return _StartSpec(
        action_type="start_backtesting_with_current_bot_data",
        extra_params=extra_params,
        body=body,
        requested_source=source,
    )


async def _post_start_action(client: httpx.AsyncClient, params: dict[str, Any], body: dict[str, Any]) -> None:
    """POST the start action; raises `MissingExchangeIdError`/`BacktestStartFailedError` on failure.

    Success is any status < 400 -- the response body on success is just
    `flask.jsonify("Backtesting started")` (confirmed against source),
    which carries no information this tool needs (the real "did it
    actually start" signal is the watcher's own subsequent
    `backtesting_status` polling).
    """
    response = await _request_raw(client, "POST", _BACKTESTING_PATH, params=params, json=body)
    if response.status_code < 400:
        return
    text = _extract_error_text(response)
    if text.strip() == _MISSING_EXCHANGE_ID_EXPLANATION:
        raise MissingExchangeIdError(text)
    raise BacktestStartFailedError(f"OctoBot failed to start the backtest (HTTP {response.status_code}): {text}")


async def _post_stop_action(client: httpx.AsyncClient) -> None:
    """POST `action_type=stop_backtesting` (no body). Classified like any other Tier-A write (NFR-4)."""
    response = await _request_raw(
        client,
        "POST",
        _BACKTESTING_PATH,
        params={"action_type": "stop_backtesting"},
        headers=_JSON_ERROR_HINT_HEADERS,
    )
    if response.status_code >= 500:
        raise OctoBotServerError(
            f"OctoBot returned server error {response.status_code} stopping the backtest: "
            f"{_extract_error_text(response)}",
            status_code=response.status_code,
        )
    if response.status_code >= 400:
        raise OctoBotClientError(
            f"OctoBot returned client error {response.status_code} stopping the backtest: "
            f"{_extract_error_text(response)}",
            status_code=response.status_code,
        )


async def _fetch_backtesting_report(client: httpx.AsyncClient, run_source: str) -> dict[str, Any]:
    """`GET /backtesting?update_type=backtesting_report&source=<run_source>` [V].

    Always a clean `flask.jsonify(...)` response on the one code path
    confirmed against source (`models.get_backtesting_report`) -- no
    distinct error branch exists there, so this reuses `request_json`
    directly, unlike the start/stop actions above.
    """
    return await request_json(
        client,
        "GET",
        _BACKTESTING_PATH,
        params={"update_type": "backtesting_report", "source": run_source},
    )


class _BacktestingStatusChannel:
    """Bridges OctoBot's plain (non-ack) `backtesting_status` Socket.IO event
    round trip into an async request/await-response shape for the watcher
    loop.

    Confirmed against OctoBot source (`websockets/backtesting.py`) and this
    milestone's own standalone compatibility check (see this module's
    docstring): the client emits `backtesting_status` with no payload, and
    the server replies by emitting an event of the SAME name back on the
    SAME namespace -- a plain broadcast-style event, NOT `python-socketio`'s
    `call()`/ack-callback request/reply pattern (OctoBot's own handler
    calls plain `flask_socketio.emit(...)`, never with a matching
    `callback=`). This class bridges that manually with a fresh
    `asyncio.Future` created before each `request_status()` call's own
    `emit`, exactly per this milestone's own task guidance.
    """

    def __init__(self, sio: "socketio.AsyncClient") -> None:
        self._sio = sio
        self._pending: asyncio.Future | None = None
        sio.on("backtesting_status", self._on_status, namespace=_BACKTESTING_NAMESPACE)

    def _on_status(self, data: dict[str, Any]) -> None:
        if self._pending is not None and not self._pending.done():
            self._pending.set_result(data)

    async def request_status(self) -> dict[str, Any]:
        """Emit one `backtesting_status` request and await its response.

        A timeout here (`asyncio.TimeoutError`, deliberately left
        unhandled) is a hard error per spec's watcher step 6 -- it
        propagates up to `_run_backtest_watcher`'s catch-all, which fails
        the job in a `finally`-guaranteed lock release (INV-1).
        """
        loop = asyncio.get_running_loop()
        self._pending = loop.create_future()
        await self._sio.emit("backtesting_status", namespace=_BACKTESTING_NAMESPACE)
        try:
            return await asyncio.wait_for(self._pending, timeout=SOCKETIO_STATUS_TIMEOUT_SECONDS)
        finally:
            self._pending = None


async def _watch_until_done(
    client: httpx.AsyncClient,
    store: JobStore,
    job_id: str,
    run_source: str,
    *,
    socketio_client_factory: Callable[[], "socketio.AsyncClient"],
    interval_seconds: float,
) -> None:
    """Spec's "Background watcher" steps 2-5, for one already-`running` job.

    Connects a fresh Socket.IO client (see module docstring on the
    per-job-connection choice), then loops every `interval_seconds` until
    OctoBot reports `status == "finished"`, `cancel_requested` becomes
    true on the job record, or a hard error occurs (an unhandled exception
    here propagates to `_run_backtest_watcher`'s catch-all -- this
    function itself does not implement INV-1's lock release; its caller's
    `async with` does).
    """
    sio = socketio_client_factory()
    await sio.connect(
        str(client.base_url),
        namespaces=[_BACKTESTING_NAMESPACE],
        wait_timeout=SOCKETIO_CONNECT_TIMEOUT_SECONDS,
    )
    channel = _BacktestingStatusChannel(sio)
    try:
        start_monotonic = time.monotonic()
        while True:
            job = store.get(job_id)
            if job is None:
                return
            if job.cancel_requested:
                # Spec step 5: stop is best-effort -- a failure to confirm
                # the stop with OctoBot is recorded but does not, by
                # itself, keep this job from finishing its cancellation
                # (there would be no other way to ever release the lock
                # for a stop call OctoBot itself refuses to acknowledge).
                try:
                    await _post_stop_action(client)
                except OctoBotAPIError as exc:
                    store.append_error(job_id, f"Failed to confirm stop with OctoBot: {exc}")
                store.confirm_cancelled(job_id)
                return
            payload = await channel.request_status()
            elapsed_seconds = time.monotonic() - start_monotonic
            store.record_watcher_tick(
                job_id,
                elapsed_seconds=elapsed_seconds,
                progress_percent=float(payload.get("progress") or 0.0),
                error_count=int(payload.get("errors") or 0),
            )
            if payload.get("status") == "finished":
                report = await _fetch_backtesting_report(client, run_source)
                store.complete(job_id, result=report)
                return
            await asyncio.sleep(interval_seconds)
    finally:
        await sio.disconnect()


async def _run_backtest_watcher(
    client: httpx.AsyncClient,
    store: JobStore,
    job_id: str,
    spec: _StartSpec,
    *,
    socketio_client_factory: Callable[[], "socketio.AsyncClient"] = socketio.AsyncClient,
    interval_seconds: float = WATCHER_INTERVAL_SECONDS,
) -> None:
    """Spec's "Background watcher" steps 1 and 6, wrapping `_watch_until_done`.

    Run as a background `asyncio.Task` by `start_backtest` (never awaited
    inline -- see that function's docstring). `socketio_client_factory`/
    `interval_seconds` default to the real client/the real NFR-2 constant
    in production and exist as parameters purely so tests can substitute a
    fake Socket.IO client and a near-zero interval without patching module
    globals.

    INV-1 (`backtest_execution_lock` released on every code path): the
    entire body runs inside `async with store.backtest_execution_lock`,
    whose `__aexit__` is guaranteed to run on any exit from the block
    (normal return OR exception) -- functionally identical to an explicit
    `try/finally: lock.release()`, and arguably more robust since it is
    enforced by the language rather than by remembering to write the
    `finally` by hand. Any exception raised anywhere below (start action,
    `_watch_until_done`, or a bug in this function itself) is caught by
    the single `except Exception` here, which fails the job -- there is no
    path out of this function that leaves the lock held or the job stuck
    in a non-terminal state forever.
    """
    async with store.backtest_execution_lock:
        job = store.get(job_id)
        if job is None or job.state in TERMINAL_STATES:
            # Cancelled while still queued for the lock: JobStore.cancel()
            # transitions a queued job straight to "cancelled" without
            # ever touching this lock (spec's cancel_job "queued" branch),
            # so by the time we acquire it there may be nothing left to do.
            return
        try:
            run_source = spec.requested_source or str(uuid.uuid4())
            params = {"action_type": spec.action_type, "source": run_source, **spec.extra_params}
            try:
                await _post_start_action(client, params, spec.body)
            except OctoBotAPIError as exc:
                store.fail(job_id, error=str(exc))
                return
            store.start(job_id, octobot_run_source=run_source)
            await _watch_until_done(
                client,
                store,
                job_id,
                run_source,
                socketio_client_factory=socketio_client_factory,
                interval_seconds=interval_seconds,
            )
        except Exception as exc:  # noqa: BLE001 -- INV-1: a watcher bug must never leave a job stuck.
            store.fail(job_id, error=f"Unhandled watcher error: {exc}")


@with_error_handling
async def start_backtest(
    ctx: Context,
    mode: str,
    files: list[str] | None = None,
    start_timestamp: int | None = None,
    end_timestamp: int | None = None,
    enable_logs: bool = False,
    source: str | None = None,
    auto_stop: bool = False,
    run_on_common_part_only: bool = True,
    reset_tentacle_config: bool = False,
    exchange_id: str | None = None,
    exchange_type: str | None = None,
    profile_id: str | None = None,
    name: str | None = None,
    data_source: str | None = None,
) -> Any:
    """Start a real OctoBot backtest run in the background and return immediately.

    Not confirm-gated (ADR-0003): starts a simulation, never touches live
    trading or destroys data. Two mutually exclusive `mode`s (all other
    parameters are mode-specific; unused ones for the chosen mode are
    ignored):

    - `mode="data_files"` -> OctoBot's `start_backtesting` action [V].
      Requires `files` (a non-empty list of exact data file names --
      there is currently no MCP tool exposing what's available; the agent
      must already know a valid name, e.g. from OctoBot's own
      `/backtesting` page). `start_timestamp`/`end_timestamp` are epoch
      **milliseconds** (confirmed against source: `_start_backtesting`
      divides by 1000 before use), matching this project's convention
      elsewhere. `run_on_common_part_only` defaults to `True` (OctoBot's
      own default when omitted).
    - `mode="current_bot_data"` -> OctoBot's
      `start_backtesting_with_current_bot_data` action [V]. Every field
      is technically optional at the HTTP layer (confirmed against
      source), but **`exchange_id` is effectively required unless
      `data_source` names an explicit data file**: if `data_source` is
      omitted or `None`, OctoBot defaults it to `"current_bot_data"`
      (use a live snapshot of the bot's own current exchange data), which
      requires a valid `exchange_id` -- omitting `exchange_id` in that
      case fails with a *generic* `BacktestStartFailedError`, not
      `MissingExchangeIdError` (that error is only raised for an
      actually-provided-but-unrecognized id; see this module's docstring,
      "Open question #6"). `exchange_id` is the same value
      `get_exchange_details` returns as its `exchange_id` field.
      `exchange_type` accepts `None`, `"use_current_profile"`, `"spot"`,
      `"inverse_perpetual"`, `"linear_perpetual"`, or `"margin"` (any
      other value raises inside OctoBot, surfaced as
      `BacktestStartFailedError`).

    `source` (both modes) is an optional caller-supplied override for
    OctoBot's own per-run `source` identifier; if omitted (the expected
    case), a fresh UUID is generated and stored as the job's
    `octobot_run_source`, then reused automatically for the later
    `get_job_result` report fetch -- the caller never needs to manage this
    value.

    Output: `{"job_id": str, "state": "queued"}` -- always `"queued"`
    immediately after this call (the background watcher has not yet had a
    chance to run at all: `asyncio.create_task` schedules it, it does not
    run any of it synchronously). Poll `get_job_status(job_id)` to observe
    the transition to `"running"` and then `progress_percent` advancing;
    call `get_job_result(job_id)` once `state == "completed"`.

    Errors: `BacktestStartFailedError` for an unknown `mode`, a missing
    required field for the chosen mode, or any other start failure;
    `MissingExchangeIdError` specifically for `current_bot_data` mode's
    confirmed exception. Both are raised from the background watcher (not
    this function) and surface as the job's `errors`/`state="failed"` via
    `get_job_status` -- `start_backtest` itself only raises synchronously
    for input validation caught *before* any job is created (unknown
    `mode`, missing `files`), so a bad call never leaves a dangling queued
    job behind.
    """
    if mode == "data_files":
        spec = _data_files_mode_spec(
            files=files,
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
            enable_logs=enable_logs,
            source=source,
            auto_stop=auto_stop,
            run_on_common_part_only=run_on_common_part_only,
            reset_tentacle_config=reset_tentacle_config,
        )
    elif mode == "current_bot_data":
        spec = _current_bot_data_mode_spec(
            data_source=data_source,
            exchange_id=exchange_id,
            exchange_type=exchange_type,
            profile_id=profile_id,
            name=name,
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
            enable_logs=enable_logs,
            source=source,
            auto_stop=auto_stop,
            reset_tentacle_config=reset_tentacle_config,
        )
    else:
        raise BacktestStartFailedError(
            f"start_backtest: unknown mode {mode!r}; expected 'data_files' or 'current_bot_data'."
        )

    client = get_client(ctx)
    store = get_job_store(ctx)
    job = store.create_job(kind="backtest")
    task = asyncio.create_task(_run_backtest_watcher(client, store, job.job_id, spec))
    store.track_watcher_task(job.job_id, task)
    return {"job_id": job.job_id, "state": job.state}


@with_error_handling
async def get_job_result(ctx: Context, job_id: str) -> Any:
    """Fetch a completed backtest job's final report.

    Requires `state == "completed"`; raises `JobNotCompletedError` naming
    the current state otherwise -- never returns partial results, even if
    OctoBot itself would technically serve a report for an in-progress
    run. Raises `JobNotFoundError` for an unknown `job_id` (same as
    `get_job_status`), and `CompareStrategiesResultUnsupportedError` for a
    `compare_strategies` job (not implemented until docs/tasklist.md item
    10 -- see that error's own docstring).

    For `kind: "backtest"`, output is `{"report": <passthrough>, "trades":
    <passthrough>}` from OctoBot's own `GET /backtesting?update_type=
    backtesting_report` response (NFR-8: no renaming/reshaping).

    **Real `report`/`trades` shape, captured live against the OctoBot
    2.1.1 test instance this milestone (resolves open question #3) --
    `data_files` mode, `DailyTradingMode` profile, `BTC/EUR` on
    `binance`**, exactly as OctoBot returned it (field names/nesting
    verbatim, values are example data from that one run):
    ```
    {
      "report": {
        "bot_report": {
          "starting_portfolio": {"binance": {"BTC": {"available": 10.0, "total": 10.0}, "USDT": {...}}},
          "end_portfolio": {"binance": {"BTC": {...}, "EUR": {...}, "USDT": {...}}},
          "profitability": {"binance": -15.223942726263024},
          "market_average_profitability": {"binance": -16.839423647357034},
          "reference_market": "EUR",
          "trading_mode": "DailyTradingMode"
        },
        "chart_identifiers": [
          {"exchange_id": "...", "exchange_name": "binance", "symbol": "BTC/EUR", "time_frames": ["1m", "1h", "4h", "1d"]}
        ],
        "errors_count": 0,
        "symbol_report": [{"BTC/EUR": 0.8013392638261951}]
      },
      "trades": [
        {
          "SoR": "Simulated", "amount": "7.99127178", "cost": "535066.3103340852",
          "date": "2026-05-02 16:00:00", "exchange": "binance", "fee_cost": "535.0663103340852",
          "fee_currency": "EUR", "id": "b5eb50fd-...", "market": "EUR", "price": "66956.34",
          "ref_market_cost": 535066.3103340853, "symbol": "BTC/EUR", "time": 1777737600,
          "type": "SELL MARKET"
        },
        ...
      ]
    }
    ```
    Confirms the spec's own explicit warning was correct: `profitability`
    genuinely exists, but nested three levels deep
    (`report.bot_report.profitability`, itself keyed by exchange name, not
    a bare float) -- NOT at the top level of `report` as a naive read of
    the spec's placeholder field list (`profitability`/`max_drawdown`/
    `win_rate`) might have assumed; `max_drawdown`/`win_rate` were not
    observed anywhere in this real payload at all. `amount`/`cost`/
    `fee_cost`/`price` in each trade are numeric-looking **strings**, not
    JSON numbers (`ref_market_cost` IS a JSON number) -- passed through
    exactly as OctoBot sent them (NFR-8), not coerced. This tool does not
    hardcode or validate against any of these field names anywhere in its
    own code (per NFR-8/the spec's own instruction not to assume a field
    list) -- this is documentation of what was observed, not a schema this
    tool depends on.
    """
    store = get_job_store(ctx)
    job = store.get(job_id)
    if job is None:
        raise _job_not_found(job_id)
    if job.state != "completed":
        raise JobNotCompletedError(
            f"Job {job_id!r} is not completed yet (current state: {job.state!r}). "
            "get_job_result never returns partial results -- poll get_job_status "
            "until state == 'completed', then retry."
        )
    if job.kind == "compare_strategies":
        raise CompareStrategiesResultUnsupportedError(
            f"get_job_result({job_id!r}): this job's kind is 'compare_strategies', "
            "which is not implemented yet (docs/tasklist.md item 10)."
        )
    result = job.result or {}
    return {"report": result.get("report"), "trades": result.get("trades")}


def register(mcp: MCPServer) -> None:
    """Register this module's tools onto `mcp` (called once from `octobot_mcp.tools.register_all`)."""
    mcp.tool()(start_backtest)
    mcp.tool()(get_job_result)
