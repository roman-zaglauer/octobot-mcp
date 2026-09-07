"""Unit tests for the milestone-13 tentacle config + logs tools (docs/tasklist.md item 13).

`get_tentacle_config`/`update_tentacle_config`/`export_logs` -- see
`src/octobot_mcp/tools/tentacles.py`'s per-tool docstrings for the exact
request/response shapes these tests assert, verified against OctoBot source
and the live test instance as part of this milestone's implementation.

`list_tentacles`/`get_logs` are Tier C (ADR-0006): their tests assert the
structured "not available" shape AND that no HTTP call is ever attempted,
via the same assertion-raising mock-handler technique used throughout this
project's other test modules.
"""

import base64
import json

import httpx2 as httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from octobot_mcp.tools.tentacles import (
    export_logs,
    get_logs,
    get_tentacle_config,
    list_tentacles,
    update_tentacle_config,
)
from tests.test_tools_shared import make_ctx


@pytest.fixture
def anyio_backend():
    return "asyncio"


# --- get_tentacle_config ------------------------------------------------------


@pytest.mark.anyio
async def test_get_tentacle_config_returns_octobot_payload_unchanged():
    payload = {
        "name": "DailyTradingMode",
        "config": {"disable_buy_orders": False, "use_stop_orders": True},
        "displayed_elements": {"data": {"elements": []}},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/config_tentacle_edit_details/DailyTradingMode"
        return httpx.Response(200, json=payload)

    result = await get_tentacle_config(make_ctx(httpx.MockTransport(handler)), tentacle="DailyTradingMode")
    assert result == payload


@pytest.mark.anyio
async def test_get_tentacle_config_classifies_5xx_with_octobot_error_text():
    # Reproduces the live-confirmed shape: a plain-text body despite a
    # Content-Type: application/json header that lies about the encoding.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            content=b"Can't find tentacle: NotARealTentacle",
            headers={"Content-Type": "application/json"},
        )

    with pytest.raises(ToolError) as exc_info:
        await get_tentacle_config(make_ctx(httpx.MockTransport(handler)), tentacle="NotARealTentacle")
    assert "OctoBotServerError" in str(exc_info.value)
    assert "Can't find tentacle: NotARealTentacle" in str(exc_info.value)


@pytest.mark.anyio
async def test_get_tentacle_config_classifies_4xx():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    with pytest.raises(ToolError) as exc_info:
        await get_tentacle_config(make_ctx(httpx.MockTransport(handler)), tentacle="Whatever")
    assert "OctoBotClientError" in str(exc_info.value)


@pytest.mark.anyio
async def test_get_tentacle_config_classifies_malformed_2xx_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json</html>")

    with pytest.raises(ToolError) as exc_info:
        await get_tentacle_config(make_ctx(httpx.MockTransport(handler)), tentacle="Whatever")
    assert "OctoBotMalformedResponseError" in str(exc_info.value)


# --- update_tentacle_config ----------------------------------------------------


@pytest.mark.anyio
async def test_update_tentacle_config_sends_name_as_query_param_and_patch_as_raw_body():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/config_tentacle"
        assert dict(request.url.params) == {"name": "DailyTradingMode", "action": "update"}
        assert json.loads(request.content) == {"disable_buy_orders": True}
        return httpx.Response(200, json="DailyTradingMode updated")

    result = await update_tentacle_config(
        make_ctx(httpx.MockTransport(handler)),
        tentacle="DailyTradingMode",
        patch={"disable_buy_orders": True},
    )
    assert result == {
        "tentacle": "DailyTradingMode",
        "updated_fields": {"disable_buy_orders": True},
        "message": "DailyTradingMode updated",
    }


@pytest.mark.anyio
async def test_update_tentacle_config_classifies_failure_with_octobot_error_text():
    # Reproduces the live-confirmed shape: HTTP 500, plain-text body,
    # Content-Type: application/json header that lies about the encoding.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            content=b"Can't find NotARealTentacle class",
            headers={"Content-Type": "application/json"},
        )

    with pytest.raises(ToolError) as exc_info:
        await update_tentacle_config(
            make_ctx(httpx.MockTransport(handler)),
            tentacle="NotARealTentacle",
            patch={"foo": "bar"},
        )
    assert "TentacleConfigUpdateRejectedError" in str(exc_info.value)
    assert "Can't find NotARealTentacle class" in str(exc_info.value)


@pytest.mark.anyio
async def test_update_tentacle_config_classifies_malformed_2xx_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json</html>")

    with pytest.raises(ToolError) as exc_info:
        await update_tentacle_config(
            make_ctx(httpx.MockTransport(handler)), tentacle="DailyTradingMode", patch={}
        )
    assert "OctoBotMalformedResponseError" in str(exc_info.value)


# --- export_logs ----------------------------------------------------------------


@pytest.mark.anyio
async def test_export_logs_returns_base64_content_and_the_real_filename():
    zip_bytes = b"PK\x03\x04fake logs zip content"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/export_logs"
        return httpx.Response(
            200,
            content=zip_bytes,
            headers={
                "Content-Type": "application/zip",
                "Content-Disposition": "attachment; filename=logs_export.zip",
            },
        )

    result = await export_logs(make_ctx(httpx.MockTransport(handler)))
    assert result["filename"] == "logs_export.zip"
    assert base64.b64decode(result["content_base64"]) == zip_bytes


@pytest.mark.anyio
async def test_export_logs_parses_a_quoted_content_disposition_filename():
    # Regression test proving the shared helper (moved from profiles.py this
    # milestone) still handles BOTH Content-Disposition forms after the move.
    zip_bytes = b"PK\x03\x04another fake logs zip"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=zip_bytes,
            headers={
                "Content-Type": "application/zip",
                "Content-Disposition": 'attachment; filename="logs export with spaces.zip"',
            },
        )

    result = await export_logs(make_ctx(httpx.MockTransport(handler)))
    assert result["filename"] == "logs export with spaces.zip"
    assert base64.b64decode(result["content_base64"]) == zip_bytes


@pytest.mark.anyio
async def test_export_logs_fails_loud_when_content_disposition_is_missing():
    # Reproduces OctoBot's own documented failure mode: an internal export
    # failure silently 302-redirects to /logs with no useful body/header.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "/logs"})

    with pytest.raises(ToolError) as exc_info:
        await export_logs(make_ctx(httpx.MockTransport(handler)))
    assert "OctoBotMalformedResponseError" in str(exc_info.value)


@pytest.mark.anyio
async def test_export_logs_classifies_5xx():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(ToolError) as exc_info:
        await export_logs(make_ctx(httpx.MockTransport(handler)))
    assert "OctoBotServerError" in str(exc_info.value)


# --- list_tentacles / get_logs: Tier C stubs (ADR-0006) ------------------------


def _no_call_allowed_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(
            f"Tier C stub tools must never make an HTTP call, but {request.method} "
            f"{request.url.path} was attempted"
        )

    return httpx.MockTransport(handler)


@pytest.mark.anyio
async def test_list_tentacles_returns_structured_unavailable_without_any_http_call():
    result = await list_tentacles(make_ctx(_no_call_allowed_transport()))
    assert result == {
        "available": False,
        "reason": result["reason"],
        "alternative": "get_tentacle_config",
    }
    assert result["available"] is False
    assert isinstance(result["reason"], str) and result["reason"]


@pytest.mark.anyio
async def test_get_logs_returns_structured_unavailable_without_any_http_call():
    result = await get_logs(make_ctx(_no_call_allowed_transport()))
    assert result == {
        "available": False,
        "reason": result["reason"],
        "alternative": "export_logs",
    }
    assert result["available"] is False
    assert isinstance(result["reason"], str) and result["reason"]


@pytest.mark.anyio
async def test_list_tentacles_never_raises():
    # Tier C tools must never surface as a tool-call failure (ADR-0006).
    result = await list_tentacles(make_ctx(_no_call_allowed_transport()))
    assert isinstance(result, dict)


@pytest.mark.anyio
async def test_get_logs_never_raises():
    result = await get_logs(make_ctx(_no_call_allowed_transport()))
    assert isinstance(result, dict)
