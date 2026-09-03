"""Structured error taxonomy for OctoBot connectivity failures (NFR-4).

Every outbound call to the OctoBot HTTP API goes through
`octobot_mcp.client.request_json`, the single classification point that
turns raw `httpx2` exceptions and non-2xx/malformed responses into one of
the `OctoBotAPIError` subclasses below. Tool implementations (milestone 3+)
SHALL catch `OctoBotAPIError` (or a specific subclass) rather than
`httpx2` exceptions directly, so a raw stack trace never reaches the MCP
client.

Failure classes (one subclass each, per NFR-4's verification target):
- `OctoBotTimeoutError`     -- the request timed out (connect/read/write/pool).
- `OctoBotConnectionError`  -- connection refused or otherwise unreachable.
- `OctoBotClientError`      -- OctoBot responded with an HTTP 4xx status.
- `OctoBotServerError`      -- OctoBot responded with an HTTP 5xx status.
- `OctoBotMalformedResponseError` -- a 2xx response whose body was not the
  expected JSON (e.g. non-JSON body, or JSON of an unexpected shape).
"""

from __future__ import annotations


class OctoBotAPIError(Exception):
    """Base class for all classified OctoBot connectivity failures.

    Catch this to handle any OctoBot connectivity failure generically;
    catch a specific subclass to handle one failure class.
    """


class OctoBotTimeoutError(OctoBotAPIError):
    """The request to OctoBot did not complete within the configured timeout."""


class OctoBotConnectionError(OctoBotAPIError):
    """OctoBot could not be reached (connection refused, DNS failure, etc.)."""


class OctoBotHTTPStatusError(OctoBotAPIError):
    """Base class for classified non-2xx HTTP responses from OctoBot.

    Carries the response `status_code` so callers can log/inspect it
    without re-parsing a message string.
    """

    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class OctoBotClientError(OctoBotHTTPStatusError):
    """OctoBot responded with an HTTP 4xx status (e.g. bad request, not found)."""


class OctoBotServerError(OctoBotHTTPStatusError):
    """OctoBot responded with an HTTP 5xx status (server-side failure)."""


class OctoBotMalformedResponseError(OctoBotAPIError):
    """OctoBot returned a 2xx response whose body was not valid/expected JSON."""
