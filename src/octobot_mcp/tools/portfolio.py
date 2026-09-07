"""Portfolio & trading read tools (milestone 3, docs/tasklist.md item 3).

`get_orders`, `get_positions`, `get_trades`, `get_pnl_history`, and
`get_historical_portfolio_value` all map directly to OctoBot's confirmed
JSON API (`api/trading.py`, tagged `[V]` in
docs/specs/octobot-mcp-tool-spec.md). None are confirm-gated (ADR-0003):
they are read-only and touch no destructive/high-risk resource. Every
response body is returned exactly as OctoBot sends it (NFR-8) -- this
server never renames, drops, or reshapes a field.

Milestone 12 (docs/tasklist.md item 12) adds `clear_orders_history`/
`clear_trades_history`/`clear_portfolio_history`/`clear_transactions_history`
-- the remaining four confirm-gated (ADR-0003) tools in this module.
Their routes were re-verified against OctoBot source this session (same
`api/trading.py`, `[V]`) rather than assumed byte-for-byte correct from the
spec table alone, per this project's established practice:
`register(blueprint)` in `Services/Interfaces/web_interface/api/trading.py`
registers all four as `@blueprint.route("/clear_<x>_history",
methods=['POST'])` on the `/api` blueprint -- confirmed the spec's route
names are exactly right, no correction needed here (unlike several
milestone 5/6 profile routes). Each delegates to one `models.trading.py`
function (`clear_exchanges_orders_history`/`..._trades_history`/
`..._transactions_history`/`..._portfolio_history`) whose confirmed, real
success body is `{"title": "Cleared <x> history"}` -- reproduced live this
session (`POST /api/clear_transactions_history` against the OctoBot 2.1.1
test instance -> HTTP 200, `{"title":"Cleared transactions history"}`).
These four tools return that body unchanged (NFR-8), the same passthrough
idiom this module already uses for its read tools, rather than reshaping it
into an invented `{"cleared": true}` shape -- OctoBot's own response here is
already an accurate, non-misleading confirmation (unlike e.g.
`delete_profile`'s reused-string bug documented in `profiles.py`), so there
is nothing to correct.

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
from octobot_mcp.tools._shared import require_confirmation, with_error_handling


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


# --- milestone 12: the remaining confirm-gated history-clear tools (ADR-0003) --


@with_error_handling
async def clear_orders_history(ctx: Context, confirm: bool = False) -> Any:
    """Permanently clear OctoBot's stored orders history. Confirm-gated (ADR-0003).

    If `confirm` is not exactly `true`, no OctoBot call is made at all --
    this returns `require_confirmation`'s structured refusal (a normal
    return, not an error) instead.

    Once confirmed, maps to `POST /api/clear_orders_history` (`[V]`,
    re-confirmed against source this session -- see this module's
    docstring). Returns OctoBot's own JSON body unchanged (NFR-8):
    `{"title": "Cleared orders history"}`.
    """
    if confirm is not True:
        return require_confirmation(
            "clear_orders_history",
            "orders history",
            "This permanently deletes OctoBot's stored orders history and cannot be undone.",
        )
    return await request_json(get_client(ctx), "POST", "/api/clear_orders_history")


@with_error_handling
async def clear_trades_history(ctx: Context, confirm: bool = False) -> Any:
    """Permanently clear OctoBot's stored trades history. Confirm-gated (ADR-0003).

    If `confirm` is not exactly `true`, no OctoBot call is made at all --
    this returns `require_confirmation`'s structured refusal (a normal
    return, not an error) instead.

    Once confirmed, maps to `POST /api/clear_trades_history` (`[V]`,
    re-confirmed against source this session -- see this module's
    docstring). Returns OctoBot's own JSON body unchanged (NFR-8):
    `{"title": "Cleared trades history"}`.
    """
    if confirm is not True:
        return require_confirmation(
            "clear_trades_history",
            "trades history",
            "This permanently deletes OctoBot's stored trades history and cannot be undone.",
        )
    return await request_json(get_client(ctx), "POST", "/api/clear_trades_history")


@with_error_handling
async def clear_portfolio_history(ctx: Context, confirm: bool = False) -> Any:
    """Permanently clear OctoBot's stored historical portfolio values. Confirm-gated (ADR-0003).

    If `confirm` is not exactly `true`, no OctoBot call is made at all --
    this returns `require_confirmation`'s structured refusal (a normal
    return, not an error) instead.

    Once confirmed, maps to `POST /api/clear_portfolio_history` (`[V]`,
    re-confirmed against source this session -- see this module's
    docstring). Returns OctoBot's own JSON body unchanged (NFR-8):
    `{"title": "Cleared portfolio history"}`.
    """
    if confirm is not True:
        return require_confirmation(
            "clear_portfolio_history",
            "portfolio history",
            "This permanently deletes OctoBot's stored historical portfolio "
            "values and cannot be undone.",
        )
    return await request_json(get_client(ctx), "POST", "/api/clear_portfolio_history")


@with_error_handling
async def clear_transactions_history(ctx: Context, confirm: bool = False) -> Any:
    """Permanently clear OctoBot's stored transactions history. Confirm-gated (ADR-0003).

    If `confirm` is not exactly `true`, no OctoBot call is made at all --
    this returns `require_confirmation`'s structured refusal (a normal
    return, not an error) instead.

    Once confirmed, maps to `POST /api/clear_transactions_history` (`[V]`,
    re-confirmed against source AND reproduced live this session against
    the OctoBot 2.1.1 test instance -- see this module's docstring). Returns
    OctoBot's own JSON body unchanged (NFR-8):
    `{"title": "Cleared transactions history"}`.
    """
    if confirm is not True:
        return require_confirmation(
            "clear_transactions_history",
            "transactions history",
            "This permanently deletes OctoBot's stored transactions history and cannot be undone.",
        )
    return await request_json(get_client(ctx), "POST", "/api/clear_transactions_history")


def register(mcp: MCPServer) -> None:
    """Register this module's tools onto `mcp` (called once from `octobot_mcp.tools.register_all`)."""
    mcp.tool()(get_orders)
    mcp.tool()(get_positions)
    mcp.tool()(get_trades)
    mcp.tool()(get_pnl_history)
    mcp.tool()(get_historical_portfolio_value)
    mcp.tool()(clear_orders_history)
    mcp.tool()(clear_trades_history)
    mcp.tool()(clear_portfolio_history)
    mcp.tool()(clear_transactions_history)
