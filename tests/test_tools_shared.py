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

Milestone 11 (docs/tasklist.md item 11) adds optional `progress_token`/
`session` parameters so `tests/test_tools_backtesting.py` can build a
`Context` that carries an MCP `progressToken` (`ctx.request_context.meta`)
and a fake `ctx.session` (whatever `octobot_mcp.tools.backtesting` needs to
build a progress notifier -- see that module's docstring) -- the exact same
two things `start_backtest`/`compare_strategies` read off a real `Context`.
Both default to `None` (the common no-token case), matching a real
`ServerRequestContext` whose `meta` is `None` when the caller supplied none.
"""

from types import SimpleNamespace
from typing import Any

import httpx2 as httpx
from mcp.server.mcpserver.context import Context

from octobot_mcp.context import AppContext
from octobot_mcp.jobs import JobStore


def make_ctx(
    transport: httpx.MockTransport,
    base_url: str = "http://octobot.example",
    jobs: JobStore | None = None,
    progress_token: str | int | None = None,
    session: Any = None,
) -> Context:
    """Build a `Context` whose injected client is backed by `transport`.

    `jobs` defaults to a fresh, empty `JobStore` (via `AppContext`'s own
    default) when omitted; pass one explicitly to assert against the same
    store instance a job tool mutated. `progress_token`/`session` default to
    `None` (no token supplied, the common case); pass both together to
    exercise milestone 11's opportunistic-notification path the same way a
    real caller supplying `_meta.progressToken` would.
    """
    client = httpx.AsyncClient(base_url=base_url, transport=transport)
    app_context = AppContext(client=client) if jobs is None else AppContext(client=client, jobs=jobs)
    meta = {"progress_token": progress_token} if progress_token is not None else None
    request_context = SimpleNamespace(lifespan_context=app_context, meta=meta, session=session)
    return Context(request_context=request_context)
