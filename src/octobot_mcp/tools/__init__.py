"""Tool registration entry point.

`server.py` calls `register_all(mcp)` once, right after constructing
`mcp` and before `mcp.run()`, to register every tool this package
defines. Each domain submodule (`portfolio.py`, `exchanges.py`, and
whatever milestone 4+ adds) owns its own tool functions and exposes a
`register(mcp)` that adds them -- add a new submodule and one line here
for the next milestone's tools, rather than growing one file.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from octobot_mcp.tools import (
    automations,
    backtesting,
    data_collection,
    exchanges,
    instance_lifecycle,
    interfaces,
    jobs,
    portfolio,
    profiles,
    tentacles,
    trading_config,
)

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

__all__ = ["register_all"]


def register_all(mcp: "MCPServer") -> None:
    """Register every tool from every domain submodule onto `mcp`."""
    portfolio.register(mcp)
    exchanges.register(mcp)
    profiles.register(mcp)
    jobs.register(mcp)
    backtesting.register(mcp)
    tentacles.register(mcp)
    trading_config.register(mcp)
    data_collection.register(mcp)
    instance_lifecycle.register(mcp)
    interfaces.register(mcp)
    automations.register(mcp)
