"""MCP server instance and entry point.

Per ADR-0001, this server speaks MCP over the stdio transport only: the
MCP client (e.g. Claude Code) launches this process and communicates over
its stdin/stdout. There is no network listener and no MCP-level
authentication.

Milestone 1 registers zero tools. Later milestones (see docs/tasklist.md)
add the OctoBot HTTP client and tools one at a time.
"""

from mcp.server import MCPServer

mcp = MCPServer("OctoBot")


def main() -> None:
    """Start the server on the stdio transport. Blocks until the client disconnects."""
    mcp.run()


if __name__ == "__main__":
    main()
