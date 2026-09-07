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

from mcp.server import MCPServer

from octobot_mcp.context import app_lifespan
from octobot_mcp.tools import register_all

INSTRUCTIONS = """\
This server manages ONE OctoBot trading-bot instance. Its base URL is fixed \
for the whole process (the `OCTOBOT_BASE_URL` environment variable set when \
this server was launched) -- no tool takes an instance/base URL parameter. \
There is no login/auth at this MCP layer or against OctoBot itself (v1 only \
supports instances with OctoBot's own login disabled).

## Tool groups

- Profiles: `list_profiles`/`get_profile` discover ids and names; \
`create_profile`/`update_profile`/`select_profile`/`export_profile` manage \
them; `delete_profile`/`convert_profile_to_live` are destructive/high-risk \
and confirm-gated (see Safety).
- Backtesting & strategy comparison (asynchronous -- see next section): \
`start_backtest`, `compare_strategies`, `get_job_status`, `list_jobs`, \
`cancel_job`, `get_job_result`.
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
`update_trading_config` read/write configuration; `export_logs` exports a \
real log archive. `list_tentacles`/`get_logs` are honest stubs -- OctoBot \
has no JSON API for either -- so they always return \
`{"available": false, "reason": ..., "alternative": ...}` naming a real \
alternative tool, instead of failing or guessing at a scrape.

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

## Safety

Any tool that destroys data, writes exchange credentials, or enables live/ \
real-money trading requires an explicit `confirm=true` argument. Omitting \
it (or passing `confirm=false`) performs NO action and returns a normal \
(non-error) structured refusal explaining why and how to proceed -- read \
that refusal before blindly retrying with `confirm=true`. Routine trading \
actions (`cancel_order`, `close_position`) are deliberately NOT \
confirm-gated even though they have real effects, since they're expected, \
everyday operations for a trading bot, not rare/catastrophic mistakes.\
"""

mcp = MCPServer("OctoBot", instructions=INSTRUCTIONS, lifespan=app_lifespan)
register_all(mcp)


def main() -> None:
    """Start the server on the stdio transport. Blocks until the client disconnects."""
    mcp.run()


if __name__ == "__main__":
    main()
