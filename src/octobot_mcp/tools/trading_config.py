"""`update_trading_config` -- closing a backlog gap (milestone 13.5, docs/tasklist.md item 13.5).

`update_trading_config` was added to `docs/specs/octobot-mcp-tool-spec.md`
(FR-21, the "Trading config" section) and to ADR-0003's non-gated-tools
rationale during milestone 5's review, to close a dangling reference -- but
it was never assigned to a tasklist milestone, so it was never implemented,
until item 13.5 added it explicitly as a standalone addendum.

This is its own module rather than a new function bolted onto an existing
one: the spec gives "Trading config" its own top-level section, distinct
from both "Exchanges & credentials" (`exchanges.py`, which happens to reuse
the exact same `POST /config` route for `update_exchange_credentials`'s
`global_config` branch, but is a different request-body key/OctoBot config
domain) and "Portfolio & trading" (`portfolio.py`, which is `api/trading.py`
-- an entirely unrelated OctoBot API surface despite the similarly-named
domain). Reusing `exchanges.py`'s `_request_raw`/`_extract_error_text` was
considered and rejected in favor of this module's own copies, matching this
project's established convention (see `tentacles.py`'s module docstring):
every tool module that needs the raw non-`request_json` request path
duplicates this small helper pair independently rather than sharing one
across domains.

## Source verification this session (not trusted from the spec's `[V]` tag alone)

Read `constants.py` and `controllers/configuration.py::config()`
(`Services/Interfaces/web_interface/`, `Drakkar-Software/OctoBot`, master
branch) directly from GitHub:

- `TRADING_CONFIG_KEY = "trading_config"` -- the exact literal request-body
  key (confirmed, `constants.py`).
- The route body confirms the spec's claim about the model function calls:
  `if constants.TRADING_CONFIG_KEY in request_data and
  request_data[constants.TRADING_CONFIG_KEY]: success = success and
  models.update_tentacles_activation_config(request_data[constants.
  TRADING_CONFIG_KEY])` -- the exact same `models.
  update_tentacles_activation_config` function the `TENTACLES_CONFIG_KEY`
  branch also calls (the `EVALUATOR_CONFIG_KEY` branch calls it too, with an
  extra `deactivate_others` argument neither other branch passes). So
  `patch` is submitted as-is, unwrapped and un-nested, under the top-level
  `"trading_config"` request-body key -- confirmed, not assumed.
- The success response is built as `response = {"evaluator_updated_config":
  ..., "trading_updated_config": request_data[constants.TRADING_CONFIG_KEY],
  "tentacle_updated_config": ..., "global_updated_config": ...,
  "removed_elements": ...}`, returned via `util.get_rest_reply(flask.
  jsonify(response))` -- real JSON, HTTP 200 -- confirming the spec's
  `trading_updated_config` echo key exactly. Since this tool only ever sends
  `{"trading_config": patch}` (never `tentacle_config`/`evaluator_config`/
  `global_config`/`removed_elements`), every sibling key in that envelope is
  always the route's own empty-string default for an absent key; this tool
  extracts and returns only `trading_updated_config`, per the spec's stated
  `{"trading_updated_config": ...}` output shape, not the whole envelope.

## A genuine spec correction: the documented error message is never actually sent for this tool's own failures

The spec's "Errors" line claims the `CONFIG_UPDATE_FAILED` failure carries
"OctoBot's `err_message` string." Reading the full route body shows this is
only true when the *`global_config`* or *`removed_elements`* branches are
the ones that fail: `err_message` is a local variable initialized to `""` at
the top of `config()`, and it is ONLY ever reassigned inside those two
branches (`success, err_message = models.update_global_config(...)`). The
`trading_config`/`tentacle_config`/`evaluator_config` branches only ever
touch the `success` boolean, never `err_message`. Since this tool sends only
`trading_config`, ANY failure it can trigger leaves `err_message` at its
untouched initial value of `""` -- so OctoBot's own HTTP 500 response body
for a rejected `update_trading_config` call is *always* `flask.jsonify("")`,
a real but completely uninformative empty JSON string, never a substantive
message. Confirmed further by reading `models.update_tentacles_activation_config`
itself (`models/configuration.py`): it catches every internal exception,
logs it server-side, and returns a bare `False` -- there is no exception
text available to surface even indirectly. `TradingConfigUpdateRejectedError`
reflects this honestly (its message names whatever text was actually
extracted, which is an empty string for every failure this tool can ever
observe) rather than implying a substantive message OctoBot never actually
sends for this specific request shape.

Not confirm-gated (ADR-0003): mutates strategy/tentacle activation config,
does not destroy data, expose credentials, or enable live trading -- no
`confirm` parameter exists on this tool at all.

No `from __future__ import annotations` here: see
`octobot_mcp.tools._shared.with_error_handling`'s docstring for why
postponed annotations would silently break this module's Context injection.

## `update_evaluator_config` (task 16, docs/tasklist.md item 16, FR-28)

Added after v1 shipped, closing a gap surfaced while answering a user
question about evaluator CRUD. Shares this module rather than getting its
own (per the tasklist's own reasoning): same `POST /config` route, same
underlying `models.update_tentacles_activation_config` function as
`update_trading_config` above, just a different top-level request-body key
plus one extra argument that tool's branch doesn't pass.

### Source verification this session, resolving both `[UNVERIFIED]` items the
tool spec and requirements doc (FR-28, open questions #12/#13) flagged

Read `constants.py`, `controllers/configuration.py::config()`, `models/
configuration.py::update_tentacles_activation_config`, `octobot_tentacles_
manager/api/configurator.py::update_activation_configuration`, and
`octobot_tentacles_manager/configuration/tentacles_setup_configuration.py`
(`Services/Interfaces/web_interface/` and `packages/tentacles_manager/
octobot_tentacles_manager/`, `Drakkar-Software/OctoBot`, master branch)
directly from GitHub:

- **`EVALUATOR_CONFIG_KEY = "evaluator_config"`** -- the exact literal
  request-body key (confirmed, `constants.py`, the line right after
  `TRADING_CONFIG_KEY`'s own). Resolves open question #12.
- The route body confirms `patch` is submitted the same unwrapped,
  un-nested way as `update_trading_config`'s `patch`, under the top-level
  `"evaluator_config"` key: `if constants.EVALUATOR_CONFIG_KEY in
  request_data and request_data[constants.EVALUATOR_CONFIG_KEY]: ...
  success = success and models.update_tentacles_activation_config(
  request_data[constants.EVALUATOR_CONFIG_KEY], deactivate_others)`.
- **`deactivate_others` is a SEPARATE top-level request-body key, not
  nested inside the `evaluator_config` patch dict** -- a genuine spec
  correction to the tool spec's inference. The route reads it as its own
  sibling key: `deactivate_others = False; if constants.DEACTIVATE_OTHERS
  in request_data: deactivate_others = request_data[constants.
  DEACTIVATE_OTHERS]` (`constants.DEACTIVATE_OTHERS = "deactivate_others"`,
  confirmed). So the actual outgoing JSON body this tool sends is
  `{"evaluator_config": patch, "deactivate_others": <bool>}` -- two
  top-level keys, never one nested under the other.
- **`deactivate_others`'s exact semantics, resolving open question #13**:
  traced `models.update_tentacles_activation_config(new_config,
  deactivate_others)` into `tentacles_manager_api.
  update_activation_configuration(tentacles_setup_config, new_config,
  deactivate_others)` into `TentaclesSetupConfiguration.
  update_activation_configuration`'s own body. After applying every
  `element_name -> activated` pair in `patch` as before, when
  `deactivate_others` is `True` it additionally calls
  `self._deactivate_other_evaluators(new_config)`, whose body iterates
  EVERY currently-tracked tentacle of ANY type (trading modes, strategies,
  evaluators) that is currently active and NOT a key in `patch`, and for
  each one checks `get_simple_tentacle_type()` against the four evaluator
  subtypes (`TENTACLES_EVALUATOR_TA_PATH`/`_SOCIAL_PATH`/`_REALTIME_PATH`/
  `_SCRIPTED_PATH`) -- ONLY tentacles matching one of those four subtypes
  are force-deactivated; trading modes and strategies are never touched by
  this step regardless of `deactivate_others`. Confirmed, not inferred:
  `deactivate_others=True` means "treat `patch` as the exhaustive set of
  evaluators that should remain enabled -- every OTHER currently-active
  evaluator (never a trading mode or strategy) gets force-disabled";
  `deactivate_others=False` (this tool's default, matching both the spec's
  direction and the conservative choice) is purely additive -- only the
  evaluators named in `patch` have their activation state touched, every
  other tentacle's state is left exactly as it was.

## `list_evaluators` (task 19, `docs/tasklist.md` item 19, ADR-0009 reversed to GO, FR-32/NFR-18)

A third Tier-B HTML-scrape read adapter (mirroring `octobot_mcp.tools.profiles`/
`octobot_mcp.tools.data_collection`'s own adapters), added here rather than a
new module since the tool spec's "Trading config" section places it right
after `update_evaluator_config` above -- the tool this closes a companion-read
gap for: `patch`'s keys must be an evaluator's exact class name, and this is
the only tool in this server that discovers one.

ADR-0009 originally recommended NO-GO on this exact tool (evaluator names are
OctoBot's own public tentacle catalog, discoverable without this server); the
user later explicitly asked to build it anyway. See
`docs/adr/0009-evaluator-discovery-third-tier-b-adapter.md`'s "Status"/
"Implementation plan" sections for the full live-verified DOM shape this
adapter implements against (a real fetch of `GET
http://192.168.178.100:5001/advanced/evaluator_config`, OctoBot 2.1.1,
performed before this tool was written) -- not re-derived here.

**Confirmed DOM shape**: each evaluator renders as an `<a class="...
config-element" ... config-type="evaluator_config" config-key=<Name>
current-value=<Bool> config-value=<Bool> startup-config-value=<Bool> ...>`
element (`current-value` is a literal `"True"`/`"False"` string, never
Python-truthy-evaluated directly), nested inside one of four category
containers, each a stable `id`: `ta-evaluator-config-root` ->
`"technical_analysis"`, `social-evaluator-config-root` -> `"social"`,
`rt-evaluator-config-root` -> `"realtime"`, `scripted-evaluator-config-root`
-> `"scripted"`. `type` is always taken from the container an evaluator's row
was found inside, never inferred from the evaluator's own class name.

**Fail-loud gate, deliberately scoped differently than
`ProfileScrapeContractBrokenError`'s "zero is always broken" model**:
`EvaluatorScrapeContractBrokenError` is raised only if zero `config-element`
rows are found across ALL FOUR containers combined, or if any of the four
expected container `id`s is missing from the page entirely -- a single
category container legitimately having zero rows is NOT an error (confirmed
live: this project's own test instance's "Scripted evaluators" category has
zero installed evaluators, rendering an intact but empty container). Do not
special-case that in a way that changes this gate's whole-page scope.

Read-only, not confirm-gated (ADR-0003).
"""

from typing import Any

import httpx2 as httpx
from bs4 import BeautifulSoup
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context

from octobot_mcp.context import get_client
from octobot_mcp.errors import (
    EvaluatorConfigUpdateRejectedError,
    EvaluatorScrapeContractBrokenError,
    OctoBotClientError,
    OctoBotConnectionError,
    OctoBotMalformedResponseError,
    OctoBotServerError,
    OctoBotTimeoutError,
    TradingConfigUpdateRejectedError,
)
from octobot_mcp.tools._shared import with_error_handling

_CONFIG_PATH = "/config"
_TRADING_CONFIG_KEY = "trading_config"
_TRADING_UPDATED_CONFIG_KEY = "trading_updated_config"
_EVALUATOR_CONFIG_KEY = "evaluator_config"
_EVALUATOR_UPDATED_CONFIG_KEY = "evaluator_updated_config"
_DEACTIVATE_OTHERS_KEY = "deactivate_others"

#: `list_evaluators` (task 19, ADR-0009): `GET /advanced/evaluator_config`'s
#: bare page, distinct from the `/config` POST route the two tools above use.
_EVALUATOR_CONFIG_PAGE_PATH = "/advanced/evaluator_config"

#: The four category container ids ADR-0009's live fetch confirmed, mapped to
#: the `type` value this tool reports for every evaluator found inside each
#: one -- never inferred from the evaluator's own class name.
_EVALUATOR_CATEGORY_CONTAINERS = {
    "ta-evaluator-config-root": "technical_analysis",
    "social-evaluator-config-root": "social",
    "rt-evaluator-config-root": "realtime",
    "scripted-evaluator-config-root": "scripted",
}


async def _request_raw(
    client: httpx.AsyncClient, method: str, path: str, **kwargs: Any
) -> httpx.Response:
    """Perform one request without decoding JSON, classifying only transport failures.

    Mirrors `octobot_mcp.tools.exchanges._request_raw`/
    `octobot_mcp.tools.tentacles._request_raw` (independently duplicated in
    each module by this project's established convention, not shared):
    `octobot_mcp.client.request_json` can't be reused here because a failed
    call needs OctoBot's own raw response body (to build
    `TradingConfigUpdateRejectedError`'s message), not just a generic
    classified exception with no body content. Only transport-level failures
    are classified here; the call site classifies its own status code.
    """
    try:
        return await client.request(method, path, **kwargs)
    except httpx.TimeoutException as exc:
        raise OctoBotTimeoutError(f"Request to OctoBot timed out: {method} {path}") from exc
    except httpx.HTTPError as exc:
        raise OctoBotConnectionError(f"Could not reach OctoBot: {method} {path} ({exc})") from exc


def _extract_error_text(response: httpx.Response) -> str:
    """Best-effort extraction of OctoBot's own error text from a non-2xx response.

    Mirrors `octobot_mcp.tools.exchanges._extract_error_text` for the exact
    same `/config` route: its failure branch is `util.get_rest_reply(flask.
    jsonify(err_message), 500)` -- a bare JSON string, confirmed against
    source -- so the JSON-first attempt is the expected path here (unlike
    `tentacles.py`'s two routes, whose failure bodies are raw unquoted text
    despite a lying `Content-Type: application/json` header). The raw-text
    fallback is kept anyway for consistency with every other module's
    identical helper and in case a future OctoBot version's error path
    changes. See this module's docstring for why, for THIS tool's own
    request shape, the extracted text is always an empty string in practice.
    """
    try:
        body = response.json()
    except ValueError:
        return response.text.strip() or f"HTTP {response.status_code} with an empty body"
    if isinstance(body, dict) and "text" in body:
        return str(body["text"])
    return str(body)


@with_error_handling
async def update_trading_config(ctx: Context, patch: dict[str, Any]) -> Any:
    """Update the current profile's trading-mode tentacle-activation config.

    Maps to `POST /config`, JSON body `{"trading_config": patch}` -- see this
    module's docstring for the full source-verified derivation of this exact
    shape (the spec's `[V]`-tagged claim, independently re-confirmed against
    OctoBot source this session) and the request-body key's literal value
    (`TRADING_CONFIG_KEY = "trading_config"`). `patch` is submitted as-is,
    unwrapped -- the same shape `models.update_tentacles_activation_config`
    (also used by the tentacle/evaluator activation branches of this same
    route) expects: typically a dict mapping tentacle class names to a
    boolean (or a `"true"`/`"false"` string) activation state.

    Output: `{"trading_updated_config": <passthrough of OctoBot's own echoed
    value for this key from its success response>}` -- matches the route's
    own response shape (NFR-8), narrowed to just this one key rather than
    OctoBot's whole `/config` response envelope, since every other key in
    that envelope reflects config domains (`tentacle_config`,
    `evaluator_config`, `global_config`, `removed_elements`) this tool never
    sends and is therefore always that route's own empty-string default.

    Not confirm-gated (ADR-0003): mutates strategy/tentacle activation
    config, does not destroy data, expose credentials, or enable live
    trading. Raises `TradingConfigUpdateRejectedError` on any non-2xx
    response -- see this module's docstring for why, for this tool's own
    request shape, OctoBot's own error text is confirmed to always be an
    empty string in practice, never a substantive message, despite the
    spec's original claim otherwise.
    """
    client = get_client(ctx)
    response = await _request_raw(
        client,
        "POST",
        _CONFIG_PATH,
        json={_TRADING_CONFIG_KEY: patch},
    )
    if response.status_code >= 400:
        raise TradingConfigUpdateRejectedError(
            f"OctoBot rejected update_trading_config: {_extract_error_text(response)}"
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise OctoBotMalformedResponseError(
            "OctoBot returned a 2xx response with a non-JSON body for "
            f"POST {_CONFIG_PATH} (trading_config)."
        ) from exc
    if not isinstance(body, dict) or _TRADING_UPDATED_CONFIG_KEY not in body:
        raise OctoBotMalformedResponseError(
            f"OctoBot's 2xx response for POST {_CONFIG_PATH} (trading_config) "
            f"had no {_TRADING_UPDATED_CONFIG_KEY!r} key: {body!r}"
        )
    return {_TRADING_UPDATED_CONFIG_KEY: body[_TRADING_UPDATED_CONFIG_KEY]}


@with_error_handling
async def update_evaluator_config(
    ctx: Context, patch: dict[str, Any], deactivate_others: bool = False
) -> Any:
    """Update the current profile's evaluator-activation config.

    Maps to `POST /config`, JSON body `{"evaluator_config": patch,
    "deactivate_others": deactivate_others}` -- TWO top-level keys, never one
    nested under the other -- see this module's docstring for the full
    source-verified derivation of this exact shape and the request-body
    key's literal value (`EVALUATOR_CONFIG_KEY = "evaluator_config"`,
    resolving the tool spec's/requirements doc's open question #12). `patch`
    is submitted as-is, unwrapped, mapping evaluator class names to a
    boolean (or `"true"`/`"false"` string) activation state -- the same
    shape `update_trading_config` submits under its own key, since both
    reach the same underlying `models.update_tentacles_activation_config`.

    `deactivate_others` (default `False`, resolving open question #13):
    when `True`, every OTHER currently-active evaluator NOT named in `patch`
    is force-disabled -- confirmed by tracing `models.
    update_tentacles_activation_config` into `TentaclesSetupConfiguration.
    update_activation_configuration`/`_deactivate_other_evaluators`'s own
    body (see this module's docstring for the full trace); this only ever
    touches tentacles whose type is one of the four evaluator subtypes
    (technical-analysis/social/real-time/scripted) -- trading modes and
    strategies are never affected by this flag. `False` (this tool's
    default) is purely additive: only the evaluators named in `patch` have
    their activation state changed, every other tentacle is left untouched.

    `patch`'s keys are evaluator CLASS NAMES, not display names -- e.g.
    `"RSIMomentumEvaluator"`, `"BBMomentumEvaluator"`,
    `"DoubleMovingAverageTrendEvaluator"`, `"MACDMomentumEvaluator"`,
    `"SuperTrendEvaluator"` are real examples confirmed against the OctoBot
    2.1.1 test instance's default evaluator set. Use this module's own
    `list_evaluators()` (task 19, ADR-0009 -- reversed to GO by explicit user
    decision after originally recommending NO-GO here) to discover every
    evaluator's exact class name and current activation state: its `name`
    field is exactly the string this tool's `patch` keys must equal, closing
    the loop structurally. `get_tentacle_config(name)` does NOT expose
    activation state (confirmed live: its `config` field holds only that
    evaluator's own tunable parameters, e.g. RSI thresholds -- never an
    enabled/disabled flag).


    Output: `{"evaluator_updated_config": <passthrough of OctoBot's own
    echoed value for this key from its success response>}` -- matches the
    route's own response shape (NFR-8), by direct analogy to
    `update_trading_config`'s own confirmed `{"trading_updated_config": ...}`
    shape (same response envelope, different key extracted).

    Not confirm-gated (ADR-0003): mutates which evaluators are active, does
    not destroy data, expose credentials, or enable live trading. Raises
    `EvaluatorConfigUpdateRejectedError` on any non-2xx response -- see this
    module's docstring for why, for this tool's own request shape, OctoBot's
    own error text is confirmed to always be an empty string in practice
    (the `evaluator_config` branch never populates the route's own
    `err_message` variable, exactly like the `trading_config` branch
    `update_trading_config` documents).
    """
    client = get_client(ctx)
    response = await _request_raw(
        client,
        "POST",
        _CONFIG_PATH,
        json={_EVALUATOR_CONFIG_KEY: patch, _DEACTIVATE_OTHERS_KEY: deactivate_others},
    )
    if response.status_code >= 400:
        raise EvaluatorConfigUpdateRejectedError(
            f"OctoBot rejected update_evaluator_config: {_extract_error_text(response)}"
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise OctoBotMalformedResponseError(
            "OctoBot returned a 2xx response with a non-JSON body for "
            f"POST {_CONFIG_PATH} (evaluator_config)."
        ) from exc
    if not isinstance(body, dict) or _EVALUATOR_UPDATED_CONFIG_KEY not in body:
        raise OctoBotMalformedResponseError(
            f"OctoBot's 2xx response for POST {_CONFIG_PATH} (evaluator_config) "
            f"had no {_EVALUATOR_UPDATED_CONFIG_KEY!r} key: {body!r}"
        )
    return {_EVALUATOR_UPDATED_CONFIG_KEY: body[_EVALUATOR_UPDATED_CONFIG_KEY]}


# --- list_evaluators: third Tier-B scrape adapter (task 19, ADR-0009) -------


async def _fetch_evaluator_config_html(client: httpx.AsyncClient) -> str:
    """Fetch `GET /advanced/evaluator_config`'s raw HTML body.

    Mirrors `octobot_mcp.tools.profiles._fetch_profiles_selector_html`/
    `octobot_mcp.tools.data_collection._fetch_data_collector_html`'s
    identically-purposed helper (duplicated, not imported -- this project's
    established convention). `octobot_mcp.client.request_json` can't be
    reused: this route renders HTML, not JSON.
    """
    try:
        response = await client.request("GET", _EVALUATOR_CONFIG_PAGE_PATH)
    except httpx.TimeoutException as exc:
        raise OctoBotTimeoutError(
            f"Request to OctoBot timed out: GET {_EVALUATOR_CONFIG_PAGE_PATH}"
        ) from exc
    except httpx.HTTPError as exc:
        raise OctoBotConnectionError(
            f"Could not reach OctoBot: GET {_EVALUATOR_CONFIG_PAGE_PATH} ({exc})"
        ) from exc

    if response.status_code >= 500:
        raise OctoBotServerError(
            f"OctoBot returned server error {response.status_code} for "
            f"GET {_EVALUATOR_CONFIG_PAGE_PATH}",
            status_code=response.status_code,
        )
    if response.status_code >= 400:
        raise OctoBotClientError(
            f"OctoBot returned client error {response.status_code} for "
            f"GET {_EVALUATOR_CONFIG_PAGE_PATH}",
            status_code=response.status_code,
        )
    return response.text


def _parse_evaluator_row(row: Any, evaluator_type: str) -> dict[str, Any]:
    """Parse one `a.config-element` row (ADR-0009's exact markup) into one evaluator dict.

    Raises `EvaluatorScrapeContractBrokenError` -- never a partial/best-effort
    dict -- if this row's `config-key`/`current-value` attributes don't hold
    the shape ADR-0009's live fetch confirmed, mirroring
    `octobot_mcp.tools.data_collection._parse_data_files_row`'s per-row
    fail-loud discipline.
    """
    name = row.get("config-key")
    if not name:
        raise EvaluatorScrapeContractBrokenError(
            "An evaluator config-element row is missing its config-key attribute."
        )
    current_value = row.get("current-value")
    if current_value not in ("True", "False"):
        raise EvaluatorScrapeContractBrokenError(
            f"Evaluator {name!r}'s current-value attribute is {current_value!r}, "
            "expected the literal string 'True' or 'False' (confirmed casing, "
            "see octobot_mcp.tools.trading_config's module docstring)."
        )
    return {"name": name, "enabled": current_value == "True", "type": evaluator_type}


def _parse_evaluator_config_html(html: str) -> list[dict[str, Any]]:
    """Parse `/advanced/evaluator_config`'s rendered HTML into evaluator dicts.

    Fail-loud gate (NFR-18), deliberately scoped to the WHOLE page, not any
    single category -- see this module's docstring: raises
    `EvaluatorScrapeContractBrokenError` if any of the four expected category
    container ids is missing from the page entirely, or if zero
    `config-element` rows are found across all four containers combined. A
    single category container legitimately having zero rows inside it is not
    itself an error.
    """
    soup = BeautifulSoup(html, "html.parser")
    evaluators: list[dict[str, Any]] = []
    for container_id, evaluator_type in _EVALUATOR_CATEGORY_CONTAINERS.items():
        container = soup.find(id=container_id)
        if container is None:
            raise EvaluatorScrapeContractBrokenError(
                f"No element with id={container_id!r} found on "
                f"{_EVALUATOR_CONFIG_PAGE_PATH}. OctoBot always renders all "
                "four evaluator category containers, even ones with zero "
                "evaluators inside -- this means the page structure has "
                "changed. See octobot_mcp.tools.trading_config's module "
                "docstring for the last-confirmed OctoBot version."
            )
        rows = container.find_all("a", class_="config-element")
        evaluators.extend(_parse_evaluator_row(row, evaluator_type) for row in rows)

    if not evaluators:
        raise EvaluatorScrapeContractBrokenError(
            f"No evaluator config-element rows found across any of the four "
            f"category containers on {_EVALUATOR_CONFIG_PAGE_PATH}. A real "
            "OctoBot instance always has at least one evaluator installed "
            "in some category -- this means the page structure has changed, "
            "not that zero evaluators exist (a single, legitimately-empty "
            "category is not itself an error -- see this module's "
            "docstring)."
        )
    return evaluators


@with_error_handling
async def list_evaluators(ctx: Context) -> Any:
    """List every evaluator OctoBot knows about, including its activation state and category.

    Tier-B (ADR-0009, reversed to GO by explicit user decision -- see this
    module's docstring for the full verified DOM shape and fail-loud
    assumptions). Closes `update_evaluator_config`'s own companion-read gap:
    that tool is unusable without already knowing an evaluator's exact class
    name and current state. Idempotent, read-only, not confirm-gated
    (ADR-0003).

    Output: `{"evaluators": [{"name": str, "enabled": bool, "type": str}]}`.
    `name` is the `config-key` attribute value -- the same string
    `update_evaluator_config`'s `patch` keys must equal, making the closed
    loop between these two tools a structural guarantee, not a convention to
    maintain by hand. `type` is one of `"technical_analysis"`/`"social"`/
    `"realtime"`/`"scripted"`, taken from whichever category container an
    evaluator's row was found inside.

    Raises `EvaluatorScrapeContractBrokenError` (NFR-18) only if zero
    evaluators are found across ALL FOUR category containers combined, or an
    expected container is missing from the page entirely -- never for a
    single category legitimately having zero evaluators (confirmed live:
    this project's own test instance has zero "Scripted evaluators"
    installed).
    """
    html = await _fetch_evaluator_config_html(get_client(ctx))
    return {"evaluators": _parse_evaluator_config_html(html)}


def register(mcp: MCPServer) -> None:
    """Register this module's tools onto `mcp` (called once from `octobot_mcp.tools.register_all`)."""
    mcp.tool()(update_trading_config)
    mcp.tool()(update_evaluator_config)
    mcp.tool()(list_evaluators)
