"""Generic Tier-B `.config-element` scrape helper (task 21, docs/tasklist.md
item 21, ADR-0012, NFR-21).

Every prior Tier-B adapter in this project (`profiles.py`'s
`/profiles_selector`, `data_collection.py`'s `/data_collector`,
`trading_config.py`'s `/advanced/evaluator_config`, `portfolio.py`'s
`/portfolio`/`/advanced/matrix`) hardcodes the exact fields it expects to
find, field by field, because each of those pages' editable fields does NOT
self-describe its own type. The Accounts/Profile pages ADR-0012 investigated
(`/accounts`'s exchange-card deck/Interfaces/Notifications panels,
`/profile`'s Currencies panel) are different: EVERY editable field on all
four already carries a uniform, self-describing attribute convention --
`config-key` (the flat `POST /config` request-body key this field would be
submitted under), `config-type` (always the literal string `"global_config"`
for every field these four read tools scrape, confirmed live), `config-value`
(the field's current value, as a string), and `data-type` (one of `"bool"`/
`"number"`/`"text"`/`"list"`, confirmed live across every field captured this
session) -- all on an element carrying the CSS class `config-element`. This
module is therefore the one GENERIC adapter this project needed a fourth
distinct exception class for (`octobot_mcp.errors.
ConfigElementScrapeContractBrokenError`) rather than four more bespoke ones:
no per-field hardcoding is required at all, only a uniform per-`data-type`
value parser and a uniform "did the expected container/attributes exist"
fail-loud check.

## Confirmed value-encoding per `data-type` (live, this session, OctoBot
2.1.1, redacted real captures from `/accounts` and `/profile`)

- `"bool"`: the literal string `"True"`/`"False"` (same casing
  `trading_config.py`'s `list_evaluators` already confirmed for
  `current-value` on a DIFFERENT page's DIFFERENT attribute -- this project's
  established convention, not a new one).
- `"number"`: a plain decimal string (e.g. `"5001"` for
  `services_web_port`). Parsed as `int` when it round-trips through `int()`
  cleanly, else `float` -- no field observed live needed the `float` branch,
  but nothing in the `data-type="number"` convention rules one out (e.g. a
  future fractional config value), so this adapter supports both rather than
  assuming every number is an integer.
- `"text"`: the raw string verbatim (already HTML-entity-decoded by
  BeautifulSoup's own attribute parsing) -- no further processing.
- `"list"`: **a Python `repr()` of a list of strings, NOT a JSON array** --
  confirmed live, e.g. `crypto-currencies_Bitcoin_pairs`'s `config-value` is
  the literal (HTML-entity-decoded) string `"['BTC/EUR']"`, and
  `notification_notification-type`'s is `"['web', 'telegram']"`. A `json.loads`
  attempt on either would fail (single quotes are not valid JSON string
  delimiters) -- this is why this adapter uses `ast.literal_eval` (safe:
  evaluates only Python literals, never arbitrary code) rather than `json`,
  the one genuine implementation subtlety this generic design has to get
  right that a per-field adapter wouldn't need to think about at all.

## Fail-loud scope (NFR-21) -- deliberately narrower than every prior adapter

Every prior Tier-B adapter's fail-loud gate is scoped to its own one page's
entire expected structure. This one is scoped PER CALL: `find_config_container`
raises `ConfigElementScrapeContractBrokenError` only when the caller's target
container (`#exchange-container`, `#panelServices`, `#panelNotifications`,
`#panelCurrency` -- each tool names its own) is missing from the page
entirely, and `scrape_config_elements` raises it only when an individual
`.config-element` is missing `config-key`/`data-type`, or its `config-value`
doesn't parse against its own declared `data-type`. **Zero matched
`.config-element`s inside an intact container is explicitly NOT an error**
(ADR-0012 Decision 1, mirroring ADR-0011's "zero is not broken" precedent,
not `list_profiles`'/`list_evaluators`'s "zero is always broken" one) -- a
fresh OctoBot instance may legitimately have zero configured interface
services, and a profile may legitimately trade zero currencies. Each of the
four tool-specific call sites in `exchanges.py`/`interfaces.py`/`profiles.py`
is responsible for raising this same exception for ITS OWN additional
structural assumptions beyond "the container exists" (e.g. an exchange's
card missing its `enabled` field entirely) -- this module only guards the
generic, page-structure-agnostic half.

No `from __future__ import annotations` here would be harmless on its own
(this module defines no `Context`-typed tool function `with_error_handling`
wraps), but IS used, matching this project's established convention for
every other pure-helper, non-tool-registering module (`errors.py`,
`tools/_shared.py`, `jobs.py`, `eta.py`) -- see `tools/_shared.py`'s own
docstring for why a TOOL module specifically must not use it.
"""

from __future__ import annotations

import ast
from typing import Any

from bs4 import BeautifulSoup
from bs4.element import Tag

from octobot_mcp.errors import ConfigElementScrapeContractBrokenError

#: The `data-type` values this adapter knows how to parse a `config-value`
#: against -- confirmed live across every field captured this session
#: (ADR-0012 Context). Any other value is a scrape-contract break, not a
#: silently-passed-through raw string.
_KNOWN_DATA_TYPES = ("bool", "number", "text", "list")


def find_config_container(html: str, container_id: str, *, page_path: str) -> Tag:
    """Find the one element with `id=container_id` in `html`, or fail loud.

    Shared by every one of this milestone's four read tools -- each supplies
    its own page's HTML and its own container id (`#exchange-container`,
    `#panelServices`, `#panelNotifications`, `#panelCurrency`). Raises
    `ConfigElementScrapeContractBrokenError` (never returns `None`) if no
    element with that id exists -- this is the "the container itself is
    missing" half of NFR-21's fail-loud gate; it says nothing about whether
    anything is found INSIDE the container, which `scrape_config_elements`
    governs separately (zero is fine there).
    """
    soup = BeautifulSoup(html, "html.parser")
    container = soup.find(id=container_id)
    if container is None:
        raise ConfigElementScrapeContractBrokenError(
            f"No element with id={container_id!r} found on {page_path}. "
            "This means the page structure has changed -- verify against "
            "the current OctoBot version (see octobot_mcp.tools._config_scrape's "
            "module docstring for the last-confirmed version)."
        )
    return container


def _parse_config_value(data_type: str, raw_value: str | None, config_key: str) -> Any:
    """Parse one `.config-element`'s `config-value` per its own `data-type`.

    See this module's docstring for the confirmed live encoding of each of
    the four known `data-type`s. Raises `ConfigElementScrapeContractBrokenError`
    -- never guesses, never silently passes through an unparseable raw string
    -- the instant `raw_value` doesn't parse against `data_type`, or
    `data_type` itself isn't one of the four known values.
    """
    if raw_value is None:
        raise ConfigElementScrapeContractBrokenError(
            f"config-element {config_key!r} has no config-value attribute."
        )
    if data_type == "bool":
        if raw_value not in ("True", "False"):
            raise ConfigElementScrapeContractBrokenError(
                f"config-element {config_key!r}'s config-value is {raw_value!r}, "
                "expected the literal string 'True' or 'False' for data-type='bool'."
            )
        return raw_value == "True"
    if data_type == "number":
        try:
            return int(raw_value)
        except ValueError:
            pass
        try:
            return float(raw_value)
        except ValueError as exc:
            raise ConfigElementScrapeContractBrokenError(
                f"config-element {config_key!r}'s config-value is {raw_value!r}, "
                "which is not parseable as a number for data-type='number'."
            ) from exc
    if data_type == "text":
        return raw_value
    if data_type == "list":
        try:
            parsed = ast.literal_eval(raw_value)
        except (ValueError, SyntaxError) as exc:
            raise ConfigElementScrapeContractBrokenError(
                f"config-element {config_key!r}'s config-value is {raw_value!r}, "
                "which is not parseable as a Python list literal for "
                "data-type='list' (confirmed live: these render as a Python "
                "repr, e.g. \"['BTC/EUR']\", not a JSON array -- see this "
                "module's docstring)."
            ) from exc
        if not isinstance(parsed, list):
            raise ConfigElementScrapeContractBrokenError(
                f"config-element {config_key!r}'s config-value {raw_value!r} "
                f"parsed to a {type(parsed).__name__}, not a list, for "
                "data-type='list'."
            )
        return parsed
    raise ConfigElementScrapeContractBrokenError(
        f"config-element {config_key!r} has unrecognized data-type "
        f"{data_type!r}; expected one of {_KNOWN_DATA_TYPES!r}."
    )


def scrape_config_elements(container: Tag) -> list[dict[str, Any]]:
    """Parse every `.config-element` found inside `container` (any descendant depth, any tag).

    Returns one dict per element: `{"config_key": str, "data_type": str,
    "value": <parsed per _parse_config_value>}`, in document order. Raises
    `ConfigElementScrapeContractBrokenError` if any matched element is
    missing its `config-key` or `data-type` attribute, or if its
    `config-value` doesn't parse against its own `data-type` (see
    `_parse_config_value`). Returns an empty list -- never raises -- if
    `container` holds zero `.config-element`s (NFR-21's "zero is not
    broken" half; see this module's docstring).
    """
    elements: list[dict[str, Any]] = []
    for element in container.find_all(class_="config-element"):
        config_key = element.get("config-key")
        if not config_key:
            raise ConfigElementScrapeContractBrokenError(
                "A config-element is missing its config-key attribute "
                f"(tag: {element.name!r})."
            )
        data_type = element.get("data-type")
        if not data_type:
            raise ConfigElementScrapeContractBrokenError(
                f"config-element {config_key!r} is missing its data-type attribute."
            )
        value = _parse_config_value(data_type, element.get("config-value"), config_key)
        elements.append({"config_key": config_key, "data_type": data_type, "value": value})
    return elements


def find_entities_with_config_elements(
    container: Tag, *, entity_class: str = "card-body"
) -> list[tuple[str, Tag]]:
    """Find every per-entity sub-element inside `container` that carries both
    `class` containing `entity_class` and a non-empty `name` attribute.

    This is the shape `get_exchanges`/`get_interfaces`/`get_profile_currencies`
    all share (confirmed live, this session): each exchange/service/currency
    renders as its own `<div class="card-body" name="<entity>" ...>` (plus,
    for exchanges, extra classes alongside `card-body` -- BeautifulSoup's
    `class_` filter matches any ONE class token present, not an exact full
    `class` attribute match, so this works unchanged for all three). A
    sibling "Add a new <thing>" selector card the same pages render ALSO has
    `class="card-body"` but carries no `name` attribute at all, so it is
    naturally excluded by the `name`-presence filter -- confirmed live for
    all three pages' own "Add" selector markup, not assumed.

    `get_notification_routing`'s container has no such per-entity nesting at
    all (its fields are flat, directly inside the container) -- that tool
    calls `scrape_config_elements` on its container directly instead of this
    function.

    Returns `(name, entity_tag)` pairs in document order; an empty list if
    zero entities are found (not itself an error -- the caller decides
    whether zero is legitimate for its own domain).
    """
    entities: list[tuple[str, Tag]] = []
    for tag in container.find_all(class_=entity_class):
        name = tag.get("name")
        if name:
            entities.append((name, tag))
    return entities


def strip_prefix(config_key: str, prefix: str, *, context: str) -> str:
    """Strip a known, exact `prefix` off `config_key`, or fail loud.

    Deliberately NOT a `"_"`-splitting operation: `exchanges.py`'s own module
    docstring already documents the real OctoBot-side corruption risk of
    splitting a flat config-key on every literal underscore when an entity
    name itself contains one. Every caller of this function already knows
    the full, exact entity name from the page's own DOM structure (an
    attribute value, e.g. `name="binance"`/`name="telegram"`/
    `name="Bitcoin"`, never derived by splitting `config_key` itself), so the
    prefix this function strips is always `f"{domain}_{entity_name}_"` in
    full -- a single, exact string match, never an underscore split. Raises
    `ConfigElementScrapeContractBrokenError` (naming `context`, the calling
    tool/entity, for an actionable message) if `config_key` doesn't start
    with exactly `prefix`.
    """
    if not config_key.startswith(prefix):
        raise ConfigElementScrapeContractBrokenError(
            f"{context}: config-key {config_key!r} does not start with the "
            f"expected prefix {prefix!r}."
        )
    return config_key[len(prefix) :]
