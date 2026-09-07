"""Unit tests for milestone 14's per-call structured logging
(docs/tasklist.md item 14, spec's Observability section) --
`octobot_mcp.tools._shared.with_error_handling`'s own extension: a
`{tool_name, args (redacted per INV-4), duration_ms, outcome, error_class?}`
log line emitted exactly once per tool call, on every outcome.

Uses small standalone fake "tool" functions decorated with the real
`with_error_handling` (never a reimplementation) -- the same convention
`tests/test_confirm_gating.py` uses for `require_confirmation` in isolation.
"""

import logging

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from octobot_mcp.errors import OctoBotClientError
from octobot_mcp.tools._shared import REDACTION_MARKER, with_error_handling

_LOGGER_NAME = "octobot_mcp.tools._shared"


@with_error_handling
async def _fake_ok_tool(ctx, profile_id: str) -> dict:
    return {"profile_id": profile_id, "ok": True}


@with_error_handling
async def _fake_failing_tool(ctx, profile_id: str) -> dict:
    raise OctoBotClientError(f"OctoBot rejected {profile_id!r}", status_code=404)


@with_error_handling
async def _fake_crashing_tool(ctx) -> dict:
    raise ValueError("an unanticipated bug, not an OctoBot failure")


@with_error_handling
async def update_exchange_credentials(
    ctx, exchange: str, api_key: str, api_secret: str, api_password: str | None = None, confirm: bool = False
) -> dict:
    return {"exchange": exchange, "updated": True}


@with_error_handling
async def _fake_tool_with_a_differently_named_secret_param(ctx, api_key: str) -> dict:
    """Proves the redaction denylist is keyed on parameter NAME, not tied to
    `update_exchange_credentials` specifically -- any tool with a same-named
    parameter gets the same treatment."""
    return {"ok": True}


# --- successful call: tool_name/args/duration_ms/outcome, no error_class ---------


@pytest.mark.anyio
async def test_logs_ok_outcome_with_tool_name_duration_and_no_error_class(caplog):
    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        result = await _fake_ok_tool(ctx=object(), profile_id="profile-123")

    assert result == {"profile_id": "profile-123", "ok": True}
    [record] = [r for r in caplog.records if r.name == _LOGGER_NAME]
    assert "'tool_name': '_fake_ok_tool'" in record.getMessage()
    assert "'profile_id': 'profile-123'" in record.getMessage()
    assert "'outcome': 'ok'" in record.getMessage()
    assert "'duration_ms':" in record.getMessage()
    assert "error_class" not in record.getMessage()


@pytest.mark.anyio
async def test_logged_args_never_include_ctx(caplog):
    sentinel_ctx = object()
    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        await _fake_ok_tool(ctx=sentinel_ctx, profile_id="profile-123")

    [record] = [r for r in caplog.records if r.name == _LOGGER_NAME]
    assert "ctx" not in record.getMessage()


# --- classified OctoBotAPIError: outcome=error, error_class set, still raises ToolError -----


@pytest.mark.anyio
async def test_logs_error_outcome_and_error_class_for_a_classified_octobot_failure(caplog):
    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        with pytest.raises(ToolError):
            await _fake_failing_tool(ctx=object(), profile_id="profile-999")

    [record] = [r for r in caplog.records if r.name == _LOGGER_NAME]
    assert "'tool_name': '_fake_failing_tool'" in record.getMessage()
    assert "'outcome': 'error'" in record.getMessage()
    assert "'error_class': 'OctoBotClientError'" in record.getMessage()


# --- an unanticipated crash is also logged (still propagates unchanged) ---------------


@pytest.mark.anyio
async def test_logs_error_outcome_for_an_unanticipated_exception_which_still_propagates(caplog):
    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        with pytest.raises(ValueError, match="an unanticipated bug"):
            await _fake_crashing_tool(ctx=object())

    [record] = [r for r in caplog.records if r.name == _LOGGER_NAME]
    assert "'outcome': 'error'" in record.getMessage()
    assert "'error_class': 'ValueError'" in record.getMessage()


# --- INV-4/NFR-6: generic by-parameter-name redaction, never a raw secret in the log ---


@pytest.mark.anyio
async def test_secret_shaped_params_are_redacted_by_name_in_the_log_line(caplog):
    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        await update_exchange_credentials(
            ctx=object(),
            exchange="binance",
            api_key="THE_REAL_API_KEY_VALUE",
            api_secret="THE_REAL_API_SECRET_VALUE",
            api_password="THE_REAL_API_PASSWORD_VALUE",
            confirm=True,
        )

    full_text = caplog.text
    assert "THE_REAL_API_KEY_VALUE" not in full_text
    assert "THE_REAL_API_SECRET_VALUE" not in full_text
    assert "THE_REAL_API_PASSWORD_VALUE" not in full_text
    assert full_text.count(REDACTION_MARKER) == 3
    # Non-secret args (and the gated-tool `confirm` flag -- see the
    # auditable-action assertion below) still appear in the clear.
    assert "'exchange': 'binance'" in full_text
    assert "'confirm': True" in full_text


@pytest.mark.anyio
async def test_secret_redaction_is_generic_by_parameter_name_not_hardcoded_to_one_tool(caplog):
    """A DIFFERENT tool (not `update_exchange_credentials`) with a
    same-named `api_key` parameter is redacted the same way -- proving the
    mechanism is keyed on the parameter name, generically, not special-cased
    to one tool."""
    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        await _fake_tool_with_a_differently_named_secret_param(ctx=object(), api_key="ANOTHER_REAL_SECRET")

    assert "ANOTHER_REAL_SECRET" not in caplog.text
    assert REDACTION_MARKER in caplog.text


@pytest.mark.anyio
async def test_secret_is_redacted_even_on_the_error_path(caplog):
    @with_error_handling
    async def _failing_credentials_tool(ctx, api_key: str) -> dict:
        raise OctoBotClientError(f"rejected {api_key}", status_code=500)

    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        with pytest.raises(ToolError):
            await _failing_credentials_tool(ctx=object(), api_key="SECRET_ON_ERROR_PATH")

    assert "SECRET_ON_ERROR_PATH" not in caplog.text


# --- auditable-action logging (spec's Security & privacy section) --------------------


@pytest.mark.anyio
async def test_a_gated_tools_confirm_flag_is_identifiable_in_the_log_regardless_of_value(caplog):
    @with_error_handling
    async def delete_profile(ctx, profile_id: str, confirm: bool = False) -> dict:
        return {"confirmed": False} if not confirm else {"deleted": True}

    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        await delete_profile(ctx=object(), profile_id="profile-1", confirm=False)
        await delete_profile(ctx=object(), profile_id="profile-1", confirm=True)

    records = [r.getMessage() for r in caplog.records if r.name == _LOGGER_NAME]
    assert len(records) == 2
    assert "'tool_name': 'delete_profile'" in records[0]
    assert "'confirm': False" in records[0]
    assert "'tool_name': 'delete_profile'" in records[1]
    assert "'confirm': True" in records[1]


# --- logging is best-effort: a logging-internal failure never breaks the tool call ----


@pytest.mark.anyio
async def test_a_logging_failure_never_breaks_the_actual_tool_call(monkeypatch, caplog):
    import octobot_mcp.tools._shared as shared

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated logging-internal failure")

    monkeypatch.setattr(shared._logger, "info", _boom)

    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        result = await _fake_ok_tool(ctx=object(), profile_id="profile-123")

    # The real tool result is returned unchanged despite the logging crash.
    assert result == {"profile_id": "profile-123", "ok": True}
    # The failure itself is swallowed, not propagated -- best-effort, at DEBUG.
    assert any("Failed to log tool call" in r.getMessage() for r in caplog.records)


@pytest.fixture
def anyio_backend():
    return "asyncio"
