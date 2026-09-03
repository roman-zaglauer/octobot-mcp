"""MCP server instance and entry point.

Per ADR-0001, this server speaks MCP over the stdio transport only: the
MCP client (e.g. Claude Code) launches this process and communicates over
its stdin/stdout. There is no network listener and no MCP-level
authentication.

Milestone 3 (see docs/tasklist.md item 3) registers this server's first
tools: the read-only Tier-A JSON tools in `octobot_mcp.tools`. The single
`httpx2.AsyncClient` those tools use is built once, at startup, by
`octobot_mcp.context.app_lifespan` and threaded into every tool call via
the MCP SDK's lifespan mechanism (NFR-11) -- never a module-level global.
"""

from mcp.server import MCPServer

from octobot_mcp.context import app_lifespan
from octobot_mcp.tools import register_all

mcp = MCPServer("OctoBot", lifespan=app_lifespan)
register_all(mcp)


def main() -> None:
    """Start the server on the stdio transport. Blocks until the client disconnects."""
    mcp.run()


if __name__ == "__main__":
    main()
