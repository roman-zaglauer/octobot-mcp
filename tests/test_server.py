"""Milestone 1 smoke tests: the server object constructs and exposes zero tools.

Uses the MCP SDK's in-memory Client (see docs/get-started/testing.md in the
SDK's own docs) so the handshake (initialize + tools/list) is exercised
without spawning a subprocess.
"""

import pytest
from mcp import Client

from octobot_mcp.server import mcp


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    async with Client(mcp, raise_exceptions=True) as c:
        yield c


@pytest.mark.anyio
async def test_server_lists_zero_tools(client: Client):
    result = await client.list_tools()
    assert result.tools == []


@pytest.mark.anyio
async def test_server_declares_tools_capability(client: Client):
    capabilities = client.server_capabilities
    assert capabilities is not None
    assert capabilities.tools is not None


def test_server_name():
    assert mcp.name == "OctoBot"
