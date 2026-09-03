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

Milestone 7 (docs/tasklist.md item 7) adds `AppContext.jobs`, an
`octobot_mcp.jobs.JobStore` instance built the same way and for the same
reason as `client`: so the process-wide `backtest_execution_lock` (ADR-0002)
and the in-memory job records are reachable via `get_job_store(ctx)`, never
a module-level global reached by name.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import httpx2 as httpx

from octobot_mcp.client import create_http_client
from octobot_mcp.config import load_config
from octobot_mcp.jobs import JobStore

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.context import Context


@dataclass(frozen=True)
class AppContext:
    """The single object injected into every tool call for this server (NFR-11).

    Holds the one `httpx2.AsyncClient` built at startup from
    `load_config()` + `create_http_client()` and kept for the process
    lifetime (ADR-0004: exactly one OctoBot instance per process), plus the
    one `JobStore` (milestone 7) holding every backtest/compare_strategies
    job record and the shared `backtest_execution_lock` for that same
    process lifetime.
    """

    client: httpx.AsyncClient
    jobs: JobStore = field(default_factory=JobStore)


@asynccontextmanager
async def app_lifespan(server: "MCPServer") -> AsyncIterator[AppContext]:
    """Build the one OctoBot HTTP client and job store for the process lifetime.

    Runs once, when the MCP server starts (before it accepts any request)
    and tears the client down once, when the server shuts down -- the SDK
    keeps this context manager open for the whole session and yields its
    result to every request handler. The job store is purely in-memory (no
    teardown needed): it is simply discarded when the process exits, which
    is why an unknown `job_id` after a restart is expected (ADR-0002 risk
    table), not a bug.
    """
    config = load_config()
    client = create_http_client(config)
    try:
        yield AppContext(client=client, jobs=JobStore())
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


def get_job_store(ctx: "Context") -> JobStore:
    """The single accessor tool functions use to reach the injected job store.

    Every tool takes a `ctx: Context` parameter (the MCP SDK injects it by
    type annotation) and calls `get_job_store(ctx)` instead of importing a
    module-level store -- this is the one place that knows the lifespan
    context's shape. A future milestone 8 tool reaches the shared lock via
    `get_job_store(ctx).backtest_execution_lock`.
    """
    return ctx.request_context.lifespan_context.jobs
