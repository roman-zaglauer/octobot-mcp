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

Milestone 5 (docs/tasklist.md item 5, profile write tools) adds three more,
for the same reason: none are raw connectivity failures, but each wraps a
domain-specific failure mode of one write tool in `octobot_mcp.tools.profiles`
that this project's verification against OctoBot source (see that module's
docstrings) found is NOT reliably signalled by HTTP status alone:
- `ProfileImportFailedError` -- `create_profile` (any of its 4 modes)
  failed. Wraps OctoBot's own error text where available (`duplicate`,
  `import_file`, `import_url`, `import_cloud_strategy` all classified into
  this one error per the spec's `IMPORT_FAILED` code), including the case
  where OctoBot reported success but this tool could not reliably identify
  which profile it created.
- `ProfileUpdateRejectedError` -- `update_profile` failed; wraps OctoBot's
  own `(success, err)` failure string (spec's `UPDATE_REJECTED` code).
- `ProfileSelectionFailedError` -- `select_profile` did not actually select
  the requested profile. Not in the original spec's error-code table:
  added because `GET /profile?select=<id>` (verified against source) always
  returns HTTP 200 and silently keeps the previous profile selected on an
  unknown id, so this tool must positively re-check the selection
  afterward rather than trust the response status -- see
  `octobot_mcp.tools.profiles.select_profile`'s docstring.
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


class ProfileImportFailedError(OctoBotAPIError):
    """`create_profile` failed, in any of its 4 modes (milestone 5).

    Wraps OctoBot's own error text where the underlying
    `/profiles_management/duplicate`/`import`/`download` action provided
    one, or explains why this tool couldn't determine the outcome (e.g. a
    diff-based new-profile lookup found zero or more than one candidate)
    when it didn't. See `octobot_mcp.tools.profiles.create_profile`'s
    docstring for the per-mode verified request/response shapes and the
    corrections made to this tool's spec entry.
    """


class ProfileUpdateRejectedError(OctoBotAPIError):
    """`update_profile` failed (spec's `UPDATE_REJECTED` code).

    Wraps the `err` string from OctoBot's `update_profile()` model
    function's `(success, err)` return value, or another failure OctoBot's
    global error handler surfaced as JSON text. See
    `octobot_mcp.tools.profiles.update_profile`'s docstring for the
    verified request body shape (including the spec correction for
    `risk`/`complexity`'s actual expected type).
    """


class ProfileSelectionFailedError(OctoBotAPIError):
    """`select_profile` did not actually select the requested profile.

    Not a status-code failure: `GET /profile?select=<id>` (verified against
    OctoBot source) always responds HTTP 200 and silently keeps whatever
    profile was already selected if `profile_id` doesn't exist, so this
    tool re-checks the selection afterward via the same scrape
    `list_profiles`/`get_profile` use and raises this instead of trusting
    the response status. See
    `octobot_mcp.tools.profiles.select_profile`'s docstring.
    """
