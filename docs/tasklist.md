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

14. **Error handling / resilience polish pass.**
    Sweep every tool for NFR-4/NFR-5 compliance (timeout present,
    error classified, no raw stack traces reaching the MCP client);
    add the structured per-call and per-job-transition logging from the
    spec's Observability section; run a full smoke-test checklist against
    the live instance covering every tool at least once. This is the
    exit criterion for calling v1 done.
