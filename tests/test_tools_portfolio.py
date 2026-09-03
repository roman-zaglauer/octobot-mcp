"""Unit tests for the portfolio/trading read tools (milestone 3).

Each tool gets a success-path test asserting the OctoBot route/method and
a passthrough (NFR-8) result, plus one error-classification test per
tool proving `OctoBotAPIError` never escapes -- it must arrive as
`mcp.server.mcpserver.exceptions.ToolError` (NFR-4), not the raw
classified exception, so the MCP SDK reports a structured `is_error=True`
result instead of logging a crash and hiding the classified message.
"""

import httpx2 as httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from octobot_mcp.tools.portfolio import (
    get_historical_portfolio_value,
    get_orders,
    get_pnl_history,
    get_positions,
    get_trades,
)
from tests.test_tools_shared import make_ctx


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_get_orders_returns_octobot_payload_unchanged():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(200, json=[{"order_id": "1", "extra_field": "kept"}])

    result = await get_orders(make_ctx(httpx.MockTransport(handler)))
    assert seen == {"method": "GET", "path": "/api/orders"}
    assert result == [{"order_id": "1", "extra_field": "kept"}]


@pytest.mark.anyio
async def test_get_orders_classifies_5xx_as_tool_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    with pytest.raises(ToolError):
        await get_orders(make_ctx(httpx.MockTransport(handler)))


@pytest.mark.anyio
async def test_get_positions_returns_octobot_payload_unchanged():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/positions"
        return httpx.Response(200, json=[{"position_id": "p1"}])

    result = await get_positions(make_ctx(httpx.MockTransport(handler)))
    assert result == [{"position_id": "p1"}]


@pytest.mark.anyio
async def test_get_positions_classifies_connection_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ToolError):
        await get_positions(make_ctx(httpx.MockTransport(handler)))


@pytest.mark.anyio
async def test_get_trades_returns_octobot_payload_unchanged():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/trades"
        return httpx.Response(200, json=[{"trade_id": "t1"}])

    result = await get_trades(make_ctx(httpx.MockTransport(handler)))
    assert result == [{"trade_id": "t1"}]


@pytest.mark.anyio
async def test_get_trades_classifies_timeout():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    with pytest.raises(ToolError):
        await get_trades(make_ctx(httpx.MockTransport(handler)))


@pytest.mark.anyio
async def test_get_pnl_history_forwards_only_given_filters_as_query_params():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"pnl": []})

    result = await get_pnl_history(
        make_ctx(httpx.MockTransport(handler)), exchange="binance", since="123"
    )
    assert seen["path"] == "/api/pnl_history"
    assert seen["params"] == {"exchange": "binance", "since": "123"}
    assert result == {"pnl": []}


@pytest.mark.anyio
async def test_get_pnl_history_classifies_4xx():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    with pytest.raises(ToolError):
        await get_pnl_history(make_ctx(httpx.MockTransport(handler)))


@pytest.mark.anyio
async def test_get_historical_portfolio_value_forwards_only_given_filters():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"values": []})

    result = await get_historical_portfolio_value(
        make_ctx(httpx.MockTransport(handler)), currency="BTC"
    )
    assert seen["path"] == "/api/historical_portfolio_value"
    assert seen["params"] == {"currency": "BTC"}
    assert result == {"values": []}


@pytest.mark.anyio
async def test_get_historical_portfolio_value_classifies_malformed_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json</html>")

    with pytest.raises(ToolError):
        await get_historical_portfolio_value(make_ctx(httpx.MockTransport(handler)))
