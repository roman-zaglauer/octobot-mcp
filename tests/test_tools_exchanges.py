"""Unit tests for the exchange lookup/compatibility tools (milestone 3).

Each tool gets a success-path test asserting the OctoBot route/method and
a passthrough (NFR-8) result, plus one error-classification test per
tool proving `OctoBotAPIError` never escapes -- it must arrive as
`mcp.server.mcpserver.exceptions.ToolError` (NFR-4).
"""

import json

import httpx2 as httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from octobot_mcp.tools.exchanges import (
    check_accounts_compatible,
    get_all_currencies,
    get_all_symbols,
    get_currency_list,
    get_exchange_details,
)
from tests.test_tools_shared import make_ctx


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_get_currency_list_calls_expected_route():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json=["BTC", "ETH"])

    result = await get_currency_list(make_ctx(httpx.MockTransport(handler)))
    assert seen["path"] == "/api/currency_list"
    assert result == ["BTC", "ETH"]


@pytest.mark.anyio
async def test_get_currency_list_classifies_5xx():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    with pytest.raises(ToolError):
        await get_currency_list(make_ctx(httpx.MockTransport(handler)))


@pytest.mark.anyio
async def test_get_all_currencies_uses_path_parameter():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json=["BTC", "ETH"])

    result = await get_all_currencies(make_ctx(httpx.MockTransport(handler)), "binance")
    assert seen["path"] == "/api/get_all_currencies/binance"
    assert result == ["BTC", "ETH"]


@pytest.mark.anyio
async def test_get_all_currencies_classifies_connection_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ToolError):
        await get_all_currencies(make_ctx(httpx.MockTransport(handler)), "binance")


@pytest.mark.anyio
async def test_get_all_symbols_uses_path_parameter():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json=["BTC/USDT"])

    result = await get_all_symbols(make_ctx(httpx.MockTransport(handler)), "binance")
    assert seen["path"] == "/api/get_all_symbols/binance"
    assert result == ["BTC/USDT"]


@pytest.mark.anyio
async def test_get_all_symbols_classifies_timeout():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    with pytest.raises(ToolError):
        await get_all_symbols(make_ctx(httpx.MockTransport(handler)), "binance")


@pytest.mark.anyio
async def test_check_accounts_compatible_sends_masked_placeholders_for_each_name():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["method"] = request.method
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"binance": {"compatible_account": True}})

    result = await check_accounts_compatible(
        make_ctx(httpx.MockTransport(handler)), ["binance"]
    )
    assert seen["path"] == "/api/are_compatible_accounts"
    assert seen["method"] == "POST"
    assert seen["body"] == {
        "binance": {
            "exchange": "binance",
            "apiKey": "******",
            "apiSecret": "******",
            "apiPassword": "******",
            "sandboxed": False,
        }
    }
    # No credential value in the output either (NFR-6 spirit, [V] confirmed
    # get_compatibility_result never echoes apiKey/apiSecret/apiPassword).
    assert "apiKey" not in str(result)
    assert result == {"binance": {"compatible_account": True}}


@pytest.mark.anyio
async def test_check_accounts_compatible_classifies_4xx():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "bad request"})

    with pytest.raises(ToolError):
        await check_accounts_compatible(make_ctx(httpx.MockTransport(handler)), ["binance"])


@pytest.mark.anyio
async def test_get_exchange_details_calls_expected_route():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"exchange_name": "binance", "exchange_id": "abc"})

    result = await get_exchange_details(
        make_ctx(httpx.MockTransport(handler)), exchange_name="binance"
    )
    assert seen["path"] == "/api/first_exchange_details"
    assert seen["params"] == {"exchange_name": "binance"}
    assert result == {"exchange_name": "binance", "exchange_id": "abc"}


@pytest.mark.anyio
async def test_get_exchange_details_classifies_malformed_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    with pytest.raises(ToolError):
        await get_exchange_details(make_ctx(httpx.MockTransport(handler)))
