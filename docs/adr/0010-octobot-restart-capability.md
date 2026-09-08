# ADR-0010: An OctoBot-restart capability (`restart_octobot` / `wait_for_octobot_ready`), scoped to unblock evaluator-config-tuning backtest validation

## Status
Accepted (Decision 3 revised after initial drafting — see note below).
Downstream artefacts written: FR-30/FR-31/NFR-17/open question #14
(requirements doc), the "Instance lifecycle" section (tool spec), and
tasklist item 18 (implementation task, step 0 blocking on a live-restart
timing measurement).

## Context

**Revision note:** Decision 3 originally proposed a hard, non-`confirm`-
bypassable refusal for `LIVE` profiles. After this draft was reviewed,
additional research into OctoBot's own public `CHANGELOG.md` and `trading`
package source materially changed that decision's risk assessment. Decision
3 below records the revised decision and the evidence behind it; Alternatives
considered records the original hard-refusal design as the rejected
alternative.

The requirements doc's "Out of scope (v1)" section currently reads: "Any
UI-plumbing route explicitly excluded by the user (about/terms/robots.txt/
static media/**reboot**/onboarding/community/webhook/feedback)"
(`docs/requirements/octobot-mcp-requirements.md`, line 119, emphasis added).
This ADR is the user's own explicit, narrow reversal of that exclusion — not
a silent contradiction of it. The user has since said: "I do not mind
Octobot restarts. It is quite fast," specifically in service of one genuinely
new workflow: validating evaluator-level config tuning (e.g.
`DoubleMovingAverageTrendEvaluator`'s periods, or a strategy's
`required_evaluators` set) via backtest, which today silently produces
stale results because of an OctoBot-side caching bug that only a full
process restart clears. The original "reboot" exclusion covered the generic
UI-plumbing route as a category the user didn't want this server touching at
all; this ADR authorizes exactly one narrow capability inside that category,
for exactly this workflow, and records the reversal here rather than
quietly editing the exclusion away.

**Root cause this exists to fix** (verified against `Drakkar-Software/OctoBot`
source, master branch, this session): `web_interface/models/configuration.py`
`get_cached_tentacle_config(klass)` (line 404) reads a module-level
`_TENTACLE_CONFIG_CACHE` dict populated once per key and invalidated only by
`clear_tentacle_config_cache()` (line 113), which is called only from the
cloud-sync-profile-refresh path — never from `select_profile()`,
`update_tentacle_config()`, `update_evaluator_config()`, or backtest start.
`_add_strategy_requirements_and_default_config` (line 580) computes a
strategy's required-evaluators descriptor from `get_startup_tentacles_config()`
(frozen at process boot) plus this stale cache. A full process restart clears
both (fresh module import, fresh startup-config read); nothing this server
can do over the existing HTTP API clears it without one. By contrast, plain
uncached reads (`get_tentacle_config`) and `select_profile()`'s own
`_update_edited_tentacles_config()` call are not stale — this is exactly why
`update_trading_config`/`update_evaluator_config` (FR-21/FR-28) already work
live without a restart, while evaluator-*requirements* tuning does not.
Independent backtesting's own `profile_id`-scoped path
(`web_interface/models/backtesting.py:191`) refreshes `tentacles_setup_config`
activation state from the profile's files, but does not touch
`_TENTACLE_CONFIG_CACHE` — it does not fully solve this by itself.

**Post-shipping field observation — this may be a broader pattern than one
cache (not yet root-caused in source, flagged honestly rather than silently
folded into the fix above):** after this capability shipped, the user
reported the same class of symptom — a `profile_id`-targeted backtest not
reflecting a config change — on a completely different mechanism,
`DailyTradingMode.required_strategies_min_count` (a trading-mode-level
field, not an evaluator-level one, so NOT the same `_TENTACLE_CONFIG_CACHE`
code path traced above), confirmed across two separate bots/datasets.
Order-execution parameters (position sizing, stop-loss/take-profit) were
NOT affected — only strategy-COMPOSITION parameters (which/how-many
evaluators or strategies must agree) were. This suggests OctoBot's backtest
engine may have a broader limitation with `profile_id`-scoped composition
parameters than the one specific cache this ADR fixes — `restart_octobot`
is confirmed to fix the evaluator-requirements case, but is **not**
confirmed to fix every strategy-composition tuning case. Nobody has traced
`required_strategies_min_count`'s own code path in source yet; treat this as
an open empirical finding, not a resolved root cause, until someone does.
See `server.py`'s `INSTRUCTIONS` (Known OctoBot-side quirks) for the
user-facing version of this caveat.

**What the restart route actually does** (verified this session):
`GET /wait_reboot?reboot=true[&trading_delay_info=&next=&onboarding=]`
(`packages/tentacles/Services/Interfaces/web_interface/controllers/reboot.py`)
renders an HTML wait page, then schedules `models.restart_bot(delay=2)`.
`restart_bot` (`octobot/commands.py:372`, reached via `octobot_api.py:144`)
either `os.execl`s in place (script launch) or `subprocess.Popen`s a new
process and `os._exit(0)`s (packaged launch) — **an abrupt kill-and-reexec,
not the graceful `OctoBot.stop()` shutdown** at `octobot/octobot.py:211`
(which cleanly stops `evaluator_producer`/`exchange_producer`/community auth
and would handle open orders/tasks in an orderly way). No task cancellation,
no explicit order handling occurs before the kill. This is an HTML route
(login-disabled-compatible, matching ADR-0005), not JSON — a caller gets a
rendered page back, not a structured response. The HTTP server and Socket.IO
transport this MCP server depends on both go down and come back up; there is
no socket left to watch through the outage, so ADR-0002's watcher pattern
cannot apply here by construction.

**Safety facts, stated plainly rather than undersold:**
- No graceful shutdown happens before the kill. Exchange-side orders are not
  cancelled by a restart (they live on the exchange, not in OctoBot's
  process), but there is an **unmeasured window** during the restart where
  OctoBot is not monitoring or managing them, before it resyncs on reboot.
  No source evidence of state corruption was found, but also no evidence of
  a clean pre-restart flush — this is an honestly-unresolved risk, not a
  cleared one.
- This server's own `JobStore` (ADR-0002/ADR-0007) enforces "one backtest at
  a time" / "one data collection at a time" via its own locks. A restart
  while one of those jobs is genuinely running on the OctoBot side would
  kill it uncleanly, and this server's watcher would simply see its
  Socket.IO connection die — it has no way to distinguish that from any
  other connectivity failure.
- This is, by construction, **the first tool in this project that can never
  be made fully safe or graceful** — every other confirm-gated tool
  (`delete_profile`, `convert_profile_to_live`, `clear_*_history`,
  `update_exchange_credentials`) performs one scoped, well-understood
  OctoBot-side operation with a defined success/failure outcome this server
  can verify. `restart_octobot` cannot offer that: it briefly removes the
  entire instance from observability, and its "success" signal is only ever
  "the HTTP server answered a GET before dying," never "the restart
  completed cleanly."

**Additional evidence on live-trading restart safety** (from
`Drakkar-Software/OctoBot`'s `CHANGELOG.md`, fetched via `gh api
repos/Drakkar-Software/OctoBot/contents/CHANGELOG.md`, and
`packages/trading/octobot_trading/exchanges/traders/trader.py`, added to
this ADR after the user pushed back on the initial draft's framing):
- Restart-from-web-interface is a long-standing, intentional, advertised
  feature, not an incidental side door: a "New features" changelog entry
  reads "Update / Restart / Stop Octobot from Web interface", and a
  bug-fix-hardening entry reads "Can now properly stop and restart OctoBot
  from web interface" — meaning this was explicitly tested and fixed as a
  first-class capability.
- A more recent entry, **"[StopLoss] Now always recreate stop losses when
  restarting on real trading"**, is direct evidence OctoBot's own
  maintainers anticipate and have hardened for restarts occurring *during*
  live/real trading, not just as a dev-only convenience — and that an
  earlier version of this exact scenario (a real stop-loss order existing
  across a restart) needed and received a real fix.
- `trader.py`'s `create_order` (`loaded=True` path) confirms every OctoBot
  boot loads/resyncs existing orders **from the exchange**
  (`order.is_from_this_octobot = False; await order.initialize()`) —
  exchange-side order state is designed to be reconciled from the exchange
  as a normal part of every startup, not something a restart silently loses
  track of.

This evidence does not overturn everything in "Safety facts" above: there is
still no evidence `OctoBot.stop()`'s graceful shutdown runs before the kill,
and the duration of the window between kill and full reconnection/
reconciliation remains unmeasured. But it does establish that **restarting
while live trading is active is a known, designed-for, and specifically
hardened OctoBot scenario** — not a gap this MCP server would be uniquely
exposing. Critically, the underlying mechanism a human triggers by clicking
"Restart" in OctoBot's own web UI while a `LIVE` profile is trading is the
exact same `GET /wait_reboot?reboot=true` route this tool calls — this
session's research found no evidence OctoBot gates that UI action behind any
profile-type check of its own. This materially weakens the original
Decision 3 argument that an MCP-triggered restart is riskier than an
equivalent human-triggered one; see Decision 3 for the revised conclusion.

**Not yet determined this session (flagged, not guessed):**
- `[UNVERIFIED]` How long a full restart actually takes end-to-end. Could
  not be safely probed without triggering a real restart, which was out of
  scope for this design task.
- `[UNVERIFIED]` Whether any config layer besides `_TENTACLE_CONFIG_CACHE`
  (e.g. per-parameter values, as opposed to the requirements descriptor)
  also needs a boot to refresh.
- `[UNVERIFIED]` Whether "the HTTP server answers a request again" is the
  same moment as "the tentacle-config cache is actually clear and a
  subsequent backtest reflects the tuned config" — these are plausibly two
  different points in OctoBot's own startup sequence, and conflating them
  would make `wait_for_octobot_ready` report a false-positive "ready" for
  the very workflow this capability exists to support.

## Decision

### 1. Go, with the capability scoped narrowly to the stated workflow
Given the explicit user request and the absence of any other way to clear
`_TENTACLE_CONFIG_CACHE`, this ADR decides **go**. The capability is scoped
to exactly what the workflow needs — trigger a restart, and find out when
the instance is reachable again — not a general "OctoBot process control"
surface. Per the architecture principles' precedence order, reliability/
operability concerns (tier 2) shape every constraint below; this is not a
"ship it because the user is fine with it" rubber stamp.

### 2. `confirm=true` gating, via the existing shared helper (ADR-0003)
`restart_octobot` requires `confirm=true`, refusing via the same
`require_confirmation(action, target, reason)` helper every other gated tool
uses (NFR-7) — no bespoke logic. Comparing this to the two existing
gating rationales it sits closest to:
- Like `convert_profile_to_live` (gated because it is "the closest confirmed
  signal to enabling real-money trading"), `restart_octobot` interacts with
  the live-trading blind-spot window described above — but unlike
  `convert_profile_to_live`, which acts on one named profile the caller
  explicitly chose, a restart's blind-spot window is **process-wide and
  invisible to the calling agent**: it can affect live orders the agent has
  no way to know about (e.g. a human operating OctoBot's own web UI
  concurrently — the same cross-channel scenario ADR-0007/A6 already
  identified for its own job-store guard, but with materially higher
  stakes here).
- Like `delete_profile`/`clear_*_history` (gated because the action is not
  something an instantly-correctable follow-up call can undo), a restart's
  interruption of any currently-running job or in-flight order-management
  cycle cannot be undone by calling `restart_octobot` again.

The live-trading blind-spot window **alone** justifies confirm-gating by
this project's own established bar (ADR-0003's stated purpose: protecting
against an ambiguous or mistaken instruction before a human notices).
Decision 3 addresses whether `LIVE` profiles need anything beyond this
standard gate.

### 3. `LIVE` profiles: a standard `confirm=true` gate with a risk-scaled reason string — not a hard refusal
An earlier draft of this ADR proposed a hard, non-`confirm`-bypassable
refusal whenever the currently-selected profile's `profile_type` is `LIVE`.
After further research into OctoBot's own `CHANGELOG.md` and `trading`
package (see Context's "Additional evidence" above), that decision is
revised: `restart_octobot` on a `LIVE` profile is gated by the **same**
`confirm=true` mechanism as every other case, via `require_confirmation`
(ADR-0003) — no non-bypassable block.

**Why the hard refusal doesn't hold up:** the argument for it was that an
agent's `confirm=true` couldn't be informed consent for a risk invisible to
it — a process-wide blind-spot window with no evidence OctoBot handles
restarting during live trading well. The new evidence directly contradicts
the "no evidence it's handled" premise: restart-during-live-trading is an
advertised, changelog-hardened OctoBot scenario ("Update / Restart / Stop
Octobot from Web interface"; "[StopLoss] Now always recreate stop losses
when restarting on real trading"), with exchange-side order reconciliation
as designed, normal boot behavior (`trader.py`). A human operating OctoBot's
own web UI can already trigger this exact route
(`GET /wait_reboot?reboot=true`) against a `LIVE` profile with, as far as
this session's research found, no equivalent profile-type gate on OctoBot's
own side. Refusing outright at the MCP layer — for a risk OctoBot's own
maintainers have judged acceptable enough to ship as a headline feature and
that real users routinely accept — would make this server categorically
more conservative than the system it wraps, for a risk the agent's
`confirm=true` can now be meaningfully informed about (see reason string
below), not one it's structurally blind to.

**What still justifies risk-scaled treatment, not identical treatment:**
two facts from the original research remain true and unresolved by the new
evidence:
- No evidence `OctoBot.stop()`'s graceful shutdown (stopping
  `evaluator_producer`/`exchange_producer`/community auth cleanly) runs
  before the kill — the changelog evidence is about *recovery* (reconciling
  state on boot), not about an orderly *shutdown* beforehand.
- The duration of the window between kill and full reconnection/
  reconciliation remains `[UNVERIFIED]` (unmeasured this session, same as
  before).

So, mirroring `convert_profile_to_live`'s own precedent of a distinct, more
serious confirmation reason string for its higher-stakes case: when the
selected profile's `profile_type` is `LIVE`, the refusal-without-`confirm`
message names this specifically — e.g. "The currently selected profile is
LIVE. Restarting stops OctoBot without its graceful shutdown sequence; open
orders and stop-losses are reconciled from the exchange on reboot (OctoBot's
own designed behavior), but the exact duration of the unmonitored window
during the restart itself is not established. Pass confirm=true to
proceed." — versus the lighter, generic reason string used for a
`SIMULATOR` profile. Both are `confirm=true`-bypassable; only the
explanatory text differs.

**Fail-open on an inconclusive profile-type check, with the more severe
message:** if the profile-type scrape itself fails
(`ProfileScrapeContractBrokenError`, ADR-0006/NFR-12) or its result is
otherwise inconclusive, `restart_octobot` no longer refuses outright over
this — there is nothing left that a hard refusal was categorically
protecting against — it falls back to the `LIVE`-style reason string
(treating "unknown" as "assume higher risk" for messaging purposes only),
and proceeds normally once `confirm=true` is supplied.

**What this revision does not resolve:** neither `[UNVERIFIED]` item named
in Context — end-to-end restart timing, and whether "HTTP-reachable again"
(what `wait_for_octobot_ready` measures) coincides with "the
tentacle-config cache is actually clear" — is touched by this evidence.
Both stand exactly as before; this revision is scoped to the
`LIVE`-vs-`SIMULATOR` gating question only.

### 4. Job-store interaction: refuse, don't warn, don't auto-cancel
`restart_octobot` checks `JobStore` for any `backtest` or `data_collection`
job in state `queued` or `running` (both locks, independently — mirroring
ADR-0007's own two-independent-locks reasoning) and **refuses outright** if
one exists, again as a structured non-exception return, naming the job(s)
and instructing the caller to `cancel_job` or wait for completion first.
Two alternatives were rejected:
- **Warn but proceed** — rejected: a warning the calling agent can silently
  ignore defeats the purpose; this project's own precedent
  (`require_confirmation`) is to actually block, not merely annotate.
- **Auto-cancel the active job, then proceed** — rejected: silently
  destroying/interrupting a job the caller never asked to cancel is a worse
  surprise than a clear refusal that puts the choice back in the caller's
  hands, consistent with this project's preference for explicit,
  caller-driven action over server-side inference of intent.

**Check ordering** (cheapest/safest first, to avoid unnecessary live calls
on a call that's about to be refused anyway): (1) `confirm` flag — in-memory,
free; (2) `JobStore` active-job check — in-memory, free; (3) profile-type
scrape — the one live call, performed last, only once the first two checks
already pass.

**Residual risk, accepted:** a job could start in the window between check
(2) and the actual reboot call. Given A3 (single MCP client, ADR-0001) this
window is narrow and the risk is accepted rather than engineered away with
additional locking machinery; revisit if multi-client support is ever added
(NFR-11's own extension point).

### 5. Two separate tools, not one blocking call and not a `JobStore` job
`restart_octobot(confirm=true)` and `wait_for_octobot_ready(timeout_seconds?,
poll_interval_seconds?)` are two independent tools, deciding fresh rather
than force-fitting ADR-0002's job/watcher pattern:

- **Why not reuse `JobStore`/the watcher pattern:** that pattern exists to
  track a *graduated* progress signal (0–100%) sourced from OctoBot's own
  Socket.IO push, which requires a live connection to the very instance
  that's about to go down. A restart's readiness signal is binary
  (reachable / not yet), sourced by this server polling its own client, not
  something OctoBot pushes. Forcing this into `JobStore` would mean
  fabricating a `progress_percent`/`eta_seconds` for an operation with none
  of that granularity — directly contradicting this project's established
  honesty bar (NFR-3/NFR-8: never invent a number OctoBot doesn't actually
  provide).
- **Why not one tool that blocks internally until ready:** it would
  conflate two different failure semantics — "did the restart request get
  accepted" vs. "is the instance back up" — into one call, making a
  wait-phase timeout indistinguishable from a failed restart trigger
  (undermining NFR-4-style honest error classification). It also risks the
  *calling MCP client's* own request timeout for a call that could
  legitimately take tens of seconds, a failure mode entirely outside this
  server's control. Returning fast and letting the caller decide its own
  poll cadence matches this project's async philosophy in spirit, even
  without reusing `JobStore` literally.
- **`restart_octobot(confirm=true)`:** after checks 2–4 pass, issues one
  `GET /wait_reboot?reboot=true` using the existing shared `httpx2` client
  (a new lightweight raw-status helper, not `request_json` — this route
  returns HTML, not JSON, so no body parsing is attempted). Per the
  confirmed `restart.py` source, OctoBot renders the wait page and returns
  it to the client *before* the scheduled 2-second-delayed kill fires, so
  an HTTP 200 is expected to be a reliable "restart accepted" signal;
  classify anything else (4xx/5xx via the existing `OctoBotClientError`/
  `OctoBotServerError`, a connection-level failure via the existing
  `OctoBotConnectionError`/`OctoBotTimeoutError`) as a genuine failure, not
  a false-success. Returns immediately:
  `{"restart_triggered": true, "message": "OctoBot restart scheduled (~2s
  delay); the server will be briefly unreachable. Call
  wait_for_octobot_ready() next, then re-confirm the active profile via
  get_profile()/list_profiles() before starting a backtest."}`.
- **`wait_for_octobot_ready(timeout_seconds=60, poll_interval_seconds=2)`:**
  a bounded retry-with-backoff loop against a lightweight existing read
  route (`[UNVERIFIED]` — no dedicated health-check endpoint was confirmed
  this session; the implementer must pick and document the cheapest
  confirmed-working GET, e.g. `get_exchange_details`'s route, and verify it
  behaves sanely — doesn't itself hang — in the first seconds after a
  restart). Never blocks past `timeout_seconds`; on timeout, returns
  `{"ready": false, "elapsed_seconds": <float>, "attempts": <int>}` rather
  than raising, matching the "honest `null`/`false` over a fabricated
  guess" ethic NFR-3 already established for ETA. On success:
  `{"ready": true, "elapsed_seconds": <float>, "attempts": <int>}`.
  **Flagged risk, not resolved by this ADR:** "ready" here means "the HTTP
  server answered," which is `[UNVERIFIED]` to be the same moment as "the
  tentacle-config cache is actually clear." The tool's own docstring must
  say this plainly rather than imply a false guarantee; see Risks.

### 6. No bundled higher-level workflow tool
The end-to-end flow (`select_profile` → `restart_octobot` →
`wait_for_octobot_ready` → re-confirm via `get_profile`/`list_profiles` →
`start_backtest`) is left as separate primitives for the calling agent to
chain, matching this project's general preference for small composable
tools over bundled workflows (established throughout every prior
milestone), and explicitly **not** mirrored on `compare_strategies`:
`compare_strategies` was bundled because it has a genuine technical need —
one `job_id` tracking a combined progress/ETA formula across N children that
an agent polling N separate job ids could not itself compute without
duplicating server logic. No equivalent combined-state need exists across
these five steps: each already has (or, per this ADR, will have) its own
independent output, there is nothing to combine into one number, and the
five steps carry materially different risk profiles (only one is
confirm-gated) that a single bundled call's semantics would blur — a single
`confirm=true` should not be read as implicitly authorizing four
heterogeneous OctoBot operations at once.

### 7. New exception class
Following this project's one-class-per-domain-failure convention
(`octobot_mcp.errors`):
- **`RestartFailedError(OctoBotAPIError)`** — raised only when the `GET
  /wait_reboot?reboot=true` call itself fails in a way that indicates the
  restart was never actually scheduled (a non-200 response, or a
  connection-level failure that isn't the expected post-render
  disconnection pattern). The `confirm=false` refusal (with its
  profile-type-scaled reason string, decision 3) and the active-job refusal
  (decision 4) are both structured non-exception returns, not this
  exception — consistent with
  `require_confirmation`'s existing convention that a known, expected,
  avoidable-by-different-input condition is a normal result, never a raised
  error.
  `wait_for_octobot_ready` raises nothing new: a timeout is a normal,
  honestly-reported `{"ready": false, ...}` result, not an error.

## Consequences

### Positive
- Unblocks the one workflow this ADR exists for: an agent can tune
  evaluator config, restart, and get a backtest that actually reflects the
  change — currently impossible through this server at all.
- The narrow scoping (risk-scaled confirm-gating, job-store refusal) keeps
  the blast radius of this admittedly-never-fully-safe capability as small
  as the stated use case allows, without blocking that use case, and
  without making this server more conservative than OctoBot's own
  advertised, hardened support for restarting during live trading
  (Context's "Additional evidence").
- No changes to `JobStore`, the ETA module, or the watcher pattern are
  needed — this capability is additive and self-contained, touching only a
  new module plus one new error class, mirroring how cheaply ADR-0007 was
  able to extend the existing job model when a genuine technical fit
  existed (and, symmetrically, demonstrating restraint here where one
  doesn't).

### Negative
- This is the first tool in the project whose safety cannot be fully
  engineered — only scoped. Even for a `SIMULATOR` profile with no active
  job, the abrupt kill-and-reexec (no graceful shutdown) is inherent to
  OctoBot's own restart mechanism, not a limitation of this server's design
  that a better implementation could remove. This must be stated in the
  tool's own docstring, not softened.
- `wait_for_octobot_ready`'s readiness signal is weaker than callers may
  assume (HTTP-reachable vs. cache-actually-cleared) — an `[UNVERIFIED]` gap
  this ADR does not resolve, only surfaces (see Risks).
- Restarting while a `LIVE` profile is selected is permitted with only a
  stronger confirmation prompt (decision 3), not a hard block — this relies
  on the calling agent (and whatever human reviews its plan) actually
  reading and weighing the risk-scaled reason string rather than reflexively
  re-issuing the call with `confirm=true`, the same general limitation
  ADR-0003 already accepts for every other gated tool, now extended to a
  process-wide action for the first time.
- The revised Decision 3 rests partly on an inference — that OctoBot's own
  web UI does not gate this route by profile type either — which was not
  exhaustively confirmed (the UI's client-side behavior around the restart
  button was not traced this session, only the server-side route and the
  changelog/source evidence that the underlying restart mechanism itself is
  hardened for live trading). If OctoBot's own UI does impose some
  additional guard this server doesn't replicate, this revision would be
  slightly more permissive than the system it wraps rather than exactly
  matching it.

## Risks
| Risk | Severity | Likelihood | Mitigation |
|---|---|---|---|
| Abrupt kill-and-reexec with no graceful shutdown is inherent to OctoBot's own restart mechanism and cannot be made safe by this server | High | Certain (whenever the tool is used) | Confirm-gated (decision 2) with a risk-scaled reason string for `LIVE` profiles (decision 3); OctoBot's own changelog/source evidence (Context) shows restart-during-live-trading is a designed, hardened scenario with exchange-side reconciliation on boot, which narrows but does not eliminate this risk — stated plainly in the tool description as an accepted, unremovable trade-off, not engineered around, because it cannot be |
| `wait_for_octobot_ready`'s "ready" (HTTP-reachable) may not coincide with "the tentacle-config cache is actually clear," the very fix this capability exists to validate | Medium | Medium — `[UNVERIFIED]` | Implementer must empirically verify end-to-end timing against the live instance before shipping (a real restart, safe to trigger once during implementation, unlike during this design session); document the distinction plainly in the tool's docstring regardless of the outcome |
| A backtest/data-collection job starts in the narrow window between the job-store check and the actual reboot call | Low | Low (single-client assumption, A3) | Accepted residual risk given A3; revisit if multi-instance/multi-client support is ever added (NFR-11) |
| `/wait_reboot`'s HTML response shape/status code changes in a future OctoBot version | Low | Low | Only the HTTP status code is inspected, never the HTML body — minimal coupling surface; document the last-verified OctoBot version (2.1.1) alongside the tool, per this project's existing scrape-adapter convention |
| Live-trading blind-spot window duration is unmeasured, so an agent confirming `restart_octobot` against a `LIVE` profile is confirming a real but unquantified risk | Medium | Medium (whenever a caller confirms restart against a `LIVE` profile) | `[UNVERIFIED]`; the `LIVE`-specific confirmation reason string (decision 3) names this gap explicitly rather than implying the risk is fully characterized; a future measurement task (trigger one real restart during a real-trading session) would let this be stated as a concrete bound instead of an open unknown |
| Decision 3's inference that OctoBot's own web UI does not gate the restart button by profile type was not traced client-side this session | Low | Low | Flagged in Consequences; an implementer with access to the live instance's `/accounts` or dashboard UI can confirm directly before shipping, but does not block this ADR's decision, which stands on the server-side route/changelog evidence alone |

## Alternatives considered
- **A graceful "stop cleanly, then relaunch" route instead of the abrupt
  restart.** Rejected: not implementable — no confirmed OctoBot HTTP route
  triggers the graceful `OctoBot.stop()` shutdown independently of the
  abrupt `restart_bot()` path; this is not a design choice this server can
  make differently, only a limitation of the wrapped API.
- **A hard, non-`confirm`-bypassable refusal for `LIVE` profiles** (this
  ADR's own original Decision 3, in an earlier draft). Rejected upon further
  research: OctoBot's own `CHANGELOG.md` ("Update / Restart / Stop Octobot
  from Web interface"; "[StopLoss] Now always recreate stop losses when
  restarting on real trading") and `trading` package source (exchange-side
  order reconciliation on every boot, `trader.py`) show that restarting
  during live trading is a known, designed-for, specifically-hardened
  OctoBot scenario that a human can already trigger via the same route with
  no equivalent gate found on OctoBot's own side — making a categorical
  MCP-side block inconsistent with the risk posture of the system being
  wrapped, for a risk the agent's `confirm=true` can now be meaningfully
  informed about via a risk-scaled reason string (decision 3) rather than
  one it was structurally blind to.
- **One tool that blocks internally until ready (bounded timeout/backoff
  inside `restart_octobot` itself).** Rejected per decision 5: conflates two
  failure semantics, risks the calling MCP client's own timeout, and
  contradicts this project's preference for small, composable tools.
- **Reusing `JobStore`/the ADR-0002 watcher pattern for readiness
  tracking.** Rejected per decision 5: no live socket exists to watch
  through the outage, and fabricating a graduated progress/ETA signal for a
  binary reachable/not-yet-reachable condition would violate this project's
  established honesty bar (NFR-3/NFR-8).
- **A single bundled `restart_and_rebacktest`-style workflow tool.**
  Rejected per decision 6: no genuine combined-state need (unlike
  `compare_strategies`), and it would blur `confirm`'s per-action scope
  across four heterogeneous operations.
- **Warn-and-proceed, or auto-cancel, on an active `JobStore` job instead of
  refusing.** Rejected per decision 4: both replace an explicit,
  caller-driven choice with server-side inference of intent, which this
  project consistently avoids elsewhere (e.g. `require_confirmation` itself
  blocks rather than warns).

## Downstream artefacts a "go" would need (not written by this ADR)
Mirroring how ADR-0007/ADR-0008 each named their own follow-on work:
- `docs/requirements/octobot-mcp-requirements.md`: new FR entries for
  `restart_octobot`/`wait_for_octobot_ready`, an NFR for the bounded-timeout
  behavior (mirroring NFR-5's timeout-policy framing), an amendment to the
  "Out of scope" line quoted in Context recording this narrow reversal (not
  a deletion of the line — the general "reboot" exclusion still holds
  outside this one capability), and a new open question for the two
  `[UNVERIFIED]` items above (end-to-end restart timing; HTTP-reachable vs.
  cache-actually-cleared).
- `docs/specs/octobot-mcp-tool-spec.md`: a new "Instance lifecycle" section
  with both tools' full input/output/error shapes, following this ADR's
  decisions 3–5 verbatim.
- `docs/tasklist.md`: a new milestone, structured like milestone 15's own
  "Step 0, verify against the live instance first" pattern — specifically,
  triggering one real restart to resolve both `[UNVERIFIED]` timing
  questions before finalizing `wait_for_octobot_ready`'s default
  `timeout_seconds`/polling endpoint choice — followed by implementation of
  `restart_octobot`, `wait_for_octobot_ready`, and `RestartFailedError`
  (added to `octobot_mcp/errors.py` before the new module that raises it,
  per this project's established ordering convention).
