"""Interface/service-integration and notification-routing tools (task 21,
docs/tasklist.md item 21, ADR-0012): `get_interfaces`, `update_interface_config`,
`get_notification_routing`, `update_notification_routing`.

A new module, not folded into `exchanges.py`, per the tasklist's own
instruction: the secret-suffix-based conditional gating (NFR-22/23) is
distinctive enough to warrant its own home. Both pages' reads go through the
generic `octobot_mcp.tools._config_scrape` Tier-B adapter (ADR-0012
Decision 1); both pages' writes go through the same `POST /config`
`global_config` route every other write in this addendum uses.

## `get_interfaces()`/`update_interface_config()`

Container: `/accounts`'s `#panelServices` (confirmed [V-live] this session,
Telegram/Web fully; 7 further services' existence/naming convention
confirmed, their exact per-field completeness is open question #18, non-
blocking since this adapter is generic). Each configured service renders as
its own `<div class="card-body" name="<service>" config-key="services_<service>">`
-- the same per-entity shape `exchanges.py`'s `get_exchanges`/`profiles.py`'s
`get_profile_currencies` rely on, via
`octobot_mcp.tools._config_scrape.find_entities_with_config_elements`.

**NFR-22/23's secret-suffix list** (case-insensitive field-name suffix
match, confirmed against ADR-0012 Decision 3): `token`, `secret`,
`password`, `api-key`, `api-secret`, `api-password`, `api-hash`,
`client-secret`. A field matches if its name (the part of `config-key`
AFTER the `services_<service>_` prefix is stripped -- e.g. `"token"` for
`services_telegram_token`) ends with any of these, case-insensitively. This
drives BOTH `get_interfaces`'s read-side redaction (every matching field's
value is replaced by `_redact_secret_value` below, never echoed in full) AND
`update_interface_config`'s write-side conditional confirm-gating (a `patch`
touching at least one matching key requires `confirm=true`; a patch with
none proceeds regardless, per ADR-0012 Decision 3 -- this is the one tool in
this project whose gating is per-call, not per-tool).

**Redaction format, a deliberate, slightly more conservative choice than
`update_exchange_credentials`'s existing `api_key_last4` convention**: that
tool's `api_key_last4` falls back to echoing a secret FULLY when it's
shorter than 4 characters (`api_key[-4:] if len(api_key) >= 4 else api_key`)
-- harmless there since that field's whole purpose is "show only the last
4 chars, however short that ends up being." Here, the field being redacted
KEEPS ITS ORIGINAL NAME (e.g. `"token"`), so a bare short value could be
mistaken for the real one. `_redact_secret_value` therefore never echoes a
secret value in full regardless of length: `f"***{value[-4:]}"` when
`len(value) >= 4`, else the fixed marker `"***"` alone.

## `get_notification_routing()`/`update_notification_routing()`

Container: `/accounts`'s `#panelNotifications` (confirmed [V-live]). Unlike
the three other new read tools, this container has NO per-entity nesting at
all -- every field is a direct, flat `.config-element` child, so this module
calls `scrape_config_elements` on the container directly rather than
`find_entities_with_config_elements`.

**A genuine spec correction, confirmed live this session**: the tool spec's
documented output shape names exactly 5 fields (`notification_type`,
`price_alerts`, `trades`, `trading_script_alerts`, `other`) -- but the live
`/accounts` page renders a SIXTH toggle, `notification_global-info`, not
mentioned anywhere in ADR-0012/the tool spec. Consistent with ADR-0012
Decision 1's own explicit design intent ("no per-field hardcoding
required"), this module does NOT drop that sixth field to match the spec's
example shape exactly -- it returns every `notification_*` field the
container actually renders, generically, with `global_info` included
alongside the five spec-named ones. Dropping a field OctoBot genuinely
exposes, to match a worked example in a design doc, would be the exact kind
of "invented/reshaped" behavior NFR-8 already forbids elsewhere in this
project (backtest report passthrough); the generic design this ADR itself
specifies is the actual contract, the five-field example is illustrative,
not exhaustive.

Field-name mapping (hyphen-to-underscore, confirmed to round-trip losslessly
for every field observed -- no `notification_*` field name contains a
literal underscore of its own): `config-key`'s `notification_` prefix is
stripped, then remaining hyphens are replaced with underscores (e.g.
`notification_notification-type` -> `notification_type`,
`notification_trading-script-alerts` -> `trading_script_alerts`).
`update_notification_routing` reverses this exactly (`field.replace("_",
"-")`) to rebuild the real `config-key`.

No `from __future__ import annotations` here: same reason as every other
tool module in this project (`Context`-typed functions `with_error_handling`
wraps) -- see `octobot_mcp.tools._shared.with_error_handling`'s docstring.
"""

from typing import Any

import httpx2 as httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context

from octobot_mcp.context import get_client
from octobot_mcp.errors import (
    OctoBotClientError,
    OctoBotConnectionError,
    OctoBotServerError,
    OctoBotTimeoutError,
)
from octobot_mcp.tools._config_scrape import (
    find_config_container,
    find_entities_with_config_elements,
    scrape_config_elements,
    strip_prefix,
)
from octobot_mcp.tools._shared import (
    _SECRET_FIELD_SUFFIXES,
    reject_underscore_in_config_path_segment,
    require_confirmation,
    with_error_handling,
)

_CONFIG_PATH = "/config"
_ACCOUNTS_PATH = "/accounts"
_PANEL_SERVICES_ID = "panelServices"
_PANEL_NOTIFICATIONS_ID = "panelNotifications"
_NOTIFICATION_PREFIX = "notification_"

#: NFR-22/23's secret-suffix list (ADR-0012 Decision 3), case-insensitive
#: field-NAME suffix match (after the `services_<service>_` prefix is
#: already stripped off `config-key`). Imported from `octobot_mcp.tools
#: ._shared` (confirmed defect fix: moved there as the single source of
#: truth so `_redact_logged_args`'s generic dict-valued-parameter redaction
#: uses the identical list, rather than this module keeping its own copy
#: that could drift from it).


def _is_secret_field(field: str, value: Any) -> bool:
    """Whether `field` (a service's field name, e.g. `"token"`) matches NFR-22/23's suffix list.

    Also requires `value` to be a string -- a deliberate, documented
    refinement found while implementing this module: a BOOLEAN toggle named
    `services_web_require-password` (whether password protection is enabled
    at all, confirmed live as a real `data-type="bool"` field) would
    otherwise literally match the `"password"` suffix by name alone, and
    get nonsensically redacted to `"***"` instead of staying `true`/`false`
    -- a name-only match is a reasonable heuristic for `data-type="text"`
    fields (the only kind a real credential VALUE can be), but produces a
    false positive for a same-named boolean/number/list field. NFR-22/23's
    concern is a VALUE being a secret, not a field's name alone.
    """
    if not isinstance(value, str):
        return False
    lowered = field.lower()
    return any(lowered.endswith(suffix) for suffix in _SECRET_FIELD_SUFFIXES)


def _redact_secret_value(value: Any) -> Any:
    """Redact a secret-suffix-matching field's value (NFR-23). See this module's docstring."""
    if isinstance(value, str) and len(value) >= 4:
        return f"***{value[-4:]}"
    return "***"


async def _request_raw(client: httpx.AsyncClient, method: str, path: str, **kwargs: Any) -> httpx.Response:
    """Perform one request without decoding JSON, classifying only transport failures.

    Mirrors `octobot_mcp.tools.exchanges._request_raw`/
    `octobot_mcp.tools.trading_config._request_raw` (independently
    duplicated per this project's established convention, not shared).
    """
    try:
        return await client.request(method, path, **kwargs)
    except httpx.TimeoutException as exc:
        raise OctoBotTimeoutError(f"Request to OctoBot timed out: {method} {path}") from exc
    except httpx.HTTPError as exc:
        raise OctoBotConnectionError(f"Could not reach OctoBot: {method} {path} ({exc})") from exc


def _extract_error_text(response: httpx.Response) -> str:
    """Best-effort extraction of OctoBot's own error text from a non-2xx response.

    Mirrors every other module's identical helper (`/config`'s failure
    branch is a bare JSON string, confirmed against source).
    """
    try:
        body = response.json()
    except ValueError:
        return response.text.strip() or f"HTTP {response.status_code} with an empty body"
    if isinstance(body, dict) and "text" in body:
        return str(body["text"])
    return str(body)


async def _fetch_accounts_html(client: httpx.AsyncClient) -> str:
    """Fetch `GET /accounts`'s raw HTML body, classified like `request_json` (NFR-4).

    Shared by both read tools in this module -- mirrors
    `octobot_mcp.tools.exchanges._fetch_accounts_html` (independently
    duplicated, not imported -- this project's established per-module
    convention for this exact helper shape).
    """
    response = await _request_raw(client, "GET", _ACCOUNTS_PATH)
    if response.status_code >= 500:
        raise OctoBotServerError(
            f"OctoBot returned server error {response.status_code} for GET {_ACCOUNTS_PATH}",
            status_code=response.status_code,
        )
    if response.status_code >= 400:
        raise OctoBotClientError(
            f"OctoBot returned client error {response.status_code} for GET {_ACCOUNTS_PATH}",
            status_code=response.status_code,
        )
    return response.text


# --- get_interfaces / update_interface_config --------------------------------


def _parse_interfaces(html: str) -> list[dict[str, Any]]:
    """Parse `/accounts`'s `#panelServices` into per-service field dicts (fail-loud, NFR-21).

    Secret-suffix-matching fields are redacted per NFR-23 -- the raw value
    is never present anywhere in this function's return value.
    """
    container = find_config_container(html, _PANEL_SERVICES_ID, page_path=_ACCOUNTS_PATH)
    services: list[dict[str, Any]] = []
    for name, entity in find_entities_with_config_elements(container):
        prefix = f"services_{name}_"
        fields: dict[str, Any] = {}
        for element in scrape_config_elements(entity):
            field = strip_prefix(element["config_key"], prefix, context=f"get_interfaces service={name!r}")
            value = element["value"]
            fields[field] = _redact_secret_value(value) if _is_secret_field(field, value) else value
        services.append({"service": name, "fields": fields})
    return services


@with_error_handling
async def get_interfaces(ctx: Context) -> Any:
    """List every configured interface/service's fields, with credentials redacted.

    Tier-B (ADR-0012 Decision 1): scrapes `GET /accounts`'s `#panelServices`
    panel via the generic `octobot_mcp.tools._config_scrape` adapter.

    Output: `{"services": [{"service": str, "fields": {<field name>: <value,
    or a redacted "***<last4>"/"***" if the field name matches NFR-22/23's
    secret-suffix list>}}]}`. Never includes a credential-shaped field's
    full value.

    Raises `ConfigElementScrapeContractBrokenError` only if `#panelServices`
    itself is missing -- never for zero configured services (a fresh
    instance may legitimately have none). Idempotent, read-only, not
    confirm-gated.
    """
    html = await _fetch_accounts_html(get_client(ctx))
    return {"services": _parse_interfaces(html)}


@with_error_handling
async def update_interface_config(
    ctx: Context, service: str, patch: dict[str, Any], confirm: bool = False
) -> Any:
    """Update one interface/service's config fields. Conditionally confirm-gated (ADR-0012 Decision 3).

    Requires `confirm=true` IFF `patch` contains at least one key matching
    NFR-22/23's secret-suffix list (case-insensitive) -- a `patch` touching
    only non-credential fields (e.g. `{"port": 8080}`) proceeds without
    `confirm` regardless. This is the ONE tool in this project whose gating
    is per-CALL, not per-tool (every other gated tool is gated
    unconditionally) -- see this module's docstring and ADR-0012 Decision 3.

    If gating applies and `confirm` is not exactly `true`, no OctoBot call
    is made at all -- returns `require_confirmation`'s structured refusal,
    naming which field(s) triggered it.

    Once past gating, maps to `POST /config`, `{"global_config":
    {"services_<service>_<field>": value, ...}}`.

    Output: `{"service": str, "updated_fields": {<field>: <value, or
    redacted if secret-suffix-matching>}}` -- secret-suffix-matching values
    are redacted in the OUTPUT the same way they are gated on input (NFR-23),
    never echoed in full even on success.

    Before any gating or OctoBot call, refuses locally (raising
    `ExchangeCredentialsUpdateRejectedError`) if `service` or any key in
    `patch` contains an underscore (confirmed defect fix): both are
    interpolated into the flat `"services_<service>_<field>"` config key
    above, the same config-path-corruption mechanism
    `exchanges.py`'s exchange-name guard already protects against -- see
    `octobot_mcp.tools._shared.reject_underscore_in_config_path_segment`'s
    docstring.
    """
    reject_underscore_in_config_path_segment("update_interface_config", "service", service)
    for field in patch:
        reject_underscore_in_config_path_segment("update_interface_config", "patch field", field)

    secret_fields = [field for field, value in patch.items() if _is_secret_field(field, value)]
    if secret_fields and confirm is not True:
        return require_confirmation(
            "update_interface_config",
            service,
            f"This patch touches credential-shaped field(s) {secret_fields!r} "
            "(matching the secret-suffix list token/secret/password/api-key/"
            "api-secret/api-password/api-hash/client-secret) -- verify the "
            "service and the intended value before writing it.",
        )

    global_config = {f"services_{service}_{field}": value for field, value in patch.items()}
    client = get_client(ctx)
    response = await _request_raw(client, "POST", _CONFIG_PATH, json={"global_config": global_config})
    if response.status_code >= 500:
        raise OctoBotServerError(
            f"OctoBot returned server error {response.status_code} updating "
            f"interface config for service={service!r}: {_extract_error_text(response)}",
            status_code=response.status_code,
        )
    if response.status_code >= 400:
        raise OctoBotClientError(
            f"OctoBot returned client error {response.status_code} updating "
            f"interface config for service={service!r}: {_extract_error_text(response)}",
            status_code=response.status_code,
        )

    updated_fields = {
        field: (_redact_secret_value(value) if _is_secret_field(field, value) else value)
        for field, value in patch.items()
    }
    return {"service": service, "updated_fields": updated_fields}


# --- get_notification_routing / update_notification_routing ------------------


def _notification_field_name(config_key: str) -> str:
    """`notification_<field-with-hyphens>` -> `<field_with_underscores>` (see this module's docstring)."""
    stripped = strip_prefix(config_key, _NOTIFICATION_PREFIX, context="get_notification_routing")
    return stripped.replace("-", "_")


def _parse_notification_routing(html: str) -> dict[str, Any]:
    """Parse `/accounts`'s `#panelNotifications` into a flat field dict (fail-loud, NFR-21).

    No per-entity nesting on this page -- every `.config-element` is a
    direct child of the container, so this scrapes it flat rather than via
    `find_entities_with_config_elements`.
    """
    container = find_config_container(html, _PANEL_NOTIFICATIONS_ID, page_path=_ACCOUNTS_PATH)
    return {
        _notification_field_name(element["config_key"]): element["value"]
        for element in scrape_config_elements(container)
    }


@with_error_handling
async def get_notification_routing(ctx: Context) -> Any:
    """Read which event categories are notified and which interfaces receive notifications.

    Tier-B (ADR-0012 Decision 1): scrapes `GET /accounts`'s
    `#panelNotifications` panel. Output includes every `notification_*`
    field the page actually renders (confirmed live:
    `notification_type`/`price_alerts`/`trades`/`trading_script_alerts`/
    `other`, PLUS `global_info` -- a field this tool's own docstring notes
    is not in the tool spec's worked example but IS rendered live; see this
    module's docstring for why it is included rather than dropped).

    Raises `ConfigElementScrapeContractBrokenError` only if
    `#panelNotifications` itself is missing. Idempotent, read-only, not
    confirm-gated.
    """
    html = await _fetch_accounts_html(get_client(ctx))
    return _parse_notification_routing(html)


@with_error_handling
async def update_notification_routing(ctx: Context, patch: dict[str, Any]) -> Any:
    """Update notification routing: which event categories notify, and which interfaces receive them.

    Input: `patch` maps field names in `get_notification_routing`'s own
    output shape (e.g. `"price_alerts"`, `"notification_type"`) to their new
    values; only provided fields are sent.

    Maps to `POST /config`, `{"global_config": {"notification_<field-with-
    hyphens>": value, ...}}` (reversing this module's underscore/hyphen
    mapping exactly -- see this module's docstring).

    Output: `{"updated_fields": patch}` (a plain echo of what was sent --
    OctoBot's own success response for this route is trustworthy by status
    code alone, the same established finding `update_exchange_credentials`'s
    docstring already documents for this exact route). Not confirm-gated
    (ADR-0012 Decision 4: mutates routing only, no credentials/destruction/
    live-trading involved).
    """
    global_config = {f"notification_{field.replace('_', '-')}": value for field, value in patch.items()}
    client = get_client(ctx)
    response = await _request_raw(client, "POST", _CONFIG_PATH, json={"global_config": global_config})
    if response.status_code >= 500:
        raise OctoBotServerError(
            f"OctoBot returned server error {response.status_code} updating "
            f"notification routing: {_extract_error_text(response)}",
            status_code=response.status_code,
        )
    if response.status_code >= 400:
        raise OctoBotClientError(
            f"OctoBot returned client error {response.status_code} updating "
            f"notification routing: {_extract_error_text(response)}",
            status_code=response.status_code,
        )
    return {"updated_fields": patch}


def register(mcp: MCPServer) -> None:
    """Register this module's tools onto `mcp` (called once from `octobot_mcp.tools.register_all`)."""
    mcp.tool()(get_interfaces)
    mcp.tool()(update_interface_config)
    mcp.tool()(get_notification_routing)
    mcp.tool()(update_notification_routing)
