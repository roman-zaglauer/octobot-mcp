# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.1] - 2026-09-08

### Fixed
- `convert_profile_to_live`'s safety messaging overstated its effect: its
  docstring and confirm-gating reason string previously claimed the call
  could cause "real orders \[to\] be placed against a real exchange
  account." Tracing the tool's underlying OctoBot calls
  (`convert_to_live_profile`/`select_profile`) confirmed they only relabel
  `profile.profile_type` and select the profile — neither touches
  `config.trader.enabled`, the separate flag OctoBot's own
  `is_real_trading()` actually checks. This is a documentation/safety-
  messaging correction only, not a functional or behavioral change: the
  tool's HTTP calls, inputs, and output shape are unchanged, and it
  remains confirm-gated (it still persistently relabels and selects the
  profile, and could otherwise mislead a caller into believing real
  trading is now active when it is not). Corrects the tool's docstring,
  confirm-gating message, `server.py`'s INSTRUCTIONS, the requirements
  doc (resolving open question #5), the tool spec, and ADR-0003's
  rationale accordingly ([0bd0c17](https://github.com/roman-zaglauer/octobot-mcp/commit/0bd0c17f89cf3159d6214965c02b7d784b7cb8d7)).

## [1.0.0] - 2026-09-08

First public release. All 14 original tasklist milestones plus five
post-v1 addenda (`docs/tasklist.md` items 15–19: historical data
collection, evaluator config CRUD, this server's own logging, the
restart capability, and evaluator discovery) are implemented, tested, and
(per this project's own convention) judge-verified. 47 tools, 371 tests
(all mocking the OctoBot HTTP/Socket.IO layer — no live instance required
to run the suite).

**Known limitation**: `restart_octobot`/`wait_for_octobot_ready` (see
"Instance lifecycle" below) are correctly implemented and safe as
designed, but live testing has confirmed — on two independent OctoBot
instances — that they do **not** achieve the evaluator/strategy-
composition-tuning backtest validation they were built for. See
`docs/adr/0010-octobot-restart-capability.md`'s "Negative finding" for the
full account.

### Added

#### Profiles
- `list_profiles` / `get_profile` — the project's first Tier-B HTML-scrape
  read adapter (`/profiles_selector` has no JSON API), with fail-loud
  parsing rather than ever returning a partial or silently-empty list.
- `create_profile` (duplicate / import_file / import_url / import_cloud_strategy),
  `update_profile`, `select_profile`, `export_profile`.
- `delete_profile`, `convert_profile_to_live` — confirm-gated (irreversible
  deletion, and the closest confirmed signal to enabling real-money trading).

#### Backtesting & strategy comparison
- `start_backtest`, `get_job_status`, `list_jobs`, `cancel_job`,
  `get_job_result` — an async job model backed by a Socket.IO progress
  watcher, with a window-based ETA heuristic (linear rate over a bounded
  sample window, confidence tiers, and a staleness check that honestly
  reports `null`/low-confidence on a stall rather than fabricating a
  number).
- `compare_strategies` — runs N backtests sequentially under the same
  execution lock, with combined progress/ETA and a diff table built by
  flattening and intersecting each completed child's report (no
  hardcoded field list).
- Native MCP progress notifications on top of polling, opportunistically
  emitted when a caller supplies a `progressToken`; polling remains fully
  functional without one.

#### Historical data collection
- `start_data_collection`, `list_data_files`, `import_data_file`,
  `get_available_timeframes_for_collection` — extends the same async
  job/watcher/ETA infrastructure to a second, independent "one collection
  at a time" lock (OctoBot enforces backtesting and data collection as
  separate constraints), so `start_backtest(mode="data_files")` has a way
  to discover and populate real exchange history. See ADR-0007/ADR-0008.
- `delete_data_file` — confirm-gated (an erroneous delete costs a full
  re-collection, not a quick redo).

#### Portfolio & trading
- `get_orders`, `get_positions`, `get_trades`, `get_pnl_history`,
  `get_historical_portfolio_value` (read-only).
- `cancel_order`, `close_position`, `refresh_portfolio` (routine, not
  confirm-gated, by explicit project decision).
- `clear_orders_history`, `clear_trades_history`, `clear_portfolio_history`,
  `clear_transactions_history` — confirm-gated.

#### Exchanges
- `get_currency_list`, `get_all_currencies`, `get_all_symbols`,
  `check_accounts_compatible`, `get_exchange_details` (read-only).
- `update_exchange_credentials` — confirm-gated; never round-trips a real
  API key/secret back through its own response, since OctoBot's own
  `/config` route echoes submitted credentials verbatim on success.

#### Tentacle, trading, and evaluator config
- `get_tentacle_config`, `update_tentacle_config`, `update_trading_config`.
- `update_evaluator_config` — evaluator-level config CRUD (per-evaluator
  patch plus a `deactivate_others` flag).
- `list_evaluators` — a third Tier-B HTML-scrape adapter, closing
  `update_evaluator_config`'s companion-read gap (discovering an
  evaluator's exact class name and activation state). ADR-0009 originally
  recommended NO-GO on this adapter; the user explicitly asked for it
  anyway, and the ADR was reversed to GO once live verification resolved
  every structural question the original design had flagged as blocking.
- `export_logs` (real route); `list_tentacles` and `get_logs` are honest
  Tier-C stubs — OctoBot has no JSON API for enumerating tentacles or log
  entries, so these explain the gap rather than attempting a call that
  can't work.

#### Instance lifecycle
- `restart_octobot` (confirm-gated) / `wait_for_octobot_ready` — added
  (ADR-0010) as a narrow, explicit reversal of this project's own
  "reboot" out-of-scope exclusion, specifically to unblock validating
  evaluator-level config tuning via backtest, which OctoBot's own
  `_TENTACLE_CONFIG_CACHE` staleness silently defeats without a full
  process restart. Both tools are correctly implemented and safe as
  designed: real restart timing was measured live (~2.1s until the old
  process goes down, ~6.3–6.7s of outage, ~8.5–8.9s total until
  reachable again), and a genuine readiness-check race (an early poll
  could catch the still-alive old process and report `ready: true`
  before anything had restarted) was found and fixed.
  **However, live testing has since confirmed this capability does NOT
  achieve the workflow it was built for**: a `profile_id`-targeted
  backtest still does not reflect strategy-composition config changes
  (which/how-many evaluators must agree) after a restart, confirmed on
  two independent OctoBot instances. Order-execution config (sizing,
  stop-loss/take-profit) is unaffected and applies correctly without a
  restart. `restart_octobot`/`wait_for_octobot_ready` remain available as
  general-purpose, working restart/readiness primitives — just not as a
  fix for strategy-composition backtest validation. See
  `docs/adr/0010-octobot-restart-capability.md` ("Negative finding") for
  the full account, including an open, undecided question of whether to
  keep this capability at all now that its original purpose doesn't
  hold.

#### Observability
- Structured per-tool-call and per-job-transition logging (tool name,
  redacted arguments, duration, outcome, error class), with generic
  redaction by parameter name (`api_key`/`api_secret`/`api_password`) on
  both the success and error path.
- This server's own rotating log file (`configure_logging()`), wired into
  `main()` only (no import-time side effect under tests), overridable via
  `OCTOBOT_MCP_LOG_FILE`/`OCTOBOT_MCP_LOG_LEVEL`/`OCTOBOT_MCP_LOG_MAX_BYTES`/
  `OCTOBOT_MCP_LOG_BACKUP_COUNT`, plus a fixed stderr `WARNING`+ handler so
  a crashing process still surfaces something to the MCP client.
- Bounded resilience in the backtest watcher: a single transient
  Socket.IO tick failure is now logged and retried (up to 3 consecutive
  failures) rather than immediately failing the job.

#### Documentation
- ADRs 0001–0010 covering transport/SDK choice, the async job model,
  confirm-flag gating, single-instance configuration, no-auth v1 scope,
  the Tier-A/B/C read-model acquisition strategy, historical data
  collection, evaluator-discovery adapter policy, and the restart
  capability.
- Functional/non-functional requirements doc, tool spec (full input/
  output/error shapes, job store design, ETA algorithm), and
  `docs/tasklist.md` (the build roadmap in implementation order).
- Repository boilerplate: MIT license, `CONTRIBUTING.md`,
  `CODE_OF_CONDUCT.md`, `.github/SECURITY.md` (private vulnerability
  reporting enabled), issue/PR templates, `CODEOWNERS`, and CI
  (`.github/workflows/ci.yml`, Python 3.10–3.14, fully-mocked test suite,
  default-deny permissions, actions pinned to a commit SHA).

### Fixed
(Bugs found and corrected during pre-release development and hardening —
none of these affected a prior public release, since this is the first
one, but they're recorded here because each reflects real, verified
behavior worth knowing about.)
- A real concurrency defect in `compare_strategies`: a job force-cancelled
  from `queued` to `cancelled` while its start request was still in
  flight could leave OctoBot running an "orphaned" backtest with the
  shared execution lock held for the run's full duration. Fixed by
  checking the job's actual state immediately after the start call
  succeeds and stopping the orphaned run if the race occurred.
- `wait_for_octobot_ready` could report a false `ready: true` by racing
  OctoBot's own ~2s pre-kill grace window and catching the not-yet-killed
  old process (reproduced live, twice). Fixed: a later success only
  counts once at least one genuinely failed poll has been observed.
- This server's own structured logging was implemented but never
  actually wired up — no handler was ever configured, so INFO/DEBUG log
  lines were silently dropped. Also fixed an MCP SDK import-time side
  effect (`MCPServer.__init__` calling `logging.basicConfig()`) that
  otherwise clobbered this server's own logging configuration.
- `list_tentacles`' stub response kept telling callers that evaluator
  discovery "was considered and rejected" after `list_evaluators` shipped
  and solved exactly that — pointed at the new tool instead.

### Security
- Scrubbed one real trading profile's name and a detailed personal
  strategy-tuning description (stop-loss %, ladder sizing, take-profit
  tiers) out of a captured-HTML test fixture before this repository's
  first public commit, replacing it with synthetic placeholder text (no
  test assertion depended on the removed content).
- Enabled GitHub private vulnerability reporting as the primary security
  contact path (`.github/SECURITY.md`).

[Unreleased]: https://github.com/roman-zaglauer/octobot-mcp/compare/v1.0.1...HEAD
[1.0.1]: https://github.com/roman-zaglauer/octobot-mcp/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/roman-zaglauer/octobot-mcp/releases/tag/v1.0.0
