"""End-to-end tool-call tests through the real MCP protocol stack (milestone 3).

Unlike `test_tools_portfolio.py`/`test_tools_exchanges.py` (which call a
tool function directly with a hand-built `Context`), this drives the same
tool functions through `MCPServer.list_tools()`/`call_tool()` via the
SDK's in-memory `Client` (same technique as `test_server.py`), proving the
NFR-11 lifespan wiring and the NFR-4 error-to-`is_error` translation both
work through the actual `tools/call` request path, not just when called
directly.

Builds a *separate* `MCPServer` instance with a test-only lifespan (an
`httpx2.MockTransport`-backed client) rather than reusing
`octobot_mcp.server.mcp` (whose real `app_lifespan` reads
`OCTOBOT_BASE_URL` from the environment) -- `client.py`/`config.py` are
untouched; this only exercises the same `octobot_mcp.tools.register_all`
registration `server.py` itself calls.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx2 as httpx
import pytest
from mcp import Client
from mcp.server import MCPServer

from octobot_mcp.context import AppContext
from octobot_mcp.tools import register_all


def _build_test_server(handler) -> MCPServer:
    @asynccontextmanager
    async def test_lifespan(_server: MCPServer) -> AsyncIterator[AppContext]:
        client = httpx.AsyncClient(
            base_url="http://octobot.example", transport=httpx.MockTransport(handler)
        )
        try:
            yield AppContext(client=client)
        finally:
            await client.aclose()

    server = MCPServer("OctoBot-test", lifespan=test_lifespan)
    register_all(server)
    return server


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_get_orders_succeeds_through_the_full_protocol_stack():
    # A dict body (rather than a bare list) keeps the unstructured-output
    # conversion to exactly one text content block, so this test can
    # assert on it directly without depending on the SDK's list-to-blocks
    # heuristic, which is incidental to what this test is verifying.
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/orders"
        return httpx.Response(200, json={"orders": [{"order_id": "1"}]})

    server = _build_test_server(handler)
    async with Client(server, raise_exceptions=True) as client:
        result = await client.call_tool("get_orders", {})
        assert result.is_error is not True
        (text_block,) = result.content
        assert json.loads(text_block.text) == {"orders": [{"order_id": "1"}]}


@pytest.mark.anyio
async def test_get_positions_failure_surfaces_as_structured_mcp_error_not_a_crash():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    server = _build_test_server(handler)
    # raise_exceptions=False (the default) so a tool failure comes back as
    # a normal `CallToolResult(is_error=True)`, not an exception the test
    # would have to catch -- this is what "never a raw traceback reaching
    # the MCP client" (NFR-4) looks like from the client's own vantage point.
    async with Client(server) as client:
        result = await client.call_tool("get_positions", {})
        assert result.is_error is True
        # The classified OctoBot failure text, not a generic crash message
        # or a Python traceback, is what the agent actually sees.
        text = "".join(
            block.text for block in result.content if getattr(block, "text", None)
        )
        assert "OctoBotServerError" in text
        assert "Traceback" not in text
