# ADR-0002: Async job/polling model as source of truth, with layered progress notifications and a Socket.IO progress watcher

## Status
Accepted

## Context
Backtests and strategy comparisons run for minutes. MCP tool calls are
naturally request/response; blocking a tool call for minutes risks client-side
timeouts and gives the agent no way to report intermediate status to the user.

Verified via OctoBot source (`controllers/backtesting.py`,
`models/backtesting.py`, `websockets/backtesting.py`, master branch):
- `POST /backtesting?action_type=start_backtesting` (explicit data files) and
  `?action_type=start_backtesting_with_current_bot_data` (current profile +
  exchange) start a run; `?action_type=stop_backtesting` stops it.
- `GET /backtesting?update_type=backtesting_report&source=...` is the **only**
  plain-HTTP JSON branch of that route, and it returns the *final* report
  (`{"report": ..., "trades": ...}`) — it does **not** return interim
  progress. No other `update_type` value is handled.
- Live progress is **only** available via Flask-SocketIO, namespace
  `/backtesting`: the client emits `backtesting_status` and the server emits
  back `backtesting_status` with payload `{"status": ..., "progress": ...,
  "errors": ...}`, sourced from `models.get_backtesting_status()` (returns
  `(status, progress_percent, error_count)`, status ∈ at least
  `starting|computing|finished`). OctoBot's own frontend polls this every
  50ms; that cadence is UI-grade, not appropriate for a server-side watcher.
- OctoBot only ever runs **one** backtest at a time on a given instance
  (confirmed by the single `start_backtesting`/`stop_backtesting` dispatch
  with no run-id parameter and no queuing in the controller).
- There is **no ETA field anywhere** in OctoBot's API or socket payload.

## Decision
1. **Async job pattern is the source of truth.** Every long-running operation
   (`start_backtest`, `compare_strategies`) returns a `job_id` immediately.
   Callers poll `get_job_status(job_id)` for `state`, `progress_percent`,
   `elapsed_seconds`, `eta_seconds`, `eta_confidence`, and `errors`, and call
   `get_job_result(job_id)` once `state == "completed"`.
2. **A background watcher, not the caller, drives progress acquisition.**
   When a job starts, the server opens (or reuses) a `python-socketio` async
   client connected to OctoBot's `/backtesting` namespace, and on a
   **1.5s server-side interval** emits `backtesting_status` and updates the
   in-memory job record from the response. This interval is a deliberate,
   documented choice — not OctoBot's 50ms UI cadence — chosen to bound load on
   OctoBot and on the ETA sampler while remaining responsive enough for
   minutes-long runs; it is a single named constant so it is easy to tune.
3. **Native MCP progress notifications are a convenience layer on top,** not
   the source of truth: if the caller supplies a `progressToken`, the server
   also emits an MCP progress notification each time the watcher observes a
   changed value. If no token is supplied, or the client doesn't render
   notifications, polling still works because the job record is authoritative
   regardless of notification delivery.
4. **One MCP job can wrap multiple OctoBot backtest runs.** Because OctoBot
   itself is single-run-at-a-time, `compare_strategies` submits its N
   configured runs sequentially through a single `asyncio.Lock`
   (`backtest_execution_lock`); this lock is also used by plain `start_backtest`
   so two concurrent MCP tool calls queue correctly instead of racing against
   OctoBot's single active run. The parent job's `progress_percent` is the
   weighted aggregate across sub-runs (see spec for the formula).
5. **Job store is in-memory, single-process** (dict keyed by `job_id`,
   bounded by a retention cap/TTL). This is an explicit v1 decision, not an
   oversight: per ADR-0001 the server is already single-process/stdio-bound,
   so job lifetime naturally can't outlive the client session; adding
   persistence (e.g. sqlite) would buy nothing until multi-client/remote
   transport (out of scope) is built.

## Consequences

### Positive
- Polling remains correct and cheap (in-memory lookup) regardless of MCP
  transport hiccups or whether the client supports progress notifications at
  all.
- The one-lock design gets correct serialization against OctoBot's
  one-backtest-at-a-time constraint "for free," and gives `compare_strategies`
  a natural implementation (N sequential acquisitions) rather than bespoke
  queuing logic.
- ETA is clearly framed as an MCP-server-computed heuristic (see spec),
  matching the confirmed fact that OctoBot provides no ETA itself — no false
  claim of upstream support.

### Negative
- Requires a second client protocol (Socket.IO) in addition to the REST HTTP
  client — extra dependency (`python-socketio[asyncio_client]`) and a second
  failure mode to handle (socket connect/auth-namespace/version mismatch)
  independent of the REST client's failure modes.
- All job state is lost on server restart. Accepted (see above) — flagged
  explicitly as a v1 limitation, not hidden.
- The 1.5s watcher interval means `elapsed_seconds`/`progress_percent` can lag
  reality by up to that interval; acceptable given backtests run for minutes.

## Risks
| Risk | Severity | Likelihood | Mitigation |
|---|---|---|---|
| Socket.IO client/server version mismatch (Engine.IO protocol) between `python-socketio` and OctoBot's bundled Flask-SocketIO version prevents the watcher from connecting at all | High | Medium | Verification task for developer: connect a `python-socketio` async client to the live instance's `/backtesting` namespace before wiring the full watcher; pin a compatible `python-socketio` version once confirmed |
| A crashed/restarted MCP server silently loses an in-flight backtest job while OctoBot keeps running it | Medium | Low | On next `get_job_status` for an unknown `job_id`, return a clear "job not found (server may have restarted)" error rather than a generic 404; document that OctoBot itself is unaffected and can be re-queried via `GET /backtesting_run_id` |

## Alternatives considered
- **Fully synchronous tool call** (block until backtest finishes, return final
  report; emit progress notifications during the block). Rejected: ties up
  the calling agent's turn for minutes, risks client-side call timeouts, and
  has no fallback if the client doesn't render notifications — the caller
  would have no way to see progress at all.
- **Notifications-only, no polling tool.** Rejected: MCP progress
  notifications are not guaranteed to be replayed/persisted across a
  reconnect, and are optional for clients to render; making them the *only*
  channel would make progress/ETA invisible for a large class of clients,
  directly violating the product requirement.
