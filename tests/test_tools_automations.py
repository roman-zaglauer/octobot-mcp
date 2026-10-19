"""Unit tests for milestone 22's `get_automations`/`update_automations`/
`start_automations`/`reset_automations_to_default` (docs/tasklist.md item
22, ADR-0013).

Per this project's established safety discipline (explicitly restated for
this milestone), EVERY test here mocks the HTTP transport
(`httpx2.MockTransport`) -- none exercises a live OctoBot instance, and no
test ever issues a real confirmed `POST /automations?action=start` or
`action=factory_reset` against a live instance. `start_automations`'s and
`reset_automations_to_default`'s confirmed paths are only ever driven
against a mocked transport in this suite.
"""

import json

import httpx2 as httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from octobot_mcp.jobs import JobStore
from octobot_mcp.tools.automations import (
    _RESET_REFUSAL_REASON,
    _START_REFUSAL_REASON,
    get_automations,
    reset_automations_to_default,
    start_automations,
    update_automations,
)
from tests.test_tools_shared import make_ctx

#: The real zero-automations capture this ADR's own live investigation
#: recorded verbatim (ADR-0013 Context) -- the only shape confirmed live
#: this session.
_ZERO_AUTOMATIONS_PAYLOAD = {
    "config": {"automations_count": 0},
    "displayed_elements": {
        "data": {
            "elements": [
                {
                    "schema": {
                        "properties": {
                            "automations_count": {
                                "title": "Number of automations.",
                                "type": "number",
                                "default": 0,
                                "minimum": 0,
                            }
                        },
                        "title": "Automation configuration",
                        "type": "object",
                    },
                    "tentacle": "Automation",
                    "tentacle_type": "automation",
                    "type": "input",
                }
            ],
            "sub_elements": [],
        },
        "name": "root",
        "type": "chart",
    },
    "name": "Automation",
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


# --- get_automations: passthrough, NFR-24 -----------------------------------


@pytest.mark.anyio
async def test_get_automations_returns_zero_automations_payload_unchanged():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/automations_edit_details"
        return httpx.Response(200, json=_ZERO_AUTOMATIONS_PAYLOAD)

    result = await get_automations(make_ctx(httpx.MockTransport(handler)))
    assert result == _ZERO_AUTOMATIONS_PAYLOAD


@pytest.mark.anyio
async def test_get_automations_classifies_5xx():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    with pytest.raises(ToolError) as exc_info:
        await get_automations(make_ctx(httpx.MockTransport(handler)))
    assert "OctoBotServerError" in str(exc_info.value)


# --- update_automations: raw-body passthrough save --------------------------


@pytest.mark.anyio
async def test_update_automations_sends_patch_as_raw_body_and_wraps_echo():
    echoed = {"automations_count": 1}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/automations"
        assert dict(request.url.params) == {"action": "save"}
        assert json.loads(request.content) == {"automations_count": 1}
        return httpx.Response(200, json=echoed)

    result = await update_automations(make_ctx(httpx.MockTransport(handler)), patch={"automations_count": 1})
    assert result == {"automations_updated_config": echoed}


@pytest.mark.anyio
async def test_update_automations_classifies_malformed_2xx_body():
    # Mirrors update_trading_config's/update_evaluator_config's own
    # equivalent test (tests/test_tools_trading_config.py) -- a 2xx response
    # with a non-JSON body is a genuine OctoBot-side malformed response, not
    # something update_automations should silently pass through as success.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json</html>")

    with pytest.raises(ToolError) as exc_info:
        await update_automations(make_ctx(httpx.MockTransport(handler)), patch={"automations_count": 1})
    assert "OctoBotMalformedResponseError" in str(exc_info.value)


@pytest.mark.anyio
async def test_update_automations_classifies_rejection():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Can't save automation config")

    with pytest.raises(ToolError) as exc_info:
        await update_automations(make_ctx(httpx.MockTransport(handler)), patch={"automations_count": -1})
    assert "AutomationConfigUpdateRejectedError" in str(exc_info.value)
    assert "Can't save automation config" in str(exc_info.value)


# --- start_automations: confirm-gated, new "autonomous ongoing behavior" ---
# category (ADR-0013 Decision 3) --------------------------------------------


@pytest.mark.anyio
async def test_start_automations_refuses_without_confirm():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("start_automations must not call OctoBot without confirm=true")

    result = await start_automations(make_ctx(httpx.MockTransport(handler)))
    assert result["confirmed"] is False
    assert result["action"] == "start_automations"
    assert result["reason"] == _START_REFUSAL_REASON
    assert "confirm=true" in result["message"]


@pytest.mark.anyio
async def test_reset_automations_to_default_refuses_without_confirm():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("reset_automations_to_default must not call OctoBot without confirm=true")

    result = await reset_automations_to_default(make_ctx(httpx.MockTransport(handler)))
    assert result["confirmed"] is False
    assert result["action"] == "reset_automations_to_default"
    assert result["reason"] == _RESET_REFUSAL_REASON
    assert "confirm=true" in result["message"]


def test_start_and_reset_refusal_reasons_differ_meaningfully():
    # The spec requires these two refusal reasons to differ meaningfully --
    # one about autonomous ongoing behavior, one about irreversibility --
    # not share boilerplate.
    assert _START_REFUSAL_REASON != _RESET_REFUSAL_REASON
    assert "autonomous" in _START_REFUSAL_REASON
    assert "cannot be undone" in _RESET_REFUSAL_REASON
    assert "autonomous" not in _RESET_REFUSAL_REASON
    assert "cannot be undone" not in _START_REFUSAL_REASON


# --- start_automations/reset_automations_to_default: JobStore active-job ---
# refusal (mirroring restart_octobot's own pattern, reused verbatim) --------


@pytest.mark.anyio
async def test_start_automations_refuses_outright_with_active_backtest_job():
    jobs = JobStore()
    job = jobs.create_job("backtest")
    jobs.start(job.job_id)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("start_automations must not call OctoBot with an active job")

    ctx = make_ctx(httpx.MockTransport(handler), jobs=jobs)
    result = await start_automations(ctx, confirm=True)

    assert result["started"] is False
    assert result["active_jobs"] == [{"job_id": job.job_id, "kind": "backtest", "state": "running"}]
    assert job.job_id in result["message"]


@pytest.mark.anyio
async def test_reset_automations_to_default_refuses_outright_with_active_data_collection_job():
    jobs = JobStore()
    job = jobs.create_job("data_collection")
    # Left `queued` (never started) -- still counts, mirroring ADR-0010 Decision 4.

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("reset_automations_to_default must not call OctoBot with an active job")

    ctx = make_ctx(httpx.MockTransport(handler), jobs=jobs)
    result = await reset_automations_to_default(ctx, confirm=True)

    assert result["reset"] is False
    assert result["active_jobs"] == [{"job_id": job.job_id, "kind": "data_collection", "state": "queued"}]


# --- start_automations/reset_automations_to_default: confirm=true, no ------
# active jobs -- the one live call -------------------------------------------


@pytest.mark.anyio
async def test_start_automations_succeeds_on_200():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/automations"
        assert dict(request.url.params) == {"action": "start"}
        return httpx.Response(200, json={})

    result = await start_automations(make_ctx(httpx.MockTransport(handler)), confirm=True)
    assert result == {"started": True}


@pytest.mark.anyio
async def test_reset_automations_to_default_succeeds_on_200():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/automations"
        assert dict(request.url.params) == {"action": "factory_reset"}
        return httpx.Response(200, json={})

    result = await reset_automations_to_default(make_ctx(httpx.MockTransport(handler)), confirm=True)
    assert result == {"reset": True}


# --- start_automations/reset_automations_to_default: InvalidAutomationConfig
# classification (ADR-0013 Decision 5), distinct from a generic rejection --


@pytest.mark.anyio
async def test_start_automations_classifies_invalid_config_as_400():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="Invalid automation configuration: bad rule")

    with pytest.raises(ToolError) as exc_info:
        await start_automations(make_ctx(httpx.MockTransport(handler)), confirm=True)
    assert "AutomationConfigInvalidError" in str(exc_info.value)
    assert "Invalid automation configuration: bad rule" in str(exc_info.value)
    assert "AutomationConfigUpdateRejectedError" not in str(exc_info.value)


@pytest.mark.anyio
async def test_reset_automations_to_default_classifies_invalid_config_as_400():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="Invalid automation configuration: bad rule")

    with pytest.raises(ToolError) as exc_info:
        await reset_automations_to_default(make_ctx(httpx.MockTransport(handler)), confirm=True)
    assert "AutomationConfigInvalidError" in str(exc_info.value)
    assert "Invalid automation configuration: bad rule" in str(exc_info.value)
    assert "AutomationConfigUpdateRejectedError" not in str(exc_info.value)


@pytest.mark.anyio
async def test_start_automations_classifies_non_400_failure_as_generic_rejection():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    with pytest.raises(ToolError) as exc_info:
        await start_automations(make_ctx(httpx.MockTransport(handler)), confirm=True)
    assert "AutomationConfigUpdateRejectedError" in str(exc_info.value)
    assert "AutomationConfigInvalidError" not in str(exc_info.value)


@pytest.mark.anyio
async def test_reset_automations_to_default_classifies_non_400_failure_as_generic_rejection():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    with pytest.raises(ToolError) as exc_info:
        await reset_automations_to_default(make_ctx(httpx.MockTransport(handler)), confirm=True)
    assert "AutomationConfigUpdateRejectedError" in str(exc_info.value)
    assert "AutomationConfigInvalidError" not in str(exc_info.value)
