# ADR-0005: v1 targets no-auth OctoBot instances only; session-login is a defined extension point

## Status
Accepted

## Context
Verified via OctoBot source (`web_interface/login/web_login_manager.py`,
master branch): every web route is decorated
`@login.login_required_when_activated`, which checks a runtime-toggle flag
(`_IS_LOGIN_REQUIRED`, set via `set_is_login_required()`) OR whether the
community `Authenticator` requires it. Login itself is **Flask-Login
session-cookie based**, password-only (no username field — validated against
either the community account email or a locally stored password hash), with
IP-based lockout after repeated failures. The test instance has this toggle
disabled, and the product decision (made directly with the user) is to
support only no-auth instances in v1.

## Decision
v1 assumes `login_required_when_activated` is **disabled** on the target
OctoBot instance. The server's HTTP client makes unauthenticated requests
and does not implement a login flow, cookie jar persistence, or credential
storage for OctoBot's own login.

The extension point for future support is named now, not deferred silently:
a future version would (a) accept an OctoBot login password via environment
variable or OS keychain — never as a tool parameter, to avoid it appearing in
MCP transcripts/logs — (b) perform `POST` to OctoBot's login endpoint once at
startup, (c) retain the resulting session cookie in the shared `httpx.Client`
(and separately, the Socket.IO client would need the same cookie for its
handshake), and (d) surface a clear "authentication expired/failed" error
class distinct from generic connectivity errors, since a 302-to-login
response looks different from a network timeout and tools must not
misreport one as the other.

## Consequences

### Positive
- v1 scope is honest about a real limitation instead of silently failing
  against a login-enabled instance with a confusing error.
- The extension point is concrete enough that adding login support later is
  additive (new startup step + cookie-aware client), not a redesign of the
  HTTP layer.

### Negative
- The server cannot manage any OctoBot instance that has login enabled,
  including any instance protected because it's reachable beyond a trusted
  LAN — this is a meaningful security-posture constraint on where this
  server can safely be pointed, and should be stated in user-facing docs,
  not just this ADR.

## Alternatives considered
- **Build session-login support now.** Rejected for v1: no test instance with
  login enabled was available to verify the flow against (the live instance
  has it off), and guessing at cookie/CSRF handling for a security-sensitive
  path without verification would violate the "don't present a guess as
  fact" constraint on this design work. Revisit once a login-enabled instance
  is available to validate against.
