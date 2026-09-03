"""Server-level smoke tests: the server object constructs and exposes tools.

Uses the MCP SDK's in-memory Client (see docs/get-started/testing.md in the
SDK's own docs) so the handshake (initialize + tools/list) is exercised
without spawning a subprocess.

Milestone 1 registered zero tools; milestone 3 (docs/tasklist.md item 3)
registered the first 10; milestone 4 (item 4) adds the Tier-B profile
scrape adapter's `list_profiles`/`get_profile`; milestone 5 (item 5) adds
the non-destructive profile write tools `create_profile`/`update_profile`/
`select_profile`/`export_profile`. Each milestone updates this same
assertion to the new -- expected -- tool set instead of keeping a now-false
invariant (the pattern milestone 3 itself established over milestone 1's
`test_server_lists_zero_tools`). See
`test_tools_portfolio.py`/`test_tools_exchanges.py`/`test_tools_profiles.py`
for per-tool behavior and `test_server_tools_integration.py` for an
end-to-end call through this same `mcp` wiring.

Since milestone 3 also wires `mcp`'s real `app_lifespan` (NFR-11) into
this module-level `mcp` instance, connecting a `Client` to it now runs
`load_config()` against the real environment (ADR-0004) -- `OCTOBOT_BASE_URL`
must be set for these tests, even though they never issue a real HTTP
request (`list_tools`/capability negotiation don't touch the client).
"""

import pytest
from mcp import Client

from octobot_mcp.server import mcp

EXPECTED_MILESTONE_5_TOOL_NAMES = {
    "get_orders",
    "get_positions",
    "get_trades",
    "get_pnl_history",
    "get_historical_portfolio_value",
    "get_currency_list",
    "get_all_currencies",
    "get_all_symbols",
    "check_accounts_compatible",
    "get_exchange_details",
    "list_profiles",
    "get_profile",
    "create_profile",
    "update_profile",
    "select_profile",
    "export_profile",
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OCTOBOT_BASE_URL", "http://octobot.example")
    async with Client(mcp, raise_exceptions=True) as c:
        yield c


@pytest.mark.anyio
async def test_server_lists_the_milestone_5_tools(client: Client):
    result = await client.list_tools()
    assert {tool.name for tool in result.tools} == EXPECTED_MILESTONE_5_TOOL_NAMES


@pytest.mark.anyio
async def test_server_declares_tools_capability(client: Client):
    capabilities = client.server_capabilities
    assert capabilities is not None
    assert capabilities.tools is not None


def test_server_name():
    assert mcp.name == "OctoBot"
