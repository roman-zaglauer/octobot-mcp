## What changed and why

<!-- Describe the change and the motivation for it. -->

## Related tasklist item / capability

<!--
If this addresses a docs/tasklist.md milestone, name its number (e.g.
"docs/tasklist.md item 12"). If it's new work not tracked there, describe
the capability or bug in your own words -- see CONTRIBUTING.md's "Opening a
pull request" section for why this project tracks changes this way.
-->

## Checklist

- [ ] `pytest` passes locally, and I added tests covering the happy path and
      relevant edge cases (e.g. confirm/no-confirm for a gated tool, each new
      error branch).
- [ ] Any OctoBot route/API claim in this PR (new tool, changed request/response
      shape) was verified against
      [OctoBot's source](https://github.com/Drakkar-Software/OctoBot) (or
      reproduced live against a real instance), not assumed -- and that
      verification is recorded in the relevant docstring. See
      CONTRIBUTING.md's "Never guess an OctoBot route" section.
- [ ] Any new destructive/high-risk tool (deletes data, writes exchange
      credentials, or enables live trading) is gated with
      `require_confirmation` (ADR-0003), or this PR doesn't add one.
- [ ] Any credential-shaped new tool parameter is covered by the redaction
      convention in `_shared.py` (NFR-6/INV-4), or this PR doesn't add one.
- [ ] If the tool surface changed (added/removed/renamed a tool, or changed
      its inputs/outputs), the README's "Available tools" list and
      `src/octobot_mcp/server.py`'s `INSTRUCTIONS` constant were updated to
      match.
