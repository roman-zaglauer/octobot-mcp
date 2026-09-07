# ADR-0007: Historical data collection as a new async job kind, extending ADR-0002's job model

## Status
Accepted

## Context
The shipped v1 `start_backtest(mode="data_files", files=[...])` tool's own
docstring already admits a real gap: "there is currently no MCP tool
exposing what's available; the agent must already know a valid name." This
capability (letting an agent trigger and observe OctoBot's own historical
data collection) was never in v1's curated scope at all — it appears nowhere
in the original requirements doc, tool spec, or tasklist. This ADR designs
it from scratch, deciding specifically whether it should follow ADR-0002's
async job/watcher pattern or be a simple synchronous tool.

Verified this session against OctoBot source (`OctoBot-Tentacles` `master`
branch, `Services/Interfaces/web_interface/{controllers,websockets,models}/`,
the same package family ADR-0002/ADR-0006 were verified against):

- `POST /data_collector?action_type=start_collector`, JSON body
  `{"exchange": str, "symbols": [str], "time_frames": [str]|null,
  "startTimestamp": int|null, "endTimestamp": int|null}` →
  `models.collect_data_file(exchange, symbols, time_frames, start_timestamp,
  end_timestamp)`. On success this dispatches to
  `_background_collect_exchange_historical_data(...)` (collection runs in
  the background; the HTTP call returns a `(True, "Historical data
  collection started.")` pair almost immediately) and the route additionally
  calls `web_interface.send_data_collector_status()` — the exact same naming
  pattern ADR-0002 already found for `send_backtesting_status()`.
- **Real-time progress is confirmed to exist via Socket.IO**, namespace
  `/data_collector` (`websockets/data_collector.py`,
  `DataCollectorNamespace`): the client emits `data_collector_status` (no
  payload — same broadcast-style, non-ack pattern ADR-0002/milestone 8
  already confirmed and round-trip-tested for `/backtesting`), and the
  server emits back an event of the **same name** on the same namespace,
  sourced from `models.get_data_collector_status()`. Connecting to the
  namespace itself also triggers one push on `on_connect`, exactly mirroring
  `/backtesting`'s `BacktestingNamespace.on_connect`.
- `get_data_collector_status()`'s exact, exhaustive return shape (source
  read in full):
  ```python
  def get_data_collector_status():
      progress = {"current_step": 0, "total_steps": 0, "current_step_percent": 0}
      ...
      if <collecting>:
          return "collecting", progress   # progress fields populated
      if <finished>:
          return "finished", progress
      if <starting>:
          return "starting", progress
      return "not started", progress
  ```
  So the Socket.IO payload is `{"status": "not started"|"starting"|
  "collecting"|"finished", "progress": {"current_step": int, "total_steps":
  int, "current_step_percent": int}}`. Two confirmed divergences from
  backtesting's `{"status", "progress": <float>, "errors": <int>}` payload:
  1. **`progress` is a nested step object, not a flat 0–100 float.** OctoBot
     appears to model data collection as N discrete steps (plausibly one per
     symbol/timeframe pair), each internally 0–100% complete.
  2. **There is no `errors` count field at all** in this payload, unlike
     backtesting's.
- **OctoBot enforces its own "one data collection run at a time" limit**,
  confirmed by `collect_data_file`'s own guard: if the tool reference is not
  `None` and not finished, it returns `(False, f"Can't collect data for
  {symbols} on {exchange} (Historical data collector is already
  running)")`. This is the same *shape* of constraint ADR-0002 found for
  backtesting (A4), but it is a **distinct, independent resource** — the
  guard checks `constants.BOT_TOOLS_DATA_COLLECTOR`, a different registered
  tool reference than backtesting's own, with its own separate "already
  running" message. Nothing in the source read this session couples the two
  constraints to each other.
- `POST /data_collector?action_type=stop_collector` → `models.
  stop_data_collector()`, a real stop action (`backtesting_api.
  stop_data_collector(...)`, run via `interfaces_util.
  run_in_bot_main_loop(...)`), not a no-op.
- `get_job_status`/`list_jobs`/`cancel_job`
  (`octobot_mcp.tools.jobs`) were re-read this session specifically to
  check whether they are generic across `JobKind` or backtest-specific, per
  this ADR's own decision criteria. Confirmed: **all three are already
  fully generic.** `_job_status_payload`'s only kind-specific branch is `if
  job.kind == "compare_strategies": ... else: <read the record's own
  fields>` — any kind other than `"compare_strategies"` (today just
  `"backtest"`) takes the generic `else` branch already. `cancel_job`'s
  state-machine transitions (`queued` → `cancelled` directly; `running` →
  set `cancel_requested`) never inspect `kind` at all. `JobStore` itself
  (`create_job`/`start`/`complete`/`fail`/`cancel`/`confirm_cancelled`/
  `record_watcher_tick`/`append_error`/`track_watcher_task`/
  `set_progress_notifier`) is likewise entirely kind-agnostic — none of its
  methods branch on `kind` anywhere. The one confirmed **non**-generic piece
  is `get_job_result` (`octobot_mcp.tools.backtesting`), whose body is `if
  job.kind == "compare_strategies": ... else: return {"report":
  result.get("report"), "trades": result.get("trades")}` — the `else`
  branch hardcodes the backtest result shape.

## Decision
1. **Async job pattern, extending ADR-0002 rather than replacing it.**
   `start_data_collection` returns a `job_id` immediately, exactly like
   `start_backtest`. A new `JobKind` literal value, `"data_collection"`, is
   added to `octobot_mcp.jobs.JobKind` (currently `Literal["backtest",
   "compare_strategies"]`). This is the **only** change `JobStore` needs
   beyond one addition (item 3 below) — every mutator method already works
   unchanged for the new kind, per the generic-across-`kind` confirmation
   above.
2. **No dedicated `stop_data_collection` tool — `cancel_job(job_id)` is
   reused as-is.** This mirrors the existing precedent exactly: there is no
   separate `stop_backtest` tool today either; `cancel_job` is the one
   generic cancellation entry point for every job kind. Adding a
   domain-specific stop tool here would be an inconsistent, unnecessary
   second way to do the same thing (simplicity/maintainability precedence,
   tier 3).
3. **A second, independent lock: `JobStore.data_collection_execution_lock`.**
   Because OctoBot's own one-collection-at-a-time constraint is a distinct
   resource from its one-backtest-at-a-time constraint (confirmed above,
   distinct guard/message/tool-reference), this server must not conflate
   the two into one lock — doing so would incorrectly serialize an
   MCP-submitted backtest against an MCP-submitted data collection even
   though OctoBot itself does not require that. `JobStore` gains one new
   `asyncio.Lock` field, initialized the same way
   `backtest_execution_lock` already is, reachable the same
   dependency-injected way (NFR-11): `get_job_store(ctx).
   data_collection_execution_lock`. No change to `octobot_mcp.context` is
   needed — the lock lives on the same `JobStore` instance `AppContext.jobs`
   already holds.
4. **A new watcher module, `octobot_mcp.tools.data_collection`, structurally
   mirroring `octobot_mcp.tools.backtesting`'s watcher.** Specifically:
   - A `_DataCollectorStatusChannel` class mirroring
     `_BacktestingStatusChannel` exactly (same non-ack emit/await-next-event
     bridge), targeting namespace `/data_collector`, event
     `data_collector_status`.
   - A `_watch_data_collection_until_done` function mirroring
     `_watch_until_done`'s loop structure (staleness/cancel/tick/finish
     handling, INV-1's lock-released-in-`finally` guarantee via `async
     with`), with one substantive difference: **converting the nested
     step-progress payload into the flat 0–100 `progress_percent`
     `record_watcher_tick` already expects**, via
     ```
     progress_percent = 0.0 if total_steps <= 0 else max(0.0, min(100.0,
         ((current_step - 1) * 100 + current_step_percent) / total_steps))
     ```
     (assumes 1-indexed `current_step`, per the "one step per
     symbol/timeframe pair, currently at least the first" reading of
     `current_step: 0` at `"not started"` — **flagged as an explicit open
     question, see requirements doc Q9**: the 1-indexing assumption is not
     source-confirmed and must be checked against a live multi-step
     collection's observed `(current_step, total_steps, current_step_percent)`
     sequence before this milestone ships). `record_watcher_tick`'s
     `error_count` parameter is always passed `0` — there is no equivalent
     field in this payload, confirmed above; a data-collection job's
     `errors` list is therefore populated only by this watcher's own
     stop-confirmation-failure path (mirroring `_watch_until_done`'s
     existing `append_error` use), never by an OctoBot-reported count.
   - A `_run_data_collection_watcher` function mirroring
     `_run_backtest_watcher` exactly (acquire
     `data_collection_execution_lock`, POST the start action, handle the
     `queued→cancelled` race the same way, delegate to the watch loop,
     catch-all → `store.fail(...)`).
   - `start_data_collection`'s own tool function, reusing
     `_extract_progress_token`/`_build_progress_notifier`/
     `_attach_progress_notifier` for `progressToken` support (ADR-0002
     decision point 3) exactly as `start_backtest` does. Since this module
     becomes their SECOND consumer, **these three helpers should move from
     `octobot_mcp.tools.backtesting` to `octobot_mcp.tools._shared`** as
     part of this milestone — following this project's own established
     precedent for exactly this situation (`_parse_content_disposition_filename`
     moved to `_shared.py` in milestone 13 when `export_logs` became its
     second consumer).
5. **`get_job_result` gets one new branch, not a new tool.** The existing
   function in `octobot_mcp.tools.backtesting` adds `elif job.kind ==
   "data_collection": return job.result` alongside its existing
   `compare_strategies`/backtest branches. The stored `result` (set once, at
   `state="completed"`, by the new watcher — INV-3 unchanged) is:
   ```
   {"exchange": str, "symbols": [str], "time_frames": [str]|null,
    "start_timestamp": int|null, "end_timestamp": int|null,
    "message": str}
   ```
   — an honest echo of the request plus OctoBot's own completion message,
   **not** a fabricated file manifest: neither `collect_data_file`'s
   response nor the Socket.IO payload names the resulting data file
   anywhere (confirmed above), so this job's result deliberately does not
   invent one (same NFR-8 honesty this project already applies to backtest
   report fields). The tool description tells the agent to call
   `list_data_files()` (ADR-0008) afterward to find the new file.
6. **The generic staleness/ETA algorithm (`octobot_mcp.eta`) is reused
   completely unchanged.** It operates purely on `(elapsed_seconds,
   progress_percent)` samples and has no notion of job kind — no new ETA
   code is needed for data collection, including its mid-run-stall
   detection (NFR-3), which becomes the primary signal for a data
   collection that has silently stopped progressing (see Risks: OctoBot's
   own status payload has no distinguishable "failed" value beyond the
   4 enumerated strings, so staleness detection is the safety net).

## Consequences

### Positive
- Because `JobStore` and `octobot_mcp.tools.jobs` were already built
  generically across `kind` (a design choice made in milestones 7/10 for
  `compare_strategies`, not anticipating this exact reuse), extending to a
  second job kind costs almost nothing: one `Literal` value, one new lock
  field, one new watcher module, one new `get_job_result` branch. No
  changes to `get_job_status`/`list_jobs`/`cancel_job`, `context.py`, or the
  ETA module at all.
- The agent-facing experience is identical to backtests: poll
  `get_job_status`, watch `progress_percent`/`eta_seconds` advance, call
  `get_job_result` on completion, `cancel_job` to abort — no new concepts
  for a client to learn.
- Matches this project's own stated top-line priority (progress/ETA
  visibility for long-running operations) rather than treating this new
  capability as a lesser, unmonitored fire-and-forget action.

### Negative
- A second Socket.IO namespace/event pair to maintain, with its own
  untested compatibility risk (see Risks) — `/backtesting` was round-trip
  tested live in milestone 8; `/data_collector` has not been.
- The nested step-progress → flat-percent conversion is a new heuristic
  this server invents (OctoBot itself never expresses "overall percent
  complete" for a multi-step collection) — an additional, data-collection
  specific approximation layered on top of the ETA estimator's own
  already-heuristic nature (mirrors ADR-0002's own "ETA is an MCP-server
  heuristic, not upstream truth" framing, extended one layer further here).
- A second independent lock is a second piece of concurrency state a future
  maintainer must reason about — mitigated by making it structurally
  identical to, and clearly documented alongside, `backtest_execution_lock`.

## Risks
| Risk | Severity | Likelihood | Mitigation |
|---|---|---|---|
| `current_step`/`total_steps` indexing assumption (1-indexed, `current_step` never exceeds `total_steps`) is wrong, producing an out-of-range or misleading `progress_percent` | Medium | Medium | Developer verification task (first task of the new tasklist item, mirroring ADR-0002's own "connect and confirm the round trip first" precedent): run one real multi-symbol/multi-timeframe collection against the live instance and log the raw `(current_step, total_steps, current_step_percent)` sequence before finalizing the conversion formula; the formula already clamps to `[0, 100]` defensively regardless |
| `python-socketio` round-trip against `/data_collector` has not been tested live (unlike `/backtesting`, tested in milestone 8) | High | Low | First implementation task: repeat milestone 8's own standalone compatibility check, this time against `/data_collector`/`data_collector_status`, before wiring the full watcher — same version of `python-socketio` already pinned and proven for `/backtesting`, so a mismatch is unlikely but unverified |
| OctoBot's own data-collection failure path (e.g. an exception inside `_background_collect_exchange_historical_data`) has no distinguishable status value beyond `not started`/`starting`/`collecting`/`finished` — a crashed collection may look identical to a healthy one that has stopped progressing | Medium | Low | Rely on the existing, generic mid-run-stall staleness detection (NFR-3) to flag `eta_confidence: "low"`/`eta_seconds: null` once progress stops advancing for >15s, and document in the tool description that a data-collection job stuck at a fixed `progress_percent` for an extended period likely failed silently upstream and should be cancelled; no new detection machinery is built for this in v1 |
| Conflating this job's lock with `backtest_execution_lock` (the "obvious" reuse) would silently and incorrectly serialize backtests against data collections | Medium | Low (mitigated by this ADR's own explicit decision) | Addressed directly by decision point 3 (a dedicated `data_collection_execution_lock`) — flagged here so a future refactor doesn't "simplify" the two locks back into one without re-confirming OctoBot's own constraints are actually coupled |

## Alternatives considered
- **Option B: simple synchronous fire-and-forget tool** (matching
  `update_trading_config`'s POST-and-return pattern, no progress tracking).
  Rejected: real-time progress is confirmed to exist via `/data_collector`'s
  Socket.IO namespace, the same mechanism ADR-0002 already built
  infrastructure for — choosing Option B here would mean building a
  *worse* experience for data collection than backtesting has, for a
  capability whose whole point (per the user's own framing of this gap) is
  letting an agent collect data it doesn't already have, which is
  plausibly the *longer*-running of the two operations for a wide date
  range. This would contradict the project's own top-line requirement
  (progress/ETA visibility for long-running operations) without a forcing
  technical reason not to.
- **A bespoke, non-`JobStore` polling mechanism specific to data
  collection** (its own small in-memory dict, its own watcher, no reuse of
  `JobKind`/`JobStore`). Rejected: would duplicate TTL/retention eviction,
  ETA estimation, and cancellation semantics that already exist generically,
  for no benefit — violates simplicity/maintainability precedence (tier 3)
  given how cleanly `JobStore` already generalizes (per the "generic across
  kind" confirmation above).
- **Reusing `backtest_execution_lock` for data collection too** (one lock
  for "any OctoBot background job"). Rejected: source confirms these are
  two independent one-at-a-time constraints inside OctoBot itself; sharing
  one lock would be a correctness regression (over-serializing operations
  OctoBot itself allows to run independently), not just a simplification.
- **A dedicated `stop_data_collection` tool** instead of reusing
  `cancel_job`. Rejected: inconsistent with the existing, working
  `cancel_job`-for-everything precedent; would force a caller to remember
  which stop tool applies to which job kind for no benefit.
