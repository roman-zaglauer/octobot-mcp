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

## Functional requirements

| ID | Requirement | Priority | Acceptance criterion |
|---|---|---|---|
| FR-1 | The server SHALL provide a tool to list all profiles known to the OctoBot instance, including which one is currently selected. | MUST | Given the OctoBot instance has ≥2 profiles, when `list_profiles` is called, then the result includes every profile's id and name, and exactly one is marked `is_selected: true`. |
| FR-2 | The server SHALL provide a tool to fetch a single profile's details by id (defaulting to the current profile). | MUST | Given a valid `profile_id`, when `get_profile` is called, then the result includes name, description, risk, complexity, and profile_type. |
| FR-3 | The server SHALL provide tools to create a profile via duplication of an existing profile, file import, URL import, or cloud-strategy import. | MUST | Given a valid source of each kind, when `create_profile` is called with that `mode`, then a new profile is created on the OctoBot instance and its id/name is returned. |
| FR-4 | The server SHALL provide a tool to update a profile's mutable fields (name, description, risk, complexity, config). | MUST | Given a valid `profile_id` and patch, when `update_profile` is called, then OctoBot's stored profile reflects the patch and the tool returns the updated fields. |
| FR-5 | The server SHALL provide a tool to delete a profile, gated by `confirm=true` (ADR-0003). | MUST | Given a valid `profile_id` and `confirm` omitted/false, when `delete_profile` is called, then no deletion occurs and a refusal explaining the required flag is returned. Given `confirm=true`, then the profile is deleted. |
| FR-6 | The server SHALL provide a tool to select a profile as the currently active one. | MUST | Given a valid `profile_id`, when `select_profile` is called, then subsequent `list_profiles` calls show that profile as `is_selected: true`. |
| FR-7 | The server SHALL provide a tool to convert a profile to a live-trading profile and select it, gated by `confirm=true`. | MUST | Given `confirm=false`/omitted, no conversion occurs and a refusal is returned naming the live-trading risk. Given `confirm=true`, the profile is converted and selected. |
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
  robots.txt/static media/reboot/onboarding/community/webhook/feedback).

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
5. **Whether a separate "simulated vs. live" toggle exists beyond profile
   `profile_type`** — `convert_profile_to_live` is the only confirmed
   live-trading signal; if OctoBot also gates real order placement on a
   separate exchange-level flag, that flag is currently unaccounted for and
   should be confirmed against source/live instance before considering the
   confirm-gating list (ADR-0003) complete.
6. **Valid values/format for `start_backtest`'s `current_bot_data` mode
   fields** (`exchange_id`, `exchange_type`, `data_source`) — only the route
   and action-dispatch (`start_backtesting_with_current_bot_data`) are
   confirmed from source; the acceptable values/format for these three
   fields were not confirmed. Developer must trigger this mode against the
   live instance (or trace `models.start_backtesting_using_current_bot_data`'s
   callers) to confirm valid values **before** the backtest-watcher
   milestone (tasklist step 8) — do not implement against assumed values.

## Traceability
Source material: OctoBot GitHub (Drakkar-Software/OctoBot, `master` branch,
`packages/tentacles/Services/Interfaces/web_interface/**`), plus the original
live-instance route scrape recorded in `docs/info.md` and this session's
research. Downstream artefacts: `docs/specs/octobot-mcp-tool-spec.md`
(implementable contracts), `docs/tasklist.md` (developer sequencing), ADRs
0001–0006 (rationale for the constraints these requirements assume).
