"""Historical data collection tools (milestone 15, docs/tasklist.md item 15,
ADR-0007/ADR-0008 -- a genuinely new-scope addendum, not part of v1).

`start_data_collection` is `start_backtest`'s sibling: a `kind:
"data_collection"` job (ADR-0007), submitted via `POST /data_collector
?action_type=start_collector` and watched via OctoBot's `/data_collector`
Socket.IO namespace, structurally mirroring
`octobot_mcp.tools.backtesting`'s `_BacktestingStatusChannel`/
`_watch_until_done`/`_run_backtest_watcher` (same non-ack emit/await-
response bridge, same `WATCHER_INTERVAL_SECONDS` tick interval, same INV-1
lock-released-in-`finally` guarantee via `async with`, same bounded-retry
resilience from milestone 14). `list_data_files`/`delete_data_file`/
`import_data_file`/`get_available_timeframes_for_collection` are the
remaining tools closing the "collect then backtest against it" workflow
gap `start_backtest(mode="data_files")`'s own docstring already admitted
(ADR-0007's Context section).

## Step 0: live verification session (this milestone's own explicit first task)

Everything below was independently reverified this session against the
live test instance (`http://192.168.178.100:5001`, `GET /api/version` ->
`"OctoBot 2.1.1"` -- the same instance/version every prior milestone's own
verification sessions used), NOT assumed from the ADRs alone, per this
project's own "connect and confirm first" convention (mirroring milestone
8's own Step 0 for `/backtesting`).

**`python-socketio` round trip against `/data_collector` -- confirmed
clean.** A standalone `socketio.AsyncClient()` connected to
`/data_collector`, emitted `data_collector_status` with no payload (NOT
`call()`/ack-style -- confirmed against source, `websockets/
data_collector.py`'s handler calls plain `flask_socketio.emit(...)`), and
received an event of the SAME name back on the SAME namespace, exactly the
broadcast-style round trip ADR-0007 documented from source. No version/
protocol mismatch (same `python-socketio` 5.16.4 already proven for
`/backtesting` in milestone 8).

**Open question #9 (indexing of `current_step`/`total_steps`) -- RESOLVED
live, definitively.** A real collection was submitted for 20 symbols x 15
time frames on `binance` and the raw `(current_step, total_steps,
current_step_percent)` sequence was logged tick by tick:
`total_steps` was exactly `300` (`20 * 15`, confirming "one step per
symbol/timeframe pair"); `current_step` started at `1` on the very first
`"collecting"` tick (never `0` while collecting) and climbed
monotonically, one at a time, up to and including exactly `300` (never
exceeding `total_steps`) on the last `"collecting"` tick before
`"finished"`. **`current_step` is 1-indexed and never exceeds
`total_steps`, confirming ADR-0007's formula's assumption exactly.**
`current_step_percent` was observed as `0` on every single tick across
several runs of varying size (5-30-300 steps) -- never seen non-zero live.
This does not invalidate the formula (each step apparently completes fast
enough, relative to this watcher's polling cadence, that a genuine
mid-step percentage was never actually caught in-flight; the formula
already accounts for it correctly either way), but it is recorded here as
an honest empirical observation, not assumed to always be `0`. On the
final `"finished"` tick, `progress` resets to `{"current_step": 0,
"total_steps": 0, "current_step_percent": 0}` (confirmed, matching
`get_data_collector_status`'s own `"not started"`-shaped default) --
harmless here because `JobStore.complete()` unconditionally sets
`progress_percent = 100.0` regardless of the last recorded tick, exactly
as it already does for `start_backtest` (see that module's own
`_watch_until_done`, whose "finished" tick is recorded via
`record_watcher_tick` BEFORE `store.complete()` overwrites it the same
way).

**Open question #8 (timestamp units) -- RESOLVED, both live AND from
source.** Live: a collection submitted with `startTimestamp`/
`endTimestamp` values 2 days apart, expressed as **epoch milliseconds**,
produced a data file whose rendered date range was exactly that 2-day
window (`"05 September 2026 to 07 September 2026"` for a request made on
`"07 September 2026"`). Source (`Services/Interfaces/web_interface/
models/backtesting.py::_ensure_backtesting_limits`, `Drakkar-Software/
OctoBot-Tentacles` `master`, read this session via `gh api`) independently
confirms the same thing directly: `start_timestamp = start_timestamp /
commons_constants.MSECONDS_TO_SECONDS if start_timestamp else
start_timestamp` -- an explicit divide-by-1000 before use, the exact same
pattern this project's own `start_backtest` already relies on for its own
`start_timestamp`/`end_timestamp`. **Milliseconds, confirmed twice over,
not merely the documented default assumption.**

**A genuine, previously-undocumented OctoBot quirk, found only by testing
live (not visible from source alone without tracing Flask's own request
parsing) -- the JSON body's `time_frames`/`startTimestamp`/`endTimestamp`
keys MUST be present, even when their value is `null`, or the route
raises an unhandled `KeyError` (`HTTP 500`, body: `"... 'startTimestamp'
(KeyError)"` / `"... 'time_frames' (KeyError)"`) instead of a graceful
`(False, <message>)` response.** This differs from `start_backtest`'s own
`_data_files_mode_spec`/`_current_bot_data_mode_spec` convention of only
including an optional key in the body when the caller actually supplied a
value -- that convention would silently 500 here. `_build_start_body`
below therefore ALWAYS includes all three keys (using `None`/`null` when
the caller omitted them), confirmed live to succeed cleanly. `exchange`/
`symbols`, by contrast, are read defensively (`if not exchange`/`if not
symbols`) inside `collect_data_file` itself and correctly produce
`"Please select an exchange."`/`"Please select a trading pair."` even when
entirely absent from the body -- this quirk is specific to the other
three keys.

**Open question #10 (`_ensure_backtesting_limits`'s exact text) --
RESOLVED from source** (`Drakkar-Software/OctoBot`
`octobot/limits.py::ensure_backtesting_limits`/
`_check_max_backtesting_setting`/`_check_max_backtesting_candles_count`,
read this session via `gh api`; not reachable live -- see below). Three
distinct limits exist, each independently able to produce the message
`_ensure_backtesting_limits` wraps as `f"Can't collect data for {symbols}
on {exchange}: {message}"`:
1. Exchange count: `f"The maximum allowed simultaneous backtesting
   exchanges for your selected plan is {limit}"` (irrelevant here --
   `start_data_collection` always submits exactly one exchange).
2. Symbol count: `f"The maximum allowed simultaneous backtesting trading
   pairs for your selected plan is {limit}"`.
3. Time frame count (`"time frames using custom duration"`) OR candle
   history length: `f"For this plan, the maximum allowed backtesting
   candles per time frame is {limit}. With the selected backtesting
   duration, the {time_frame} time frame would cover {candles_count}
   candles. Please select other time frames or reduce the backtesting
   duration."`.
All three limits (`MAX_ALLOWED_EXCHANGES`/`MAX_ALLOWED_SYMBOLS`/
`MAX_ALLOWED_TIME_FRAMES`/`MAX_ALLOWED_BACKTESTING_CANDLES_HISTORY`) default
to `UNLIMITED_ALLOWED` unless a `MAX_ALLOWED_*` environment variable is set
on the OctoBot process itself (a paid-plan enforcement mechanism, not a
per-request parameter) -- **confirming why this session's live attempts to
trigger this path (up to 300 symbol/timeframe steps, a 2-day range) never
did**: this self-hosted test instance runs with the unlimited defaults.
This tool does not hardcode any of the three message templates above
anywhere in its own code -- `DataCollectionStartFailedError` always wraps
whatever text OctoBot actually returns, verbatim, exactly like every other
start-failure path in this project (NFR-8) -- this section exists purely
so an agent reading this docstring understands what a rejection means
if it is ever seen, not so this tool can special-case it.

**Open question #11 (a distinguishable OctoBot-side failure signal) --
INVESTIGATED, and the answer is worse than "no signal": a live test
submitting a collection for a nonexistent exchange name
(`"not_a_real_exchange_xyz"`) returned the SAME `(True, "Historical data
collection started.")` success response as a real exchange, and the
Socket.IO status channel went straight to `{"status": "finished",
"progress": {"current_step": 0, "total_steps": 0,
"current_step_percent": 0}}` -- indistinguishable from a genuinely
successful zero-file-produced run at the protocol level. This confirms
ADR-0007's own risk-table entry (no distinguishable failure status beyond
the four enumerated strings) and its accepted fallback exactly as
documented: this watcher relies entirely on the generic ETA staleness
detection (NFR-3, `octobot_mcp.eta`, unchanged here) for a collection that
silently stalls, and `list_data_files()` (ADR-0008) is what actually tells
an agent whether a "completed" data-collection job produced a real file --
`get_job_result`'s own honest, no-filename result (ADR-0007 decision point
5) already directs the agent there. No new detection machinery is added
here, per the ADR's own explicit decision not to build any for v1.

**Open question #7 (`list_data_files`'s `file` field type) -- RESOLVED
live.** The live `/data_collector` page's rendered rows use an UNQUOTED
HTML attribute, `data-file=ExchangeHistoryDataCollector_1788790770.79745
08.data` (no directory prefix, no quotes) -- BeautifulSoup parses this
identically to a quoted attribute. `file` is a bare filename string, never
a path-like value with a directory component, confirmed directly from the
live DOM (not just the Jinja template source ADR-0008 read).
`tests/fixtures/data_collector_valid.html` (a real, trimmed 3-row capture
of this exact live structure, including one `is_full` row and one
non-`is_full` row) is reused for this module's own tests below.

## Design choices mirrored unchanged from `octobot_mcp.tools.backtesting`

- **Per-job Socket.IO connect/disconnect** (see that module's docstring
  for why): `_watch_data_collection_until_done` opens a fresh
  `socketio.AsyncClient()` per job and disconnects it in a `finally`.
- **The watcher task reference lives on `JobStore`**
  (`JobStore.track_watcher_task`), never a bare, unreferenced
  `asyncio.create_task(...)` result.
- **Milestone 14's bounded per-tick retry** (`MAX_CONSECUTIVE_WATCHER_
  TICK_FAILURES`): a single `channel.request_status()` failure is caught,
  logged, and retried on the next tick rather than immediately failing the
  job -- up to the same bound `octobot_mcp.tools.backtesting` uses,
  duplicated here rather than imported (this project's established
  "small per-module duplication over premature shared abstraction"
  convention, e.g. `_request_raw`/`_extract_error_text` below).
- **The `queued -> cancelled` race fix** (milestone 10's own discovery,
  documented at length on `_run_backtest_watcher`'s docstring): this
  module's `_run_data_collection_watcher` reconciles the exact same race
  the exact same way (inspect `store.start()`'s own return value; if the
  job isn't actually `"running"`, the start action nonetheless already
  succeeded against OctoBot, so best-effort stop it and return without
  ever reaching the watch loop).

## FR-27's symbols-half developer note -- RESOLVED, `get_available_symbols_for_collection` intentionally NOT added

Confirmed live this session, per FR-27's own explicit instruction to
verify before deciding: `GET /api/get_all_symbols/<exchange>` (the
already-shipped `get_all_symbols` tool, `octobot_mcp.tools.exchanges`) and
`GET /data_collector?action_type=symbol_list&exchange=<exchange>` returned
BYTE-FOR-BYTE IDENTICAL data for `exchange="binance"` (2289 symbols,
identical as sets) -- confirming FR-27's own stated contingency applies:
"if equivalent, prefer reusing `get_all_symbols` and drop this tool rather
than shipping a duplicate." This module therefore does NOT define
`get_available_symbols_for_collection` at all; `start_data_collection`'s
own docstring below tells an agent to use the existing `get_all_symbols`
tool instead. `get_available_timeframes_for_collection` IS added (FR-27:
"the timeframes half has no existing equivalent and MUST be a new tool
regardless" -- confirmed true, no existing tool exposes
`available_timeframes_list`).

No `from __future__ import annotations` here: see
`octobot_mcp.tools._shared.with_error_handling`'s docstring for why
postponed annotations would silently break this module's `Context`
injection.
"""

import asyncio
import base64
import binascii
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx2 as httpx
import socketio
from bs4 import BeautifulSoup
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context

from octobot_mcp.client import request_json
from octobot_mcp.context import get_client, get_job_store
from octobot_mcp.eta import WATCHER_INTERVAL_SECONDS
from octobot_mcp.errors import (
    DataCollectionStartFailedError,
    DataFileDeleteRejectedError,
    DataFileImportFailedError,
    DataFileScrapeContractBrokenError,
    JobNotFoundError,
    OctoBotAPIError,
    OctoBotClientError,
    OctoBotConnectionError,
    OctoBotServerError,
    OctoBotTimeoutError,
)
from octobot_mcp.jobs import TERMINAL_STATES, JobRecord, JobStore
from octobot_mcp.tools._shared import _attach_progress_notifier, require_confirmation, with_error_handling

_logger = logging.getLogger(__name__)

_DATA_COLLECTOR_PATH = "/data_collector"
_DATA_COLLECTOR_NAMESPACE = "/data_collector"

#: NFR-5, mirroring `octobot_mcp.tools.backtesting`'s identically-named/
#: purposed constants exactly (duplicated, not imported -- see this
#: module's docstring).
SOCKETIO_CONNECT_TIMEOUT_SECONDS = 5.0
SOCKETIO_STATUS_TIMEOUT_SECONDS = 5.0

#: Mirrors `octobot_mcp.tools.backtesting.MAX_CONSECUTIVE_WATCHER_TICK_FAILURES`
#: exactly (same bound, same rationale: ~15s of wall-clock tolerance for a
#: transient Socket.IO hiccup before genuinely failing the job) -- see that
#: constant's own docstring for the full reasoning, not repeated here.
MAX_CONSECUTIVE_WATCHER_TICK_FAILURES = 3

#: Sent on write requests that can hit OctoBot's global error handler,
#: which only renders a readable plain-text (never HTML) body when the
#: *request's* Content-Type was `application/json` -- same convention as
#: every other module in this project (duplicated, not imported).
_JSON_ERROR_HINT_HEADERS = {"Content-Type": "application/json"}

#: Confirmed live this session (see module docstring): the table
#: containing every historical data file row always renders with this id,
#: even when zero files exist (only its <tbody> would be empty) -- this is
#: the fail-loud gate for "the page structure changed" vs. "genuinely zero
#: data files," mirroring ADR-0008 decision point 3's own instruction not
#: to conflate the two.
_DATA_FILES_TABLE_ID = "dataFilesTable"


def _job_not_found(job_id: str) -> JobNotFoundError:
    """Mirrors `octobot_mcp.tools.backtesting`'s identically-purposed helper (small intentional duplication)."""
    return JobNotFoundError(
        f"No job found with id {job_id!r}. If the MCP server has restarted since "
        "this job was created, its in-memory record no longer exists -- this is "
        "expected, not a bug (the job store does not persist across restarts)."
    )


async def _request_raw(client: httpx.AsyncClient, method: str, path: str, **kwargs: Any) -> httpx.Response:
    """One request without decoding JSON, classifying only transport failures.

    Mirrors `octobot_mcp.tools.backtesting`/`octobot_mcp.tools.profiles`'
    identically-named/purposed helper (duplicated, not imported -- see this
    module's docstring): `/data_collector`'s various actions return a bare
    JSON string, plain error text with a misleading
    `Content-Type: application/json` header, or (for `import_data_file`) a
    full re-rendered HTML page regardless of outcome -- never one uniform
    shape `request_json` could decode.
    """
    try:
        return await client.request(method, path, **kwargs)
    except httpx.TimeoutException as exc:
        raise OctoBotTimeoutError(f"Request to OctoBot timed out: {method} {path}") from exc
    except httpx.HTTPError as exc:
        raise OctoBotConnectionError(f"Could not reach OctoBot: {method} {path} ({exc})") from exc


def _extract_error_text(response: httpx.Response) -> str:
    """Best-effort extraction of OctoBot's own error text from a non-2xx response.

    Mirrors `octobot_mcp.tools.backtesting`/`octobot_mcp.tools.profiles`'
    identically-named/purposed helper (duplicated, not imported). Confirmed
    live this session that `/data_collector`'s own failure bodies are plain
    text (e.g. `"Please select an exchange."`), never JSON, DESPITE
    carrying a `Content-Type: application/json` header -- `response.json()`
    reliably raises `ValueError` for them, falling through to the
    `response.text` branch below.
    """
    try:
        body = response.json()
    except ValueError:
        return response.text.strip() or f"HTTP {response.status_code} with an empty body"
    if isinstance(body, dict) and "text" in body:
        return str(body["text"])
    return str(body)


def _build_start_body(
    exchange: str,
    symbols: list[str],
    time_frames: list[str] | None,
    start_timestamp: int | None,
    end_timestamp: int | None,
) -> dict[str, Any]:
    """Build `start_collector`'s JSON body, per this module's own live-verified quirk.

    **Always** includes `time_frames`/`startTimestamp`/`endTimestamp` as
    keys, using `None` (serialized as JSON `null`) when the caller omitted
    them -- confirmed live this session (see module docstring) that
    omitting any of these three keys ENTIRELY causes OctoBot's route to
    raise an unhandled `KeyError` (`HTTP 500`) instead of a graceful
    validation message, unlike `exchange`/`symbols` (read defensively
    inside `collect_data_file` itself). `startTimestamp`/`endTimestamp` use
    OctoBot's own camelCase field names here specifically (confirmed
    against source, ADR-0007) -- NOT this project's usual snake_case
    convention used everywhere else, including this same tool's own
    `start_timestamp`/`end_timestamp` PARAMETER names.
    """
    return {
        "exchange": exchange,
        "symbols": symbols,
        "time_frames": time_frames,
        "startTimestamp": start_timestamp,
        "endTimestamp": end_timestamp,
    }


async def _post_start_action(client: httpx.AsyncClient, body: dict[str, Any]) -> str:
    """POST `action_type=start_collector`; returns OctoBot's own success message string.

    Success is HTTP < 400, body `flask.jsonify("Historical data collection
    started.")` -- confirmed live this session, a bare JSON string, decoded
    here via `response.json()` and returned as-is so `_run_data_collection_
    watcher` can store it verbatim as this job's eventual `result.message`
    (ADR-0007 decision point 5: "OctoBot's own completion message" -- since
    OctoBot's `/data_collector` Socket.IO status payload never carries any
    text at all, confirmed live, this start-time message is the only
    OctoBot-authored text available anywhere in this entire flow, so it is
    what is honestly echoed back at completion rather than a fabricated
    string).

    Raises `DataCollectionStartFailedError` wrapping OctoBot's own text on
    any failure -- see this module's docstring for the confirmed failure
    messages (a disabled backtesting feature, a missing exchange/symbols,
    `_ensure_backtesting_limits`'s three possible messages, or the
    one-at-a-time guard).
    """
    response = await _request_raw(
        client,
        "POST",
        _DATA_COLLECTOR_PATH,
        params={"action_type": "start_collector"},
        json=body,
        headers=_JSON_ERROR_HINT_HEADERS,
    )
    if response.status_code < 400:
        try:
            message = response.json()
        except ValueError:
            message = response.text.strip()
        return str(message)
    text = _extract_error_text(response)
    raise DataCollectionStartFailedError(
        f"OctoBot failed to start the data collection (HTTP {response.status_code}): {text}"
    )


async def _post_stop_action(client: httpx.AsyncClient) -> None:
    """POST `action_type=stop_collector` (no body). Confirmed live this session:
    HTTP 200, body `"Data collector stopped"`, and the status channel
    reports `"not started"` immediately afterward (unlike backtesting,
    which reports `"finished"` after a stop -- irrelevant here, since this
    watcher's cancel path never inspects the post-stop status itself, only
    whether the stop request succeeded, exactly mirroring
    `octobot_mcp.tools.backtesting._post_stop_action`'s own behavior).
    """
    response = await _request_raw(
        client,
        "POST",
        _DATA_COLLECTOR_PATH,
        params={"action_type": "stop_collector"},
        headers=_JSON_ERROR_HINT_HEADERS,
    )
    if response.status_code >= 500:
        raise OctoBotServerError(
            f"OctoBot returned server error {response.status_code} stopping the data collection: "
            f"{_extract_error_text(response)}",
            status_code=response.status_code,
        )
    if response.status_code >= 400:
        raise OctoBotClientError(
            f"OctoBot returned client error {response.status_code} stopping the data collection: "
            f"{_extract_error_text(response)}",
            status_code=response.status_code,
        )


class _DataCollectorStatusChannel:
    """Bridges OctoBot's plain (non-ack) `data_collector_status` Socket.IO
    event round trip into an async request/await-response shape for the
    watcher loop -- structurally identical to
    `octobot_mcp.tools.backtesting._BacktestingStatusChannel`, targeting
    `/data_collector`'s own namespace/event instead. See this module's
    docstring for this session's own live round-trip confirmation.
    """

    def __init__(self, sio: "socketio.AsyncClient") -> None:
        self._sio = sio
        self._pending: asyncio.Future | None = None
        sio.on("data_collector_status", self._on_status, namespace=_DATA_COLLECTOR_NAMESPACE)

    def _on_status(self, data: dict[str, Any]) -> None:
        if self._pending is not None and not self._pending.done():
            self._pending.set_result(data)

    async def request_status(self) -> dict[str, Any]:
        """Emit one `data_collector_status` request and await its response.

        A timeout here (`asyncio.TimeoutError`, deliberately left
        unhandled) propagates up through `_watch_data_collection_until_done`'s
        bounded-retry handling -- a sustained timeout still eventually
        fails the job (INV-1), exactly like the backtest watcher's own
        equivalent.
        """
        loop = asyncio.get_running_loop()
        self._pending = loop.create_future()
        await self._sio.emit("data_collector_status", namespace=_DATA_COLLECTOR_NAMESPACE)
        try:
            return await asyncio.wait_for(self._pending, timeout=SOCKETIO_STATUS_TIMEOUT_SECONDS)
        finally:
            self._pending = None


def _convert_data_collection_progress(progress: dict[str, Any]) -> float:
    """ADR-0007 decision point 4's nested-step -> flat-percent conversion.

    `0.0 if total_steps <= 0 else clamp(((current_step - 1) * 100 +
    current_step_percent) / total_steps, 0, 100)` -- the 1-indexing
    assumption on `current_step` is now LIVE-CONFIRMED (see this module's
    docstring's Step 0 section), not merely assumed, but the `max`/`min`
    clamp is kept regardless, as pure defense-in-depth against any future
    OctoBot version that might violate it.
    """
    total_steps = int(progress.get("total_steps") or 0)
    if total_steps <= 0:
        return 0.0
    current_step = int(progress.get("current_step") or 0)
    current_step_percent = float(progress.get("current_step_percent") or 0.0)
    raw = ((current_step - 1) * 100 + current_step_percent) / total_steps
    return max(0.0, min(100.0, raw))


async def _watch_data_collection_until_done(
    client: httpx.AsyncClient,
    store: JobStore,
    job_id: str,
    request: dict[str, Any],
    start_message: str,
    *,
    socketio_client_factory: Callable[[], "socketio.AsyncClient"],
    interval_seconds: float,
    on_tick: Callable[[JobRecord], Awaitable[None]] | None = None,
) -> None:
    """`octobot_mcp.tools.backtesting._watch_until_done`'s data-collection sibling.

    Structurally identical to that function (staleness/cancel/tick/finish
    handling, bounded per-tick retry, INV-1's lock-released-in-`finally`
    guarantee via its caller's `async with`) with two substantive
    differences, both from ADR-0007: (1) the nested step-progress payload
    is converted via `_convert_data_collection_progress` instead of read
    as a flat float directly; (2) `error_count` is always passed `0` to
    `record_watcher_tick` -- there is no equivalent field in this payload
    (confirmed against source and live this session) -- so a data-
    collection job's `errors` list is only ever populated by this
    function's own stop-confirmation-failure path below, never by an
    OctoBot-reported count.

    `request` is `start_data_collection`'s own validated input, echoed
    honestly into the job's `result` at completion (ADR-0007 decision
    point 5) -- never mutated here. `start_message` is `_post_start_action`'s
    own returned OctoBot text, stored as `result["message"]` at completion.
    """
    sio = socketio_client_factory()
    await sio.connect(
        str(client.base_url),
        namespaces=[_DATA_COLLECTOR_NAMESPACE],
        wait_timeout=SOCKETIO_CONNECT_TIMEOUT_SECONDS,
    )
    channel = _DataCollectorStatusChannel(sio)
    consecutive_tick_failures = 0
    try:
        start_monotonic = time.monotonic()
        while True:
            job = store.get(job_id)
            if job is None:
                return
            if job.cancel_requested:
                # Best-effort, mirroring _watch_until_done's own cancel
                # handling exactly: a failure to confirm the stop is
                # recorded but does not block the job from finishing its
                # cancellation.
                try:
                    await _post_stop_action(client)
                except OctoBotAPIError as exc:
                    store.append_error(job_id, f"Failed to confirm stop with OctoBot: {exc}")
                store.confirm_cancelled(job_id)
                return
            try:
                payload = await channel.request_status()
            except Exception as exc:  # noqa: BLE001 -- bounded retry: see this function's own docstring.
                consecutive_tick_failures += 1
                _logger.warning(
                    "Data collection watcher tick failed for job %s (%d/%d consecutive failure(s)): %r",
                    job_id,
                    consecutive_tick_failures,
                    MAX_CONSECUTIVE_WATCHER_TICK_FAILURES,
                    exc,
                )
                if consecutive_tick_failures >= MAX_CONSECUTIVE_WATCHER_TICK_FAILURES:
                    raise
                await asyncio.sleep(interval_seconds)
                continue
            consecutive_tick_failures = 0
            elapsed_seconds = time.monotonic() - start_monotonic
            progress_percent = _convert_data_collection_progress(payload.get("progress") or {})
            updated = store.record_watcher_tick(
                job_id,
                elapsed_seconds=elapsed_seconds,
                progress_percent=progress_percent,
                error_count=0,
            )
            if on_tick is not None and updated is not None:
                try:
                    await on_tick(updated)
                except Exception as exc:  # noqa: BLE001 -- best-effort: never fail the watcher over this.
                    _logger.debug("on_tick progress-notification hook raised (best-effort, ignored): %r", exc)
            if payload.get("status") == "finished":
                result = {**request, "message": start_message}
                store.complete(job_id, result=result)
                return
            await asyncio.sleep(interval_seconds)
    finally:
        await sio.disconnect()


def _make_data_collection_on_tick() -> Callable[[JobRecord], Awaitable[None]]:
    """Mirrors `octobot_mcp.tools.backtesting._make_single_job_on_tick` exactly
    (own docstring not repeated here) -- a fresh dedup closure per job,
    reporting the same 0-100 `progress_percent` scale as
    `get_job_status`.
    """
    last_notified: list[float | None] = [None]

    async def on_tick(job: JobRecord) -> None:
        if job.progress_notifier is None:
            return
        if last_notified[0] == job.progress_percent:
            return
        last_notified[0] = job.progress_percent
        await job.progress_notifier(job.progress_percent, 100.0, f"{job.progress_percent:.1f}% complete")

    return on_tick


async def _run_data_collection_watcher(
    client: httpx.AsyncClient,
    store: JobStore,
    job_id: str,
    request: dict[str, Any],
    *,
    socketio_client_factory: Callable[[], "socketio.AsyncClient"] = socketio.AsyncClient,
    interval_seconds: float = WATCHER_INTERVAL_SECONDS,
    on_tick: Callable[[JobRecord], Awaitable[None]] | None = None,
) -> None:
    """`octobot_mcp.tools.backtesting._run_backtest_watcher`'s data-collection
    sibling -- structurally identical (own docstring's reasoning applies
    verbatim, not repeated here): INV-1 via `async with
    store.data_collection_execution_lock` (ADR-0007 decision point 3/
    NFR-15 -- a lock INDEPENDENT of `backtest_execution_lock`, never
    conflated with it), the same `queued -> cancelled` race reconciliation
    (inspect `store.start()`'s return value; best-effort stop + return if
    the job isn't actually `"running"`), and the same catch-all ->
    `store.fail(...)` guarantee.

    `request` is the exact body `_build_start_body` will POST, doubling as
    the honest request-echo half of this job's eventual `result` (ADR-0007
    decision point 5) -- built once here, before the lock is even
    acquired, so a job created for a request that will be rejected still
    fails cleanly through the normal `_post_start_action` error path
    rather than needing separate validation.
    """
    async with store.data_collection_execution_lock:
        job = store.get(job_id)
        if job is None or job.state in TERMINAL_STATES:
            # Cancelled while still queued for the lock -- see
            # _run_backtest_watcher's identical early-return for why.
            return
        try:
            body = _build_start_body(
                request["exchange"],
                request["symbols"],
                request["time_frames"],
                request["start_timestamp"],
                request["end_timestamp"],
            )
            try:
                start_message = await _post_start_action(client, body)
            except OctoBotAPIError as exc:
                store.fail(job_id, error=str(exc))
                return
            started = store.start(job_id)
            if started is None or started.state != "running":
                # Reconcile the queued->cancelled race (see
                # _run_backtest_watcher's own docstring for the full
                # explanation -- identical situation here): the start
                # action just succeeded against OctoBot, so tell it to
                # stop (best effort) and never proceed into the watch loop.
                try:
                    await _post_stop_action(client)
                except OctoBotAPIError as exc:
                    if started is not None:
                        store.append_error(
                            job_id,
                            f"Failed to confirm stop with OctoBot after a queued->cancelled race: {exc}",
                        )
                return
            await _watch_data_collection_until_done(
                client,
                store,
                job_id,
                request,
                start_message,
                socketio_client_factory=socketio_client_factory,
                interval_seconds=interval_seconds,
                on_tick=on_tick,
            )
        except Exception as exc:  # noqa: BLE001 -- INV-1: a watcher bug must never leave a job stuck.
            store.fail(job_id, error=f"Unhandled watcher error: {exc}")


@with_error_handling
async def start_data_collection(
    ctx: Context,
    exchange: str,
    symbols: list[str],
    time_frames: list[str] | None = None,
    start_timestamp: int | None = None,
    end_timestamp: int | None = None,
) -> Any:
    """Start a real OctoBot historical data collection run in the background and return immediately.

    Not confirm-gated (ADR-0003/tool spec): starts a background download,
    never touches live trading, destroys no data, exposes no credentials --
    same reasoning as `start_backtest`. Maps to `POST /data_collector
    ?action_type=start_collector` [V]. `symbols` must be non-empty.
    `time_frames` is optional (OctoBot collects its own default set when
    omitted). `start_timestamp`/`end_timestamp` are optional epoch
    **milliseconds** (confirmed both live and from source this session --
    see this module's docstring).

    To discover valid `exchange`/`symbols` values, use the existing
    `get_all_symbols(exchange)` tool (confirmed this session to return
    data identical to OctoBot's own data-collector-scoped symbol list, per
    FR-27 -- no separate `get_available_symbols_for_collection` tool
    exists here for that reason). For `time_frames`, use this module's own
    `get_available_timeframes_for_collection(exchange)`.

    Behavior (ADR-0007): creates a `kind: "data_collection"` job, acquires
    `JobStore.data_collection_execution_lock` -- a lock INDEPENDENT of
    `backtest_execution_lock` (NFR-15): a concurrent `start_backtest` +
    `start_data_collection` never block each other, but two concurrent
    `start_data_collection` calls do serialize (queueing, same as two
    concurrent backtests) -- then submits the start action and spawns a
    background watcher connecting to OctoBot's `/data_collector` Socket.IO
    namespace, mirroring `start_backtest`'s watcher exactly (this session's
    own live round-trip confirmation is recorded in this module's
    docstring).

    Output: `{"job_id": str, "state": "queued"}` -- always `"queued"`
    immediately (the background watcher has not yet run at all). Poll
    `get_job_status(job_id)` for `progress_percent`/`eta_seconds`;
    `get_job_result(job_id)` once `state == "completed"` returns
    `{"exchange", "symbols", "time_frames", "start_timestamp",
    "end_timestamp", "message"}` -- deliberately NO resulting filename
    (OctoBot itself names none anywhere in this flow, confirmed this
    session): call `list_data_files()` afterward to find the new file.

    **A confirmed OctoBot-side limitation, not a bug in this tool**: a
    collection submitted for a nonexistent/invalid exchange name returns
    the SAME success response and the SAME `"finished"` status as a
    genuinely successful run (confirmed live this session, resolving open
    question #11) -- if `list_data_files()` doesn't show the expected new
    file after a `"completed"` job, the exchange/symbol/timeframe
    combination was likely invalid upstream, not a failure this server
    could have detected sooner. A collection that never progresses at all
    (staleness, NFR-3) is a separate, detectable case: its
    `eta_confidence` will show `"low"`/`eta_seconds: null` and it should
    be cancelled via `cancel_job`.

    Supports `progressToken` (ADR-0002 decision point 3, reused via
    `octobot_mcp.tools._shared._attach_progress_notifier`) exactly like
    `start_backtest`.

    Errors: `DataCollectionStartFailedError` wrapping OctoBot's own
    failure text -- confirmed messages include "Backtesting is disabled.",
    "Please select an exchange.", "Please select a trading pair.", any of
    `_ensure_backtesting_limits`'s three possible messages (see this
    module's docstring -- source-confirmed text, not guessed), and "Can't
    collect data for {symbols} on {exchange} (Historical data collector is
    already running)" for OctoBot's own one-at-a-time guard. Raised from
    the background watcher (not this function), surfacing as the job's
    `errors`/`state="failed"` via `get_job_status` -- this function itself
    only raises synchronously for input validation caught before any job
    is created.
    """
    if not symbols:
        raise DataCollectionStartFailedError(
            "start_data_collection requires a non-empty 'symbols' list."
        )

    request = {
        "exchange": exchange,
        "symbols": symbols,
        "time_frames": time_frames,
        "start_timestamp": start_timestamp,
        "end_timestamp": end_timestamp,
    }

    client = get_client(ctx)
    store = get_job_store(ctx)
    job = store.create_job(kind="data_collection")
    _attach_progress_notifier(store, ctx, job.job_id)
    task = asyncio.create_task(
        _run_data_collection_watcher(client, store, job.job_id, request, on_tick=_make_data_collection_on_tick())
    )
    store.track_watcher_task(job.job_id, task)
    return {"job_id": job.job_id, "state": job.state}


# --- list_data_files: Tier-B scrape adapter (ADR-0008) ----------------------


async def _fetch_data_collector_html(client: httpx.AsyncClient) -> str:
    """Fetch the bare `GET /data_collector` page's raw HTML body.

    Mirrors `octobot_mcp.tools.profiles._fetch_profiles_selector_html`'s
    identically-purposed helper (duplicated, not imported -- see this
    module's docstring).
    """
    try:
        response = await client.request("GET", _DATA_COLLECTOR_PATH)
    except httpx.TimeoutException as exc:
        raise OctoBotTimeoutError(f"Request to OctoBot timed out: GET {_DATA_COLLECTOR_PATH}") from exc
    except httpx.HTTPError as exc:
        raise OctoBotConnectionError(f"Could not reach OctoBot: GET {_DATA_COLLECTOR_PATH} ({exc})") from exc

    if response.status_code >= 500:
        raise OctoBotServerError(
            f"OctoBot returned server error {response.status_code} for GET {_DATA_COLLECTOR_PATH}",
            status_code=response.status_code,
        )
    if response.status_code >= 400:
        raise OctoBotClientError(
            f"OctoBot returned client error {response.status_code} for GET {_DATA_COLLECTOR_PATH}",
            status_code=response.status_code,
        )
    return response.text


def _parse_data_files_row(row: Any) -> dict[str, Any]:
    """Parse one `tr.selectable_datafile` row (ADR-0008's exact markup) into one file dict.

    Raises `DataFileScrapeContractBrokenError` -- never returns a
    partial/best-effort dict -- the instant any of this row's structural
    assumptions doesn't hold, mirroring `octobot_mcp.tools.profiles`'
    per-card fail-loud discipline (NFR-12/INV-5 precedent, now NFR-13's own
    acceptance criterion).
    """
    cells = row.find_all("td", recursive=False)
    if len(cells) != 7:
        raise DataFileScrapeContractBrokenError(
            f"Expected exactly 7 <td> cells in one data-file row, found {len(cells)}."
        )
    symbols_cell, date_cell, full_or_count_cell, exchange_cell, time_frames_cell, file_cell, action_cell = cells

    delete_link = action_cell.find("a", class_="delete_data_file")
    if delete_link is None:
        raise DataFileScrapeContractBrokenError(
            "A data-file row's action cell is missing its delete_data_file link."
        )
    file_name = delete_link.get("data-file")
    if not file_name:
        raise DataFileScrapeContractBrokenError(
            "A data-file row's delete_data_file link is missing its data-file attribute."
        )
    visible_file_name = file_cell.get_text(strip=True)
    if visible_file_name != file_name:
        raise DataFileScrapeContractBrokenError(
            f"A data-file row's visible file name ({visible_file_name!r}) does not match "
            f"its delete button's data-file attribute ({file_name!r})."
        )

    full_or_count_text = full_or_count_cell.get_text(strip=True)
    is_full = full_or_count_text == "Full"
    date_text = date_cell.get_text(strip=True)

    start_date: str | None = None
    end_date: str | None = None
    date: str | None = None
    candles_length: int | None = None
    if is_full:
        if " to " not in date_text:
            raise DataFileScrapeContractBrokenError(
                f"A 'Full' data-file row's date cell ({date_text!r}) does not contain the "
                "expected 'X to Y' date-range separator."
            )
        start_date, end_date = (part.strip() for part in date_text.split(" to ", 1))
    else:
        date = date_text
        try:
            candles_length = int(full_or_count_text)
        except ValueError as exc:
            raise DataFileScrapeContractBrokenError(
                f"A non-'Full' data-file row's candle-count cell ({full_or_count_text!r}) is not an integer."
            ) from exc

    return {
        "file": file_name,
        "exchange": exchange_cell.get_text(strip=True),
        "symbols": [s.strip() for s in symbols_cell.get_text(strip=True).split(",") if s.strip()],
        "time_frames": [tf.strip() for tf in time_frames_cell.get_text(strip=True).split(",") if tf.strip()],
        "is_full": is_full,
        "start_date": start_date,
        "end_date": end_date,
        "date": date,
        "candles_length": candles_length,
    }


def _parse_data_collector_html(html: str) -> list[dict[str, Any]]:
    """Parse `/data_collector`'s rendered HTML into file dicts (fail-loud, ADR-0008/NFR-13).

    Unlike `octobot_mcp.tools.profiles`' adapter (where zero cards is
    ALWAYS broken -- OctoBot always ships built-in profiles), a fresh
    OctoBot instance can genuinely have zero historical data files. The
    fail-loud gate here is therefore the CONTAINING TABLE's own presence
    (`id="dataFilesTable"`, confirmed live to always render, even with an
    empty `<tbody>`) -- zero `tr.selectable_datafile` rows inside an
    otherwise-present table is a legitimate empty result, never raised.
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id=_DATA_FILES_TABLE_ID)
    if table is None:
        raise DataFileScrapeContractBrokenError(
            f"No <table id={_DATA_FILES_TABLE_ID!r}> found on {_DATA_COLLECTOR_PATH}. "
            "This means the page structure has changed -- verify against the current "
            "OctoBot version (see octobot_mcp.tools.data_collection's module docstring "
            "for the last-confirmed version)."
        )

    rows = table.find_all("tr", class_="selectable_datafile")
    files = [_parse_data_files_row(row) for row in rows]

    seen_files = set()
    for entry in files:
        if entry["file"] in seen_files:
            raise DataFileScrapeContractBrokenError(f"Duplicate file name {entry['file']!r} found across rows.")
        seen_files.add(entry["file"])
    return files


@with_error_handling
async def list_data_files(ctx: Context) -> Any:
    """List every historical data file already present on the OctoBot instance.

    Tier-B (ADR-0008): scrapes the bare `GET /data_collector` page -- see
    this module's docstring/`_parse_data_collector_html` for the verified
    DOM shape and fail-loud assumptions. Idempotent, read-only, not
    confirm-gated.

    Output: `{"files": [{"file": str, "exchange": str, "symbols": [str],
    "time_frames": [str], "is_full": bool, "start_date": str|null,
    "end_date": str|null, "date": str|null, "candles_length": int|null}]}`.
    `file` is the authoritative identifier to pass to `delete_data_file`.
    An empty `files` list is a legitimate result (no data files exist yet)
    -- distinct from `DataFileScrapeContractBrokenError`, which means the
    scrape itself is broken, never silently returned as an empty/partial
    list (NFR-13).
    """
    html = await _fetch_data_collector_html(get_client(ctx))
    return {"files": _parse_data_collector_html(html)}


# --- delete_data_file: confirm-gated (ADR-0003/FR-25) -----------------------


@with_error_handling
async def delete_data_file(ctx: Context, file: str, confirm: bool = False) -> Any:
    """Permanently delete a historical data file. **Confirm-gated** (ADR-0003/FR-25).

    **Deliberately gated, reversed from this addendum's original
    not-gated draft** -- see the tool spec/requirements doc FR-25 for the
    full reasoning: unlike `cancel_order`/`close_position` (instantly
    correctable), an erroneous delete means re-running a collection that
    can take minutes to hours, placing this closer to `clear_*_history`'s
    risk profile. If `confirm` is not exactly `true`, no OctoBot call is
    made at all -- this returns `require_confirmation`'s structured
    refusal (a normal return, not an error).

    Once confirmed, maps to `POST /data_collector?action_type=
    delete_data_file` with the raw `file` string as the JSON body
    (confirmed against source AND live this session: `flask.request.
    get_json()`'s raw value is passed straight through to
    `backtesting_api.delete_data_file(file_name)`, no wrapping object).

    Output on success: `{"file": str, "deleted": true, "message": str}`
    (OctoBot's own confirmed live success body: `f"{file} deleted"`).

    Errors: `DataFileDeleteRejectedError` wrapping OctoBot's own `f"Can't
    delete {file_name} ({error})"` text -- confirmed verbatim live this
    session for a nonexistent file (`"Can't delete does_not_exist.data
    (file can't be found)"`).
    """
    if confirm is not True:
        return require_confirmation(
            "delete_data_file",
            file,
            "This permanently deletes the historical data file; re-collecting it via "
            "start_data_collection can take minutes to hours depending on the date range.",
        )

    client = get_client(ctx)
    response = await _request_raw(
        client,
        "POST",
        _DATA_COLLECTOR_PATH,
        params={"action_type": "delete_data_file"},
        json=file,
        headers=_JSON_ERROR_HINT_HEADERS,
    )
    if response.status_code >= 400:
        raise DataFileDeleteRejectedError(
            f"OctoBot failed to delete data file {file!r}: {_extract_error_text(response)}"
        )
    try:
        message = response.json()
    except ValueError:
        message = response.text.strip()
    return {"file": file, "deleted": True, "message": str(message)}


# --- import_data_file: not gated (ADR-0003/FR-26) ---------------------------


async def _find_new_file(
    before: list[dict[str, Any]], after: list[dict[str, Any]], *, filename: str
) -> dict[str, Any]:
    """Diff two `list_data_files`-shaped scrapes to find the file `import_data_file` just added.

    Mirrors `octobot_mcp.tools.profiles._find_new_profile`'s identically-
    purposed diffing approach (this session's own live verification found
    the SAME invisible-at-the-HTTP-level outcome that tool's own
    `_create_profile_import_file` already documents -- see this module's
    docstring and `DataFileImportFailedError`'s own docstring).
    """
    before_files = {entry["file"] for entry in before}
    new_entries = [entry for entry in after if entry["file"] not in before_files]
    if len(new_entries) == 1:
        return new_entries[0]
    if len(new_entries) > 1:
        raise DataFileImportFailedError(
            f"import_data_file(filename={filename!r}): expected exactly one new data file after "
            f"import, found {len(new_entries)} -- another change to this OctoBot instance's data "
            "files happened concurrently and made the diff ambiguous."
        )
    raise DataFileImportFailedError(
        f"import_data_file(filename={filename!r}) failed: no new data file appeared after the "
        "upload. OctoBot's own /data_collector?action_type=import_data_file route always responds "
        "HTTP 200 regardless of outcome (confirmed live this session), so this is the only way this "
        "tool can detect a rejected import (e.g. an invalid/corrupt file)."
    )


@with_error_handling
async def import_data_file(ctx: Context, file_base64: str, filename: str) -> Any:
    """Upload an existing OctoBot-format historical data file without collecting it via an exchange.

    Not confirm-gated (ADR-0003/FR-26): creates new state, destroys
    nothing. Maps to `POST /data_collector?action_type=import_data_file`
    (multipart `file` field, mirroring `create_profile(mode="import_file")`'s
    existing pattern exactly: base64-decode, then a multipart upload).

    **A confirmed OctoBot-side limitation, not a bug in this tool**: this
    route ALWAYS responds HTTP 200 with the full re-rendered
    `/data_collector` page, on both a genuinely saved file and a silently
    rejected one (confirmed live this session with garbage bytes -- no
    file appeared in `list_data_files()` afterward, and the 200 response
    body carried no inspectable error text anywhere) -- the same
    invisible-at-the-HTTP-level outcome
    `octobot_mcp.tools.profiles._create_profile_import_file` already
    documents for `create_profile(mode="import_file")`. This tool
    therefore determines the outcome via a before/after `list_data_files()`
    diff, exactly like that tool does for profiles.

    Output: `{"filename": str, "imported": true, "message": str}`.

    Errors: `DataFileImportFailedError` if `file_base64` isn't valid
    base64, or if no new file appears in `list_data_files()` after the
    upload (OctoBot silently rejected it).
    """
    try:
        file_bytes = base64.b64decode(file_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise DataFileImportFailedError(f"import_data_file: file_base64 is not valid base64 ({exc}).") from exc

    client = get_client(ctx)
    before = _parse_data_collector_html(await _fetch_data_collector_html(client))
    response = await _request_raw(
        client,
        "POST",
        _DATA_COLLECTOR_PATH,
        params={"action_type": "import_data_file"},
        files={"file": (filename, file_bytes)},
    )
    if response.status_code >= 400:
        raise DataFileImportFailedError(
            f"OctoBot failed to import data file {filename!r}: {_extract_error_text(response)}"
        )
    after = _parse_data_collector_html(await _fetch_data_collector_html(client))
    new_file = await _find_new_file(before, after, filename=filename)
    return {"filename": new_file["file"], "imported": True, "message": "Historical data collection file saved"}


# --- get_available_timeframes_for_collection: Tier A read (FR-27) ----------


@with_error_handling
async def get_available_timeframes_for_collection(ctx: Context, exchange: str) -> Any:
    """List the time frames OctoBot's data collector supports for one exchange.

    Maps to `GET /data_collector?action_type=available_timeframes_list
    &exchange=<name>` [V, confirmed live this session]. Read-only, not
    confirm-gated. No dedicated exception class -- a plain read with no
    domain-specific failure mode, matching this project's existing
    convention for read-only tools (e.g. `get_all_symbols`).

    Output: `{"time_frames": [str]}` -- direct passthrough of OctoBot's own
    sorted JSON array (NFR-8), e.g. `["1m", "3m", "5m", ..., "1M"]`.

    See this module's docstring for why there is no sibling
    `get_available_symbols_for_collection` tool: `get_all_symbols(exchange)`
    (already shipped) was confirmed this session to return identical data.
    """
    time_frames = await request_json(
        get_client(ctx),
        "GET",
        _DATA_COLLECTOR_PATH,
        params={"action_type": "available_timeframes_list", "exchange": exchange},
    )
    return {"time_frames": time_frames}


def register(mcp: MCPServer) -> None:
    """Register this module's tools onto `mcp` (called once from `octobot_mcp.tools.register_all`)."""
    mcp.tool()(start_data_collection)
    mcp.tool()(list_data_files)
    mcp.tool()(delete_data_file)
    mcp.tool()(import_data_file)
    mcp.tool()(get_available_timeframes_for_collection)
