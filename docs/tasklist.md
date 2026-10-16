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

    **Step 0 — initially DEFERRED by explicit user decision, later
    COMPLETED in a follow-up live-testing round.** This step originally
    called for triggering one real restart against the live instance to
    resolve open question #14 before implementation. At implementation
    time, the user chose to skip live verification rather than authorize a
    real restart, so the tool shipped against a conservative
    `[UNVERIFIED]`-flagged placeholder (see "Then implement" below).
    **A later session ran this step for real**: two live restarts against
    the `SIMULATOR`-selected "Smart DCA v4 - Baseline (Simulator)" profile
    (never `LIVE`; state restored afterward), dense-polled throughout.
    Findings (also in `docs/adr/0010-octobot-restart-capability.md`'s
    "Post-shipping live verification" and
    `src/octobot_mcp/tools/instance_lifecycle.py`'s module/function
    docstrings): (a) timing resolved — reachable until ~2.1s post-trigger,
    genuinely unreachable ~6.3–6.7s, reachable again at ~8.5–8.9s total
    (n=2); (b) a real bug found and fixed — the original first-success-wins
    `wait_for_octobot_ready` loop returned `ready: true` in ~0.05s on both
    live trials, racing OctoBot's own confirmed ~2s pre-kill grace window
    and catching the OLD process, never observing the actual restart; fixed
    by requiring an observed failed poll before a later success counts as
    ready. (c) NOT resolved: whether "ready" coincides with the
    tentacle-config cache actually being clear — no backtest was run after
    a test restart to check. Open question #14's cache-clear half remains
    open. Original step 0 text, preserved for reference:

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

19. **`list_evaluators` — a third Tier-B scrape adapter, ADR-0009 reversed
    to GO by explicit user decision.**
    ADR-0009 originally recommended NO-GO on this exact tool (evaluator
    names/state are discoverable other ways; a third scrape adapter is a
    standing maintenance liability). The user later explicitly asked to
    build it anyway. Traces to FR-32/NFR-18 (requirements doc) and the
    `list_evaluators` subsection of the tool spec's "Trading config"
    section, which is the authoritative, implementable contract — implement
    against that section.

    **Live verification is ALREADY DONE (unlike prior addenda's own "Step 0"
    pattern) — do not re-derive it.** ADR-0009's "Implementation plan"
    section records a real fetch of `GET
    http://192.168.178.100:5001/advanced/evaluator_config` (OctoBot 2.1.1)
    performed before this tasklist item was written: activation state is
    confirmed server-rendered (not client-side-JS-applied), the exact row
    markup (`a.config-element` with `config-type`/`config-key`/
    `current-value` attributes) and the four category container `id`s
    (`ta-`/`social-`/`rt-`/`scripted-evaluator-config-root`) are recorded
    verbatim, and a real legitimate-zero-evaluators-in-one-category case
    was found (this instance's own "Scripted evaluators" section). A
    developer should still re-fetch the page once to build/validate a test
    fixture, but the design's structural assumptions are settled, not open.

    **Implement, in this order:**
    1. New error class `EvaluatorScrapeContractBrokenError` in
       `octobot_mcp.errors` (before the module below that raises it, per
       this project's established ordering convention), following
       `ProfileScrapeContractBrokenError`/`DataFileScrapeContractBrokenError`'s
       exact documentation convention.
    2. `list_evaluators()` — add to `octobot_mcp.tools.trading_config`
       alongside `update_evaluator_config` (same spec section; the tool
       spec places it there, not in a new module), or its own small module
       if the developer judges the BeautifulSoup-scrape machinery doesn't
       belong next to `trading_config.py`'s request/response helpers —
       developer's call, consistent with this project's existing
       module-boundary judgment (see `list_profiles`/`list_data_files` for
       precedent on where a Tier-B scrape adapter typically lives). Parses
       `GET /advanced/evaluator_config`'s HTML: selects `a.config-element`
       (or `a[config-type="evaluator_config"]`) rows within each of the
       four category containers, extracting `name` (`config-key` attribute),
       `enabled` (`current-value` attribute, parsed as bool), `type` (fixed
       per-container: `"technical_analysis"`/`"social"`/`"realtime"`/
       `"scripted"`). Fail-loud gate (NFR-18): raise
       `EvaluatorScrapeContractBrokenError` only if zero rows are found
       across ALL FOUR containers combined, or a container `id` is missing
       entirely — never for one legitimately-empty category.
    3. Register the tool (`register_all`), update `README.md`'s tool
       count/list and `server.py`'s `INSTRUCTIONS` constant, following this
       project's established practice of keeping both in sync with every
       new tool.
    4. Tests, fixture-based (mirroring `list_profiles`/`list_data_files`'s
       own test structure): a valid fixture with all four categories
       populated; a fixture mirroring the live instance's own real
       "Scripted evaluators has zero rows" case, asserting this does NOT
       raise; a broken fixture (zero rows everywhere, or a missing
       container), asserting `EvaluatorScrapeContractBrokenError` IS raised;
       correct `name`/`enabled`/`type` extraction against a fixture with a
       mix of `True`/`False` `current-value`s across multiple categories.

    **Demo:** agent calls `list_evaluators`, gets back every evaluator on
    the live instance with correct name/enabled/type, cross-checks one
    result's `name` by feeding it straight into
    `update_evaluator_config(patch={<that name>: not <its current enabled>})`
    and confirms the change — demonstrating the closed loop between the two
    tools this addendum exists to close.

20. **`get_portfolio_snapshot`/`get_evaluation_matrix` — a fourth and fifth
    Tier-B adapter, closing two gaps found via a live UI audit (ADR-0011).**
    Not one of the original 14 milestones or any earlier addendum —
    genuinely new scope, found this session by auditing OctoBot's own
    `/portfolio` and `/advanced/matrix` pages directly (no existing tool
    gives a current per-asset holdings breakdown or the live
    evaluator/timeframe/symbol/exchange signal matrix). Traces to
    FR-33/FR-34, NFR-19/NFR-20 (requirements doc) and the "Portfolio
    snapshot & evaluation matrix" section of the tool spec, which is the
    authoritative, implementable contract — implement against that section.

    **Live verification is ALREADY DONE (mirroring milestone 19's own
    "already done" pattern, not milestone 15/16/18's own "step 0, verify
    first" pattern) — do not re-derive it.** ADR-0011's Context section
    records, verbatim: the confirmed absence of any JSON API for either
    feature (four plausible routes tried directly against each page, all
    404 or an unchanged HTML body); the confirmed `/portfolio` DOM shape
    (`#portfoliosCard`'s `reference_market` attribute,
    `tr.symbol-holding` rows in `#holdings-table`, the rounded-cell-text
    vs. full-precision-tooltip distinction, and the one column with no
    tooltip at all); the confirmed `/advanced/matrix` DOM shape
    (`#matrixDataTable`'s plain positional `<td>` cells, the confirmed
    `<thead>` column order, and two real non-numeric cell cases: a literal
    `"None"` value and an empty `time_frame`); and the direct evidence
    ruling out a Socket.IO/Tier-C design for the matrix (no matrix-specific
    namespace or push ever observed; `advanced_matrix.js` initializes
    DataTables from HTML rows already present in the response, with no
    `ajax`/`data` option and no listener). A developer should still fetch
    both pages once more to build/validate test fixtures, but the design's
    structural assumptions are settled, not open — the only genuinely open
    items are requirements doc Q15 (multi-exchange tooltip format) and Q16
    (server-side pagination at scale), neither of which blocks shipping
    (see spec's "Open questions" section — both ship with documented,
    honest fallback behavior rather than blocking).

    **Implement, in this order:**
    1. New error classes `PortfolioScrapeContractBrokenError` and
       `EvaluationMatrixScrapeContractBrokenError` in `octobot_mcp.errors`
       (before the modules below that raise them, per this project's
       established ordering convention), following
       `ProfileScrapeContractBrokenError`/`DataFileScrapeContractBrokenError`/
       `EvaluatorScrapeContractBrokenError`'s exact documentation convention.
    2. `get_portfolio_snapshot()` — add to `octobot_mcp.tools.portfolio`
       alongside its existing Tier-A siblings (same domain — portfolio
       reads), or its own small module if the developer judges the
       BeautifulSoup-scrape machinery doesn't belong next to that module's
       request/response helpers — developer's call, consistent with this
       project's existing module-boundary judgment (see `list_evaluators`'s
       own precedent for this exact choice). Parses `#portfoliosCard`'s
       `reference_market` attribute and every `tr.symbol-holding` row in
       `#holdings-table`; for each of `total`/`available`/`locked_in_orders`,
       parses the cell's rounded display text AND attempts to parse its
       `title` tooltip as one or more `"<Exchange>: <amount>"` pairs,
       degrading to `exact: null, per_exchange: null` (never raising, never
       guessing a split) when the tooltip doesn't match cleanly;
       `value_in_reference_currency` is rounded-text-only (no tooltip exists
       for this column, confirmed — do not add an `exact` key for it). Gate:
       `PortfolioScrapeContractBrokenError` only on missing
       table/reference-currency structure, never on zero assets.
    3. `get_evaluation_matrix()` — add to `octobot_mcp.tools.trading_config`
       alongside `list_evaluators` (same general domain — evaluator state),
       or its own small module — developer's call, same precedent as above.
       Parses `#matrixDataTable`'s `<thead>` (asserting the exact confirmed
       column-name order before trusting positional extraction) and every
       body `<tr>`'s 5 cells positionally: `evaluator`, `value` (numeric,
       `null` for literal `"None"`, else passthrough raw string), `time_frame`
       (`null` when the cell is empty), `symbol`, `exchange`. Gate:
       `EvaluationMatrixScrapeContractBrokenError` only on a missing table or
       a `<thead>` mismatch (or a row with ≠5 cells), never on zero entries.
    4. Register both tools (`register_all`), update `README.md`'s tool
       count/list and `server.py`'s `INSTRUCTIONS` constant, following this
       project's established practice of keeping both in sync with every new
       tool.
    5. Tests, fixture-based (mirroring `list_profiles`/`list_data_files`/
       `list_evaluators`'s own test structure): for each adapter, a valid
       fixture built from this session's real captures (including the
       matrix's real `"None"`-value and empty-`time_frame` rows, and the
       portfolio's real single-exchange tooltip shape); a zero-rows-but-
       intact fixture for each, asserting this does NOT raise; a
       deliberately-broken fixture for each (missing table/attribute, or —
       for the matrix — a reordered/renamed `<thead>`), asserting the
       correct distinct exception IS raised; a portfolio fixture with an
       unparseable/malformed tooltip, asserting graceful `exact: null,
       per_exchange: null` degradation rather than a raised error or a
       guessed split.

    **Demo:** agent calls `get_portfolio_snapshot`, gets back every
    currently-held asset with its reference-currency value and
    available/locked breakdown in one call (replacing the
    three-tools-cross-referenced workaround this addendum exists to close);
    agent calls `get_evaluation_matrix`, gets back every active
    evaluator's current signal value per timeframe/symbol/exchange in one
    call (replacing the `export_logs`-grepping workaround this addendum
    exists to close), and cross-checks one row's `evaluator` name against
    `list_evaluators`'s own output to confirm the two tools agree on
    evaluator naming.

21. **Accounts & profile global config — ten new tools, closing six gaps
    found via a live UI audit (ADR-0012).**
    Not one of the original 14 milestones or any earlier addendum —
    genuinely new scope, found this session by auditing OctoBot's `/`
    (navbar), `/accounts`, and `/profile` pages directly. Traces to
    FR-35..FR-42, NFR-21..NFR-23 (requirements doc) and the "Accounts &
    profile global config" section of the tool spec, which is the
    authoritative, implementable contract — implement against that section.

    **Live verification is ALREADY DONE (mirroring milestones 19/20's own
    "already done" pattern, not the earlier addenda's "step 0, verify
    first" pattern) — do not re-derive it.** ADR-0012's Context section
    records, verbatim: the confirmed single underlying mechanism for all six
    features (`POST /config`'s `global_config`/`removed_elements`
    dispatch, read in full from `Drakkar-Software/OctoBot`'s
    `controllers/configuration.py`); the confirmed flat-key shapes for
    exchange add (`exchanges_<ex>_api-key`/`-secret`/`-password`/`-enabled`)
    and remove (`removed_elements: ["exchanges_<ex>"]`, confirmed to delete
    the whole nested node via `config_operations.py`'s full source); the
    confirmed Telegram/Web interface field names and the notification-routing
    field names (`notification_notification-type`/`-price-alerts`/`-trades`/
    `-trading-script-alerts`/`-other`); the confirmed per-profile currency
    fields (`crypto-currencies_<Currency>_enabled`/`-pairs`); and the
    real-trading toggle's exact confirmed body
    (`{"trader_enabled": bool, "trader-simulator_enabled": bool,
    "restart_after_save": true}`) and its confirmed restart side effect. A
    developer should still fetch `/accounts`/`/profile` once more to build/
    validate test fixtures (redacting any real secret value before
    committing a fixture), but the design's structural assumptions are
    settled, not open — the only genuinely open items are requirements doc
    Q17 (`exchange_type`'s exact enum strings), Q18 (the 7 un-configured
    services' exact field sets), and Q19 (resolved, recorded only for
    convenience) — none block shipping (see spec's "Open questions" section).

    **Implement, in this order:**
    1. New module `octobot_mcp.tools._config_scrape`: the generic
       `.config-element` Tier-B scrape helper
       (`scrape_config_elements(html, container_selector)`), typed per each
       element's own `data-type` attribute (`bool`/`number`/`text`/`list`) —
       the first scrape adapter in this project that doesn't hardcode a
       per-field list, since every field here already self-describes.
    2. New error classes in `octobot_mcp.errors`:
       `ConfigElementScrapeContractBrokenError` (shared by all four new
       read tools below) and `ExchangeCredentialsUpdateRejectedError` is
       reused, not duplicated, from `exchanges.py` for `add_exchange`
       (same underlying route/failure mode) — defined/imported before the
       modules below that raise them, per this project's established
       ordering convention.
    3. `set_real_trading_enabled` — add to `octobot_mcp.tools.profiles` or
       its own small module (developer's call — it's conceptually closer to
       profile/trading-state than to exchanges; it shares no code with
       either existing module beyond the generic `require_confirmation`/
       `JobStore` helpers already used by `restart_octobot`). Reuses the
       `JobStore` active-job check pattern verbatim from
       `instance_lifecycle.py`'s `restart_octobot`.
    4. `add_exchange`/`remove_exchange`/`update_exchange_trading_mode`/
       `get_exchanges` — add to `octobot_mcp.tools.exchanges` alongside
       `update_exchange_credentials` (same domain, same route,
       `add_exchange` literally shares its underscore-guard helper).
    5. `get_interfaces`/`update_interface_config` — new module
       `octobot_mcp.tools.interfaces` (the secret-suffix-based conditional
       gating, NFR-22/23, is distinctive enough to warrant its own module
       rather than folding into `exchanges.py`).
    6. `get_notification_routing`/`update_notification_routing` — add to the
       same `interfaces.py` module (same page, same container-scrape
       helper, small enough not to warrant a third module).
    7. `get_profile_currencies`/`update_profile_currencies` — add to
       `octobot_mcp.tools.profiles` (profile-domain data, even though it
       writes through `/config` rather than `/profiles_management/*`).
    8. Register all ten tools (`register_all`), update `README.md`'s tool
       count/list and `server.py`'s `INSTRUCTIONS` constant, following this
       project's established practice of keeping both in sync with every
       new tool — including a new "Known OctoBot-side quirks" bullet for
       `set_real_trading_enabled`'s forced restart, mirroring
       `restart_octobot`'s own existing bullet.
    9. Tests, mirroring `list_profiles`/`list_evaluators`'s own test
       structure for the four read tools (valid fixture, zero-elements-but-
       intact-container fixture, broken fixture); `confirm=false`/`true` for
       every gated tool including `update_interface_config`'s conditional
       case (non-credential patch proceeds without `confirm`; credential
       patch refuses without it); the `JobStore` active-job refusal for
       `set_real_trading_enabled`; the underscore-in-exchange-name local
       refusal shared between `update_exchange_credentials`/`add_exchange`.

    **Demo:** agent calls `get_exchanges`/`get_interfaces`/
    `get_notification_routing`/`get_profile_currencies` and sees the live
    instance's real configured state in each domain; agent attempts
    `set_real_trading_enabled(enabled=True)` without `confirm`, gets a
    refusal naming both the restart and the real-money consequence, and
    (on a test/non-financially-sensitive instance only — **never demo the
    confirmed path against this project's own live trading instance**)
    confirms the gated tools' refusal-vs-proceed behavior against a mocked
    transport instead; agent calls `update_interface_config` with a
    non-credential patch (e.g. `services_web_port`) and sees it proceed
    without `confirm`, then with a credential patch and sees it refuse
    without `confirm`.

22. **Automations — four new tools, reusing the tentacle-config mechanism
    (ADR-0013).**
    Not one of the original 14 milestones or any earlier addendum —
    genuinely new scope, found via the same live UI audit as milestone 21,
    auditing OctoBot's `/automations` page. Traces to FR-43..FR-45, NFR-24
    (requirements doc) and the "Automations" section of the tool spec,
    which is the authoritative, implementable contract — implement against
    that section.

    **Live verification is ALREADY DONE** — ADR-0013's Context section
    records, verbatim, the complete `/automations`/`/automations_edit_details`
    route source (read in full from `Drakkar-Software/OctoBot`'s
    `controllers/automation.py`), confirming: `save`/read both reuse
    `models.update_tentacle_config`/`get_tentacle_config_and_edit_display`
    (the exact mechanism `update_tentacle_config`/`get_tentacle_config`
    already wrap); `start`/`factory_reset` both call
    `models.restart_global_automations()`, which can raise
    `InvalidAutomationConfigError`; and the zero-automations response shape
    this session captured live. The explicit scope-down evaluation (read-only
    now, defer writes) was considered and rejected in the ADR itself — do
    not re-litigate it; implement all four tools. The only genuinely open
    item is Q20 (the populated-automation nested schema shape, unobserved
    since this profile has zero automations configured) — not blocking,
    since `get_automations`/`update_automations` are passthrough-only by
    design (NFR-24) and don't need that shape to be correct.

    **Implement, in this order:**
    1. New error class `AutomationConfigInvalidError` in
       `octobot_mcp.errors` (before the module below that raises it, per
       this project's established ordering convention) — wraps OctoBot's
       own `InvalidAutomationConfigError` message, raised by both
       `start_automations` and `reset_automations_to_default` (the two
       actions that call `models.restart_global_automations()`).
    2. New module `octobot_mcp.tools.automations`: `get_automations`
       (`GET /automations_edit_details`, passthrough, NFR-24),
       `update_automations` (`POST /automations?action=save`, raw-body
       passthrough), `start_automations` (`POST /automations?action=start`,
       confirm-gated under ADR-0013's new "activates autonomous, ongoing
       behavior" category, plus the same `JobStore` active-job check
       `set_real_trading_enabled`/`restart_octobot` already use), and
       `reset_automations_to_default` (`POST /automations?action=factory_reset`,
       confirm-gated under the ordinary irreversible-deletion criterion,
       same `JobStore` check).
    3. Register the four tools (`register_all`), update `README.md`'s tool
       count/list and `server.py`'s `INSTRUCTIONS` constant.
    4. Tests: the zero-automations fixture from ADR-0013's own capture for
       `get_automations`; `update_automations` passthrough (input patch
       appears unchanged in the sent body); `confirm=false`/`true` for both
       `start_automations` and `reset_automations_to_default`, asserting
       the refusal reason text differs between them (Decision 3 vs. the
       ordinary destructive-action criterion, not the same boilerplate
       reused); a mocked `InvalidAutomationConfigError` response from
       OctoBot asserting it's classified as `AutomationConfigInvalidError`,
       not a generic `CONFIG_UPDATE_FAILED`, for both tools; the `JobStore`
       active-job refusal for both.

    **Demo:** agent calls `get_automations` on the live instance and sees
    the real `automations_count: 0` response; agent calls
    `update_automations` with a trivial patch (e.g. `{"automations_count":
    0}`, a genuine no-op given the current state) and sees it echoed back
    successfully; agent attempts `start_automations`/
    `reset_automations_to_default` without `confirm`, gets refusals whose
    reason text visibly differs between the two (one naming "autonomous,
    ongoing behavior," the other naming "irreversible" — **do not demo the
    confirmed path against this project's own live instance's real profile
    unless a developer has explicitly set up a disposable test profile
    first**, since `factory_reset` is genuinely irreversible and `start`
    changes live automation-running state).
