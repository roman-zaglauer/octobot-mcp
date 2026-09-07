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
"""

from typing import Any

import httpx2 as httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context

from octobot_mcp.context import get_client
from octobot_mcp.errors import (
    OctoBotConnectionError,
    OctoBotMalformedResponseError,
    OctoBotTimeoutError,
    TradingConfigUpdateRejectedError,
)
from octobot_mcp.tools._shared import with_error_handling

_CONFIG_PATH = "/config"
_TRADING_CONFIG_KEY = "trading_config"
_TRADING_UPDATED_CONFIG_KEY = "trading_updated_config"


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


def register(mcp: MCPServer) -> None:
    """Register this module's tool onto `mcp` (called once from `octobot_mcp.tools.register_all`)."""
    mcp.tool()(update_trading_config)
