# ADR-0009: Evaluator discovery (`list_evaluators`) — a third Tier-B HTML-scrape adapter

## Status
**Superseded (reversed to GO) by explicit user decision.** Originally
accepted as NO-GO; the user later explicitly asked to build the adapter
anyway. The NO-GO analysis below (Context/"case for NO-GO"/Consequences/
Risks/Alternatives) is preserved as the record of that first decision and
remains accurate as a risk/trade-off analysis — only the final call
changed, by direct user instruction, not because the analysis was found
wrong. The "If go" section's design is now the accepted implementation
plan, updated below with a completed live-verification pass that resolves
its own previously-blocking pre-implementation task.

**Live verification performed** (this session, `GET
http://192.168.178.100:5001/advanced/evaluator_config`, OctoBot 2.1.1,
fetched directly via `curl` — resolving the "Not verified this session"
gap the original architect draft flagged): all three previously-open
structural questions are now answered, all favorably for the "If go"
design:
1. **Activation state IS server-rendered, not client-side-JS-applied.**
   Each evaluator row is a real, static `<a class="config-element" ...
   config-type="evaluator_config" config-key=<Name> current-value=<Bool>
   config-value=<Bool> startup-config-value=<Bool> ...>` element — a plain
   BeautifulSoup parse recovers real state, no JS execution needed. (This
   was the single highest-severity risk the original draft flagged; it does
   not hold.)
2. **Type/category grouping IS rendered**, as four distinct `<div
   class="card">` sections, each with a stable container `id`: "Technical
   analysis" (`id="ta-evaluator-config-root"`), "Social analysis"
   (`id="social-evaluator-config-root"`), "Scripted evaluators"
   (`id="scripted-evaluator-config-root"`), "Real time analysis"
   (`id="rt-evaluator-config-root"`) — matching exactly the four evaluator
   subtypes `update_evaluator_config`'s own `deactivate_others` logic
   already operates on (ADR-0010's evidence trail). `type` can be populated
   confidently, not omitted.
3. **A legitimate zero-evaluators state exists and is structurally
   distinguishable from a broken page**: on this instance, the "Scripted
   evaluators" container renders with genuinely zero `config-element` rows
   inside an otherwise-intact `<div class="row config-container"
   id="scripted-evaluator-config-root">` — a real "this category has no
   evaluators installed" state, not an error. The fail-loud gate should
   therefore be scoped to the WHOLE page (zero `config-element` rows found
   across ALL FOUR containers combined is broken/suspicious), not any
   single category container (a single empty category is normal).

A redundant secondary signal was also found: each row's CSS class includes
`list-group-item-success` (enabled) vs. `list-group-item-light` (disabled),
and a `<span class="badge badge-success">Activated</span>` /
`badge-secondary">Deactivated</span>` badge — both agree with
`current-value` on every row checked. Not required for the scrape (the
`current-value` attribute is authoritative and simpler to parse), but
available as a cheap cross-check in tests/fixtures if useful.

## Context

`update_evaluator_config(patch, deactivate_others=False)` shipped (task 16,
`docs/tasklist.md` item 16, FR-28) as a write-only tool: `patch` is a map of
**evaluator class name → enabled boolean** (e.g.
`{"RSIMomentumEvaluator": true, "BBMomentumEvaluator": false}`), submitted
verbatim to `POST /config`'s `evaluator_config` branch. It has no companion
read tool. Confirmed this session (`src/octobot_mcp/tools/trading_config.py`,
`src/octobot_mcp/errors.py`): there is no Tier-A JSON route that enumerates
evaluators or their current activation state —
`get_tentacle_config(tentacle)` requires already knowing one tentacle's exact
class name (it maps to `GET /config_tentacle_edit_details/<tentacle>`, a
per-tentacle route, not an enumeration one), and `list_tentacles` is an
explicit **Tier C** stub (ADR-0006) precisely because no JSON route
enumerates tentacles of any kind, evaluators included.

This project's standing rule (established when ADR-0008 approved the second
Tier-B adapter, `list_data_files`) is that a **third** Tier-B HTML-scrape
adapter gets its own ADR rather than being added ad hoc alongside a write
tool's implementation. This is that ADR, triggered by discovering the gap
immediately after shipping `update_evaluator_config`.

### What's already verified, and what isn't (this session)

**Already verified, in a prior session** (recorded in
`update_evaluator_config`'s own tool-spec entry,
`docs/specs/octobot-mcp-tool-spec.md`, and reproduced in
`trading_config.py`'s module docstring): the live instance's rendered
`/advanced/evaluator_config` page renders each evaluator's activation toggle
with literal `config-type="evaluator_config" config-key=<EvaluatorClassName>`
attributes — the same `_save_config()` JS mechanism already confirmed for
`update_trading_config`/`update_exchange_credentials`. This is the one
concrete, source-adjacent fact grounding everything below.

**Not verified this session**: this session's own attempt to re-fetch
`http://192.168.178.100:5001/advanced/evaluator_config` and inspect its full
rendered HTML — the task's own explicit grounding instruction — could not be
completed with the tools available to this architect role: `WebFetch`
unconditionally upgrades `http://` to `https://` and the live instance has no
TLS listener (`SSL routines:WRONG_VERSION_NUMBER`); the Playwright MCP
browser tool is connected but has no Chromium binary installed in this
environment; no `Bash`/`curl` is available to this role by design. **[UNVERIFIED,
this session]**: the exact row/section markup (tag names, CSS classes),
whether the checkbox/toggle's checked state is exposed as a plain HTML
attribute BeautifulSoup can read statically or is only set by client-side JS
after page load (the latter would make a `GET`-and-parse scrape return every
evaluator as `enabled: false` regardless of true state — a materially
different, higher-risk finding than either existing adapter had to rule
out), and whether evaluators are grouped in the DOM by type/category
(technical-analysis/social/real-time/scripted — confirmed to exist as a
*model-layer* distinction in `update_evaluator_config`'s own
`deactivate_others` verification, but not confirmed as a *rendered* grouping).
Any "go" implementation must resolve this before writing the scraper,
mirroring ADR-0006/ADR-0008's own "fetch and inspect the live page first" task.

## The go/no-go analysis

### The case for GO
`update_evaluator_config` is, in the literal sense ADR-0006 used to justify
`list_profiles`, unusable without already knowing an evaluator's exact class
name. Building `list_evaluators` would close a genuine "write tool with no
matching discovery tool" gap, using an already twice-proven pattern
(BeautifulSoup, fail-loud via a distinct exception, broken-fixture test), on
a page already partially verified. It would also close a **loop** exactly
like `list_data_files` → `delete_data_file`: the `config-key` attribute that
already backs `update_evaluator_config`'s `patch` keys is the same attribute
`list_evaluators` would read, so a "go" implementation's output is
structurally guaranteed to feed the write tool's input without translation.

### The case for NO-GO — why this gap is not the same shape as the first two

This project's own precedence order (compliance/ASRs → reliability/
operability → simplicity/maintainability → extensibility/reuse →
aesthetics) is what justified building the *first* two Tier-B adapters
despite their acknowledged, compounding maintenance cost — but it justified
them because discovery was a genuine **precondition for the write tool to be
usable at all**: `profile_id` and data-collector filenames are opaque,
per-instance, runtime-generated identifiers with no possible external
source. **Evaluator class names are not that.** They are OctoBot's own
public, documented tentacle catalog (`RSIMomentumEvaluator`,
`BBMomentumEvaluator`, and the rest of `Drakkar-Software/OctoBot`'s
evaluator tentacles) — a bounded, comparatively stable set an agent or human
can reasonably obtain from OctoBot's own documentation/source, from asking
the user, or from a one-time manual look at the OctoBot web UI, **without**
needing this server to enumerate them. The one thing genuinely unavailable
elsewhere — current per-instance **activation state** — is real, but lower
stakes than it was for the first two adapters: `update_evaluator_config`'s
default `deactivate_others=False` is purely additive (confirmed, that
tool's own docstring/verification) — calling it without knowing current
state cannot silently disable an evaluator the caller didn't name, unlike,
say, `delete_profile` acting on the wrong opaque id. Reliability/
maintainability (tiers 2–3) outrank closing a gap that is real but narrower
and lower-risk than the ones already accepted — especially for FR-28, a
`SHOULD`-priority addendum added *after* v1 shipped, not part of the
original curated scope.

A third scrape adapter also compounds a cost this project has already
flagged twice as a real, standing liability (ADR-0006's "Negative
consequences," restated for the second adapter): every additional Tier-B
adapter is one more page whose structure can silently drift on an OctoBot
UI upgrade, one more fail-loud contract to keep current, one more fixture to
maintain forever. Three fragile HTML-scrape surfaces for a fourteen-plus-
milestone project whose vast majority of tools are confirmed JSON routes is
a meaningfully different risk profile than two.

### Recommendation: **NO-GO** for now

Do not add `list_evaluators` as a third Tier-B scrape adapter. Close the gap
more cheaply (see "If no-go" below). Revisit if real usage shows agents
repeatedly failing to call `update_evaluator_config` successfully for lack
of a name/state they could not otherwise obtain — the same "revisit if a
real need emerges" posture the requirements doc already applies to MCP
Resources.

---

## Implementation plan (was "If go" — now the accepted plan; live-verified this session)

- **Tool name**: `list_evaluators()`. Confirmed available — `list_tentacles`'s
  existing Tier-C stub (`src/octobot_mcp/tools/tentacles.py`) does **not**
  reserve this name; its own `alternative` field points callers at
  `get_tentacle_config`, never at anything evaluator-specific, so
  `list_evaluators` is free and matches this project's existing
  `list_profiles`/`list_data_files` naming convention exactly.
- **Scrape target**: `GET /advanced/evaluator_config` — confirmed correct
  by direct fetch this session (`curl`, OctoBot 2.1.1, 91617 bytes, HTTP 200).
- **Confirmed exact row markup** (real excerpt from the live instance):
  ```html
  <a href="#" onclick="return false;"
     class="col-md-6 col-lg-4 p-1 rounded list-group-item waves-effect list-group-item-success config-element"
     id=BBMomentumEvaluator name=BBMomentumEvaluator
     config-type="evaluator_config" config-key=BBMomentumEvaluator
     current-value=True config-value=True startup-config-value=True
     requirements="" default-elements="" requirements-min-count="">
    ...
    <span class="badge badge-success">Activated</span>
    ...
  </a>
  ```
  Select on `a.config-element` (or `a[config-type="evaluator_config"]`);
  `name` = the `config-key` attribute; `enabled` = the `current-value`
  attribute parsed as a bool (`"True"`/`"False"` string, confirmed literal
  casing). The CSS class (`list-group-item-success`/`-light`) and the
  `Activated`/`Deactivated` badge text agree with `current-value` on every
  row checked — redundant, not required, but usable as a fixture
  cross-check.
- **Confirmed category grouping — resolves the type field**: four `<div
  class="card">` sections, each wrapping a `<div class="row
  config-container" id="...">`:
  - `id="ta-evaluator-config-root"` (header "Technical analysis") → `type: "technical_analysis"`
  - `id="social-evaluator-config-root"` (header "Social analysis") → `type: "social"`
  - `id="rt-evaluator-config-root"` (header "Real time analysis") → `type: "realtime"`
  - `id="scripted-evaluator-config-root"` (header "Scripted evaluators") → `type: "scripted"`

  Parse each container separately and tag every `config-element` found
  inside it with that container's `type` — do not try to infer type from
  the evaluator's own class name.
- **Output shape** (by direct analogy to `list_profiles`/`list_data_files`):
  `{"evaluators": [{"name": str, "enabled": bool, "type": str}]}`. `type`
  is always populated now (confirmed rendered, not omitted per the original
  draft's fallback) using the four literal values above. `name` is the
  `config-key` attribute value — the same string `update_evaluator_config`'s
  `patch` keys must equal, making the closed loop with that tool a
  structural guarantee, not a convention to maintain by hand.
- **Fail-loud gate, confirmed scope**: raise
  `EvaluatorScrapeContractBrokenError` only if **zero** `config-element` rows
  are found across **all four** containers combined, or if any of the four
  expected container `id`s is missing from the page entirely. A single
  category legitimately having zero rows is NOT an error — confirmed live:
  this instance's own "Scripted evaluators" section renders with genuinely
  zero evaluators inside an intact, correctly-structured empty container.
  Do NOT reuse `list_profiles`'s "zero is always broken" gate model here —
  it's the wrong fit for this page, as originally suspected and now confirmed.
- **New exception**: `EvaluatorScrapeContractBrokenError(OctoBotAPIError)`
  in `src/octobot_mcp/errors.py`, following `ProfileScrapeContractBrokenError`/
  `DataFileScrapeContractBrokenError`'s exact documentation convention (one
  domain-specific exception class per adapter, never conflated).
- **Pre-implementation verification task — COMPLETE** (was blocking, mirroring
  ADR-0006/ADR-0008's own first task): all three sub-questions this task
  asked are now answered from a real live fetch this session — see Status
  and the "Live verification performed" note in Context above. No further
  live verification is required before implementation; a developer should
  still fetch the page again to build/validate a test fixture, but the
  design's core structural assumptions are no longer open questions.
- **Downstream artefacts this "go" requires** (not written here, per this
  ADR's own scope — the orchestrator writes these next): `FR-32` in
  `docs/requirements/octobot-mcp-requirements.md` (mirroring FR-24's
  "including the exact identifier the write tool expects" framing) plus a
  new `NFR-18` (mirroring NFR-12/NFR-13's scrape-reliability acceptance
  criterion pattern); a new "`list_evaluators`" entry in the "Trading
  config" section of `docs/specs/octobot-mcp-tool-spec.md`, alongside
  `update_evaluator_config`; a new numbered `docs/tasklist.md` item mirroring
  item 15/16's structure (implementation, fixture-based tests, demo).

## If no-go (the recommended path)

1. **Strengthen `update_evaluator_config`'s own docstring** (a documentation
   change, not a new tool) to explicitly tell the calling agent how to
   obtain evaluator names/state without a dedicated discovery tool: name a
   few real OctoBot evaluator classes as concrete examples (the docstring
   already does this for `patch`'s shape), and instruct the agent to consult
   the OctoBot web UI's `/advanced/evaluator_config` page directly (a human
   or browser-driving agent can read it even though this MCP server cannot
   reliably scrape it per-instance) or ask the user, before calling this
   tool blind.
2. **Extend `list_tentacles`'s existing Tier-C stub** (`tentacles.py`) so its
   `reason` text explicitly names evaluators as included in the "no JSON
   enumeration API exists" gap it already discloses for tentacles generally
   — a one-string edit to an already-existing, already-honest stub, at zero
   new maintenance surface, rather than a new adapter.
3. **Cheap, non-blocking follow-up worth a developer's time before either of
   the above is finalized** (mirroring FR-27's own "verify before adding a
   duplicate tool" discipline that avoided building
   `get_available_symbols_for_collection`): confirm whether
   `get_tentacle_config(<a known evaluator class name>)`'s existing JSON
   response already exposes that evaluator's activation state. If it does,
   the real remaining gap is narrower still — just *names*, not *state* —
   and (1) above should say so explicitly instead of just gesturing at the
   OctoBot UI.
4. **Revisit trigger**: reopen this ADR if a user/agent is observed
   repeatedly failing to call `update_evaluator_config` successfully for
   lack of a discoverable name or state — concrete evidence of real friction,
   not a hypothetical one.

## Consequences

### Positive (of NO-GO)
- No third fragile, OctoBot-UI-version-coupled scrape surface added; the two
  existing Tier-B adapters' maintenance cost does not triple.
- `update_evaluator_config` remains exactly as useful as it already is
  (real gap, but a pre-existing one this decision doesn't worsen) while
  costing nothing new to build or keep passing.

### Negative (of NO-GO)
- An agent with no prior knowledge of OctoBot's evaluator catalog and no
  human to ask genuinely cannot discover exact class names or current state
  through this MCP server alone — a real, acknowledged capability gap,
  disclosed honestly per this project's own convention (ADR-0006's Tier-C
  precedent), not hidden.

### Neutral
- If this recommendation is later overridden, the "If go" section above is
  written to be directly actionable without re-deriving the tool contract,
  scrape target, or exception name from scratch.

## Risks
| Risk | Severity | Likelihood | Mitigation |
|---|---|---|---|
| The "evaluator names are a discoverable public catalog" premise this NO-GO leans on turns out wrong in practice — e.g. an OctoBot instance running third-party/custom evaluator tentacles with names no public catalog lists | Medium | Low | Revisit trigger above; the "If go" design is fully specified and can be implemented on short notice if this surfaces |
| `/advanced/evaluator_config`'s activation state turns out to be client-side-JS-applied rather than server-rendered (unconfirmed this session — see the blocking verification task in "If go") | High (would make even a future "go" non-viable as scoped) | Unknown | Named explicitly as the first thing a future implementer must confirm, before any other scrape work |
| This session's live-instance re-verification could not be performed at all (tooling gap: `WebFetch` forces HTTPS, no Chromium binary for Playwright MCP, no shell access for this role) | Low (this ADR's one load-bearing fact was already verified in a prior session and is cited, not invented) | N/A | Flagged plainly here rather than silently presented as freshly confirmed; a future implementer's own verification task supersedes it either way |

## Alternatives considered
- **Build `list_evaluators` now, matching `list_data_files`'s precedent
  exactly.** Rejected (for now): the "If go" section above is this
  alternative, fully specified — rejected on the go/no-go analysis above,
  not because it's poorly defined.
- **Extend `list_tentacles` (Tier C) into a real Tier-B scrape of the
  general tentacle-listing page instead of a narrow evaluator-only one.**
  Rejected: broader scope than the actual gap (ADR-0006's "Alternatives
  considered" already rejected scraping every HTML-only page for the same
  reason), and would still need the same client-side-vs-server-rendered
  verification risk this ADR flags, for a larger page.
- **Do nothing — leave `update_evaluator_config`'s docstring unchanged.**
  Rejected: costs nothing to improve the docstring (step 1 of "If no-go"),
  so leaving a known, cheaply-fixable gap fully undocumented has no
  offsetting benefit.
