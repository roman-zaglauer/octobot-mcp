"""Unit tests for milestone 13.5's `update_trading_config` (docs/tasklist.md item 13.5)
and task 16's `update_evaluator_config` (docs/tasklist.md item 16, FR-28).

See `src/octobot_mcp/tools/trading_config.py`'s module docstring for the
exact request/response shape these tests assert, verified against OctoBot
source as part of each tool's implementation.
"""

import inspect
import json

import httpx2 as httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from octobot_mcp.tools.trading_config import update_evaluator_config, update_trading_config
from tests.test_tools_shared import make_ctx


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_update_trading_config_sends_patch_under_trading_config_key():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/config"
        assert json.loads(request.content) == {
            "trading_config": {"DailyTradingMode": True}
        }
        # Reproduces OctoBot's own confirmed success envelope: this tool
        # only sends `trading_config`, so every sibling key is the route's
        # own empty-string default for an absent key.
        return httpx.Response(
            200,
            json={
                "evaluator_updated_config": "",
                "trading_updated_config": {"DailyTradingMode": True},
                "tentacle_updated_config": "",
                "global_updated_config": "",
                "removed_elements": "",
            },
        )

    result = await update_trading_config(
        make_ctx(httpx.MockTransport(handler)), patch={"DailyTradingMode": True}
    )
    assert result == {"trading_updated_config": {"DailyTradingMode": True}}


@pytest.mark.anyio
async def test_update_trading_config_classifies_failure_with_octobot_error_text():
    # Reproduces the source-confirmed shape for this tool's own failures:
    # OctoBot's `err_message` local variable is never populated by the
    # `trading_config` branch, so the 500 body is always a bare empty
    # JSON string, not a substantive message.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json="")

    with pytest.raises(ToolError) as exc_info:
        await update_trading_config(
            make_ctx(httpx.MockTransport(handler)), patch={"DailyTradingMode": True}
        )
    assert "TradingConfigUpdateRejectedError" in str(exc_info.value)


@pytest.mark.anyio
async def test_update_trading_config_classifies_4xx():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad request")

    with pytest.raises(ToolError) as exc_info:
        await update_trading_config(
            make_ctx(httpx.MockTransport(handler)), patch={"DailyTradingMode": True}
        )
    assert "TradingConfigUpdateRejectedError" in str(exc_info.value)


@pytest.mark.anyio
async def test_update_trading_config_classifies_malformed_2xx_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json</html>")

    with pytest.raises(ToolError) as exc_info:
        await update_trading_config(
            make_ctx(httpx.MockTransport(handler)), patch={"DailyTradingMode": True}
        )
    assert "OctoBotMalformedResponseError" in str(exc_info.value)


@pytest.mark.anyio
async def test_update_trading_config_classifies_2xx_body_missing_the_echo_key():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"global_updated_config": ""})

    with pytest.raises(ToolError) as exc_info:
        await update_trading_config(
            make_ctx(httpx.MockTransport(handler)), patch={"DailyTradingMode": True}
        )
    assert "OctoBotMalformedResponseError" in str(exc_info.value)


def test_update_trading_config_has_no_confirm_parameter():
    # Not confirm-gated (ADR-0003): assert the signature itself carries no
    # `confirm` parameter at all, not just that one is unused.
    params = inspect.signature(update_trading_config).parameters
    assert "confirm" not in params
    assert list(params) == ["ctx", "patch"]


@pytest.mark.anyio
async def test_update_evaluator_config_sends_patch_under_evaluator_config_key_with_default_deactivate_others():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/config"
        # Two top-level keys, never one nested under the other -- see this
        # module's docstring for the source-verified derivation.
        assert json.loads(request.content) == {
            "evaluator_config": {"RSIMomentumEvaluator": True},
            "deactivate_others": False,
        }
        return httpx.Response(
            200,
            json={
                "evaluator_updated_config": {"RSIMomentumEvaluator": True},
                "trading_updated_config": "",
                "tentacle_updated_config": "",
                "global_updated_config": "",
                "removed_elements": "",
            },
        )

    result = await update_evaluator_config(
        make_ctx(httpx.MockTransport(handler)), patch={"RSIMomentumEvaluator": True}
    )
    assert result == {"evaluator_updated_config": {"RSIMomentumEvaluator": True}}


@pytest.mark.anyio
async def test_update_evaluator_config_sends_deactivate_others_true_as_its_own_top_level_key():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        # `deactivate_others` is a sibling top-level request-body key, not
        # nested inside the `evaluator_config` patch dict -- confirmed
        # against source, see this module's docstring.
        assert body == {
            "evaluator_config": {"RSIMomentumEvaluator": True},
            "deactivate_others": True,
        }
        return httpx.Response(
            200,
            json={
                "evaluator_updated_config": {"RSIMomentumEvaluator": True},
                "trading_updated_config": "",
                "tentacle_updated_config": "",
                "global_updated_config": "",
                "removed_elements": "",
            },
        )

    result = await update_evaluator_config(
        make_ctx(httpx.MockTransport(handler)),
        patch={"RSIMomentumEvaluator": True},
        deactivate_others=True,
    )
    assert result == {"evaluator_updated_config": {"RSIMomentumEvaluator": True}}


@pytest.mark.anyio
async def test_update_evaluator_config_classifies_failure_with_octobot_error_text():
    # Same source-confirmed shape as `update_trading_config`'s own failure
    # test: OctoBot's `err_message` is never populated by the
    # `evaluator_config` branch, so the 500 body is always a bare empty
    # JSON string.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json="")

    with pytest.raises(ToolError) as exc_info:
        await update_evaluator_config(
            make_ctx(httpx.MockTransport(handler)), patch={"RSIMomentumEvaluator": True}
        )
    assert "EvaluatorConfigUpdateRejectedError" in str(exc_info.value)


@pytest.mark.anyio
async def test_update_evaluator_config_classifies_4xx():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad request")

    with pytest.raises(ToolError) as exc_info:
        await update_evaluator_config(
            make_ctx(httpx.MockTransport(handler)), patch={"RSIMomentumEvaluator": True}
        )
    assert "EvaluatorConfigUpdateRejectedError" in str(exc_info.value)


@pytest.mark.anyio
async def test_update_evaluator_config_classifies_malformed_2xx_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json</html>")

    with pytest.raises(ToolError) as exc_info:
        await update_evaluator_config(
            make_ctx(httpx.MockTransport(handler)), patch={"RSIMomentumEvaluator": True}
        )
    assert "OctoBotMalformedResponseError" in str(exc_info.value)


@pytest.mark.anyio
async def test_update_evaluator_config_classifies_2xx_body_missing_the_echo_key():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"global_updated_config": ""})

    with pytest.raises(ToolError) as exc_info:
        await update_evaluator_config(
            make_ctx(httpx.MockTransport(handler)), patch={"RSIMomentumEvaluator": True}
        )
    assert "OctoBotMalformedResponseError" in str(exc_info.value)


def test_update_evaluator_config_has_no_confirm_parameter():
    # Not confirm-gated (ADR-0003): assert the signature itself carries no
    # `confirm` parameter at all, not just that one is unused.
    params = inspect.signature(update_evaluator_config).parameters
    assert "confirm" not in params
    assert list(params) == ["ctx", "patch", "deactivate_others"]
