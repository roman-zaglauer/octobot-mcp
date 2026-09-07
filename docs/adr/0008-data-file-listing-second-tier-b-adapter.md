# ADR-0008: `list_data_files` as a second, narrowly-scoped Tier-B HTML-scrape adapter, extending ADR-0006

## Status
Accepted

## Context
ADR-0006 established a three-tier read-model acquisition strategy — Tier A
(real JSON API), Tier B (exactly one narrow, fail-loud HTML-scrape adapter,
`/profiles_selector`), Tier C (explicit "not available" stubs) — and
deliberately bounded Tier B to that single page, weighing "reliability &
operability" (this project's standing precedence tier 2) against maximizing
curated-scope coverage. This ADR applies that **same framework** to a second
candidate uncovered while designing historical-data-collection tools
(ADR-0007): listing which historical data files already exist on the
OctoBot instance.

Verified this session against OctoBot source
(`Services/Interfaces/web_interface/{controllers,models}/backtesting.py`,
`templates/data_collector.html`, `OctoBot-Tentacles` `master` branch):

- `models.get_data_files_with_description()` is rendered into
  `data_collector.html` on **every** GET and POST response to
  `/data_collector` (both the plain-GET full-page branch and the
  `import_data_file` POST branch) — there is no JSON-returning branch of
  this route for it anywhere in the controller (confirmed by reading the
  full function body: the only JSON `flask.jsonify(...)` branches are
  `symbol_list`/`available_timeframes_list`, both scoped to a single
  `exchange`, never the file listing). This is structurally identical to
  ADR-0006's `/profiles_selector` finding.
- The rendered template's exact relevant markup (fetched and read this
  session, not merely inferred from Jinja variable names):
  ```html
  {% for file, description in data_files %}
      <tr class="selectable_datafile">
          <td>{{", ".join(description.symbols)}}</td>
          {% if description.start_timestamp %}
              <td data-order="{{description.timestamp}}">
                  {{description.start_date}} to {{description.end_date}}
              </td>
              <td>Full</td>
          {% else %}
              <td data-order="{{description.timestamp}}">
                  {{description.date}}
              </td>
              <td>{{description.candles_length}}</td>
          {% endif %}
          <td>{{description.exchange}}</td>
          <td>{{", ".join(description.time_frames)}}</td>
          <td>{{file}}</td>
          <td class="text-center">
              <a class="... delete_data_file ..." data-file={{file}} ...>
                  <i class="fas fa-trash"></i>
              </a>
          </td>
      </tr>
  {% endfor %}
  ```
  This is a plain, bounded `<table>`/`<tr class="selectable_datafile">`
  structure with a fixed, small number of `<td>` columns and one attribute
  (`data-file`) carrying the exact identifier `delete_data_file`'s
  underlying route consumes verbatim (`models.get_delete_data_file(file_name)`
  passes `file_name` straight through to `backtesting_api.delete_data_file
  (file_name)` with no transformation). **This is a materially more
  verified starting position than ADR-0006 had for `/profiles_selector` at
  its own decision time**: that adapter was approved based on Jinja
  *variable names* read from the controller alone, with the actual rendered
  DOM explicitly flagged as unverified (ADR-0006's own Q1/risk table); here,
  the literal template markup itself was read this session.
- Why this matters *specifically* for the data-collection milestone (beyond
  the pre-existing `start_backtest(mode="data_files")` gap this ADR also
  closes): `start_data_collection` (ADR-0007) itself returns no filename
  anywhere in its response or job result — OctoBot's `collect_data_file`/
  `get_data_collector_status` never name the resulting file. Without some
  way to list files, an agent that just collected new historical data has
  no way to discover what it now has, making `start_backtest(mode=
  "data_files")` un-drivable end-to-end for exactly the data the agent
  itself just collected — the same "precondition for the actual, adjacent
  requirement to be usable" argument ADR-0006 used to justify Tier B over
  Tier C for profile listing, arguably stronger here since it blocks
  consuming this very milestone's own new capability.
- Two other values are embedded in the same template
  (`other_ccxt_exchanges`/`get_other_history_exchange_list()`,
  `full_candle_history_ccxt_exchanges`/`get_full_candle_history_exchange_list()`)
  — supplementary "which exchanges support full vs. partial candle history"
  discovery aids, not load-bearing for any MUST-have tool in this milestone
  (`start_data_collection`'s `exchange` field does not require this
  distinction to function, and the already-shipped `get_currency_list`/
  `get_all_currencies`/`get_all_symbols` tools already give an agent
  workable exchange context). These are treated as Tier C (explicit
  non-goal) below, not scraped.

## Decision
1. **`list_data_files()` is a second Tier-B scrape adapter**, targeting
   `GET /data_collector` (the bare route, no `action_type` — the same route
   family already used, Tier A, for `symbol_list`/`available_timeframes_list`)
   and parsing the `<tr class="selectable_datafile">` rows shown above via
   BeautifulSoup, the same library/technique ADR-0006's adapter already
   uses.
2. **Output shape**, one entry per row, both template branches represented
   honestly rather than normalized into a lossy common shape:
   ```
   {"files": [
     {
       "file": str,                    # the delete-button's data-file value — the exact identifier delete_data_file expects
       "exchange": str,
       "symbols": [str],
       "time_frames": [str],
       "is_full": bool,                # true iff description.start_timestamp was truthy (the "Full" branch)
       "start_date": str | null,       # populated only when is_full
       "end_date": str | null,         # populated only when is_full
       "date": str | null,             # populated only when NOT is_full
       "candles_length": int | null    # populated only when NOT is_full
     }, ...
   ]}
   ```
3. **Fail-loud, mirroring INV-5/NFR-12 exactly, via a distinct exception
   class.** A new `DataFileScrapeContractBrokenError` (named in parallel
   to, and never conflated with, `ProfileScrapeContractBrokenError` — this
   project's established convention is one domain-specific exception class
   per adapter, not a shared generic one) is raised the moment an expected
   structural assumption doesn't hold (e.g. the `<table>`/row class isn't
   found at all, a row is missing an expected `<td>`, or the delete
   button's `data-file` attribute is absent) — never a silent empty/partial
   list indistinguishable from "no data files exist yet."
4. **Feeds `delete_data_file`'s input.** The `file` field is documented as
   the authoritative value to pass to `delete_data_file(file)` — closing a
   second, related gap (an agent otherwise has no way to know what
   identifier `delete_data_file` expects either).
5. **This explicitly extends, not silently contradicts, ADR-0006's "bounded
   to one page" framing.** ADR-0006's negative-consequences section already
   named the one adapter as "a real, if bounded, maintenance liability";
   this decision knowingly doubles that bounded surface to two adapters,
   for the reasons above (load-bearing for two MUST-have workflows, and a
   materially lower structural-risk starting point than ADR-0006's own
   adapter had at its decision time). A **third** Tier-B candidate, if one
   ever arises, should not be added by the same ad hoc reasoning — it
   should prompt a fresh ADR re-examining whether this project's HTML-
   scraping strategy itself needs to change (e.g. a shared scraping utility
   module, or reconsidering the "wrap the HTTP API only" architecture,
   ADR-0004's alternatives-considered) rather than accreting adapters one
   at a time.
6. **The two supplementary exchange-list values
   (`other_ccxt_exchanges`/`full_candle_history_ccxt_exchanges`) remain
   Tier C — explicitly out of scope**, for the reasons in Context above.
   No stub tool is added for them at all (unlike `get_logs`/`list_tentacles`,
   which ARE discoverable stubs per ADR-0006): they were never a named
   requirement anywhere, curated-scope discipline (this project's own
   stated practice of not inventing requirements silently) applies, and the
   existing exchange tools already give an agent workable context.

## Consequences

### Positive
- Closes a real, already-admitted gap in a shipped MUST-have tool
  (`start_backtest(mode="data_files")`'s own docstring) and makes
  `start_data_collection`'s output actually consumable by an agent, without
  which this entire milestone's "collect then backtest against it" workflow
  is not achievable end-to-end via agent-driven discovery alone.
- Reuses an already-proven technique/dependency (BeautifulSoup, the
  fail-loud discipline, the distinct-exception-per-adapter convention) —
  the *pattern* is not new, only its second application is.
- `delete_data_file`'s own input-discovery gap is closed by the same
  adapter, at no extra cost.

### Negative
- Doubles this project's HTML-scraping surface (one adapter → two),
  compounding the exact maintenance liability ADR-0006 already flagged as a
  negative consequence, now across two independent pages that can each
  drift independently on an OctoBot UI upgrade.
- The live-instance rendered structure has still not been directly
  round-trip verified through this project's own scraper this session
  (only the Jinja template source was read) — the same class of residual
  risk ADR-0006's own Q1 carried before its adapter was implemented and
  tested; the exact runtime type of `{{file}}` (a bare filename string vs.
  a `str()`-rendered path-like object with a directory prefix) is
  specifically unconfirmed (see requirements doc Q7).

## Risks
| Risk | Severity | Likelihood | Mitigation |
|---|---|---|---|
| OctoBot UI upgrade changes `data_collector.html`'s rendered structure, silently breaking the scrape | Medium | Medium | Same mitigation as ADR-0006: fail-loud parsing, a smoke test against a real instance, version pin noted in the adapter's docstring |
| The exact runtime value/type behind `{{file}}` (bare filename string vs. a path-like object whose `str()` form includes a directory component) is unconfirmed | Medium | Medium | Developer verification task: fetch the live instance's rendered `/data_collector` page (or inspect `backtesting_api.get_all_available_data_files()`'s return type from `octobot_backtesting`, out of this session's scope) before finalizing the scrape's `file`-field extraction and before wiring `delete_data_file` against its output |
| Two independent scrape adapters now exist with no shared parsing utility, risking copy-pasted-and-drifted fail-loud logic between them | Low | Medium | Not addressed in this ADR (kept as two small, independent, per-page adapters — consistent with this project's own established "small per-module duplication over premature shared abstraction" convention, e.g. `_request_raw`/`_extract_error_text` in `profiles.py` vs. `backtesting.py`); revisit if a third adapter is ever proposed (see decision point 5) |

## Alternatives considered
- **Tier C stub** (`list_data_files` returns a structured "not available in
  v1" result, same shape as `get_logs`/`list_tentacles`). Rejected: unlike
  logs/tentacle-listing — which the user's own domain-scope ordering placed
  last and which do not block any MUST-have tool — file discovery is a hard
  precondition for two MUST-have workflows (`start_backtest(mode=
  "data_files")`, already shipped, and this milestone's own
  `start_data_collection`). Choosing Tier C here would leave both
  practically undrivable via agent-driven discovery, the exact
  disqualifying argument ADR-0006 already used to reject Tier C for profile
  listing.
- **Derive the resulting filename deterministically from
  `start_data_collection`'s own input parameters** instead of scraping
  (e.g. assume a `<exchange>_<symbols>_<timeframes>.data` naming
  convention). Rejected: OctoBot's actual file-naming convention was not
  confirmed from source this session (would require reading the historical
  data collector's own file-writing code, e.g. in `octobot_backtesting`,
  out of scope for this session) — encoding an assumed naming scheme would
  be exactly the kind of guessed fact this project's own discipline
  forbids (see the requirements doc's "Open questions (flagged, not
  guessed)" convention). Scraping the instance's own authoritative listing
  is more honest than assuming a convention.
- **Scrape every HTML-only value in `data_collector.html`**, including the
  two supplementary exchange lists (decision point 6's Tier-C items).
  Rejected: multiplies the fragile surface for values that are not
  load-bearing for any MUST-have tool — the same "don't scrape everything
  just because a page exists" reasoning ADR-0006 already applied to logs/
  tentacle listing.
