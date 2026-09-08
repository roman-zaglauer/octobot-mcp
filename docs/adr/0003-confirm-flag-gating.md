# ADR-0003: Explicit `confirm=true` gating for destructive/high-risk operations

## Status
Accepted

## Context
The MCP server exposes full CRUD/action coverage over a curated set of
OctoBot resources. Some actions are irreversible or materially increase risk
(real-money exposure). An LLM agent can construct and issue tool calls
autonomously, sometimes from an ambiguous instruction ("clean up old
profiles"); a single misinterpreted call executing a destructive action
immediately is a correctness *and* safety issue, not just a UX nicety.

## Decision
Every tool classified as destructive or high-risk declares an optional
`confirm: boolean` input parameter (schema default `false`, not required —
omitting it is valid input, not a schema error). At the application layer:
- If `confirm` is not exactly `true`, the tool **does not perform the
  action** and returns a structured refusal result (not a thrown/protocol
  error) that (a) names the exact action and target, (b) states why
  confirmation is required, and (c) tells the caller to re-issue the same
  call with `confirm=true`.
- If `confirm=true`, the action proceeds normally.

Tools requiring `confirm=true` (full rationale and mapping to OctoBot routes
in the spec):
- `delete_profile` — irreversible deletion of a profile.
- `convert_profile_to_live` — gated originally as "the closest confirmed
  signal to enabling real-money trading"; **a later session confirmed
  against OctoBot source that this framing was wrong** — the tool only
  relabels `profile.profile_type` and selects the profile, and does NOT
  touch `config.trader.enabled`, the flag OctoBot's own `is_real_trading()`
  actually checks (requirements doc open question #5, resolved). Kept
  gated anyway: it still persistently changes the profile and its
  selection, and — precisely because of this finding — a caller could
  otherwise be misled into believing real trading is now active when it is
  not. See `convert_profile_to_live`'s own docstring for the full account.
  No tool in this project's scope can actually toggle real-vs-simulated
  trading as of this writing.
- `update_exchange_credentials` — writes exchange API key/secret.
- `clear_orders_history`, `clear_trades_history`, `clear_portfolio_history`,
  `clear_transactions_history` — irreversible deletion of trading history.

Tools deliberately **not** gated, with rationale: `stop_backtest`/`cancel_job`
(reversible, no data loss), `select_profile` (reversible selection change,
not deletion or live-trading), `cancel_order`/`close_position` (routine
trading actions inherent to using a trading bot, not in the user's named
destructive category — flagged in the spec as a judgment call the user can
revisit), `create_profile`/`update_profile`/`update_trading_config` (mutate
but do not destroy or expose credentials/live-trading).

## Consequences

### Positive
- Predictable, uniform pattern: one shared server-side helper
  (`require_confirmation(action, target, reason)`) implements the check and
  the refusal message shape once, used by every gated tool — no per-tool
  bespoke logic to get wrong.
- The refusal message is designed to be *actionable by the agent*, not just
  a hard stop: the agent can read it and decide, based on its own
  instructions/context, whether to re-call with `confirm=true` — keeping the
  human, via the agent's judgment, in the loop for irreversible actions.

### Negative
- Every destructive action costs one extra round trip even when the caller
  already "meant it" (e.g. a human explicitly asked to delete a specific
  profile). Accepted: the cost (one extra tool call) is small relative to the
  benefit (no accidental irreversible action from a misread instruction).
- The gated-tool list is a judgment call, not derived mechanically from
  OctoBot's API — it must be revisited if new destructive routes are added
  to the curated scope later (tracked as a maintenance note in the spec).

## Alternatives considered
- **`confirm` required with no default (schema-level enforcement).** Rejected:
  this turns an omitted flag into a raw JSON-schema validation error with a
  generic message, which is a worse experience for the agent than a
  structured, explanatory refusal it can reason about and act on.
- **Two-step confirmation via a separate `confirm_action(token)` tool**
  (first call returns a confirmation token, second call redeems it).
  Rejected as over-engineered for a single local user with no concurrent
  callers: it adds a token-management state machine for no additional safety
  over a same-call `confirm=true` flag, since there is no multi-actor race to
  protect against.
