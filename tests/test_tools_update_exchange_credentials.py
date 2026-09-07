"""Unit tests for `update_exchange_credentials` (milestone 12, docs/tasklist.md item 12).

Covers: refusal without `confirm` (no HTTP call at all, same pattern as
`delete_profile`'s tests), success with `confirm=true` (asserting the exact
source-verified request shape from `src/octobot_mcp/tools/exchanges.py`'s
module docstring), the local underscore-in-exchange-name refusal, one
OctoBot-side failure-classification path, and -- the dedicated test this
milestone requires -- the INV-4/NFR-6 redaction invariant: OctoBot's own
error response text is scrubbed of the literal submitted secret values
before ever reaching a raised exception's message, and the success output
never contains anything but `api_key_last4`.
"""

import json

import httpx2 as httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from octobot_mcp.tools.exchanges import update_exchange_credentials
from tests.test_tools_shared import make_ctx

_EXCHANGE = "binance"
_API_KEY = "sk_live_ABCDEFGH1234"
_API_SECRET = "secret_ZYXWVUTS9876"
_API_PASSWORD = "memo_QQQQ"


@pytest.fixture
def anyio_backend():
    return "asyncio"


# --- confirm gating -----------------------------------------------------------


@pytest.mark.anyio
async def test_refuses_without_confirm_and_makes_no_octobot_call():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("OctoBot should never be called when confirm is not true")

    result = await update_exchange_credentials(
        make_ctx(httpx.MockTransport(handler)),
        exchange=_EXCHANGE,
        api_key=_API_KEY,
        api_secret=_API_SECRET,
    )
    assert result["confirmed"] is False
    assert result["action"] == "update_exchange_credentials"
    assert result["target"] == _EXCHANGE
    assert "trading access" in result["reason"]
    assert "confirm=true" in result["message"]
    # NFR-6: the refusal path must not leak the credentials either.
    assert _API_KEY not in json.dumps(result)
    assert _API_SECRET not in json.dumps(result)


@pytest.mark.anyio
async def test_refuses_when_confirm_is_explicitly_false():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("OctoBot should never be called when confirm=false")

    result = await update_exchange_credentials(
        make_ctx(httpx.MockTransport(handler)),
        exchange=_EXCHANGE,
        api_key=_API_KEY,
        api_secret=_API_SECRET,
        confirm=False,
    )
    assert result["confirmed"] is False


# --- success path: exact request shape (open question #2, resolved) -----------


@pytest.mark.anyio
async def test_succeeds_and_sends_the_source_verified_request_shape():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["content_type"] = request.headers["content-type"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"global_updated_config": seen["body"]["global_config"]})

    result = await update_exchange_credentials(
        make_ctx(httpx.MockTransport(handler)),
        exchange=_EXCHANGE,
        api_key=_API_KEY,
        api_secret=_API_SECRET,
        api_password=_API_PASSWORD,
        confirm=True,
    )

    assert seen["method"] == "POST"
    assert seen["path"] == "/config"
    assert seen["content_type"] == "application/json"
    assert seen["body"] == {
        "global_config": {
            "exchanges_binance_api-key": _API_KEY,
            "exchanges_binance_api-secret": _API_SECRET,
            "exchanges_binance_api-password": _API_PASSWORD,
        }
    }
    assert result == {"exchange": _EXCHANGE, "updated": True, "api_key_last4": _API_KEY[-4:]}


@pytest.mark.anyio
async def test_succeeds_without_optional_api_password():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"global_updated_config": {}})

    result = await update_exchange_credentials(
        make_ctx(httpx.MockTransport(handler)),
        exchange=_EXCHANGE,
        api_key=_API_KEY,
        api_secret=_API_SECRET,
        confirm=True,
    )

    assert seen["body"] == {
        "global_config": {
            "exchanges_binance_api-key": _API_KEY,
            "exchanges_binance_api-secret": _API_SECRET,
        }
    }
    assert "api_key_last4" in result
    assert result["api_key_last4"] == _API_KEY[-4:]


@pytest.mark.anyio
async def test_api_key_last4_falls_back_to_the_whole_key_when_shorter_than_4_chars():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"global_updated_config": {}})

    result = await update_exchange_credentials(
        make_ctx(httpx.MockTransport(handler)),
        exchange=_EXCHANGE,
        api_key="ab",
        api_secret=_API_SECRET,
        confirm=True,
    )
    assert result["api_key_last4"] == "ab"


# --- local refusal: underscore in exchange name --------------------------------


@pytest.mark.anyio
async def test_refuses_locally_for_an_exchange_name_containing_an_underscore():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(
            "OctoBot should never be called for an exchange name containing "
            "an underscore -- see update_exchange_credentials' docstring"
        )

    with pytest.raises(ToolError) as exc_info:
        await update_exchange_credentials(
            make_ctx(httpx.MockTransport(handler)),
            exchange="kucoin_futures",
            api_key=_API_KEY,
            api_secret=_API_SECRET,
            confirm=True,
        )
    assert "ExchangeCredentialsUpdateRejectedError" in str(exc_info.value)
    assert "underscore" in str(exc_info.value)


# --- OctoBot-side failure classification ---------------------------------------


@pytest.mark.anyio
async def test_classifies_octobot_rejection_as_a_tool_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json="Error while updating global config")

    with pytest.raises(ToolError) as exc_info:
        await update_exchange_credentials(
            make_ctx(httpx.MockTransport(handler)),
            exchange=_EXCHANGE,
            api_key=_API_KEY,
            api_secret=_API_SECRET,
            confirm=True,
        )
    assert "ExchangeCredentialsUpdateRejectedError" in str(exc_info.value)
    assert "Error while updating global config" in str(exc_info.value)


# --- INV-4/NFR-6: the dedicated redaction-invariant test -----------------------


@pytest.mark.anyio
async def test_redaction_invariant_no_full_secret_ever_appears_in_output_or_logs(caplog):
    """OctoBot's own error text literally contains the submitted secrets.

    Constructs the exact scenario this milestone's brief calls for: a mocked
    OctoBot response whose error body echoes back the literal `api_key`/
    `api_secret` values submitted in the same call (confirmed against source
    to be a real behavior of `/config`'s success path -- reproduced here on
    the failure path too, since INV-4 must hold regardless of outcome).
    Asserts the raised exception's message contains neither literal value,
    only the redaction marker; and that the success path (a separate call,
    below) never contains the full key/secret either, only `api_key_last4`.
    """
    leaking_error_text = (
        f"Error when updating global config: exchanges.binance.api-key="
        f"{_API_KEY} exchanges.binance.api-secret={_API_SECRET} "
        f"exchanges.binance.api-password={_API_PASSWORD} is invalid"
    )

    def failing_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json=leaking_error_text)

    with caplog.at_level("DEBUG"):
        with pytest.raises(ToolError) as exc_info:
            await update_exchange_credentials(
                make_ctx(httpx.MockTransport(failing_handler)),
                exchange=_EXCHANGE,
                api_key=_API_KEY,
                api_secret=_API_SECRET,
                api_password=_API_PASSWORD,
                confirm=True,
            )

    error_message = str(exc_info.value)
    assert _API_KEY not in error_message
    assert _API_SECRET not in error_message
    assert _API_PASSWORD not in error_message
    assert "[REDACTED]" in error_message
    for record in caplog.records:
        assert _API_KEY not in record.getMessage()
        assert _API_SECRET not in record.getMessage()
        assert _API_PASSWORD not in record.getMessage()

    # Success path, independently: the output must never contain the full
    # key/secret/password at all -- not even a redacted echo -- only the
    # deliberately-constructed `api_key_last4` field.
    def success_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "global_updated_config": {
                    "exchanges_binance_api-key": _API_KEY,
                    "exchanges_binance_api-secret": _API_SECRET,
                    "exchanges_binance_api-password": _API_PASSWORD,
                }
            },
        )

    with caplog.at_level("DEBUG"):
        result = await update_exchange_credentials(
            make_ctx(httpx.MockTransport(success_handler)),
            exchange=_EXCHANGE,
            api_key=_API_KEY,
            api_secret=_API_SECRET,
            api_password=_API_PASSWORD,
            confirm=True,
        )

    result_text = json.dumps(result)
    assert _API_KEY not in result_text
    assert _API_SECRET not in result_text
    assert _API_PASSWORD not in result_text
    assert result["api_key_last4"] == _API_KEY[-4:]
    for record in caplog.records:
        assert _API_KEY not in record.getMessage()
        assert _API_SECRET not in record.getMessage()
        assert _API_PASSWORD not in record.getMessage()
