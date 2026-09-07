# Contributing to OctoBot MCP Server

Thanks for considering a contribution. This project is a small, focused
Python MCP server with one job: wrap a single OctoBot instance's web API
safely and honestly. The conventions below exist because they've already
mattered once in this codebase — read them before opening a PR.

## Dev environment

Same steps as the [README](README.md#install):

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

The `dev` extra installs `pytest` and the `mcp` CLI extra (`mcp dev` runs the
MCP Inspector against `src/octobot_mcp/server.py`, useful for poking at tools
by hand). You do **not** need a live OctoBot instance to develop or run the
test suite — see below.

## Running the tests

```bash
pytest
```

The full suite (294 tests at the time of writing) mocks every OctoBot
HTTP/Socket.IO interaction — no live instance, network access, or
credentials are required, and none of it is needed for CI either. If you add
a live-instance verification step while developing (recommended for
anything touching a previously-unverified OctoBot route — see below), keep
it out of the committed test suite; document what you verified and against
which OctoBot version in the code's docstring instead.

Run a single test file or test with the usual pytest selectors, e.g.
`pytest tests/test_profiles.py -k delete_profile`.

## Conventions this project actually follows

These aren't aspirational — they're patterns already used consistently
across the codebase, and PRs are expected to match them.

### 1. Never guess an OctoBot route or payload shape — verify it against source

Every tool module's docstring documents exactly which OctoBot source file
and function a route maps to (e.g. `src/octobot_mcp/tools/profiles.py`'s
module docstring cites `controllers/configuration.py`'s
`profiles_management` dispatcher; `tentacles.py`'s cites
`controllers/tentacles_config.py::config_tentacle()`). Several of these
verifications turned up **genuine spec corrections** — routes that don't
behave the way `docs/specs/octobot-mcp-tool-spec.md` originally assumed
(e.g. `update_profile`'s risk/complexity fields needing integer enum
ordinals, not display strings; `close_position` taking `symbol`/`side`
because OctoBot positions have no `id` at all). If you add or change a tool:

- Read the actual route handler in
  [`Drakkar-Software/OctoBot`](https://github.com/Drakkar-Software/OctoBot)
  (or `OctoBot-Tentacles`, which mirrors the same web-interface package) —
  don't infer behavior from the tool spec, the README, or how a similar
  route looks elsewhere in this codebase.
- Record what you verified — the exact route, method, request/response
  shape, and any surprises versus the spec — in the tool function's or
  module's docstring, the same way existing modules do. This is the
  project's substitute for a separate design doc per tool: the code itself
  is the record.
- If you can reach a live OctoBot instance, reproduce the behavior there
  too and say so in the docstring (`confirmed against source AND live`).
  If you can't, say that too, plainly — this project consistently
  documents "what remains genuinely unverified" rather than implying more
  confidence than it has (see `exchanges.py`'s module docstring for an
  example).

### 2. Test-first (TDD)

`docs/tasklist.md` calls this out explicitly for every milestone: write a
failing test first, implement the minimal change to pass it, then refactor.
Follow the same discipline for new work, especially for anything touching
the ETA estimator, the job store's state machine, or a confirm-gated tool —
these have historically been where an untested edge case (a mid-run stall,
a queued→cancelled race) caused real bugs.

### 3. Confirm-gating for destructive/high-risk tools

If a new tool destroys data, writes exchange credentials, or enables
real-money trading, gate it the way every existing destructive tool does
(ADR-0003), using the one shared helper —
`octobot_mcp.tools._shared.require_confirmation` — rather than writing
bespoke per-tool confirmation logic:

```python
if confirm is not True:
    return require_confirmation(
        "your_tool_name",
        target,
        "A human-readable reason confirmation is required for this specific action.",
    )
```

This is a **normal return value, not an exception** — an unconfirmed call is
the tool correctly doing what was asked, not a failure. Never route it
through `with_error_handling`/`ToolError`. See
`src/octobot_mcp/tools/_shared.py`'s docstring and
`docs/adr/0003-confirm-flag-gating.md` for the full rationale and the
current gated-tool list. If you're unsure whether a new tool should be
gated, look at ADR-0003's existing categorization (irreversible deletion,
credential writes, and live-trading enablement are gated; routine trading
actions and reversible mutations are not) before inventing a new category.

### 4. Structured errors, never raw tracebacks

Every tool function is wrapped in `@with_error_handling`
(`src/octobot_mcp/tools/_shared.py`), which classifies OctoBot connectivity
failures into `OctoBotAPIError` subclasses and re-raises them as `ToolError`
so the calling agent sees a clear, classified message instead of a stack
trace (NFR-4). New tools should:

- Use `@with_error_handling` on every registered tool function.
- Raise a specific, named exception from `octobot_mcp.errors` (add a new one
  if none fits) rather than letting a generic exception propagate — a bug
  should crash loudly in server logs, but a *classified OctoBot failure*
  should always reach the agent as a structured message.
- **Not** use `from __future__ import annotations` in a tool module — the
  MCP SDK inspects live `__annotations__` to build input schemas and detect
  the injected `ctx: Context` parameter; postponed evaluation silently
  breaks this (see `with_error_handling`'s docstring for the mechanism).

### 5. Never let a secret reach a log line or tool response

If your change touches `update_exchange_credentials` or adds a new tool that
accepts a credential-shaped argument, redact it. There are two distinct
mechanisms, both in `_shared.py` — use whichever matches your case, not a
new one:

- `redact_secrets(text, secrets)` — scrubs literal secret values out of
  unstructured text (e.g. an OctoBot error message that might echo a
  submitted credential back).
- The `_SENSITIVE_PARAM_NAMES` denylist — redacts a named parameter's entire
  value from the structured per-call log line, generically, for any tool
  whose signature has a parameter named `api_key`/`api_secret`/
  `api_password`. Add your parameter name to that set if it's a new kind of
  secret, rather than writing a second logging path.

A test asserting no full secret value ever appears in a tool's output or in
logs is required for any change in this area (see NFR-6/INV-4 in
`docs/requirements/octobot-mcp-requirements.md`).

### 6. One domain module per tool group, each with its own `register(mcp)`

Tools live in `src/octobot_mcp/tools/<domain>.py` (`profiles.py`,
`portfolio.py`, `exchanges.py`, `tentacles.py`, `trading_config.py`,
`jobs.py`, `backtesting.py`), each exposing a `register(mcp)` function
called once from `octobot_mcp.tools.register_all`. Small per-module
helpers like `_request_raw`/`_extract_error_text` are **intentionally
duplicated** across modules rather than shared — this project's established
convention, documented in each module's docstring, is that only genuinely
cross-cutting concerns (error wrapping, confirm-gating, secret redaction)
live in `_shared.py`; per-module HTTP quirks stay local to the module that
needs them. Follow this pattern rather than introducing a new shared utility
module for a single domain's needs.

### 7. Passthrough, don't reshape, OctoBot's own response fields

Per NFR-8, tool outputs return OctoBot's own field names/nesting unchanged
wherever the exact schema isn't independently confirmed (backtest reports
are the clearest example — see `backtesting.py`'s `get_job_result`
docstring for a captured real payload). Don't rename, flatten, or invent
fields for convenience; if a field's meaning is unclear or unconfirmed,
document that in the docstring instead of normalizing it away.

## Opening a pull request

- Reference what you're addressing: a `docs/tasklist.md` item number if one
  exists, or otherwise describe the capability/bug in your own words — this
  project has tracked its entire build history against the tasklist, and
  new work should be traceable the same way even where it isn't a literal
  tasklist item.
- Confirm the test suite passes (`pytest`) and that you added tests for the
  happy path and the relevant edge cases (confirm/no-confirm for a gated
  tool, each error branch you added, etc.).
- If you changed the tool surface (added/removed/renamed a tool, or changed
  its inputs/outputs), update the README's "Available tools" list and the
  server's `INSTRUCTIONS` constant in `src/octobot_mcp/server.py` — both are
  meant to stay accurate without a live server running.
- Fill in the pull request template (`.github/PULL_REQUEST_TEMPLATE.md`) —
  it asks for exactly the above.

## Filing a bug or requesting a feature

Use the issue templates under `.github/ISSUE_TEMPLATE/` — they ask for the
OctoBot version, this server's version, and the exact tool call involved.
**Never include real exchange API keys/secrets in a public issue**, even
redacted or partial. If you believe you've found a security vulnerability
(especially anything related to credential handling or redaction), do not
open a public issue at all — see [`.github/SECURITY.md`](.github/SECURITY.md).
