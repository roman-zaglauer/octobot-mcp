# ADR-0013: Automations — a fourth tentacle-config-shaped surface, not a new rules-engine subsystem

## Status
Accepted

## Context

The live UI audit's seventh item, Profile → Automations, was flagged in the
task brief as the most likely candidate for a scoped-down design: "a full
rules engine that can trigger real actions on bot events" sounded, from the
UI alone (an empty "Automations" tab with "0 automation" and a link to
`/automations`), like it could be arbitrarily complex to model.

**Live, read-only investigation found the opposite.** Navigating to
`/automations` (zero automations currently configured on this instance — no
form was submitted, no automation was created) triggered one real network
request worth inspecting: `GET /automations_edit_details`, returning:
```json
{"config": {"automations_count": 0},
 "displayed_elements": {"data": {"elements": [{
   "schema": {"properties": {"automations_count": {
     "title": "Number of automations.", "type": "number", "default": 0, "minimum": 0}},
     "title": "Automation configuration", "type": "object"},
   "tentacle": "Automation", "tentacle_type": "automation", "type": "input"
 }], "sub_elements": []}, "name": "root", "type": "chart"}, "name": "Automation"}
```
— `"tentacle": "Automation"` is the load-bearing detail. Reading
`static/js/components/automations.js` in full confirms the "Save" button's
handler is `onEditorChange → updateTentacleConfig(newValue, update_url)` —
the **exact same** `updateTentacleConfig` helper (confirmed identical,
`config_tentacle.js`) this project's own already-shipped
`update_tentacle_config` tool wraps for every other tentacle. "+ADD
AUTOMATION"/"REMOVE LAST AUTOMATION" are confirmed to do nothing but
increment/decrement the `automations_count` field locally in the JSON-editor
widget — no network call — before the next Save. Reading the controller
source in full
(`packages/tentacles/Services/Interfaces/web_interface/controllers/automation.py`,
`Drakkar-Software/OctoBot`) confirms the complete, exact mechanism:

```python
@blueprint.route("/automations", methods=["POST", "GET"])
def automations():
    ...
    if action == "save":
        success, response = models.update_tentacle_config(
            tentacle_name, request_data, tentacle_class=tentacle_class)
    if action == "start":
        restart = True
    elif action == "factory_reset":
        success, response = models.reset_automation_config_to_default()
        restart = True
    if restart:
        models.restart_global_automations()  # raises InvalidAutomationConfigError

@blueprint.route('/automations_edit_details')
def automations_edit_details():
    return util.get_rest_reply(
        models.get_tentacle_config_and_edit_display(
            bot_automation.Automation.get_name(), tentacle_class=bot_automation.Automation))
```

**Conclusion: "Automations" is OctoBot's own internal representation of a
pseudo-tentacle** (`bot_automation.Automation`, `tentacle_type="automation"`)
whose config happens to be a dynamically-sized JSON-schema object
(`automations_count` plus, implied but unobserved this session — see Open
questions — per-automation nested event/condition/action blocks). The
"rules engine" complexity this task anticipated lives entirely *inside* that
one opaque, schema-described config blob — exactly the kind of object this
project's NFR-8 discipline ("pass through whatever OctoBot returns, never
invent or reshape fields") was already designed to handle, for backtest
report payloads. There is no new subsystem to design around: there is one
new read route (`/automations_edit_details`), one familiar write action
(`save`, identical in shape to every other tentacle-config write already in
this project), and two genuinely new actions this project hasn't needed
before: `start` (activates configured automations — `models.
restart_global_automations()`, which can raise `InvalidAutomationConfigError`
if the saved config is structurally invalid) and `factory_reset` (discards
the saved config back to OctoBot's own default and also restarts).

### Scope-down considered, and explicitly rejected

Per the task's own explicit instruction ("fine to scope down... read-only
`get_automations` now, defer write support... if live investigation shows
[it] is too complex/risky for one pass"), this was seriously evaluated. **It
is not warranted here**: the write mechanism is byte-for-byte the same
`update_tentacle_config`-style passthrough this project has safely shipped
three times already (`update_tentacle_config`, `update_trading_config`,
`update_evaluator_config`, all confirm-gating-exempt config mutations per
ADR-0003). The only two things this ADR adds that are genuinely new in kind
are the `start`/`factory_reset` actions — both small, independently
nameable, and independently gateable. A read-only-first design would
therefore under-deliver relative to the actual, confirmed risk: it would
ship the safe part (reading/writing a passthrough config blob, no riskier
than four already-shipped tools) while *withholding* the one piece that
cannot be approximated by composing existing tools (there is no other way
to start/reset automations). See Alternatives considered.

## Decision

### 1. Four tools, mirroring the confirmed route exactly
| Tool | Maps to | Confirm |
|---|---|---|
| `get_automations()` | `GET /automations_edit_details` | no |
| `update_automations(patch)` | `POST /automations?action=save` | no |
| `start_automations(confirm)` | `POST /automations?action=start` | **yes** |
| `reset_automations_to_default(confirm)` | `POST /automations?action=factory_reset` | **yes** |

### 2. `get_automations()`/`update_automations(patch)`: Tier A, pure
   passthrough, NFR-8-style
`get_automations()` returns OctoBot's own `config`/`displayed_elements`
(schema) structure unchanged — this server does not attempt to understand,
flatten, or re-model the nested event/condition/action schema; an agent
reads the schema to know what a valid `patch` looks like, the same way a
human uses the JSON-editor widget to know what fields exist.
`update_automations(patch)` sends `patch` as the request body to
`?action=save` unchanged (confirmed: `updateTentacleConfig` sends the raw
edited value with no wrapper key). Not confirm-gated — mutates saved
config only, consistent with `update_tentacle_config`'s own ADR-0003
classification (does not itself trigger anything; `models.update_tentacle_config`
is the same function every other tentacle-config write already calls).

### 3. `start_automations(confirm)`: confirm-gated — a genuinely new
   gating category for this project
Every prior confirm-gated tool in this project is gated because it is
destructive, exposes credentials, or (per ADR-0012, same session) directly
flips the real/simulated trading flag. `start_automations` fits none of
those three literally — but it is the one action in this entire addendum
that can cause OctoBot to **autonomously take further actions on live bot
events without any further per-action human confirmation**, which is a
materially different risk shape than a static config write (the config
write only takes effect once started; starting is the step that turns a
saved rule into a running one). This ADR extends ADR-0003's gating criteria
with a fourth named category — **"activates autonomous, ongoing behavior
beyond this one call"** — explicitly, rather than silently stretching
"enables live trading" to cover it. Refusal reason text names this
distinction plainly (not "this is destructive," which would be inaccurate).

### 4. `reset_automations_to_default(confirm)`: confirm-gated — ordinary
   destructive-action criterion, same as `clear_*_history`/`delete_data_file`
Discards whatever automations the user configured, irrecoverably (OctoBot
itself doesn't version/back up the prior config), and also restarts
automations per the confirmed route source. Squarely matches ADR-0003's
original "irreversible deletion" criterion — no new category needed here,
unlike `start_automations`.

### 5. `InvalidAutomationConfigError` surfaces as a named, non-exception-path
   result where possible
Confirmed from source: `models.restart_global_automations()` can raise
`InvalidAutomationConfigError`, which the route catches and returns as a
400 with the error text. Both `start_automations` and
`reset_automations_to_default` (which also restarts) classify this into a
new `AutomationConfigInvalidError` (this project's existing
one-exception-class-per-domain-failure convention), distinct from a generic
`CONFIG_UPDATE_FAILED`, so an agent can tell "your saved automation config
doesn't parse" apart from a generic connectivity failure.

## Consequences

### Positive
- Closes the one feature from this session's audit with genuinely zero
  existing tool overlap, at the cost of four small tools reusing an
  already-trusted mechanism (`update_tentacle_config`'s own route family) —
  not a new subsystem.
- The passthrough design (Decision 2) means this server never has to keep
  up with OctoBot adding new automation event/condition/action types in a
  future release — the schema a developer/agent sees is always whatever
  OctoBot's own `/automations_edit_details` currently describes.
- `start_automations`'s new gating category is named and bounded (one
  criterion, one tool) rather than a vague expansion of "enables live
  trading" that could invite scope creep into gating other borderline
  actions by analogy later.

### Negative
- This session never observed a **populated** automation (the live instance
  has zero configured) — the nested event/condition/action schema shape
  that appears once `automations_count > 0` was not captured. The
  passthrough design tolerates this (NFR-8 discipline: never invent the
  shape), but a developer implementing `get_automations()`'s response
  schema in the tool spec has only the zero-automations shape as a real
  fixture; a populated-automation fixture should be captured before or
  during implementation (see Open questions).
- `start_automations`'s new gating category (Decision 3) is a precedent a
  future addendum could try to stretch loosely ("this also activates
  something"); it is deliberately defined narrowly here
  ("autonomous, ongoing behavior beyond this one call") specifically to
  resist that drift, but the judgment of whether a future action meets it
  is still a human/architect call, not a mechanical test.
- Four more tools against a feature area this project has never touched
  before (automations can, by the UI's own description, "trigger real
  actions... when something happens") means a misconfigured-then-started
  automation is a real, if confirm-gated, risk surface this server didn't
  have before.

## Risks
| Risk | Severity | Likelihood | Mitigation |
|---|---|---|---|
| An agent calls `start_automations(confirm=true)` against a saved config that performs unintended real actions (the config itself, not this server, decides what "when X happens, do Y" means) | Medium–High (depends entirely on the saved config's own content, outside this server's control) | Low (confirm-gated; the config must already have been explicitly saved via `update_automations` first) | Confirm-gated per Decision 3; refusal text explicitly recommends reviewing `get_automations()`'s current saved config before confirming; `AutomationConfigInvalidError` surfaces a structurally-broken config instead of silently starting nothing |
| The real, populated nested schema (unobserved this session) turns out to contain a field this server's NFR-8 passthrough doesn't anticipate (e.g. a binary/file field) | Low | Low | Passthrough-only design (Decision 2) means no reshaping logic exists to be wrong about; worst case is an agent sees an unfamiliar field name in the schema it already has to read anyway |
| `reset_automations_to_default`'s restart (confirmed from source, bundled with `factory_reset`) interacts with the job-store active-job check this project applies to other restart-adjacent actions (ADR-0010/ADR-0012) | Low | Low | Apply the same `JobStore` active-backtest/data-collection-job check `restart_octobot`/`set_real_trading_enabled` already use, before either `start_automations` or `reset_automations_to_default` proceeds |

## Alternatives considered
- **Read-only `get_automations()` now, defer `update_automations`/
  `start_automations`/`reset_automations_to_default` to a future addendum**
  (the task's own suggested fallback). Rejected, explicitly, per the
  "Scope-down considered" discussion above: the write mechanism is not
  materially riskier or more complex than three tools this project has
  already shipped; deferring it would withhold safe capability for no
  corresponding safety gain, and would still leave the genuinely-new
  `start`/`factory_reset` actions unaddressed either way (they aren't made
  safer by also withholding the config write).
- **Model the automation event/condition/action schema explicitly as typed
  MCP tool parameters** (e.g. `add_automation_rule(event, conditions,
  actions)`) instead of an opaque `patch` blob. Rejected: the schema is
  dynamically described by OctoBot itself (`/automations_edit_details`'s own
  `schema` key) and can change between OctoBot versions/tentacle-package
  updates without this server's involvement — hardcoding it would violate
  the same NFR-8 "don't invent what OctoBot didn't confirm" discipline this
  project already applies to backtest reports, and would need re-deriving
  every time OctoBot adds a new automation building block.
- **Treat `start_automations` as ungated**, reasoning that it only "starts"
  something the user already explicitly saved via `update_automations`.
  Rejected: saving a config and deciding to let it run autonomously are two
  different, separable decisions in the real UI too (a save-only "Save"
  button exists distinctly from the "Apply"/start button) — collapsing them
  into one ungated step would remove the one deliberate pause point OctoBot
  itself already designed into this flow.

## Open questions (flagged, not guessed)
20. **The populated (`automations_count > 0`) nested event/condition/action
    schema shape** — confirmed this session only for the zero-automations
    case. A developer should configure at least one automation on a test
    instance (or trace `bot_automation.Automation`'s schema-generation code
    in source) to capture a real fixture before writing
    `get_automations()`'s test suite — not blocking for implementation
    (Decision 2's passthrough design tolerates this), but needed for
    meaningful test coverage beyond the trivial empty case.
21. **Whether `InvalidAutomationConfigError`'s message text ever contains
    anything that should be redacted** — unlikely (automation
    rules reference bot-internal event/condition/action names, not
    credentials), but not explicitly checked against a real invalid-config
    error message this session (none was triggered, since no automation was
    ever saved or started).

## Downstream artefacts (written alongside this ADR)
- `docs/requirements/octobot-mcp-requirements.md`: FR-42..FR-45, NFR-24,
  open questions 20–21 (above).
- `docs/specs/octobot-mcp-tool-spec.md`: a new "Automations" section with
  all four tools' full contracts.
- `docs/tasklist.md`: one new milestone (22), implementable directly from
  the spec section — live verification of the mechanism is already
  complete (this ADR's own Context); only the populated-schema fixture
  (open question 20) is marked as a non-blocking follow-up inside that
  milestone.
