# Requirements: OctoBot MCP Server (v1)

## Context
An MCP server, running as a local stdio subprocess, wraps a single OctoBot
instance's HTTP (and, for backtest progress, Socket.IO) API so an AI agent
can manage trading profiles and run/compare backtests, plus curated
CRUD-style control over trading config, portfolio, exchanges, and
tentacles/logs where a real endpoint exists.

## Stakeholders
- **Primary user**: a developer/trader running Claude Code (or another MCP
  client) locally against their own OctoBot instance.
- **Downstream implementer**: `developer` agent, building against this spec.
- **Upstream dependency owner**: OctoBot (Drakkar-Software), whose route
  contracts this server wraps and does not control.

## Assumptions & constraints
- A1: Exactly one OctoBot instance, reachable over plain HTTP on a trusted
  LAN, base URL fixed via `OCTOBOT_BASE_URL` env var at server startup
  (ADR-0004).
- A2: The target instance has `login_required_when_activated` disabled;
  session-login is out of scope for v1 (ADR-0005).
- A3: Single MCP client/session at a time (stdio, ADR-0001).
- A4: OctoBot runs at most one backtest at a time; this server does not
  change that — it serializes its own submissions to match (ADR-0002).
- A5: Some read capabilities (profile discovery) depend on scraping one
  HTML page whose exact rendered structure has been verified against source
  but **not yet against the live instance** (see open questions).
- A6 (added for the historical-data-collection addendum, ADR-0007): OctoBot
  enforces its own "one data collection run at a time" limit, independent
  of its "one backtest at a time" limit (A4) — confirmed to be a distinct
  resource guarded by a distinct internal reference, not the same
  constraint reused. This server serializes its own `start_data_collection`
  submissions via a dedicated lock to match, separate from the backtest
  lock; a human using OctoBot's own web UI concurrently could still trigger
  OctoBot's own "already running" guard against an MCP-initiated
  collection, surfaced as a classified `DataCollectionStartFailedError`
  rather than a raw failure.

## Functional requirements

| ID | Requirement | Priority | Acceptance criterion |
|---|---|---|---|
| FR-1 | The server SHALL provide a tool to list all profiles known to the OctoBot instance, including which one is currently selected. | MUST | Given the OctoBot instance has ≥2 profiles, when `list_profiles` is called, then the result includes every profile's id and name, and exactly one is marked `is_selected: true`. |
| FR-2 | The server SHALL provide a tool to fetch a single profile's details by id (defaulting to the current profile). | MUST | Given a valid `profile_id`, when `get_profile` is called, then the result includes name, description, risk, complexity, and profile_type. |
| FR-3 | The server SHALL provide tools to create a profile via duplication of an existing profile, file import, URL import, or cloud-strategy import. | MUST | Given a valid source of each kind, when `create_profile` is called with that `mode`, then a new profile is created on the OctoBot instance and its id/name is returned. |
| FR-4 | The server SHALL provide a tool to update a profile's mutable fields (name, description, risk, complexity, config). | MUST | Given a valid `profile_id` and patch, when `update_profile` is called, then OctoBot's stored profile reflects the patch and the tool returns the updated fields. |
| FR-5 | The server SHALL provide a tool to delete a profile, gated by `confirm=true` (ADR-0003). | MUST | Given a valid `profile_id` and `confirm` omitted/false, when `delete_profile` is called, then no deletion occurs and a refusal explaining the required flag is returned. Given `confirm=true`, then the profile is deleted. |
| FR-6 | The server SHALL provide a tool to select a profile as the currently active one. | MUST | Given a valid `profile_id`, when `select_profile` is called, then subsequent `list_profiles` calls show that profile as `is_selected: true`. |
| FR-7 | The server SHALL provide a tool to relabel a profile as OctoBot's internal `LIVE` profile type and select it, gated by `confirm=true`. **Note (open question #5, resolved): this does NOT enable real-money trading** — that is governed by a separate `config.trader.enabled` flag this tool never touches; no tool in this server's scope can set that flag (see open question #5). | MUST | Given `confirm=false`/omitted, no conversion occurs and a refusal is returned explaining the relabeling is not itself a real-trading toggle. Given `confirm=true`, `profile.profile_type` is set to `LIVE` and the profile is selected — `config.trader.enabled` is unaffected either way. |
| FR-8 | The server SHALL provide a tool to export a profile as a downloadable archive. | SHOULD | Given a valid `profile_id`, when `export_profile` is called, then the result contains a base64-encoded zip payload and a filename. |
| FR-9 | The server SHALL provide a tool to start a backtest, either against explicit historical data files or against the currently active profile/exchange, returning a `job_id` immediately (no blocking). | MUST | When `start_backtest` is called with either valid `mode`, then the call returns within 2s with a `job_id` and `state: "queued"` or `"running"`, without waiting for the backtest to finish. |
| FR-10 | The server SHALL provide a tool to poll job status, returning state, progress percentage, elapsed time, ETA, and ETA confidence. | MUST | Given a `job_id` for a running backtest, when `get_job_status` is called, then the result includes `progress_percent` (0–100), `elapsed_seconds`, `eta_seconds` (nullable), and `eta_confidence` ∈ {low, medium, high}. |
| FR-11 | The server SHALL provide a tool to fetch the final result of a completed job (backtest report, or comparison table for `compare_strategies`). | MUST | Given a `job_id` with `state: "completed"`, when `get_job_result` is called, then the result contains OctoBot's report/trades data for a single backtest, or the diffed comparison table for a compare job. |
| FR-12 | The server SHALL provide a tool to stop/cancel a running or queued job. | MUST | Given a `job_id` in state `queued` or `running`, when `cancel_job` is called, then OctoBot's active run is stopped (for a running backtest job) or the job is marked cancelled without ever starting (for a queued job), and subsequent `get_job_status` reflects `state: "cancelled"`. |
| FR-13 | The server SHALL provide a `compare_strategies` tool accepting a list of ≥2 profile/strategy configs, running one backtest per entry sequentially, and returning a single `job_id` whose status reflects combined progress/ETA across all runs. | MUST | Given 3 valid profile configs, when `compare_strategies` is called, then a single `job_id` is returned; `get_job_status` shows monotonically non-decreasing combined `progress_percent` as sub-runs complete; `get_job_result` after completion returns one row per input config, with the diff table's fields derived from whatever numeric fields are common across all children's OctoBot report payloads — no specific field names (e.g. "profitability") are guaranteed in advance; see NFR-8 and open question #3 on the unverified report schema. |
| FR-14 | The server SHALL provide read tools for portfolio, open orders, positions, trade history, PnL history, and historical portfolio value, backed by OctoBot's confirmed JSON API, plus a tool to trigger a portfolio refresh/re-sync against the exchange. | MUST | Each read tool returns the same fields OctoBot's corresponding `/api/...` route returns, with no scraping involved; `refresh_portfolio` returns a confirmation that OctoBot's portfolio sync (`POST /api/refresh_portfolio`) was triggered. |
| FR-15 | The server SHALL provide a tool to cancel an order and to close a position. | SHOULD | Given a valid order/position id, the corresponding tool performs the cancel/close and returns confirmation; not `confirm`-gated per ADR-0003's scoping (flagged as revisitable). |
| FR-16 | The server SHALL provide tools to clear orders/trades/portfolio/transactions history, each gated by `confirm=true`. | MUST | Each clear-history tool refuses without `confirm=true`, explaining the irreversible deletion; proceeds and returns a count/confirmation with `confirm=true`. |
| FR-17 | The server SHALL provide a tool to update exchange API credentials, gated by `confirm=true`, with secret values never echoed back in full. | MUST | Given `confirm=true` and valid credential fields, the tool applies the update and returns only a redacted confirmation (e.g. last 4 characters of the key). See NFR-6. |
| FR-18 | The server SHALL provide read tools for currency/symbol lookups per exchange and exchange-account compatibility checks. | SHOULD | Each maps directly to a confirmed `/api/...` route with matching fields. |
| FR-19 | The server SHALL provide a tool to read and update a tentacle's configuration by name. | SHOULD | Given a valid tentacle name, `get_tentacle_config`/`update_tentacle_config` read/write via the confirmed `/config_tentacle(s)` routes. |
| FR-20 | The server SHALL expose `get_logs` and `list_tentacles` (enumerate-all) as discoverable tools that return a structured "not available in v1" result rather than omitting the capability silently. | MUST | Calling either tool returns a structured response with a clear reason and a suggested alternative (`export_logs`, `get_tentacle_config`), never a raw 404/exception. |
| FR-21 | The server SHALL provide a tool to update trading-mode (strategy/tentacle-activation) configuration for the current profile, matching the "trading config" domain named in scope. | SHOULD | Given a valid patch, when `update_trading_config` is called, then OctoBot's stored trading config reflects the patch (via `POST /config`'s `TRADING_CONFIG_KEY` branch) and the tool returns the updated fields; not confirm-gated (mutates config, does not destroy data or expose credentials). |

### Historical data collection (added addendum — genuinely new scope, ADR-0007/ADR-0008; not in v1's original curated scope)

| ID | Requirement | Priority | Acceptance criterion |
|---|---|---|---|
| FR-22 | The server SHALL provide a tool to start a historical data collection run against a given exchange/symbols (optionally timeframes and a date range), returning a `job_id` immediately (no blocking), following the same async job pattern as `start_backtest` (ADR-0002/ADR-0007). | MUST | When `start_data_collection` is called with a valid `exchange` and non-empty `symbols`, then the call returns within 2s with a `job_id` and `state: "queued"`, without waiting for the collection to finish. |
| FR-23 | `get_job_status`, `list_jobs`, `cancel_job`, and `get_job_result` SHALL each support jobs of `kind: "data_collection"` with the same semantics they already provide for `kind: "backtest"` (progress/ETA visibility, cancellation, final-result retrieval), per ADR-0007. | MUST | Given a `job_id` for a running data-collection job, `get_job_status` returns `progress_percent`/`elapsed_seconds`/`eta_seconds`/`eta_confidence` in the same shape as for a backtest job; `cancel_job` transitions it to `cancelled`; `get_job_result` on a `completed` data-collection job returns `{"exchange", "symbols", "time_frames", "start_timestamp", "end_timestamp", "message"}` without fabricating a filename OctoBot itself never reports. |
| FR-24 | The server SHALL provide a tool to list historical data files already present on the OctoBot instance, including the exact identifier `delete_data_file` expects (ADR-0008, Tier B). | MUST | Given the OctoBot instance has ≥1 previously collected/imported data file, when `list_data_files` is called, the result includes each file's `file`/`exchange`/`symbols`/`time_frames` and its full-vs-partial date-range fields; the tool raises a distinct, named error (never a silent empty list) if the underlying scrape's structural assumptions don't hold. |
| FR-25 | The server SHALL provide a tool to delete a historical data file by the identifier `list_data_files` returns, gated by `confirm=true` (ADR-0003). | SHOULD | Given a valid `file` identifier and `confirm` omitted/false, when `delete_data_file` is called, then no deletion occurs and a refusal explaining the required flag is returned. Given `confirm=true`, then the file is deleted. (User-confirmed decision, reversing this addendum's original not-gated draft call: an erroneous delete means re-running a collection that can take minutes to hours, unlike the instantly-correctable `cancel_order`/`close_position`, placing this closer to `clear_*_history`'s risk profile per ADR-0003's actual purpose.) |
| FR-26 | The server SHALL provide a tool to upload an existing OctoBot-format historical data file to the instance without collecting it via an exchange. | SHOULD | Given a valid file payload, when `import_data_file` is called, then the file is saved on the OctoBot instance and the tool returns a confirmation/message; not confirm-gated (creates new state, does not destroy data). |
| FR-27 | The server SHALL provide read tools for the exchange-scoped symbol list and available timeframe list a data collection run may target. | SHOULD | Given a valid `exchange`, `get_available_symbols_for_collection`/`get_available_timeframes_for_collection` each return the same list OctoBot's own `/data_collector` `symbol_list`/`available_timeframes_list` JSON actions return, with no scraping involved. This requirement's symbols half MAY be satisfied by reusing the existing `get_all_symbols` tool instead of adding a new one, if a developer confirms it returns equivalent data for the same exchange (see tool spec's developer note); the timeframes half has no existing equivalent and MUST be a new tool regardless. |
| FR-28 | The server SHALL provide a tool to update evaluator activation configuration for the current profile, matching the "trading config" domain's existing `update_trading_config` tool's own pattern (same `POST /config` route, same underlying model function, a different top-level request-body key). | SHOULD | Given a valid `patch` (a map of evaluator class name → enabled boolean), when `update_evaluator_config` is called, then OctoBot's stored evaluator-activation config reflects the patch (via `POST /config`'s `EVALUATOR_CONFIG_KEY` branch) and the tool returns the updated fields; not confirm-gated (mutates which evaluators are active, does not destroy data or expose credentials), by the same ADR-0003 reasoning already applied to `update_trading_config`. |
| FR-29 | The server SHALL actually emit its own structured per-call/per-job-transition log lines (already specified in the tool spec's "Observability" section) to a durable, discoverable destination — closing a gap where those log calls exist in code but were never wired to any configured `logging` handler, so they were silently dropped. | MUST | On process startup, `main()` configures a rotating file handler (bounded size, per NFR-16) writing to a documented, overridable path, plus a stderr handler for `WARNING`+, before `mcp.run()` is called. After any tool call, the corresponding structured log line (per the tool spec's existing format) is present in that file. Import-time module loading (e.g. under `pytest`) never attaches a handler or creates a log file as a side effect — only `main()` does. |
| FR-30 | The server SHALL provide a tool to trigger a full OctoBot process restart, scoped to unblocking evaluator-config-tuning backtest validation (ADR-0010) — a narrow, explicit reversal of this doc's own "Out of scope" reboot exclusion, for exactly this one capability. | SHOULD | Given `confirm=true`, when `restart_octobot` is called, then it (1) refuses if this server's own `JobStore` shows an active backtest/data-collection job (naming the job(s)), (2) otherwise issues `GET /wait_reboot?reboot=true` and returns `{"restart_triggered": true, "message": ...}` on a 200 response, or raises `RestartFailedError` otherwise. Without `confirm=true`, it refuses via the shared `require_confirmation` helper (ADR-0003) with a reason string that is more detailed/risk-scaled when the currently-selected profile's `profile_type` is `LIVE` (per ADR-0010 Decision 3) than when it is `SIMULATOR` or inconclusive. |
| FR-31 | The server SHALL provide a tool to poll for OctoBot's HTTP server becoming reachable again after a restart, honestly reporting a bounded timeout rather than blocking indefinitely or fabricating success (ADR-0010 Decision 5). | SHOULD | Given a bounded `timeout_seconds`/`poll_interval_seconds`, `wait_for_octobot_ready` returns `{"ready": true, "elapsed_seconds", "attempts"}` once a lightweight read route responds, or `{"ready": false, "elapsed_seconds", "attempts"}` on timeout — never raises for a timeout, and never claims the instance is "ready" for more than "the HTTP server answered" (see open question #14's cache-clear-timing caveat). |
| FR-32 | The server SHALL provide a tool to enumerate every evaluator OctoBot knows about, including its current activation state and category, including the exact identifier `update_evaluator_config`'s `patch` keys expect (ADR-0009, reversed from its original NO-GO recommendation by explicit user decision). | SHOULD | Given the OctoBot instance has ≥1 evaluator installed (in any category), when `list_evaluators` is called, the result includes each evaluator's `name`/`enabled`/`type` via a Tier-B scrape of `GET /advanced/evaluator_config`; the tool raises a distinct, named error (`EvaluatorScrapeContractBrokenError`, never a silent empty list) only if zero evaluators are found across ALL FOUR category containers combined or an expected container is missing entirely — a single category legitimately having zero evaluators is not itself an error (confirmed live: this project's own test instance has zero "Scripted evaluators" installed). |

## Non-functional requirements (ASRs)

| ID | Category | Requirement | Measurable target | Verification |
|---|---|---|---|---|
| NFR-1 | Responsiveness | Polling tools (`get_job_status`, `list_jobs`) SHALL respond without any network call to OctoBot — pure in-memory lookup. | p95 latency < 50ms for `get_job_status` under sustained polling every 2s during a running job. | Load test: poll a running job every 2s for its full duration, assert p95/p99 latency. |
| NFR-2 | Progress fidelity | The background watcher SHALL update job progress at a fixed, documented interval. | Watcher poll interval = 1.5s ±10% jitter, configurable via one named constant. | Unit test asserting watcher loop timing; code review of the constant's single definition site. |
| NFR-3 | ETA honesty | The ETA estimator SHALL return `null` with `eta_confidence: "low"` rather than a fabricated number when insufficient samples exist, OR when no new progress sample has arrived recently (mid-run stall) even if earlier samples would otherwise support a confident estimate. | ETA is non-null only once ≥3 progress samples spanning ≥5s have been observed (see spec for full heuristic), AND is forced back to null/low-confidence if no new sample has arrived within 10× the watcher interval (15s by default). | Unit tests with synthetic progress sequences, including a "stalled at 0%" sequence and a "mid-run stall" sequence (progress advances normally then stops updating for >15s while the job stays `running`), both asserting `eta_seconds: null`. |
| NFR-4 | Error mapping | All OctoBot connectivity failures (timeout, connection refused, 4xx, 5xx, unexpected response shape) SHALL be mapped to a structured MCP error/result distinguishing failure class, never surfaced as a raw stack trace. | 100% of injected failure-mode tests (timeout, refused, 500, malformed JSON) produce a structured, classified error. | Integration tests against a mocked OctoBot HTTP layer covering each failure class. |
| NFR-5 | Timeout policy | Every outbound HTTP call to OctoBot SHALL have an explicit timeout; the server SHALL NOT hang indefinitely on an unresponsive instance. | Default timeout 10s for REST calls; Socket.IO connect timeout 5s. | Code review + a test against a deliberately non-responding mock server. |
| NFR-6 | Secret handling | Exchange API key/secret values SHALL NEVER appear in full in tool results, logs, or error messages. | 0 occurrences of a full raw secret value in any log line or tool response across the test suite. | Dedicated test asserting `update_exchange_credentials`'s response and all log output contain only a redacted form (e.g. last 4 chars). |
| NFR-7 | Confirm-flag semantics | Every tool in the destructive/high-risk set (ADR-0003) SHALL refuse without `confirm=true` via the shared confirmation helper, not bespoke per-tool logic. | 100% of the 7 gated tools (`delete_profile`, `convert_profile_to_live`, `update_exchange_credentials`, `clear_orders_history`, `clear_trades_history`, `clear_portfolio_history`, `clear_transactions_history`) use the shared `require_confirmation` helper (code review); each has a test asserting refusal without the flag and success with it. | Code review + unit test per gated tool. |
| NFR-8 | Report schema honesty | Since OctoBot's `get_backtesting_report`'s exact `report` dict schema is not fully confirmed from source (it originates in `octobot_api.get_independent_backtesting_report`, outside the tentacles package examined), the server SHALL pass through whatever fields OctoBot returns without inventing or renaming fields, and SHALL document in code comments which fields are confirmed (`profitability`, `max_drawdown`, `win_rate` — TBD, see open questions) vs. passthrough-only. | 0 invented/renamed fields in `get_job_result` output for backtest jobs. | Code review against a captured real report payload from the live instance (developer verification task). |
| NFR-9 | Memory bound | The in-memory job store SHALL NOT grow unbounded over a long-lived session. | Completed/failed/cancelled jobs evicted after a TTL (default 1h) or beyond a retention cap (default 50), whichever comes first. | Unit test: create >50 completed jobs, assert store size stays ≤ cap. |
| NFR-10 | Extensibility (auth) | The HTTP/Socket.IO client construction SHALL be isolated behind one factory function so future session-cookie auth (ADR-0005) is a localized change. | Adding login support touches ≤2 files (client factory + one new login step) without changing tool implementations. | Code review at implementation time. |
| NFR-11 | Extensibility (multi-instance) | Tool implementations SHALL NOT hardcode the single client instance as a module-level global accessed by name from arbitrary call sites; SHALL receive it via a single injected context object. | Adding a second instance later requires changing the context-construction site only, not each tool. | Code review. |
| NFR-12 | Scrape reliability | The Tier-B `/profiles_selector` scrape adapter (ADR-0006) SHALL fail loudly (raise a distinct, named exception) if expected structure is missing, never silently return a partial/empty result indistinguishable from "no profiles exist." | 100% of a test suite that mutates the fixture HTML's expected nodes triggers the distinct exception, not a fallback empty list. | Unit tests against both a valid and a deliberately-broken HTML fixture. |
| NFR-13 | Scrape reliability (second adapter) | The Tier-B `/data_collector` scrape adapter backing `list_data_files` (ADR-0008) SHALL fail loudly (a distinct, named exception, `DataFileScrapeContractBrokenError`, never conflated with `ProfileScrapeContractBrokenError`) if expected structure is missing, never silently return a partial/empty result indistinguishable from "no data files exist." | 100% of a test suite that mutates the fixture HTML's expected nodes triggers the distinct exception, not a fallback empty list. | Unit tests against both a valid and a deliberately-broken HTML fixture, mirroring NFR-12's test structure. |
| NFR-14 | Reuse / consistency | Data-collection job progress/ETA SHALL be computed by the same generic `octobot_mcp.eta` algorithm already used for backtest jobs, with no data-collection-specific ETA logic added (ADR-0007). | 0 new branches/functions in `octobot_mcp.eta` attributable to data-collection jobs; the only new code is the nested-step → flat-percent conversion, which lives in the data-collection watcher, not the ETA module. | Code review at implementation time. |
| NFR-15 | Concurrency correctness | The server SHALL serialize its own `start_data_collection` submissions via a lock independent of `backtest_execution_lock` (ADR-0007), reflecting OctoBot's own two independent one-at-a-time constraints. | A concurrent `start_backtest` + `start_data_collection` pair does not block on each other's lock; two concurrent `start_data_collection` calls do serialize via `data_collection_execution_lock`. | Unit test asserting both locks are acquired/released independently. |
| NFR-16 | Log volume bound | The server's own log file SHALL NOT grow unbounded over a long-lived process, mirroring NFR-9's job-store bound but for disk instead of memory. | Rotating file handler with a fixed `maxBytes`/`backupCount` (documented, overridable via env vars, sane defaults e.g. 5MB × 3 backups). | Unit test asserting the configured handler is a `RotatingFileHandler` with the expected bounds; code review of the one definition site. |
| NFR-17 | Timeout policy (restart readiness) | `wait_for_octobot_ready` SHALL NOT block past its own bounded timeout, mirroring NFR-5's outbound-call timeout policy but for a multi-attempt polling loop instead of one HTTP call. | Default `timeout_seconds`/`poll_interval_seconds` documented and overridable per-call; the loop returns `{"ready": false, ...}` at the deadline rather than raising or blocking further. | Unit test with a mocked always-unreachable transport asserting the loop returns within the configured timeout, not indefinitely. |
| NFR-18 | Scrape reliability (third adapter) | The Tier-B `/advanced/evaluator_config` scrape adapter backing `list_evaluators` (ADR-0009) SHALL fail loudly (a distinct, named exception, `EvaluatorScrapeContractBrokenError`, never conflated with `ProfileScrapeContractBrokenError`/`DataFileScrapeContractBrokenError`) only when the page-wide structural assumption breaks (zero evaluators across all four category containers combined, or a missing container), never when a single category legitimately has zero evaluators. | 100% of a test suite that mutates the fixture HTML's expected container/row structure triggers the distinct exception; a fixture with one category's container present-but-empty (mirroring the live instance's own "Scripted evaluators" case) does NOT trigger it. | Unit tests against a valid fixture, a fixture with one legitimately-empty category, and a deliberately-broken fixture (missing container / zero rows everywhere), mirroring NFR-12/NFR-13's test structure. |

## Data requirements
- Profile identity: OctoBot's `profile_id` is the sole stable identifier used
  in all profile tool inputs/outputs; names are not assumed unique.
- Job identity: server-generated UUIDv4 `job_id`, opaque to OctoBot.
- No PII is handled; exchange credentials are the only sensitive data class
  in scope (see NFR-6, and Security section of the spec).

## Interface requirements
- Transport: MCP stdio (ADR-0001).
- All tool inputs/outputs are JSON-schema-validated by the MCP SDK.
- All monetary/percentage fields pass through OctoBot's own units unchanged
  (no unit conversion invented by this server).

## Out of scope (v1)
- Session-cookie login to OctoBot (ADR-0005).
- Multi-instance / per-tool instance selection (ADR-0004).
- Persistent (cross-restart) job history (ADR-0002).
- `get_logs` / `list_tentacles` real implementations (ADR-0006, Tier C).
- MCP Resources (only Tools are exposed in v1 — a deliberate simplicity
  choice, revisit if a read-heavy "dashboard" use case emerges).
- Any UI-plumbing route explicitly excluded by the user (about/terms/
  robots.txt/static media/reboot/onboarding/community/webhook/feedback) —
  **amended by ADR-0010**: the general "reboot" exclusion still holds, but
  the user explicitly authorized one narrow capability inside it
  (`restart_octobot`/`wait_for_octobot_ready`, FR-30/FR-31) to unblock
  evaluator-config-tuning backtest validation. This is a recorded, narrow
  reversal, not a deletion of the broader exclusion.

## Open questions (flagged, not guessed)
1. **Exact rendered DOM/JSON shape of `/profiles_selector`** — verified
   against Jinja template variable *names* in source, not against the live
   instance's actual HTML output. Developer must fetch and inspect the live
   page before finalizing the Tier-B scrape adapter (spec has a dedicated
   verification task).
2. **Exact JSON path for exchange credentials inside `POST /config`'s
   `global_config` payload** — the route's dispatch is confirmed, but the
   nested key structure for `exchanges.<name>.api-key`/`api-secret` was not
   found in the portion of `configuration.py` read; must be confirmed via the
   live instance's browser devtools network tab (`/accounts` page's exchange
   edit form) before implementing `update_exchange_credentials`.
3. **Exact `report` dict schema from `get_backtesting_report`** — confirmed
   wrapper keys are `report` and `trades`; the `report` value's internal
   fields (profitability, drawdown, win rate, etc.) originate outside the
   examined package and were not confirmed. Developer must run one real
   backtest against the live instance and capture the actual payload before
   finalizing `compare_strategies`' diff-table field list.
4. **`python-socketio` client compatibility with OctoBot's bundled
   Flask-SocketIO version** — not verified; first implementation task for
   the backtest-watcher milestone (see ADR-0002 risks).
5. **RESOLVED (confirmed against source, a later session): yes, a separate
   "simulated vs. live" toggle exists, and `convert_profile_to_live` does
   NOT set it.** `is_real_trading(profile)` (`web_interface/models/profiles.py`)
   returns `trading_util.is_trader_enabled(profile.config)`, which reads
   `config[CONFIG_TRADER][CONFIG_ENABLED_OPTION]` (`config.trader.enabled`)
   — confirmed via `octobot_trading/util/config_util.py`. `convert_to_live_profile`
   only sets `profile.profile_type = ProfileType.LIVE`; `select_profile`
   (the other function `convert_profile_to_live`'s route calls) only updates
   profile selection and tentacle config. Neither touches
   `config.trader`/`config.trader-simulator` at all — confirmed by reading
   both functions' full bodies. **`convert_profile_to_live` therefore does
   not, and cannot, enable real-money trading by itself** — see FR-7's
   revised acceptance criterion and `convert_profile_to_live`'s own
   docstring. The one HTTP-visible route found that DOES set
   `config.trader.enabled` (`_save_distribution_user_config`,
   `web_interface/models/configuration.py`) is registered only when the
   OctoBot instance's distribution is `OctoBotDistribution.PREDICTION_MARKET`
   (confirmed against `controllers/__init__.py::register()`), mutually
   exclusive with the `DEFAULT` distribution this project assumes
   throughout — unrelated to OctoBot's actual onboarding wizard
   (`controllers/welcome.py`, always registered). This server currently has
   **no tool** that can toggle real-vs-simulated trading on an ordinary
   OctoBot instance. Whether to build one (which would need its own ADR,
   given the stakes) is an open, undecided question, not something this
   resolution silently answers.
6. **Valid values/format for `start_backtest`'s `current_bot_data` mode
   fields** (`exchange_id`, `exchange_type`, `data_source`) — only the route
   and action-dispatch (`start_backtesting_with_current_bot_data`) are
   confirmed from source; the acceptable values/format for these three
   fields were not confirmed. Developer must trigger this mode against the
   live instance (or trace `models.start_backtesting_using_current_bot_data`'s
   callers) to confirm valid values **before** the backtest-watcher
   milestone (tasklist step 8) — do not implement against assumed values.

The following are new for the historical-data-collection addendum
(ADR-0007/ADR-0008), continuing the same numbering:

7. **Exact DOM/value type behind `list_data_files`'s `file` field** —
   verified against the Jinja template's markup in source
   (`{{file}}`/`data-file={{file}}`) but not against the live instance's
   actual rendered output; specifically unconfirmed whether `file` is a
   bare filename string or a `str()`-rendered path-like object with a
   directory prefix. Developer must fetch and inspect the live
   `/data_collector` page before finalizing the scrape's `file`-field
   extraction and before wiring `delete_data_file` against its output (see
   ADR-0008's risk table).
8. **Units (milliseconds vs. seconds) for `start_data_collection`'s
   `start_timestamp`/`end_timestamp`** — the controller/model dispatch is
   confirmed from source (`models.collect_data_file(exchange, symbols,
   time_frames, start_timestamp, end_timestamp)`), but
   `_background_collect_exchange_historical_data`'s body (where any
   unit conversion would happen, mirroring `_start_backtesting`'s
   confirmed divide-by-1000 for `start_backtest`) was not read this
   session. Developer must confirm before implementing — default
   assumption (documented, not silently guessed): milliseconds, matching
   this project's existing convention for `start_backtest`'s equivalent
   fields, until proven otherwise.
9. **Indexing/bounds of `get_data_collector_status()`'s `current_step`/
   `total_steps`** — the function's return shape is fully confirmed from
   source (see ADR-0007), but whether `current_step` is 0- or 1-indexed,
   and whether it can transiently exceed `total_steps`, is not. This
   directly affects the nested-step → flat-percent `progress_percent`
   conversion formula ADR-0007 specifies (which already clamps
   defensively to `[0, 100]` regardless). Developer must capture a real
   multi-step collection's raw progress sequence against the live
   instance before finalizing the formula (ADR-0007's first risk-table
   entry).
10. **`_ensure_backtesting_limits`'s exact validation rules/message text**
    — `collect_data_file`'s source confirms this function is called and
    can reject a request with a message this tool would surface as
    `DataCollectionStartFailedError`, but the function's own body
    (plausibly a plan/tier-based duration or symbol-count cap) was not
    read this session. Developer should read it before writing this
    tool's docstring, so an agent isn't left guessing why a seemingly
    valid request was rejected.
11. **Whether a genuine mid-collection OctoBot-side failure (e.g. an
    unhandled exception inside `_background_collect_exchange_historical_data`)
    produces any distinguishable status signal** beyond the four
    enumerated values (`"not started"`/`"starting"`/`"collecting"`/
    `"finished"`) confirmed in `get_data_collector_status()`'s source —
    not confirmed either way this session. ADR-0007 documents the
    accepted fallback (rely on generic ETA staleness detection, NFR-3) if
    this remains unconfirmed; a developer who does confirm a distinguishable
    failure signal should wire it directly into `store.fail(...)` instead.

The following are new for the `update_evaluator_config` addendum (FR-28),
continuing the same numbering:

12. **Exact literal string value of `EVALUATOR_CONFIG_KEY`** — the
    `update_trading_config` implementation confirms this constant exists and
    names a third branch of `POST /config`'s dispatch (alongside
    `TRADING_CONFIG_KEY`/`global_config`), but the constant's own literal
    value was not read this session. Developer must confirm it (grep the same
    module `TRADING_CONFIG_KEY`'s value came from) before writing
    `update_evaluator_config`'s request body — do not guess a string like
    `"evaluator_config"` without checking.
13. **Exact semantics of `deactivate_others`** — `update_trading_config`'s
    docstring notes the `EVALUATOR_CONFIG_KEY` branch calls
    `models.update_tentacles_activation_config()` "with an extra
    `deactivate_others` argument neither other branch passes," but the
    argument's effect (e.g. whether omitted/unlisted evaluators in `patch`
    are force-disabled, and whether it defaults `true` or `false` at the
    OctoBot layer when this server doesn't pass it) was not traced into
    `update_tentacles_activation_config`'s own body this session. Developer
    must confirm before finalizing `update_evaluator_config`'s
    `deactivate_others` parameter's default and documented behavior.
14. **End-to-end OctoBot restart timing — RESOLVED. Whether "HTTP-reachable
    again" coincides with "the tentacle-config cache is actually clear" —
    RESOLVED NEGATIVELY: it does not** (ADR-0010). Timing: resolved by live
    testing (two real restarts, `SIMULATOR` profile, OctoBot 2.1.1 test
    instance) — the HTTP server stayed reachable for ~2.1s post-trigger
    (matching OctoBot's own confirmed `restart_bot(delay=2)`), was
    genuinely unreachable for ~6.3–6.7s, then answered again at ~8.5–8.9s
    total (n=2, one instance/version — treat as informed, not a guaranteed
    bound for every deployment). This same testing also found and fixed a
    real bug: `wait_for_octobot_ready`'s first poll fires immediately after
    `restart_octobot` returns, which can — and did, both times — land
    within OctoBot's ~2s pre-kill grace window and hit the OLD process,
    falsely reporting `ready: true` in ~0.05s before anything had actually
    restarted. Fixed: a success now only counts as `ready: true` once at
    least one prior poll in the same call has genuinely failed.
    **Cache-clearing coincidence, resolved negatively in a follow-up
    session**: a real, genuine restart was confirmed via dense polling, then
    `SimpleStrategyEvaluator`'s `default_config`/`required_evaluators` were
    checked immediately after "ready" — unchanged from before the restart.
    Tracing this into OctoBot source showed those specific fields are
    static per-tentacle config values, not activation-dependent at all, so
    this doesn't fully rule out the cache theory for every mechanism — but
    the user separately confirmed the same end result (restart does not
    unblock composition-tuning validation) on an entirely different OctoBot
    instance/session, independent of this specific field's quirk. This
    capability's stated purpose does not hold; see ADR-0010's Context
    ("Negative finding") and Consequences for the full accounting, and its
    "Alternatives considered" for the resulting open question of whether to
    keep or remove `restart_octobot`/`wait_for_octobot_ready`.

## Traceability
Source material: OctoBot GitHub (Drakkar-Software/OctoBot, `master` branch,
`packages/tentacles/Services/Interfaces/web_interface/**`), plus the original
live-instance route scrape recorded in `docs/info.md` and this session's
research. Downstream artefacts: `docs/specs/octobot-mcp-tool-spec.md`
(implementable contracts), `docs/tasklist.md` (developer sequencing), ADRs
0001–0006 (rationale for the constraints these requirements assume).

The historical-data-collection addendum (FR-22..FR-27, NFR-13..NFR-15, A6,
open questions 7–11) traces additionally to: OctoBot GitHub
(Drakkar-Software/OctoBot-Tentacles, `master` branch,
`Services/Interfaces/web_interface/{controllers,websockets,models}/
backtesting.py`, `templates/data_collector.html`), read directly this
session; and ADR-0007 (async job model decision)/ADR-0008 (Tier-B scrape
decision) for the rationale. Downstream: the same
`docs/specs/octobot-mcp-tool-spec.md` and `docs/tasklist.md`, extended
additively (new tasklist item, new tool-spec section) rather than modified
in place.
