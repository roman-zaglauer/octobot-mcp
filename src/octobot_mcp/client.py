"""HTTP client construction and error-classified requests (NFR-4, NFR-5, NFR-10).

This module is the *only* place an `httpx2.AsyncClient` is constructed
(NFR-10) and the *only* place raw `httpx2` exceptions/response failures are
translated into `octobot_mcp.errors.OctoBotAPIError` subclasses (NFR-4).

This project imports `httpx2` directly rather than `httpx`: the installed
next-generation HTTP client library ships under the `httpx2` distribution
name (the `mcp` SDK itself imports it the same way -- see its
`mcp.shared._httpx_utils` module), so there is no legacy `httpx` package in
this environment to alias.

Milestone 3+ tool implementations must reach this client via a single
injected context object (NFR-11), not a module-level global -- see
`create_http_client`'s docstring.
"""

from __future__ import annotations

from typing import Any

import httpx2 as httpx

from octobot_mcp.config import OctoBotConfig
from octobot_mcp.errors import (
    OctoBotClientError,
    OctoBotConnectionError,
    OctoBotMalformedResponseError,
    OctoBotServerError,
    OctoBotTimeoutError,
)

#: NFR-5: default timeout for outbound REST calls to OctoBot. The Socket.IO
#: 5s connect timeout (also NFR-5) belongs to milestone 8, not this module.
REST_TIMEOUT_SECONDS = 10.0


def create_http_client(config: OctoBotConfig) -> httpx.AsyncClient:
    """Build the single `httpx2.AsyncClient` used for all OctoBot REST calls.

    Isolated behind this factory (NFR-10) so that adding session-cookie
    auth later (ADR-0005) only requires changing this function (e.g.
    attaching cookies/an `httpx2.Auth`), not every call site. Per
    ADR-0004, the caller (the server's lifespan/startup code, from
    milestone 3 onward) constructs exactly one client for the process
    lifetime and injects it into tools via a context object rather than a
    module-level global (NFR-11) -- this function itself holds no state.
    """
    return httpx.AsyncClient(
        base_url=config.base_url,
        timeout=httpx.Timeout(REST_TIMEOUT_SECONDS),
    )


async def request_json(
    client: httpx.AsyncClient, method: str, url: str, **kwargs: Any
) -> Any:
    """Perform one OctoBot HTTP request and return its decoded JSON body.

    The single error-classification helper (NFR-4): every outbound call
    site MUST go through this function instead of calling `client.request`
    directly, so timeout, connection-refused/unreachable, HTTP 4xx, HTTP
    5xx, and malformed/non-JSON responses are all mapped to exactly one
    `OctoBotAPIError` subclass each, and no raw `httpx2` exception or stack
    trace ever reaches the MCP client.

    Raises:
        OctoBotTimeoutError: the request did not complete within the
            configured timeout.
        OctoBotConnectionError: OctoBot could not be reached at all.
        OctoBotClientError: OctoBot responded with an HTTP 4xx status.
        OctoBotServerError: OctoBot responded with an HTTP 5xx status.
        OctoBotMalformedResponseError: OctoBot responded 2xx with a body
            that was not valid JSON.
    """
    try:
        response = await client.request(method, url, **kwargs)
    except httpx.TimeoutException as exc:
        raise OctoBotTimeoutError(
            f"Request to OctoBot timed out: {method} {url}"
        ) from exc
    except httpx.HTTPError as exc:
        # Everything else at the transport level (connection refused, DNS
        # failure, protocol errors, ...) is "OctoBot is unreachable" from
        # this server's point of view.
        raise OctoBotConnectionError(
            f"Could not reach OctoBot: {method} {url} ({exc})"
        ) from exc

    if response.status_code >= 500:
        raise OctoBotServerError(
            f"OctoBot returned server error {response.status_code} for {method} {url}",
            status_code=response.status_code,
        )
    if response.status_code >= 400:
        raise OctoBotClientError(
            f"OctoBot returned client error {response.status_code} for {method} {url}",
            status_code=response.status_code,
        )

    try:
        return response.json()
    except ValueError as exc:
        # `json.JSONDecodeError` (raised by `response.json()` on a
        # non-JSON body) is itself a `ValueError` subclass.
        raise OctoBotMalformedResponseError(
            f"OctoBot returned a non-JSON or malformed body for {method} {url}"
        ) from exc
