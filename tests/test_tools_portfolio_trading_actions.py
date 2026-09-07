"""Unit tests for milestone 13.6's `cancel_order`/`close_position`/`refresh_portfolio`
(docs/tasklist.md item 13.6).

See `src/octobot_mcp/tools/portfolio.py`'s module docstring for the exact
request/response shapes these tests assert, verified against OctoBot source
as part of this milestone's implementation -- including the genuine spec
correction for `close_position` (`symbol`/`side`, not `position_id`; OctoBot
positions have no `id` concept at all).

None of the three are confirm-gated (ADR-0003): the user was explicitly
asked about the spec's own footnote flagging `cancel_order`/`close_position`
as "revisitable" given their irreversible effect, and chose to keep both
ungated. Each tool gets a success-path test (real-looking request/response
shapes), a failure-classification test reproducing OctoBot's own confirmed
"real HTTP 500, but Content-Type: application/json lies about a plain-text
body" shape, a generic 4xx-classification test, and (together, at the
bottom) one signature-inspection test confirming none of the three tools has
a `confirm` parameter at all.
"""

import inspect
import json

import httpx2 as httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from octobot_mcp.tools.portfolio import cancel_order, close_position, refresh_portfolio
from tests.test_tools_shared import make_ctx


@pytest.fixture
def anyio_backend():
    return "asyncio"


# --- cancel_order ------------------------------------------------------


@pytest.mark.anyio
async def test_cancel_order_sends_raw_order_id_as_the_json_body():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/orders"
        assert dict(request.url.params) == {"action": "cancel_order"}
        # Confirmed against source: the JSON body is the raw order_id value
        # itself, not wrapped in an object.
        assert json.loads(request.content) == "order-123"
        return httpx.Response(200, json="Order cancelled")

    result = await cancel_order(make_ctx(httpx.MockTransport(handler)), order_id="order-123")
    assert result == "Order cancelled"


@pytest.mark.anyio
async def test_cancel_order_classifies_the_real_but_lying_content_type_500():
    # Reproduces OctoBot's own confirmed shape: a real, trustworthy HTTP 500
    # whose body is a raw, unquoted plain-text string despite a
    # Content-Type: application/json header.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            content=b"Impossible to cancel order: order not found.",
            headers={"Content-Type": "application/json"},
        )

    with pytest.raises(ToolError) as exc_info:
        await cancel_order(make_ctx(httpx.MockTransport(handler)), order_id="missing")
    assert "OrderCancelRejectedError" in str(exc_info.value)
    assert "Impossible to cancel order: order not found." in str(exc_info.value)


@pytest.mark.anyio
async def test_cancel_order_classifies_4xx():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad request")

    with pytest.raises(ToolError) as exc_info:
        await cancel_order(make_ctx(httpx.MockTransport(handler)), order_id="order-123")
    assert "OrderCancelRejectedError" in str(exc_info.value)


@pytest.mark.anyio
async def test_cancel_order_classifies_malformed_2xx_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json</html>")

    with pytest.raises(ToolError) as exc_info:
        await cancel_order(make_ctx(httpx.MockTransport(handler)), order_id="order-123")
    assert "OctoBotMalformedResponseError" in str(exc_info.value)


# --- close_position ------------------------------------------------------


@pytest.mark.anyio
async def test_close_position_sends_symbol_and_side_under_those_keys():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/positions"
        assert dict(request.url.params) == {"action": "close_position"}
        assert json.loads(request.content) == {"symbol": "BTC/USDT", "side": "long"}
        return httpx.Response(200, json="Position closed")

    result = await close_position(
        make_ctx(httpx.MockTransport(handler)), symbol="BTC/USDT", side="long"
    )
    assert result == "Position closed"


@pytest.mark.anyio
async def test_close_position_classifies_the_real_but_lying_content_type_500():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            content=b"Impossible to close position: position already closed.",
            headers={"Content-Type": "application/json"},
        )

    with pytest.raises(ToolError) as exc_info:
        await close_position(
            make_ctx(httpx.MockTransport(handler)), symbol="BTC/USDT", side="long"
        )
    assert "PositionCloseRejectedError" in str(exc_info.value)
    assert "Impossible to close position: position already closed." in str(exc_info.value)


@pytest.mark.anyio
async def test_close_position_classifies_4xx():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    with pytest.raises(ToolError) as exc_info:
        await close_position(
            make_ctx(httpx.MockTransport(handler)), symbol="BTC/USDT", side="long"
        )
    assert "PositionCloseRejectedError" in str(exc_info.value)


@pytest.mark.anyio
async def test_close_position_classifies_malformed_2xx_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json</html>")

    with pytest.raises(ToolError) as exc_info:
        await close_position(
            make_ctx(httpx.MockTransport(handler)), symbol="BTC/USDT", side="long"
        )
    assert "OctoBotMalformedResponseError" in str(exc_info.value)


# --- refresh_portfolio ------------------------------------------------------


@pytest.mark.anyio
async def test_refresh_portfolio_sends_a_bare_post_with_no_body():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/refresh_portfolio"
        assert request.content == b""
        return httpx.Response(200, json="Portfolio(s) refreshed")

    result = await refresh_portfolio(make_ctx(httpx.MockTransport(handler)))
    assert result == "Portfolio(s) refreshed"


@pytest.mark.anyio
async def test_refresh_portfolio_classifies_the_real_but_lying_content_type_500():
    # Reproduces the confirmed "no real trader to update" failure -- an
    # entirely expected outcome on a simulated-only OctoBot instance, not a
    # bug, but still surfaced as a classified ToolError, never silently.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            content=b"No portfolio to refresh",
            headers={"Content-Type": "application/json"},
        )

    with pytest.raises(ToolError) as exc_info:
        await refresh_portfolio(make_ctx(httpx.MockTransport(handler)))
    assert "PortfolioRefreshFailedError" in str(exc_info.value)
    assert "No portfolio to refresh" in str(exc_info.value)


@pytest.mark.anyio
async def test_refresh_portfolio_classifies_4xx():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad request")

    with pytest.raises(ToolError) as exc_info:
        await refresh_portfolio(make_ctx(httpx.MockTransport(handler)))
    assert "PortfolioRefreshFailedError" in str(exc_info.value)


@pytest.mark.anyio
async def test_refresh_portfolio_classifies_malformed_2xx_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json</html>")

    with pytest.raises(ToolError) as exc_info:
        await refresh_portfolio(make_ctx(httpx.MockTransport(handler)))
    assert "OctoBotMalformedResponseError" in str(exc_info.value)


# --- not confirm-gated (ADR-0003) -- signature-level proof, not just an unused arg ---


def test_none_of_the_three_have_a_confirm_parameter():
    assert list(inspect.signature(cancel_order).parameters) == ["ctx", "order_id"]
    assert list(inspect.signature(close_position).parameters) == ["ctx", "symbol", "side"]
    assert list(inspect.signature(refresh_portfolio).parameters) == ["ctx"]
    for tool in (cancel_order, close_position, refresh_portfolio):
        assert "confirm" not in inspect.signature(tool).parameters
