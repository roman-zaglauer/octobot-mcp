# OctoBot MCP Server

An MCP (Model Context Protocol) server that wraps a single [OctoBot](https://github.com/Drakkar-Software/OctoBot)
instance's web API so an AI agent (e.g. Claude Code) can manage trading
profiles and run/compare backtests.

See `docs/requirements/octobot-mcp-requirements.md`, `docs/specs/octobot-mcp-tool-spec.md`,
and `docs/adr/` for the full design, and `docs/tasklist.md` for the build
roadmap.

**Status: milestone 1 (scaffolding).** The server starts, speaks the MCP
stdio handshake, and registers **zero tools**. Tools are added in later
milestones.

## Requirements

- Python 3.10+
- The [`mcp`](https://pypi.org/project/mcp/) package (installed automatically
  as a dependency)

## Install

From the repo root, in a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

The `dev` extra adds `pytest` (for the test suite) and the `mcp` CLI extra
(for `mcp dev`, useful for manual poking with the MCP Inspector).

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
claude mcp add octobot -- /absolute/path/to/.venv/bin/python -m octobot_mcp
```

Then run `/mcp` inside a Claude Code session to confirm `octobot` is
connected. At this milestone it should list **0 tools** with no error.

## Test

```bash
pytest
```

The test suite uses the MCP SDK's in-memory `Client` to drive the
initialize/`tools/list` handshake against the server object directly (no
subprocess needed) and asserts the tool list is empty.
