"""Automations -- a fourth tentacle-config-shaped surface, not a new
rules-engine subsystem (milestone 22, docs/tasklist.md item 22, ADR-0013,
FR-43..FR-45, NFR-24).

Per ADR-0013's Context (full route source read from `Drakkar-Software/
OctoBot`'s `Services/Interfaces/web_interface/controllers/automation.py`):
"Automations" is OctoBot's own internal representation of a pseudo-tentacle
(`bot_automation.Automation`, `tentacle_type="automation"`) whose `save`/
read actions reuse the EXACT SAME `models.update_tentacle_config`/
`models.get_tentacle_config_and_edit_display` mechanism
`octobot_mcp.tools.tentacles.update_tentacle_config`/`get_tentacle_config`
already wrap for every other tentacle -- there is no new subsystem to
design around. Two genuinely new actions this project hasn't needed before,
`start` and `factory_reset`, both call `models.restart_global_automations()`,
which can raise `InvalidAutomationConfigError` if the saved config doesn't
parse (confirmed: the route catches exactly this exception and returns it
as an HTTP 400).

## What was, and was NOT, verified live this session

ADR-0013's live investigation was deliberately read-only: navigating to
`/automations` (zero automations configured on the live instance) triggered
exactly one real network request, `GET /automations_edit_details` -- no
form was ever submitted, so NONE of `action=save`/`start`/`factory_reset`
was ever actually exercised against the live instance. `get_automations`'s
own request/response shape is therefore `[V-live]`-confirmed (this module's
own zero-automations fixture, reproduced in `tests/test_tools_automations.py`,
is the real captured payload); `update_automations`/`start_automations`/
`reset_automations_to_default`'s exact JSON response shapes on success are
`[V-source]` only (confirmed against the route's control flow, not its
exact final `flask.jsonify(...)` call, which ADR-0013's own quoted source
excerpt truncates) -- this module follows the tool spec's own stated output
shapes exactly (its authoritative, implementable contract per this
project's established practice, not ADR prose directly), passing through
whatever OctoBot actually returns rather than inventing/narrowing a
response shape it hasn't observed (NFR-24, the same NFR-8 discipline this
project already applies to backtest report payloads).

`InvalidAutomationConfigError` classification (`AutomationConfigInvalidError`
below) is the one failure mode ADR-0013 confirmed precisely: an HTTP 400
response from `POST /automations` (any `action`) means specifically this.
Any OTHER non-2xx status is classified as the generic
`AutomationConfigUpdateRejectedError` (`octobot_mcp.errors`'s own
docstring has the full reasoning for keeping these two classes distinct).

## Confirm-gating: two distinct categories (ADR-0013 Decisions 3/4)

`start_automations` is gated under a NEW, explicitly-named category this
project hasn't used before -- "activates autonomous, ongoing behavior
beyond this one call" -- since it is the one action in this addendum that
lets OctoBot take further actions on live bot events with no further
per-action confirmation, a materially different risk shape than a static
config write. `reset_automations_to_default` is gated under the ORDINARY
irreversible-deletion criterion (ADR-0003), the same category as
`clear_*_history`/`delete_data_file` -- it permanently discards whatever
the user configured, with no undo. The spec requires these two refusal
reason strings to differ meaningfully, not share boilerplate -- see
`_START_REFUSAL_REASON`/`_RESET_REFUSAL_REASON` below, and
`tests/test_tools_automations.py`'s own dedicated assertion that they do.

Both `start_automations`/`reset_automations_to_default` also restart
automations (confirmed against the route source: `restart = True` is set
for both `start`/`factory_reset`), so both reuse
`octobot_mcp.tools.instance_lifecycle.restart_octobot`'s own `JobStore`
active-backtest/data-collection-job check -- VERBATIM, per this project's
established per-module duplication convention (see e.g. `tentacles.py`'s/
`trading_config.py`'s own independently-duplicated `_request_raw`/
`_extract_error_text` pairs), not imported from that module.

No `from __future__ import annotations` here: see
`octobot_mcp.tools._shared.with_error_handling`'s docstring for why
postponed annotations would silently break this module's Context
injection.
"""

from typing import Any

import httpx2 as httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context

from octobot_mcp.client import request_json
from octobot_mcp.context import get_client, get_job_store
from octobot_mcp.errors import (
    AutomationConfigInvalidError,
    AutomationConfigUpdateRejectedError,
    OctoBotConnectionError,
    OctoBotMalformedResponseError,
    OctoBotTimeoutError,
)
from octobot_mcp.jobs import JobStore
from octobot_mcp.tools._shared import require_confirmation, with_error_handling

_AUTOMATIONS_PATH = "/automations"
_AUTOMATIONS_EDIT_DETAILS_PATH = "/automations_edit_details"

#: `JobKind`s `start_automations`/`reset_automations_to_default`'s job-store
#: check inspects (ADR-0013 Decisions 3/4) -- the exact same pair
#: `octobot_mcp.tools.instance_lifecycle._GUARDED_JOB_KINDS` checks, since
#: both tools here also restart automations, mirroring that module's own
#: "two independent locks" reasoning (ADR-0007/ADR-0010). Duplicated here
#: verbatim rather than imported, per this project's established
#: per-module convention (see this module's own docstring).
_GUARDED_JOB_KINDS = ("backtest", "data_collection")

#: `start_automations`'s confirm-gating reason string (ADR-0013 Decision 3's
#: new "activates autonomous, ongoing behavior beyond this one call"
#: category) -- deliberately NOT phrased as "destructive" or "irreversible,"
#: which would be inaccurate; see this module's docstring.
_START_REFUSAL_REASON = (
    "This activates the currently saved automation configuration, which "
    "can then take real actions autonomously on bot events going forward, "
    "with no further per-action confirmation from this server. Review "
    "get_automations()'s current saved config before confirming."
)

#: `reset_automations_to_default`'s confirm-gating reason string -- the
#: ORDINARY irreversible-deletion criterion (ADR-0003), the same category as
#: `clear_*_history`/`delete_data_file`, deliberately phrased differently
#: from `_START_REFUSAL_REASON` above (the spec requires these two reasons
#: to differ meaningfully, not share boilerplate).
_RESET_REFUSAL_REASON = (
    "This permanently discards the current saved automation configuration "
    "back to OctoBot's default and cannot be undone."
)


def _find_active_jobs(store: JobStore) -> list[dict[str, Any]]:
    """Return every `queued`/`running` `backtest`/`data_collection` job.

    Verbatim copy of `octobot_mcp.tools.instance_lifecycle._find_active_jobs`
    (ADR-0010 Decision 4) -- both `JobKind`s are checked independently, a job
    of either kind in either non-terminal state blocks both
    `start_automations` and `reset_automations_to_default`, since both also
    restart automations (confirmed against the route source, ADR-0013
    Context).
    """
    active: list[dict[str, Any]] = []
    for kind in _GUARDED_JOB_KINDS:
        for job in store.list_jobs(kind=kind):
            if job.state in ("queued", "running"):
                active.append({"job_id": job.job_id, "kind": job.kind, "state": job.state})
    return active


async def _request_raw(
    client: httpx.AsyncClient, method: str, path: str, **kwargs: Any
) -> httpx.Response:
    """Perform one request without decoding JSON, classifying only transport failures.

    Mirrors `octobot_mcp.tools.tentacles._request_raw`/
    `octobot_mcp.tools.trading_config._request_raw` (independently
    duplicated in each module by this project's established convention, not
    shared): `octobot_mcp.client.request_json` can't be reused here because
    a failed `update_automations`/`start_automations`/
    `reset_automations_to_default` call needs OctoBot's own raw response
    body/status code (to distinguish `AutomationConfigInvalidError`'s
    confirmed HTTP 400 signal from any other non-2xx status), not just a
    generic classified exception with no body content. Only transport-level
    failures are classified here; each call site classifies its own status
    code.
    """
    try:
        return await client.request(method, path, **kwargs)
    except httpx.TimeoutException as exc:
        raise OctoBotTimeoutError(f"Request to OctoBot timed out: {method} {path}") from exc
    except httpx.HTTPError as exc:
        raise OctoBotConnectionError(f"Could not reach OctoBot: {method} {path} ({exc})") from exc


def _extract_error_text(response: httpx.Response) -> str:
    """Best-effort extraction of OctoBot's own error text from a non-2xx response.

    Mirrors every other tool module's identically-named, independently
    duplicated helper (`tentacles.py`/`exchanges.py`/`trading_config.py`):
    tries a JSON body first, falling back to the raw response text, since
    this project has repeatedly confirmed OctoBot's `/config`-family routes
    sometimes send a real JSON string body and sometimes send raw unquoted
    text despite an unconditional `Content-Type: application/json` header.
    `/automations`'s own exact failure-body encoding was not observed live
    this session (see this module's docstring) -- this helper's
    JSON-first-then-raw-text fallback handles either case without guessing
    which one applies.
    """
    try:
        body = response.json()
    except ValueError:
        return response.text.strip() or f"HTTP {response.status_code} with an empty body"
    if isinstance(body, dict) and "text" in body:
        return str(body["text"])
    return str(body)


@with_error_handling
async def get_automations(ctx: Context) -> Any:
    """Fetch the current profile's automation configuration and its schema.

    Maps to `GET /automations_edit_details` [V-live, ADR-0013 Context: the
    real zero-automations response this session's live investigation
    captured]. Returns OctoBot's own `{"config": {...}, "displayed_elements":
    {...}}` response unchanged (NFR-24 -- passthrough-only, the same NFR-8
    discipline this project already applies to backtest report payloads):
    this tool does not attempt to understand, flatten, or re-model the
    nested event/condition/action schema a populated (`automations_count >
    0`) config would contain -- an agent reads `displayed_elements`'s own
    `schema` key to know what a valid `update_automations` `patch` looks
    like, the same way a human uses OctoBot's JSON-editor widget.

    `config.automations_count` is always present (confirmed live, `0` on a
    fresh profile); the nested per-automation schema shape for
    `automations_count > 0` was not observed this session (open question
    #20) -- this tool does not reshape it either way.

    Read-only, not confirm-gated. Errors: generic `OctoBotClientError`/
    `OctoBotServerError`/`OctoBotMalformedResponseError` (a plain read, no
    domain-specific failure mode -- `request_json` classifies all three).
    """
    return await request_json(get_client(ctx), "GET", _AUTOMATIONS_EDIT_DETAILS_PATH)


@with_error_handling
async def update_automations(ctx: Context, patch: dict[str, Any]) -> Any:
    """Update the current profile's automation configuration. Passthrough-only.

    Maps to `POST /automations?action=save`, `patch` sent as the raw JSON
    body, unwrapped [V: confirmed `automations.js`'s `onEditorChange ->
    updateTentacleConfig(newValue, update_url)` sends the edited value
    directly, no wrapper key -- the same convention
    `octobot_mcp.tools.tentacles.update_tentacle_config` already follows].
    `patch` must match whatever shape `get_automations()`'s own
    `displayed_elements.data.elements[0].schema` currently describes; this
    tool does not validate `patch` against that schema itself (OctoBot's own
    route does).

    Output: `{"automations_updated_config": <OctoBot's own JSON response
    body for this call, unchanged>}` -- per the tool spec's own stated
    output shape (NFR-24: this tool never invents/narrows that body's
    content, since its exact shape was not observed live this session -- see
    this module's docstring).

    Not confirm-gated (ADR-0013 Decision 2: mutates saved config only, does
    not itself start anything -- the same ADR-0003 classification already
    applied to `update_tentacle_config`). Raises
    `AutomationConfigUpdateRejectedError` on any non-2xx response.
    """
    client = get_client(ctx)
    response = await _request_raw(
        client,
        "POST",
        _AUTOMATIONS_PATH,
        params={"action": "save"},
        json=patch,
    )
    if response.status_code >= 400:
        raise AutomationConfigUpdateRejectedError(
            f"OctoBot rejected update_automations: {_extract_error_text(response)}"
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise OctoBotMalformedResponseError(
            "OctoBot returned a 2xx response with a non-JSON body for "
            f"POST {_AUTOMATIONS_PATH}?action=save."
        ) from exc
    return {"automations_updated_config": body}


def _active_job_refusal(result_key: str, tool_name: str, active_jobs: list[dict[str, Any]]) -> Any:
    """Build the structured, non-exception job-store-active-job refusal (ADR-0010 Decision 4).

    Mirrors `octobot_mcp.tools.instance_lifecycle.restart_octobot`'s own
    inline refusal shape exactly, parameterized over the one result key
    (`"started"`/`"reset"`) that differs between `start_automations`/
    `reset_automations_to_default`.
    """
    job_descriptions = ", ".join(f"{job['kind']} job {job['job_id']} ({job['state']})" for job in active_jobs)
    return {
        result_key: False,
        "active_jobs": active_jobs,
        "message": (
            f"{tool_name} was NOT performed: at least one active job is "
            f"still queued/running ({job_descriptions}). This action also "
            "restarts automations, which would interrupt it uncleanly. "
            "Cancel it via cancel_job, or wait for it to finish, then retry."
        ),
    }


def _classify_automations_failure(tool_name: str, response: httpx.Response) -> Exception:
    """Classify a non-2xx `POST /automations` response for `start_automations`/
    `reset_automations_to_default` (ADR-0013 Decision 5).

    HTTP 400 is the one signal ADR-0013 confirmed precisely means OctoBot's
    own `InvalidAutomationConfigError` (the route catches exactly this
    exception and returns it as a 400) -- classified as
    `AutomationConfigInvalidError`, distinct from any other non-2xx status
    (`AutomationConfigUpdateRejectedError`). See `octobot_mcp.errors`'s own
    docstring for the full reasoning.
    """
    error_text = _extract_error_text(response)
    if response.status_code == 400:
        return AutomationConfigInvalidError(
            f"OctoBot rejected {tool_name}: the saved automation config is invalid: {error_text}"
        )
    return AutomationConfigUpdateRejectedError(f"OctoBot rejected {tool_name}: {error_text}")


@with_error_handling
async def start_automations(ctx: Context, confirm: bool = False) -> Any:
    """Activate (start) the current profile's saved automation configuration.

    Confirm-gated under ADR-0013's new "activates autonomous, ongoing
    behavior beyond this one call" category (Decision 3) -- NOT the ordinary
    destructive/credential-exposure/live-trading criteria every other gated
    tool in this project uses, since this is the one action in this
    addendum that lets OctoBot take further real actions on bot events going
    forward with no further per-action confirmation from this server.

    Check order (cheapest/safest first, mirroring
    `octobot_mcp.tools.instance_lifecycle.restart_octobot`'s own order):

    1. **`confirm` flag.** If not exactly `true`, no OctoBot call is made at
       all -- returns `require_confirmation`'s structured refusal (a normal
       return, not an error) naming this tool's own distinct reason,
       `_START_REFUSAL_REASON` (recommending the caller review
       `get_automations()`'s current saved config first).
    2. **`JobStore` active-job check.** Refuses outright -- a structured
       non-exception return, naming every job -- if any `backtest`/
       `data_collection` job is `queued`/`running` (verbatim copy of
       `restart_octobot`'s own check, ADR-0010 Decision 4): starting
       automations also restarts them (confirmed against the route source,
       ADR-0013 Context), which would interrupt an active job uncleanly.
    3. **The one live call.** `POST /automations?action=start` ->
       `models.restart_global_automations()` [V].

    Output on success: `{"started": true}`. Raises
    `AutomationConfigInvalidError` (NOT a generic
    `AutomationConfigUpdateRejectedError`) if OctoBot reports the saved
    config is structurally invalid (confirmed: an HTTP 400 response means
    specifically this); any other non-2xx response raises
    `AutomationConfigUpdateRejectedError`.
    """
    if confirm is not True:
        return require_confirmation("start_automations", "current profile", _START_REFUSAL_REASON)

    active_jobs = _find_active_jobs(get_job_store(ctx))
    if active_jobs:
        return _active_job_refusal("started", "start_automations", active_jobs)

    client = get_client(ctx)
    response = await _request_raw(client, "POST", _AUTOMATIONS_PATH, params={"action": "start"})
    if response.status_code >= 400:
        raise _classify_automations_failure("start_automations", response)
    return {"started": True}


@with_error_handling
async def reset_automations_to_default(ctx: Context, confirm: bool = False) -> Any:
    """Factory-reset the current profile's saved automation configuration to OctoBot's default.

    Confirm-gated under the ORDINARY irreversible-deletion criterion
    (ADR-0003) -- the same category as `clear_*_history`/`delete_data_file`,
    NOT `start_automations`'s new Decision-3 category: this permanently
    discards whatever automations the user configured, and OctoBot itself
    does not version/back up the prior config.

    Check order (identical structure to `start_automations`'s own, since
    this action also restarts automations per the confirmed route source):

    1. **`confirm` flag.** If not exactly `true`, no OctoBot call is made at
       all -- returns `require_confirmation`'s structured refusal naming
       this tool's own distinct reason, `_RESET_REFUSAL_REASON` (explicitly
       different wording from `_START_REFUSAL_REASON` -- the spec requires
       these two reasons to differ meaningfully, not share boilerplate).
    2. **`JobStore` active-job check.** Identical to `start_automations`'s
       own (verbatim copy of `restart_octobot`'s check, ADR-0010 Decision 4).
    3. **The one live call.** `POST /automations?action=factory_reset` ->
       `models.reset_automation_config_to_default()` then
       `models.restart_global_automations()` [V].

    Output on success: `{"reset": true}`. Raises
    `AutomationConfigInvalidError` (same class as `start_automations`, since
    the same restart call can raise it) if OctoBot reports the saved config
    is structurally invalid; any OTHER non-2xx response (e.g. the reset
    step's own failure, independent of the following restart call) raises
    `AutomationConfigUpdateRejectedError`.
    """
    if confirm is not True:
        return require_confirmation("reset_automations_to_default", "current profile", _RESET_REFUSAL_REASON)

    active_jobs = _find_active_jobs(get_job_store(ctx))
    if active_jobs:
        return _active_job_refusal("reset", "reset_automations_to_default", active_jobs)

    client = get_client(ctx)
    response = await _request_raw(client, "POST", _AUTOMATIONS_PATH, params={"action": "factory_reset"})
    if response.status_code >= 400:
        raise _classify_automations_failure("reset_automations_to_default", response)
    return {"reset": True}


def register(mcp: MCPServer) -> None:
    """Register this module's tools onto `mcp` (called once from `octobot_mcp.tools.register_all`)."""
    mcp.tool()(get_automations)
    mcp.tool()(update_automations)
    mcp.tool()(start_automations)
    mcp.tool()(reset_automations_to_default)
