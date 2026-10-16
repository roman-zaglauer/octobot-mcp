"""Server-level smoke tests: the server object constructs and exposes tools.

Uses the MCP SDK's in-memory Client (see docs/get-started/testing.md in the
SDK's own docs) so the handshake (initialize + tools/list) is exercised
without spawning a subprocess.

Milestone 1 registered zero tools; milestone 3 (docs/tasklist.md item 3)
registered the first 10; milestone 4 (item 4) adds the Tier-B profile
scrape adapter's `list_profiles`/`get_profile`; milestone 5 (item 5) adds
the non-destructive profile write tools `create_profile`/`update_profile`/
`select_profile`/`export_profile`; milestone 6 (item 6) adds the first two
confirm-gated tools (ADR-0003), `delete_profile`/`convert_profile_to_live`;
milestone 7 (item 7) adds the job store scaffolding's 3 tools,
`get_job_status`/`list_jobs`/`cancel_job` (none confirm-gated, none making
any HTTP/Socket.IO call -- they operate purely on the in-memory job store);
milestone 8 (item 8) adds `start_backtest`/`get_job_result`, the tools that
finally wire the job store to a real OctoBot backtest run via the
Socket.IO progress watcher (neither confirm-gated: `start_backtest` starts
a simulation, `get_job_result` is read-only); milestone 12 (item 12) adds
the remaining confirm-gated tools (`clear_orders_history`/
`clear_trades_history`/`clear_portfolio_history`/`clear_transactions_history`/
`update_exchange_credentials`); milestone 13 (item 13) adds
`get_tentacle_config`/`update_tentacle_config`/`export_logs` (Tier A) and
the Tier-C stubs `list_tentacles`/`get_logs` (ADR-0006 -- neither ever
makes an HTTP call); milestone 13.5 (item 13.5) adds `update_trading_config`,
closing a backlog gap (spec FR-21, never assigned to a tasklist milestone
until now) -- not confirm-gated, `POST /config`'s `trading_config` branch;
milestone 13.6 (item 13.6) adds `cancel_order`/`close_position`/
`refresh_portfolio`, closing a second backlog gap the same way -- none
confirm-gated (the user was explicitly asked about the spec's own footnote
flagging `cancel_order`/`close_position` as "revisitable" given their
irreversible effect, and chose to keep both ungated, per ADR-0003's
original reasoning); milestone 15 (item 15, ADR-0007/ADR-0008, a genuinely
new-scope addendum) adds `start_data_collection` (not confirm-gated, same
reasoning as `start_backtest`), `list_data_files`/
`get_available_timeframes_for_collection` (read-only Tier-B/Tier-A reads),
`import_data_file` (not confirm-gated, creates new state only), and
`delete_data_file` (**confirm-gated**, reversed from this addendum's
original draft -- see FR-25). No `get_available_symbols_for_collection`
tool exists (FR-27's own contingency: confirmed live this session that
`get_all_symbols` already returns identical data). `get_job_status`/
`list_jobs`/`cancel_job`/`get_job_result` are reused unchanged for
`kind: "data_collection"` jobs (ADR-0007) -- no new tool names for those.
Task 16 (item 16, FR-28, a genuinely new-scope addendum closing an
evaluator-CRUD gap) adds `update_evaluator_config` (not confirm-gated, same
`POST /config` route and reasoning as `update_trading_config`, a sibling
`EVALUATOR_CONFIG_KEY` branch). Task 18 (item 18, ADR-0010, FR-30/FR-31, a
genuinely new-scope addendum narrowly reversing this project's own "reboot"
out-of-scope exclusion) adds `restart_octobot` (**confirm-gated**, ADR-0003 --
the first tool in this project whose safety cannot be fully engineered, only
scoped) and `wait_for_octobot_ready` (not confirm-gated, a read-only poll).
Task 19 (item 19, ADR-0009 -- originally recommended NO-GO, reversed to GO
by explicit user decision, FR-32/NFR-18) adds `list_evaluators` (not
confirm-gated, a third Tier-B HTML-scrape read adapter, `GET
/advanced/evaluator_config`), closing `update_evaluator_config`'s own
companion-read gap. Milestone 20 (item 20, ADR-0011, a genuinely new-scope
addendum found via a live UI audit) adds `get_portfolio_snapshot`/
`get_evaluation_matrix` (neither confirm-gated, a fourth/fifth Tier-B
scrape adapter pair).
Task 21 (item 21, ADR-0012, the accounts/profile-global-config addendum)
adds eleven tools: `get_exchanges`/`add_exchange`/`remove_exchange`/
`update_exchange_trading_mode` (`exchanges.py`; `add_exchange`/
`remove_exchange` confirm-gated, `get_exchanges`/`update_exchange_trading_mode`
not), `get_interfaces`/`update_interface_config` (new module
`interfaces.py`; `update_interface_config` CONDITIONALLY confirm-gated --
NFR-22/23, the one tool in this project whose gating is per-call, not
per-tool), `get_notification_routing`/`update_notification_routing` (same
module, neither gated), and `set_real_trading_enabled` (`profiles.py`,
confirm-gated BOTH directions -- finally closes requirements doc open
question #5), `get_profile_currencies`/`update_profile_currencies`
(`profiles.py`, neither gated). Milestone 22 (item 22, ADR-0013,
FR-43..FR-45/NFR-24, a genuinely new-scope addendum reusing the existing
tentacle-config mechanism, not a new rules-engine subsystem) adds
`get_automations`/`update_automations` (neither confirm-gated --
passthrough reads/writes of the current profile's automation config) and
`start_automations`/`reset_automations_to_default` (**both confirm-gated**,
under two distinct criteria: `start_automations` under a brand-new
"activates autonomous, ongoing behavior" category,
`reset_automations_to_default` under the ordinary irreversible-deletion
criterion already used by `clear_*_history`/`delete_data_file`).
Each milestone updates this same assertion to the new -- expected -- tool
set instead of keeping a now-false invariant (the pattern milestone 3
itself established over milestone 1's `test_server_lists_zero_tools`). See
`test_tools_portfolio.py`/`test_tools_exchanges.py`/`test_tools_profiles.py`/
`test_tools_profiles_gated.py`/`test_tools_jobs.py`/`test_tools_backtesting.py`/
`test_tools_tentacles.py`/`test_tools_trading_config.py`/
`test_tools_portfolio_trading_actions.py`/`test_tools_data_collection.py`/
`test_tools_instance_lifecycle.py`/`test_tools_exchanges_accounts_config.py`/
`test_tools_interfaces.py`/`test_tools_profiles_accounts_config.py`/
`test_tools_automations.py`
for per-tool behavior and `test_server_tools_integration.py` for an
end-to-end call through this same `mcp` wiring.

Since milestone 3 also wires `mcp`'s real `app_lifespan` (NFR-11) into
this module-level `mcp` instance, connecting a `Client` to it now runs
`load_config()` against the real environment (ADR-0004) -- `OCTOBOT_BASE_URL`
must be set for these tests, even though they never issue a real HTTP
request (`list_tools`/capability negotiation don't touch the client).
"""

import pytest
from mcp import Client

from octobot_mcp.server import mcp

EXPECTED_MILESTONE_22_TOOL_NAMES = {
    "get_orders",
    "get_positions",
    "get_trades",
    "get_pnl_history",
    "get_historical_portfolio_value",
    "clear_orders_history",
    "clear_trades_history",
    "clear_portfolio_history",
    "clear_transactions_history",
    "get_currency_list",
    "get_all_currencies",
    "get_all_symbols",
    "check_accounts_compatible",
    "get_exchange_details",
    "update_exchange_credentials",
    "list_profiles",
    "get_profile",
    "create_profile",
    "update_profile",
    "select_profile",
    "export_profile",
    "delete_profile",
    "convert_profile_to_live",
    "get_job_status",
    "list_jobs",
    "cancel_job",
    "start_backtest",
    "get_job_result",
    "compare_strategies",
    "get_tentacle_config",
    "update_tentacle_config",
    "list_tentacles",
    "get_logs",
    "export_logs",
    "update_trading_config",
    "update_evaluator_config",
    "cancel_order",
    "close_position",
    "refresh_portfolio",
    "start_data_collection",
    "list_data_files",
    "delete_data_file",
    "import_data_file",
    "get_available_timeframes_for_collection",
    "restart_octobot",
    "wait_for_octobot_ready",
    "list_evaluators",
    "get_portfolio_snapshot",
    "get_evaluation_matrix",
    "get_exchanges",
    "add_exchange",
    "remove_exchange",
    "update_exchange_trading_mode",
    "get_interfaces",
    "update_interface_config",
    "get_notification_routing",
    "update_notification_routing",
    "set_real_trading_enabled",
    "get_profile_currencies",
    "update_profile_currencies",
    "get_automations",
    "update_automations",
    "start_automations",
    "reset_automations_to_default",
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OCTOBOT_BASE_URL", "http://octobot.example")
    async with Client(mcp, raise_exceptions=True) as c:
        yield c


@pytest.mark.anyio
async def test_server_lists_the_milestone_22_tools(client: Client):
    result = await client.list_tools()
    assert {tool.name for tool in result.tools} == EXPECTED_MILESTONE_22_TOOL_NAMES


@pytest.mark.anyio
async def test_server_declares_tools_capability(client: Client):
    capabilities = client.server_capabilities
    assert capabilities is not None
    assert capabilities.tools is not None


def test_server_name():
    assert mcp.name == "OctoBot"
