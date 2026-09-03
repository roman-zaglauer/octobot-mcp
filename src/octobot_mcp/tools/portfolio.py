"""Portfolio & trading read tools (milestone 3, docs/tasklist.md item 3).

`get_orders`, `get_positions`, `get_trades`, `get_pnl_history`, and
`get_historical_portfolio_value` all map directly to OctoBot's confirmed
JSON API (`api/trading.py`, tagged `[V]` in
docs/specs/octobot-mcp-tool-spec.md). None are confirm-gated (ADR-0003):
they are read-only and touch no destructive/high-risk resource. Every
response body is returned exactly as OctoBot sends it (NFR-8) -- this
server never renames, drops, or reshapes a field.

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
async def get_orders(ctx: Context) -> Any:
    """List every order OctoBot currently knows about (open and historical).

    Maps to `GET /api/orders`. Read-only, not confirm-gated. Returns
    OctoBot's own JSON body unchanged (NFR-8).
    """
    return await request_json(get_client(ctx), "GET", "/api/orders")


@with_error_handling
async def get_positions(ctx: Context) -> Any:
    """List every trading position OctoBot currently knows about.

    Maps to `GET /api/positions`. Read-only, not confirm-gated. Returns
    OctoBot's own JSON body unchanged (NFR-8).
    """
    return await request_json(get_client(ctx), "GET", "/api/positions")


@with_error_handling
async def get_trades(ctx: Context) -> Any:
    """List OctoBot's trade history.

    Maps to `GET /api/trades`. Read-only, not confirm-gated. Returns
    OctoBot's own JSON body unchanged (NFR-8).
    """
    return await request_json(get_client(ctx), "GET", "/api/trades")


@with_error_handling
async def get_pnl_history(
    ctx: Context,
    exchange: str | None = None,
    symbol: str | None = None,
    quote: str | None = None,
    since: str | None = None,
    scale: str | None = None,
) -> Any:
    """Fetch realized PnL history, optionally filtered.

    Maps to `GET /api/pnl_history` -- query parameters confirmed against
    OctoBot source (`api/trading.py::pnl_history()`, master branch, `[V]`
    this session): `exchange`, `symbol`, `quote`, `since`, and `scale` are
    all optional filters OctoBot itself reads from the query string. Omit
    any you don't want to filter by. Read-only, not confirm-gated. Returns
    OctoBot's own JSON body unchanged (NFR-8).
    """
    params = {
        key: value
        for key, value in {
            "exchange": exchange,
            "symbol": symbol,
            "quote": quote,
            "since": since,
            "scale": scale,
        }.items()
        if value is not None
    }
    return await request_json(get_client(ctx), "GET", "/api/pnl_history", params=params)


@with_error_handling
async def get_historical_portfolio_value(
    ctx: Context,
    currency: str | None = None,
    time_frame: str | None = None,
    from_timestamp: str | None = None,
    to_timestamp: str | None = None,
    exchange: str | None = None,
) -> Any:
    """Fetch historical portfolio value samples, optionally filtered.

    Maps to `GET /api/historical_portfolio_value` -- query parameters
    confirmed against OctoBot source
    (`api/trading.py::historical_portfolio_value()`, master branch, `[V]`
    this session): `currency` (OctoBot defaults this to `"USDT"`
    server-side if omitted), `time_frame`, `from_timestamp`,
    `to_timestamp`, and `exchange` are all optional query-string filters.
    Read-only, not confirm-gated. Returns OctoBot's own JSON body
    unchanged (NFR-8).
    """
    params = {
        key: value
        for key, value in {
            "currency": currency,
            "time_frame": time_frame,
            "from_timestamp": from_timestamp,
            "to_timestamp": to_timestamp,
            "exchange": exchange,
        }.items()
        if value is not None
    }
    return await request_json(
        get_client(ctx), "GET", "/api/historical_portfolio_value", params=params
    )


def register(mcp: MCPServer) -> None:
    """Register this module's tools onto `mcp` (called once from `octobot_mcp.tools.register_all`)."""
    mcp.tool()(get_orders)
    mcp.tool()(get_positions)
    mcp.tool()(get_trades)
    mcp.tool()(get_pnl_history)
    mcp.tool()(get_historical_portfolio_value)
