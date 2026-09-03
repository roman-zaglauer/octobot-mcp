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

Milestone 6 (docs/tasklist.md item 6) adds `require_confirmation`, the
shared confirm-gating helper for ADR-0003's destructive/high-risk tools
(`delete_profile`/`convert_profile_to_live` are its first two consumers;
milestone 12's `update_exchange_credentials`/`clear_*_history` tools reuse
it unchanged). It is a deliberately different kind of "failure surfacing"
than `with_error_handling` above: per ADR-0003, an unconfirmed call is not
an error at all -- it is the tool doing exactly what was asked (checking)
and reporting back a normal, structured refusal a calling agent can read
and act on. A gated tool therefore calls `require_confirmation` and
`return`s its result directly (never `raise`s it, and it is never passed
through `with_error_handling`/`ToolError`).
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


def require_confirmation(action: str, target: str, reason: str) -> dict[str, Any]:
    """Build the structured refusal result for an unconfirmed gated tool call (ADR-0003).

    The ONE shared implementation of ADR-0003's confirm-gating check, used
    by every tool classified as destructive/high-risk (`delete_profile`/
    `convert_profile_to_live` first, milestone 12's
    `update_exchange_credentials`/`clear_*_history` tools later) so there is
    no per-tool bespoke confirm-checking logic to get wrong.

    Callers are responsible for the actual gate (`if confirm is not True:
    return require_confirmation(...)`) -- this function only builds the
    result, since whether `confirm` was satisfied is specific to each call
    site's parameter.

    Per ADR-0003, this is a NORMAL tool return value, not an error: a
    calling tool function must `return` this dict directly rather than
    `raise` anything, and it must never be routed through
    `with_error_handling`/`ToolError` -- an unconfirmed call is the tool
    correctly doing what was asked (checking), not a failure.

    Args:
        action: the tool/action name (e.g. `"delete_profile"`), echoed back
            so the calling agent knows exactly what was refused.
        target: the specific resource the action would apply to (e.g. a
            `profile_id`), echoed back for the same reason.
        reason: a human-readable explanation of why confirmation is
            required for this specific action (e.g. "This permanently
            deletes the profile and cannot be undone.").

    Returns:
        `{"confirmed": False, "action": action, "target": target, "reason":
        reason, "message": <a human-readable summary combining all three
        and telling the caller to re-issue the call with `confirm=true`>}`.
    """
    return {
        "confirmed": False,
        "action": action,
        "target": target,
        "reason": reason,
        "message": (
            f"{action} on {target!r} was NOT performed: confirmation is "
            f"required. {reason} Re-issue this call with confirm=true to "
            "proceed."
        ),
    }
