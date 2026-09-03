"""Server lifespan and dependency-injection context (NFR-11).

Per NFR-11, tool implementations must not reach for the OctoBot HTTP client
as a module-level global accessed by name from arbitrary call sites. This
project uses the MCP SDK's `MCPServer(lifespan=...)` mechanism instead: an
async context manager that runs once for the whole process, builds
whatever the server needs for its lifetime, and yields it as the "lifespan
context." The SDK threads that yielded value into every request as
`ctx.request_context.lifespan_context` (see
`mcp.server.mcpserver.context.Context.request_context` and
`mcp.server.context.ServerRequestContext.lifespan_context`), which is
exactly the "single injected context object" NFR-11 requires.

This module owns both halves of that wiring:
- `AppContext`: the dataclass that *is* the injected context object.
- `app_lifespan`: the async context manager `server.py` passes to
  `MCPServer(..., lifespan=app_lifespan)`.
- `get_client`: the one accessor every tool uses to reach the client,
  so a future second OctoBot instance (out of v1's scope, ADR-0004) is a
  change to this module alone, never to each tool.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx2 as httpx

from octobot_mcp.client import create_http_client
from octobot_mcp.config import load_config

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.context import Context


@dataclass(frozen=True)
class AppContext:
    """The single object injected into every tool call for this server (NFR-11).

    Holds the one `httpx2.AsyncClient` built at startup from
    `load_config()` + `create_http_client()` and kept for the process
    lifetime (ADR-0004: exactly one OctoBot instance per process).
    """

    client: httpx.AsyncClient


@asynccontextmanager
async def app_lifespan(server: "MCPServer") -> AsyncIterator[AppContext]:
    """Build the one OctoBot HTTP client for the process lifetime.

    Runs once, when the MCP server starts (before it accepts any request)
    and tears the client down once, when the server shuts down -- the SDK
    keeps this context manager open for the whole session and yields its
    result to every request handler.
    """
    config = load_config()
    client = create_http_client(config)
    try:
        yield AppContext(client=client)
    finally:
        await client.aclose()


def get_client(ctx: "Context") -> httpx.AsyncClient:
    """The single accessor tool functions use to reach the injected HTTP client.

    Every tool takes a `ctx: Context` parameter (the MCP SDK injects it by
    type annotation) and calls `get_client(ctx)` instead of importing a
    module-level client -- this is the one place that knows the lifespan
    context's shape.
    """
    return ctx.request_context.lifespan_context.client
