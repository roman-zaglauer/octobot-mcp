# ADR-0004: Single OctoBot instance, configured via environment variable at startup

## Status
Accepted

## Context
The server is launched as a local subprocess for one user managing one
OctoBot deployment. There is no requirement today to address multiple
OctoBot instances from one MCP server process.

## Decision
The server reads its target OctoBot base URL (e.g. `OCTOBOT_BASE_URL=
http://192.168.178.100:5001`) from an environment variable at process
startup, and holds a single configured HTTP client (and single Socket.IO
client, per ADR-0002) for the process lifetime. No MCP tool accepts an
`instance` or `base_url` parameter.

## Consequences

### Positive
- Every tool's input schema is smaller and simpler (no instance-selection
  parameter to validate, document, or get wrong).
- The single-lock backtest serialization in ADR-0002 needs no per-instance
  keying — there is exactly one lock, matching the one real OctoBot instance.

### Negative
- Managing two OctoBot instances requires running two separate MCP server
  processes (two entries in the client's MCP config, each with its own
  `OCTOBOT_BASE_URL`). This is a real limitation if multi-instance becomes a
  requirement, but is explicitly acceptable per the user's stated scope.

## Alternatives considered
- **Per-tool `instance_url` parameter, server holds a pool of clients.**
  Rejected for v1: adds a connection-pool/lifecycle-management component and
  a parameter every tool call must supply, for a capability nobody asked for.
  If multi-instance is needed later, this ADR should be superseded rather
  than the parameter silently bolted on, since it also interacts with
  ADR-0002's single execution lock (would need to become per-instance) and
  ADR-0005's per-instance auth story.
