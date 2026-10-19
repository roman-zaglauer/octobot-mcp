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

Milestone 12 (docs/tasklist.md item 12) adds `redact_secrets`, INV-4/NFR-6's
"single redaction helper" -- see its own docstring below. Its one consumer
is `octobot_mcp.tools.exchanges.update_exchange_credentials`.

Milestone 13 (docs/tasklist.md item 13) moves `_parse_content_disposition_filename`
here from `octobot_mcp.tools.profiles` -- it was originally written for
`export_profile` alone, but `octobot_mcp.tools.tentacles.export_logs` needs
the exact same binary-file-download Content-Disposition parsing (both
quoted and unquoted header forms), so this is now the one shared
implementation both consumers call rather than a second copy.

Milestone 15 (docs/tasklist.md item 15, ADR-0007) moves
`_extract_progress_token`/`_build_progress_notifier`/`_attach_progress_notifier`
here from `octobot_mcp.tools.backtesting` -- the same
`_parse_content_disposition_filename` precedent immediately above: they were
originally written for `start_backtest`/`compare_strategies` alone, but
`octobot_mcp.tools.data_collection.start_data_collection` (this milestone)
needs the exact same "capture `ctx.session` + a caller-supplied
`progressToken` into a callable a background watcher task can invoke long
after the original tool call has returned" mechanism, so this is now the
one shared implementation both consumers call. See
`octobot_mcp.tools.backtesting`'s own module-level "Milestone 11" comment
block for the full SDK-internals research this mechanism rests on (how
`progressToken` reaches `ctx.request_context.meta`, why `ctx.session`
specifically -- not `ctx` itself -- must be captured, and the real-vs-test-
harness `Connection`/`outbound` gotcha); that research is not repeated here
since it does not change by virtue of the move.

Milestone 14 (docs/tasklist.md item 14, the error handling / resilience
polish pass) extends `with_error_handling` itself with the spec's
Observability section's per-call structured log line: `{tool_name, args
(redacted per INV-4), duration_ms, outcome, error_class?}`, emitted exactly
once per tool invocation regardless of outcome (success, a classified
`OctoBotAPIError`, an unconfirmed gated-tool refusal, or an unanticipated
crash). This is the ONE place every registered tool's call already passes
through (this module's own established DRY precedent -- see
`require_confirmation`/`redact_secrets` above), so this is where the
logging lives rather than a second cross-cutting wrapper or scattered
per-tool `logger.info(...)` calls. `_redact_logged_args` is INV-4/NFR-6's
"redact by parameter name, generically" helper this milestone's own
instructions call for -- deliberately a *different* mechanism than
`redact_secrets` above (which scrubs literal string occurrences out of
unstructured text for `update_exchange_credentials`'s one error-message
call site): this one redacts a named parameter's entire value, before it is
ever formatted into a log line, for ANY tool whose signature happens to
carry a parameter named `api_key`/`api_secret`/`api_password` (case-
insensitive), not just that one tool. Logging is best-effort throughout
(mirrors this project's other "must never fail the actual operation"
patterns, e.g. `octobot_mcp.tools.backtesting._build_progress_notifier`'s
notification sending): any exception raised while building or emitting the
log line is caught and swallowed (at DEBUG) rather than ever propagating
out of a tool call.
"""

from __future__ import annotations

import functools
import inspect
import logging
import time
from collections.abc import Awaitable, Callable, Iterable
from email.message import Message
from typing import Any, TypeVar

from mcp.server.mcpserver.context import Context
from mcp.server.mcpserver.exceptions import ToolError

from octobot_mcp.errors import ExchangeCredentialsUpdateRejectedError, OctoBotAPIError
from octobot_mcp.jobs import JobStore

_logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])

#: INV-4/NFR-6's generic "redact by parameter name" denylist (milestone 14).
#: Deliberately a plain set of parameter NAMES, not tool names -- generic by
#: construction: any current or future tool whose signature carries a
#: parameter matching one of these (case-insensitively) has that argument's
#: value redacted from the structured per-call log line, regardless of
#: which tool it belongs to. `update_exchange_credentials` is this
#: denylist's only current match (its `api_key`/`api_secret`/`api_password`
#: parameters), but nothing here hardcodes that tool's name.
_SENSITIVE_PARAM_NAMES = frozenset({"api_key", "api_secret", "api_password"})

#: The single source of truth for "does this field NAME look like a secret",
#: case-insensitive SUFFIX match (confirmed defect fix: this list used to be
#: defined only in `octobot_mcp.tools.interfaces`, where it drove that
#: module's own read/write redaction and confirm-gating -- but
#: `_redact_logged_args` below needs the identical list to redact secret-
#: shaped KEYS found inside a dict-valued parameter (e.g.
#: `update_interface_config`'s own `patch={"token": "<real secret>"}`, which
#: is not itself a parameter named `api_key`/`api_secret`/`api_password` and
#: so previously passed through this module's by-parameter-name-only
#: redaction completely unredacted). Moved here as the one shared
#: implementation; `octobot_mcp.tools.interfaces` imports this constant
#: rather than keeping its own copy, so the two can never drift apart.
_SECRET_FIELD_SUFFIXES = (
    "token",
    "secret",
    "password",
    "api-key",
    "api-secret",
    "api-password",
    "api-hash",
    "client-secret",
)

#: Parameter names never worth including in the logged `args` payload at
#: all (as opposed to redacted-in-place): `ctx` is the injected MCP
#: `Context`/`AppContext` object (not something the calling agent passed,
#: and not JSON/repr-safe in general -- it holds a live `httpx2.AsyncClient`
#: and, for backtest jobs, an MCP `ServerSession`), so it is dropped
#: entirely rather than redacted.
_NON_LOGGED_PARAM_NAMES = frozenset({"ctx"})

#: The fixed marker `redact_secrets` substitutes for every scrubbed literal
#: value. Deliberately not e.g. the secret's own last-4-chars (that would be
#: a second, ad-hoc partial-redaction scheme independent of
#: `update_exchange_credentials`' own dedicated `api_key_last4` field) --
#: this marker means only "a secret value was here," nothing more.
REDACTION_MARKER = "[REDACTED]"


def _redact_logged_args(func: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    """Build the redacted `args` mapping for one tool call's log line (INV-4/NFR-6, milestone 14).

    Binds the actual call's `args`/`kwargs` against `func`'s own (pre-decoration)
    signature -- via `inspect.signature`, which follows `functools.wraps`'
    `__wrapped__` back to the original function regardless of how this
    wrapper itself was called -- so this works identically whether the MCP
    SDK invokes the tool the way it always does in production
    (`fn(**all_kwargs)`, confirmed against `FuncMetadata.call_fn`) or a unit
    test calls it positionally (`get_job_result(ctx, job_id)`, the
    convention throughout this project's own test suite). `ctx` is dropped
    entirely (`_NON_LOGGED_PARAM_NAMES`) rather than redacted -- it is
    server-injected, not caller-supplied, and not something a log line
    should ever try to stringify. Every remaining parameter whose NAME
    (case-insensitively) is in `_SENSITIVE_PARAM_NAMES` has its VALUE
    replaced with `REDACTION_MARKER`, generically, regardless of which tool
    owns that parameter -- the mechanism this milestone's own instructions
    call for, as opposed to `redact_secrets`'s literal-text scrubbing above.

    Never raises: an unresolvable bind (e.g. a call shape this function
    didn't anticipate) falls back to `{}` rather than ever surfacing a
    logging-internal error to the caller of `with_error_handling`'s wrapper.
    """
    try:
        bound = inspect.signature(func).bind_partial(*args, **kwargs)
    except TypeError:
        return {}
    return {
        name: (
            REDACTION_MARKER
            if name.lower() in _SENSITIVE_PARAM_NAMES
            else _redact_dict_param_value(value)
            if isinstance(value, dict)
            else value
        )
        for name, value in bound.arguments.items()
        if name not in _NON_LOGGED_PARAM_NAMES
    }


def _is_secret_shaped_field(key: Any, value: Any) -> bool:
    """Whether `key` (a field/parameter name) matches `_SECRET_FIELD_SUFFIXES`
    (case-insensitive suffix match) AND `value` is itself a string.

    The single shared predicate for "is this a secret field" (confirmed
    defect fix): both `_redact_dict_param_value` below and
    `octobot_mcp.tools.interfaces._is_secret_field` now call this, rather
    than keeping two separately-maintained implementations -- which is
    exactly how they drifted apart in the first place (`_redact_dict_param_value`
    used to redact ANY key matching a secret suffix regardless of the
    value's type, over-redacting e.g. a boolean `require-password` toggle to
    `REDACTION_MARKER` instead of leaving it `True`/`False`; see
    `octobot_mcp.tools.interfaces._is_secret_field`'s own docstring for the
    source-confirmed reason that `isinstance(value, str)` requirement
    exists). `key` is checked for `isinstance(..., str)` too, since a
    dict-valued parameter can in principle carry a non-string key.
    """
    return isinstance(key, str) and isinstance(value, str) and key.lower().endswith(_SECRET_FIELD_SUFFIXES)


def _redact_dict_param_value(value: dict[Any, Any]) -> dict[Any, Any]:
    """Redact a COPY of a dict-valued parameter's secret-suffix-matching keys (confirmed defect fix).

    `_redact_logged_args` only ever redacted a parameter whose own NAME
    matched `_SENSITIVE_PARAM_NAMES` -- a parameter that is itself a dict
    (e.g. `update_interface_config`'s `patch={"token": "<real secret>"}`)
    passed through completely unredacted, since `patch` isn't a name in that
    denylist. This closes that gap generically: any dict-valued parameter
    (for ANY tool, not special-cased) gets a redacted copy where every key
    whose value is a string AND matches `_is_secret_shaped_field` has that
    value replaced with `REDACTION_MARKER` -- every other key, including
    non-secret fields like `"port"` and secret-suffix-matching but non-string
    fields like a boolean `"require-password"`, is left exactly as-is, so
    logs stay useful for debugging.

    Recurses into nested `dict` values (e.g. `update_profile_currencies`'s
    `patch={"Bitcoin": {"enabled": True}}`) AND nested `list`/`tuple` values
    (e.g. a freeform `patch`'s plausible `"webhooks": [{"url": ...,
    "token": ...}]` shape -- confirmed defect fix: a list was previously
    left completely untouched, since only `dict` values were ever recursed
    into) via `_redact_sequence_param_value`, to arbitrary depth -- a
    list/tuple can contain dicts that contain lists that contain dicts, and
    so on. A sequence element that is itself neither a `dict` nor a
    `list`/`tuple` (a string, number, `None`, etc.) is left exactly as-is.

    **Third-round confirmed defect fix**: the original list-only version of
    this recursion (committed as the Gap-1 fix) still left a `tuple` value
    completely unredacted -- reproduced live: `_redact_dict_param_value(
    {"items": ({"token": "SECRET"},)})` returned the secret unredacted,
    since `isinstance(({"token": "SECRET"},), list)` is `False`. Not
    currently reachable through the MCP server's real external input path
    (JSON-RPC -> pydantic can only ever produce `dict`/`list`/`str`/`int`/
    `float`/`bool`/`None`, never `tuple`, for an `Any`-typed field) --
    but given this exact "not reachable today" reasoning already failed to
    hold twice before for this same mechanism, the check is widened to
    `(list, tuple)` now rather than left as a theoretical gap to rediscover
    a fourth time.

    Never mutates `value` itself -- always returns a new dict.
    """
    redacted: dict[Any, Any] = {}
    for key, inner_value in value.items():
        if isinstance(inner_value, dict):
            redacted[key] = _redact_dict_param_value(inner_value)
        elif isinstance(inner_value, (list, tuple)):
            redacted[key] = _redact_sequence_param_value(inner_value)
        elif _is_secret_shaped_field(key, inner_value):
            redacted[key] = REDACTION_MARKER
        else:
            redacted[key] = inner_value
    return redacted


def _redact_sequence_param_value(value: list[Any] | tuple[Any, ...]) -> list[Any] | tuple[Any, ...]:
    """Redact a COPY of a list/tuple-valued field, walking into `dict`/sequence elements (confirmed defect fix, Gap 1 + its own round-3 tuple follow-up).

    A sequence cannot itself carry a "key," so there is nothing here to
    match against `_SECRET_FIELD_SUFFIXES` directly -- this exists purely so
    a `dict` element anywhere inside the sequence (at any nesting depth, via
    mutual recursion with `_redact_dict_param_value`) gets its own
    secret-suffix-matching keys redacted. Every other element (a string,
    number, `None`, bool, etc.) is left exactly as-is. The redacted copy
    preserves the original container TYPE (`list` stays a `list`, `tuple`
    stays a `tuple`) rather than normalizing both to one type, so a logged
    `tuple`-valued field still reads as a tuple.

    Never mutates `value` itself -- always returns a new list/tuple.
    """
    redacted: list[Any] = []
    for item in value:
        if isinstance(item, dict):
            redacted.append(_redact_dict_param_value(item))
        elif isinstance(item, (list, tuple)):
            redacted.append(_redact_sequence_param_value(item))
        else:
            redacted.append(item)
    return type(value)(redacted)


def _log_tool_call(
    func: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    *,
    duration_ms: float,
    outcome: str,
    error_class: str | None,
) -> None:
    """Emit the spec's Observability section per-call structured log line (milestone 14).

    `{tool_name, args (redacted per INV-4), duration_ms, outcome:
    "ok"|"error", error_class?}` -- `error_class` is only included at all
    when `outcome == "error"` (the spec's own `?` on that field), never a
    literal `None` on a successful call. This single log line is also
    exactly what the spec's Security & privacy section calls "auditable
    action" logging for gated (confirm-required) tools and for
    `update_exchange_credentials` specifically: `tool_name` identifies which
    action ran, and `args` (redacted) carries e.g. `confirm`/the target
    resource, on every outcome (refusal, success, or failure) -- see this
    module's own docstring for why no second, separate logging path exists.

    Best-effort per this milestone's own explicit instruction (mirrors
    `octobot_mcp.tools.backtesting._build_progress_notifier`'s identical
    "never let sending/logging break the real operation" pattern): any
    exception raised while redacting/formatting this line is caught and
    logged at DEBUG instead, never propagated into the calling tool's
    result.
    """
    try:
        payload: dict[str, Any] = {
            "tool_name": func.__name__,
            "args": _redact_logged_args(func, args, kwargs),
            "duration_ms": round(duration_ms, 1),
            "outcome": outcome,
        }
        if error_class is not None:
            payload["error_class"] = error_class
        _logger.info("tool_call %r", payload)
    except Exception as exc:  # noqa: BLE001 -- best-effort: logging must never fail the tool call.
        _logger.debug("Failed to log tool call for %r (best-effort, ignored): %r", func, exc)


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

    Milestone 14 (docs/tasklist.md item 14) additionally times every call
    and emits the spec's Observability section per-call structured log line
    (`_log_tool_call`) exactly once, on every exit path -- success, a
    classified `OctoBotAPIError`, or any other exception -- via a `finally`
    block, so the log line and the original control flow (which exception,
    if any, propagates to the caller) are entirely independent: logging can
    never change what the calling agent sees, and a logging failure can
    never suppress or replace it either (`_log_tool_call` is itself
    best-effort, see its own docstring).
    """

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        start = time.monotonic()
        outcome = "ok"
        error_class: str | None = None
        try:
            return await func(*args, **kwargs)
        except OctoBotAPIError as exc:
            outcome = "error"
            error_class = type(exc).__name__
            raise ToolError(f"{type(exc).__name__}: {exc}") from exc
        except Exception as exc:
            outcome = "error"
            error_class = type(exc).__name__
            raise
        finally:
            _log_tool_call(
                func,
                args,
                kwargs,
                duration_ms=(time.monotonic() - start) * 1000,
                outcome=outcome,
                error_class=error_class,
            )

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


def redact_secrets(text: str, secrets: Iterable[str | None]) -> str:
    """Scrub every literal occurrence of `secrets` out of `text` (INV-4/NFR-6).

    The ONE shared implementation of INV-4's "single redaction helper used
    at the boundary where OctoBot's response is read, not left to each call
    site to remember." `update_exchange_credentials` (milestone 12,
    `octobot_mcp.tools.exchanges`) is this helper's one consumer: it calls
    this at the single point it reads OctoBot's own response text to build
    an error message, passing the literal `api_key`/`api_secret`/
    `api_password` values submitted in that same call.

    This is deliberately active scrubbing of `text`, not a passive "assume
    OctoBot never echoes a submitted credential back" assumption --
    defense in depth is the whole point of NFR-6 (and, concretely,
    `update_exchange_credentials` was confirmed against OctoBot source to
    do exactly this on ITS success path: `controllers/configuration.py`'s
    `/config` route echoes the caller's entire submitted `global_config`
    dict back verbatim as `global_updated_config` -- which is precisely why
    that tool never reads or forwards OctoBot's response body content at
    all on success, only on the error path this helper guards).

    Every occurrence of every non-empty value in `secrets` is replaced with
    `REDACTION_MARKER`; falsy entries (`None` or `""`, e.g. an omitted
    optional `api_password`) are skipped -- an empty-string `.replace` would
    otherwise insert the marker between every character of `text`.

    Caveat: this is exact-literal-match only (case-sensitive, no
    truncation/partial-match or encoding awareness -- a URL-encoded,
    re-cased, or partially-quoted rendering of a secret would not be
    scrubbed). Judged acceptable for its one current consumer
    (`update_exchange_credentials`), whose only real error text source
    (`str(exception)` from `update_global_config`'s own `except Exception`
    branch, confirmed against source) is not known to transform submitted
    values that way -- but a future consumer with a different error-text
    source should re-check this assumption before relying on it.

    Args:
        text: the raw string to scrub (e.g. OctoBot's own error response
            text for a failed request).
        secrets: the literal secret values submitted in this call (e.g.
            `[api_key, api_secret, api_password]`); `None`/empty entries are
            ignored.

    Returns:
        `text` with every occurrence of every given secret replaced by
        `REDACTION_MARKER`.
    """
    redacted = text
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, REDACTION_MARKER)
    return redacted


def reject_underscore_in_config_path_segment(tool_name: str, label: str, value: str) -> None:
    """Raise `ExchangeCredentialsUpdateRejectedError` if `value` contains an underscore.

    The ONE shared implementation of the config-path-corruption guard
    `octobot_mcp.tools.exchanges._reject_exchange_name_with_underscore`
    originally introduced (milestone 12, for `exchange` names) and now
    generalized here (confirmed defect fix) so every write path that
    interpolates a caller-supplied value into a flat
    `"<prefix>_<value>_<field>"`-shaped config-path segment gets the same
    guard, without duplicating the check's logic at each call site.
    `exchanges.py`'s own `_reject_exchange_name_with_underscore` now
    delegates here; `octobot_mcp.tools.interfaces.update_interface_config`
    (for `service`, and each field name in `patch`) and
    `octobot_mcp.tools.profiles.update_profile_currencies` (for `currency`)
    call this directly.

    Root cause this guards against (confirmed against source,
    `octobot_commons.configuration.config_operations.parse_and_update`):
    OctoBot's own config-path decoder splits ANY flat `global_config` key
    on every literal `"_"`, positionally, to rebuild the nested config path
    it writes to. A caller-supplied value that itself contains `"_"` (e.g.
    an exchange name, a service name, a patch field name, a currency name)
    is therefore split apart incorrectly, silently writing to -- or
    deleting -- the wrong nested config location instead of the intended
    one. Refusing locally, before any OctoBot call is made, is strictly
    better than letting that silent misrouting happen.

    A no-op (returns `None`) when `value` contains no underscore.

    Args:
        tool_name: the tool refusing the call (e.g. `"update_interface_config"`),
            echoed in the raised message so the calling agent knows exactly
            which call was refused.
        label: a short description of what `value` is (e.g. `"exchange"`,
            `"service"`, `"patch field"`, `"currency"`), echoed in the
            message.
        value: the caller-supplied value to check.
    """
    if "_" in value:
        raise ExchangeCredentialsUpdateRejectedError(
            f"{tool_name}: {label}={value!r} contains an underscore, which this "
            "tool refuses to send. OctoBot's own config-path decoder (confirmed "
            "against source, octobot_commons.configuration.config_operations."
            "parse_and_update) splits a flat config key on every literal '_' "
            "positionally, so a value containing '_' here would be silently "
            "written to (or deleted from) the wrong nested config location "
            "instead of the intended one."
        )


def _parse_content_disposition_filename(content_disposition: str) -> str | None:
    """Extract `filename` from a `Content-Disposition` header value, or `None` if absent.

    RFC 6266/2183: a header parameter is only quoted when its value isn't a
    bare HTTP token (e.g. it contains a space) -- `attachment;
    filename="some name.zip"` vs. `attachment; filename=some_name.zip` are
    both valid and both real (confirmed live: OctoBot's own `flask.send_file`
    produces the unquoted form for any download name that happens to already
    be a single token, e.g. `export_profile`'s stock `daily_trading` profile
    and `export_logs`'s hardcoded `logs_export.zip`). A regex matching only
    the quoted form silently mishandles the unquoted one, so this uses the
    stdlib's own header-parameter parser (`email.message.Message.get_filename`,
    the same RFC 2183 parameter grammar `Content-Disposition` uses) instead
    of hand-rolling either form.

    Milestone 5's one original consumer was `octobot_mcp.tools.profiles.
    export_profile`; milestone 13 (docs/tasklist.md item 13) moved this here
    so `octobot_mcp.tools.tentacles.export_logs` shares the same
    implementation rather than duplicating it.
    """
    message = Message()
    message["content-disposition"] = content_disposition
    return message.get_filename()


# --- Milestone 15 (moved from octobot_mcp.tools.backtesting, ADR-0007): -----
# opportunistic MCP progress notifications, shared by any job-submitting
# tool (`start_backtest`/`compare_strategies`, and now
# `octobot_mcp.tools.data_collection.start_data_collection`). See this
# module's own docstring above, and `octobot_mcp.tools.backtesting`'s
# module-level "Milestone 11" comment block, for the full research this
# mechanism rests on -- not repeated here.


def _extract_progress_token(ctx: Context) -> str | int | None:
    """Read the caller-supplied `progressToken` off `ctx`, or `None` if absent.

    See `octobot_mcp.tools.backtesting`'s "Milestone 11" comment block for
    exactly how this value reaches `ctx.request_context.meta` and why it is
    read here rather than via `ctx.report_progress` (which cannot be used
    later, from a background watcher task, at all). A malformed `bool` value
    can never reach here in practice (the SDK's own wire-level parsing
    already excludes it -- `bool` is deliberately not a valid progress token
    in the protocol, `progressToken: str | int`), but the `isinstance(...,
    bool)` guard costs nothing and matches the same defensiveness the SDK's
    own internals apply at the same check.
    """
    meta = ctx.request_context.meta
    if not meta:
        return None
    token = meta.get("progress_token")
    if isinstance(token, bool) or not isinstance(token, (str, int)):
        return None
    return token


def _build_progress_notifier(
    ctx: Context, progress_token: str | int
) -> Callable[[float, float | None, str | None], Awaitable[None]]:
    """Capture `ctx.session` + `progress_token` into a `(progress, total,
    message) -> None` async callable a background watcher task can call
    later, long after `ctx` itself (and the tool call it belongs to) has
    gone out of scope.

    See `octobot_mcp.tools.backtesting`'s "Milestone 11" comment block for
    why `ctx.session` specifically (not `ctx` itself) is what must be
    captured, and why this can only use `send_progress_notification` (never
    `ctx.report_progress`). Sending is best-effort: any exception -- a
    disconnected client, a closed connection, anything else -- is caught and
    logged at DEBUG, never propagated, so a notification-delivery failure
    can NEVER fail the job or the watcher loop that called this.
    """
    session = ctx.session

    async def _notify(progress: float, total: float | None, message: str | None) -> None:
        try:
            await session.send_progress_notification(progress_token, progress, total, message)
        except Exception as exc:  # noqa: BLE001 -- best-effort: never fail the watcher over this.
            _logger.debug(
                "Failed to send MCP progress notification for token %r (best-effort, ignored): %r",
                progress_token,
                exc,
            )

    return _notify


def _attach_progress_notifier(store: JobStore, ctx: Context, job_id: str) -> None:
    """Extract `ctx`'s `progressToken` (if any) and attach a notifier for `job_id`.

    A no-op (never calls `JobStore.set_progress_notifier` at all) when the
    caller supplied no token -- `JobRecord.progress_token`/`progress_notifier`
    simply stay at their `None` defaults, and every watcher-tick hook already
    treats a `None` `progress_notifier` as "nothing to do here." Shared by
    `start_backtest`/`compare_strategies` (`octobot_mcp.tools.backtesting`)
    and `start_data_collection` (`octobot_mcp.tools.data_collection`,
    milestone 15) -- for `compare_strategies` specifically, only the PARENT
    job gets a notifier, never its children (see
    `octobot_mcp.tools.backtesting._run_compare_strategies`'s own docstring
    for why).
    """
    progress_token = _extract_progress_token(ctx)
    if progress_token is None:
        return
    store.set_progress_notifier(
        job_id,
        progress_token=progress_token,
        notifier=_build_progress_notifier(ctx, progress_token),
    )
