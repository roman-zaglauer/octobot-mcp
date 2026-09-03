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

Milestone 4 (docs/adr/0006-read-model-acquisition-strategy.md) adds two more
subclasses for the one Tier-B HTML-scrape adapter
(`octobot_mcp.tools.profiles`). Neither is a connectivity failure in the
above sense, but both still need the same "never a raw stack trace reaches
the MCP client" treatment (NFR-4), so they share the same
`OctoBotAPIError` base that `octobot_mcp.tools._shared.with_error_handling`
catches:
- `ProfileScrapeContractBrokenError` -- the scrape's structural assumptions
  about `/profiles_selector`'s rendered HTML didn't hold (NFR-12, INV-5).
- `ProfileNotFoundError` -- the scrape succeeded but no profile matched the
  requested `profile_id` (a normal "not found," distinct from a broken
  scrape).
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


class ProfileScrapeContractBrokenError(OctoBotAPIError):
    """The `/profiles_selector` HTML page's structure didn't match this
    server's Tier-B scrape adapter's expectations (ADR-0006, NFR-12, INV-5).

    Raised the moment any structural assumption is violated -- e.g. zero
    profile cards found, a card missing its `data-id`, or a count of
    selected-profile cards other than exactly one -- so a scrape-contract
    drift (most likely an OctoBot UI version upgrade) surfaces as a loud,
    distinct, actionable failure instead of a silent empty/partial profile
    list that would look indistinguishable from "no profiles exist." See
    `octobot_mcp.tools.profiles` for the exact assumptions this guards and
    the last-confirmed OctoBot version.
    """


class ProfileNotFoundError(OctoBotAPIError):
    """No profile matching the requested `profile_id` was found.

    Distinct from `ProfileScrapeContractBrokenError`: the underlying scrape
    itself succeeded and returned a well-formed profile list, but the
    requested id simply isn't among the profiles that currently exist on
    the OctoBot instance. Raised by `octobot_mcp.tools.profiles.get_profile`.
    """
