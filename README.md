# OctoBot MCP Server

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/roman-zaglauer/octobot-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/roman-zaglauer/octobot-mcp/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)

An MCP ([Model Context Protocol](https://modelcontextprotocol.io/)) server
that wraps a single [OctoBot](https://github.com/Drakkar-Software/OctoBot)
instance's web API so an AI agent (e.g. Claude Code) can manage trading
profiles, run and compare backtests, and drive OctoBot's portfolio/trading,
exchange, and tentacle-config surfaces — all through OctoBot's existing HTTP
API, without you hand-writing a client for it.

Use this if you already run an OctoBot instance and want an agent to operate
it (list/create/switch profiles, kick off and compare backtests, check
positions and PnL, tweak tentacle/trading config) instead of clicking through
OctoBot's web UI yourself. It is not a trading strategy, a replacement for
OctoBot itself, or a multi-instance fleet manager (see "Design docs" below
for what's explicitly out of scope and why).

**Status: v1 complete, plus several post-v1 addenda.** All 14 original
tasklist milestones, and additional addenda closing gaps found after v1
shipped (historical data collection, evaluator config, this server's own
logging, a scoped OctoBot-restart capability, a portfolio snapshot and live
evaluation matrix, accounts/profile global-config writes — real-trading
toggle, exchange add/remove, interface/notification config, per-profile
currency selection — and automations), are implemented, judge-verified, and
committed — see `docs/tasklist.md` for the full, numbered history. The
server registers **64 tools** across profiles, async backtesting/strategy
comparison/historical data collection, portfolio & trading, exchanges,
interfaces & notifications, tentacle/trading config, instance lifecycle, and
automations. See "Available tools" below for the full list, or connect a
client and read `mcp.instructions`/each tool's own description — both are
written to stand alone without this file.

## Requirements

- Python 3.10+
- A running OctoBot instance reachable over plain HTTP, with `login_required_when_activated`
  disabled (session-login support is out of scope for v1 — see ADR-0005)
- The [`mcp`](https://pypi.org/project/mcp/) package and its other runtime
  dependencies (`httpx2`, `beautifulsoup4`, `python-socketio`) — installed
  automatically as dependencies

## Install

From the repo root, in a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

The `dev` extra adds `pytest` (for the test suite) and the `mcp` CLI extra
(for `mcp dev`, useful for manual poking with the MCP Inspector).

## Configure

The server targets exactly one OctoBot instance for its whole process
lifetime (ADR-0004) — no tool takes an instance/base-URL parameter. Set its
base URL via an environment variable before launching:

```bash
export OCTOBOT_BASE_URL=http://192.168.178.100:5001
```

The server fails fast at startup with a clear error if this is unset or not
a valid `http(s)://` URL.

## Run

Per ADR-0001, this server speaks MCP over **stdio only**: it is meant to be
launched by an MCP client (e.g. Claude Code), not run standalone. Any of
these are equivalent launch commands:

```bash
python -m octobot_mcp
# or, if installed:
octobot-mcp
# or, via the SDK's own CLI:
mcp run src/octobot_mcp/server.py
```

Once running, the process blocks silently waiting for a client to speak
first on stdin — that's expected, not a hang. `Ctrl-C` to stop it.

## Register with Claude Code

```bash
claude mcp add octobot --env OCTOBOT_BASE_URL=http://192.168.178.100:5001 -- /absolute/path/to/.venv/bin/python -m octobot_mcp
```

Then run `/mcp` inside a Claude Code session to confirm `octobot` is
connected and lists 64 tools.

## Available tools

- **Profiles**: `list_profiles`, `get_profile`, `create_profile`,
  `update_profile`, `select_profile`, `export_profile`, `delete_profile`
  (confirm-gated), `convert_profile_to_live` (confirm-gated),
  `set_real_trading_enabled` (task 21, ADR-0012 — confirm-gated in BOTH
  directions; finally closes the real-vs-simulated-trading toggle gap
  `convert_profile_to_live` above explicitly does NOT fill, forces a full
  OctoBot restart), `get_profile_currencies`/`update_profile_currencies`
  (task 21, ADR-0012 — per-profile traded-currency enable state/pair
  selection, neither gated; genuinely distinct from `update_profile`'s own
  broken `config` passthrough field above)
- **Backtesting & strategy comparison** (async — `start_backtest`/
  `compare_strategies` return a `job_id` immediately; poll `get_job_status`
  until `state` is terminal, then call `get_job_result`): `start_backtest`,
  `compare_strategies`, `get_job_status`, `list_jobs`, `cancel_job`,
  `get_job_result`
- **Portfolio & trading**: `get_orders`, `get_positions`, `get_trades`,
  `get_pnl_history`, `get_historical_portfolio_value`, `cancel_order`,
  `close_position`, `refresh_portfolio`, `clear_orders_history` (confirm-gated),
  `clear_trades_history` (confirm-gated), `clear_portfolio_history`
  (confirm-gated), `clear_transactions_history` (confirm-gated),
  `get_portfolio_snapshot` (task 20, ADR-0011 — a fourth Tier-B HTML-scrape
  read adapter; a current per-asset holdings breakdown — asset, total,
  value in the profile's reference currency, available, locked-in-orders —
  in one call, closing a gap the other portfolio tools above couldn't close
  directly)
- **Exchanges**: `get_currency_list`, `get_all_currencies`, `get_all_symbols`,
  `check_accounts_compatible`, `get_exchange_details`,
  `update_exchange_credentials` (confirm-gated), `get_exchanges` (task 21,
  ADR-0012 — lists every configured exchange's enable state/credential
  status via a Tier-B scrape of `/accounts`, closing ADR-0006's own "no full
  exchange list" gap), `add_exchange` (confirm-gated), `remove_exchange`
  (confirm-gated — also deletes the exchange's saved credentials, not just
  a config entry), `update_exchange_trading_mode` (not confirm-gated —
  switching spot/futures mode is a flagged judgment call, ADR-0012
  Decision 4)
- **Interfaces & notifications** (task 21, ADR-0012, new module
  `interfaces.py`): `get_interfaces` (Tier-B scrape of `/accounts`'
  configured services — Telegram/Web/etc. — with any credential-shaped
  field redacted), `update_interface_config` (CONDITIONALLY confirm-gated —
  the ONE tool in this project whose gating is per-call, not per-tool: a
  patch touching a field matching a secret-suffix list — `token`/`secret`/
  `password`/`api-key`/`api-secret`/`api-password`/`api-hash`/
  `client-secret` — requires `confirm=true`; a patch touching only
  non-credential fields, e.g. `port`, proceeds without it),
  `get_notification_routing`/`update_notification_routing` (neither
  gated — routing only, no credentials/destruction/live-trading involved)
- **Tentacle & trading config**: `get_tentacle_config`,
  `update_tentacle_config`, `update_trading_config`, `update_evaluator_config`,
  `list_evaluators` (task 19, ADR-0009 — a third Tier-B HTML-scrape read
  adapter, originally recommended NO-GO then reversed to GO by explicit user
  decision; enumerates every evaluator's name/activation state/category,
  closing `update_evaluator_config`'s own companion-read gap),
  `get_evaluation_matrix` (task 20, ADR-0011 — a fifth Tier-B HTML-scrape
  read adapter; OctoBot's live every-evaluator/timeframe/symbol/exchange
  signal-value table in one call, closing a gap previously only
  reconstructable by grepping `export_logs`' `MATRIX` log lines),
  `export_logs`, `list_tentacles` (stub — no JSON API exists), `get_logs`
  (stub — no JSON API exists)
- **Historical data collection** (async — `start_data_collection` reuses
  the same `job_id`/`get_job_status`/`cancel_job`/`get_job_result` pattern
  as `start_backtest`, above): `start_data_collection`, `list_data_files`,
  `delete_data_file` (confirm-gated), `import_data_file`,
  `get_available_timeframes_for_collection`. There is no separate
  `get_available_symbols_for_collection` tool — the existing
  `get_all_symbols` tool already returns equivalent data for this purpose.
- **Instance lifecycle** (task 18, ADR-0010 — narrow, explicit reversal of
  this project's own "reboot" out-of-scope exclusion): `restart_octobot`
  (confirm-gated) triggers a full OctoBot process restart;
  `wait_for_octobot_ready` polls for the instance becoming reachable again
  afterward, with real measured timing behind its defaults (60s timeout/2s
  poll interval — two live restarts measured ~2.1s until the old process
  goes down, ~6.3–6.7s of real outage, ~8.5–8.9s total until reachable
  again) and a real readiness-check race bug found and fixed (an early poll
  could catch the still-alive old process and falsely report `ready: true`
  before anything had restarted). **`restart_octobot` can never be made
  fully safe or graceful** — OctoBot's own restart mechanism is an abrupt
  kill-and-re-exec, not a clean shutdown. **This capability was built to
  unblock evaluator/strategy-composition-tuning backtest validation, and
  live testing has since CONFIRMED it does not achieve that** — the
  restart mechanism itself works correctly, but a `profile_id`-targeted
  backtest still doesn't reflect strategy-composition config changes
  (which/how-many evaluators must agree) after a restart, confirmed on two
  separate OctoBot instances. Order-execution config (sizing, stop-loss/
  take-profit) is unaffected and applies correctly without a restart. See
  `docs/adr/0010-octobot-restart-capability.md`'s "Negative finding" for
  the full account, including an open question of whether to keep this
  capability at all now that its stated purpose doesn't hold.
- **Automations** (task 22, ADR-0013 — reuses the existing tentacle-config
  mechanism `update_tentacle_config` already wraps, not a new rules-engine
  subsystem): `get_automations`/`update_automations` read/write the
  current profile's automation configuration, passthrough-only (never
  reshaping OctoBot's own schema, NFR-24); `start_automations`
  (**confirm-gated**, under a brand-new gating category — "activates
  autonomous, ongoing behavior beyond this one call" — distinct from every
  other gated tool's destructive/credential-exposure/live-trading
  reasoning) activates the saved configuration; `reset_automations_to_default`
  (**confirm-gated**, under the ordinary irreversible-deletion criterion,
  the same category as `clear_*_history`/`delete_data_file`) factory-resets
  it to OctoBot's default. Both also restart automations and refuse
  outright (no OctoBot call) if an active backtest/data-collection job is
  running, mirroring `restart_octobot`'s own `JobStore` check. Both raise a
  distinct `AutomationConfigInvalidError` if OctoBot reports the saved
  config is structurally invalid, never a generic failure.

Any tool marked "confirm-gated" refuses to act unless called with
`confirm=true`, returning a structured explanation instead (ADR-0003) — see
`mcp.instructions`' "Safety" section for the full rationale. `delete_data_file`
is confirm-gated alongside `delete_profile`/`convert_profile_to_live`/the
`clear_*_history` tools/`update_exchange_credentials`/`restart_octobot`/
`set_real_trading_enabled` (both directions)/`add_exchange`/`remove_exchange`/
`update_interface_config` (conditionally — see "Interfaces & notifications"
above)/`start_automations`/`reset_automations_to_default`.

## Logging

The server's own structured logs (one line per tool call, one per job state
transition — see `docs/specs/octobot-mcp-tool-spec.md`'s "Observability"
section) are written to a local rotating log file, not exposed through any
MCP tool. Configured once, in `main()`, before the transport starts:

| Env var | Default | Purpose |
|---|---|---|
| `OCTOBOT_MCP_LOG_FILE` | `~/.octobot-mcp/server.log` | Log file path (parent directory created if missing) |
| `OCTOBOT_MCP_LOG_LEVEL` | `INFO` | Root logger level for the file handler |
| `OCTOBOT_MCP_LOG_MAX_BYTES` | `5000000` | Rotation size threshold |
| `OCTOBOT_MCP_LOG_BACKUP_COUNT` | `3` | Number of rotated backups kept |

A separate stderr handler, fixed at `WARNING`+ regardless of
`OCTOBOT_MCP_LOG_LEVEL`, ensures a crashing process still surfaces something
to whatever the MCP client captures from the subprocess.

## Test

```bash
pytest
```

488 tests, no live instance required — every test mocks the OctoBot HTTP/
Socket.IO layer. Live verification against a real instance was done
per-milestone during development (see each milestone's commit message). CI
runs this same suite on every push and pull request (see the CI badge above).

## Design docs

- `docs/adr/` — architectural decisions (transport/SDK choice, async job
  model, confirm-flag gating, single-instance config, no-auth v1 scope,
  read-model acquisition strategy)
- `docs/requirements/octobot-mcp-requirements.md` — functional/non-functional
  requirements
- `docs/specs/octobot-mcp-tool-spec.md` — the tool inventory, job store
  design, and ETA algorithm
- `docs/tasklist.md` — the build roadmap, in the order it was actually
  implemented

## Contributing

Bug reports and pull requests are welcome — see
[`CONTRIBUTING.md`](CONTRIBUTING.md) for how to set up a dev environment, the
project's conventions, and how to submit a change. Please also read the
[`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md). Found a security issue,
especially anything touching exchange credential handling? See
[`.github/SECURITY.md`](.github/SECURITY.md) instead of filing a public
issue.

## License

MIT — see [`LICENSE`](LICENSE).
