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
- **Does NOT enable real-money trading** — confirmed against source, resolving requirements doc open question #5. `is_real_trading(profile)` checks `config.trader.enabled` (`octobot_trading/util/config_util.py::is_trader_enabled`), a flag this tool's underlying `convert_to_live_profile`/`select_profile` calls never touch — they only set `profile.profile_type` and select the profile. No tool in this project's scope can set `config.trader.enabled` on an ordinary OctoBot instance (the one route found that does, `_save_distribution_user_config`, is registered only for the non-default `PREDICTION_MARKET` OctoBot distribution — unrelated to OctoBot's onboarding wizard — confirmed against `controllers/__init__.py::register()`).
- Same confirm pattern as `delete_profile`, reason string: "This relabels the profile as OctoBot's internal LIVE type and selects it — but does NOT itself enable real-money trading (confirmed against source: OctoBot's actual real-vs-simulated switch, config.trader.enabled, is a separate flag this call never touches). Still gated because it persistently changes the profile and its selection, and could otherwise mislead a caller into believing real trading is now active when it is not."
- Output: `{"profile_id": str, "profile_type": "LIVE", "selected": true}` — `profile_type` here is an echo of the internal label just set, not an independently re-verified fact (no Tier A/B signal exists to confirm it).

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
| `update_evaluator_config(patch, deactivate_others?)` | W | no | `POST /config` (`EVALUATOR_CONFIG_KEY` branch) [V] |
| `list_evaluators()` | R | no | Tier B: `GET /advanced/evaluator_config` [V] |

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
  [V: the route's failure branch returns HTTP 500 with this message]. Note:
  `update_trading_config`'s own implementation found this claim is never
  actually true for that tool's request shape (OctoBot's `err_message` stays
  an untouched empty string for the `trading_config`/`tentacle_config`/
  `evaluator_config` branches — only the `global_config`/`removed_elements`
  branches ever populate it). The same is expected to hold here; confirm
  against source/live before asserting it as fact in this tool's own
  docstring, per this project's own "verify what you actually implement"
  discipline (don't just copy the parent tool's finding without checking).
- Not confirm-gated (ADR-0003): mutates strategy/tentacle activation config;
  does not destroy data, expose credentials, or enable live trading.

#### `update_evaluator_config(patch, deactivate_others=False)`
- **Addendum, added after v1 shipped** (closing a gap surfaced while
  answering a user question about evaluator CRUD — not one of the original
  14 milestones or the 13.5/13.6/15 addenda). Reuses `update_trading_config`'s
  own already-shipped, already-judge-verified route/pattern almost exactly:
  same `POST /config` route, same underlying `models.
  update_tentacles_activation_config(...)` function (confirmed in
  `update_trading_config`'s own docstring/verification), different top-level
  request-body key.
- Input: `{"patch": object, "deactivate_others"?: bool}` — `patch` is the
  evaluator-activation config payload (a flat map of evaluator class name →
  enabled boolean, e.g. `{"RSIMomentumEvaluator": true, "BBMomentumEvaluator":
  false}`; confirmed against the live instance's `/advanced/evaluator_config`
  page's rendered form: each evaluator's toggle carries literal
  `config-type="evaluator_config" config-key=<EvaluatorClassName>` attributes,
  the same `_save_config()` JS mechanism already confirmed for
  `update_trading_config`/`update_exchange_credentials`, so
  `{"evaluator_config": patch}` is the request body sent under `POST
  /config`). Submitted under OctoBot's `EVALUATOR_CONFIG_KEY` request-body
  key — **[UNVERIFIED] exact literal string value**: confirmed by name in
  `update_trading_config`'s own source read (`constants.EVALUATOR_CONFIG_KEY`
  exists and is read by the same route), but its literal string value (almost
  certainly `"evaluator_config"`, matching the rendered form's `config-type`
  attribute and this project's established `snake_case`-matches-attribute
  convention for `TRADING_CONFIG_KEY`/`GLOBAL_CONFIG_KEY`) was not read
  directly from `constants.py` this session — **developer verification task:
  confirm the literal constant value before finalizing**, don't assume it
  matches the rendered attribute name by convention alone.
- `deactivate_others`: **[UNVERIFIED] exact semantics** — `update_trading_config`'s
  own docstring notes the `EVALUATOR_CONFIG_KEY` branch calls
  `update_tentacles_activation_config` "with an extra `deactivate_others`
  argument neither other branch passes," confirming the PARAMETER exists,
  but this session did not read `update_tentacles_activation_config`'s own
  body to confirm what `deactivate_others=True` actually does (plausibly:
  disable every evaluator not named in `patch`, vs. leaving unnamed
  evaluators at their current state when `False` — but this is an inference
  from the name, not a confirmed reading). **Developer verification task
  (blocking before finalizing the tool's own documented behavior)**: read
  `models/configuration.py::update_tentacles_activation_config`'s body to
  confirm this before describing it as fact in the tool's docstring; default
  `deactivate_others` to `False` (the conservative, narrower-blast-radius
  option) regardless of what's found.
- Output: `{"evaluator_updated_config": <passthrough of OctoBot's own echoed
  value for this key>}` — by direct analogy to `update_trading_config`'s own
  confirmed `{"trading_updated_config": ...}` shape (same response envelope,
  different key extracted) [V: envelope shape already confirmed in
  `update_trading_config`'s own verification — this tool extracts a
  different one of its keys].
- Errors: same `CONFIG_UPDATE_FAILED`-style pattern as `update_trading_config`
  — expect the same "OctoBot's `err_message` stays an untouched empty
  string" finding to hold (the `evaluator_config` branch is explicitly named
  among the branches that never touch `err_message`, per
  `update_trading_config`'s own verification), but confirm this
  independently rather than assuming it transfers unchanged.
- Not confirm-gated (ADR-0003): same rationale as `update_trading_config` —
  mutates which evaluators are active, does not destroy data, expose
  credentials, or enable live trading.

#### `list_evaluators()`
- **Addendum, ADR-0009** — a third Tier-B HTML-scrape read adapter,
  originally recommended NO-GO (evaluator names are OctoBot's own public
  tentacle catalog, discoverable without this server), then reversed to GO
  by explicit user decision. Closes `update_evaluator_config`'s own
  companion-read gap: that tool is unusable without already knowing an
  evaluator's exact class name and current state.
- Maps to `GET /advanced/evaluator_config` [V: fetched live this session,
  OctoBot 2.1.1, HTTP 200]. Each evaluator renders as an `a.config-element`
  element: `config-type="evaluator_config" config-key=<Name>
  current-value=<Bool> config-value=<Bool> startup-config-value=<Bool>`,
  inside one of four category containers, each a stable `id`: `
  ta-evaluator-config-root` ("Technical analysis"),
  `social-evaluator-config-root` ("Social analysis"),
  `rt-evaluator-config-root` ("Real time analysis"),
  `scripted-evaluator-config-root` ("Scripted evaluators") [V].
- Output: `{"evaluators": [{"name": str, "enabled": bool, "type": str}]}`.
  `name` = the `config-key` attribute value (the same string
  `update_evaluator_config`'s `patch` keys must equal — the closed loop
  between these two tools is structural, not a convention to maintain by
  hand). `enabled` = the `current-value` attribute parsed as a bool.
  `type` ∈ `{"technical_analysis", "social", "realtime", "scripted"}`,
  taken from whichever container an evaluator's row was found inside —
  always populated (confirmed rendered for every evaluator, never omitted).
- Errors: `EvaluatorScrapeContractBrokenError` (Tier-B fail-loud, NFR-18) —
  raised only if zero `config-element` rows are found across ALL FOUR
  containers combined, or an expected container `id` is missing from the
  page entirely. A single category legitimately having zero evaluators is
  NOT an error [V: confirmed live — this project's own test instance has
  zero "Scripted evaluators" installed, rendering an intact but empty
  container]. Do not reuse `list_profiles`'s "zero is always broken" gate
  model here — it does not fit this page.
- Read-only, not confirm-gated.

---

### Portfolio snapshot & evaluation matrix (addendum — ADR-0011; genuinely new scope, found via a live UI audit, not in v1's original inventory)

| Tool | R/W | Confirm | Maps to | Tier |
|---|---|---|---|---|
| `get_portfolio_snapshot()` | R | no | scrape `GET /portfolio` (bare) | Tier B — [V-live, this session] |
| `get_evaluation_matrix()` | R | no | scrape `GET /advanced/matrix` (bare) | Tier B — [V-live, this session] |

Both confirmed, this session, to have **no JSON API**: `GET /api/portfolio`,
`/api/portfolios`, `/api/portfolio_assets`, `/portfolio?format=json` (for the
first) and `GET /api/matrix`, `/api/evaluation_matrix`,
`/advanced/matrix_data`, `/advanced/matrix?format=json` (for the second) were
each fetched directly from the live instance's own page context — every one
returned OctoBot's HTML 404-fallback page or the unchanged bare-page HTML,
never JSON (ADR-0011, Context). Both pages' own client-side JS was also
inspected via chrome-devtools `list_network_requests`/the pages' own fetched
`.js` files (`portfolio.js`/`advanced_matrix.js`/`dom_updater.js`) to confirm
neither is populated by a Socket.IO push — the asset table and the matrix
table are both embedded directly in their page's initial server-rendered
HTML, re-fetched as a whole bare page on refresh (ADR-0011 rules out a
Tier-C/socket-listener design for the matrix with direct evidence, not
inference from the `export_logs`-grepping workaround this gap was originally
found through).

#### `get_portfolio_snapshot()`
- Input: `{}`.
- Maps to `GET /portfolio` (bare — the same route a human visits; no
  separate fragment/JSON route exists, confirmed [V-live]). Scrapes
  `#portfoliosCard`'s `reference_market` attribute and every
  `tr.symbol-holding` row inside `#holdings-table`/`#portfolio-display`.
- Output: `{"reference_currency": str, "assets": [{"asset": str, "total":
  {"rounded": float, "exact": float|null, "per_exchange":
  [{"exchange": str, "amount": float}]|null}, "value_in_reference_currency":
  {"rounded": float}, "available": <same shape as "total">,
  "locked_in_orders": <same shape as "total">}]}`.
  - `rounded` is always populated (the displayed cell text, parsed as a
    float) [V-live].
  - `exact`/`per_exchange` for `total`/`available`/`locked_in_orders` are
    populated **only** when that cell's `title` tooltip attribute parses
    cleanly as one or more `"<Exchange>: <amount>"` pairs (confirmed [V-live]
    for the single-exchange case: `title="Binance: 195.90814133"` against a
    displayed, rounded `"195.908"`) — `null` otherwise, never a guessed
    split. **[UNVERIFIED] multi-exchange separator format** — requirements
    doc open question #15; do not implement a specific separator-splitting
    rule beyond the single-pair case without confirming it against a real
    multi-exchange account first.
  - `value_in_reference_currency` carries **only** `rounded` — confirmed
    [V-live] that this column's `<td class="total-value ...">` has no
    `title`/tooltip attribute at all on this OctoBot version, so there is no
    full-precision source for it via this route. Do not add an `exact` key
    for this field; its absence is the documented, correct behavior, not an
    oversight.
- Errors: `PortfolioScrapeContractBrokenError` if `#holdings-table`/
  `#portfolio-display` is missing, a row is missing its `.symbol` span, or
  `#portfoliosCard`'s `reference_market` attribute is missing. **Never**
  raised for zero `assets` (a legitimately empty portfolio) — mirrors
  `list_profiles`/`list_data_files`/`list_evaluators`'s INV-5-style
  discipline, but with a different "what's legitimately empty" boundary (see
  ADR-0011 Decision 3).
- Idempotent, read-only, not confirm-gated.

#### `get_evaluation_matrix()`
- Input: `{}`.
- Maps to `GET /advanced/matrix` (bare — same route a human visits; no
  separate JSON route, no Socket.IO push into this table after initial load,
  both confirmed [V-live], see this section's intro). Scrapes
  `#matrixDataTable`'s `<thead>` (asserting column-name text matches exactly)
  and every body `<tr>`'s 5 plain, unattributed `<td>` cells, positionally.
- Output: `{"entries": [{"evaluator": str, "value": float|null|str,
  "time_frame": str|null, "symbol": str, "exchange": str}]}`, one entry per
  scraped row, in the document order OctoBot renders them.
  - `value`: the cell text parsed as a JSON number when numeric; `null` when
    the cell text is exactly the literal string `"None"` (confirmed
    [V-live] real case: `InstantFluctuationsEvaluator`'s `1m` row, not yet
    evaluated at scrape time); for any other non-numeric, non-`"None"` text
    (not observed live, but not ruled out), pass through the raw string
    rather than drop the row or raise — same "never invent, never silently
    drop" discipline as NFR-8.
  - `time_frame`: the cell text, or `null` when the cell is empty (confirmed
    [V-live] real case: `MoveSignalsStrategyEvaluator`'s row — a
    strategy-level evaluator has no single timeframe of its own; this is a
    legitimate OctoBot state, not a broken scrape).
  - `symbol`/`exchange`: the cell text verbatim (confirmed [V-live]:
    `"BTC/EUR"`/`"binance"`).
- Errors: `EvaluationMatrixScrapeContractBrokenError` if `#matrixDataTable`
  is missing, its `<thead>` column-name text doesn't match
  `["Evaluator", "Value", "Time frame", "Symbol", "Exchange"]` exactly and in
  order, or any body row has a cell count other than 5. **Never** raised for
  zero `entries` (a freshly started OctoBot, or one with no active
  evaluators, legitimately shows nothing yet) — a materially weaker "what's
  legitimately empty" invariant than every prior Tier-B adapter's, since
  there is no "OctoBot always ships at least one" guarantee here (ADR-0011
  Decision 3).
- **Known structural weakness, stated plainly (ADR-0011 Consequences):**
  unlike every other Tier-B adapter in this project, this page's cells carry
  no stable per-row/per-cell attribute to anchor on — extraction is
  positional, guarded only by the `<thead>` text-match assertion above. A
  future OctoBot version that reorders columns without also renaming their
  headers would slip through undetected. No stronger signal exists on this
  page to build on; do not add a false sense of robustness beyond the
  header-text check.
- **[UNVERIFIED]** whether this table is ever server-side paginated at a
  scale larger than the 8 real rows observed live (requirements doc open
  question #16) — not blocking for implementation, but the tool's own
  docstring should not claim completeness beyond what was actually verified.
- Idempotent, read-only, not confirm-gated.

---

### Tentacles & logs

| Tool | R/W | Confirm | Maps to | Tier |
|---|---|---|---|---|
| `get_tentacle_config(tentacle)` | R | no | `GET /config_tentacle` | [V] |
| `update_tentacle_config(tentacle, patch)` | W | no | `POST /config_tentacle` or `/config_tentacles` (batch) | [V] |
| `list_tentacles()` | R | no | n/a | Tier C — returns `{"available": false, "reason": "...", "alternative": "get_tentacle_config"}` |
| `get_logs(...)` | R | no | n/a | Tier C — returns `{"available": false, "reason": "...", "alternative": "export_logs"}` |
| `export_logs()` | R | no | `GET /export_logs` | [V] |

---

### Historical data collection (addendum — ADR-0007/ADR-0008; genuinely new scope, not in v1's original inventory)

| Tool | R/W | Confirm | Maps to | Tier |
|---|---|---|---|---|
| `start_data_collection(exchange, symbols, time_frames?, start_timestamp?, end_timestamp?)` | W | no | `POST /data_collector?action_type=start_collector` | [V] (route/body/one-at-a-time guard); [UNVERIFIED] timestamp units, Q8 |
| `get_job_status(job_id)` / `list_jobs(state?, kind?)` / `cancel_job(job_id)` / `get_job_result(job_id)` | R/W | no | **reused unchanged** for `kind: "data_collection"` (see "Job store extension" below) | n/a |
| `list_data_files()` | R | no | scrape `GET /data_collector` (bare) | Tier B — [UNVERIFIED live DOM shape, Q7] |
| `delete_data_file(file, confirm)` | W | **yes** | `POST /data_collector?action_type=delete_data_file` | [V] |
| `import_data_file(file_base64, filename)` | W | no | `POST /data_collector?action_type=import_data_file` (multipart) | [V] |
| `get_available_symbols_for_collection(exchange)` | R | no | `GET /data_collector?action_type=symbol_list&exchange=<name>` | [V] |
| `get_available_timeframes_for_collection(exchange)` | R | no | `GET /data_collector?action_type=available_timeframes_list&exchange=<name>` | [V] |

No dedicated `stop_data_collection` tool exists — `cancel_job(job_id)` is
reused as-is (ADR-0007 decision point 2), the same way there is no
dedicated `stop_backtest` tool today.

**Confirm-gating decisions, applying ADR-0003's existing framework
explicitly (per this addendum's own instruction to make the call, not
leave it implicit):**
- `start_data_collection`: **not gated.** Starts a simulation-adjacent
  background download, never touches live trading, destroys no data, and
  exposes no credentials — same reasoning as `start_backtest`'s own
  not-gated classification.
- `import_data_file`: **not gated.** Creates new state (a saved file);
  destroys nothing.
- `delete_data_file`: **gated, `confirm=true` required (reversed from this
  addendum's original draft call).** The architect's initial classification
  (not gated, reasoning: a re-collectable local cache, not trading
  history/credentials/curated config) was reviewed and the user, persuaded
  by the reviewer's counter-argument, explicitly overrode it. The
  controlling reasoning, recorded here for traceability: ADR-0003's actual
  purpose is protecting against an ambiguous/mistaken batch instruction
  before a human notices — and unlike `cancel_order`/`close_position`
  (instantly correctable: re-place the order, the position re-opens on the
  next signal), an erroneous `delete_data_file` call means re-running a
  collection that can take minutes to hours (per this addendum's own
  `start_data_collection`). That asymmetry — cheap-to-undo vs.
  expensive-to-redo — places `delete_data_file` closer to
  `clear_*_history`'s risk profile than to `cancel_order`/`close_position`'s,
  even though the *data itself* is technically re-derivable rather than
  permanently lost. Implemented via the same shared `require_confirmation`
  helper every other gated tool uses (NFR-7), never bespoke per-tool logic.
- `list_data_files`/`get_available_symbols_for_collection`/
  `get_available_timeframes_for_collection`: read-only, not gated (no
  gating question applies).

#### `start_data_collection(exchange, symbols, time_frames?, start_timestamp?, end_timestamp?)`
- Input: `{"exchange": str, "symbols": [str], "time_frames"?: [str],
  "start_timestamp"?: int, "end_timestamp"?: int}` — maps 1:1 to
  `collect_data_file(exchange, symbols, time_frames=None, start_timestamp=None,
  end_timestamp=None)` [V]. `time_frames`/`start_timestamp`/`end_timestamp`
  are all optional (OctoBot's own defaults, confirmed from the function
  signature). **Timestamp units are [UNVERIFIED]** — see requirements doc
  Q8; implement assuming milliseconds (this project's existing convention)
  until a developer confirms otherwise.
- Behavior (ADR-0007): creates a `kind: "data_collection"` job, acquires
  `JobStore.data_collection_execution_lock` (a lock independent of
  `backtest_execution_lock` — ADR-0007 decision point 3/NFR-15), POSTs the
  start action, and — on success — spawns a background watcher connecting
  to OctoBot's `/data_collector` Socket.IO namespace exactly as
  `start_backtest`'s watcher does for `/backtesting` (same non-ack
  emit/await-response bridge, same `WATCHER_INTERVAL_SECONDS` tick
  interval, same INV-1 lock-released-in-`finally` guarantee, same
  `queued→cancelled` race handling).
- Progress conversion (ADR-0007 decision point 4): each tick's
  `{"status": ..., "progress": {"current_step": int, "total_steps": int,
  "current_step_percent": int}}` payload [V] is converted to the flat
  `progress_percent` `JobStore.record_watcher_tick` expects via
  `0.0 if total_steps <= 0 else clamp(((current_step - 1) * 100 +
  current_step_percent) / total_steps, 0, 100)` — **the 1-indexing
  assumption on `current_step` is [UNVERIFIED]**, see requirements doc Q9;
  `error_count` is always passed `0` (no equivalent field exists in this
  payload, confirmed from source).
- Output: `{"job_id": str, "state": "queued"}` — same shape/timing
  guarantee as `start_backtest`'s output.
- Supports `progressToken` (ADR-0002 decision point 3 / ADR-0007 decision
  point 4), reusing `_extract_progress_token`/`_build_progress_notifier`/
  `_attach_progress_notifier` — **these should be moved from
  `octobot_mcp.tools.backtesting` to `octobot_mcp.tools._shared`** as part
  of this milestone (this module becomes their second consumer; see
  `_shared.py`'s own docstring precedent for `_parse_content_disposition_filename`).
- Errors: `DataCollectionStartFailedError` wrapping OctoBot's own `reply`
  text on a failed start (confirmed failure messages include "Backtesting
  is disabled.", "Please select an exchange.", "Please select a trading
  pair.", `_ensure_backtesting_limits`'s own message [UNVERIFIED text,
  Q10], and "Can't collect data for {symbols} on {exchange} (Historical
  data collector is already running)" for OctoBot's own one-at-a-time
  guard).

#### Job store extension for `kind: "data_collection"` (ADR-0007)
- `octobot_mcp.jobs.JobKind` widens to `Literal["backtest",
  "compare_strategies", "data_collection"]`. **No other change to
  `JobStore`'s methods is required** — `create_job`/`start`/`complete`/
  `fail`/`cancel`/`confirm_cancelled`/`record_watcher_tick`/`append_error`/
  `track_watcher_task`/`set_progress_notifier` are already fully
  kind-agnostic (verified this session by re-reading `jobs.py` in full —
  see ADR-0007's Context section for the exact proof). `JobStore` gains one
  new field, `data_collection_execution_lock: asyncio.Lock`, initialized
  the same way `backtest_execution_lock` already is.
- `octobot_mcp.tools.jobs` (`get_job_status`/`list_jobs`/`cancel_job`)
  **requires no code changes at all** — `_job_status_payload`'s only
  kind-specific branch already treats any non-`compare_strategies` kind
  generically (confirmed this session).
- `octobot_mcp.tools.backtesting.get_job_result` gains **one new branch**:
  `elif job.kind == "data_collection": return job.result`. The stored
  `result` (set once at `state="completed"`, INV-3 unchanged) is:
  `{"exchange": str, "symbols": [str], "time_frames": [str]|null,
  "start_timestamp": int|null, "end_timestamp": int|null, "message": str}`
  — deliberately does not include a resulting filename (OctoBot itself
  names none anywhere in this flow, confirmed from source); the tool
  description instructs the agent to call `list_data_files()` afterward.
  Consider relocating `get_job_result` to a kind-dispatching shared
  location if a fourth job kind is ever added — not necessary at two/three
  kinds.

#### `list_data_files()`
- Input: `{}`.
- Output: `{"files": [{"file": str, "exchange": str, "symbols": [str],
  "time_frames": [str], "is_full": bool, "start_date": str|null,
  "end_date": str|null, "date": str|null, "candles_length": int|null}]}`
  — see ADR-0008 for the exact scrape target (`tr.selectable_datafile`) and
  field-to-markup mapping.
- Errors: `DataFileScrapeContractBrokenError` (NFR-13) if the page
  structure doesn't match expectations — never returns an empty list
  silently in that case (mirrors `list_profiles`'s own INV-5-style
  discipline exactly, via a distinct exception class per ADR-0008 decision
  point 3).
- Idempotent, read-only. The `file` field is the authoritative value to
  pass to `delete_data_file`.

#### `delete_data_file(file, confirm=false)`
- Input: `{"file": str, "confirm": bool}` — `file` is whatever
  `list_data_files` returned in its `file` field, passed through verbatim
  as the request body once confirmed (confirmed from source:
  `flask.request.get_json()`'s raw value is passed straight to
  `backtesting_api.delete_data_file(file_name)`, no wrapping object).
- Behavior: if `confirm != true` →
  `require_confirmation("delete_data_file", file, "This permanently deletes
  the historical data file; re-collecting it via start_data_collection can
  take minutes to hours depending on the date range.")`, no OctoBot call
  made — same shared-helper pattern as `delete_profile`/`clear_orders_history`
  (NFR-7). If `confirm == true` → proceed with the delete request.
- Output on success: `{"file": str, "deleted": true, "message": str}`.
- Errors: `DataFileDeleteRejectedError` wrapping OctoBot's own `f"Can't
  delete {file_name} ({error})"` text [V].
- **Gated: requires `confirm=true`** — see the framework discussion above
  (reversed from this addendum's original not-gated draft; user-confirmed
  decision, applying ADR-0003's actual purpose per the reviewer's
  argument).

#### `import_data_file(file_base64, filename)`
- Input: `{"file_base64": str, "filename": str}` — mirrors
  `create_profile(mode="import_file")`'s existing multipart-upload pattern
  exactly (base64-decode, then a multipart `file` field named `filename`,
  matching OctoBot's own `werkzeug.utils.secure_filename(...)` handling
  [V]).
- Output: `{"filename": str, "imported": true, "message": str}` (OctoBot's
  own success message from `models.save_data_file`, confirmed [V]).
- Errors: `DataFileImportFailedError` wrapping OctoBot's own `f"Error when
  saving file: {e}. File can't be saved."` text [V].
- Not confirm-gated — creates new state, destroys nothing.

#### `get_available_symbols_for_collection(exchange)` / `get_available_timeframes_for_collection(exchange)`
- Input: `{"exchange": str}` for each.
- Output: `{"symbols": [str]}` / `{"time_frames": [str]}` respectively —
  direct passthrough of OctoBot's own sorted JSON arrays [V].
- Errors: generic `OctoBotClientError`/`OctoBotServerError` (no dedicated
  exception class — these are plain reads with no domain-specific failure
  mode, matching this project's existing convention for read-only tools).
- **Developer note (non-blocking):** `get_available_symbols_for_collection`
  may overlap with the already-shipped `get_all_symbols(exchange)` tool
  (`GET /api/get_all_symbols/<exchange>`) — both ultimately load ccxt
  market data for the given exchange, but this was not confirmed to return
  identical data this session. Verify before implementation; if equivalent,
  prefer reusing `get_all_symbols` and drop this tool rather than shipping
  a duplicate.

---

### Accounts & profile global config (addendum — ADR-0012; genuinely new scope, found via a live UI audit, not in v1's original inventory)

All six tools in this section write through OctoBot's existing, already-used
`POST /config` route — the same route `update_exchange_credentials`/
`update_trading_config`/`update_evaluator_config` already call — never a new
route. The four read tools share one generic Tier-B scrape helper (new:
`_scrape_config_elements(html, container_selector)` in
`octobot_mcp.tools._config_scrape`), since every field on these pages
already self-describes via `config-key`/`config-type`/`config-value`/
`data-type` attributes — no per-field hardcoding required, unlike every
prior Tier-B adapter in this project.

| Tool | R/W | Confirm | Maps to | Tier |
|---|---|---|---|---|
| `get_exchanges()` | R | no | scrape `GET /accounts` (exchange-card deck) | Tier B — [V-live, this session] |
| `add_exchange(exchange, api_key, api_secret, api_password?, confirm)` | W | **yes** | `POST /config` (`global_config`) | [V] |
| `remove_exchange(exchange, confirm)` | W | **yes** | `POST /config` (`removed_elements`) | [V] |
| `update_exchange_trading_mode(exchange, exchange_type?, enabled?)` | W | no | `POST /config` (`global_config`) | [V]; `exchange_type`'s exact enum values [UNVERIFIED], open question #17 |
| `get_interfaces()` | R | no | scrape `GET /accounts` (`#panelServices`) | Tier B — [V-live for Telegram/Web; other services' field sets UNVERIFIED, open question #18] |
| `update_interface_config(service, patch, confirm)` | W | **conditional** (iff `patch` touches a secret-suffix field — NFR-22) | `POST /config` (`global_config`) | [V] mechanism; per-service field completeness as above |
| `get_notification_routing()` | R | no | scrape `GET /accounts` (`#panelNotifications`) | Tier B — [V-live, this session] |
| `update_notification_routing(patch)` | W | no | `POST /config` (`global_config`) | [V] |
| `get_profile_currencies()` | R | no | scrape `GET /profile` (`#panelCurrency`) | Tier B — [V-live, this session] |
| `update_profile_currencies(patch)` | W | no | `POST /config` (`global_config`) | [V] |

Also in this addendum, the real-trading toggle (higher-stakes, its own
restart semantics — listed separately below rather than in the table above
since it is the one tool in this ADR with a materially different risk
profile):

| Tool | R/W | Confirm | Maps to | Tier |
|---|---|---|---|---|
| `set_real_trading_enabled(enabled, confirm)` | W | **yes, both directions** | `POST /config` (`global_config` + `restart_after_save: true`) | [V] |

#### `set_real_trading_enabled(enabled, confirm=False)`
- Input: `{"enabled": bool, "confirm": bool}`.
- Behavior: if `confirm != true` → `require_confirmation("set_real_trading_enabled",
  "enabled" if enabled else "disabled", <reason>)`, no OctoBot call made.
  Reason text differs by direction (mirroring ADR-0010 Decision 3's
  `LIVE`/`SIMULATOR`-scaled phrasing for `restart_octobot`): enabling names
  both the real-money consequence AND the forced restart explicitly;
  disabling names the forced restart (lower-stakes direction, still gated
  per ADR-0012 Decision 4's "restart itself is disruptive" reasoning).
  Before the OctoBot call, checks `JobStore` for an active
  `backtest`/`data_collection` job (mirroring `restart_octobot`, ADR-0010)
  and refuses outright, naming the job(s), if one is running.
- Once confirmed, maps to `POST /config`, JSON body
  `{"global_config": {"trader_enabled": enabled, "trader-simulator_enabled":
  !enabled}, "restart_after_save": true}` — both keys always sent together
  [V: `navbar.js::trigger_trader_state`'s exact logic, confirmed against
  `trading_state_modal.html`'s template source]. `restart_after_save: true`
  is never omitted for this tool (unlike `update_exchange_credentials`,
  which never sets it) — confirmed [V] against the full `/config` route
  source that this unconditionally triggers
  `models.schedule_delayed_command(models.restart_bot)`, the same restart
  primitive `restart_octobot` wraps via a different route.
- Output on success: `{"real_trading_enabled": enabled, "restart_triggered":
  true, "message": "OctoBot restart scheduled; call wait_for_octobot_ready()
  next, then re-confirm via get_profile()/list_profiles() before trading
  decisions that depend on this flag."}` — the message explicitly reuses
  `restart_octobot`'s own established follow-up pattern (ADR-0010), since
  this tool triggers the identical kind of restart.
- Errors: `CONFIG_UPDATE_FAILED` on a non-2xx response, same pattern as
  `update_trading_config`.
- **Does NOT need `convert_profile_to_live` to be called first or after** —
  confirmed independent: `config.trader.enabled` and `profile.profile_type`
  are two unrelated OctoBot concepts (per `convert_profile_to_live`'s own
  existing docstring); this tool is the one this project has been missing
  since requirements doc open question #5 was first raised.

#### `add_exchange(exchange, api_key, api_secret, api_password=None, confirm=False)`
- Input: identical shape to `update_exchange_credentials`'s existing input.
- Behavior: refuses without `confirm=true` (reason: "adds a new exchange and
  grants this key trading access; verify the exchange name"); refuses
  locally (no OctoBot call), same as `update_exchange_credentials`, if
  `exchange` contains an underscore (shared helper, not duplicated logic).
- Once confirmed, maps to `POST /config`, JSON body
  `{"global_config": {"exchanges_<exchange>_api-key": api_key,
  "exchanges_<exchange>_api-secret": api_secret[, "exchanges_<exchange>_
  api-password": api_password], "exchanges_<exchange>_enabled": true}}` —
  the one difference from `update_exchange_credentials`'s existing body:
  the explicit `_enabled: true` key [V: confirmed against
  `exchange_card.html`'s macro, `enabled` is an independent flat key
  `update_exchange_credentials`'s current implementation never sets].
- Output on success: `{"exchange": str, "added": true, "api_key_last4": str}`
  — never the full key/secret (NFR-6).
- Errors: `ExchangeCredentialsUpdateRejectedError` (reused from
  `exchanges.py`, same failure class — this is the same underlying route).

#### `remove_exchange(exchange, confirm=False)`
- Input: `{"exchange": str, "confirm": bool}`.
- Behavior: refuses without `confirm=true` (reason: "this permanently
  deletes the exchange's saved credentials and configuration, not just one
  field, and cannot be undone").
- Once confirmed, maps to `POST /config`, JSON body
  `{"removed_elements": ["exchanges_<exchange>"]}` — confirmed [V] against
  `config_operations.py`'s full source that a `removed_elements` entry
  deletes the WHOLE nested node it names (`config["exchanges"]["<exchange>"]`
  entirely), not one field.
- Output on success: `{"exchange": str, "removed": true}`.
- Errors: `CONFIG_UPDATE_FAILED` on a non-2xx response.

#### `update_exchange_trading_mode(exchange, exchange_type=None, enabled=None)`
- Input: `{"exchange": str, "exchange_type"?: str, "enabled"?: bool}` — at
  least one of `exchange_type`/`enabled` required.
- Maps to `POST /config`, `{"global_config": {"exchanges_<exchange>_exchange-type"?:
  exchange_type, "exchanges_<exchange>_enabled"?: enabled}}` — only the
  provided field(s) are sent.
- `exchange_type`'s accepted values are **[UNVERIFIED]** — open question
  #17; do not validate against a guessed enum client-side until confirmed.
- Output: `{"exchange": str, "updated_fields": object}`.
- Not confirm-gated (ADR-0012 Decision 4: judgment call, flagged not
  silently decided — switching to futures mode changes risk materially on a
  real-trading exchange, but meets none of this project's literal gating
  criteria).

#### `get_interfaces()`
- Input: `{}`.
- Maps to `GET /accounts` (bare — same route a human visits; confirmed [V]
  from the full route source that no JSON branch exists). Scrapes every
  `.config-element` inside `#panelServices`, grouped by the service `name`
  each card carries.
- Output: `{"services": [{"service": str, "fields": {<field name, the part
  after "services_<service>_">: <value, or "***<last4>" if the field name
  matches NFR-22/23's secret-suffix list>}}]}`.
- Errors: `ConfigElementScrapeContractBrokenError` (NFR-21) only if
  `#panelServices` itself is missing — never for zero configured services.
- Idempotent, read-only, not confirm-gated.

#### `update_interface_config(service, patch, confirm=False)`
- Input: `{"service": str, "patch": {<field name>: <value>}, "confirm": bool}`.
- Behavior: if any key in `patch` ends with one of `token`/`secret`/
  `password`/`api-key`/`api-secret`/`api-password`/`api-hash`/
  `client-secret` (case-insensitive) and `confirm != true` → refuses via
  `require_confirmation`, reason naming which field(s) triggered it. A
  `patch` with no matching key proceeds regardless of `confirm` (ADR-0012
  Decision 3 — this is the one tool in this project whose gating is
  per-call, not per-tool; documented here and in the implementation
  docstring so this isn't missed).
- Once past gating, maps to `POST /config`, `{"global_config":
  {"services_<service>_<field>": value, ...}}`.
- Output: `{"service": str, "updated_fields": {<field>: <value, or
  "***<last4>" if secret-suffix-matching>}}` — secret-suffix-matching
  values are redacted in the OUTPUT the same way they are gated on input
  (NFR-23), never echoed in full even on success.
- Errors: `CONFIG_UPDATE_FAILED` on a non-2xx response, with the raw error
  text passed through `redact_secrets` (same INV-4 boundary
  `update_exchange_credentials` already established) before inclusion.

#### `get_notification_routing()`
- Input: `{}`. Maps to `GET /accounts` (bare), scrapes `#panelNotifications`.
- Output: `{"notification_type": [str], "price_alerts": bool, "trades":
  bool, "trading_script_alerts": bool, "other": bool}` [V-live: confirmed
  field names `notification_notification-type`/`-price-alerts`/`-trades`/
  `-trading-script-alerts`/`-other`].
- Errors: `ConfigElementScrapeContractBrokenError` only if
  `#panelNotifications` is missing.
- Idempotent, read-only, not confirm-gated.

#### `update_notification_routing(patch)`
- Input: `{"patch": {"notification_type"?: [str], "price_alerts"?: bool,
  "trades"?: bool, "trading_script_alerts"?: bool, "other"?: bool}}`.
- Maps to `POST /config`, `{"global_config": {"notification_notification-type"?:
  ..., "notification_price-alerts"?: ..., ...}}` — only provided fields
  sent.
- Output: `{"updated_fields": object}`. Not confirm-gated.

#### `get_profile_currencies()`
- Input: `{}`. Maps to `GET /profile` (bare), scrapes `#panelCurrency`.
- Output: `{"currencies": [{"currency": str, "enabled": bool, "pairs":
  [str]}]}` [V-live: confirmed field names `crypto-currencies_<Currency>_enabled`/
  `-pairs`, e.g. `crypto-currencies_Bitcoin_enabled` = `true`,
  `crypto-currencies_Bitcoin_pairs` = `["BTC/EUR"]`].
- Errors: `ConfigElementScrapeContractBrokenError` only if `#panelCurrency`
  is missing — never for zero traded currencies.
- Idempotent, read-only, not confirm-gated. Genuinely distinct from
  `update_profile`'s own `config` field (which is silently ignored by
  OctoBot's deployed route, per that tool's existing docstring) — this
  reads/writes through the `/config` route instead, confirmed live.

#### `update_profile_currencies(patch)`
- Input: `{"patch": {<currency name>: {"enabled"?: bool, "pairs"?: [str]}}}`.
- Maps to `POST /config`, `{"global_config": {"crypto-currencies_<Currency>_enabled"?:
  ..., "crypto-currencies_<Currency>_pairs"?: ..., ...}}` per currency in
  `patch`.
- Output: `{"updated_fields": object}`. Not confirm-gated (ADR-0012
  Decision 4: judgment call, flagged — changes what a real-trading profile
  actually trades, but meets none of this project's literal gating
  criteria).

---

### Automations (addendum — ADR-0013; genuinely new scope, found via a live UI audit, not in v1's original inventory)

| Tool | R/W | Confirm | Maps to | Tier |
|---|---|---|---|---|
| `get_automations()` | R | no | `GET /automations_edit_details` | [V] |
| `update_automations(patch)` | W | no | `POST /automations?action=save` | [V] |
| `start_automations(confirm)` | W | **yes** | `POST /automations?action=start` | [V] |
| `reset_automations_to_default(confirm)` | W | **yes** | `POST /automations?action=factory_reset` | [V] |

All four confirmed [V] against the full `/automations`/`/automations_edit_details`
route source (`Drakkar-Software/OctoBot`,
`web_interface/controllers/automation.py`) — "Automations" is OctoBot's own
internal pseudo-tentacle (`tentacle_type="automation"`), and `save`/read both
reuse `models.update_tentacle_config`/`get_tentacle_config_and_edit_display`,
the exact mechanism `update_tentacle_config`/`get_tentacle_config` already
wrap for every other tentacle (ADR-0013).

#### `get_automations()`
- Input: `{}`. Maps to `GET /automations_edit_details` [V].
- Output: OctoBot's own `{"config": {...}, "displayed_elements": {...}}`
  response, unchanged (NFR-24 — passthrough-only, same discipline as
  backtest report fields, NFR-8). `config.automations_count` is always
  present (confirmed [V-live], `0` on a fresh profile); the nested
  per-automation schema shape for `automations_count > 0` was not observed
  this session (open question #20) — this tool does not reshape it either
  way.
- Errors: generic `OctoBotClientError`/`OctoBotServerError` (plain read, no
  domain-specific failure mode).
- Idempotent, read-only, not confirm-gated.

#### `update_automations(patch)`
- Input: `{"patch": object}` — matching whatever shape `get_automations()`'s
  own `schema` currently describes; this tool does not validate `patch`
  against that schema itself (OctoBot's own route does).
- Maps to `POST /automations?action=save`, `patch` sent as the raw JSON
  body, unwrapped [V: confirmed `updateTentacleConfig(newValue, update_url)`
  sends the edited value directly, no wrapper key — same convention
  `update_tentacle_config` already follows].
- Output: `{"automations_updated_config": <passthrough of OctoBot's own
  echoed value>}`.
- Errors: `CONFIG_UPDATE_FAILED` with OctoBot's error text, same pattern as
  `update_tentacle_config`.
- Not confirm-gated (ADR-0013 Decision 2: mutates saved config only, does
  not itself start anything).

#### `start_automations(confirm=False)`
- Input: `{"confirm": bool}`.
- Behavior: if `confirm != true` → `require_confirmation("start_automations",
  "current profile", "This activates the currently saved automation
  configuration, which can then take real actions autonomously on bot
  events going forward, with no further per-action confirmation from this
  server. Review get_automations()'s current saved config before
  confirming.")` — a new, explicitly-named gating category (ADR-0013
  Decision 3: "activates autonomous, ongoing behavior beyond this one
  call"), distinct from "destructive"/"exposes credentials"/"enables live
  trading." Before the OctoBot call, checks `JobStore` for an active
  `backtest`/`data_collection` job and refuses outright if one is running
  (mirroring `restart_octobot`/`set_real_trading_enabled`, since this action
  also restarts automations per the confirmed route source).
- Once confirmed, maps to `POST /automations?action=start`
  → `models.restart_global_automations()` [V].
- Output on success: `{"started": true}`.
- Errors: `AutomationConfigInvalidError` (new, distinct exception — NOT a
  generic `CONFIG_UPDATE_FAILED`) wrapping OctoBot's own
  `InvalidAutomationConfigError` message [V: confirmed the route catches
  exactly this exception and returns it as a 400].

#### `reset_automations_to_default(confirm=False)`
- Input: `{"confirm": bool}`.
- Behavior: if `confirm != true` → `require_confirmation(
  "reset_automations_to_default", "current profile", "This permanently
  discards the current saved automation configuration back to OctoBot's
  default and cannot be undone.")` — the ORDINARY irreversible-deletion
  criterion (ADR-0003), same category as `clear_*_history`/
  `delete_data_file`, not the new Decision-3 category `start_automations`
  uses. Same job-store active-job check as `start_automations` (this action
  also restarts automations, per the confirmed route source).
- Once confirmed, maps to `POST /automations?action=factory_reset`
  → `models.reset_automation_config_to_default()` then
  `models.restart_global_automations()` [V].
- Output on success: `{"reset": true}`.
- Errors: `AutomationConfigInvalidError` (same class as `start_automations`,
  since the same restart call can raise it) or `CONFIG_UPDATE_FAILED` for
  the reset step's own failure.

## Job store design

**Updated by the historical-data-collection addendum (ADR-0007) — this is
now the canonical, current-state description of `JobRecord`/concurrency,
not the original v1-only shape.** The "Job store extension for `kind:
'data_collection'`" subsection above is the addendum's own change-log
(what changed and why); this section reflects the *result* of that change
directly, so a reader of this section alone never sees a stale picture.

### Data model
```
JobRecord:
  job_id: str (uuid4)
  kind: "backtest" | "compare_strategies" | "data_collection"
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
(`kind: "data_collection"` added by ADR-0007; no other field changed —
`JobRecord`/`JobStore` were already generic enough to accommodate it with
no structural change, see ADR-0007's Context section for the verification
this rested on.)

### Concurrency
- **Two independent process-wide `asyncio.Lock`s live on `JobStore`, one
  per OctoBot-side one-at-a-time constraint** (ADR-0002 for the first,
  ADR-0007 for the second — confirmed from source to be two genuinely
  separate resources inside OctoBot, not the same constraint reused):
  - `backtest_execution_lock`, reflecting OctoBot's one-backtest-at-a-time
    constraint. Every backtest submission (`start_backtest`, and each child
    of `compare_strategies`) acquires it for the full "submit → watch until
    finished/failed/cancelled" span, then releases.
  - `data_collection_execution_lock` (ADR-0007), reflecting OctoBot's
    separate one-data-collection-at-a-time constraint. `start_data_collection`
    acquires it for the same kind of "submit → watch until
    finished/failed/cancelled" span, then releases. A `start_backtest` and a
    `start_data_collection` call never block on each other's lock.
- A job is `state="queued"` while waiting on its kind's lock, `state="running"`
  once acquired and OctoBot confirms the run started.

### Background watcher (per running job)
This describes the `backtest`/`compare_strategies` watcher against
`/backtesting` specifically. `start_data_collection`'s watcher (ADR-0007)
follows the identical 6-step structure and lock-release discipline against
`/data_collector`/`data_collector_status` instead, with the payload-shape
and progress-conversion differences documented in this doc's "Historical
data collection" section above — it is not re-described step-by-step here
to avoid the two watchers' descriptions drifting out of sync with each
other as much as duplicating them would risk.

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
- INV-1: **every job-execution lock** (`backtest_execution_lock`,
  `data_collection_execution_lock` added by ADR-0007, and any future
  job-kind-specific lock) is released in a `finally` block on every code
  path through that lock's own watcher — a watcher exception can never
  leave subsequent jobs waiting on that same lock permanently queued.
  Originally scoped to `backtest_execution_lock` only; generalized when
  ADR-0007 added the second lock, the same "generalize once a second
  instance appears" pattern this project already applies elsewhere (e.g.
  `_parse_content_disposition_filename`'s move to a shared helper).
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

**Destination (task 17, FR-29, NFR-16 — closing a gap where the above was
implemented as `logging.getLogger(__name__)` calls but never wired to any
configured handler, so it was silently dropped)**: since this is a stdio MCP
server, stdout is reserved for the JSON-RPC transport (writing logs there
would corrupt the protocol stream), so `main()` configures, once, before
`mcp.run()`:
- A `RotatingFileHandler` (all configured levels) at a path controlled by
  `OCTOBOT_MCP_LOG_FILE` (default: `~/.octobot-mcp/server.log`, directory
  created if missing), bounded by `OCTOBOT_MCP_LOG_MAX_BYTES`/
  `OCTOBOT_MCP_LOG_BACKUP_COUNT` (defaults: 5MB × 3 backups, NFR-16).
- A `StreamHandler(sys.stderr)` at `WARNING`+ only, so a crashed/misbehaving
  process still surfaces something to whatever the MCP client captures from
  the subprocess, without flooding stderr with per-call `INFO` lines.
- Overall level via `OCTOBOT_MCP_LOG_LEVEL` (default `INFO`).
- No new MCP tool exposes this file's contents: unlike OctoBot's own logs
  (`export_logs`/`get_logs`, remote-only, reachable solely via HTTP), this
  server's log file already lives on the same local filesystem as the
  process that launched this stdio subprocess (ADR-0001) — a dedicated
  retrieval tool would be redundant plumbing for something already directly
  readable. The file's path is documented in `server.py`'s `INSTRUCTIONS`
  and `README.md` instead.
- Configuration happens only inside `main()`/`__main__`, never at module
  import time — so importing tool modules under `pytest` (or any other
  embedding) never creates a log file or attaches a handler as a side
  effect.

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

## Instance lifecycle (task 18, FR-30/FR-31, ADR-0010)

Narrow, explicit reversal of this doc's "Out of scope" reboot exclusion —
exactly two tools, scoped to unblocking evaluator-config-tuning backtest
validation (see ADR-0010 for the full trade-off analysis; this section
states the resulting contracts only).

#### `restart_octobot(confirm=false)`
Maps to `GET /wait_reboot?reboot=true` (HTML route, no body parsing —
OctoBot renders a wait page then kills/re-execs the process ~2s later).
Check order (cheapest/safest first, ADR-0010 Decision 4): (1) `confirm`
flag; (2) `JobStore` — refuses outright, structured non-exception return,
if any `backtest`/`data_collection` job is `queued`/`running`, naming the
job(s); (3) the one live call.

Gating (ADR-0010 Decision 2/3): requires `confirm=true` via the shared
`require_confirmation` helper, same mechanism regardless of the selected
profile's `profile_type` — but the refusal-without-`confirm` reason string
is more detailed/risk-scaled when `profile_type` is `LIVE` (or the
profile-type check itself is inconclusive — fail toward the more detailed
message, not silently allow) than when it is `SIMULATOR`. **Not** a hard,
`confirm`-bypassable-only-for-SIMULATOR block — see ADR-0010 for why an
earlier draft's hard refusal didn't hold up.

Output on success (HTTP 200): `{"restart_triggered": true, "message":
"OctoBot restart scheduled (~2s delay); the server will be briefly
unreachable. Call wait_for_octobot_ready() next, then re-confirm the
active profile via get_profile()/list_profiles() before starting a
backtest."}`. Any other outcome raises `RestartFailedError` (non-200
response, or a connection failure that isn't the expected
post-render-then-disconnect pattern) — never a false "triggered" result.

#### `wait_for_octobot_ready(timeout_seconds=60, poll_interval_seconds=2)`
A bounded retry-with-backoff loop against a lightweight existing read route
(`[UNVERIFIED]`, open question #14 — implementer picks and documents the
cheapest confirmed-working `GET`, verifying it behaves sanely in the first
seconds after a restart, not just once steady-state). Never blocks past
`timeout_seconds`. Returns `{"ready": true|false, "elapsed_seconds": float,
"attempts": int}` — a timeout is a normal, honest result, never an
exception, matching this project's NFR-3 "honest `null`/`false` over a
fabricated guess" ethic. **"Ready" means only "the HTTP server answered a
request again"** — `[UNVERIFIED]` (open question #14) whether that
coincides with "the evaluator-config cache this capability exists to clear
is actually clear." State this plainly in the tool's own docstring; do not
imply a stronger guarantee.

Not confirm-gated (a read-only poll). Two separate tools, not one blocking
call and not a `JobStore` job — see ADR-0010 Decision 5 for why (no live
socket survives the outage to watch through; a binary reachable/not-yet
signal shouldn't be dressed up as a fabricated graduated progress/ETA).

No bundled workflow tool exists for the full `select_profile` →
`restart_octobot` → `wait_for_octobot_ready` → re-confirm via
`get_profile`/`list_profiles` → `start_backtest` flow — left as composable
primitives (ADR-0010 Decision 6).

## Open questions
See requirements doc's "Open questions" section (1–6) — restated here as
blocking implementation tasks for the specific tools they affect:
`list_profiles`/`get_profile` (Q1), `update_exchange_credentials` (Q2),
`get_job_result`/`compare_strategies` diff table (Q3), the backtest watcher
(Q4), and `start_backtest`'s `current_bot_data` mode field values (Q6).

Q5 (`convert_profile_to_live`/confirm-gating completeness) is **resolved**
(a later session): a separate real-vs-simulated toggle does exist
(`config.trader.enabled`), `convert_profile_to_live` does not set it, and no
in-scope tool does — see this tool's own entry above and ADR-0003's revised
rationale.

Historical-data-collection addendum open questions (requirements doc 7–11,
ADR-0007/ADR-0008): `list_data_files`'s `file`-field type (Q7),
`start_data_collection`'s timestamp units (Q8), the progress-conversion
formula's step-indexing assumption (Q9), `_ensure_backtesting_limits`'s
exact validation text (Q10), and whether a mid-collection failure has any
distinguishable status signal (Q11) — all blocking implementation tasks for
the new tasklist item, not guesses to implement against.

`update_evaluator_config` open questions (requirements doc 12–13, resolved
before that tool shipped): `EVALUATOR_CONFIG_KEY`'s literal value (Q12) and
`deactivate_others`'s semantics (Q13).

Instance-lifecycle open question (requirements doc 14, ADR-0010, blocking
for task 18): end-to-end restart timing and whether "HTTP-reachable again"
coincides with "the tentacle-config cache is actually clear" (Q14) — a
developer must trigger one real restart against the live instance to
resolve this before finalizing `wait_for_octobot_ready`'s defaults/polling
route.

Portfolio-snapshot/evaluation-matrix open questions (requirements doc 15–16,
ADR-0011) — **not blocking for initial implementation**, unlike every prior
addendum's own open questions, since both tools' core structure is already
[V-live] confirmed and their fail-loud gates don't depend on either question:
`get_portfolio_snapshot`'s multi-exchange tooltip separator format (Q15 —
ships with graceful `null` degradation until resolved) and whether
`/advanced/matrix` is ever server-side paginated at scale (Q16 — not
verifiable without a heavily-configured live instance).

Accounts/profile-global-config open questions (requirements doc 17–19,
ADR-0012) — also not blocking: `exchange_type`'s exact enum value strings
(Q17 — affects only `update_exchange_trading_mode`'s accepted-values
validation, not the route/body shape itself), the 7 un-configured services'
exact field sets (Q18 — `get_interfaces`/`update_interface_config` work
generically regardless; only NFR-22/23's secret-suffix gating/redaction
depends on it), and the masked-placeholder constant for `get_exchanges`
(Q19 — resolved, `[V]`, recorded only for implementer convenience).

Automations open questions (requirements doc 20–21, ADR-0013) — also not
blocking: the populated-automation nested schema shape (Q20 — the
passthrough design in `get_automations`/`update_automations` tolerates this
by construction) and whether `InvalidAutomationConfigError`'s message text
ever needs redaction (Q21 — not yet triggered against a real invalid
config).

## Hand-off
- Traced requirements: FR-1..FR-20, NFR-1..NFR-12 (requirements doc); the
  historical-data-collection addendum additionally traces to FR-22..FR-27,
  NFR-13..NFR-15; the instance-lifecycle addendum (ADR-0010) additionally
  traces to FR-30..FR-31, NFR-17; the portfolio-snapshot/evaluation-matrix
  addendum (ADR-0011) additionally traces to FR-33..FR-34, NFR-19..NFR-20;
  the accounts/profile-global-config addendum (ADR-0012) additionally
  traces to FR-35..FR-42, NFR-21..NFR-23; the Automations addendum
  (ADR-0013) additionally traces to FR-43..FR-45, NFR-24.
- Implementer: `developer` agent, sequenced per `docs/tasklist.md`.
- Test coverage expectations: every ETA branch (unit, synthetic sequences),
  every confirm-gated tool (unit, with/without flag), the watcher's lock
  release on exception (unit, forced exception), the job store TTL/cap
  eviction (unit), and one end-to-end smoke test per milestone against the
  live instance where the live instance is reachable from the developer's
  environment. For the addendum specifically: a standalone `/data_collector`
  Socket.IO round-trip check (mirroring milestone 8's own first task) before
  wiring the full watcher, a unit test for the step-progress conversion
  formula's boundary values (0 steps, `current_step == total_steps`, a
  clamp-triggering out-of-range input), and a broken-fixture test proving
  `list_data_files` fails loud (mirroring `list_profiles`'s own). For the
  portfolio-snapshot/evaluation-matrix addendum: fixtures built from this
  session's own [V-live] captures (including the real `"None"`-value and
  empty-`time_frame` matrix rows, and the real single-exchange tooltip
  shape), a broken-fixture test per adapter proving each fails loud
  (mirroring `list_profiles`'s own), and an explicit zero-rows-but-intact
  fixture per adapter proving that case does NOT raise (mirroring
  `list_evaluators`'s own empty-category test, but applied to a whole-table
  empty case here rather than one category). For the accounts/profile-
  global-config addendum: fixtures built from this session's own [V-live]
  captures (Telegram/Web/notification/currency field shapes — redacted of
  any real secret value before being committed to a fixture file), a
  broken-fixture test per scrape adapter proving each fails loud, and a
  dedicated test asserting `update_interface_config`'s secret-suffix-based
  conditional gating (NFR-22) both ways (a non-matching patch proceeding
  without `confirm`, a matching one refusing). For the Automations
  addendum: the zero-automations fixture from this ADR's own capture, a
  populated-automation fixture once available (open question #20,
  non-blocking), and a test asserting `start_automations`/
  `reset_automations_to_default` both classify `InvalidAutomationConfigError`
  distinctly from a generic `CONFIG_UPDATE_FAILED`.
