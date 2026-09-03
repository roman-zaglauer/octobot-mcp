"""HTTP client wrapper tests (NFR-4, NFR-5, NFR-10).

Per the milestone's demo criterion, each connectivity failure class is
injected via an `httpx2.MockTransport` and asserted to produce the
correct `OctoBotAPIError` subclass out of `request_json`, the single
error-classification helper.
"""

import httpx2 as httpx
import pytest

from octobot_mcp.client import REST_TIMEOUT_SECONDS, create_http_client, request_json
from octobot_mcp.config import OctoBotConfig
from octobot_mcp.errors import (
    OctoBotClientError,
    OctoBotConnectionError,
    OctoBotMalformedResponseError,
    OctoBotServerError,
    OctoBotTimeoutError,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _client_with_transport(transport: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url="http://octobot.example", transport=transport)


# --- NFR-10: client factory -------------------------------------------------


def test_create_http_client_uses_configured_base_url():
    config = OctoBotConfig(base_url="http://192.168.178.100:5001")
    client = create_http_client(config)
    assert str(client.base_url) == "http://192.168.178.100:5001"


def test_create_http_client_applies_nfr5_timeout_policy():
    config = OctoBotConfig(base_url="http://192.168.178.100:5001")
    client = create_http_client(config)
    assert client.timeout.connect == REST_TIMEOUT_SECONDS
    assert client.timeout.read == REST_TIMEOUT_SECONDS
    assert REST_TIMEOUT_SECONDS == 10.0


# --- NFR-4: one error-classification helper, one test per failure class ----


@pytest.mark.anyio
async def test_request_json_classifies_timeout():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("connect timed out", request=request)

    client = _client_with_transport(httpx.MockTransport(handler))
    with pytest.raises(OctoBotTimeoutError):
        await request_json(client, "GET", "/api/portfolio")


@pytest.mark.anyio
async def test_request_json_classifies_connection_refused():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = _client_with_transport(httpx.MockTransport(handler))
    with pytest.raises(OctoBotConnectionError):
        await request_json(client, "GET", "/api/portfolio")


@pytest.mark.anyio
async def test_request_json_classifies_4xx():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    client = _client_with_transport(httpx.MockTransport(handler))
    with pytest.raises(OctoBotClientError) as exc_info:
        await request_json(client, "GET", "/api/portfolio")
    assert exc_info.value.status_code == 404


@pytest.mark.anyio
async def test_request_json_classifies_5xx():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    client = _client_with_transport(httpx.MockTransport(handler))
    with pytest.raises(OctoBotServerError) as exc_info:
        await request_json(client, "GET", "/api/portfolio")
    assert exc_info.value.status_code == 500


@pytest.mark.anyio
async def test_request_json_classifies_malformed_json():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json</html>")

    client = _client_with_transport(httpx.MockTransport(handler))
    with pytest.raises(OctoBotMalformedResponseError):
        await request_json(client, "GET", "/api/portfolio")


@pytest.mark.anyio
async def test_request_json_returns_decoded_json_on_success():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"selected": "profile-1"})

    client = _client_with_transport(httpx.MockTransport(handler))
    result = await request_json(client, "GET", "/api/portfolio")
    assert result == {"selected": "profile-1"}
