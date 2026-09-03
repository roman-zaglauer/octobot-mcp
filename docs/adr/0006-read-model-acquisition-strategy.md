# ADR-0006: Read-model acquisition — JSON API where it exists, one narrow HTML-scrape adapter where it doesn't, explicit non-goals elsewhere

## Status
Accepted

## Context
This decision was **not** one of the user's pre-made constraints — it emerged
from verifying OctoBot's actual routes against source (master branch) rather
than assuming a REST API exists everywhere the UI has a page. Confirmed
findings:

- **Real JSON API exists** for: portfolio/orders/trades/positions/pnl
  history (`api/trading.py`), currency/symbol lookups and
  compatible-accounts checks (`api/config.py`, `api/exchanges.py`), backtest
  lifecycle actions and final report (`controllers/backtesting.py`), profile
  **mutations** — update/duplicate/remove/import/download/export/
  use_as_live (`controllers/configuration.py`'s `/profiles_management/<action>`
  dispatcher, confirmed by reading the literal function body), and tentacle
  config read/write (`config_tentacle`, `config_tentacles`).
- **No JSON API exists** for: listing all profiles / getting the current
  profile (`controllers/profiles.py`'s `/profiles_selector` only renders a
  Jinja template — `profiles`, `current_profile`, `cloud_strategies` etc. are
  template variables, not a JSON response), and logs
  (`controllers/logs.py`'s `/logs` likewise only renders `logs.html`;
  `/export_logs` returns a zip file, which *is* usable directly, but there is
  no JSON log-listing route). A full "list all configured exchanges" JSON
  route also does not exist — only `/api/first_exchange_details`
  (name/id of one exchange) and `/api/are_compatible_accounts` were found.

Reads that only exist as HTML pages are a real gap against the product
requirement to manage profiles (an agent needs to discover profile
ids/names before it can act on them) — but building a scraper is a
maintenance liability (fragile to OctoBot UI version changes) that must be
weighed against reliability/operability. This project applies a standing
precedence order across all its ADRs when principles conflict: (1)
compliance/security and explicit non-functional requirements, (2)
reliability & operability — design for failure, day-2 operability, (3)
simplicity & maintainability, (4) extensibility & reuse, (5) aesthetics.
Reliability/operability (tier 2) outranks maximizing curated-scope coverage,
which is otherwise just a functional-requirement breadth concern (no fixed
tier of its own — it's weighed case by case against whichever tier above
applies).

## Decision
Three-tier strategy, scoped narrowly:

1. **Tier A — JSON API (preferred, all mutations and most reads):** implement
   directly against the confirmed JSON routes. This is the large majority of
   the tool surface.
2. **Tier B — one narrow, fail-loud HTML-scrape adapter, `/profiles_selector`
   only:** because profile discovery is load-bearing for the core "manage
   profiles" requirement, v1 scrapes exactly this one page (via
   BeautifulSoup, targeting the specific template variables/DOM nodes the
   source shows are rendered) to back `list_profiles`/`get_current_profile`.
   The adapter is designed to **fail loudly** (raise a clear "profile-scrape
   contract broken — verify against current OctoBot version" error) rather
   than silently return a wrong/partial shape if its assumptions about the
   page structure stop holding. The tested OctoBot version/commit is recorded
   in the adapter's docstring and revisited on OctoBot upgrades.
3. **Tier C — explicitly not implemented in v1:** `get_logs` and
   `list_tentacles` (enumerate-all, not per-tentacle-config) return a
   structured "not available in v1 — no JSON contract found; use the OctoBot
   web UI directly, or `export_logs`/`get_tentacle_config` for the
   JSON-backed alternative" result rather than a scraper. These are lower
   priority in the user's own domain-scope ordering ("logs/tentacles" listed
   last) and do not block the core profile/backtest requirements.

## Consequences

### Positive
- Scraping surface is bounded to one page/one adapter — the smallest change
  that makes profile management actually usable end-to-end, rather than
  building a general-purpose scraper for every HTML-only page.
- Failure mode on scrape-contract drift is loud and specific, not a silent
  wrong answer — critical since this data feeds an autonomous agent's
  decisions (e.g. which profile_id to delete).
- Tier C tools remain *discoverable* (they exist in the tool list and explain
  the gap) rather than silently missing, so the agent/user understands the
  limitation instead of assuming the capability doesn't exist at all.

### Negative
- The one Tier-B adapter is still a real, if bounded, maintenance liability:
  it couples the MCP server to OctoBot's HTML structure for exactly the
  paths it covers, and needs a smoke test to catch drift on OctoBot upgrades.
- Logs and full tentacle listing are genuinely unavailable in v1 — flagged
  to the user as a real capability gap, not just a low-priority nice-to-have.

## Risks
| Risk | Severity | Likelihood | Mitigation |
|---|---|---|---|
| OctoBot UI upgrade changes `/profiles_selector`'s rendered structure, silently breaking the scrape | Medium | Medium | Fail-loud parsing (assert expected nodes exist before returning), a smoke test run against a real instance in CI/pre-release, version pin noted in adapter docstring |
| Exact DOM/variable shape of `/profiles_selector` was not verified against the *live* instance (only against Jinja template variable names in source) | Medium | Medium | Developer task: fetch the live instance's rendered `/profiles_selector` HTML (browser devtools or an HTTP client capable of reaching the LAN IP) and confirm the scrape targets before implementing Tier B — do not implement against source-only assumptions |

## Alternatives considered
- **Scrape every HTML-only page needed for full curated-scope reads** (logs,
  tentacles list, exchange list, etc.). Rejected: multiplies the fragile
  surface for marginal value; violates reliability/operability precedence
  over "cover everything."
- **Implement nothing beyond confirmed JSON routes; profile listing/current
  profile also out of scope for v1.** Rejected: profile discovery is not a
  nice-to-have here, it's a precondition for the user's top-line requirement
  ("agent should be able to change profiles") — without it, `update_profile`/
  `delete_profile` are only usable if the human already knows the exact
  `profile_id`, which defeats the point of agent-driven management.
- **Import OctoBot's tentacles package in-process and call `models.*`
  functions directly instead of scraping over HTTP.** Rejected: breaks the
  "wraps the web/HTTP API" architecture (ADR-0004), ties the MCP server's
  Python/dependency versions to OctoBot's own, and doesn't work once OctoBot
  runs on a different host/container than the MCP server — which the
  env-var-configured base URL explicitly allows for.
