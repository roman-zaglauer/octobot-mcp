"""Shared test helpers for tool unit tests (milestone 3).

Builds a minimal `Context` whose `ctx.request_context.lifespan_context`
is a real `octobot_mcp.context.AppContext` wrapping an `httpx2.AsyncClient`
backed by an `httpx2.MockTransport` -- the same fixture shape milestone
2's `test_client.py` uses for `request_json`, one layer up. This
exercises exactly the NFR-11 injection path (`get_client(ctx)` ->
`ctx.request_context.lifespan_context.client`) without spinning up the
full MCP protocol stack, matching how a tool function actually reads the
client.
"""

from types import SimpleNamespace

import httpx2 as httpx
from mcp.server.mcpserver.context import Context

from octobot_mcp.context import AppContext


def make_ctx(transport: httpx.MockTransport, base_url: str = "http://octobot.example") -> Context:
    """Build a `Context` whose injected client is backed by `transport`."""
    client = httpx.AsyncClient(base_url=base_url, transport=transport)
    request_context = SimpleNamespace(lifespan_context=AppContext(client=client))
    return Context(request_context=request_context)
