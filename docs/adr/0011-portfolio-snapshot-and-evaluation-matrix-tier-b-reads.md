# ADR-0011: Portfolio per-asset snapshot and live evaluation matrix — both Tier B, same session, one ADR

## Status
Accepted

## Context

A live UI audit this session found two real read-model gaps, neither
previously documented in `docs/`:

1. **Portfolio per-asset snapshot.** OctoBot's own `/portfolio` page shows a
   live Asset/Total/Value-in-reference-currency/Available/Locked-in-orders
   table for every currently-held currency. No existing tool returns this:
   `get_historical_portfolio_value` is a time-series of total value in one
   currency; `get_orders`/`get_positions`/`get_trades` give no current
   per-asset holdings breakdown. This session had to approximate it by
   cross-referencing three tools' outputs instead of one direct read.
2. **Live evaluation matrix.** OctoBot's own `/advanced/matrix` page shows a
   live every-evaluator/timeframe/symbol/exchange signal-value table
   (-1.0..+1.0). No existing tool exposes this. This session had to
   reconstruct equivalent data by grepping `export_logs`' `MATRIX` log
   lines — a direct read tool would have made this trivial.

**Both resolved to the same tier via the same decision framework (ADR-0006),
discovered in one live investigation session — so this is one ADR covering
both, not two.** (Contrast with ADR-0008/ADR-0009, which got separate ADRs
because they were separate sessions discovering separate capabilities over
time; here the architecturally significant finding — "Tier A doesn't exist,
Tier B does, Tier C turned out unnecessary" — is identical in kind for both
pages and was established in one sitting.)

### Investigation performed this session (live, read-only, against `http://192.168.178.100:5001`)

**Tier A check, both features — confirmed absent, not assumed.** Tried
directly, via `fetch()` executed in the actual rendered page (not a guess
from a secondhand note): `GET /api/portfolio`, `/api/portfolios`,
`/api/portfolio_assets`, `/portfolio?format=json` for the portfolio feature,
and `GET /api/matrix`, `/api/evaluation_matrix`, `/advanced/matrix_data`,
`/advanced/matrix?format=json` for the matrix feature. **Every one of the
four portfolio-route guesses returned HTTP 404 with OctoBot's own HTML
"Home" fallback page** (`Content-Type: text/html`), not JSON; the same for
all four matrix-route guesses. The `?format=json` query param is silently
ignored by both real pages (HTTP 200, unchanged HTML body) — confirming
these routes have no content-negotiation path to JSON either. No JSON API
exists for either feature.

**Portfolio per-asset snapshot — Tier B, confirmed via the page's own
network requests (chrome-devtools `list_network_requests` against
`/portfolio`, OctoBot 2.1.1).** The rendered page's own XHR traffic contains
no fetch for per-asset data at all — the asset table is embedded directly in
the initial `GET /portfolio` HTML response (confirmed: fetching `/portfolio`
again from the page's own context returns the full page, including a
populated `#holdings-table`). `GET /static/js/components/portfolio.js`
confirms the mechanism: `start_periodic_refresh()` calls jQuery's
`$("#portfolio-display").load(location.href + " #portfolio-display", ...)`
every `portfolio_update_interval` ms (confirmed live: `60000`, i.e. 60s) —
a plain re-`GET` of the *same bare page*, with the browser extracting the
`#portfolio-display` fragment client-side. There is no separate fragment
route and no Socket.IO push into this table at all.

Confirmed DOM shape (live, 2 real rows observed: EUR and BTC holdings on a
single Binance account):
- Reference currency: `<div id="portfoliosCard" class="card"
  reference_market="EUR">` — the `reference_market` attribute, confirmed
  `"EUR"` live.
- Each asset is one `<tr class="symbol-holding text-center">` inside
  `<table id="holdings-table">` (itself inside `#portfolio-display`).
- Asset symbol: `<span class="symbol">` nested in the first `<td>`
  (confirmed text `"EUR"`, `"BTC"`).
- Columns 2–4 (Total, Available, Locked in orders — in that document order)
  each carry `data-toggle="tooltip" title="<Exchange>: <amount>"` with the
  **full-precision** raw value (confirmed live: cell text `"195.908"`,
  tooltip `title="Binance: 195.90814133"` — the displayed cell text is
  rounded via `handle_rounded_numbers_display()`, confirmed by reading
  `portfolio.js`, while the tooltip is not).
- Column 3 (Value in `<reference_market>`, class
  `total-value ... sorting_1`) carries **no `title`/tooltip attribute at
  all** (confirmed by reading its exact live markup) — only the rounded
  display text is available for this one column; there is no full-precision
  source for it via this route.
- `[UNVERIFIED]` **multi-exchange tooltip format.** The live test instance
  has exactly one connected exchange (Binance), so only a single
  `"<Exchange>: <amount>"` pair was ever observed in a tooltip. How OctoBot
  renders this `title` attribute when an asset is held across ≥2 exchanges
  (comma-separated? newline-separated? one pair only, aggregated?) was not
  observed and must not be guessed — see Decision/open question below.

**Live evaluation matrix — Tier B, and the Tier-C/socket hypothesis this
ADR was asked to investigate is RULED OUT by direct evidence, not
assumed away.** The task brief flagged, correctly, that OctoBot's own logs
show matrix values streamed as "OctoBot Channel" events (Socket.IO-based)
and asked for this to be verified live rather than assumed either way. It
was: `list_network_requests` against a fresh `/advanced/matrix` page load
shows only the generic `/notifications` Socket.IO namespace connecting (the
same one connects on every OctoBot page, confirmed by comparing to the
`/portfolio` page's own network trace) — no matrix-specific namespace, no
`matrix_data`-style event ever observed. `GET
/static/js/components/advanced_matrix.js` confirms why: the entire script is
`const matrix_table = $('#matrixDataTable').DataTable();` plus a column-value
filter-dropdown initializer that reads `matrix_table.rows().data()` — i.e.
DataTables is initialized directly **from HTML rows already present in the
server-rendered response**, with no `ajax`/`data` option, no `setInterval`,
and no Socket.IO listener anywhere in the file. `GET
/static/js/common/dom_updater.js` (the shared per-page live-update script)
has no matrix-specific refresh logic either. **Conclusion: `/advanced/matrix`
is rendered fresh, server-side, from OctoBot's in-memory matrix state, on
every bare `GET` — exactly the same pattern as `/profiles_selector`
(ADR-0006), `/data_collector` (ADR-0008), and `/advanced/evaluator_config`
(ADR-0009).** The Socket.IO "OctoBot Channel"/`MATRIX` events this session
saw in `export_logs` output are OctoBot's own *internal* producer/consumer
pub-sub (how evaluators push new values into the bot's live in-memory
matrix) — a real mechanism, but an internal one this server never needs to
listen to, since a plain `GET` already returns OctoBot's current rendered
snapshot of that same in-memory state at request time. This is the one
`[UNVERIFIED→RESOLVED]` item the task explicitly called out as needing live
verification before assuming either way; it is resolved here with direct
evidence, not inferred from the log-grepping workaround alone.

Confirmed DOM shape (live, OctoBot 2.1.1, 8 real rows across 4 evaluators):
- `<table id="matrixDataTable" class="table table-striped table-sm
  table-responsive-sm dataTable">`, one `<tr>` per entry, with **plain,
  unattributed** `<td>` cells in fixed document order — confirmed via
  `advanced_matrix.js`'s own column-index constants
  (`matrix_table_evaluator_index=0`, `..._timeframe_index=2`,
  `..._symbol_index=3`, `..._exchange_index=4`; index 1 is the value column,
  implied by elimination and confirmed by the rendered header row) and
  independently confirmed by reading `<thead>`'s literal `<th>` text:
  `"Evaluator" | "Value" | "Time frame" | "Symbol" | "Exchange"`.
- Real observed row, verbatim: `<tr><td
  class="sorting_1">BBMomentumEvaluator</td><td>0.03535867173532168</td><td>4h</td><td>BTC/EUR</td><td>binance</td></tr>`.
- **Two real, legitimate non-numeric `Value`/`Time frame` cases, confirmed
  live, not hypothesized**: `InstantFluctuationsEvaluator`'s `1m` row has
  `Value` rendered as the literal text `"None"` (Python's `str(None)`,
  confirmed — this evaluator had not yet produced a value at scrape time);
  `MoveSignalsStrategyEvaluator`'s row has an **empty string** `Time frame`
  cell (confirmed — a strategy-level evaluator has no single timeframe of
  its own). Neither is a broken scrape.
- Unlike every prior Tier-B adapter in this project (`/profiles_selector`'s
  `data-id`/`id=` spans, `/data_collector`'s `tr.selectable_datafile`,
  `/advanced/evaluator_config`'s `config-type`/`config-key` attributes),
  **this table has no stable per-cell attribute to anchor on at all** — cells
  are plain, unattributed `<td>`s, positional only. The only structural
  guard available is asserting the `<thead>` column-name text matches
  exactly, in order, before parsing body rows positionally. This is a
  materially weaker coupling guarantee than every prior adapter's
  attribute-based extraction (see Risks).
- `[UNVERIFIED]` Whether the table can ever be large enough that OctoBot
  paginates it server-side (only 8 rows were observed live, well under any
  plausible page size; DataTables' own default client-side pagination
  operates on rows already present in the HTML, which is what this adapter
  reads — not evidence against server-side pagination at some larger,
  unobserved scale).

## Decision

### 1. Both features: Tier B (ADR-0006), a fifth/sixth adapter in an
   established family — not a new pattern
No new architectural category is needed. Both `/portfolio` and
`/advanced/matrix` are bare, unauthenticated, server-rendered `GET` pages
(login-disabled-compatible, per ADR-0005) that this server scrapes with
BeautifulSoup and parses fail-loud, exactly like `/profiles_selector`,
`/data_collector`, and `/advanced/evaluator_config` before them. Per this
project's standing precedence order, reliability/operability (tier 2)
again outranks "add a general scraper for every HTML-only page" — each
adapter here is scoped to exactly one page, exactly as narrowly as its three
predecessors.

### 2. New tools: `get_portfolio_snapshot()` and `get_evaluation_matrix()`
Named `get_*`, not `list_*` — matching this project's existing distinction
in spirit (the three `list_*` Tier-B tools each enumerate *named entities
with a stable id* the caller later acts on — a profile, a data file, an
evaluator; neither new tool's rows are independently addressable entities
to act on later, they are point-in-time telemetry reads, the same role
`get_orders`/`get_positions`/`get_historical_portfolio_value` already play
in the "Portfolio & trading" domain). Both take no input — the live
`?format=json` test above confirms neither underlying route accepts any
server-side filter; any filtering (by asset, by evaluator/timeframe/symbol)
is left to the calling agent over the full returned list, exactly as
OctoBot's own UI does it client-side via DataTables/select2.

### 3. Fail-loud gates, each distinct from every existing one (ADR-0003's
   `require_confirmation` convention does not apply here — both tools are
   read-only)
- **`get_portfolio_snapshot`** raises `PortfolioScrapeContractBrokenError`
  if `#holdings-table`/`#portfolio-display` is missing from the response, or
  a row is missing its `.symbol` span, or the `reference_market` attribute
  is missing from `#portfoliosCard` — **never** because `assets` is empty
  (an OctoBot instance with zero holdings anywhere is a legitimate state,
  unlike `list_profiles`' "OctoBot always ships built-in profiles"
  invariant).
- **`get_evaluation_matrix`** raises `EvaluationMatrixScrapeContractBrokenError`
  if `#matrixDataTable` is missing, or its `<thead>` column-name text doesn't
  match `["Evaluator", "Value", "Time frame", "Symbol", "Exchange"]`
  exactly, in order, or any row has a cell count different from 5 — **never**
  because `entries` is empty (a freshly started OctoBot, or one with no
  active evaluators, legitimately shows zero rows; this is a materially
  different, weaker invariant than `list_profiles`'/`list_evaluators`' "zero
  is always broken," because there is no equivalent "OctoBot always ships
  at least one" guarantee for live evaluation entries).

### 4. Per-exchange precision: parse the tooltip opportunistically, degrade
   to rounded-only, never fabricate
`get_portfolio_snapshot`'s `total`/`available`/`locked_in_orders` fields
each carry both a `rounded` value (the displayed cell text, always present)
and an `exact`/`per_exchange` breakdown parsed from the `title` tooltip,
populated **only** when the tooltip parses as one or more clean
`"<Exchange>: <amount>"` pairs. Given the `[UNVERIFIED]` multi-exchange
separator format above, a tooltip that doesn't parse cleanly degrades to
`exact: null, per_exchange: null` rather than raising or guessing a split —
this is a graceful, honest degradation (mirroring NFR-3/NFR-8's "null over
a fabricated guess" ethic project-wide), not a scrape-contract break, since
the `rounded` value is still correctly extracted either way.
`value_in_reference_currency` carries **only** `rounded` — there is no
tooltip for this column on this OctoBot version (confirmed live), so there
is nothing to parse; the tool's own docstring must say this plainly rather
than silently omitting an `exact` key some callers might otherwise expect
by analogy with the other three fields.

### 5. `get_evaluation_matrix`'s `value` field: typed passthrough, not a
   forced float
`value` is parsed as a JSON number when the cell text is numeric, `null`
when the cell text is exactly the literal string `"None"` (confirmed real
case), and — for any other non-numeric, non-`"None"` text (not observed
live, but not ruled out either) — passed through as the raw string rather
than dropped or coerced. This is the same "never invent, never silently
drop" discipline NFR-8 already established for backtest report fields,
applied here to a field this adapter cannot fully enumerate the value space
of from one live session.

## Consequences

### Positive
- Closes two confirmed, real gaps with the smallest change that makes each
  usable: one page, one adapter, one fail-loud gate each — no general HTML
  scraping framework introduced.
- The Tier-C/socket-listener pattern the task asked this ADR to seriously
  consider for the matrix feature was investigated, not assumed, and is
  conclusively ruled out by direct network/script evidence — this server
  gains no new, genuinely novel "open a socket, wait, return last-seen"
  pattern it would otherwise have had to design, test, and maintain for the
  first time (which would have been a materially bigger addition, closer to
  ADR-0002's async job machinery, than a sixth Tier-B adapter is).
- Both adapters reuse the exact BeautifulSoup/fail-loud/distinct-exception-
  class idiom this project already has three working, tested precedents
  for — no new idiom to review or maintain.

### Negative
- Two more Tier-B adapters add to this project's standing, acknowledged
  maintenance liability (ADR-0006's own "Negative" consequences) — six
  scrape adapters now couple this server to six different pieces of
  OctoBot's HTML structure, each needing its own smoke test on OctoBot
  upgrades.
- `get_evaluation_matrix`'s adapter is **structurally weaker** than every
  prior Tier-B adapter in this project: it has no stable per-cell attribute
  to anchor on (unlike `data-id`, `tr.selectable_datafile`, or
  `config-key`), so its only defense against a future column-reorder is the
  `<thead>` text-match check (Decision 3). A column reorder that also
  renamed headers identically (unlikely, but not impossible) could slip
  through undetected. Accepted: no stronger signal exists on this page to
  build on, and the text-match check still catches the overwhelmingly more
  likely failure mode (a column added, removed, or reordered without a
  matching header-text change).
- `get_portfolio_snapshot`'s per-exchange precision (Decision 4) is
  genuinely unverified for the multi-exchange case — any caller relying on
  `exact`/`per_exchange` being populated for a multi-exchange holding may
  see `null` instead, through no fault of a broken scrape, until a developer
  verifies the real separator format against a multi-exchange account (see
  open question below).
- Neither adapter is load-bearing for a write path the way `list_profiles`
  is for `delete_profile`/`select_profile` — both are pure telemetry reads —
  so a scrape-contract break here is lower-stakes than for the three
  existing adapters, but still real (a silent wrong answer would mislead an
  agent reasoning about live holdings or live strategy signals).

## Risks
| Risk | Severity | Likelihood | Mitigation |
|---|---|---|---|
| `get_evaluation_matrix`'s positional-only parsing (no stable per-cell attribute exists on this page) silently mis-maps columns if OctoBot reorders them without changing header text | Medium | Low | The `<thead>` text-match assertion (Decision 3) catches the overwhelmingly likely case (reorder + rename together); a smoke test re-run on OctoBot upgrades, per this project's existing scrape-adapter convention, is the only further mitigation available on this page's structure |
| `get_portfolio_snapshot`'s multi-exchange tooltip format is unverified; `exact`/`per_exchange` silently (but honestly) degrades to `null` for any holding whose tooltip doesn't match the single confirmed single-exchange pattern | Low | Medium (any real multi-exchange account) | Graceful `null` degradation (Decision 4) rather than a guess or a raised error; `[UNVERIFIED]` flagged in the open questions below for a developer with multi-exchange access to resolve before relying on this field in a multi-exchange deployment |
| OctoBot renders the evaluation matrix table differently at a much larger scale (hundreds of evaluator/timeframe/symbol/exchange combinations) than the 8-row live sample this ADR verified against — possible server-side pagination not exercised | Low | Low | `[UNVERIFIED]`; flagged in open questions; the fail-loud header-check (Decision 3) still holds regardless of row count, so a paginated response would at worst return a partial-but-correctly-parsed `entries` list rather than a silently wrong one |
| Both pages' rendered structure changes on a future OctoBot version | Medium | Medium | Same standing mitigation as every other Tier-B adapter: fail-loud parsing, a smoke test against a real instance on OctoBot upgrades, last-verified version (2.1.1) recorded in each adapter's own docstring |

## Alternatives considered
- **Tier C (open a Socket.IO connection, wait briefly, return the last-seen
  push) for the evaluation matrix**, the pattern the task brief flagged as a
  real possibility given OctoBot's internal "OctoBot Channel"/`MATRIX`
  pub-sub. **Rejected, on direct evidence, not as a judgment call**: no such
  push ever reaches the browser for this page (Context, above) — a plain
  `GET` already returns a fresh server-rendered snapshot of the same
  in-memory state that internal channel feeds. Building a socket listener
  here would be strictly more complex than Tier B for strictly less
  freshness than Tier B already provides (a `GET` reflects OctoBot's matrix
  state at the moment of the call; a socket listener that "waits briefly"
  for pushes would, at best, match that and, at worst, time out having
  observed nothing if no evaluator happened to tick during the wait window).
- **Cross-referencing existing tools instead of a dedicated read**, i.e.
  leaving both gaps unaddressed on the grounds that `get_orders`/
  `get_positions`/`get_historical_portfolio_value` (for portfolio) or
  `export_logs`-grepping (for the matrix) are technically sufficient.
  Rejected: both are the exact real workarounds this session had to perform
  by hand before writing this ADR, confirming they are inadequate
  substitutes for a direct read, not a hypothetical convenience.
- **One combined `get_dashboard_snapshot()` tool returning both portfolio
  and matrix data.** Rejected: the two pages are unrelated domains (trading
  holdings vs. strategy/evaluator signals) that happen to share only "Tier B,
  found the same session" — bundling them would violate this project's
  consistent preference for small, single-purpose tools over bundled
  workflows (ADR-0010 Decision 6 makes the same call for an unrelated pair
  of tools), and would force a caller interested in only one domain to pay
  for scraping both pages on every call.
- **A JSON-API "discovery" pass across more undocumented routes while this
  session was already investigating two.** Out of scope per the task's own
  explicit scope boundary (real-trading toggle, exchange add/remove,
  interfaces, notifications, Automations, per-profile currency/exchange
  config are excluded from this pass) — not investigated here beyond noting,
  as an aside, that `#portfoliosCard`'s `reference_market` attribute is
  sourced from the profile's own currency config, the same config surface
  the task explicitly deferred; no tool is proposed here that would let a
  caller change it, only read it.

## Open questions (flagged, not guessed)
15. **Multi-exchange format of `get_portfolio_snapshot`'s per-column
    `title` tooltip** — confirmed live only for a single-exchange holding
    (`"Binance: 195.90814133"`). Whether a holding split across ≥2
    exchanges renders as multiple comma/semicolon/newline-separated pairs,
    or some other shape, is unconfirmed. A developer with access to a
    multi-exchange OctoBot account must verify this before the
    `exact`/`per_exchange` fields can be considered reliable for that case
    — until then, Decision 4's graceful `null` degradation is the shipped
    behavior, not a placeholder to silently remove later.
16. **Whether `/advanced/matrix` is ever server-side paginated at scale** —
    only 8 real rows were observed live; whether OctoBot truncates or pages
    this table server-side once an instance has many more active
    evaluator/timeframe/symbol/exchange combinations was not, and could not
    safely be, verified this session (would require configuring many more
    evaluators/symbols on the live trading instance). A developer should
    confirm this is a non-issue (or handle it) before treating
    `get_evaluation_matrix`'s result as a complete snapshot on a
    heavily-configured instance.

## Downstream artefacts (written alongside this ADR, not separately later)
- `docs/requirements/octobot-mcp-requirements.md`: FR-33/FR-34, NFR-19/NFR-20,
  open questions 15–16 (above).
- `docs/specs/octobot-mcp-tool-spec.md`: a new "Portfolio snapshot &
  evaluation matrix" section with both tools' full contracts.
- `docs/tasklist.md`: one new milestone (20), implementable directly from
  the spec section above — live verification is already complete (this
  ADR's own Context), unlike prior addenda's own "step 0, verify first"
  pattern.
