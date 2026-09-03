"""Shared test helpers for tool unit tests (milestone 3).

Builds a minimal `Context` whose `ctx.request_context.lifespan_context`
is a real `octobot_mcp.context.AppContext` wrapping an `httpx2.AsyncClient`
backed by an `httpx2.MockTransport` -- the same fixture shape milestone
2's `test_client.py` uses for `request_json`, one layer up. This
exercises exactly the NFR-11 injection path (`get_client(ctx)` ->
`ctx.request_context.lifespan_context.client`) without spinning up the
full MCP protocol stack, matching how a tool function actually reads the
client.

Milestone 7 (docs/tasklist.md item 7) adds an optional `jobs` parameter so
`tests/test_tools_jobs.py` can inject a `JobStore` it has already populated
with synthetic records -- the exact same `get_job_store(ctx)` injection
path a real tool call uses, just with a store the test controls directly
instead of one built fresh by `app_lifespan`.
"""

from types import SimpleNamespace

import httpx2 as httpx
from mcp.server.mcpserver.context import Context

from octobot_mcp.context import AppContext
from octobot_mcp.jobs import JobStore


def make_ctx(
    transport: httpx.MockTransport,
    base_url: str = "http://octobot.example",
    jobs: JobStore | None = None,
) -> Context:
    """Build a `Context` whose injected client is backed by `transport`.

    `jobs` defaults to a fresh, empty `JobStore` (via `AppContext`'s own
    default) when omitted; pass one explicitly to assert against the same
    store instance a job tool mutated.
    """
    client = httpx.AsyncClient(base_url=base_url, transport=transport)
    app_context = AppContext(client=client) if jobs is None else AppContext(client=client, jobs=jobs)
    request_context = SimpleNamespace(lifespan_context=app_context)
    return Context(request_context=request_context)
