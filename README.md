# OctoBot MCP Server

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/OWNER/REPO/actions/workflows/ci.yml/badge.svg)](https://github.com/OWNER/REPO/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)

<!-- TODO: replace OWNER/REPO in the CI badge URL above with this repo's real GitHub org/name once it has a remote. -->

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

**Status: v1 complete.** All 14 tasklist milestones (plus three small
addenda closing gaps found during review) are implemented, judge-verified,
and committed. The server registers **38 tools** across profiles, async
backtesting/strategy comparison, portfolio & trading, exchanges, and
tentacle/trading config. See "Available tools" below for the full list, or
connect a client and read `mcp.instructions`/each tool's own description —
both are written to stand alone without this file.

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
connected and lists 43 tools.

## Available tools

- **Profiles**: `list_profiles`, `get_profile`, `create_profile`,
  `update_profile`, `select_profile`, `export_profile`, `delete_profile`
  (confirm-gated), `convert_profile_to_live` (confirm-gated)
- **Backtesting & strategy comparison** (async — `start_backtest`/
  `compare_strategies` return a `job_id` immediately; poll `get_job_status`
  until `state` is terminal, then call `get_job_result`): `start_backtest`,
  `compare_strategies`, `get_job_status`, `list_jobs`, `cancel_job`,
  `get_job_result`
- **Portfolio & trading**: `get_orders`, `get_positions`, `get_trades`,
  `get_pnl_history`, `get_historical_portfolio_value`, `cancel_order`,
  `close_position`, `refresh_portfolio`, `clear_orders_history` (confirm-gated),
  `clear_trades_history` (confirm-gated), `clear_portfolio_history`
  (confirm-gated), `clear_transactions_history` (confirm-gated)
- **Exchanges**: `get_currency_list`, `get_all_currencies`, `get_all_symbols`,
  `check_accounts_compatible`, `get_exchange_details`,
  `update_exchange_credentials` (confirm-gated)
- **Tentacle & trading config**: `get_tentacle_config`,
  `update_tentacle_config`, `update_trading_config`, `export_logs`,
  `list_tentacles` (stub — no JSON API exists), `get_logs` (stub — no JSON
  API exists)
- **Historical data collection** (async — `start_data_collection` reuses
  the same `job_id`/`get_job_status`/`cancel_job`/`get_job_result` pattern
  as `start_backtest`, above): `start_data_collection`, `list_data_files`,
  `delete_data_file` (confirm-gated), `import_data_file`,
  `get_available_timeframes_for_collection`. There is no separate
  `get_available_symbols_for_collection` tool — the existing
  `get_all_symbols` tool already returns equivalent data for this purpose.

Any tool marked "confirm-gated" refuses to act unless called with
`confirm=true`, returning a structured explanation instead (ADR-0003) — see
`mcp.instructions`' "Safety" section for the full rationale. `delete_data_file`
is confirm-gated alongside `delete_profile`/`convert_profile_to_live`/the
`clear_*_history` tools/`update_exchange_credentials`.

## Test

```bash
pytest
```

294 tests, no live instance required — every test mocks the OctoBot HTTP/
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
