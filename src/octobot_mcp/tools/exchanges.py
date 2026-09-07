"""Exchange lookup and compatibility tools (milestone 3, docs/tasklist.md item 3).

`get_currency_list`, `get_all_currencies`, `get_all_symbols`,
`check_accounts_compatible`, and `get_exchange_details` all map to
OctoBot's confirmed JSON API (`api/exchanges.py`, `api/config.py`,
`api/trading.py`, tagged `[V]` in docs/specs/octobot-mcp-tool-spec.md).
None are confirm-gated (ADR-0003): they are read-only lookups. Every
response body is returned exactly as OctoBot sends it (NFR-8).

## Milestone 12: `update_exchange_credentials` -- resolving open question #2

Open question #2 (requirements doc + spec's "Open questions") asked to
confirm, via a live instance's browser devtools network tab, the exact
nested JSON key path `POST /config`'s `global_config` payload expects for
exchange API credentials -- source read as of milestone 5 confirmed the
route's *dispatch* (`GLOBAL_CONFIG_KEY = "global_config"`,
`controllers/configuration.py`'s `/config` route) but not that nested shape.
No real browser is available in this environment, so this was resolved the
way every other open question in this project actually has been: by reading
further into OctoBot source on GitHub (`Drakkar-Software/OctoBot`, master
branch) rather than guessing. **Fully resolved, not a guess** -- the exact
request body was reconstructed from FOUR independently corroborating
source locations, not just the model function's docstring-free body:

1. `Services/Interfaces/web_interface/models/configuration.py::
   update_global_config(new_config, delete=False)` calls
   `current_edited_config.update_config_fields(new_config, ...,
   constants.UPDATED_CONFIG_SEPARATOR, delete=delete)` -- so `new_config`
   (the value under `global_config`) is a FLAT dict whose keys are
   separator-joined paths into the nested config, not a nested dict itself.
2. `octobot_commons.configuration.config_operations.parse_and_update(key,
   new_data, config_separator)` confirms exactly how that flat key is
   turned into a nested path: `key.split(config_separator)`, then a nested
   dict is built positionally from the parts. `constants.
   UPDATED_CONFIG_SEPARATOR = "_"` (confirmed in `web_interface/constants.py`).
3. `octobot_commons.constants` confirms the literal field-name segments:
   `CONFIG_EXCHANGES = "exchanges"`, `CONFIG_EXCHANGE_KEY = "api-key"`,
   `CONFIG_EXCHANGE_SECRET = "api-secret"`, `CONFIG_EXCHANGE_PASSWORD =
   "api-password"` (all hyphenated, not underscored -- load-bearing, since
   the separator is itself `"_"`; see the underscore caveat below).
4. **Byte-for-byte confirmation from the browser-rendered form this open
   question originally wanted network-tab evidence for**:
   `templates/components/config/exchange_card.html`'s exchange-credentials
   `<a>` elements carry the LITERAL attributes
   `config-key="exchanges_{{exchange}}_api-key"` (`...-secret`,
   `...-password` siblings) and `config-type="global_config"`, and
   `static/js/components/configuration.js::_save_config()` builds its POST
   body as `updated_config[config_type][config_key] = new_value` directly
   from those two attributes -- i.e. exactly
   `{"global_config": {"exchanges_<exchange>_api-key": <value>, ...}}`, with
   no other request-body fields required (the controller defaults every
   other top-level key -- `trading_config`, `tentacle_config`,
   `evaluator_config`, `removed_elements`, `restart_after_save` -- to a
   falsy value when absent from the request body; confirmed by reading the
   `/config` route's full body, not just its `GLOBAL_CONFIG_KEY` branch).

So the confirmed request is `POST /config`, JSON body
`{"global_config": {"exchanges_<exchange>_api-key": <api_key>,
"exchanges_<exchange>_api-secret": <api_secret>[, "exchanges_<exchange>_
api-password": <api_password>]}}`.

**A genuine caveat this source-reading surfaced, not present in the
original open question**: because the separator is the literal character
`"_"` and `parse_and_update` splits on EVERY occurrence of it positionally
(`"exchanges_<exchange>_api-key".split("_")`), an `exchange` name that
itself contains an underscore is split apart incorrectly -- e.g.
`exchange="foo_bar"` would build the nested path
`{"exchanges": {"foo": {"bar": {"api-key": ...}}}}` instead of
`{"exchanges": {"foo_bar": {"api-key": ...}}}`, silently writing to the
wrong place in OctoBot's config rather than updating the intended
exchange's credentials. This is a real limitation of OctoBot's own
separator-based config-path encoding (not something this server invented
or can fix), so `update_exchange_credentials` refuses locally -- before any
OctoBot call -- whenever `exchange` contains an underscore, rather than
silently sending a request that would corrupt the wrong config path.

**What remains genuinely unverified** (documented, not guessed past): this
was not exercised against a live instance's actual `/accounts` page network
traffic (no browser available here), only reconstructed from source across
the four locations above; and whether a freshly-written credential is
picked up without a full OctoBot restart is unconfirmed (the JS success
handler's own alert text -- "Restart OctoBot for changes to be applied." --
suggests it is not, consistent with `update_trading_config`/
`update_tentacle_config`'s same documented restart caveat elsewhere in this
project).

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
from octobot_mcp.context import get_client
from octobot_mcp.errors import (
    ExchangeCredentialsUpdateRejectedError,
    OctoBotConnectionError,
    OctoBotTimeoutError,
)
from octobot_mcp.tools._shared import redact_secrets, require_confirmation, with_error_handling

_GLOBAL_CONFIG_PATH = "/config"


@with_error_handling
async def get_currency_list(ctx: Context, exchange: str | None = None) -> Any:
    """List OctoBot's globally configured trading currencies/symbols.

    Maps to `GET /api/currency_list`. `exchange` is accepted for
    forward-compatibility with this tool's spec entry, but the currently
    deployed OctoBot route (`api/trading.py::currency_list()`, master
    branch, confirmed by reading its body this session) takes no request
    arguments at all and always returns the same global list regardless
    of this value -- it is a no-op filter on today's OctoBot. Read-only,
    not confirm-gated. Returns OctoBot's own JSON body unchanged (NFR-8).
    """
    params = {"exchange": exchange} if exchange is not None else None
    return await request_json(get_client(ctx), "GET", "/api/currency_list", params=params)


@with_error_handling
async def get_all_currencies(ctx: Context, exchange: str) -> Any:
    """List every currency OctoBot knows about for one exchange.

    Maps to `GET /api/get_all_currencies/<exchange>` (`[V]`, confirmed
    against OctoBot source `api/config.py`). Read-only, not
    confirm-gated. Returns OctoBot's own JSON body unchanged (NFR-8).
    """
    return await request_json(get_client(ctx), "GET", f"/api/get_all_currencies/{exchange}")


@with_error_handling
async def get_all_symbols(ctx: Context, exchange: str) -> Any:
    """List every trading symbol/pair OctoBot knows about for one exchange.

    Maps to `GET /api/get_all_symbols/<exchange>` (`[V]`, confirmed
    against OctoBot source `api/config.py`). Read-only, not
    confirm-gated. Returns OctoBot's own JSON body unchanged (NFR-8).
    """
    return await request_json(get_client(ctx), "GET", f"/api/get_all_symbols/{exchange}")


@with_error_handling
async def check_accounts_compatible(ctx: Context, exchange_names: list[str]) -> Any:
    """Check whether each named exchange's already-configured account is usable.

    Maps to `POST /api/are_compatible_accounts`. OctoBot's real request
    body for this route is more general than this tool's input -- it
    expects a dict keyed by exchange, each value an object with
    `exchange`/`apiKey`/`apiSecret`/`apiPassword`/`sandboxed` fields meant
    for OctoBot's own "test these credentials" UI form (confirmed against
    OctoBot source `models/configuration.py::are_compatible_accounts()`
    and `octobot_commons.constants`, `[V]` this session -- this is a
    correction of this tool's spec entry, which only names
    `exchange_names`; see the implementation report for detail).

    This tool deliberately narrows that to just `exchange_names` and
    always submits OctoBot's own masked-placeholder value (`"******"`)
    for the credential fields, so it checks compatibility using whatever
    credentials are *already stored* on the OctoBot instance for each
    named exchange, never a credential supplied through this call.
    Confirmed from source: OctoBot only treats a non-placeholder value as
    a real credential update (`_is_real_exchange_value`) and otherwise
    falls back to the exchange's already-configured encrypted value, so a
    placeholder-only request checks the existing configuration without
    ever transmitting or requiring a real credential. No credential value
    passes through this tool's input or output. Read-only in effect, not
    confirm-gated. Returns OctoBot's own JSON body unchanged (NFR-8).

    CAVEAT: `sandboxed` is currently hardcoded to `False` for every
    exchange checked, regardless of how that exchange is actually
    configured on the instance. It is unconfirmed whether OctoBot's
    credential-fallback path uses this value to pick which environment
    (testnet vs. production) to validate stored credentials against; if
    it does, a genuinely sandboxed exchange may be checked against the
    wrong environment. Treat this tool's result with caution for any
    exchange you know to be configured in sandbox/testnet mode until this
    is confirmed against source.
    """
    exchange_details = {
        name: {
            "exchange": name,
            "apiKey": "******",
            "apiSecret": "******",
            "apiPassword": "******",
            "sandboxed": False,
        }
        for name in exchange_names
    }
    return await request_json(
        get_client(ctx), "POST", "/api/are_compatible_accounts", json=exchange_details
    )


@with_error_handling
async def get_exchange_details(ctx: Context, exchange_name: str | None = None) -> Any:
    """Fetch ONE exchange's name and internal id.

    Maps to `GET /api/first_exchange_details` (`[V]`). IMPORTANT: this
    endpoint only ever returns a single exchange -- the one matching
    `exchange_name` if given, or OctoBot's own notion of "the first
    exchange" otherwise -- confirmed against OctoBot source
    (`api/exchanges.py::first_exchange_details()`). It does NOT enumerate
    every exchange configured on the instance; do not call this tool
    expecting a full exchange list, and do not assume its result is the
    only configured exchange. Read-only, not confirm-gated. Returns
    OctoBot's own JSON body unchanged (NFR-8).
    """
    params = {"exchange_name": exchange_name} if exchange_name is not None else None
    return await request_json(
        get_client(ctx), "GET", "/api/first_exchange_details", params=params
    )


# --- milestone 12: update_exchange_credentials (ADR-0003, INV-4/NFR-6) ------


async def _request_raw(
    client: httpx.AsyncClient, method: str, path: str, **kwargs: Any
) -> httpx.Response:
    """Perform one request without decoding JSON, classifying only transport failures.

    `octobot_mcp.client.request_json` can't be reused here: on a failure this
    tool needs OctoBot's own raw error body text (to build a helpful,
    redacted error message), not just a generic classified exception with no
    body content -- the same reason `octobot_mcp.tools.profiles` has its own
    equivalent of this helper rather than sharing one across modules.
    """
    try:
        return await client.request(method, path, **kwargs)
    except httpx.TimeoutException as exc:
        raise OctoBotTimeoutError(f"Request to OctoBot timed out: {method} {path}") from exc
    except httpx.HTTPError as exc:
        raise OctoBotConnectionError(f"Could not reach OctoBot: {method} {path} ({exc})") from exc


def _extract_error_text(response: httpx.Response) -> str:
    """Best-effort extraction of OctoBot's own error text from a non-2xx response.

    Mirrors `octobot_mcp.tools.profiles._extract_error_text`: tries a JSON
    body first (`/config`'s failure branch is `util.get_rest_reply(flask.
    jsonify(err_message), 500)` -- a bare JSON string, confirmed against
    source), falling back to the raw response text for anything else (e.g.
    an HTML error page from a layer below OctoBot's own error handling).
    """
    try:
        body = response.json()
    except ValueError:
        return response.text.strip() or f"HTTP {response.status_code} with an empty body"
    if isinstance(body, dict) and "text" in body:
        return str(body["text"])
    return str(body)


@with_error_handling
async def update_exchange_credentials(
    ctx: Context,
    exchange: str,
    api_key: str,
    api_secret: str,
    api_password: str | None = None,
    confirm: bool = False,
) -> Any:
    """Write an exchange's API credentials to OctoBot's global config. Confirm-gated (ADR-0003).

    If `confirm` is not exactly `true`, no OctoBot call is made at all --
    this returns `require_confirmation`'s structured refusal (a normal
    return, not an error) instead. Reason given: "writes exchange API
    credentials; verify the exchange name and that you intend to grant this
    key trading access" (per spec).

    Once confirmed, maps to `POST /config` with JSON body
    `{"global_config": {"exchanges_<exchange>_api-key": api_key,
    "exchanges_<exchange>_api-secret": api_secret[, "exchanges_<exchange>_
    api-password": api_password]}}` -- see this module's docstring
    ("Milestone 12") for the full, source-verified derivation of this exact
    shape (open question #2, now resolved) and its known limitation.

    **Refuses locally, before any OctoBot call, if `exchange` contains an
    underscore** -- confirmed against source
    (`octobot_commons.configuration.config_operations.parse_and_update`)
    that OctoBot's own config-path decoder would otherwise split the
    generated `"exchanges_<exchange>_api-key"` key on every literal `"_"`
    positionally, silently writing the credential into the wrong nested
    config location instead of the intended exchange's. Raises
    `ExchangeCredentialsUpdateRejectedError` for this case.

    **INV-4/NFR-6, this tool's one consumer of `redact_secrets`**: OctoBot's
    own success response for this route (`global_updated_config`, confirmed
    against source) echoes the ENTIRE submitted `global_config` dict back
    verbatim -- including the raw `api_key`/`api_secret`/`api_password` just
    sent. This tool therefore never reads OR forwards any part of OctoBot's
    response body on success: a 2xx status alone is trusted (this route's
    status code IS a faithful `(success, err_message)` signal, confirmed
    against source `models/configuration.py::update_global_config`, unlike
    several `profiles.py` routes that needed a post-hoc re-check), and the
    tool's own output below is built from only what it already knows
    locally. On failure, OctoBot's raw error text is passed through
    `redact_secrets` with the literal `api_key`/`api_secret`/`api_password`
    values from THIS call before being included in the raised exception's
    message -- the single boundary INV-4 requires, not scattered ad-hoc
    scrubbing.

    Output on success: `{"exchange": str, "updated": true, "api_key_last4":
    str}` -- never the full `api_key`/`api_secret`, per NFR-6.
    """
    if confirm is not True:
        return require_confirmation(
            "update_exchange_credentials",
            exchange,
            "This writes exchange API credentials; verify the exchange "
            "name and that you intend to grant this key trading access.",
        )

    if "_" in exchange:
        raise ExchangeCredentialsUpdateRejectedError(
            f"update_exchange_credentials: exchange={exchange!r} contains an "
            "underscore, which this tool refuses to send. OctoBot's own "
            "config-path decoder (confirmed against source, "
            "octobot_commons.configuration.config_operations.parse_and_update) "
            "splits the 'exchanges_<exchange>_<field>' key on every literal "
            "'_' positionally, so an exchange name containing '_' would be "
            "silently written to the wrong nested config location instead "
            "of this exchange's credentials."
        )

    submitted_secrets = [api_key, api_secret, api_password]
    global_config: dict[str, str] = {
        f"exchanges_{exchange}_api-key": api_key,
        f"exchanges_{exchange}_api-secret": api_secret,
    }
    if api_password is not None:
        global_config[f"exchanges_{exchange}_api-password"] = api_password

    client = get_client(ctx)
    response = await _request_raw(
        client,
        "POST",
        _GLOBAL_CONFIG_PATH,
        json={"global_config": global_config},
    )
    if response.status_code >= 400:
        raw_error_text = _extract_error_text(response)
        raise ExchangeCredentialsUpdateRejectedError(
            f"OctoBot rejected updating credentials for exchange={exchange!r}: "
            f"{redact_secrets(raw_error_text, submitted_secrets)}"
        )

    return {
        "exchange": exchange,
        "updated": True,
        "api_key_last4": api_key[-4:] if len(api_key) >= 4 else api_key,
    }


def register(mcp: MCPServer) -> None:
    """Register this module's tools onto `mcp` (called once from `octobot_mcp.tools.register_all`)."""
    mcp.tool()(get_currency_list)
    mcp.tool()(get_all_currencies)
    mcp.tool()(get_all_symbols)
    mcp.tool()(check_accounts_compatible)
    mcp.tool()(get_exchange_details)
    mcp.tool()(update_exchange_credentials)
