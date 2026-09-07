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

Milestone 13.6 (docs/tasklist.md item 13.6) adds `cancel_order`/
`close_position`/`refresh_portfolio`, closing a second backlog gap the same
way milestone 13.5's `update_trading_config` closed the first: all three
were specced (with verified routes) but never assigned to a tasklist
milestone until now. Source re-verified this session (not trusted from the
spec's `[V]` tag alone), reading the SAME `api/trading.py` this module's
other tools already use plus the three `octobot_services.interfaces.util`
model functions each route delegates to (`Drakkar-Software/OctoBot`, master
branch):

- `cancel_order(order_id)` -> `POST /api/orders?action=cancel_order`. The
  `action` is a query parameter (the same "not a JSON body field" pattern
  `tentacles.py::update_tentacle_config` already documents for its own
  route), and the JSON body is the raw `order_id` value itself -- NOT
  wrapped in an object. Confirmed against source: the route does
  `interfaces_util.cancel_orders([flask.request.get_json()])`, and
  `cancel_orders(order_ids)` iterates its argument directly as a list of
  order ids. Use the `id` field from `get_orders()`'s own response items
  for this value.
- `close_position(position_id)` -> **a genuine spec correction**: OctoBot
  positions have no `id` concept at all. Confirmed against source: `models/
  trading.py::_dump_position` (the exact function behind `get_positions()`)
  builds each position dict with `symbol`/`side`/`contract`/... keys but no
  `id` key, unlike `_dump_order`'s sibling function which does include
  `"id": order.order_id`. The real route,
  `POST /api/positions?action=close_position` (`action` again a query
  parameter), calls `interfaces_util.close_positions([{"symbol": ...,
  "side": ...}])`, which reads exactly `positions_desc["symbol"]`/
  `positions_desc["side"]` -- so this tool's parameters are `symbol`/`side`
  (exactly the two fields `get_positions()` already returns per position),
  not the spec's `position_id`, since there is nothing in OctoBot's model a
  `position_id` could actually map onto. `side` is submitted as OctoBot's
  own `PositionSide` enum's literal value (`"long"`/`"short"`/`"both"`/
  `"unknown"`, confirmed against `octobot_trading.enums.PositionSide`) --
  the exact same string `get_positions()`'s own `side` field already uses,
  so a value read from that response can be passed straight through.
- `refresh_portfolio()` -> `POST /api/refresh_portfolio`, confirmed as a
  bare POST with no request body at all (the route never calls
  `flask.request.get_json()`).

All three share the same real-but-differently-encoded failure shape:
`util.get_rest_reply(<plain string>, 500)` -- a genuine, trustworthy HTTP
500 (no post-hoc re-check needed, unlike several `profiles.py` routes), but
`get_rest_reply` sends that string as a raw, unquoted body while still
setting `Content-Type: application/json`, so `response.json()` fails and
the real message must be read via `response.text` instead -- the same
lying-content-type pattern `tentacles.py`/`exchanges.py`/`trading_config.py`
already document. See `octobot_mcp.errors`'s milestone 13.6 section for each
tool's exact, source-confirmed failure text and each tool's own docstring
below for the full derivation.

None of the three are confirm-gated (ADR-0003). The spec itself flags
`cancel_order`/`close_position` as "revisitable with the user" given their
irreversible effect despite being routine trading activity; the user was
explicitly asked and chose to keep both ungated, per ADR-0003's original
reasoning that these are routine, expected trading-bot actions, not the
kind of rare/catastrophic mistake confirm-gating protects against
elsewhere. Neither has a `confirm` parameter at all.

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
    OctoBotConnectionError,
    OctoBotMalformedResponseError,
    OctoBotTimeoutError,
    OrderCancelRejectedError,
    PortfolioRefreshFailedError,
    PositionCloseRejectedError,
)
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


# --- milestone 13.6: cancel_order/close_position/refresh_portfolio (ADR-0003) --


async def _request_raw(
    client: httpx.AsyncClient, method: str, path: str, **kwargs: Any
) -> httpx.Response:
    """Perform one request without decoding JSON, classifying only transport failures.

    Independently duplicated per this project's established convention (see
    `octobot_mcp.tools.trading_config`'s module docstring): can't reuse
    `octobot_mcp.client.request_json` here because a failed call needs
    OctoBot's own raw response body text (see `_extract_error_text` below),
    not just a generic classified exception with no body content.
    """
    try:
        return await client.request(method, path, **kwargs)
    except httpx.TimeoutException as exc:
        raise OctoBotTimeoutError(f"Request to OctoBot timed out: {method} {path}") from exc
    except httpx.HTTPError as exc:
        raise OctoBotConnectionError(f"Could not reach OctoBot: {method} {path} ({exc})") from exc


def _extract_error_text(response: httpx.Response) -> str:
    """Best-effort extraction of OctoBot's own error text from a non-2xx response.

    All three of this section's routes fail via `util.get_rest_reply(<plain
    string>, 500)`, confirmed against source: a real HTTP 500 whose body is
    a raw, unquoted string despite a `Content-Type: application/json` header
    that lies about the actual encoding -- the same pattern
    `tentacles.py`/`exchanges.py`/`trading_config.py` already document via
    their own identical copies of this helper. The JSON-first attempt is
    kept anyway for consistency with every other module and in case a
    future OctoBot version's error path changes.
    """
    try:
        body = response.json()
    except ValueError:
        return response.text.strip() or f"HTTP {response.status_code} with an empty body"
    if isinstance(body, dict) and "text" in body:
        return str(body["text"])
    return str(body)


@with_error_handling
async def cancel_order(ctx: Context, order_id: str) -> Any:
    """Cancel one order by id.

    Maps to `POST /api/orders?action=cancel_order` -- `action` is a query
    parameter, not a JSON body field (confirmed against source, the same
    pattern `octobot_mcp.tools.tentacles.update_tentacle_config` already
    documents for its own route). The JSON body is the raw `order_id` value
    itself, NOT wrapped in an object: confirmed against source
    (`Services/Interfaces/web_interface/api/trading.py::orders()` does
    `interfaces_util.cancel_orders([flask.request.get_json()])`, and
    `cancel_orders(order_ids)` -- `packages/services/octobot_services/
    interfaces/util/order.py` -- iterates its argument directly as a list of
    order ids). Use the `id` field from `get_orders()`'s own response items
    for this value.

    Success: OctoBot returns HTTP 200 with the literal JSON string `"Order
    cancelled"`, returned unchanged (NFR-8). Failure -- the order wasn't
    found on any connected exchange, including the case where every
    exchange manager tried raised an internal cancel error OctoBot itself
    silently swallows rather than propagating (see this module's docstring)
    -- raises `OrderCancelRejectedError` with OctoBot's own extracted error
    text (always, in practice, the literal `"Impossible to cancel order:
    order not found."`).

    Not confirm-gated: the user was explicitly asked about the spec's own
    footnote flagging this tool as "revisitable" given its irreversible
    effect, and chose to keep it ungated (milestone 13.6), per ADR-0003's
    original reasoning that this is routine, expected trading-bot activity.
    No `confirm` parameter exists on this tool at all.
    """
    client = get_client(ctx)
    response = await _request_raw(
        client, "POST", "/api/orders", params={"action": "cancel_order"}, json=order_id
    )
    if response.status_code >= 400:
        raise OrderCancelRejectedError(
            f"OctoBot rejected cancel_order(order_id={order_id!r}): {_extract_error_text(response)}"
        )
    try:
        return response.json()
    except ValueError as exc:
        raise OctoBotMalformedResponseError(
            "OctoBot returned a 2xx response with a non-JSON body for "
            "POST /api/orders (action=cancel_order)."
        ) from exc


@with_error_handling
async def close_position(ctx: Context, symbol: str, side: str) -> Any:
    """Close one open position, identified by `symbol` and `side`.

    Maps to `POST /api/positions?action=close_position` -- `action` is a
    query parameter, not a JSON body field (same pattern as `cancel_order`
    above). **A genuine spec correction**: the spec names this tool's
    parameter `position_id`, but OctoBot positions have no `id` concept at
    all -- confirmed against source (`Services/Interfaces/web_interface/
    models/trading.py::_dump_position`, the exact function behind
    `get_positions()`'s response, builds each position dict with
    `symbol`/`side`/`contract`/... keys but no `id` key, unlike
    `_dump_order`'s sibling function which does include `"id":
    order.order_id`). The real route calls `interfaces_util.close_positions(
    [{"symbol": ..., "side": ...}])` -- `packages/services/
    octobot_services/interfaces/util/position.py` -- which reads exactly
    `positions_desc["symbol"]`/`positions_desc["side"]`. `side` is submitted
    as OctoBot's own `PositionSide` enum's literal value (`"long"`/
    `"short"`/`"both"`/`"unknown"`, confirmed against
    `octobot_trading.enums.PositionSide`) -- the exact same string
    `get_positions()`'s own `side` field already uses, so a value read from
    that response can be passed straight through as both `symbol` and
    `side` here.

    Success: OctoBot returns HTTP 200 with the literal JSON string
    `"Position closed"`, returned unchanged (NFR-8). Failure -- no matching
    open position on any connected exchange -- raises
    `PositionCloseRejectedError` with OctoBot's own extracted error text
    (always, in practice, the literal `"Impossible to close position:
    position already closed."`).

    Not confirm-gated: the user was explicitly asked about the spec's own
    footnote flagging this tool as "revisitable" given its irreversible
    effect, and chose to keep it ungated (milestone 13.6), per ADR-0003's
    original reasoning that this is routine, expected trading-bot activity.
    No `confirm` parameter exists on this tool at all.
    """
    client = get_client(ctx)
    response = await _request_raw(
        client,
        "POST",
        "/api/positions",
        params={"action": "close_position"},
        json={"symbol": symbol, "side": side},
    )
    if response.status_code >= 400:
        raise PositionCloseRejectedError(
            f"OctoBot rejected close_position(symbol={symbol!r}, side={side!r}): "
            f"{_extract_error_text(response)}"
        )
    try:
        return response.json()
    except ValueError as exc:
        raise OctoBotMalformedResponseError(
            "OctoBot returned a 2xx response with a non-JSON body for "
            "POST /api/positions (action=close_position)."
        ) from exc


@with_error_handling
async def refresh_portfolio(ctx: Context) -> Any:
    """Trigger an immediate refresh of every enabled real (non-simulated) trader's portfolio.

    Maps to `POST /api/refresh_portfolio` -- confirmed against source as a
    bare POST with no request body at all (the route never calls
    `flask.request.get_json()`).

    Success: OctoBot returns HTTP 200 with the literal JSON string
    `"Portfolio(s) refreshed"`, returned unchanged (NFR-8). Failure -- zero
    enabled real traders across every connected exchange (confirmed against
    source, `octobot_services.interfaces.util.portfolio::
    async_trigger_portfolios_refresh` raises `RuntimeError("no real trader
    to update.")` in exactly this case) -- raises
    `PortfolioRefreshFailedError` with OctoBot's own extracted error text
    (always, in practice, the literal `"No portfolio to refresh"`). This is
    an entirely expected outcome on any OctoBot instance running only
    simulated trading, not a bug.

    Not confirm-gated (ADR-0003): triggers a routine, non-destructive
    portfolio data refresh, not a trade action. No `confirm` parameter
    exists on this tool at all.
    """
    client = get_client(ctx)
    response = await _request_raw(client, "POST", "/api/refresh_portfolio")
    if response.status_code >= 400:
        raise PortfolioRefreshFailedError(
            f"OctoBot rejected refresh_portfolio: {_extract_error_text(response)}"
        )
    try:
        return response.json()
    except ValueError as exc:
        raise OctoBotMalformedResponseError(
            "OctoBot returned a 2xx response with a non-JSON body for POST /api/refresh_portfolio."
        ) from exc


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
    mcp.tool()(cancel_order)
    mcp.tool()(close_position)
    mcp.tool()(refresh_portfolio)
