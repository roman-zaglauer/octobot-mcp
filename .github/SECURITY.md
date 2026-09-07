# Security Policy

## Supported versions

This project is pre-1.0 (currently `0.1.0`, v1 of its own design docs) with a
single maintained line. Security fixes are made against the latest release
on the default branch; there is no separate long-term-support branch at this
stage.

| Version | Supported |
| ------- | --------- |
| latest on `main` | yes |
| anything older | no |

## Reporting a vulnerability

**Do not open a public GitHub issue for a security vulnerability.** This
server is designed to sit between an AI agent and a live OctoBot instance
that may hold real exchange API credentials and be able to place real
orders — a public report gives a head start to anyone who wants to exploit
the gap before a fix ships.

Instead, report it privately to:

TODO: add a maintainer contact here (e.g. a project security-report email
address, or instructions to open a private GitHub security advisory once
this repository has a GitHub remote).

Please include:

- A description of the vulnerability and its potential impact.
- Steps to reproduce it (a minimal tool call sequence, if applicable).
- The version of this server and, if relevant, the OctoBot version you
  tested against.
- **Do not include real exchange API keys/secrets in your report**, even if
  they were involved in reproducing the issue — describe the shape of the
  data instead, or use clearly fake placeholder values.

We will acknowledge your report and aim to keep you updated as we work
towards a fix. Once a fix is available, we will coordinate with you on
disclosure timing before any public write-up.

## Severity guidance specific to this project

Because this server's entire purpose is to act on a live trading bot, some
classes of bug are more severe here than "denial of service against a local
tool" would suggest elsewhere:

- **Anything that causes a full exchange API key or secret to appear in a
  tool response, log line, or error message is treated as high severity.**
  This project's requirements document (NFR-6) and invariants (INV-4 in
  `docs/specs/octobot-mcp-tool-spec.md`) commit to *never* returning or
  logging a full credential value — only a last-4-characters hint. A bug
  that breaks this guarantee (e.g. a new error path that echoes OctoBot's
  raw response text without redaction, or a new tool with a credential-like
  parameter that isn't wired into the redaction denylist described in
  `CONTRIBUTING.md`) should be reported as above, not filed publicly.
- **Anything that bypasses the `confirm=true` gate** (ADR-0003) on a
  destructive or high-risk tool (`delete_profile`, `convert_profile_to_live`,
  `update_exchange_credentials`, or any `clear_*_history` tool) — e.g. a
  code path that performs the action without the flag being set — is also
  high severity, since it removes the one safeguard against an
  agent-issued action executing an irreversible or real-money-affecting
  operation unintentionally.
- Bugs that only affect read-only tools, or that require an attacker to
  already control the OctoBot instance or the machine running this server,
  are still worth reporting privately, but are lower severity — this
  server has no authentication of its own at the MCP layer (the OS process
  boundary is the control, per ADR-0001) and is designed for a single
  trusted local user, not a multi-tenant or network-exposed deployment.

## Out of scope

This server assumes the OctoBot instance it talks to is on a trusted network
and that the MCP client (e.g. Claude Code) invoking its tools is trusted.
Vulnerabilities that require an already-compromised OctoBot instance, an
already-compromised MCP client, or a threat model of "an untrusted party can
run arbitrary tool calls against this server" are architectural limitations
of the current design (see `docs/adr/`), not bugs — though we're still happy
to hear about them privately if you think the current scope is wrong.
