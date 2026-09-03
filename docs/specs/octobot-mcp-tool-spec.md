# Spec: OctoBot MCP Server — Tool Inventory, Job Store, ETA Algorithm

## Purpose
Define the concrete, implementable MCP tool contracts (inputs/outputs,
errors, idempotency, confirm-gating), the async job store's internal design,
and the ETA-estimation algorithm, so the `developer` agent can implement
without re-deriving any of these decisions.

## Scope
In scope: all tools listed in the inventory below. Out of scope: anything
listed under the requirements doc's "Out of scope" section.

## Verification tiers (used throughout this spec)
- **[V]** Verified against OctoBot source (master branch) this session —
  route path, method, and payload keys read directly from the function body.
- **[V-live]** Verified against the live test instance's route scrape in the
  original research (`docs/info.md` context), independent of source reading.
- **[UNVERIFIED]** Not confirmed by source or live instance — a concrete
  developer verification task is named; do not implement against a guess.

## Tool inventory

### Profiles

| Tool | R/W | Confirm | Maps to | Tier |
|---|---|---|---|---|
| `list_profiles()` | R | no | scrape `/profiles_selector` | Tier B — [UNVERIFIED live DOM shape] |
| `get_profile(profile_id?)` | R | no | same scrape, filtered | Tier B — [UNVERIFIED live DOM shape] |
| `create_profile(mode, ...)` | W | no | `GET /profiles_management/duplicate`, `POST /profiles_management/import`, `POST /profiles_management/download` | [V] |
| `update_profile(profile_id, patch)` | W | no | `POST /profiles_management/update` | [V] |
| `delete_profile(profile_id, confirm)` | W | **yes** | `POST /profiles_management/remove` | [V] |
| `select_profile(profile_id)` | W | no | `GET /profile?select=<id>` | [V] |
| `convert_profile_to_live(profile_id, confirm)` | W | **yes** | `GET /profiles_management/use_as_live` | [V] |
| `export_profile(profile_id)` | R | no | `GET /profiles_management/export` | [V] |

#### `list_profiles()`
- Input: `{}`
- Output: `{"profiles": [{"profile_id": str, "name": str, "description": str, "risk": str, "complexity": str, "profile_type": "LIVE"|"SIMULATOR"|..., "is_selected": bool}], "warning": str|null}`
- Errors: `SCRAPE_CONTRACT_BROKEN` (see NFR-12) if the page structure doesn't match expectations — never returns an empty list silently in that case.
- Idempotent, read-only.

#### `get_profile(profile_id?)`
- Input: `{"profile_id": str | null}` — null/omitted means the currently selected profile.
- Output: single profile object (same shape as one entry of `list_profiles`).
- Errors: `PROFILE_NOT_FOUND` if `profile_id` doesn't match any entry from the underlying scrape.

#### `create_profile(mode, ...)`
- Input (discriminated by `mode`):
  - `{"mode": "duplicate", "source_profile_id": str}`
  - `{"mode": "import_file", "file_base64": str, "filename": str}`
  - `{"mode": "import_url", "url": str}`
  - `{"mode": "import_cloud_strategy", "strategy_id": str, "name": str, "description": str}`
- Per-mode OctoBot route mapping (explicit, so no mode is left to guess):
  - `duplicate` → `GET /profiles_management/duplicate?profile_id=<source_profile_id>` [V]
  - `import_file` → `POST /profiles_management/import` (multipart `file`) [V]
  - `import_url` → `POST /profiles_management/download` with form field `inputProfileLink=<url>` [V]
  - `import_cloud_strategy` → `POST /profiles_management/download` with JSON body `{"strategy_id", "name", "description"}` and no `inputProfileLink` [V]

  Note: `import_url` and `import_cloud_strategy` are two distinct input
  `mode`s of this tool but dispatch to the **same** OctoBot route
  (`/profiles_management/download`), distinguished by which fields are
  present in the request body — this is intentional (it mirrors OctoBot's
  own single-route, field-presence-based dispatch for that action), not a
  duplicate/ambiguous mapping.
- Output: `{"profile_id": str, "name": str, "message": str}`
- Errors: `IMPORT_FAILED` (wraps OctoBot's own `FileNotFoundError`/generic exception text from the `download` action — [V] confirmed both are caught and surfaced as a message string, not a stack trace).
- Not confirm-gated: creates new state, does not destroy or expose credentials/enable live trading.

#### `update_profile(profile_id, patch)`
- Input: `{"profile_id": str, "patch": {"name"?: str, "description"?: str, "risk"?: str, "complexity"?: str, "config"?: object}}`
- Output: `{"profile_id": str, "updated_fields": object}`
- Errors: `UPDATE_REJECTED` with OctoBot's own error string [V: `update_profile()` returns `(success, err)`, mapped 1:1 on failure with HTTP 400].

#### `delete_profile(profile_id, confirm=false)`
- Input: `{"profile_id": str, "confirm": bool}`
- Behavior: if `confirm != true` → `require_confirmation("delete_profile", profile_id, "This permanently deletes the profile and cannot be undone.")`, no OctoBot call made. If `confirm == true` → `POST /profiles_management/remove {"id": profile_id}`.
- Output on success: `{"profile_id": str, "name": str, "deleted": true}`.
- Errors: `DELETE_REJECTED` with OctoBot's error string [V: 400 branch returns `str(err)`].

#### `select_profile(profile_id)`
- Input: `{"profile_id": str}`; Output: `{"profile_id": str, "selected": true}`.

#### `convert_profile_to_live(profile_id, confirm=false)`
- Same confirm pattern as `delete_profile`, reason string: "This converts the profile to LIVE trading mode and selects it — real orders may be placed against a real exchange account."
- Output: `{"profile_id": str, "profile_type": "LIVE", "selected": true}`.

#### `export_profile(profile_id)`
- Output: `{"profile_id": str, "filename": str, "content_base64": str}`.

---

### Backtesting & comparison (all job-based — see Job Store section)

| Tool | R/W | Confirm | Maps to | Tier |
|---|---|---|---|---|
| `start_backtest(mode, ...)` | W | no | `POST /backtesting?action_type=start_backtesting[_with_current_bot_data]` | `data_files` mode [V]; `current_bot_data` mode's route/dispatch [V] but its field **values** [UNVERIFIED] — see requirements open question #6 |
| `compare_strategies(configs[])` | W | no | N × the above, sequential | [V] (mechanism) + [UNVERIFIED] report fields |
| `cancel_job(job_id)` | W | no | `POST /backtesting?action_type=stop_backtesting` (backtest jobs only; compare jobs cancel remaining queued sub-runs without a matching OctoBot call) | [V] |
| `get_job_status(job_id)` | R | no | in-memory job store only | n/a |
| `get_job_result(job_id)` | R | no | in-memory job store (fetched once, at completion, via `GET /backtesting?update_type=backtesting_report`) | [V] wrapper keys; [UNVERIFIED] `report` internals |
| `list_jobs(state?, kind?)` | R | no | in-memory job store only | n/a |

#### `start_backtest(mode, ...)`
- Input, `mode="data_files"`: `{"mode": "data_files", "files": [str], "start_timestamp"?: int, "end_timestamp"?: int, "enable_logs"?: bool, "source"?: str, "auto_stop"?: bool, "run_on_common_part_only"?: bool, "reset_tentacle_config"?: bool}` — maps to `start_backtesting` [V].
- Input, `mode="current_bot_data"`: `{"mode": "current_bot_data", "exchange_id": str, "exchange_type": str, "profile_id": str, "name"?: str, "data_source": str, "start_timestamp"?: int, "end_timestamp"?: int, "enable_logs"?: bool, "source"?: str, "auto_stop"?: bool, "reset_tentacle_config"?: bool}` — maps to `start_backtesting_with_current_bot_data`. The route and the fact that these are the accepted field **names** is [V]; the valid **values**/format for `exchange_id`, `exchange_type`, and `data_source` are **[UNVERIFIED]** — see requirements doc open question #6. Do not implement against assumed values; confirm against source or the live instance first (blocking task before tasklist step 8).
- Output: `{"job_id": str, "state": "queued"|"running"}` — returned before the backtest necessarily finishes; if the `backtest_execution_lock` (ADR-0002) is already held by another job, `state` is `"queued"`.
- Errors: `BACKTEST_START_FAILED` wrapping OctoBot's 500 response [V: failure path returns HTTP 500]; `MISSING_EXCHANGE_ID` for the `current_bot_data` mode's confirmed exception [V].

#### `compare_strategies(configs, name?)`
- Input: `{"configs": [<one of start_backtest's mode-specific input shapes, minus job semantics>, ...], "name"?: str}`, `len(configs) >= 2`.
- Behavior: creates one parent job (`kind: "compare_strategies"`) with N child job records; submits child 1 through the shared lock, awaits completion, submits child 2, etc. (ADR-0002 §4). If any child fails, remaining children are still attempted (one bad config shouldn't abort the whole comparison) — the failure is recorded per-child in the result and does not fail the parent job.
- Output: `{"job_id": str, "state": "queued"|"running", "sub_job_count": int}`.
- Combined progress formula (also used by `get_job_status`):
  `combined_progress = ((completed_children * 100) + current_child_progress) / total_children`
  where `current_child_progress` is 0 if no child is currently running (e.g. between children, or before the first starts).

#### `cancel_job(job_id)`
- For a `backtest` job in state `running`: calls `POST /backtesting?action_type=stop_backtesting`, sets state `cancelled` once OctoBot confirms.
- For a `backtest`/`compare_strategies` job in state `queued` (waiting on the lock): sets state `cancelled` directly, never submits to OctoBot.
- For a `compare_strategies` job with a currently-running child: stops that child (as above) and marks all not-yet-started children `cancelled`; parent state becomes `cancelled`.
- Idempotent: cancelling an already-terminal job returns the existing terminal state, not an error.

#### `get_job_status(job_id)`
- Output: `{"job_id": str, "kind": "backtest"|"compare_strategies", "state": "queued"|"running"|"completed"|"failed"|"cancelled", "progress_percent": float, "elapsed_seconds": float, "eta_seconds": float|null, "eta_confidence": "low"|"medium"|"high", "errors": [str], "sub_jobs"?: [{"job_id": str, "state": str, "progress_percent": float}]}` (the `sub_jobs` field present only for `compare_strategies` jobs).
- Errors: `JOB_NOT_FOUND` (ADR-0002 risk table) — distinct message noting the server may have restarted.

#### `get_job_result(job_id)`
- Requires `state == "completed"`; otherwise returns `JOB_NOT_COMPLETED` naming the current state (never partial results — a half-finished report is not returned even if OctoBot would technically serve one).
- For `kind: "backtest"`: `{"report": <passthrough from OctoBot's "report" key — NFR-8, do not rename/reshape>, "trades": <passthrough from OctoBot's "trades" key>}`.
- For `kind: "compare_strategies"`: `{"comparison": [{"config_label": str, "job_id": str, "report": <passthrough>, "trades": <passthrough>}], "diff": <a table diffing whatever numeric fields are common across all entries' "report" — field list intentionally not hardcoded here per NFR-8; implementation must derive it from what OctoBot actually returns, confirmed via the developer verification task below, not from an assumed field list such as "profitability"/"max_drawdown"/"win_rate">}`.

#### `list_jobs(state?, kind?)`
- Output: `{"jobs": [<abbreviated get_job_status shape, no sub_jobs>]}`, filterable.

---

### Portfolio & trading (all [V], real JSON API — `api/trading.py`)

| Tool | R/W | Confirm | Maps to |
|---|---|---|---|
| `get_orders()` | R | no | `GET /api/orders` |
| `cancel_order(order_id)` | W | no* | `POST /api/orders` |
| `get_positions()` | R | no | `GET /api/positions` |
| `close_position(position_id)` | W | no* | `POST /api/positions` |
| `get_trades()` | R | no | `GET /api/trades` |
| `get_pnl_history(...)` | R | no | `GET /api/pnl_history` |
| `get_historical_portfolio_value(...)` | R | no | `GET /api/historical_portfolio_value` |
| `refresh_portfolio()` | W | no | `POST /api/refresh_portfolio` |
| `clear_orders_history(confirm)` | W | **yes** | `POST /api/clear_orders_history` |
| `clear_trades_history(confirm)` | W | **yes** | `POST /api/clear_trades_history` |
| `clear_portfolio_history(confirm)` | W | **yes** | `POST /api/clear_portfolio_history` |
| `clear_transactions_history(confirm)` | W | **yes** | `POST /api/clear_transactions_history` |

\* Not gated per ADR-0003's scoping decision — flagged as revisitable with the user, since cancelling an order/closing a position is irreversible in effect even though it's routine trading activity.

---

### Exchanges (all [V])

| Tool | R/W | Confirm | Maps to |
|---|---|---|---|
| `get_currency_list(exchange?)` | R | no | `GET /api/currency_list` |
| `get_all_currencies(exchange)` | R | no | `GET /api/get_all_currencies/<exchange>` |
| `get_all_symbols(exchange)` | R | no | `GET /api/get_all_symbols/<exchange>` |
| `check_accounts_compatible(exchange_names)` | R | no | `POST /api/are_compatible_accounts` |
| `get_exchange_details(exchange_name?)` | R | no | `GET /api/first_exchange_details` — **note:** only returns one exchange's name/id, not a full list; document this limitation in the tool description itself so the agent doesn't assume it enumerates all configured exchanges |
| `update_exchange_credentials(exchange, api_key, api_secret, api_password?, confirm)` | W | **yes** | `POST /config` with `global_config` — **[UNVERIFIED] exact nested key path, open question #2** |

#### `update_exchange_credentials`
- Input: `{"exchange": str, "api_key": str, "api_secret": str, "api_password"?: str, "confirm": bool}`.
- Behavior: refuses without `confirm=true` (reason: "writes exchange API credentials; verify the exchange name and that you intend to grant this key trading access").
- Output: `{"exchange": str, "updated": true, "api_key_last4": str}` — **never** the full key/secret, per NFR-6, in output, logs, or error messages, even on failure (OctoBot's own error string must be scrubbed for the literal credential values before being included in any error response).
- Developer verification task (blocking, before implementation): capture the exact request body the OctoBot `/accounts` page's exchange-credentials form sends to `/config`, via the live instance's browser devtools network tab, since the source excerpt read this session did not reveal the nested `global_config` key path for exchange credentials.

---

### Trading config

| Tool | R/W | Confirm | Maps to |
|---|---|---|---|
| `update_trading_config(patch)` | W | no | `POST /config` (`TRADING_CONFIG_KEY` branch) [V] |

#### `update_trading_config(patch)`
- Input: `{"patch": object}` — the trading-mode tentacle-activation config
  payload OctoBot's own `/config` route expects under its `TRADING_CONFIG_KEY`
  request-body key [V: the route reads this key and calls
  `models.update_tentacles_activation_config(request_data[TRADING_CONFIG_KEY])`
  — the same underlying model function the `TENTACLES_CONFIG_KEY` and
  `EVALUATOR_CONFIG_KEY` branches also call, confirmed from the literal
  route body].
- Output: `{"trading_updated_config": <passthrough of OctoBot's own echoed
  value for this key>}` [V: matches the route's own response shape].
- Errors: `CONFIG_UPDATE_FAILED` with OctoBot's `err_message` string
  [V: the route's failure branch returns HTTP 500 with this message].
- Not confirm-gated (ADR-0003): mutates strategy/tentacle activation config;
  does not destroy data, expose credentials, or enable live trading.

---

### Tentacles & logs

| Tool | R/W | Confirm | Maps to | Tier |
|---|---|---|---|---|
| `get_tentacle_config(tentacle)` | R | no | `GET /config_tentacle` | [V] |
| `update_tentacle_config(tentacle, patch)` | W | no | `POST /config_tentacle` or `/config_tentacles` (batch) | [V] |
| `list_tentacles()` | R | no | n/a | Tier C — returns `{"available": false, "reason": "...", "alternative": "get_tentacle_config"}` |
| `get_logs(...)` | R | no | n/a | Tier C — returns `{"available": false, "reason": "...", "alternative": "export_logs"}` |
| `export_logs()` | R | no | `GET /export_logs` | [V] |

## Job store design

### Data model
```
JobRecord:
  job_id: str (uuid4)
  kind: "backtest" | "compare_strategies"
  state: "queued" | "running" | "completed" | "failed" | "cancelled"
  created_at, started_at, finished_at: float (monotonic + wall-clock pair)
  progress_percent: float  # 0-100
  samples: deque[(elapsed_seconds: float, progress_percent: float)]  # bounded, see ETA section
  eta_seconds: float | None
  eta_confidence: "low" | "medium" | "high"
  errors: list[str]
  result: dict | None           # set once, at completion
  child_job_ids: list[str] | None   # compare_strategies only, ordered
  cancel_requested: bool
  octobot_run_source: str | None    # OctoBot's own "source" identifier for this run
```

### Concurrency
- One process-wide `asyncio.Lock` (`backtest_execution_lock`), reflecting
  OctoBot's real one-run-at-a-time constraint (ADR-0002). Every backtest
  submission (`start_backtest`, and each child of `compare_strategies`)
  acquires it for the full "submit → watch until finished/failed/cancelled"
  span, then releases.
- A job is `state="queued"` while waiting on the lock, `state="running"` once
  acquired and OctoBot confirms the run started.

### Background watcher (per running job)
1. On acquiring the lock: `POST` the appropriate start action; on 200,
   record `octobot_run_source`; on error, set `state="failed"`, release lock,
   return.
2. Connect (or reuse a long-lived) Socket.IO async client to `/backtesting`.
3. Loop every 1.5s (NFR-2 constant) until `status == "finished"`,
   `cancel_requested`, or a hard error: emit `backtesting_status`, await the
   `backtesting_status` response, append `(elapsed_seconds, progress)` to
   `samples` (deduped — only append if `progress` changed from the last
   sample), update `eta_seconds`/`eta_confidence` (see below), update
   `errors` from the payload's `errors` field.
4. On `status == "finished"`: `GET /backtesting?update_type=backtesting_report`,
   store into `result`, set `state="completed"`, release lock.
5. On `cancel_requested`: `POST stop_backtesting`, set `state="cancelled"`,
   release lock, do not fetch a report.
6. Any unhandled exception in the loop: set `state="failed"`, append the
   exception message to `errors`, release lock (the lock release MUST happen
   in a `finally`, so a watcher crash never deadlocks subsequent jobs — this
   is an explicit invariant, see INV-1 below).

### Retention (NFR-9)
- On every `start_backtest`/`compare_strategies` call, before creating the
  new record: evict terminal (`completed`/`failed`/`cancelled`) records older
  than `JOB_TTL_SECONDS` (default 3600), then evict the oldest terminal
  records beyond `JOB_RETENTION_CAP` (default 50) if still over cap.
  Non-terminal jobs are never evicted.

## ETA estimation algorithm
OctoBot exposes no ETA (confirmed, ADR-0002); this is entirely an
MCP-server-authored heuristic, and is documented as such in code comments
and in tool descriptions (so the agent doesn't attribute false precision to
OctoBot).

1. Maintain `samples` as a deque bounded to the **last 20 entries or the
   last 60 seconds of elapsed time, whichever is fewer**, appended only when
   `progress` changes (dedup avoids overweighting periods where OctoBot
   hasn't updated progress yet).
2. **Staleness check (mid-run stall detection) — evaluated first, on every
   watcher tick, before any of steps 3–6 below.** Let
   `staleness = current_wall_clock_elapsed - samples[-1].elapsed` (undefined/
   skip this check if `samples` is empty). If
   `staleness > STALENESS_MULTIPLE * WATCHER_INTERVAL_SECONDS`
   (`STALENESS_MULTIPLE = 10`, i.e. 15s at the default 1.5s watcher
   interval — a constant kept separate from the watcher interval itself so
   the two can be tuned independently), force `eta_seconds = None` and
   `eta_confidence = "low"` regardless of what steps 3–6 would otherwise
   compute, and skip directly to step 7. This is what catches a mid-run
   stall (e.g. progress stuck at 45% for many minutes) that step 1's
   dedup alone does **not** catch: dedup only stops *new* samples from
   being recorded, it does not itself invalidate an already-confident ETA
   computed from older samples once time has moved on without progress
   moving with it.
3. If not stale, and `len(samples) < 2`: `eta_seconds = None`,
   `eta_confidence = "low"`.
4. Else, linear rate from the *window* (not the whole run, to stay responsive
   to slowdowns):
   `rate = (samples[-1].progress - samples[0].progress) / (samples[-1].elapsed - samples[0].elapsed)`
   (guard: if the denominator is 0, treat as insufficient data → step 3's
   result).
5. If `rate <= 0` (progress stalled or went backward — shouldn't happen, but
   guard it): `eta_seconds = None`, `eta_confidence = "low"`.
6. Else: `eta_seconds = max(0, (100 - samples[-1].progress) / rate)`, with
   confidence:
   - `"low"`: fewer than 3 samples, or samples span < 5s, or current
     progress < 2%.
   - `"medium"`: 3–9 samples spanning ≥5s.
   - `"high"`: ≥10 samples spanning ≥30s.
7. For `compare_strategies`, combined ETA (the per-child staleness check in
   step 2 applies before this aggregation runs, so a stalled child's own
   `eta_seconds` is already `None` by the time it's aggregated):
   - If ≥1 child has completed: `avg_child_duration = mean(finished_at - started_at for completed children)`; `combined_eta = current_child_eta_or_avg_remaining + avg_child_duration * children_not_yet_started`, where `current_child_eta_or_avg_remaining` is the running child's own `eta_seconds` if available, else `avg_child_duration`.
   - If 0 children have completed yet: `combined_eta = current_child_eta_seconds` if available, else `None` with `"low"` confidence (do not fabricate a multi-child estimate from zero completed runs — this directly implements NFR-3's "no fabricated ETA" requirement at the aggregate level too).

## Invariants
- INV-1: `backtest_execution_lock` is released in a `finally` block on every
  code path through the watcher — a watcher exception can never leave
  subsequent jobs permanently queued.
- INV-2: `eta_seconds` is `None` whenever `eta_confidence == "low"` AND
  (fewer than 2 samples exist, OR the staleness check has triggered) —
  never a numeric value with no basis, and never a stale numeric value left
  over from before a mid-run stall.
- INV-3: A job's `result` is set exactly once, only on the transition into
  `state="completed"`, and is immutable thereafter.
- INV-4: No tool ever includes a full exchange API key/secret value in its
  return payload or in any log line (NFR-6) — enforced via a single
  redaction helper used at the boundary where OctoBot's response is read,
  not left to each call site to remember.
- INV-5: The Tier-B scrape adapter either returns a fully-populated profile
  list or raises `SCRAPE_CONTRACT_BROKEN` — it never returns a partial or
  empty list that looks like a valid "no profiles" state.

## Data model (cross-cutting)
- `profile_id`: opaque string, OctoBot-assigned, sole identifier for profile
  tools.
- `job_id`: server-assigned UUIDv4, opaque to OctoBot.
- Monetary/percentage values: passthrough from OctoBot, no unit conversion.

## Observability
- Structured log line per tool invocation: `{tool_name, args (redacted per
  INV-4), duration_ms, outcome: "ok"|"error", error_class?}`. No secret
  values, ever, per NFR-6/INV-4.
- Log line per job state transition: `{job_id, kind, from_state, to_state,
  progress_percent}`.
- Log line per watcher loop error (not just terminal failure): so a
  transient Socket.IO hiccup is visible even if the job ultimately recovers.

## Security & privacy
- Data classification: exchange API key/secret is the only sensitive data
  class. It is redacted (last-4-chars-only) in all outputs and logs (NFR-6,
  INV-4), and never written to disk by this server (OctoBot itself persists
  it; this server is a stateless-per-call proxy for that one write).
- AuthZ: none at the MCP layer (ADR-0001) — the OS process boundary is the
  control. Do not add a false sense of security by, e.g., partially checking
  `confirm` flags only sometimes.
- Auditable actions: every gated (confirm-required) tool call, and every
  `update_exchange_credentials` call regardless of outcome, is logged at
  the structured-log level described above, redacted per NFR-6.

## Open questions
See requirements doc's "Open questions" section (1–6) — restated here as
blocking implementation tasks for the specific tools they affect:
`list_profiles`/`get_profile` (Q1), `update_exchange_credentials` (Q2),
`get_job_result`/`compare_strategies` diff table (Q3), the backtest watcher
(Q4), the `convert_profile_to_live`/confirm-gating completeness (Q5), and
`start_backtest`'s `current_bot_data` mode field values (Q6).

## Hand-off
- Traced requirements: FR-1..FR-20, NFR-1..NFR-12 (requirements doc).
- Implementer: `developer` agent, sequenced per `docs/tasklist.md`.
- Test coverage expectations: every ETA branch (unit, synthetic sequences),
  every confirm-gated tool (unit, with/without flag), the watcher's lock
  release on exception (unit, forced exception), the job store TTL/cap
  eviction (unit), and one end-to-end smoke test per milestone against the
  live instance where the live instance is reachable from the developer's
  environment.
