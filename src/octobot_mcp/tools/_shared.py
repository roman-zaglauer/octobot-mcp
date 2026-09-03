"""Shared tool-implementation helpers: error surfacing (NFR-4).

`octobot_mcp.client.request_json` classifies every OctoBot connectivity
failure into an `OctoBotAPIError` subclass, but a plain `raise` of one of
those from inside a tool function is not enough on its own: the MCP SDK
(`mcp.server.mcpserver.tools.base.Tool.run`) treats any exception that
isn't its own `ToolError`/`ResourceError` as a *crash* -- it logs a full
traceback at ERROR and replaces the message the client sees with a generic
"Error executing tool <name>", discarding the very classification
`request_json` computed. Raising `ToolError` instead keeps the classified
message visible to the calling agent (as an `is_error=True` result) and
logs it at INFO without a traceback -- exactly NFR-4's "never a raw stack
trace" requirement.

`with_error_handling` is the one place that translation happens, so no
individual tool repeats the same try/except.
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from mcp.server.mcpserver.exceptions import ToolError

from octobot_mcp.errors import OctoBotAPIError

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def with_error_handling(func: F) -> F:
    """Wrap an async tool function so classified OctoBot failures reach the MCP client structured.

    Catches `OctoBotAPIError` (any of the five subclasses `request_json`
    raises) and re-raises as `mcp.server.mcpserver.exceptions.ToolError`
    with the same message, preserving `__cause__` for local debugging.
    Any other exception is intentionally left alone: an unanticipated bug
    should still surface to the server logs as a crash, not be silently
    reclassified as an "OctoBot failure."

    Uses `functools.wraps` so the wrapped function keeps the original's
    name, docstring, and (critically) `__annotations__` -- the MCP SDK
    inspects those to build the tool's input schema and to detect the
    `ctx: Context` parameter to inject (NFR-11). Tool modules using this
    decorator must therefore NOT use `from __future__ import annotations`
    (see their module docstrings): postponed evaluation would turn
    `__annotations__` into strings that the SDK re-evaluates against
    *this* module's globals (via `__wrapped__`/`typing.get_type_hints`),
    where `Context` isn't imported.
    """

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await func(*args, **kwargs)
        except OctoBotAPIError as exc:
            raise ToolError(f"{type(exc).__name__}: {exc}") from exc

    return wrapper  # type: ignore[return-value]
