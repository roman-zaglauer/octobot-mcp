"""Instance-lifecycle tools: `restart_octobot`/`wait_for_octobot_ready`
(task 18, docs/tasklist.md item 18, ADR-0010, FR-30/FR-31/NFR-17).

A narrow, user-explicitly-authorized reversal of this project's own "reboot"
out-of-scope exclusion (see requirements doc's amended "Out of scope" note),
scoped to exactly one workflow: unblocking evaluator-config-tuning backtest
validation, which today silently produces stale results because OctoBot
caches a strategy's required-evaluators descriptor
(`_TENTACLE_CONFIG_CACHE`, `web_interface/models/configuration.py`) and
never invalidates it on `select_profile`/`update_tentacle_config`/
`update_evaluator_config`/backtest start -- only a full process restart
clears it. Implemented against
`docs/specs/octobot-mcp-tool-spec.md`'s "Instance lifecycle" section (the
authoritative, implementable restatement of ADR-0010's decisions), not
against the ADR's prose directly.

## Step 0 deferred by explicit user decision -- `[UNVERIFIED]` defaults

ADR-0010's own "Downstream artefacts" section called for triggering ONE
real restart against the live instance before finalizing
`wait_for_octobot_ready`'s default `timeout_seconds`/polling route. The
user explicitly declined to authorize a live restart for this
implementation round (see `docs/tasklist.md` item 18's "Deferred-step-0
substitute" note). Accordingly:
- `DEFAULT_TIMEOUT_SECONDS`/`DEFAULT_POLL_INTERVAL_SECONDS` below are
  conservative, UNMEASURED placeholders, not empirically-derived defaults.
- The polling route (`_POLL_ROUTE_PATH`, `GET /api/version`) was picked for
  being this project's own already-established "is OctoBot up at all"
  smoke-test route -- every prior milestone's own live-verification session
  used exactly this call to confirm the live test instance was reachable
  before doing anything else (see e.g. `octobot_mcp.tools.profiles`'s and
  `octobot_mcp.tools.data_collection`'s module docstrings, both of which
  cite `GET /api/version -> "OctoBot 2.1.1"` as their own first live check).
  It returns a bare version string, the cheapest possible authenticated-free
  read this API exposes, with no dependency on any exchange/profile state
  existing yet -- unlike `get_exchange_details` (`/api/first_exchange_details`),
  which depends on at least one exchange being configured and loaded, a
  precondition this module has no way to guarantee holds in the first few
  seconds after a restart. It was NOT re-verified this round to "behave
  sanely in the first seconds after a restart" (ADR-0010's own caveat) --
  no restart was triggered to check that against, live.
- Requirements doc open question #14 (end-to-end restart timing, and
  whether "HTTP reachable again" coincides with "the tentacle-config cache
  is actually clear") is deliberately left OPEN by this implementation --
  see `wait_for_octobot_ready`'s own docstring, which states this plainly
  rather than implying either has been verified.

All of this module's own tests mock the HTTP transport; none exercise a
live OctoBot instance, and no test or code path in this module (or anywhere
in this implementation round) ever issues a real `GET
/wait_reboot?reboot=true` against a live instance.
"""

import asyncio
import time
from typing import Any

import httpx2 as httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context

from octobot_mcp.context import get_client, get_job_store
from octobot_mcp.errors import OctoBotAPIError, RestartFailedError
from octobot_mcp.jobs import JobStore
from octobot_mcp.tools._shared import require_confirmation, with_error_handling
from octobot_mcp.tools.profiles import _fetch_and_parse_profiles

#: `[UNVERIFIED]` placeholder (open question #14) -- see this module's
#: docstring for why no live measurement backs these two values this round.
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_POLL_INTERVAL_SECONDS = 2.0

#: The lightweight `GET` route `wait_for_octobot_ready` polls -- see this
#: module's docstring for why this route, specifically, was chosen.
_POLL_ROUTE_PATH = "/api/version"

#: `GET /wait_reboot?reboot=true` (`Services/Interfaces/web_interface/
#: controllers/reboot.py`, confirmed against source, ADR-0010 Context): an
#: HTML route (not JSON, login-disabled-compatible per ADR-0005) that
#: renders a wait page then schedules `models.restart_bot(delay=2)` -- an
#: abrupt kill-and-re-exec, not OctoBot's graceful `OctoBot.stop()` shutdown.
_RESTART_ROUTE_PATH = "/wait_reboot"

#: `JobKind`s `restart_octobot`'s job-store check inspects (ADR-0010
#: Decision 4): both of `octobot_mcp.jobs.JobStore`'s independent locks,
#: mirroring ADR-0007's own "two independent locks" reasoning.
_GUARDED_JOB_KINDS = ("backtest", "data_collection")

#: The confirm-gating reason string used when the currently selected
#: profile's `profile_type` is `SIMULATOR` (ADR-0010 Decision 3) -- the
#: lighter of the two, since no live-money orders are at risk.
_SIMULATOR_RESTART_REASON = (
    "This triggers an abrupt OctoBot restart (kill-and-re-exec, NOT a "
    "graceful shutdown -- open tasks/connections are not cleanly stopped "
    "first) that briefly makes the instance unreachable while it re-execs. "
    "The currently selected profile is SIMULATOR, so no live-money orders "
    "are at risk."
)

#: The confirm-gating reason string used when the currently selected
#: profile's `profile_type` is `LIVE`, OR the profile-type check itself was
#: inconclusive (ADR-0010 Decision 3: fail toward the more detailed/cautious
#: message on an inconclusive check, never toward silently allowing).
_LIVE_OR_INCONCLUSIVE_RESTART_REASON = (
    "The currently selected profile is LIVE (or its type could not be "
    "reliably determined -- treated as the higher-risk case). Restarting "
    "stops OctoBot without its graceful shutdown sequence; open orders and "
    "stop-losses are reconciled from the exchange on reboot (OctoBot's own "
    "designed, changelog-hardened behavior for restarting during live "
    "trading -- see ADR-0010's Context for the source evidence), but the "
    "exact duration of the unmonitored window during the restart itself is "
    "not established."
)


async def _describe_restart_risk(client: httpx.AsyncClient) -> str:
    """Build `restart_octobot`'s confirm-gating reason string, risk-scaled by
    the currently selected profile's `profile_type` (ADR-0010 Decision 3).

    Reuses `octobot_mcp.tools.profiles`'s existing Tier-B `/profiles_selector`
    scrape (the same one `list_profiles`/`get_profile` already use) rather
    than a second adapter -- this module is a single, one-directional
    consumer of that scrape (not a symmetric two-module dependency), so it
    is imported directly from `octobot_mcp.tools.profiles` rather than moved
    into `_shared` (which this project reserves for helpers genuinely shared
    by two or more sibling modules -- see that module's own docstring for
    the precedent).

    Fails toward the more detailed/cautious `LIVE`-style message whenever
    the scrape itself is inconclusive -- raises any `OctoBotAPIError` (e.g.
    `ProfileScrapeContractBrokenError`), finds no selected profile at all
    (should never happen per that scrape's own fail-loud guarantees, but
    guarded anyway), or resolves to `profile_type: "UNKNOWN"` -- per
    ADR-0010's explicit instruction that "unknown" is treated as "assume
    higher risk" for MESSAGING purposes only. This never blocks the call
    outright: the same `confirm=true` gate always applies regardless of
    `profile_type` or whether this check even succeeded.
    """
    try:
        profiles = await _fetch_and_parse_profiles(client)
        selected = next((profile for profile in profiles if profile["is_selected"]), None)
        profile_type = selected["profile_type"] if selected is not None else "UNKNOWN"
    except OctoBotAPIError:
        profile_type = "UNKNOWN"

    if profile_type == "SIMULATOR":
        return _SIMULATOR_RESTART_REASON
    return _LIVE_OR_INCONCLUSIVE_RESTART_REASON


def _find_active_jobs(store: JobStore) -> list[dict[str, Any]]:
    """Return every `queued`/`running` `backtest`/`data_collection` job (ADR-0010 Decision 4).

    Both `JobKind`s are checked independently (mirroring
    `octobot_mcp.jobs.JobStore`'s own two-independent-locks design,
    ADR-0007) -- a job of either kind, in either non-terminal state, blocks
    a restart.
    """
    active: list[dict[str, Any]] = []
    for kind in _GUARDED_JOB_KINDS:
        for job in store.list_jobs(kind=kind):
            if job.state in ("queued", "running"):
                active.append({"job_id": job.job_id, "kind": job.kind, "state": job.state})
    return active


async def _trigger_restart(client: httpx.AsyncClient) -> None:
    """Issue the one live `GET /wait_reboot?reboot=true` call.

    Per the tool spec's "Instance lifecycle" section, ONLY a clean HTTP 200
    is treated as "restart scheduled" -- OctoBot's confirmed source
    behavior (ADR-0010 Context) renders the wait page and returns it to the
    client BEFORE the scheduled 2-second-delayed kill fires, so a 200 is a
    reliable "accepted" signal. Anything else -- a non-200 status, a
    connection-level failure, or a timeout -- raises `RestartFailedError`
    (never a false "triggered" result, and never the generic
    `OctoBotConnectionError`/`OctoBotTimeoutError`/`OctoBotClientError`/
    `OctoBotServerError` classification `octobot_mcp.client.request_json`
    would otherwise apply, per the tool spec's own explicit contract for
    this one route).
    """
    try:
        response = await client.request(
            "GET", _RESTART_ROUTE_PATH, params={"reboot": "true"}
        )
    except httpx.TimeoutException as exc:
        raise RestartFailedError(
            f"Restart request to OctoBot timed out: GET {_RESTART_ROUTE_PATH}"
        ) from exc
    except httpx.HTTPError as exc:
        raise RestartFailedError(
            f"Could not reach OctoBot to trigger a restart: GET "
            f"{_RESTART_ROUTE_PATH} ({exc})"
        ) from exc

    if response.status_code != 200:
        raise RestartFailedError(
            f"OctoBot returned status {response.status_code} for GET "
            f"{_RESTART_ROUTE_PATH} -- the restart was NOT scheduled."
        )


@with_error_handling
async def restart_octobot(ctx: Context, confirm: bool = False) -> Any:
    """Trigger a full OctoBot process restart. Confirm-gated (ADR-0003/ADR-0010).

    **This is the first tool in this project that can never be made fully
    safe or graceful (ADR-0010) -- stated plainly, not softened.** OctoBot's
    own restart mechanism (`GET /wait_reboot?reboot=true`, confirmed against
    `Drakkar-Software/OctoBot` source) is an ABRUPT kill-and-re-exec, never
    the graceful `OctoBot.stop()` shutdown -- no task cancellation, no
    explicit order handling occurs before the kill. Exchange-side orders
    are reconciled from the exchange on reboot (OctoBot's own designed,
    changelog-hardened behavior, even for live trading), but the exact
    duration of the unmonitored window during the restart itself is
    UNVERIFIED (open question #14) and this tool cannot measure or bound it.

    Check order (cheapest/safest first, ADR-0010 Decision 4), so a call
    that's about to be refused never makes an unnecessary live call:

    1. **`confirm` flag.** If not exactly `true`, no OctoBot call is made at
       all -- this scrapes the currently selected profile's `profile_type`
       (the same Tier-B scrape `list_profiles`/`get_profile` use) ONLY to
       build a risk-scaled reason string, then returns
       `require_confirmation`'s structured refusal (a normal return, not an
       error). The reason string is more detailed/cautious when
       `profile_type` is `LIVE` -- or the scrape itself is inconclusive,
       which fails toward the MORE cautious message, never toward silently
       allowing (ADR-0010 Decision 3) -- than when it is `SIMULATOR`. This
       is the SAME `confirm=true` gate regardless of `profile_type`: never a
       hard, non-bypassable block for `LIVE` profiles (see ADR-0010 for why
       an earlier draft's hard refusal didn't hold up against OctoBot's own
       changelog/source evidence that restart-during-live-trading is a
       designed, hardened scenario).
    2. **`JobStore` active-job check.** Refuses outright -- a structured
       non-exception return, naming every job -- if any `backtest`/
       `data_collection` job is `queued`/`running` (both of
       `octobot_mcp.jobs.JobStore`'s independent locks checked, ADR-0007's
       "two independent locks" precedent). Never warns-and-proceeds or
       auto-cancels (ADR-0010 Decision 4 explicitly rejects both): the
       caller must `cancel_job` or wait for completion first.
    3. **The one live call.** `GET /wait_reboot?reboot=true`. HTTP 200 ->
       `{"restart_triggered": true, "message": ...}` (below). Anything else
       raises `RestartFailedError` -- never a false "triggered" result.

    **Residual risk, accepted (ADR-0010):** a job could start in the narrow
    window between check 2 and the actual reboot call -- accepted given this
    project's single-MCP-client assumption (A3), not engineered away.

    Output on success: `{"restart_triggered": true, "message": "OctoBot
    restart scheduled (~2s delay); the server will be briefly unreachable.
    Call wait_for_octobot_ready() next, then re-confirm the active profile
    via get_profile()/list_profiles() before starting a backtest."}`.
    """
    client = get_client(ctx)

    if confirm is not True:
        reason = await _describe_restart_risk(client)
        return require_confirmation("restart_octobot", "the OctoBot instance", reason)

    active_jobs = _find_active_jobs(get_job_store(ctx))
    if active_jobs:
        job_descriptions = ", ".join(f"{job['kind']} job {job['job_id']} ({job['state']})" for job in active_jobs)
        return {
            "restart_triggered": False,
            "active_jobs": active_jobs,
            "message": (
                "restart_octobot was NOT performed: at least one active job "
                f"is still queued/running ({job_descriptions}). A restart "
                "would kill it uncleanly. Cancel it via cancel_job, or wait "
                "for it to finish, then retry."
            ),
        }

    await _trigger_restart(client)
    return {
        "restart_triggered": True,
        "message": (
            "OctoBot restart scheduled (~2s delay); the server will be "
            "briefly unreachable. Call wait_for_octobot_ready() next, then "
            "re-confirm the active profile via "
            "get_profile()/list_profiles() before starting a backtest."
        ),
    }


async def _is_octobot_reachable(client: httpx.AsyncClient) -> bool:
    """One poll attempt: `True` iff the HTTP server answered at all.

    Per the tool spec ("ready" means only "the HTTP server answered a
    request again"), ANY response -- including a non-2xx status -- counts
    as reachable; only a transport-level failure (timeout, connection
    refused, OctoBot mid-re-exec) counts as "not yet."
    """
    try:
        await client.request("GET", _POLL_ROUTE_PATH)
    except httpx.HTTPError:
        return False
    return True


@with_error_handling
async def wait_for_octobot_ready(
    ctx: Context,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
) -> Any:
    """Poll for OctoBot's HTTP server becoming reachable again after a restart.

    A bounded retry loop against `GET /api/version` (see this module's
    docstring for why this specific route was chosen) -- never blocks past
    `timeout_seconds`, and never raises for a timeout: that is a normal,
    honestly-reported result (NFR-3's "honest `null`/`false` over a
    fabricated guess" ethic), matching every other timeout-bounded loop in
    this project.

    **"Ready" means ONLY "the HTTP server answered a request again" --
    nothing more.** `[UNVERIFIED]`, open question #14, NOT resolved by this
    implementation (a live restart was explicitly not authorized this
    round -- see this module's docstring): whether that moment coincides
    with "OctoBot's tentacle-config cache
    (`_TENTACLE_CONFIG_CACHE`, ADR-0010's root-cause analysis) is actually
    clear" -- the very fix this capability exists to let you validate via
    backtest -- is NOT established. Do not assume a `ready: true` result
    means a subsequent backtest will reflect a just-tuned evaluator config;
    it may need additional, unquantified time beyond "HTTP reachable."

    `DEFAULT_TIMEOUT_SECONDS`/`DEFAULT_POLL_INTERVAL_SECONDS` (60s/2s) are
    conservative, UNMEASURED placeholders -- no real restart's end-to-end
    timing backs them (open question #14). Override both per-call if you
    have your own measurement.

    Not confirm-gated (a read-only poll). Returns `{"ready": true|false,
    "elapsed_seconds": float, "attempts": int}` -- both fields reflect this
    loop's REAL timing/attempt count, never fabricated.
    """
    client = get_client(ctx)
    start = time.monotonic()
    attempts = 0
    while True:
        attempts += 1
        if await _is_octobot_reachable(client):
            return {"ready": True, "elapsed_seconds": time.monotonic() - start, "attempts": attempts}
        elapsed = time.monotonic() - start
        if elapsed >= timeout_seconds:
            return {"ready": False, "elapsed_seconds": elapsed, "attempts": attempts}
        await asyncio.sleep(min(poll_interval_seconds, timeout_seconds - elapsed))


def register(mcp: MCPServer) -> None:
    """Register this module's tools onto `mcp` (called once from `octobot_mcp.tools.register_all`)."""
    mcp.tool()(restart_octobot)
    mcp.tool()(wait_for_octobot_ready)
