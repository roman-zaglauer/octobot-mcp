"""MCP server instance and entry point.

Per ADR-0001, this server speaks MCP over the stdio transport only: the
MCP client (e.g. Claude Code) launches this process and communicates over
its stdin/stdout. There is no network listener and no MCP-level
authentication.

The single `httpx2.AsyncClient` every tool uses is built once, at startup,
by `octobot_mcp.context.app_lifespan` and threaded into every tool call via
the MCP SDK's lifespan mechanism (NFR-11) -- never a module-level global.
`INSTRUCTIONS` below is this server's `instructions` (MCP's `initialize`-time
field for orienting a connecting agent before it starts reading individual
tool descriptions) -- kept here, not inline in the `MCPServer(...)` call, so
it reads like the rest of this project's other prose-heavy module docstrings.
"""

import logging

from mcp.server import MCPServer

from octobot_mcp.context import app_lifespan
from octobot_mcp.logging_config import configure_logging
from octobot_mcp.tools import register_all

INSTRUCTIONS = """\
This server manages ONE OctoBot trading-bot instance. Its base URL is fixed \
for the whole process (the `OCTOBOT_BASE_URL` environment variable set when \
this server was launched) -- no tool takes an instance/base URL parameter. \
There is no login/auth at this MCP layer or against OctoBot itself (v1 only \
supports instances with OctoBot's own login disabled).

This server's OWN structured logs (one line per tool call, one per job state \
transition) are written to a local rotating log file, not exposed through any \
tool -- it already lives on the same filesystem as whatever process launched \
this stdio subprocess. Default path: `~/.octobot-mcp/server.log` (5MB x 3 \
rotated backups), overridable via `OCTOBOT_MCP_LOG_FILE`/`OCTOBOT_MCP_LOG_LEVEL`/ \
`OCTOBOT_MCP_LOG_MAX_BYTES`/`OCTOBOT_MCP_LOG_BACKUP_COUNT`.

## Tool groups

- Profiles: `list_profiles`/`get_profile` discover ids and names; \
`create_profile`/`update_profile`/`select_profile`/`export_profile` manage \
them; `delete_profile`/`convert_profile_to_live` are destructive/high-risk \
and confirm-gated (see Safety).
- Backtesting & strategy comparison (asynchronous -- see next section): \
`start_backtest`, `compare_strategies`, `get_job_status`, `list_jobs`, \
`cancel_job`, `get_job_result`.
- Historical data collection (asynchronous, same job pattern as \
backtesting -- see next section): `start_data_collection` downloads real \
exchange history so `start_backtest(mode="data_files")` has something to \
run against; `list_data_files` discovers what's already downloaded (a \
started collection never reports its own resulting filename -- always \
call `list_data_files` afterward to find it); `delete_data_file` (confirm-\
gated) removes one; `import_data_file` uploads a file obtained out of \
band; `get_available_timeframes_for_collection` lists valid timeframes \
for an exchange (for valid symbols, reuse the existing `get_all_symbols`).
- Portfolio & trading: `get_orders`/`get_positions`/`get_trades`/ \
`get_pnl_history`/`get_historical_portfolio_value` are reads; \
`cancel_order`/`close_position`/`refresh_portfolio` are routine, ungated \
trading actions; the `clear_*_history` tools are irreversible and \
confirm-gated.
- Exchanges: currency/symbol lookups and `check_accounts_compatible` are \
reads; `get_exchange_details` returns only ONE exchange's details, never a \
full list; `update_exchange_credentials` writes real API credentials and is \
confirm-gated.
- Tentacle & trading config: `get_tentacle_config`/`update_tentacle_config`/ \
`update_trading_config`/`update_evaluator_config` read/write configuration; \
`list_evaluators` enumerates every evaluator OctoBot knows about (name, \
activation state, category) by scraping OctoBot's own evaluator-config page \
-- its `name` field is exactly the class-name string `update_evaluator_config`'s \
`patch` keys must equal, closing that tool's own companion-read gap; \
`export_logs` exports a \
real log archive. `list_tentacles`/`get_logs` are honest stubs -- OctoBot \
has no JSON API for either -- so they always return \
`{"available": false, "reason": ..., "alternative": ...}` naming a real \
alternative tool, instead of failing or guessing at a scrape.
- Instance lifecycle: `restart_octobot` (confirm-gated) triggers a full \
OctoBot process restart -- narrowly scoped to unblocking evaluator-config- \
tuning backtest validation (OctoBot caches a strategy's required-evaluators \
descriptor and only clears it on a full restart); `wait_for_octobot_ready` \
polls for the instance becoming reachable again afterward, never blocking \
past its own timeout and never claiming more than "the HTTP server \
answered" (see Known OctoBot-side quirks below).

## The async job pattern (backtesting & comparison)

`start_backtest`/`compare_strategies` return `{"job_id": ...}` immediately, \
without waiting for completion. Poll `get_job_status(job_id)` for `state`/ \
`progress_percent`/`eta_seconds`/`eta_confidence` until `state` is \
`"completed"`, `"failed"`, or `"cancelled"`, then call \
`get_job_result(job_id)` for the report (or the diffed comparison table, \
for `compare_strategies`). A poll interval of a few seconds is reasonable; \
OctoBot's own progress updates roughly every 1.5s.

- OctoBot runs only ONE backtest at a time. Starting a second job while one \
is already running does not fail -- it queues (`state: "queued"`) until the \
lock frees up. `compare_strategies` runs its child configs sequentially for \
the same reason, one after another, not in parallel.
- `eta_seconds`/`eta_confidence` are a heuristic THIS SERVER computes from \
observed progress samples -- OctoBot itself provides no ETA at all. Treat \
it as a rough estimate, not a guarantee; `eta_seconds` is `null` whenever \
there isn't enough data yet or progress has stalled, never a fabricated \
number.
- `cancel_job` on a `"queued"` job cancels it immediately with no OctoBot \
call. On a `"running"` job it only REQUESTS cancellation -- the job stays \
`"running"` for a moment while OctoBot is told to stop, then transitions to \
`"cancelled"` on a later `get_job_status` poll. Don't assume cancellation \
is instant.
- Data collection has its OWN independent one-at-a-time limit, separate \
from backtesting's -- a `start_data_collection` job and a `start_backtest` \
job can run genuinely concurrently, but two data collections (or two \
backtests) still queue behind each other.
- `compare_strategies`' `diff` table in `get_job_result` only ever includes \
numeric fields present in EVERY successfully-completed child's report -- \
there is no fixed field list (OctoBot's real report shape varies by \
strategy/exchange and is deeply nested). A child that failed contributes no \
report data to the comparison at all; check each entry's own `state` \
before assuming `report`/`trades` are populated.

## Known OctoBot-side quirks and limitations (verified, not guessed)

- `create_profile(mode="import_url")` is currently BROKEN on this OctoBot \
version due to a real upstream bug (the route accesses JSON-only request \
data before checking the form field it's supposed to read, guaranteeing an \
HTTP 415). Use `mode="duplicate"`, `mode="import_file"`, or \
`mode="import_cloud_strategy"` instead.
- `profile_id` is an opaque string whose FORMAT varies: built-in profiles \
use a short slug (e.g. `"arbitrage_trading"`), user-created/imported \
profiles use a UUID. Never assume one format or validate it as a UUID.
- `list_profiles`/`get_profile` work by parsing OctoBot's profile-selector \
web page (no JSON API exists for profile discovery). This is reliable in \
normal operation but can fail loudly with a distinct, clearly-named error \
if OctoBot's page structure ever changes -- that error means "this server \
needs an update to match a newer OctoBot version," not a transient issue \
worth blindly retrying.
- `update_exchange_credentials` locally refuses (before contacting OctoBot \
at all) for any exchange name containing an underscore -- OctoBot's own \
config-path encoding would otherwise silently corrupt a different config \
entry. It also never returns the full API key/secret in its output, error \
messages, or anywhere else, only a last-4-characters hint.
- `update_trading_config`'s failure responses from OctoBot are often \
literally an empty string on this OctoBot version -- an uninformative \
error here is a known OctoBot-side limitation, not a sign this server \
dropped information.
- The `profile_type` field `list_profiles`/`get_profile` return \
(`"LIVE"`/`"SIMULATOR"`/`"UNKNOWN"`) reflects whether a real vs. simulated \
TRADER is currently enabled in that profile's own config -- it is a \
different concept from what `convert_profile_to_live` actually sets \
(OctoBot's internal live/backtesting profile type), and converting a \
profile to live does not necessarily change this scraped field. Don't use \
one as a proxy for the other.
- A `start_data_collection` job against a bad/misspelled exchange name \
does NOT fail -- OctoBot reports an identical `"finished"` success status \
regardless, so a collection that produced no real data is \
indistinguishable from a genuinely successful one by `get_job_status` \
alone. Always confirm with `list_data_files` afterward that the expected \
file actually appeared before relying on it.
- `delete_data_file` is confirm-gated (unlike `cancel_order`/ \
`close_position` above): treated as closer to the `clear_*_history` tools, \
since re-collecting an erroneously deleted file can take minutes to hours, \
not a quick redo.
- `update_evaluator_config`'s `deactivate_others=True` only ever \
force-disables OTHER EVALUATORS (technical-analysis/social/real-time/ \
scripted) not named in `patch` -- it never touches trading modes or \
strategies, even though they share the same underlying activation-config \
mechanism. Leave it at its default `False` (purely additive) unless you \
specifically want `patch` to become the exhaustive enabled-evaluator set \
for this profile.
- `list_evaluators` (like `list_profiles`/`list_data_files`) works by \
parsing OctoBot's own evaluator-config web page -- no JSON API exists for \
evaluator discovery. Its fail-loud error only fires if a WHOLE category is \
missing or if literally zero evaluators are found across ALL categories \
combined; one category (e.g. "Scripted evaluators") legitimately having \
zero installed evaluators is normal on some instances, not an error.
- `restart_octobot` can NEVER be made fully safe or graceful: OctoBot's own \
restart mechanism is an abrupt kill-and-re-exec, not a clean shutdown -- no \
task cancellation or explicit order handling happens first. This is \
inherent to OctoBot itself, not a limitation this server's implementation \
could remove. `wait_for_octobot_ready`'s default `timeout_seconds`/ \
`poll_interval_seconds` (60s/2s) are UNVERIFIED placeholders, not \
empirically measured against a real restart -- override them if you have \
your own measurement, and do not assume "ready" (HTTP reachable) means the \
tentacle-config cache this capability exists to clear is actually cleared.
- Restarting fixes the SPECIFIC `_TENTACLE_CONFIG_CACHE` bug behind \
evaluator-requirements tuning (see above), but this is NOT confirmed to be \
a universal fix for every strategy-COMPOSITION tuning case: the same \
symptom (a `profile_id`-targeted backtest not reflecting a config change) \
has independently been observed on an unrelated trading-mode-level field \
(`DailyTradingMode.required_strategies_min_count`), suggesting a broader \
OctoBot backtest-engine limitation, not a single isolated cache. \
Order-execution parameters (position sizing, stop-loss/take-profit levels) \
are unaffected and DO apply correctly in a `profile_id`-targeted backtest. \
Strategy-composition parameters (which/how-many evaluators or strategies \
must agree) should be treated as suspect until independently reverified for \
that specific mechanism -- do not assume `restart_octobot` resolves every \
future case just because it resolved this one.

## Safety

Any tool that destroys data, writes exchange credentials, enables live/ \
real-money trading, or disrupts the whole running instance (`restart_octobot`) \
requires an explicit `confirm=true` argument. Omitting \
it (or passing `confirm=false`) performs NO action and returns a normal \
(non-error) structured refusal explaining why and how to proceed -- read \
that refusal before blindly retrying with `confirm=true`. Routine trading \
actions (`cancel_order`, `close_position`) are deliberately NOT \
confirm-gated even though they have real effects, since they're expected, \
everyday operations for a trading bot, not rare/catastrophic mistakes. \
`restart_octobot`'s refusal reason string is more detailed when the \
currently selected profile is LIVE (or its type is inconclusive) than when \
it is SIMULATOR -- read it either way; `confirm=true` is the SAME gate in \
both cases, never a hard block.\
"""

# The MCP SDK's own `MCPServer.__init__` unconditionally calls its internal
# `mcp.server.mcpserver.utilities.logging.configure_logging()`, which calls
# `logging.basicConfig(level="INFO", handlers=[RichHandler(...)])` on the ROOT
# logger as a side effect of construction -- at IMPORT time, since `mcp`
# below is a module-level instance. Because the root logger has no handlers
# yet at that point, `basicConfig` doesn't just attach the handler: per
# `logging.basicConfig`'s own semantics, it also unconditionally calls
# `root.setLevel("INFO")`, a SEPARATE side effect independent of the handler
# list. Both undermine this module's own invariant (task 17, FR-29): only
# this project's `configure_logging()` may touch root-logger handlers/level,
# and only when `main()` calls it explicitly, never as an import-time side
# effect (see `tests/test_logging_config.py`'s subprocess-based regression
# test, which caught the handler half of this from a clean interpreter).
# Snapshot both the handler list and the level immediately before
# construction, then restore both right after -- a diff/restore, not an
# unconditional reset, so this stays a targeted workaround for the SDK's own
# behavior and doesn't mask a real handler- or level-changing regression
# introduced elsewhere in this codebase.
_root_logger = logging.getLogger()
_handlers_before_mcp_construction = list(_root_logger.handlers)
_level_before_mcp_construction = _root_logger.level
mcp = MCPServer("OctoBot", instructions=INSTRUCTIONS, lifespan=app_lifespan)
register_all(mcp)
for _sdk_attached_handler in list(_root_logger.handlers):
    if _sdk_attached_handler not in _handlers_before_mcp_construction:
        _root_logger.removeHandler(_sdk_attached_handler)
        _sdk_attached_handler.close()
if _root_logger.level != _level_before_mcp_construction:
    _root_logger.setLevel(_level_before_mcp_construction)


def main() -> None:
    """Start the server on the stdio transport. Blocks until the client disconnects."""
    configure_logging()
    mcp.run()


if __name__ == "__main__":
    main()
