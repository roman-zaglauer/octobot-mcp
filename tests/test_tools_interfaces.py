"""Unit tests for the new `octobot_mcp.tools.interfaces` module (task 21,
docs/tasklist.md item 21, ADR-0012): `get_interfaces`, `update_interface_config`,
`get_notification_routing`, `update_notification_routing`.

`ACCOUNTS_HTML`/`tests/fixtures/accounts_valid.html` is the same trimmed,
redacted real `/accounts` snapshot `test_tools_exchanges_accounts_config.py`
uses (see that fixture file's own contents) -- its `#panelServices`/
`#panelNotifications` containers back these tests.
"""

import inspect
import json
from pathlib import Path

import httpx2 as httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from octobot_mcp.tools.interfaces import (
    get_interfaces,
    get_notification_routing,
    update_interface_config,
    update_notification_routing,
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


# --- get_interfaces -----------------------------------------------------------


@pytest.mark.anyio
async def test_get_interfaces_extracts_every_service():
    result = await get_interfaces(make_ctx(httpx.MockTransport(_accounts_handler(ACCOUNTS_HTML))))
    by_service = {s["service"]: s for s in result["services"]}
    assert set(by_service) == {"telegram", "web"}


@pytest.mark.anyio
async def test_get_interfaces_redacts_secret_suffix_fields():
    result = await get_interfaces(make_ctx(httpx.MockTransport(_accounts_handler(ACCOUNTS_HTML))))
    by_service = {s["service"]: s for s in result["services"]}
    telegram_fields = by_service["telegram"]["fields"]
    # token matches the secret-suffix list -- must be redacted, never full.
    assert telegram_fields["token"] != "0000000000:FAKE-REDACTED-TOKEN-ffffffffff"
    assert "ffff" in telegram_fields["token"]  # last-4-chars convention
    assert "FAKE-REDACTED-TOKEN" not in json.dumps(result)
    # chat-id does NOT match the secret-suffix list -- passes through as-is.
    assert telegram_fields["chat-id"] == "111222333"


@pytest.mark.anyio
async def test_get_interfaces_redacts_web_password():
    result = await get_interfaces(make_ctx(httpx.MockTransport(_accounts_handler(ACCOUNTS_HTML))))
    by_service = {s["service"]: s for s in result["services"]}
    web_fields = by_service["web"]["fields"]
    assert web_fields["port"] == 5001
    assert web_fields["auto-open-in-web-browser"] is True
    assert web_fields["require-password"] is False
    assert web_fields["password"] != "*********"  # redacted, not the raw masked placeholder either


@pytest.mark.anyio
async def test_get_interfaces_returns_empty_list_for_zero_configured_services():
    empty_html = '<html><body><div id="panelServices"></div></body></html>'
    result = await get_interfaces(make_ctx(httpx.MockTransport(_accounts_handler(empty_html))))
    assert result["services"] == []


@pytest.mark.anyio
async def test_get_interfaces_raises_when_container_is_missing():
    broken_html = ACCOUNTS_HTML.replace('id="panelServices"', 'id="panelServices-renamed"')
    with pytest.raises(ToolError) as exc_info:
        await get_interfaces(make_ctx(httpx.MockTransport(_accounts_handler(broken_html))))
    assert "ConfigElementScrapeContractBrokenError" in str(exc_info.value)


def test_get_interfaces_has_no_confirm_parameter():
    assert "confirm" not in inspect.signature(get_interfaces).parameters


# --- update_interface_config: conditional gating (NFR-22) --------------------


@pytest.mark.anyio
async def test_update_interface_config_proceeds_without_confirm_for_non_credential_patch():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"global_updated_config": {}})

    result = await update_interface_config(
        make_ctx(httpx.MockTransport(handler)), service="web", patch={"port": 8080}
    )
    assert seen["body"] == {"global_config": {"services_web_port": 8080}}
    assert result == {"service": "web", "updated_fields": {"port": 8080}}


@pytest.mark.anyio
async def test_update_interface_config_refuses_without_confirm_for_credential_patch():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("OctoBot should never be called without confirm for a credential patch")

    result = await update_interface_config(
        make_ctx(httpx.MockTransport(handler)),
        service="telegram",
        patch={"token": "0000000000:NEWFAKE"},
    )
    assert result["confirmed"] is False
    assert result["action"] == "update_interface_config"
    assert "token" in result["reason"]
    assert "NEWFAKE" not in json.dumps(result)


@pytest.mark.anyio
async def test_update_interface_config_refuses_without_confirm_for_mixed_patch():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("OctoBot should never be called without confirm for a mixed patch")

    result = await update_interface_config(
        make_ctx(httpx.MockTransport(handler)),
        service="telegram",
        patch={"chat-id": "999", "token": "0000000000:NEWFAKE"},
    )
    assert result["confirmed"] is False


@pytest.mark.anyio
async def test_update_interface_config_proceeds_with_confirm_for_credential_patch_and_redacts_output():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"global_updated_config": {}})

    result = await update_interface_config(
        make_ctx(httpx.MockTransport(handler)),
        service="telegram",
        patch={"token": "0000000000:NEWFAKE"},
        confirm=True,
    )
    assert seen["body"] == {"global_config": {"services_telegram_token": "0000000000:NEWFAKE"}}
    assert result["service"] == "telegram"
    assert result["updated_fields"]["token"] != "0000000000:NEWFAKE"
    # Last-4-chars redaction convention (matches update_exchange_credentials'
    # api_key_last4): the full submitted value must never appear, but the
    # last 4 chars are deliberately still visible, same as that tool.
    assert "0000000000:NEWFAKE" not in json.dumps(result)
    assert result["updated_fields"]["token"] == "***FAKE"


@pytest.mark.anyio
async def test_update_interface_config_classifies_octobot_rejection():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json="some error")

    with pytest.raises(ToolError):
        await update_interface_config(
            make_ctx(httpx.MockTransport(handler)), service="web", patch={"port": 8080}
        )


# --- get_notification_routing / update_notification_routing ------------------


@pytest.mark.anyio
async def test_get_notification_routing_extracts_all_fields():
    result = await get_notification_routing(make_ctx(httpx.MockTransport(_accounts_handler(ACCOUNTS_HTML))))
    assert result["notification_type"] == ["web", "telegram"]
    assert result["price_alerts"] is True
    assert result["trades"] is True
    assert result["trading_script_alerts"] is True
    assert result["other"] is True
    # Discovered live but not named in the spec's example shape -- included
    # generically per ADR-0012 Decision 1's "no per-field hardcoding".
    assert result["global_info"] is True


@pytest.mark.anyio
async def test_get_notification_routing_returns_empty_dict_for_an_intact_but_empty_container():
    # NFR-21: a container with zero .config-element children must not raise.
    empty_html = '<html><body><div id="panelNotifications"></div></body></html>'
    result = await get_notification_routing(make_ctx(httpx.MockTransport(_accounts_handler(empty_html))))
    assert result == {}


@pytest.mark.anyio
async def test_get_notification_routing_raises_when_container_is_missing():
    broken_html = ACCOUNTS_HTML.replace('id="panelNotifications"', 'id="panelNotifications-renamed"')
    with pytest.raises(ToolError) as exc_info:
        await get_notification_routing(make_ctx(httpx.MockTransport(_accounts_handler(broken_html))))
    assert "ConfigElementScrapeContractBrokenError" in str(exc_info.value)


@pytest.mark.anyio
async def test_update_notification_routing_sends_only_the_provided_fields():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"global_updated_config": {}})

    result = await update_notification_routing(
        make_ctx(httpx.MockTransport(handler)),
        patch={"trading_script_alerts": False, "notification_type": ["web"]},
    )
    assert seen["body"] == {
        "global_config": {
            "notification_trading-script-alerts": False,
            "notification_notification-type": ["web"],
        }
    }
    assert result == {"updated_fields": {"trading_script_alerts": False, "notification_type": ["web"]}}


def test_update_notification_routing_has_no_confirm_parameter():
    assert "confirm" not in inspect.signature(update_notification_routing).parameters


def test_get_interfaces_and_notifications_have_no_confirm_parameter():
    assert "confirm" not in inspect.signature(get_interfaces).parameters
    assert "confirm" not in inspect.signature(get_notification_routing).parameters
