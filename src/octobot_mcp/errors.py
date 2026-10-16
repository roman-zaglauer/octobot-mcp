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

Milestone 8 (docs/tasklist.md item 8, the backtest watcher + `start_backtest`/
`get_job_result`) adds four more:
- `BacktestStartFailedError` -- `start_backtest` failed to submit a run to
  OctoBot (spec's `BACKTEST_START_FAILED` code), or failed local input
  validation before ever reaching OctoBot.
- `MissingExchangeIdError` -- `start_backtest(mode="current_bot_data")`'s
  `exchange_id` doesn't match a currently configured exchange (spec's
  `MISSING_EXCHANGE_ID` code), OctoBot's own confirmed named exception for
  this case.
- `JobNotCompletedError` -- `get_job_result` was called before a job
  reached `state == "completed"` (spec's `JOB_NOT_COMPLETED` code).

Milestone 8 also added a placeholder `CompareStrategiesResultUnsupportedError`
for `get_job_result` on a `compare_strategies` job, since nothing yet
created one; milestone 10 (below) replaces that placeholder with the real
`compare_strategies` implementation, so this class no longer exists.

Milestone 10 (docs/tasklist.md item 10, `compare_strategies`) adds one more:
- `CompareStrategiesStartFailedError` -- `compare_strategies` was given
  fewer than 2 `configs`, or one of its per-config entries failed the same
  mode/field validation `start_backtest` itself performs (an unknown
  `mode`, a missing required field for that mode, or an unrecognized field
  name) -- always raised synchronously, before any child or parent job is
  created, naming the offending `configs[<index>]` so the caller can tell
  which entry was invalid. Wraps the underlying `BacktestStartFailedError`
  (or, for an unexpected field name, the raw `TypeError` from the mode
  builder) rather than reusing that error type directly, so a caller can
  tell "one of my `compare_strategies` configs was invalid" apart from "my
  plain `start_backtest` call was invalid."

Milestone 12 (docs/tasklist.md item 12, the remaining confirm-gated
destructive tools) adds one more. `clear_orders_history`/
`clear_trades_history`/`clear_portfolio_history`/`clear_transactions_history`
have no dedicated exception class: all four map to a confirmed real JSON
route (`api/trading.py`, `[V]`) whose only failure modes are the generic
4xx/5xx classification `octobot_mcp.client.request_json` already handles,
so `OctoBotClientError`/`OctoBotServerError` are the right (and only)
exceptions these tools ever raise.
- `ExchangeCredentialsUpdateRejectedError` -- `update_exchange_credentials`
  failed, either because OctoBot itself rejected the write (`POST /config`'s
  `global_config` branch, confirmed against source
  `models/configuration.py::update_global_config`'s `(success, err_message)`
  return value, which the route maps 1:1 to HTTP 200/500 -- unlike several
  `profiles.py` routes, this one's status code IS trustworthy, no post-hoc
  re-check needed) or because this tool refused locally, before ever
  contacting OctoBot, because the given `exchange` name contains an
  underscore (see `octobot_mcp.tools.exchanges.update_exchange_credentials`'s
  docstring for why that specific case is refused). Every message this
  exception carries has already been passed through
  `octobot_mcp.tools._shared.redact_secrets` (INV-4/NFR-6) -- never the raw,
  unscrubbed OctoBot response text.

Milestone 13 (docs/tasklist.md item 13, tentacle config + logs) adds one
more:
- `TentacleConfigUpdateRejectedError` -- `update_tentacle_config` failed.
  Wraps OctoBot's own error text from `POST /config_tentacle` (confirmed
  against source `Services/Interfaces/web_interface/controllers/
  tentacles_config.py::config_tentacle()` and reproduced live against the
  OctoBot 2.1.1 test instance): its one failure branch responds HTTP 500
  with a plain-text body (e.g. `"Can't find <tentacle> class"`) despite a
  `Content-Type: application/json` header that lies about the body's
  actual encoding -- the same pattern `profiles.py`/`exchanges.py` already
  handle via their own `_extract_error_text` helpers. `get_tentacle_config`/
  `export_logs` have no dedicated exception classes of their own: both are
  reads whose failures are already well served by the generic
  `OctoBotClientError`/`OctoBotServerError` (with OctoBot's own extracted
  error text folded into the message, the same idiom
  `octobot_mcp.tools.profiles.export_profile` already uses) -- matching
  this project's established convention that only *mutations* needing a
  domain-specific outcome get their own error class.

Milestone 13.5 (docs/tasklist.md item 13.5, closing the `update_trading_config`
backlog gap) adds one more:
- `TradingConfigUpdateRejectedError` -- `update_trading_config` failed.
  Wraps OctoBot's own error text from `POST /config`'s `trading_config`
  branch (confirmed against source `Services/Interfaces/web_interface/
  controllers/configuration.py::config()`): unlike `update_exchange_credentials`'s
  `global_config` branch of this SAME route, the `trading_config` branch's
  failures never populate the route's `err_message` local variable (it's
  only ever reassigned by the `global_config`/`removed_elements` branches),
  so this tool's own extracted error text is always an empty string in
  practice -- see `octobot_mcp.tools.trading_config`'s module docstring for
  the full derivation. Not confirm-gated (ADR-0003): mutates strategy/
  tentacle activation config, does not destroy data, expose credentials, or
  enable live trading.

Milestone 13.6 (docs/tasklist.md item 13.6, closing the `cancel_order`/
`close_position`/`refresh_portfolio` backlog gap) adds three more. All three
map to real routes in the same `api/trading.py` this module's other errors
already document, confirmed against source
(`packages/tentacles/Services/Interfaces/web_interface/api/trading.py` and
`packages/services/octobot_services/interfaces/util/{order,position,portfolio}.py`,
`Drakkar-Software/OctoBot`), and all three share one trait that makes a
dedicated `_extract_error_text` worthwhile: OctoBot's own failure body for
each is built via `util.get_rest_reply(<plain string>, 500)` -- a real,
trustworthy HTTP 500, but `get_rest_reply` sends that string as a raw,
unquoted response body while still forcing a `Content-Type:
application/json` header, so `response.json()` fails and the message must be
read from `response.text` instead -- the same lying-content-type pattern
`tentacles.py`/`exchanges.py`/`trading_config.py` already document. None are
confirm-gated (ADR-0003): the user was explicitly asked about the spec's own
footnote flagging `cancel_order`/`close_position` as "revisitable" given
their irreversible effect, and chose to keep both ungated, per ADR-0003's
original reasoning that these are routine, expected trading-bot actions.
- `OrderCancelRejectedError` -- `cancel_order` failed. OctoBot's failure text
  is always the literal `"Impossible to cancel order: order not found."`
  (confirmed against source: `api/trading.py`'s `cancel_order` action
  branches on `interfaces_util.cancel_orders([order_id])`'s truthiness,
  which is `0` whenever no connected exchange's `cancel_order_with_id` call
  actually removed that order -- including when every exchange manager
  tried raised `OrderCancelError`/`UnexpectedExchangeSideOrderStateError`
  internally, which OctoBot itself silently swallows rather than
  propagating).
- `PositionCloseRejectedError` -- `close_position` failed. OctoBot's failure
  text is always the literal `"Impossible to close position: position
  already closed."` (confirmed against source: the same truthiness pattern,
  via `interfaces_util.close_positions([{"symbol": ..., "side": ...}])`).
- `PortfolioRefreshFailedError` -- `refresh_portfolio` failed. OctoBot's
  failure text is always the literal `"No portfolio to refresh"` (confirmed
  against source: `async_trigger_portfolios_refresh` raises a `RuntimeError`
  whenever it finds zero enabled *real* (non-simulated) traders across every
  connected exchange -- an entirely expected outcome on any OctoBot instance
  running only simulated trading, not a bug).

Milestone 15 (docs/tasklist.md item 15, ADR-0007/ADR-0008, the historical-
data-collection addendum) adds four more. Defined here, before
`octobot_mcp.tools.data_collection` (the one module that raises all four),
per this project's own established ordering convention (a reviewer-caught
bug in an earlier draft of this exact milestone reintroduced the opposite
ordering; see that module's docstring):
- `DataCollectionStartFailedError` -- `start_data_collection` failed to
  submit a run to OctoBot (confirmed against source and this milestone's
  own live verification session, `POST /data_collector?action_type=
  start_collector`: its only non-raising failure path returns `(False,
  <message>)`, surfaced by the controller as an HTTP 500 whose body is that
  plain-text message -- e.g. "Backtesting is disabled.", "Please select an
  exchange.", "Please select a trading pair.", `_ensure_backtesting_limits`'s
  own message (exact text still unconfirmed -- see
  `octobot_mcp.tools.data_collection`'s module docstring), or the verbatim
  `"Can't collect data for {symbols} on {exchange} (Historical data
  collector is already running)"` one-at-a-time guard, reproduced live this
  session). Mirrors `BacktestStartFailedError`'s role for `start_backtest`.
- `DataFileScrapeContractBrokenError` -- the `GET /data_collector` Tier-B
  scrape adapter's structural assumptions about its rendered
  `tr.selectable_datafile` rows didn't hold (ADR-0008, NFR-13). Named in
  parallel to, and never conflated with, `ProfileScrapeContractBrokenError`
  above -- this project's established one-domain-specific-exception-class-
  per-adapter convention, not a shared generic one.
- `DataFileDeleteRejectedError` -- `delete_data_file` failed (confirm-gated,
  ADR-0003/FR-25). Raised only after the caller has already confirmed
  (`confirm=true`) -- see `octobot_mcp.tools._shared.require_confirmation`
  for the unconfirmed path, which is a normal return, not an exception.
  Wraps OctoBot's own `f"Can't delete {file_name} ({error})"` text,
  reproduced verbatim against the live instance this session (e.g.
  `"Can't delete does_not_exist.data (file can't be found)")`).
- `DataFileImportFailedError` -- `import_data_file` failed. This session's
  live verification found the SAME invisible-at-the-HTTP-level outcome
  `octobot_mcp.tools.profiles._create_profile_import_file` already
  documents for `create_profile(mode="import_file")`: `POST
  /data_collector?action_type=import_data_file` always responds HTTP 200
  with the full re-rendered `/data_collector` page, on both a genuinely
  saved file and a rejected one (confirmed live with both garbage bytes and
  a syntactically valid but semantically-wrong SQLite file, neither of
  which OctoBot's own `models.save_data_file` accepted, and neither of
  which produced any inspectable error text in the response body or a
  session cookie) -- so this tool determines the outcome the same
  before/after `list_data_files()` diff `_create_profile_import_file` uses,
  not by parsing a response message (the spec's assumption of a readable
  OctoBot success/failure message for this route did not hold up against
  the live instance).

Task 16 (docs/tasklist.md item 16, the `update_evaluator_config` addendum,
FR-28) adds one more:
- `EvaluatorConfigUpdateRejectedError` -- `update_evaluator_config` failed.
  Same `POST /config` route and `err_message`-stays-empty finding as
  `TradingConfigUpdateRejectedError` above (confirmed against source this
  session: the `evaluator_config` branch of `Services/Interfaces/
  web_interface/controllers/configuration.py::config()` only ever touches
  the `success` boolean, never `err_message`, exactly like the
  `trading_config`/`tentacle_config` branches). A dedicated class rather
  than reusing `TradingConfigUpdateRejectedError` directly, matching this
  project's established one-class-per-write-tool convention, so a caller can
  tell which tool's write was rejected. Not confirm-gated (ADR-0003):
  mutates which evaluators are active, does not destroy data, expose
  credentials, or enable live trading.

Task 18 (docs/tasklist.md item 18, ADR-0010, FR-30/FR-31) adds one more.
Defined here, before `octobot_mcp.tools.instance_lifecycle` (the one module
that raises it), per this project's established ordering convention:
- `RestartFailedError` -- `restart_octobot` failed to actually schedule a
  restart. Per the tool spec's "Instance lifecycle" section (the
  authoritative contract this tool implements against, not ADR-0010's prose
  directly), this is raised for ANY outcome of the one live `GET
  /wait_reboot?reboot=true` call other than a clean HTTP 200 -- a non-200
  response, a connection-level failure, or a timeout -- never the generic
  `OctoBotClientError`/`OctoBotServerError`/`OctoBotConnectionError`/
  `OctoBotTimeoutError` classification `request_json` would otherwise apply,
  since this route's own success/failure semantics (a 200 means "OctoBot
  rendered the wait page and scheduled the kill," confirmed against
  `Drakkar-Software/OctoBot`'s `reboot.py` source, ADR-0010 Context) don't
  map onto that generic classification the same way a JSON API route's
  would. Never raised for the `confirm`/active-job refusals -- those are
  structured non-exception returns (`require_confirmation`/ADR-0003, and a
  plain dict for the job-store check respectively), the same convention as
  every other gated tool.
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


class BacktestStartFailedError(OctoBotAPIError):
    """`start_backtest` failed to start a run (spec's `BACKTEST_START_FAILED` code), milestone 8.

    Wraps OctoBot's own error text from a failed `POST /backtesting` start
    action (confirmed against source `models/backtesting.py::
    _start_backtesting`: its only non-raising failure path returns
    `(False, <message>)`, surfaced by the controller as an HTTP 500 whose
    body is that plain-text message -- see
    `octobot_mcp.tools.backtesting`'s module docstring), or a local
    input-validation failure (an unknown `mode`, or `data_files` mode's
    required `files` list omitted) caught before any OctoBot call is made
    at all.
    """


class MissingExchangeIdError(OctoBotAPIError):
    """`start_backtest(mode="current_bot_data")` was given an unrecognized `exchange_id`.

    Distinct from the generic `BacktestStartFailedError`: confirmed against
    source (`Services/Interfaces/web_interface/errors.py::MissingExchangeId`,
    `models/trading.py::ensure_valid_exchange_id`, which raises it whenever
    `octobot_trading.api.get_exchange_manager_from_exchange_id(exchange_id)`
    raises a `KeyError`) as OctoBot's own named exception for exactly this
    case. The controller surfaces it as an HTTP 500 whose body is the
    literal string `MissingExchangeId.EXPLANATION` -- this tool matches
    that exact text to raise this instead of the generic error, so an
    agent can tell "bad exchange_id" apart from any other start failure.
    See `octobot_mcp.tools.backtesting.start_backtest`'s docstring for how
    `exchange_id` values are actually obtained (`get_exchange_details`'s
    `exchange_id` field).
    """


class JobNotCompletedError(OctoBotAPIError):
    """`get_job_result` was called on a job that has not reached `state == "completed"` (spec's `JOB_NOT_COMPLETED` code).

    Names the job's current state in the message. Never returns partial
    results, even if OctoBot itself would technically serve a report for
    an in-progress or already-failed/cancelled run.
    """


class CompareStrategiesStartFailedError(OctoBotAPIError):
    """`compare_strategies` was given an invalid `configs` list (milestone 10).

    Raised synchronously, before any job (parent or child) is created --
    same philosophy as `BacktestStartFailedError`'s own local-validation
    branch. Covers `len(configs) < 2` and any per-config entry that fails
    `start_backtest`'s own mode/field validation (wrapping the resulting
    `BacktestStartFailedError`/`TypeError`), with the offending
    `configs[<index>]` named in the message. See
    `octobot_mcp.tools.backtesting.compare_strategies`'s docstring.
    """


class ExchangeCredentialsUpdateRejectedError(OctoBotAPIError):
    """`update_exchange_credentials` failed (milestone 12, ADR-0003, confirm-gated).

    Raised only after the caller has already confirmed (`confirm=true`) --
    see `octobot_mcp.tools._shared.require_confirmation` for the unconfirmed
    path, which is a normal return, not an exception. Two distinct causes:
    - A local refusal, before any OctoBot call is made, because `exchange`
      contains an underscore -- see
      `octobot_mcp.tools.exchanges.update_exchange_credentials`'s docstring
      for the source-confirmed reason this specific case is refused.
    - OctoBot itself rejected the write: `POST /config`'s `global_config`
      branch (confirmed against source `models/configuration.py::
      update_global_config`) returns a real `(success, err_message)` pair
      the route maps 1:1 to HTTP 200/500 -- this tool trusts that status
      code, unlike several `profiles.py` routes that needed a post-hoc
      re-check.
    Every message this exception carries has already been passed through
    `octobot_mcp.tools._shared.redact_secrets` (INV-4/NFR-6): the literal
    `api_key`/`api_secret`/`api_password` values submitted in the same call
    are scrubbed before being included here, since OctoBot's own responses
    for this route are confirmed (see that tool's docstring) to sometimes
    echo submitted config values back verbatim.
    """


class TentacleConfigUpdateRejectedError(OctoBotAPIError):
    """`update_tentacle_config` failed (milestone 13).

    Wraps OctoBot's own error text from `POST /config_tentacle`'s
    `action=update` branch (confirmed against source
    `Services/Interfaces/web_interface/controllers/tentacles_config.py::
    config_tentacle()` and reproduced live against the OctoBot 2.1.1 test
    instance: an unrecognized `tentacle` name returns HTTP 500 with the
    plain-text body `"Can't find <tentacle> class"`). Not confirm-gated
    (ADR-0003): mutates tentacle configuration, does not destroy data,
    expose credentials, or enable live trading. See
    `octobot_mcp.tools.tentacles.update_tentacle_config`'s docstring for the
    full verified request/response shape and the spec correction it
    documents (the tentacle name is a query parameter, not a JSON body
    field).
    """


class TradingConfigUpdateRejectedError(OctoBotAPIError):
    """`update_trading_config` failed (milestone 13.5).

    Wraps OctoBot's own error text from `POST /config`'s `trading_config`
    branch (confirmed against source `Services/Interfaces/web_interface/
    controllers/configuration.py::config()`). Not confirm-gated (ADR-0003):
    mutates strategy/tentacle activation config, does not destroy data,
    expose credentials, or enable live trading. See
    `octobot_mcp.tools.trading_config.update_trading_config`'s docstring for
    the full verified request/response shape, including the genuine spec
    correction it documents: the extracted error text is always an empty
    string in practice for this tool's own failures, never a substantive
    message, because the route's `err_message` variable is only ever
    populated by its `global_config`/`removed_elements` branches, which this
    tool never triggers.
    """


class OrderCancelRejectedError(OctoBotAPIError):
    """`cancel_order` failed (milestone 13.6). Not confirm-gated (ADR-0003).

    Wraps OctoBot's own error text from `POST /api/orders?action=cancel_order`
    (confirmed against source `Services/Interfaces/web_interface/
    api/trading.py::orders()`): a real, trustworthy HTTP 500 whose body is
    always the literal plain-text string `"Impossible to cancel order: order
    not found."`, despite a `Content-Type: application/json` header that
    lies about the body's actual encoding. See
    `octobot_mcp.tools.portfolio.cancel_order`'s docstring for the full
    verified request/response shape.
    """


class PositionCloseRejectedError(OctoBotAPIError):
    """`close_position` failed (milestone 13.6). Not confirm-gated (ADR-0003).

    Wraps OctoBot's own error text from
    `POST /api/positions?action=close_position` (confirmed against source
    `Services/Interfaces/web_interface/api/trading.py::positions()`): a real,
    trustworthy HTTP 500 whose body is always the literal plain-text string
    `"Impossible to close position: position already closed."`, despite a
    `Content-Type: application/json` header that lies about the body's
    actual encoding. See `octobot_mcp.tools.portfolio.close_position`'s
    docstring for the full verified request/response shape, including the
    genuine spec correction it documents: OctoBot positions have no `id`
    concept at all, so this tool's parameters are `symbol`/`side`, not the
    spec's `position_id`.
    """


class PortfolioRefreshFailedError(OctoBotAPIError):
    """`refresh_portfolio` failed (milestone 13.6). Not confirm-gated (ADR-0003).

    Wraps OctoBot's own error text from `POST /api/refresh_portfolio`
    (confirmed against source `Services/Interfaces/web_interface/
    api/trading.py::refresh_portfolio()`): a real, trustworthy HTTP 500 whose
    body is always the literal plain-text string `"No portfolio to
    refresh"`, despite a `Content-Type: application/json` header that lies
    about the body's actual encoding. Raised whenever OctoBot finds zero
    enabled *real* (non-simulated) traders across every connected exchange
    (confirmed against source `octobot_services.interfaces.util.portfolio::
    async_trigger_portfolios_refresh`) -- an entirely expected outcome on any
    instance running only simulated trading, not a bug.
    """


class DataCollectionStartFailedError(OctoBotAPIError):
    """`start_data_collection` failed to start a run (milestone 15, ADR-0007).

    Wraps OctoBot's own error text from a failed `POST /data_collector
    ?action_type=start_collector` (confirmed against source and this
    milestone's own live verification session: its only non-raising failure
    path returns `(False, <message>)`, surfaced by the controller as an HTTP
    500 whose body is that plain-text message), including OctoBot's own
    one-at-a-time guard text (`"Can't collect data for {symbols} on
    {exchange} (Historical data collector is already running)"`, reproduced
    verbatim live this session). See
    `octobot_mcp.tools.data_collection.start_data_collection`'s docstring
    for the full verified request/response shape.
    """


class DataFileScrapeContractBrokenError(OctoBotAPIError):
    """The `GET /data_collector` HTML page's structure didn't match this
    server's second Tier-B scrape adapter's expectations (ADR-0008, NFR-13).

    Raised the moment any structural assumption is violated -- e.g. zero
    `tr.selectable_datafile` rows' parent table found, a row missing an
    expected `<td>`, or the delete button's `data-file` attribute absent --
    so a scrape-contract drift surfaces as a loud, distinct, actionable
    failure instead of a silent empty/partial file list indistinguishable
    from "no data files exist." Named in parallel to, and never conflated
    with, `ProfileScrapeContractBrokenError` above -- one domain-specific
    exception class per adapter, this project's established convention. See
    `octobot_mcp.tools.data_collection.list_data_files` for the exact
    assumptions this guards.
    """


class DataFileDeleteRejectedError(OctoBotAPIError):
    """`delete_data_file` failed (milestone 15, ADR-0007/ADR-0008, FR-25).

    Raised only after the caller has already confirmed (`confirm=true`) --
    see `octobot_mcp.tools._shared.require_confirmation` for the unconfirmed
    path, which is a normal return, not an exception. Wraps OctoBot's own
    `f"Can't delete {file_name} ({error})"` text from `POST /data_collector
    ?action_type=delete_data_file` (confirmed against source and reproduced
    verbatim against the live instance this session, e.g. `"Can't delete
    does_not_exist.data (file can't be found)"`). See
    `octobot_mcp.tools.data_collection.delete_data_file`'s docstring for the
    full verified request/response shape, including why `delete_data_file`
    is confirm-gated (a reversal from this addendum's original draft).
    """


class DataFileImportFailedError(OctoBotAPIError):
    """`import_data_file` failed (milestone 15, ADR-0007/ADR-0008).

    This tool's outcome is determined by a before/after `list_data_files()`
    diff, not by parsing an OctoBot response message: this session's live
    verification found `POST /data_collector?action_type=import_data_file`
    always responds HTTP 200 with the full re-rendered `/data_collector`
    page, on both a genuinely saved file and a rejected one, with no
    inspectable error text anywhere in the response -- the same
    invisible-at-the-HTTP-level outcome
    `octobot_mcp.tools.profiles._create_profile_import_file` already
    documents for `create_profile(mode="import_file")`. Raised when the
    uploaded `filename` cannot be found in `list_data_files()`'s output
    after the upload. See
    `octobot_mcp.tools.data_collection.import_data_file`'s docstring for the
    full detail.
    """


class EvaluatorConfigUpdateRejectedError(OctoBotAPIError):
    """`update_evaluator_config` failed (task 16, `docs/tasklist.md` item 16, FR-28).

    Wraps OctoBot's own error text from `POST /config`'s `evaluator_config`
    branch (confirmed against source `Services/Interfaces/web_interface/
    controllers/configuration.py::config()`): like the `trading_config`
    branch `TradingConfigUpdateRejectedError` documents, this branch only
    ever touches the route's `success` boolean, never its `err_message`
    local variable -- so the extracted error text is always an empty string
    in practice, never a substantive message. Not confirm-gated (ADR-0003):
    mutates which evaluators are active, does not destroy data, expose
    credentials, or enable live trading. See
    `octobot_mcp.tools.trading_config.update_evaluator_config`'s docstring
    for the full verified request/response shape.
    """


class RestartFailedError(OctoBotAPIError):
    """`restart_octobot` failed to actually schedule a restart (task 18, ADR-0010).

    Raised only once the caller has already confirmed (`confirm=true`) and
    no active `backtest`/`data_collection` job blocked the call -- the
    `confirm`/active-job refusals are both structured non-exception returns
    (`octobot_mcp.tools._shared.require_confirmation` for the former, a
    plain dict naming the job(s) for the latter), never this exception. Per
    the tool spec's "Instance lifecycle" section, ANY outcome of the one
    live `GET /wait_reboot?reboot=true` call other than a clean HTTP 200 is
    classified as this one error -- a non-200 response, a connection-level
    failure, or a timeout -- rather than the generic
    `OctoBotClientError`/`OctoBotServerError`/`OctoBotConnectionError`/
    `OctoBotTimeoutError` classification `octobot_mcp.client.request_json`
    would otherwise apply, so a caller has one unambiguous "the restart
    itself was never triggered" signal instead of four different generic
    connectivity exceptions to reason about for this one route. See
    `octobot_mcp.tools.instance_lifecycle.restart_octobot`'s docstring for
    the full verified request/response shape.
    """


class EvaluatorScrapeContractBrokenError(OctoBotAPIError):
    """The `GET /advanced/evaluator_config` HTML page's structure didn't
    match this server's third Tier-B scrape adapter's expectations
    (ADR-0009, reversed to GO by explicit user decision; NFR-18).

    Raised the moment the page-wide structural assumption is violated --
    zero `a.config-element` rows found across ALL FOUR category containers
    (`ta-`/`social-`/`rt-`/`scripted-evaluator-config-root`) combined, or
    one of those four container `id`s missing from the page entirely -- so
    a scrape-contract drift surfaces as a loud, distinct, actionable
    failure instead of a silent empty/partial evaluator list indistinguishable
    from "no evaluators exist." Named in parallel to, and never conflated
    with, `ProfileScrapeContractBrokenError`/`DataFileScrapeContractBrokenError`
    above -- one domain-specific exception class per adapter, this project's
    established convention. **Deliberately NOT raised for a single category
    container legitimately having zero rows** -- confirmed live on this
    project's own test instance ("Scripted evaluators" renders with zero
    installed evaluators inside an otherwise-intact container) -- unlike
    `ProfileScrapeContractBrokenError`'s "zero is always broken" gate, this
    adapter's fail-loud scope is the whole page, not any single category.
    See `octobot_mcp.tools.trading_config.list_evaluators` for the exact
    assumptions this guards.
    """


class PortfolioScrapeContractBrokenError(OctoBotAPIError):
    """The `GET /portfolio` HTML page's structure didn't match this
    server's fourth Tier-B scrape adapter's expectations (ADR-0011, NFR-19).

    Raised the moment any structural assumption is violated -- `#holdings-
    table`/`#portfolio-display` missing, a row missing its `.symbol` span,
    or `#portfoliosCard`'s `reference_market` attribute missing -- so a
    scrape-contract drift surfaces as a loud, distinct, actionable failure.
    **Deliberately NOT raised for zero `tr.symbol-holding` rows** -- a
    legitimately empty portfolio (zero holdings anywhere) is a real OctoBot
    state, unlike `ProfileScrapeContractBrokenError`'s "OctoBot always ships
    built-in profiles" invariant. Named in parallel to, and never conflated
    with, every other `*ScrapeContractBrokenError` above -- this project's
    established one-domain-specific-exception-class-per-adapter convention.
    See `octobot_mcp.tools.portfolio.get_portfolio_snapshot` for the exact
    assumptions this guards.
    """


class EvaluationMatrixScrapeContractBrokenError(OctoBotAPIError):
    """The `GET /advanced/matrix` HTML page's structure didn't match this
    server's fifth Tier-B scrape adapter's expectations (ADR-0011, NFR-20).

    Raised the moment the page-wide structural assumption is violated --
    `#matrixDataTable` missing, its `<thead>` column-name text not matching
    `["Evaluator", "Value", "Time frame", "Symbol", "Exchange"]` exactly and
    in order, or any body row having a cell count other than 5. **Deliberately
    NOT raised for zero entries** -- a freshly started OctoBot, or one with
    no active evaluators, legitimately shows nothing yet; a materially
    weaker "what's legitimately empty" invariant than every prior Tier-B
    adapter's, since there is no "OctoBot always ships at least one"
    guarantee here (ADR-0011 Decision 3). Named in parallel to, and never
    conflated with, every other `*ScrapeContractBrokenError` above -- this
    project's established one-domain-specific-exception-class-per-adapter
    convention. See `octobot_mcp.tools.trading_config.get_evaluation_matrix`
    for the exact assumptions this guards.
    """
