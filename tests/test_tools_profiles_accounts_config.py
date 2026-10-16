"""Unit tests for task 21's three new `profiles.py` tools (docs/tasklist.md
item 21, ADR-0012): `set_real_trading_enabled`, `get_profile_currencies`,
`update_profile_currencies`.

`PROFILE_CURRENCY_HTML`/`tests/fixtures/profile_currency_valid.html` is a
trimmed real snapshot of the live test instance's own `GET /profile`
response's `#panelCurrency` panel (OctoBot 2.1.1) -- 2 currencies (Bitcoin
enabled with 1 pair, Ethereum disabled with 2 pairs), mirroring
`test_tools_trading_config.py`'s own established "real, trimmed snapshot,
broken-fixture tests mutate known substrings" convention.
"""

import inspect
import json
from pathlib import Path

import httpx2 as httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from octobot_mcp.jobs import JobStore
from octobot_mcp.tools.profiles import get_profile_currencies, set_real_trading_enabled, update_profile_currencies
from tests.test_tools_shared import make_ctx

FIXTURES_DIR = Path(__file__).parent / "fixtures"
PROFILE_CURRENCY_HTML = (FIXTURES_DIR / "profile_currency_valid.html").read_text()


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _profile_handler(html: str, *, status_code: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/profile"
        return httpx.Response(status_code, text=html)

    return handler


# --- set_real_trading_enabled: confirm gating, both directions --------------


@pytest.mark.anyio
async def test_set_real_trading_enabled_refuses_without_confirm_when_enabling():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("OctoBot should never be called when confirm is not true")

    result = await set_real_trading_enabled(make_ctx(httpx.MockTransport(handler)), enabled=True)
    assert result["confirmed"] is False
    assert result["action"] == "set_real_trading_enabled"
    assert "restart" in result["reason"].lower()
    assert "real" in result["reason"].lower() or "money" in result["reason"].lower()


@pytest.mark.anyio
async def test_set_real_trading_enabled_refuses_without_confirm_when_disabling():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("OctoBot should never be called when confirm is not true")

    result = await set_real_trading_enabled(make_ctx(httpx.MockTransport(handler)), enabled=False)
    assert result["confirmed"] is False
    assert "restart" in result["reason"].lower()


@pytest.mark.anyio
async def test_set_real_trading_enabled_reason_differs_by_direction():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no octobot call expected")

    enabling = await set_real_trading_enabled(make_ctx(httpx.MockTransport(handler)), enabled=True)
    disabling = await set_real_trading_enabled(make_ctx(httpx.MockTransport(handler)), enabled=False)
    assert enabling["reason"] != disabling["reason"]


# --- set_real_trading_enabled: JobStore active-job refusal -------------------


@pytest.mark.anyio
async def test_set_real_trading_enabled_refuses_outright_with_active_backtest_job():
    jobs = JobStore()
    job = jobs.create_job("backtest")
    jobs.start(job.job_id)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("OctoBot should never be called with an active job")

    ctx = make_ctx(httpx.MockTransport(handler), jobs=jobs)
    result = await set_real_trading_enabled(ctx, enabled=True, confirm=True)
    assert result["restart_triggered"] is False
    assert result["active_jobs"] == [{"job_id": job.job_id, "kind": "backtest", "state": "running"}]


@pytest.mark.anyio
async def test_set_real_trading_enabled_refuses_outright_with_active_data_collection_job():
    jobs = JobStore()
    job = jobs.create_job("data_collection")

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("OctoBot should never be called with an active job")

    ctx = make_ctx(httpx.MockTransport(handler), jobs=jobs)
    result = await set_real_trading_enabled(ctx, enabled=True, confirm=True)
    assert result["restart_triggered"] is False
    assert result["active_jobs"] == [{"job_id": job.job_id, "kind": "data_collection", "state": "queued"}]


@pytest.mark.anyio
async def test_set_real_trading_enabled_proceeds_when_only_terminal_jobs_exist():
    jobs = JobStore()
    job = jobs.create_job("backtest")
    jobs.start(job.job_id)
    jobs.complete(job.job_id, result={})

    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"global_updated_config": {}})

    ctx = make_ctx(httpx.MockTransport(handler), jobs=jobs)
    result = await set_real_trading_enabled(ctx, enabled=True, confirm=True)
    assert result["restart_triggered"] is True
    assert seen["body"] == {
        "global_config": {"trader_enabled": True, "trader-simulator_enabled": False},
        "restart_after_save": True,
    }


# --- set_real_trading_enabled: confirm=true, no active job -------------------


@pytest.mark.anyio
async def test_set_real_trading_enabled_sends_both_keys_and_restart_flag_when_enabling():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"global_updated_config": {}})

    result = await set_real_trading_enabled(make_ctx(httpx.MockTransport(handler)), enabled=True, confirm=True)
    assert seen["body"] == {
        "global_config": {"trader_enabled": True, "trader-simulator_enabled": False},
        "restart_after_save": True,
    }
    assert result == {
        "real_trading_enabled": True,
        "restart_triggered": True,
        "message": result["message"],
    }
    assert "wait_for_octobot_ready" in result["message"]


@pytest.mark.anyio
async def test_set_real_trading_enabled_sends_both_keys_when_disabling():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"global_updated_config": {}})

    result = await set_real_trading_enabled(make_ctx(httpx.MockTransport(handler)), enabled=False, confirm=True)
    assert seen["body"] == {
        "global_config": {"trader_enabled": False, "trader-simulator_enabled": True},
        "restart_after_save": True,
    }
    assert result["real_trading_enabled"] is False
    assert result["restart_triggered"] is True


@pytest.mark.anyio
async def test_set_real_trading_enabled_classifies_octobot_rejection():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json="some error")

    with pytest.raises(ToolError):
        await set_real_trading_enabled(make_ctx(httpx.MockTransport(handler)), enabled=True, confirm=True)


# --- get_profile_currencies ---------------------------------------------------


@pytest.mark.anyio
async def test_get_profile_currencies_extracts_every_currency():
    result = await get_profile_currencies(make_ctx(httpx.MockTransport(_profile_handler(PROFILE_CURRENCY_HTML))))
    by_currency = {c["currency"]: c for c in result["currencies"]}
    assert by_currency["Bitcoin"] == {"currency": "Bitcoin", "enabled": True, "pairs": ["BTC/EUR"]}
    assert by_currency["Ethereum"] == {
        "currency": "Ethereum",
        "enabled": False,
        "pairs": ["ETH/BTC", "ETH/USDT"],
    }


@pytest.mark.anyio
async def test_get_profile_currencies_returns_empty_list_for_zero_currencies():
    empty_html = '<html><body><div id="panelCurrency"></div></body></html>'
    result = await get_profile_currencies(make_ctx(httpx.MockTransport(_profile_handler(empty_html))))
    assert result["currencies"] == []


@pytest.mark.anyio
async def test_get_profile_currencies_raises_when_container_is_missing():
    broken_html = PROFILE_CURRENCY_HTML.replace('id="panelCurrency"', 'id="panelCurrency-renamed"')
    with pytest.raises(ToolError) as exc_info:
        await get_profile_currencies(make_ctx(httpx.MockTransport(_profile_handler(broken_html))))
    assert "ConfigElementScrapeContractBrokenError" in str(exc_info.value)


@pytest.mark.anyio
async def test_get_profile_currencies_raises_when_a_currency_is_missing_its_enabled_field():
    broken_html = PROFILE_CURRENCY_HTML.replace(
        'config-key="crypto-currencies_Bitcoin_enabled"', "config-key-removed", 1
    )
    with pytest.raises(ToolError) as exc_info:
        await get_profile_currencies(make_ctx(httpx.MockTransport(_profile_handler(broken_html))))
    assert "ConfigElementScrapeContractBrokenError" in str(exc_info.value)


def test_get_profile_currencies_has_no_confirm_parameter():
    assert "confirm" not in inspect.signature(get_profile_currencies).parameters


# --- update_profile_currencies ------------------------------------------------


@pytest.mark.anyio
async def test_update_profile_currencies_sends_only_the_provided_sub_fields():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"global_updated_config": {}})

    patch = {"Bitcoin": {"enabled": False}, "Ethereum": {"pairs": ["ETH/EUR"]}}
    result = await update_profile_currencies(make_ctx(httpx.MockTransport(handler)), patch=patch)
    assert seen["body"] == {
        "global_config": {
            "crypto-currencies_Bitcoin_enabled": False,
            "crypto-currencies_Ethereum_pairs": ["ETH/EUR"],
        }
    }
    assert result == {"updated_fields": patch}


@pytest.mark.anyio
async def test_update_profile_currencies_classifies_octobot_rejection():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json="some error")

    with pytest.raises(ToolError):
        await update_profile_currencies(
            make_ctx(httpx.MockTransport(handler)), patch={"Bitcoin": {"enabled": True}}
        )


def test_update_profile_currencies_has_no_confirm_parameter():
    assert "confirm" not in inspect.signature(update_profile_currencies).parameters
