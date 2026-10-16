"""Unit tests for task 21's four new `exchanges.py` tools (docs/tasklist.md
item 21, ADR-0012): `get_exchanges`, `add_exchange`, `remove_exchange`,
`update_exchange_trading_mode`.

`ACCOUNTS_HTML`/`tests/fixtures/accounts_valid.html` is a trimmed, redacted
real snapshot of the live test instance's own `GET /accounts` response
(OctoBot 2.1.1) -- see that fixture file and
`src/octobot_mcp/tools/exchanges.py`'s module docstring for the exact
captured DOM shape. Broken-fixture tests mutate specific, known substrings
of that same snapshot to violate exactly one structural assumption each, per
NFR-21's acceptance criterion, mirroring `test_tools_trading_config.py`'s
own established convention for `list_evaluators`.
"""

import inspect
import json
from pathlib import Path

import httpx2 as httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from octobot_mcp.tools.exchanges import (
    add_exchange,
    get_exchanges,
    remove_exchange,
    update_exchange_trading_mode,
)
from tests.test_tools_shared import make_ctx

FIXTURES_DIR = Path(__file__).parent / "fixtures"
ACCOUNTS_HTML = (FIXTURES_DIR / "accounts_valid.html").read_text()


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _accounts_handler(html: str, *, status_code: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/accounts"
        return httpx.Response(status_code, text=html)

    return handler


# --- get_exchanges: valid fixture --------------------------------------------


@pytest.mark.anyio
async def test_get_exchanges_extracts_every_exchange():
    result = await get_exchanges(make_ctx(httpx.MockTransport(_accounts_handler(ACCOUNTS_HTML))))
    by_name = {e["name"]: e for e in result["exchanges"]}
    assert set(by_name) == {"binance", "kraken"}


@pytest.mark.anyio
async def test_get_exchanges_reports_credentials_configured_true_when_masked():
    # binance's api-key config-value IS OctoBot's own masked placeholder --
    # confirmed live this means credentials ARE configured (see this
    # module's docstring for the correction to a literal reading of ADR-0012's
    # "differs from the placeholder" phrasing).
    result = await get_exchanges(make_ctx(httpx.MockTransport(_accounts_handler(ACCOUNTS_HTML))))
    by_name = {e["name"]: e for e in result["exchanges"]}
    assert by_name["binance"]["enabled"] is True
    assert by_name["binance"]["credentials_configured"] is True


@pytest.mark.anyio
async def test_get_exchanges_reports_credentials_configured_false_when_empty():
    result = await get_exchanges(make_ctx(httpx.MockTransport(_accounts_handler(ACCOUNTS_HTML))))
    by_name = {e["name"]: e for e in result["exchanges"]}
    assert by_name["kraken"]["enabled"] is False
    assert by_name["kraken"]["credentials_configured"] is False


@pytest.mark.anyio
async def test_get_exchanges_never_leaks_the_masked_credential_value():
    result = await get_exchanges(make_ctx(httpx.MockTransport(_accounts_handler(ACCOUNTS_HTML))))
    assert "*********" not in json.dumps(result)


@pytest.mark.anyio
async def test_get_exchanges_exchange_type_is_none_on_the_accounts_page():
    # Confirmed live: /accounts' exchange card has no exchange-type
    # config-element at all (only /profile's lite_config=True card does) --
    # see this module's docstring for the spec correction.
    result = await get_exchanges(make_ctx(httpx.MockTransport(_accounts_handler(ACCOUNTS_HTML))))
    by_name = {e["name"]: e for e in result["exchanges"]}
    assert by_name["binance"]["exchange_type"] is None


# --- get_exchanges: zero-but-intact and broken fixtures ----------------------


@pytest.mark.anyio
async def test_get_exchanges_returns_empty_list_for_zero_configured_exchanges():
    # A fresh OctoBot instance with zero configured exchanges still renders
    # an intact #exchange-container -- just with nothing inside it. NFR-21:
    # this must NOT raise.
    empty_html = '<html><body><div id="exchange-container"></div></body></html>'
    result = await get_exchanges(make_ctx(httpx.MockTransport(_accounts_handler(empty_html))))
    assert result["exchanges"] == []


@pytest.mark.anyio
async def test_get_exchanges_raises_when_container_is_missing():
    broken_html = ACCOUNTS_HTML.replace('id="exchange-container"', 'id="exchange-container-renamed"')
    with pytest.raises(ToolError) as exc_info:
        await get_exchanges(make_ctx(httpx.MockTransport(_accounts_handler(broken_html))))
    assert "ConfigElementScrapeContractBrokenError" in str(exc_info.value)


@pytest.mark.anyio
async def test_get_exchanges_raises_when_an_exchange_is_missing_its_enabled_field():
    broken_html = ACCOUNTS_HTML.replace('config-key="exchanges_binance_enabled"', "config-key-removed", 1)
    with pytest.raises(ToolError) as exc_info:
        await get_exchanges(make_ctx(httpx.MockTransport(_accounts_handler(broken_html))))
    assert "ConfigElementScrapeContractBrokenError" in str(exc_info.value)


@pytest.mark.anyio
async def test_get_exchanges_classifies_5xx():
    with pytest.raises(ToolError):
        await get_exchanges(make_ctx(httpx.MockTransport(_accounts_handler("", status_code=500))))


def test_get_exchanges_has_no_confirm_parameter():
    assert "confirm" not in inspect.signature(get_exchanges).parameters


# --- add_exchange: confirm gating --------------------------------------------


@pytest.mark.anyio
async def test_add_exchange_refuses_without_confirm_and_makes_no_octobot_call():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("OctoBot should never be called when confirm is not true")

    result = await add_exchange(
        make_ctx(httpx.MockTransport(handler)), exchange="coinbase", api_key="k", api_secret="s"
    )
    assert result["confirmed"] is False
    assert result["action"] == "add_exchange"
    assert result["target"] == "coinbase"


@pytest.mark.anyio
async def test_add_exchange_refuses_locally_for_underscore_in_exchange_name():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("OctoBot should never be called for an underscore exchange name")

    with pytest.raises(ToolError) as exc_info:
        await add_exchange(
            make_ctx(httpx.MockTransport(handler)),
            exchange="kucoin_futures",
            api_key="k",
            api_secret="s",
            confirm=True,
        )
    assert "ExchangeCredentialsUpdateRejectedError" in str(exc_info.value)
    assert "underscore" in str(exc_info.value)


@pytest.mark.anyio
async def test_add_exchange_sends_the_enabled_true_flag_on_top_of_credentials():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"global_updated_config": {}})

    result = await add_exchange(
        make_ctx(httpx.MockTransport(handler)),
        exchange="coinbase",
        api_key="key123456",
        api_secret="secret123456",
        api_password="pw1234",
        confirm=True,
    )
    assert seen["body"] == {
        "global_config": {
            "exchanges_coinbase_api-key": "key123456",
            "exchanges_coinbase_api-secret": "secret123456",
            "exchanges_coinbase_api-password": "pw1234",
            "exchanges_coinbase_enabled": True,
        }
    }
    assert result == {"exchange": "coinbase", "added": True, "api_key_last4": "3456"}


@pytest.mark.anyio
async def test_add_exchange_classifies_octobot_rejection():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json="some error")

    with pytest.raises(ToolError) as exc_info:
        await add_exchange(
            make_ctx(httpx.MockTransport(handler)),
            exchange="coinbase",
            api_key="key123456",
            api_secret="secret123456",
            confirm=True,
        )
    assert "ExchangeCredentialsUpdateRejectedError" in str(exc_info.value)


# --- remove_exchange: confirm gating -----------------------------------------


@pytest.mark.anyio
async def test_remove_exchange_refuses_without_confirm_and_makes_no_octobot_call():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("OctoBot should never be called when confirm is not true")

    result = await remove_exchange(make_ctx(httpx.MockTransport(handler)), exchange="binance")
    assert result["confirmed"] is False
    assert result["action"] == "remove_exchange"
    assert "credentials" in result["reason"]


@pytest.mark.anyio
async def test_remove_exchange_refuses_locally_for_underscore_in_exchange_name():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("OctoBot should never be called for an underscore exchange name")

    with pytest.raises(ToolError) as exc_info:
        await remove_exchange(make_ctx(httpx.MockTransport(handler)), exchange="kucoin_futures", confirm=True)
    assert "ExchangeCredentialsUpdateRejectedError" in str(exc_info.value)
    assert "underscore" in str(exc_info.value)


@pytest.mark.anyio
async def test_remove_exchange_sends_removed_elements_and_succeeds():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"removed_elements": ["exchanges_binance"]})

    result = await remove_exchange(make_ctx(httpx.MockTransport(handler)), exchange="binance", confirm=True)
    assert seen["body"] == {"removed_elements": ["exchanges_binance"]}
    assert result == {"exchange": "binance", "removed": True}


@pytest.mark.anyio
async def test_remove_exchange_classifies_octobot_rejection():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json="some error")

    with pytest.raises(ToolError) as exc_info:
        await remove_exchange(make_ctx(httpx.MockTransport(handler)), exchange="binance", confirm=True)
    assert "ExchangeCredentialsUpdateRejectedError" in str(exc_info.value)


# --- update_exchange_trading_mode: not confirm-gated -------------------------


def test_update_exchange_trading_mode_has_no_confirm_parameter():
    assert "confirm" not in inspect.signature(update_exchange_trading_mode).parameters


@pytest.mark.anyio
async def test_update_exchange_trading_mode_sends_only_the_provided_fields():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"global_updated_config": {}})

    result = await update_exchange_trading_mode(
        make_ctx(httpx.MockTransport(handler)), exchange="binance", exchange_type="future"
    )
    assert seen["body"] == {"global_config": {"exchanges_binance_exchange-type": "future"}}
    assert result == {"exchange": "binance", "updated_fields": {"exchange_type": "future"}}


@pytest.mark.anyio
async def test_update_exchange_trading_mode_sends_both_fields_when_both_given():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"global_updated_config": {}})

    await update_exchange_trading_mode(
        make_ctx(httpx.MockTransport(handler)), exchange="binance", exchange_type="spot", enabled=False
    )
    assert seen["body"] == {
        "global_config": {
            "exchanges_binance_exchange-type": "spot",
            "exchanges_binance_enabled": False,
        }
    }


@pytest.mark.anyio
async def test_update_exchange_trading_mode_raises_when_neither_field_given():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("OctoBot should never be called with no fields to update")

    with pytest.raises(ToolError) as exc_info:
        await update_exchange_trading_mode(make_ctx(httpx.MockTransport(handler)), exchange="binance")
    assert "ExchangeCredentialsUpdateRejectedError" in str(exc_info.value)


@pytest.mark.anyio
async def test_update_exchange_trading_mode_refuses_locally_for_underscore_in_exchange_name():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("OctoBot should never be called for an underscore exchange name")

    with pytest.raises(ToolError) as exc_info:
        await update_exchange_trading_mode(
            make_ctx(httpx.MockTransport(handler)), exchange="kucoin_futures", enabled=True
        )
    assert "ExchangeCredentialsUpdateRejectedError" in str(exc_info.value)
    assert "underscore" in str(exc_info.value)


@pytest.mark.anyio
async def test_update_exchange_trading_mode_classifies_octobot_rejection():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json="some error")

    with pytest.raises(ToolError) as exc_info:
        await update_exchange_trading_mode(make_ctx(httpx.MockTransport(handler)), exchange="binance", enabled=True)
    assert "ExchangeCredentialsUpdateRejectedError" in str(exc_info.value)
