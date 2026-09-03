"""OctoBot MCP server.

An MCP (Model Context Protocol) server that wraps a single OctoBot
instance's web API so an AI agent can manage trading profiles and run
backtests. See docs/requirements and docs/adr for the full design.

This package currently ships milestone 1 of the roadmap in
docs/tasklist.md: project scaffolding and a stdio MCP server skeleton
registering zero tools. Later milestones add the OctoBot HTTP client and
tools incrementally.
"""

from octobot_mcp.server import mcp

__all__ = ["mcp"]
