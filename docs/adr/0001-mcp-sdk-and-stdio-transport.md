# ADR-0001: MCP Python SDK over stdio transport, single local user, no MCP-level auth

## Status
Accepted

## Context
The server must let an AI agent (Claude Code/Desktop) manage a single OctoBot
instance. The agent host launches the MCP server itself as a local subprocess;
there is exactly one caller, on one machine, for the lifetime of the process.
We need a transport and SDK choice that matches that deployment shape without
adding unneeded network exposure or protocol-compliance risk.

This decision was made directly with the user during intake and is treated
here as fixed; alternatives are recorded for completeness and to make the
rejection rationale auditable.

## Decision
Build the server with the **official MCP Python SDK**, using the **stdio
transport**. The MCP client (Claude Code/Desktop) spawns the server as a
subprocess and communicates over stdin/stdout using standard MCP JSON-RPC
framing. The server performs **no authentication/authorization of its own**
— the OS process boundary, and the fact that only the spawning client holds
the stdio pipes, is the entire trust boundary for v1.

## Consequences

### Positive
- Full MCP protocol compliance (tool discovery, structured errors, progress
  notifications, cancellation) via the SDK, instead of re-deriving JSON-RPC
  framing and capability negotiation by hand.
- Process lifecycle is trivially correct: the server lives and dies with the
  client's subprocess; no separate process supervision, port management, or
  TLS termination to build or operate.
- Zero attack surface added to the host machine: no listening socket, no
  credentials to protect at the MCP layer.

### Negative
- Single-client, single-process by construction: no horizontal scaling, no
  multiple simultaneous agent sessions against one server instance. Long-running
  jobs (ADR-0002) must therefore live in-process, not in an external store.
- The server's own event loop must multiplex MCP request handling with
  background async work (HTTP calls to OctoBot, Socket.IO progress watchers);
  this pushes concurrency design into the application rather than delegating
  it to a web framework.
- If the client subprocess dies unexpectedly, in-flight jobs and their state
  are lost (accepted — see ADR-0002).

### Neutral
- Adding a network transport (HTTP/SSE) later, for a remote or multi-client
  deployment, is a separate, additive change; nothing here blocks it, but it
  is out of scope until a real multi-client requirement exists.

## Alternatives considered
- **Hand-rolled JSON-RPC over stdio (no SDK).** Rejected: reinvents protocol
  framing, capability negotiation, and progress/cancellation semantics the
  official SDK already implements and keeps compatible with client updates;
  pure maintenance liability with no offsetting benefit.
- **MCP SDK with HTTP/SSE transport.** Rejected for v1: introduces a listening
  network service, which requires answering authn/authz, TLS, and multi-tenant
  questions that don't exist for a single local user. Revisit only if a
  remote or multi-agent deployment becomes a real requirement (see ADR-0004,
  ADR-0005 for related single-instance/no-auth scoping).
