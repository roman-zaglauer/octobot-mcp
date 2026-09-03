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

Milestone 6 (docs/tasklist.md item 6, the confirm-gating helper and its
first two consumers) adds two more, both gated behind `confirm=true`
(ADR-0003) and only ever raised once the caller has already confirmed --
the refusal path itself (`confirm` not `true`) is a normal tool return, not
an error (see `octobot_mcp.tools._shared.require_confirmation`):
- `ProfileDeleteRejectedError` -- `delete_profile` failed (spec's
  `DELETE_REJECTED` code). Wraps OctoBot's own `(profile, err)` failure
  string from `POST /profiles_management/remove` (confirmed against source:
  `err` is either the literal string `"Can't remove the active profile"` or
  a `ProfileRemovalError`'s text), or -- since that route's own 200 success
  body is the same uninformative literal string `"Profile created"` copied
  from the unrelated `duplicate` action (confirmed against source) -- this
  tool re-checks via the same scrape `list_profiles`/`get_profile` use that
  the profile is actually gone afterward, and raises this instead of
  trusting the response status alone.
- `ProfileConversionFailedError` -- `convert_profile_to_live` did not
  actually select the requested profile after converting it. Not in the
  original spec's error-code table: `GET /profiles_management/use_as_live`
  (confirmed against source) always responds with an uninformative HTTP
  302 redirect to `/profile` on the one code path that doesn't raise, so
  this tool re-checks afterward (same scrape) that the profile is now
  selected, rather than trusting the response status. Deliberately does
  NOT also re-check the scrape's `profile_type` field: verified against
  source, that field reflects a completely different OctoBot concept
  (whether a real vs. simulated *trader* is enabled in the profile's own
  config) than the `profile.profile_type` attribute this action actually
  sets (`ProfileType.LIVE`, which has no rendered HTML representation to
  scrape at all) -- see
  `octobot_mcp.tools.profiles.convert_profile_to_live`'s docstring for the
  full, source-verified explanation of this terminology collision.

Milestone 7 (docs/tasklist.md item 7, job store scaffolding) adds one more,
for the same "never a raw stack trace reaches the MCP client" reason, even
though it wraps a purely in-memory lookup miss rather than any OctoBot
connectivity failure:
- `JobNotFoundError` -- `get_job_status`/`cancel_job` were given a `job_id`
  the in-memory job store has no record of. Per ADR-0002's risk table, this
  is an *expected* outcome after a server restart (the job store is
  process-lifetime only, never persisted), not a bug -- the message says so.
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


class ProfileDeleteRejectedError(OctoBotAPIError):
    """`delete_profile` failed (spec's `DELETE_REJECTED` code), milestone 6.

    Raised only after the caller has already confirmed (`confirm=true`) --
    see `octobot_mcp.tools._shared.require_confirmation` for the unconfirmed
    path, which is a normal return, not an exception. Wraps OctoBot's own
    `(profile, err)` failure string from `POST /profiles_management/remove`
    (confirmed against source `models/profiles.py::remove_profile`), or
    reports that the profile is still present after a post-hoc re-scrape
    despite a reported-success response -- see
    `octobot_mcp.tools.profiles.delete_profile`'s docstring for why that
    extra check exists.
    """


class ProfileConversionFailedError(OctoBotAPIError):
    """`convert_profile_to_live` did not actually convert/select the profile.

    Raised only after the caller has already confirmed (`confirm=true`) --
    see `octobot_mcp.tools._shared.require_confirmation` for the unconfirmed
    path, which is a normal return, not an exception. Not a status-code
    failure: `GET /profiles_management/use_as_live` (verified against
    source) always responds with an uninformative HTTP 302 redirect on its
    one non-raising code path, so this tool re-checks afterward via the
    same scrape `list_profiles`/`get_profile` use that the profile is now
    selected. It does NOT re-check `profile_type: "LIVE"` this way -- that
    field and the scrape's `profile_type` badge are two unrelated OctoBot
    concepts that merely share a name; see
    `octobot_mcp.tools.profiles.convert_profile_to_live`'s docstring for the
    full source-verified explanation.
    """


class JobNotFoundError(OctoBotAPIError):
    """No job matching the requested `job_id` was found in the in-memory job store.

    Distinct from every other error in this module: it never involves an
    OctoBot HTTP call at all -- `octobot_mcp.jobs.JobStore` is a purely
    in-memory, process-lifetime structure. Per ADR-0002's risk table, an
    unknown `job_id` is *expected* after a server restart (the store holds
    nothing across restarts), not a bug; the message raised alongside this
    exception says so explicitly. Raised by
    `octobot_mcp.tools.jobs.get_job_status`/`cancel_job`.
    """
