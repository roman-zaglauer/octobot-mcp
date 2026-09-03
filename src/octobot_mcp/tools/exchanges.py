"""Exchange lookup and compatibility tools (milestone 3, docs/tasklist.md item 3).

`get_currency_list`, `get_all_currencies`, `get_all_symbols`,
`check_accounts_compatible`, and `get_exchange_details` all map to
OctoBot's confirmed JSON API (`api/exchanges.py`, `api/config.py`,
`api/trading.py`, tagged `[V]` in docs/specs/octobot-mcp-tool-spec.md).
None are confirm-gated (ADR-0003): they are read-only lookups. Every
response body is returned exactly as OctoBot sends it (NFR-8).

No `from __future__ import annotations` here: see
`octobot_mcp.tools._shared.with_error_handling`'s docstring for why
postponed annotations would silently break this module's Context
injection.
"""

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context

from octobot_mcp.client import request_json
from octobot_mcp.context import get_client
from octobot_mcp.tools._shared import with_error_handling


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


def register(mcp: MCPServer) -> None:
    """Register this module's tools onto `mcp` (called once from `octobot_mcp.tools.register_all`)."""
    mcp.tool()(get_currency_list)
    mcp.tool()(get_all_currencies)
    mcp.tool()(get_all_symbols)
    mcp.tool()(check_accounts_compatible)
    mcp.tool()(get_exchange_details)
