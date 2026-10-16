# ADR-0012: Real-trading toggle, exchange add/remove, interfaces, notification routing, and per-profile exchange/currency config — all one `/config` + `global_config` write family

## Status
Accepted

## Context

A live UI audit this session (`http://192.168.178.100:5001`, OctoBot 2.1.1,
read-only — no form was ever submitted) found six distinct capability gaps
across the Accounts and Profile pages:

1. The navbar's real-trading on/off switch (the literal UI control behind
   `convert_profile_to_live`'s long-documented gap — ADR-0003/requirements
   doc open question #5 — "no tool in this server's scope can set
   `config.trader.enabled`").
2. Accounts → Exchanges: adding/removing a whole exchange, not just updating
   an existing one's credentials.
3. Accounts → Interfaces: Telegram/Web/other service integrations.
4. Accounts → Notifications: routing which event categories go to which
   interface.
5. Profile → Exchanges tab: per-profile exchange enable/disable + spot/
   futures mode.
6. Profile → Currencies: per-profile traded-pair selection — the surface
   behind `update_profile`'s own documented "`config` patch is silently
   ignored" limitation.

**The investigation's central finding: all six are the same mechanism.**
Every editable field across all six panels — confirmed by reading the
rendered live DOM directly, read-only, for each one — carries the identical
`config-type="global_config"` attribute, and OctoBot's `POST /config` route
(fetched in full from source, `Drakkar-Software/OctoBot`,
`packages/tentacles/Services/Interfaces/web_interface/controllers/configuration.py`,
the same repo/path family ADR-0003's `update_exchange_credentials` research
already used) has exactly five branches and nothing else:
`TRADING_CONFIG_KEY`, `TENTACLES_CONFIG_KEY`, `EVALUATOR_CONFIG_KEY`,
`removed_elements` (→ `models.update_global_config(..., delete=True)`), and
`GLOBAL_CONFIG_KEY` (→ `models.update_global_config(...)`). There is no
separate "add exchange"/"remove exchange"/"save Telegram token"/"save
notification routing"/"save profile currency" route anywhere in OctoBot —
the Accounts and Profile pages' "Add"/"Remove"/"Save" buttons
(`configuration.js`'s generic `handleCardDecksAddButtons`/
`handle_remove_buttons`/`_save_config`) are 100% client-side DOM
manipulation that produces exactly one kind of network call when something
is actually persisted: this same `POST /config`. This is the exact mechanism
this project already uses for `update_exchange_credentials`/
`update_trading_config`/`update_evaluator_config` (ADR-0003's own
`exchanges.py` module docstring already reverse-engineered the
`global_config`/`parse_and_update` flat-key-splitting mechanism from four
source locations); this session's finding is that it extends, unmodified, to
five more feature areas.

Per this project's own precedent (ADR-0011: one ADR for two features found
the same session with the same tier-decision reasoning, not a fixed 1:1
mapping), this is **one ADR for six features**, more strongly justified here
than ADR-0011's own case: not just the same *tier* decision, but literally
the same route, the same body envelope, and the same underlying model
function for all six.

### Investigation performed this session (live, read-only, plus GitHub source)

**1. Real-trading toggle.** Navbar element `id="switchTradingState"`
(`aria-label="TradingSwitch"`) opens `#tradingSwitchModal`. Its action
button, read directly from the live DOM (never clicked):
```html
<a class="trading-mode-switch-button" update-url="/config"
   config-type="global_config" config-key="trader-simulator_enabled"
   current-value="True">Switch to simulated trading</a>
```
Confirmed byte-for-byte against
`templates/components/modals/trading_state_modal.html` on GitHub, which also
shows the mirror-image branch (`config-key="trader_enabled"`, "Switch to
real trading", shown when not currently real-trading). `navbar.js::
trigger_trader_state` (full source read) always sets **both** keys as a
pair and hardcodes a restart:
```js
updated_config = { global_config: { trader_enabled: <bool>, "trader-simulator_enabled": <bool> } };
updated_config["restart_after_save"] = true;
```
— i.e. `POST /config`, body `{"global_config": {"trader_enabled": <bool>,
"trader-simulator_enabled": <bool>}, "restart_after_save": true}`. The
`/config` route's full source (read in full, see Context above) confirms
`restart_after_save: true` unconditionally calls
`models.schedule_delayed_command(models.restart_bot)` — the SAME underlying
restart primitive `restart_octobot` (ADR-0010) already wraps via a different
HTTP route (`GET /wait_reboot?reboot=true`). The modal's own on-screen text
("Warning! The switch button will also restart OctoBot") is independent,
corroborating confirmation. **`[V]`, not `[UNVERIFIED]`**: confirmed via two
independent sources (live rendered DOM attributes + the exact GitHub source
of both the JS and the route), neither requiring a form submission.
`config[CONFIG_TRADER][CONFIG_ENABLED_OPTION]` → flat key `trader_enabled`
splits (on `"_"`) to `["trader", "enabled"]`, matching the `is_trader_enabled`
path already confirmed in `profiles.py`'s existing research; `"trader-simulator_enabled"`
splits to `["trader-simulator", "enabled"]`, matching `CONFIG_SIMULATOR`
likewise — no underscore-collision risk exists for either key (unlike the
exchange-name case), since neither segment contains a stray `"_"`.

**Safety note, not a design input but recorded for the handoff report**: this
live test instance is **currently in real-trading mode** (`is_real_trading =
True` — inferred from the "Switch to simulated trading" branch being the one
actually rendered). Nothing was changed.

**2. Exchange add/remove.** Confirmed live: the "Add" button (`#AddExchange`)
and "Remove" button (`.remove-btn`, confirmed present on the existing Binance
card) are both the generic card-deck mechanism in `configuration.js` — no
network call on click; state is only sent on the page's "SAVE"/"APPLY
CHANGES AND RESTART" tab actions (also read live: `#super-container`'s
`_save_config()`). Confirmed live DOM for the existing exchange card:
```html
<div class="card-body" name="binance" config-key="exchanges_binance">
```
— `get_card_config_key()` reads exactly this attribute when a card is
removed, pushing `"exchanges_binance"` into `removed_elements`. Confirmed
against `config_operations.py`'s full source (`clear_dictionaries_by_keys`/
`parse_and_update`, read in full from GitHub) that a `removed_elements`
entry deletes the **whole** nested node it names — `"exchanges_binance"`
removes `config["exchanges"]["binance"]` entirely, credentials included, not
one field. Confirmed `templates/components/config/exchange_card.html` (read
in full) for the per-field flat keys a new exchange's card carries:
`exchanges_<exchange>_api-key` / `-secret` / `-password` (identical to
`update_exchange_credentials`'s existing confirmed shape) **plus**
`exchanges_<exchange>_enabled` (bool, independent key,
`update_exchange_credentials`'s current implementation never sets it) and
`exchanges_<exchange>_exchange-type` (radio: one of
`exchanges_details['supported_exchange_types']`'s enum values — **this
template reads an enum's `.value`, and the live instance's actual rendered
string for each supported type was not captured this session since Binance's
own Profile-tab exchange-type radio group wasn't separately inspected; see
Open questions**).

**3/4. Interfaces and Notifications.** Confirmed live, `config-type=
"global_config"` on every field (field *names* confirmed via DOM
inspection only; no value was read back into any artifact for a
credential-shaped field — see the explicit redaction note below):
- Telegram: `services_telegram_token`, `services_telegram_chat-id`,
  `services_telegram_usernames-whitelist` (list). **A real bot token and
  chat-id are currently configured on this instance; neither value is
  recorded anywhere in this ADR or any other file this session touched** —
  only the field names/types were read.
- Web interface: `services_web_port` (number), `services_web_auto-open-in-web-browser`
  (bool), `services_web_require-password` (bool),
  `services_web_password` (text).
- Eight further configurable services discovered from the "Add service"
  template ids (`reddit`, `telegram-api`, `trading-view`, `webhook`,
  `lunarcrush`, `GPT`, `langchain`, `node-api`) — their *existence* and the
  same `services_<name>_<field>` naming convention is confirmed; their
  individual field sets were only partially read from the id list (not each
  one's live `config-type`/`data-type` attributes), since none of these
  eight are actually configured on this instance to inspect a live, fully
  rendered card for. **`[UNVERIFIED-per-field]`** for anything beyond
  Telegram/Web; treated as a generic, self-describing scrape (below) rather
  than a hardcoded per-service field list, specifically so this gap doesn't
  block the design.
- Notification routing, confirmed live: `notification_notification-type`
  (a `list`-typed multi-select; live value observed `['web', 'telegram']` —
  this **is** "which interfaces receive notifications," literally the
  routing the audit asked about), `notification_price-alerts`/
  `notification_trades`/`notification_trading-script-alerts`/
  `notification_other` (bools — which event categories notify at all).

**5. Profile → Exchanges tab.** Confirmed by reading
`exchange_card.html`'s own macro parameters: the **same** macro renders both
the Accounts page's full exchange card (`lite_config=False`) and the
Profile page's per-profile exchange tab (`lite_config=True`), and emits the
exact same `config-key="exchanges_<exchange>_enabled"`/
`config-type="global_config"` pair in both, plus (lite mode only)
`config-key="exchanges_<exchange>_exchange-type"` (radio). **Architecturally
significant finding**: "enabled"/"exchange-type" are NOT profile-scoped data
in OctoBot's config model at all — they live in the GLOBAL
`config["exchanges"]["<name>"]` node, merely surfaced for convenience inside
the Profile page's UI. This is not a new mechanism; it is the identical
`/config` + `global_config` route.

**6. Profile → Currencies.** Confirmed live on the Profile page's Currencies
tab:
```html
<input ... config-key="crypto-currencies_Bitcoin_enabled" config-type="global_config" data-type="bool" ...>
<select config-key="crypto-currencies_Bitcoin_pairs" config-type="global_config" data-type="list" ...>
```
— confirming the hypothesis this feature was selected for: currency/pair
selection is edited through the identical generic `/config` +
`global_config` mechanism, under a `crypto-currencies_<Currency>_<field>`
flat-key family, and is **genuinely separate** from `update_profile`'s own
already-documented, broken `config` passthrough field
(`POST /profiles_management/update`) — that route's limitation is real but
irrelevant here, since this is a different route entirely. This directly
closes the gap `update_profile`'s docstring has carried since milestone 5.

## Decision

### 1. One generic Tier-B scrape pattern, reused across four read tools
Unlike ADR-0006's/ADR-0009's prior Tier-B adapters, **every field on these
pages already self-describes** via a uniform attribute convention
(`config-key`, `config-type`, `config-value`, `data-type` ∈
`{bool, number, text, list}`) — the exact same convention
`exchanges.py`/`profiles.py` already rely on for individual fields, now
generalized into a *generic* adapter: parse every `.config-element` found
inside a given container, typed per its own `data-type` attribute, with no
per-service/per-field hardcoding required. One shared helper
(`_scrape_config_elements(html, container_selector)` in a new
`octobot_mcp.tools._config_scrape` module) backs all four new read tools
below, each scoped to one page container:
- `get_exchanges()` — container: the exchange-card deck on `/accounts`
  (`div[data-role=exchange]`). Returns, per exchange: `name`,
  `enabled`, `exchange_type`, and `credentials_configured` (bool — whether
  `api-key`'s `config-value` differs from OctoBot's own masked placeholder
  `"*********"`, confirmed live as the literal placeholder string) —
  **never** the actual key/secret/password value. This also closes
  ADR-0006's own long-standing "no full exchange list" gap as a side effect
  (`get_exchange_details` only ever returns one exchange, by design).
- `get_interfaces()` — container: `/accounts`' `#panelServices`. Returns,
  per configured service: `service` name and its field map, with any
  credential-shaped field (see NFR-23 below) redacted the same way
  `update_exchange_credentials` already redacts (last 4 chars, never full
  value).
- `get_notification_routing()` — container: `/accounts`' `#panelNotifications`.
- `get_profile_currencies()` — container: `/profile`'s `#panelCurrency`.

None of these four raise their fail-loud exception on zero matched elements
inside an otherwise-intact container (a fresh instance may legitimately have
zero configured services, or a profile may legitimately trade zero
currencies — mirroring ADR-0011's "zero is not broken" precedent, not
`list_profiles`'/`list_evaluators`'s "zero is always broken" one); they DO
raise it if the container itself, or the page's broader expected structure,
is missing.

### 2. Six Tier-A writes, all against the already-confirmed `/config` route
No new route discovery was needed for any of these — all six reuse the
identical envelope `update_exchange_credentials` already implements
(`POST /config`, `{"global_config": {...}}` and/or `{"removed_elements":
[...]}`, optionally `{"restart_after_save": true}`):

| Tool | Body it sends | Confirm-gated |
|---|---|---|
| `set_real_trading_enabled(enabled, confirm)` | `{"global_config": {"trader_enabled": enabled, "trader-simulator_enabled": !enabled}, "restart_after_save": true}` | **Yes, both directions** |
| `add_exchange(exchange, api_key, api_secret, api_password?, confirm)` | `{"global_config": {"exchanges_<ex>_api-key": ..., "...-secret": ..., "...-password"?: ..., "exchanges_<ex>_enabled": true}}` | **Yes** |
| `remove_exchange(exchange, confirm)` | `{"removed_elements": ["exchanges_<exchange>"]}` | **Yes** |
| `update_exchange_trading_mode(exchange, exchange_type?, enabled?)` | `{"global_config": {"exchanges_<ex>_exchange-type"?: ..., "exchanges_<ex>_enabled"?: ...}}` | No |
| `update_interface_config(service, patch, confirm)` | `{"global_config": {"services_<service>_<field>": value, ...}}` | **Conditional — see Decision 3** |
| `update_notification_routing(patch)` | `{"global_config": {"notification_<field>": value, ...}}` | No |
| `update_profile_currencies(patch)` | `{"global_config": {"crypto-currencies_<Currency>_enabled"?: ..., "...-pairs"?: ...}}` | No |

`add_exchange` reuses `update_exchange_credentials`'s existing
underscore-in-exchange-name local refusal (same corruption risk, same
`parse_and_update` splitting behavior) rather than duplicating that guard —
implemented as a shared helper both tools call.

### 3. `update_interface_config`'s gating is per-field-name, not per-tool
Every other gated tool in this project (ADR-0003/NFR-7) is gated
unconditionally — the *tool* is on the gated list, full stop.
`update_interface_config` cannot honestly work that way: it is one tool
spanning services whose fields range from a plain integer (`services_web_port`)
to a bot token as sensitive as an exchange API key
(`services_telegram_token`). Rather than force an all-or-nothing choice
(either over-gate `services_web_port` or under-gate `services_telegram_token`),
this tool requires `confirm=true` **iff** any key in `patch` ends with one of
a fixed, documented secret-suffix list: `token`, `secret`, `password`,
`api-key`, `api-secret`, `api-password`, `api-hash`, `client-secret` (the
same discipline `update_exchange_credentials` already applies by name, here
generalized across services). This is a deliberate, bounded extension of
ADR-0003's framework — recorded explicitly as its own decision point, not a
silent per-call reinterpretation of "confirm-gated." The redaction half of
NFR-6 is likewise generalized (NFR-23): any value written to a matching
field is redacted (last-4-chars) in the tool's own response and logs, the
same way exchange secrets already are.

### 4. Precedence ordering explicit for the two items that are genuinely a
   judgment call (ADR-0003's own stated spirit — flag it, don't silently
   decide)
- `update_exchange_trading_mode`'s `exchange_type` switch (e.g. spot→futures
  on an exchange real trading is currently enabled on) materially changes
  risk (leverage) without meeting this project's literal "destructive /
  exposes credentials / enables live trading" gating criteria. **Not
  gated**, mirroring ADR-0003's own `cancel_order`/`close_position`
  precedent of naming a judgment call explicitly rather than silently
  picking a side — revisitable with the user.
- `update_profile_currencies`/`update_notification_routing` mutate behavior
  that *could* matter a great deal on a LIVE-trading profile (which pairs
  are traded at all; whether a losing trade alert reaches anyone) but do not
  themselves enable live trading, destroy data, or expose credentials — kept
  un-gated, by the same reasoning `update_trading_config`/
  `update_evaluator_config` already established for comparable config
  mutations.

## Consequences

### Positive
- Six real gaps closed by **zero new OctoBot routes** — every write reuses
  the one `/config` route this project already has full-source confidence
  in; every read reuses one new, genuinely generic scrape helper instead of
  six bespoke ones.
- `get_exchanges()` closes a second, independently-standing gap
  (ADR-0006's "no full exchange list" note) as a side effect of this
  session's work, not a separately-scoped feature.
- The generic `.config-element` scrape helper is itself reusable by any
  *future* Accounts/Profile-page addendum (e.g. the remaining untouched
  service integrations), lowering the cost of the next one.
- `set_real_trading_enabled` finally closes the gap ADR-0003/requirements
  doc open question #5 has carried since early in this project, with
  source-and-DOM-confirmed evidence, not a guess.

### Negative
- A fourth generic Tier-B scrape surface (on top of the four page-specific
  ones from ADR-0006/0008/0009/0011) still couples this server to OctoBot's
  HTML, even if the *pattern* used to read it is now reusable — an OctoBot
  upgrade that drops the `config-key`/`config-type`/`data-type` attribute
  convention entirely (not just reorders one page) would break all four new
  read tools at once, a wider blast radius than any single prior adapter.
  Mitigated the same way as always: fail-loud on missing structure, a smoke
  test per read tool, version pinned in the adapter's docstring.
- `update_interface_config`'s per-field-name conditional gating is a new
  kind of rule in this codebase (every prior gated tool is gated
  unconditionally) — a future maintainer extending the secret-suffix list
  must remember this tool's gating depends on it; documented explicitly here
  and in the tool's own docstring specifically so it isn't missed.
- Seven of the nine services' exact field sets are not DOM-confirmed this
  session (only Telegram and Web are); `get_interfaces()`/
  `update_interface_config` work generically regardless (no hardcoded field
  list to be wrong about), but the secret-suffix-based gating for those
  seven is a naming-convention inference, not a live-confirmed fact per
  field. Flagged as an open question, not silently assumed correct.
- `add_exchange`/`remove_exchange`/`set_real_trading_enabled` all share one
  underlying route with several other tools already in this project
  (`update_exchange_credentials`, `update_trading_config`,
  `update_evaluator_config`) — a future bug in the shared HTTP-call helper
  now has a wider blast radius across more tools than before. Accepted:
  this is the same route this project already trusts for four existing
  tools; adding more callers of an already-well-understood route is lower
  risk than introducing a new one.

## Risks
| Risk | Severity | Likelihood | Mitigation |
|---|---|---|---|
| `set_real_trading_enabled(enabled=True)` is called without the caller fully registering the forced restart / real-money consequence | High | Low (confirm-gated both directions, risk-scaled reason string) | Refusal reason text spells out both the restart AND the real-money consequence explicitly; job-store active-job check (mirroring `restart_octobot`, ADR-0010) refuses outright during a running backtest/data-collection job |
| `remove_exchange` deletes a configured exchange's credentials with no way to recover them from this server (OctoBot itself never echoes a secret back) | Medium | Low (confirm-gated) | Confirm-gated with an explicit "this also deletes the exchange's saved credentials, not just its config entry" reason string (stronger than a generic "removes this exchange") |
| `update_interface_config`'s secret-suffix list misses a credential-shaped field this session didn't observe (one of the 7 un-inspected services) | Medium | Medium | Documented as an explicit open question; the suffix list is easy to extend without an architecture change, and `get_interfaces()`'s own redaction uses the same list so a miss there is symmetric, not one-sided |
| OctoBot renames/removes the `config-key`/`config-type`/`data-type` attribute convention in a future version | Medium | Low | Same standing Tier-B mitigation as every other adapter: fail-loud, smoke test on upgrade, version pinned |

## Alternatives considered
- **A separate ADR per feature (six ADRs).** Rejected: all six share not
  just the same Tier decision (ADR-0011's bar for combining) but the
  identical route, body envelope, and underlying model function — a
  stronger case for combining than ADR-0011's own.
- **One generic `update_global_config(patch, removed_elements?, confirm?)`
  tool instead of six named ones.** Rejected: breaks this project's
  established small-single-purpose-tool convention (every prior addendum —
  ADR-0007 through ADR-0011 — adds named, purpose-specific tools even when
  they share a route, e.g. `update_trading_config`/`update_evaluator_config`
  already share one route and remain two tools); a generic tool would also
  make per-tool confirm-gating (the actual safety mechanism here)
  impossible to express cleanly, since gating genuinely differs per
  semantic action even though the transport is identical.
- **Unconditional, always-gated `update_interface_config`.** Considered to
  avoid the "conditional gating" precedent entirely. Rejected: would force
  confirming a one-line `services_web_port` change with the same ceremony as
  writing a bot token, which this project's own ADR-0003 "Negative
  consequences" section already names as the exact cost this gating
  framework tries to minimize ("every destructive action costs one extra
  round trip... accepted... small relative to the benefit" — a cost this
  project accepts for destructive/credential actions specifically, not for
  routine config edits).
- **Fold `get_exchanges()` into a future, separately-scoped addendum** since
  it wasn't literally one of the task's seven bullets. Rejected: it is a
  near-zero-marginal-cost byproduct of the same generic scrape helper this
  ADR already introduces for the other four reads, and it closes a gap this
  project has carried since ADR-0006 — shipping it now is cheaper than
  re-deriving the same scrape pattern later.

## Open questions (flagged, not guessed)
17. **Exact literal enum values for `exchanges_<ex>_exchange-type`** —
    confirmed the field exists and is a radio group over
    `exchanges_details['supported_exchange_types']`'s enum values, but the
    live instance's actual rendered option values (e.g. `"spot"`/`"future"`
    vs. `"spot"`/`"futures"`) were not captured this session. Developer must
    confirm the exact strings against the live `/profile#panelExchanges`
    tab's rendered radio `value` attributes (or OctoBot's
    `ExchangeTypes`/similar enum in source) before finalizing
    `update_exchange_trading_mode`'s `exchange_type` parameter's accepted
    values.
18. **Exact field names for the 7 un-configured services** (`reddit`,
    `telegram-api`, `trading-view`, `webhook`, `lunarcrush`, `GPT`,
    `langchain`, `node-api`) — their existence and the
    `services_<name>_<field>` naming convention is confirmed, and
    `get_interfaces()`/`update_interface_config` work generically without
    needing this list hardcoded, but the secret-suffix-based gating rule
    (Decision 3) is only as good as that suffix list. A developer should
    configure (or find test fixtures for) at least one of each remaining
    service and confirm no field with a credential-bearing purpose uses a
    suffix outside the documented list (`token`, `secret`, `password`,
    `api-key`, `api-secret`, `api-password`, `api-hash`, `client-secret`) —
    e.g. confirm `node-api`'s `admin-password` and `webhook`'s `ngrok-token`
    match (they appear to, by name, but weren't individually DOM-inspected).
19. **Masked-placeholder constant for `get_exchanges()`'s
    `credentials_configured` detection** — this session confirmed
    `"*********"` (9 asterisks) is the literal rendered placeholder for an
    already-configured Binance exchange's `api-key`/`api-secret`/
    `api-password` fields, matching `exchange_card.html`'s macro default
    (`keys_value="*********"`) exactly — this is `[V]`, not open. Recorded
    here only so the developer copies the exact confirmed literal rather
    than re-deriving it.

## Downstream artefacts (written alongside this ADR)
- `docs/requirements/octobot-mcp-requirements.md`: FR-35..FR-41, NFR-21..NFR-23,
  open questions 17–19 (above).
- `docs/specs/octobot-mcp-tool-spec.md`: a new "Accounts & profile global
  config" section with all ten tools' full contracts.
- `docs/tasklist.md`: one new milestone (21), implementable directly from
  the spec section — live verification is already complete (this ADR's own
  Context), mirroring milestones 19/20's "already done" pattern rather than
  the earlier addenda's "step 0, verify first" pattern.
