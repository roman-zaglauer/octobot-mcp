# Developer Tasklist: OctoBot MCP Server (v1)

Ordered so each milestone is independently demoable against the live test
instance (`OCTOBOT_BASE_URL=http://192.168.178.100:5001` — confirm it's still
up before relying on it). Traces to `docs/requirements/octobot-mcp-requirements.md`
and `docs/specs/octobot-mcp-tool-spec.md`. Follow TDD (failing test first) per
global project standards.

1. **Project scaffolding + MCP server skeleton.**
   Package layout, `pyproject.toml`, MCP Python SDK stdio server registering
   zero tools. Demo: an MCP client (Claude Code) connects and lists 0 tools
   without error.

2. **HTTP client wrapper + config loading.**
   `OCTOBOT_BASE_URL` env var (ADR-0004), `httpx.AsyncClient` with the
   NFR-5 timeout policy, one error-classification helper (NFR-4) mapping
   timeout/refused/4xx/5xx/malformed-JSON to a structured error type. No
   tools yet. Demo: a unit test hits each failure class against a mocked
   transport and asserts the right error class.

3. **First read-only, low-risk, Tier-A JSON tools.**
   `get_orders`, `get_positions`, `get_trades`, `get_pnl_history`,
   `get_historical_portfolio_value`, `get_currency_list`,
   `get_all_currencies`, `get_all_symbols`, `check_accounts_compatible`,
   `get_exchange_details`. Demo: agent queries live portfolio/orders data
   through Claude Code.

4. **Tier-B profile read adapter — with the live-instance verification task
   done first.**
   Before writing the scraper: fetch and inspect `/profiles_selector`'s
   actual rendered HTML from the live instance (open question #1) and record
   the confirmed DOM/variable shape in the adapter's docstring. Then
   implement `list_profiles`/`get_profile` with fail-loud parsing (INV-5) and
   a broken-fixture test proving it fails loud, not silent-empty. Demo:
   agent lists real profiles by name and correctly identifies the selected
   one.

5. **Profile write tools (non-destructive).**
   `create_profile` (all 4 modes), `update_profile`, `select_profile`,
   `export_profile`. Demo: agent duplicates a profile, renames the copy,
   selects it, exports it, and imports a second profile via `import_url` to
   exercise a second creation path live. `import_file` and
   `import_cloud_strategy` are implemented and unit-tested at this step too,
   but not separately demoed here — they dispatch to the same
   `/profiles_management/import`/`download` route family the `duplicate`
   and `import_url` demo already exercises live (see spec's per-mode
   mapping), so demoing all 4 modes live at this milestone would be
   redundant; demo them opportunistically later if a real file/cloud
   strategy becomes available.

6. **The shared confirm-gating helper, and the first two gated tools.**
   Implement `require_confirmation(action, target, reason)` once (ADR-0003),
   then `delete_profile` and `convert_profile_to_live` as its first
   consumers, each with a test asserting refusal without the flag and
   success with it. Demo: agent attempts to delete a profile without
   `confirm`, gets a clear refusal, retries with `confirm=true`, succeeds.

7. **Job store scaffolding (state machine only, no OctoBot wiring yet).**
   `JobRecord`, in-memory store, `backtest_execution_lock`, TTL/cap eviction
   (NFR-9), `get_job_status`/`list_jobs`/`cancel_job` operating against
   synthetic/fake job transitions. Demo: unit tests drive a fake job through
   queued→running→completed and queued→cancelled; eviction test with >50
   fake completed jobs.

8. **Socket.IO progress watcher + `start_backtest`, wired end-to-end.**
   First: the standalone compatibility check from ADR-0002's risk table —
   connect a `python-socketio` async client to the live instance's
   `/backtesting` namespace and confirm `backtesting_status` round-trips,
   *before* wiring the full watcher. Also resolve open question #6
   (`current_bot_data` mode's `exchange_id`/`exchange_type`/`data_source`
   valid values) before wiring that mode — implement `data_files` mode
   first if #6 isn't resolved in time, and un-block `current_bot_data` once
   it is. Then implement the watcher loop (1.5s interval, dedup samples,
   INV-1 lock-release-in-finally), both `start_backtest` modes, and
   `get_job_result` for a single backtest (capturing and recording the real
   `report` payload shape per open question #3). Demo: agent starts a real
   backtest, polls `get_job_status` and watches `progress_percent` advance
   to 100 and `state` reach `completed`, then calls `get_job_result`.

9. **ETA estimation, implemented and unit-tested in isolation.**
   The full algorithm from the spec, tested against synthetic sample
   sequences: linear-ish progress, stalled-at-0%, single-sample, a
   slowdown-then-speedup sequence, and a **mid-run stall** (progress
   advances normally to e.g. 45% and then no new sample arrives for >15s
   while the job stays `running`) — asserting the confidence-tier
   transitions, the `null`-when-insufficient-data invariant (NFR-3, INV-2),
   and the staleness downgrade (the mid-run-stall sequence must force
   `eta_seconds: null`/`eta_confidence: "low"` even though the samples
   collected just before the stall would otherwise support a confident
   estimate). Demo: replay a captured real progress sequence from step 8
   through the estimator and sanity-check the numbers look reasonable
   (not a live-instance demo — this step is pure unit testing).

10. **`compare_strategies`.**
    Sequential child submission through the same lock, combined
    progress/ETA formulas from the spec, per-child failure isolation (one
    bad config doesn't abort the batch), the diff-table construction
    (derived from whatever fields are actually present across all
    children's reports — do not hardcode a field list; use step 8's
    captured real payload to write this against real data, not a guess).
    Also unit-test the combined-ETA aggregation's two branches from the
    spec directly (not just end-to-end): zero children completed (ETA
    sourced only from the current child's own `eta_seconds`, or `null` if
    that's also unavailable — never fabricated from zero completed runs)
    and ≥1 child completed (average-child-duration-based projection for the
    remaining children) — the same way step 9 unit-tests the single-job ETA
    branches. Demo: agent compares 2–3 real profiles' backtests and gets
    back one combined job with a diffed report table.

11. **Native MCP progress notifications on top of polling.**
    Opportunistic emission when the caller supplies a `progressToken`,
    sourced from the same watcher updates as step 8/10 — polling remains
    authoritative (ADR-0002). Demo: a client that renders progress
    notifications shows a live progress bar during step 8's/10's demo,
    while `get_job_status` polling still works if notifications are
    disabled.

12. **Remaining confirm-gated destructive tools.**
    `clear_orders_history`, `clear_trades_history`, `clear_portfolio_history`,
    `clear_transactions_history`, and — only after resolving open question #2
    via the live instance's devtools network tab — `update_exchange_credentials`,
    with the redaction invariant (INV-4/NFR-6) covered by a dedicated test
    asserting no full secret value ever appears in output or logs. Demo:
    agent clears a history list with confirmation, and updates a test
    exchange's credentials with the response showing only a redacted key.

13. **Tentacle config + logs, including the honest Tier-C stubs.**
    `get_tentacle_config`, `update_tentacle_config`, `export_logs` (all
    Tier A/[V]); `list_tentacles` and `get_logs` as structured
    "not available in v1" responses (Tier C). Demo: agent reads/updates a
    tentacle's config; calling `list_tentacles`/`get_logs` returns a clear,
    non-error explanation rather than a failure.

13.5. **`update_trading_config` — closing a backlog gap.**
    Added to the spec (FR-21) and ADR-0003's non-gated-tools rationale
    during milestone 5's review, but never assigned to a tasklist item, so
    it was never implemented. `update_trading_config(patch)` → `POST
    /config`'s `TRADING_CONFIG_KEY` branch (already `[V]`-verified in the
    spec: reads `request_data[TRADING_CONFIG_KEY]`, calls
    `models.update_tentacles_activation_config(...)`, the same function the
    `TENTACLES_CONFIG_KEY`/`EVALUATOR_CONFIG_KEY` branches also call).
    Output: `{"trading_updated_config": <passthrough of OctoBot's own
    echoed value for this key>}`. Not confirm-gated (mutates
    strategy/tentacle activation config, does not destroy data, expose
    credentials, or enable live trading). Demo: agent updates the trading
    config for the current profile and sees the change reflected in the
    echoed output.

13.6. **`cancel_order`/`close_position`/`refresh_portfolio` — closing a second backlog gap.**
    Same pattern as 13.5: all three are specced in the spec's "Portfolio &
    trading" table with verified routes (`cancel_order(order_id)` →
    `POST /api/orders`; `close_position(position_id)` → `POST
    /api/positions`; `refresh_portfolio()` → `POST /api/refresh_portfolio`)
    but were never assigned to a tasklist item, so none were implemented.
    User was asked and explicitly chose to implement all three now.
    User was also asked about the spec's own flagged judgment call —
    `cancel_order`/`close_position` are marked "not confirm-gated" with a
    footnote saying this is "revisitable with the user" since they're
    irreversible in effect (and can act on real money for a LIVE profile)
    even though they're routine trading activity — and explicitly chose to
    keep them **ungated**, per ADR-0003's original reasoning. Demo: agent
    cancels an order/closes a position/refreshes the portfolio and sees
    the change reflected in a follow-up `get_orders`/`get_positions`/
    portfolio read.

14. **Error handling / resilience polish pass.**
    Sweep every tool for NFR-4/NFR-5 compliance (timeout present,
    error classified, no raw stack traces reaching the MCP client);
    add the structured per-call and per-job-transition logging from the
    spec's Observability section; run a full smoke-test checklist against
    the live instance covering every tool at least once. This is the
    exit criterion for calling v1 done.

15. **Historical data collection tools — new scope addendum (ADR-0007/ADR-0008).**
    Not one of the original 14 milestones or the 13.5/13.6 backlog-gap
    addenda — this is genuinely new scope, added after v1 shipped, closing
    a gap discovered while designing it: `start_backtest(mode="data_files")`'s
    own docstring already admits there is no tool exposing what data files
    exist. Traces to FR-22..FR-27, NFR-13..NFR-15 (requirements doc) and the
    "Historical data collection" section of the tool spec.

    **Step 0 (do first, mirroring milestone 8's own first task):**
    connect a `python-socketio` async client to the live instance's
    `/data_collector` namespace and confirm `data_collector_status` round-
    trips (client emits with no payload, server emits back
    `{"status", "progress": {"current_step", "total_steps",
    "current_step_percent"}}`) — before wiring the full watcher. While
    connected, trigger one real multi-symbol/multi-timeframe collection and
    log the raw `(current_step, total_steps, current_step_percent)`
    sequence to resolve open question #9 (indexing) before finalizing the
    progress-conversion formula in ADR-0007. Also resolve open questions #8
    (timestamp units — trace `_background_collect_exchange_historical_data`
    or test empirically) and #10 (`_ensure_backtesting_limits`'s exact
    validation text) before or during this step.

    **Then implement, in this order:**
    1. `octobot_mcp.jobs.JobKind` widened to include `"data_collection"`;
       add `JobStore.data_collection_execution_lock` (NFR-15) — no other
       `JobStore` changes needed (ADR-0007's "generic across kind" proof).
    2. Move `_extract_progress_token`/`_build_progress_notifier`/
       `_attach_progress_notifier` from `octobot_mcp.tools.backtesting` to
       `octobot_mcp.tools._shared` (this milestone is their second
       consumer, mirroring the `_parse_content_disposition_filename`
       precedent from milestone 13).
    3. New error classes in `octobot_mcp.errors`:
       `DataCollectionStartFailedError`, `DataFileScrapeContractBrokenError`,
       `DataFileDeleteRejectedError`, `DataFileImportFailedError` — same
       one-class-per-domain-specific-failure convention as every prior
       milestone. Defined **before** the module below that raises them.
    4. New module `octobot_mcp.tools.data_collection`: the
       `/data_collector` Socket.IO watcher (mirroring
       `_BacktestingStatusChannel`/`_watch_until_done`/
       `_run_backtest_watcher`'s structure, INV-1 lock-release-in-`finally`
       preserved), `start_data_collection`, `delete_data_file` (**gated,
       `confirm=true` required** via the shared `require_confirmation`
       helper — reversed from this addendum's original not-gated draft; see
       requirements doc FR-25/tool spec for the reasoning), `import_data_file`,
       `get_available_symbols_for_collection`,
       `get_available_timeframes_for_collection`, and `list_data_files`
       (Tier B — BeautifulSoup scrape of `GET /data_collector`'s
       `tr.selectable_datafile` rows, fail-loud via
       `DataFileScrapeContractBrokenError`, mirroring `list_profiles`'s
       INV-5-style discipline; resolve open question #7, the exact `file`
       value type, against the live instance first).
    5. One new branch in `octobot_mcp.tools.backtesting.get_job_result`:
       `elif job.kind == "data_collection": return job.result`.
    6. Register the new tools (`register_all`), update `README.md`'s
       "Available tools" list (noting `delete_data_file` as confirm-gated
       alongside the existing gated-tools list).

    **Demo:** agent lists available symbols/timeframes for an exchange,
    starts a real data collection job, polls `get_job_status` and watches
    `progress_percent`/`eta_seconds` advance to completion, calls
    `get_job_result`, then calls `list_data_files` and finds the new file,
    then calls `start_backtest(mode="data_files", files=[<that file>])`
    against it — demonstrating the end-to-end "collect then backtest" flow
    this addendum exists to enable. Also demo: `cancel_job` on a
    still-running collection; `delete_data_file` refusing without
    `confirm=true` and then removing the file with `confirm=true`;
    `import_data_file` uploading a file obtained out of band.

16. **`update_evaluator_config` — new scope addendum, closing an
    evaluator-CRUD gap.**
    Not one of the original 14 milestones or a backlog-gap addendum like
    13.5/13.6 — genuinely new capability, added after investigating whether
    evaluator activation is controllable via the API (it is, via the same
    `POST /config` route `update_trading_config` already uses, on a sibling
    branch). Traces to FR-28 (requirements doc) and the `update_evaluator_config`
    subsection of the tool spec's "Trading config" section.

    **First, resolve open questions #12/#13 against source** (grep for
    `EVALUATOR_CONFIG_KEY`'s literal value next to `TRADING_CONFIG_KEY`'s in
    the same controller module; trace `models.update_tentacles_activation_config`'s
    `deactivate_others` parameter to confirm its effect on evaluators not
    named in `patch`). Record both findings in the tool's docstring — do not
    ship this tool with either left as a guess.

    **Then implement** `update_evaluator_config(patch, deactivate_others=False)`
    in `octobot_mcp.tools.trading_config`, alongside `update_trading_config`
    (same module — same spec section, same route, same underlying model
    call; splitting it into its own module would separate two tools that
    share everything but one dict key and one extra argument). Not
    confirm-gated, same ADR-0003 reasoning as `update_trading_config`
    (FR-28). Output shape mirrors `update_trading_config`'s: passthrough of
    whatever OctoBot echoes back for `EVALUATOR_CONFIG_KEY`, not an invented
    field name.

    Register the new tool (`register_all`), update `README.md`'s tool count/
    list and `server.py`'s `INSTRUCTIONS` constant to mention it, following
    this project's established practice of keeping both in sync with every
    new tool (reinforced by milestone 15's judge feedback).

    **Demo:** agent updates the evaluator activation config for the current
    profile (e.g. disables one evaluator, enables another) and sees the
    change reflected in the echoed output; a follow-up `get_tentacle_config`
    against one of the affected evaluator tentacles confirms the change
    actually took effect on the OctoBot instance, not just in the echoed
    response.

17. **Wire up this server's own logging — closing a gap in milestone 14's
    own exit criterion.**
    Milestone 14 added the structured per-call/per-job-transition
    `logging.getLogger(__name__)` calls the tool spec's "Observability"
    section describes (`_shared.py`, `jobs.py`, `backtesting.py`,
    `data_collection.py`), but never configured any `logging` handler
    anywhere in the process — confirmed by grepping the whole source tree
    for `basicConfig`/`addHandler`/`dictConfig` and finding none. With zero
    handlers, Python's root logger falls back to `logging.lastResort`,
    which only ever surfaces `WARNING`+ records to stderr — every `INFO`/
    `DEBUG` structured log line (the majority of what milestone 14 added)
    has been silently dropped since it shipped. User-discovered gap ("Is it
    possible to grab logs from the MCP Server"), not part of the original
    14 milestones. Traces to FR-29/NFR-16 (requirements doc) and the tool
    spec's "Observability" section's new "Destination" subsection.

    **Implement:**
    1. A small `octobot_mcp.logging_config` module (or a function in
       `server.py` itself if that reads more naturally — developer's call,
       consistent with this project's existing module-boundary judgment)
       exposing one function, e.g. `configure_logging()`, following
       `config.py`'s existing env-var-reading convention
       (`OCTOBOT_BASE_URL`'s pattern: read once, dataclass-like resolved
       config, sane validated defaults):
       - `OCTOBOT_MCP_LOG_FILE` (default `~/.octobot-mcp/server.log`;
         create the parent directory if missing).
       - `OCTOBOT_MCP_LOG_LEVEL` (default `INFO`).
       - `OCTOBOT_MCP_LOG_MAX_BYTES` (default `5_000_000`) /
         `OCTOBOT_MCP_LOG_BACKUP_COUNT` (default `3`) for the
         `logging.handlers.RotatingFileHandler` (NFR-16).
       - Also attaches a `logging.StreamHandler(sys.stderr)` fixed at
         `WARNING` (not affected by `OCTOBOT_MCP_LOG_LEVEL`), so a crashing
         process still surfaces something to whatever the MCP client
         captures from the subprocess.
    2. Call `configure_logging()` from `server.py::main()`, **before**
       `mcp.run()` — never at module import time (FR-29's own acceptance
       criterion: importing tool modules under `pytest` must never create a
       log file or attach a handler as a side effect; verify this
       explicitly, e.g. a test that imports the package and asserts the
       root logger has no handlers unless `configure_logging()` was called).
    3. No new MCP tool is added to retrieve this file (see tool spec's
       "Destination" subsection for why: it's already on the same local
       filesystem as the process that launched this stdio subprocess,
       unlike OctoBot's own remote-only logs). Instead, document the
       resolved path: a line in `server.py`'s `INSTRUCTIONS` constant (the
       "Tool groups"/relevant section) and a short section in `README.md`.
    4. Tests: `configure_logging()` produces a `RotatingFileHandler` with
       the documented default (and env-overridden) bounds; a log line
       written after configuration actually appears in the file; the
       import-time-no-side-effect test from step 2.

    **Demo:** run the server against the live instance, make a few tool
    calls (including one that errors, e.g. an unknown `job_id`), then read
    `~/.octobot-mcp/server.log` and confirm structured `INFO`-level per-call
    lines and the error's classified `error_class` are both present.

18. **`restart_octobot`/`wait_for_octobot_ready` — instance-lifecycle
    addendum, closing an evaluator-config-tuning validation gap (ADR-0010).**
    Not part of the original 14 milestones or any earlier backlog-gap
    addendum — a narrow, user-explicitly-authorized reversal of this
    project's own "reboot" out-of-scope exclusion (see requirements doc's
    amended "Out of scope" note). Exists because OctoBot caches a strategy's
    required-evaluators descriptor (`_TENTACLE_CONFIG_CACHE`,
    `web_interface/models/configuration.py`) and never invalidates it on
    `select_profile`/`update_tentacle_config`/`update_evaluator_config`/
    backtest start — only a full process restart clears it, so
    evaluator-level tuning currently can't be validated via backtest through
    this server at all. Traces to FR-30/FR-31/NFR-17 (requirements doc) and
    the tool spec's new "Instance lifecycle" section, which fully specifies
    both tools' contracts — implement against that section, not against
    ADR-0010's prose directly (the spec section is the authoritative,
    implementable restatement of ADR-0010's decisions).

    **Step 0 — DEFERRED by explicit user decision (not resolved this
    round):** this step originally called for triggering one real restart
    against the live instance to resolve open question #14 before
    implementation. The user chose to skip live verification for now rather
    than authorize triggering a real restart. Implement against a
    conservative, explicitly `[UNVERIFIED]`-flagged placeholder instead (see
    "Then implement" below) and leave open question #14 open — do not
    silently mark it resolved. Original step 0 text, preserved for whoever
    does eventually run it:

    <details>
    <summary>Original step 0 (not run)</summary>

    Trigger ONE real restart
    against the live instance (`http://192.168.178.100:5001`) and observe:
    (a) how long from `GET /wait_reboot?reboot=true`'s response until the
    HTTP server answers a plain `GET` again — this sets
    `wait_for_octobot_ready`'s default `timeout_seconds`; (b) whether a
    backtest run against a previously-tuned evaluator config, executed
    immediately once "ready," actually reflects the tuning, or whether some
    additional delay/confirmation step is needed beyond "HTTP reachable" —
    document whichever is true plainly in `wait_for_octobot_ready`'s
    docstring, per the tool spec's explicit warning not to overclaim this.
    Also confirm which existing lightweight `GET` route is safe/sane to poll
    with in the first seconds after a restart (doesn't itself hang or
    error oddly during OctoBot's own startup sequence).
    </details>

    **Deferred-step-0 substitute (what to actually do this round):** pick a
    conservative default `timeout_seconds` (e.g. 60s) and `poll_interval_seconds`
    (e.g. 2s) without live measurement, and a reasonable lightweight existing
    `GET` route to poll (e.g. an already-implemented, cheap read route such
    as `get_exchange_details` or similar — developer's call, document the
    choice and why). Mark BOTH the docstring and the requirements doc's open
    question #14 as still open/unverified — do not claim these defaults were
    measured. Do NOT trigger any real restart against the live instance
    during this implementation round; all tests must mock the HTTP layer.

    **Then implement, in this order:**
    1. New error class `RestartFailedError` in `octobot_mcp.errors` (before
       the module below that raises it, per this project's established
       ordering convention) — raised only when the restart HTTP call itself
       fails in a way indicating the restart was never scheduled (non-200,
       or an unexpected connection failure), never for the `confirm`/
       active-job refusals (those are structured non-exception returns, same
       convention as every other gated tool via `require_confirmation`).
    2. New module `octobot_mcp.tools.instance_lifecycle` (or similar —
       developer's call on the exact name, consistent with this project's
       existing per-domain module convention): `restart_octobot` (check
       order: `confirm` → `JobStore` active-job check across both
       `backtest_execution_lock`/`data_collection_execution_lock` → the one
       live call → risk-scaled refusal reason string sourced from the
       existing `list_profiles`/`get_profile` Tier-B scrape, per the tool
       spec) and `wait_for_octobot_ready` (bounded poll/backoff loop, using
       step 0's resolved default timeout and polling route).
    3. Register both tools (`register_all`), update `README.md`'s tool
       count/list, and add both to `server.py`'s `INSTRUCTIONS` constant
       (a new bullet in "Tool groups" plus, given the never-fully-safe
       nature of this capability per ADR-0010, a "Known OctoBot-side
       quirks" bullet stating the abrupt-kill/no-graceful-shutdown fact
       plainly, not softened).
    4. Tests: `confirm=false`/`true` refusal-vs-proceed for both a
       `SIMULATOR`-selected and `LIVE`-selected profile (asserting the
       reason string differs, not just that both refuse); an active-job
       refusal for each of `backtest`/`data_collection`; a non-200 restart
       response raising `RestartFailedError`; `wait_for_octobot_ready`
       timing out honestly against a mocked always-unreachable transport
       within its configured `timeout_seconds` (NFR-17); a successful
       `wait_for_octobot_ready` against a mocked transport that starts
       failing then starts succeeding, asserting `attempts`/`elapsed_seconds`
       are populated honestly, not fabricated.

    **Demo (this round, live-restart step deferred by user decision — see
    above): unit-test-only**, against mocked transports: `confirm=false`/
    `true` × `SIMULATOR`/`LIVE` refusal reason strings differing correctly;
    `restart_octobot` refusing outright while a mocked active job exists;
    a mocked non-200 restart response raising `RestartFailedError`;
    `wait_for_octobot_ready` reporting `ready: false` on a deliberately short
    `timeout_seconds` against a mock that never succeeds, and `ready: true`
    against a mock that starts failing then succeeds. **The real, live,
    end-to-end demo below remains outstanding** until someone explicitly
    authorizes triggering a real restart against a live instance:

    On a `SIMULATOR`-selected profile with no active job: tune an
    evaluator's config via `update_evaluator_config`, call
    `restart_octobot(confirm=true)`, poll `wait_for_octobot_ready` until
    `ready: true`, re-confirm the selected profile via `get_profile`, then
    run `start_backtest` and confirm the result actually reflects the tuned
    evaluator config — the end-to-end flow this addendum exists to enable.
    Also demo: `restart_octobot` refusing without `confirm=true` (showing
    the risk-scaled reason string differs for a `LIVE`-selected profile);
    `restart_octobot` refusing outright while a backtest job is running;
    `wait_for_octobot_ready` reporting `ready: false` on a deliberately short
    `timeout_seconds` before the instance has actually come back up.
