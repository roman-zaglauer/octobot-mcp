---
name: Bug report
about: Report something that isn't working as expected
title: "[Bug] "
labels: bug
assignees: ''
---

**Do not include real exchange API keys/secrets anywhere in this report,**
even redacted or partial — this is a public issue tracker. Use clearly fake
placeholder values if you need to show the shape of a credential-related
argument. If you believe you've found a security vulnerability (especially
anything related to credential handling or redaction), stop here and see
[`.github/SECURITY.md`](../SECURITY.md) instead of filing a public issue.

## Versions

- OctoBot version (e.g. from `GET /api/version`, or the OctoBot UI's own
  version display):
- This server's version (`octobot-mcp`'s version, e.g. from `pyproject.toml`
  or `pip show octobot-mcp`):
- Python version:

## The tool call that failed

The exact tool name and arguments you called (with any real credentials
replaced by placeholders):

```
tool: <tool_name>
arguments: { ... }
```

## Expected behavior

What you expected to happen.

## Actual behavior

What actually happened — include the exact error message/response returned,
and, if available, the server's log output for that call.

## Additional context

Anything else that might help (OctoBot instance configuration relevant to
the bug, whether it's reproducible every time, etc.).
