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

## `get_portfolio_snapshot` (milestone 20, docs/tasklist.md item 20, ADR-0011, FR-33/NFR-19)

A fourth Tier-B HTML-scrape read adapter (mirroring
`octobot_mcp.tools.profiles`/`octobot_mcp.tools.data_collection`/
`octobot_mcp.tools.trading_config.list_evaluators`'s own adapters), added
here rather than its own module since the tool spec places it in this
module's domain (portfolio reads) alongside this module's existing
Tier-A siblings -- same precedent `list_evaluators` set for
`trading_config.py`. Closes a gap where no existing tool gave a current
per-asset holdings breakdown directly: `get_historical_portfolio_value` is
a time-series of TOTAL value in one currency; `get_orders`/`get_positions`/
`get_trades` give no current per-asset breakdown at all.

No JSON API exists for this page (ADR-0011 Context: `GET /api/portfolio`,
`/api/portfolios`, `/api/portfolio_assets`, `/portfolio?format=json` were
all tried directly against the live instance and every one returned
OctoBot's HTML 404-fallback page or the unchanged bare-page HTML, never
JSON) -- so this scrapes `GET /portfolio`'s own bare, server-rendered HTML
with BeautifulSoup instead, exactly like every other Tier-B adapter in this
project.

**Confirmed DOM shape (ADR-0011 Context, OctoBot 2.1.1, re-verified this
session against a fresh live capture)**: `<div id="portfoliosCard" ...
reference_market="EUR">` carries the profile's reference currency as an
attribute. Each held asset is one `<tr class="symbol-holding text-center">`
inside `<table id="holdings-table">` (itself inside `#portfolio-display`),
with exactly 5 `<td>` cells in document order: asset (a nested
`<span class="symbol">`), total, value-in-reference-currency, available,
locked-in-orders. The total/available/locked-in-orders cells each carry
`data-toggle="tooltip" title="<Exchange>: <amount>"` with the
full-precision raw value (confirmed live: displayed/rounded cell text
`"195.908"` vs. tooltip `title="Binance: 195.90814133"`); the
value-in-reference-currency cell (class `total-value`) carries NO
`title`/tooltip attribute at all on this OctoBot version -- there is no
full-precision source for that one column via this route, so this
adapter deliberately never adds an `exact` key for it.

**Per-exchange tooltip parsing (ADR-0011 Decision 4, requirements doc open
question #15, `[UNVERIFIED]`)**: the live test instance has exactly one
connected exchange, so only a single `"<Exchange>: <amount>"` pair was ever
observed in a tooltip -- how OctoBot renders this `title` when an asset is
held across >=2 exchanges (comma/semicolon/newline-separated? some other
shape?) is unconfirmed and must not be guessed. This adapter therefore
parses a tooltip ONLY when it cleanly matches the single confirmed
`"<exchange>: <amount>"` shape (one colon, both sides non-empty, the
amount side parsing as a float) -- `total`/`available`/`locked_in_orders`
then carry `exact`/`per_exchange` populated from that one pair. Any
tooltip that doesn't match this cleanly (missing, multiple candidate
values, a non-numeric amount) degrades to `exact: null, per_exchange:
null` -- NEVER raised, NEVER a guessed split -- the `rounded` value is
still always correctly extracted either way (`rounded` comes from the
cell's own display text, parsed independently of the tooltip).

**Fail-loud gate (NFR-19), deliberately scoped differently than
`ProfileScrapeContractBrokenError`'s "zero is always broken" model**:
`PortfolioScrapeContractBrokenError` is raised only if `#portfoliosCard`
is missing, its `reference_market` attribute is missing/empty,
`#holdings-table` is missing, a row is missing its `.symbol` span, or a
row's cell count isn't exactly 5 -- **never** for zero `tr.symbol-holding`
rows (a legitimately empty portfolio, zero holdings anywhere, is a real
OctoBot state, confirmed by ADR-0011 Decision 3 -- unlike
`list_profiles`'s "OctoBot always ships built-in profiles" invariant).

Idempotent, read-only, not confirm-gated (ADR-0003).

No `from __future__ import annotations` here: see
`octobot_mcp.tools._shared.with_error_handling`'s docstring for why
postponed annotations would silently break this module's Context
injection.
"""

import re
from typing import Any

import httpx2 as httpx
from bs4 import BeautifulSoup
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context

from octobot_mcp.client import request_json
from octobot_mcp.context import get_client
from octobot_mcp.errors import (
    OctoBotClientError,
    OctoBotConnectionError,
    OctoBotMalformedResponseError,
    OctoBotServerError,
    OctoBotTimeoutError,
    OrderCancelRejectedError,
    PortfolioRefreshFailedError,
    PortfolioScrapeContractBrokenError,
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


# --- milestone 20: get_portfolio_snapshot (ADR-0011, Tier B) ----------------

_PORTFOLIO_PATH = "/portfolio"

#: ADR-0011's one confirmed tooltip shape: a single `"<Exchange>: <amount>"`
#: pair, nothing more. Deliberately does NOT attempt to split on any
#: separator for a hypothetical multi-exchange tooltip -- requirements doc
#: open question #15 is unresolved, so a tooltip that doesn't match this
#: exactly (whole-string, one colon, both sides non-empty) degrades to
#: `exact: null, per_exchange: null` rather than guessing a split.
_SINGLE_EXCHANGE_TOOLTIP_RE = re.compile(r"^\s*([^:]+):\s*(\S+)\s*$")


async def _fetch_portfolio_html(client: httpx.AsyncClient) -> str:
    """Fetch `/portfolio`'s raw HTML body, classified like `request_json` (NFR-4).

    Mirrors `octobot_mcp.tools.profiles._fetch_profiles_selector_html`'s
    identically-purposed helper (independently duplicated, not imported --
    this project's established convention): this route renders HTML, not
    JSON, so `octobot_mcp.client.request_json` can't be reused here.
    """
    try:
        response = await client.request("GET", _PORTFOLIO_PATH)
    except httpx.TimeoutException as exc:
        raise OctoBotTimeoutError(f"Request to OctoBot timed out: GET {_PORTFOLIO_PATH}") from exc
    except httpx.HTTPError as exc:
        raise OctoBotConnectionError(f"Could not reach OctoBot: GET {_PORTFOLIO_PATH} ({exc})") from exc

    if response.status_code >= 500:
        raise OctoBotServerError(
            f"OctoBot returned server error {response.status_code} for GET {_PORTFOLIO_PATH}",
            status_code=response.status_code,
        )
    if response.status_code >= 400:
        raise OctoBotClientError(
            f"OctoBot returned client error {response.status_code} for GET {_PORTFOLIO_PATH}",
            status_code=response.status_code,
        )
    return response.text


def _parse_rounded_cell(cell: Any, *, asset: str, field: str) -> float:
    """Parse one amount cell's displayed (rounded) text as a float -- always expected to succeed.

    Raises `PortfolioScrapeContractBrokenError` if the cell's own displayed
    text isn't numeric at all -- a genuine structural break (this is the
    one value this adapter's own spec guarantees is "always populated"),
    distinct from the tooltip's own best-effort, never-raising parse below.
    """
    text = cell.get_text(strip=True)
    try:
        return float(text)
    except ValueError as exc:
        raise PortfolioScrapeContractBrokenError(
            f"Could not parse asset {asset!r}'s {field!r} cell as a number: {text!r}."
        ) from exc


def _parse_tooltip_exact(cell: Any) -> tuple[float | None, list[dict[str, Any]] | None]:
    """Best-effort parse of one amount cell's `title` tooltip (ADR-0011 Decision 4).

    Returns `(exact, per_exchange)`, both `None` the instant the tooltip is
    missing or doesn't cleanly match the one confirmed single-exchange
    `"<Exchange>: <amount>"` shape -- NEVER raises, NEVER guesses a split
    for an unconfirmed multi-exchange format (requirements doc open
    question #15).
    """
    title = cell.get("title")
    if not title:
        return None, None
    match = _SINGLE_EXCHANGE_TOOLTIP_RE.match(title)
    if match is None:
        return None, None
    exchange, amount_text = match.group(1).strip(), match.group(2)
    if not exchange:
        return None, None
    try:
        amount = float(amount_text)
    except ValueError:
        return None, None
    return amount, [{"exchange": exchange, "amount": amount}]


def _parse_amount_cell(cell: Any, *, asset: str, field: str) -> dict[str, Any]:
    """Parse one `total`/`available`/`locked_in_orders` cell into its full shape."""
    rounded = _parse_rounded_cell(cell, asset=asset, field=field)
    exact, per_exchange = _parse_tooltip_exact(cell)
    return {"rounded": rounded, "exact": exact, "per_exchange": per_exchange}


def _parse_portfolio_html(html: str) -> dict[str, Any]:
    """Parse `/portfolio`'s rendered HTML into the snapshot shape (fail-loud, NFR-19).

    See this module's docstring (the `get_portfolio_snapshot` section) for
    the exact DOM shape and the full list of assumptions asserted here.
    Raises `PortfolioScrapeContractBrokenError` -- never returns a
    partial/best-effort result -- the instant a structural assumption
    doesn't hold. Zero `tr.symbol-holding` rows is NOT such an assumption
    (ADR-0011 Decision 3): a legitimately empty portfolio returns
    `assets: []`.
    """
    soup = BeautifulSoup(html, "html.parser")

    card = soup.find(id="portfoliosCard")
    if card is None:
        raise PortfolioScrapeContractBrokenError(
            f"No element with id='portfoliosCard' found on {_PORTFOLIO_PATH}. "
            "This means the page structure has changed -- verify against "
            "the current OctoBot version (see octobot_mcp.tools.portfolio's "
            "module docstring for the last-confirmed version)."
        )
    reference_currency = card.get("reference_market")
    if not reference_currency:
        raise PortfolioScrapeContractBrokenError(
            "#portfoliosCard is missing its reference_market attribute."
        )

    table = soup.find(id="holdings-table")
    if table is None:
        raise PortfolioScrapeContractBrokenError(
            f"No element with id='holdings-table' found on {_PORTFOLIO_PATH}. "
            "This means the page structure has changed, not that the "
            "portfolio is empty (a legitimately empty portfolio still "
            "renders an intact, empty holdings-table -- see this module's "
            "docstring)."
        )

    assets: list[dict[str, Any]] = []
    for row in table.find_all("tr", class_="symbol-holding"):
        symbol_span = row.find("span", class_="symbol")
        if symbol_span is None:
            raise PortfolioScrapeContractBrokenError(
                "A tr.symbol-holding row is missing its .symbol span."
            )
        asset = symbol_span.get_text(strip=True)

        cells = row.find_all("td")
        if len(cells) != 5:
            raise PortfolioScrapeContractBrokenError(
                f"Expected exactly 5 <td> cells per holding row, found "
                f"{len(cells)} for asset {asset!r}."
            )

        assets.append(
            {
                "asset": asset,
                "total": _parse_amount_cell(cells[1], asset=asset, field="total"),
                "value_in_reference_currency": {
                    "rounded": _parse_rounded_cell(
                        cells[2], asset=asset, field="value_in_reference_currency"
                    )
                },
                "available": _parse_amount_cell(cells[3], asset=asset, field="available"),
                "locked_in_orders": _parse_amount_cell(cells[4], asset=asset, field="locked_in_orders"),
            }
        )

    return {"reference_currency": reference_currency, "assets": assets}


@with_error_handling
async def get_portfolio_snapshot(ctx: Context) -> Any:
    """Read a current per-asset portfolio snapshot (asset, total, value in
    the profile's reference currency, available, locked-in-orders).

    Tier-B (ADR-0011): scrapes `GET /portfolio` -- see this module's
    docstring (the `get_portfolio_snapshot` section) for the verified DOM
    shape and fail-loud assumptions. Idempotent, read-only, not
    confirm-gated.

    Output: `{"reference_currency": str, "assets": [{"asset": str, "total":
    {"rounded": float, "exact": float|null, "per_exchange": [{"exchange":
    str, "amount": float}]|null}, "value_in_reference_currency": {"rounded":
    float}, "available": <same shape as "total">, "locked_in_orders": <same
    shape as "total">}]}`.

    `exact`/`per_exchange` for `total`/`available`/`locked_in_orders` are
    populated only when that cell's tooltip cleanly matches the one
    confirmed single-exchange `"<Exchange>: <amount>"` shape -- `null`
    otherwise (see this module's docstring for why the multi-exchange case
    is `[UNVERIFIED]` and deliberately never guessed).
    `value_in_reference_currency` carries only `rounded` -- this column has
    no tooltip on this OctoBot version, confirmed live, so there is nothing
    to parse; this is the documented, correct behavior, not an oversight.

    Raises `PortfolioScrapeContractBrokenError` if `#holdings-table`/
    `#portfolio-display`'s structure is missing, a row is missing its
    `.symbol` span, or `#portfoliosCard`'s `reference_market` attribute is
    missing -- never for zero `assets` (a legitimately empty portfolio).
    """
    html = await _fetch_portfolio_html(get_client(ctx))
    return _parse_portfolio_html(html)


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
    mcp.tool()(get_portfolio_snapshot)
