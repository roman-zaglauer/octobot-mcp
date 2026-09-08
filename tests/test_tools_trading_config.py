"""Unit tests for milestone 13.5's `update_trading_config` (docs/tasklist.md item 13.5),
task 16's `update_evaluator_config` (docs/tasklist.md item 16, FR-28), and
task 19's `list_evaluators` (docs/tasklist.md item 19, ADR-0009 reversed to GO).

See `src/octobot_mcp/tools/trading_config.py`'s module docstring for the
exact request/response shape these tests assert, verified against OctoBot
source (and, for `list_evaluators`, a real live fetch) as part of each
tool's implementation.

`EVALUATOR_CONFIG_HTML`/`tests/fixtures/evaluator_config_valid.html` is a
real, trimmed snapshot of the live test instance's own
`/advanced/evaluator_config` response (OctoBot 2.1.1) -- 2 technical-analysis
rows (one enabled, one disabled), 1 social row, 1 real-time row, and a
genuinely empty (but present) scripted-evaluators container, mirroring this
instance's own real "Scripted evaluators has zero installed evaluators"
state -- not a hand-authored guess. The broken-fixture tests mutate specific,
known substrings of that same real snapshot to violate exactly one
structural assumption each, per NFR-18's acceptance criterion.
"""

import inspect
import json
from pathlib import Path

import httpx2 as httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from octobot_mcp.tools.trading_config import list_evaluators, update_evaluator_config, update_trading_config
from tests.test_tools_shared import make_ctx

FIXTURES_DIR = Path(__file__).parent / "fixtures"
EVALUATOR_CONFIG_HTML = (FIXTURES_DIR / "evaluator_config_valid.html").read_text()


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


# --- list_evaluators: third Tier-B scrape adapter (task 19, ADR-0009) -------


def _evaluator_html_handler(html: str, *, status_code: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/advanced/evaluator_config"
        return httpx.Response(status_code, text=html)

    return handler


@pytest.mark.anyio
async def test_list_evaluators_extracts_all_fields_across_all_categories():
    result = await list_evaluators(make_ctx(httpx.MockTransport(_evaluator_html_handler(EVALUATOR_CONFIG_HTML))))
    evaluators_by_name = {e["name"]: e for e in result["evaluators"]}
    assert set(evaluators_by_name) == {
        "ADXMomentumEvaluator",
        "BBMomentumEvaluator",
        "CryptoMarketCapEvaluator",
        "DSLRealtimeEvaluator",
    }

    assert evaluators_by_name["ADXMomentumEvaluator"] == {
        "name": "ADXMomentumEvaluator",
        "enabled": False,
        "type": "technical_analysis",
    }
    assert evaluators_by_name["BBMomentumEvaluator"] == {
        "name": "BBMomentumEvaluator",
        "enabled": True,
        "type": "technical_analysis",
    }
    assert evaluators_by_name["CryptoMarketCapEvaluator"] == {
        "name": "CryptoMarketCapEvaluator",
        "enabled": False,
        "type": "social",
    }
    assert evaluators_by_name["DSLRealtimeEvaluator"] == {
        "name": "DSLRealtimeEvaluator",
        "enabled": False,
        "type": "realtime",
    }


@pytest.mark.anyio
async def test_list_evaluators_does_not_raise_when_one_category_is_legitimately_empty():
    # The fixture's own scripted-evaluator-config-root container is present
    # but has zero config-element rows inside it -- mirroring the live test
    # instance's own real "Scripted evaluators" state (see this module's own
    # docstring). This must not raise, and no "scripted"-typed evaluator
    # should appear.
    result = await list_evaluators(make_ctx(httpx.MockTransport(_evaluator_html_handler(EVALUATOR_CONFIG_HTML))))
    assert "scripted" not in {e["type"] for e in result["evaluators"]}


@pytest.mark.anyio
async def test_list_evaluators_raises_when_zero_rows_found_across_all_four_containers():
    # Rename the `config-element` class everywhere so the row selector
    # matches nothing in ANY of the four (still-present) containers --
    # exercising the whole-page "zero evaluators found" gate specifically,
    # distinct from the per-row/missing-container tests below.
    broken_html = EVALUATOR_CONFIG_HTML.replace("config-element", "config-element-renamed")
    with pytest.raises(ToolError) as exc_info:
        await list_evaluators(make_ctx(httpx.MockTransport(_evaluator_html_handler(broken_html))))
    assert "EvaluatorScrapeContractBrokenError" in str(exc_info.value)


@pytest.mark.anyio
async def test_list_evaluators_raises_when_a_category_container_is_missing():
    broken_html = EVALUATOR_CONFIG_HTML.replace(
        'id="scripted-evaluator-config-root"', 'id="scripted-evaluator-config-root-renamed"'
    )
    with pytest.raises(ToolError) as exc_info:
        await list_evaluators(make_ctx(httpx.MockTransport(_evaluator_html_handler(broken_html))))
    assert "EvaluatorScrapeContractBrokenError" in str(exc_info.value)


@pytest.mark.anyio
async def test_list_evaluators_raises_when_a_row_is_missing_its_config_key():
    broken_html = EVALUATOR_CONFIG_HTML.replace('config-key="BBMomentumEvaluator" ', "", 1)
    with pytest.raises(ToolError) as exc_info:
        await list_evaluators(make_ctx(httpx.MockTransport(_evaluator_html_handler(broken_html))))
    assert "EvaluatorScrapeContractBrokenError" in str(exc_info.value)


@pytest.mark.anyio
async def test_list_evaluators_raises_when_current_value_is_not_true_or_false():
    broken_html = EVALUATOR_CONFIG_HTML.replace('current-value="True"', 'current-value="yes"', 1)
    with pytest.raises(ToolError) as exc_info:
        await list_evaluators(make_ctx(httpx.MockTransport(_evaluator_html_handler(broken_html))))
    assert "EvaluatorScrapeContractBrokenError" in str(exc_info.value)


@pytest.mark.anyio
async def test_list_evaluators_never_returns_a_partial_list_on_broken_scrape():
    """Mirrors `test_list_data_files_never_returns_a_partial_list_on_broken_scrape`'s
    intent exactly (NFR-18's own acceptance criterion)."""
    broken_html = EVALUATOR_CONFIG_HTML.replace(
        'id="ta-evaluator-config-root"', 'id="ta-evaluator-config-root-renamed"'
    )
    with pytest.raises(ToolError):
        result_that_should_never_exist = None
        try:
            result_that_should_never_exist = await list_evaluators(
                make_ctx(httpx.MockTransport(_evaluator_html_handler(broken_html)))
            )
        finally:
            assert result_that_should_never_exist is None


@pytest.mark.anyio
async def test_list_evaluators_classifies_5xx_as_tool_error():
    with pytest.raises(ToolError):
        await list_evaluators(make_ctx(httpx.MockTransport(_evaluator_html_handler("", status_code=500))))


def test_list_evaluators_has_no_confirm_parameter():
    # Read-only, not confirm-gated (ADR-0003).
    params = inspect.signature(list_evaluators).parameters
    assert "confirm" not in params
    assert list(params) == ["ctx"]
