"""Tier-B profile read adapter: HTML-scrape `/profiles_selector` (ADR-0006, milestone 4).

`list_profiles`/`get_profile` are the ONE narrow HTML-scrape adapter in this
project (ADR-0006 Tier B). No JSON API exists for profile discovery --
`controllers/profiles.py`'s `/profiles_selector` route only renders a Jinja
template -- so this module parses that page's rendered HTML with
BeautifulSoup instead. Every other tool module in this project wraps a
confirmed JSON route; this one does not, and its correctness is genuinely
load-bearing (an agent picks `profile_id`s from this output to later
delete/convert/select), so per NFR-12/INV-5 it is designed to positively
assert its structural assumptions and raise `ProfileScrapeContractBrokenError`
the instant one is violated, rather than ever return an empty/partial list
that would look indistinguishable from "no profiles exist."

No `from __future__ import annotations` here, for the same reason as
`portfolio.py`/`exchanges.py`: see
`octobot_mcp.tools._shared.with_error_handling`'s docstring.

## Verified DOM shape (OctoBot 2.1.1, confirmed live this session)

Re-fetched fresh from the live test instance
(`http://192.168.178.100:5001/profiles_selector`, `GET /api/version` ->
`"OctoBot 2.1.1"`) and cross-checked against OctoBot source
(`Drakkar-Software/OctoBot`, master branch, via `gh search code`/`gh api`)
rather than relying solely on a prior snapshot. 24 real profile cards were
observed; the assumptions below held for all 24.

- Each profile is one `<div class="col-12 col-md-6 col-lg-4 px-1
  profile-overview">` "card" (a fourth class, `profile-overview-selected`,
  is appended -- space-separated -- ONLY to the currently-selected
  profile's card; confirmed via `class="... profile-overview
  profile-overview-selected"` on exactly one of the 24 live cards). This
  CSS class is the primary `is_selected` signal.
- Nested inside each card (confirmed nested -- not scattered elsewhere in
  the DOM) is exactly one `<div class="profile-details d-flex pb-1"
  data-id="<profile_id>">`. `data-id` is the profile id. Its format is
  NOT uniform: built-in profiles use a slug (`arbitrage_trading`),
  user-created/imported profiles use a UUID
  (`a94936b1-9106-4cdc-9433-dc7dd2ccdd87`) -- both were observed live.
  Treated as an opaque string; never validated as a UUID.
- Name/description: `<span id="profile-name-<profile_id>">`/
  `<span id="profile-description-<profile_id>">`, also nested inside the
  same card. Text content, `.strip()`ped via BeautifulSoup's
  `get_text(strip=True)` (a real parser, so HTML entities are already
  decoded).
- Complexity/risk: a `<label for="<profile_id>profile-complexity">`
  (resp. `...profile-risk`) immediately followed, as a **sibling within the
  same parent `<div>`** (confirmed via source
  `templates/components/config/profiles.html`, not just observed), by a
  `<span class="badge ...">` whose text is the value (`"Difficult"`,
  `"Low"`, `"Moderate"` observed live). This adapter therefore uses
  `find_next_sibling`, scoped to that specific label, rather than a
  document-order `find_next` search that could silently latch onto an
  unrelated badge elsewhere on the page if the expected sibling were ever
  missing -- a `find_next_sibling` miss instead correctly returns `None`
  and trips the fail-loud check.
- Profile type (LIVE vs. SIMULATOR): a `<span class="float-right badge
  badge-info">` inside the card, whose text is exactly `"Real trading"` or
  `"Simulated trading"`. **This mapping is now source-confirmed, not just
  observed**: the badge text is the return value of
  `packages/tentacles/Services/Interfaces/web_interface/flask_util/context_processor.py::get_enabled_trader`,
  which returns `"Real trading"` iff
  `models.profiles.is_real_trading(profile)` (itself
  `trading_util.is_trader_enabled(profile.config)`) is true; otherwise
  `"Simulated trading"` iff `trading_util.is_trader_simulator_enabled` is
  true; **otherwise it returns `""`, and the template
  (`components/config/profiles.html`) omits the badge entirely** (`{% if
  get_enabled_trader(profile) %}`). So there is a genuine third case this
  adapter must not treat as broken: a profile with neither a real nor a
  simulated trader enabled renders NO type badge at all. This adapter maps
  that (unobserved live, but real per source) case to `profile_type:
  "UNKNOWN"` and surfaces it via `list_profiles`' `warning` field rather
  than failing loud -- it is a legitimate, if unusual, OctoBot state, not
  evidence of a broken scrape, *provided it's isolated to a minority of
  the scraped profiles* (see the majority-UNKNOWN safeguard below).

  **Known gap this safeguard only partially closes**: a per-badge check
  alone cannot tell "this one profile has no trader enabled" apart from
  "OctoBot renamed/restructured the `badge-info` CSS class this adapter
  looks for" -- both look identical to a per-profile scan: a card with no
  matching type badge. Since a real OctoBot instance is very unlikely to
  have *most or all* of its profiles in the "neither trader enabled"
  state simultaneously, `_parse_profiles_selector_html` treats a
  majority-UNKNOWN result across one scrape as itself suspicious enough
  to raise `ProfileScrapeContractBrokenError` rather than silently accept
  it (see "majority-UNKNOWN" below). This closes the gap for the
  class-rename case (which affects every profile at once) but is a
  probabilistic heuristic, not a structural guarantee: a hypothetical
  future OctoBot version/CSS change that broke type-badge detection for
  *fewer than half* of the profiles in one scrape would still slip
  through as individually-plausible `"UNKNOWN"` entries. Revisit this
  adapter if OctoBot's `badge-info`/`get_enabled_trader` contract changes.
- The selected card also carries a `<span class="badge badge-danger ...">
  active</span>` badge, confirmed present on exactly the one live card
  with `profile-overview-selected` and absent from all 23 others -- used
  below as a corroborating cross-check on `is_selected`, per NFR-12's
  "positively assert" spirit.
- **Correction to a prior hint**: the selected card was expected to also
  *lack* the `activate-profile-button` other cards carry. On the live
  OctoBot 2.1.1 instance this did NOT hold -- the selected card still has
  that button (it points at the same profile, effectively a no-op
  reselect). This adapter therefore does NOT use button-presence/absence
  as a signal at all; only the CSS class (primary) and the `active` badge
  (secondary, cross-checked below) are used.

## Fail-loud assumptions this adapter positively asserts (NFR-12/INV-5)

Before returning anything, `_parse_profiles_selector_html` raises
`ProfileScrapeContractBrokenError` if any of the following don't hold:
- at least one `profile-overview` card exists (OctoBot always ships its
  built-in profiles; zero cards means the page structure changed, not
  that zero profiles exist);
- every card has exactly one nested `profile-details` node with a
  non-empty `data-id`, and no `data-id` repeats across cards;
- every card has a name span, description span, complexity label+badge,
  and risk label+badge for its `data-id`;
- **exactly one** card (never zero, never more than one) carries the
  `profile-overview-selected` class;
- the `active`-text `badge-danger` cross-check badge appears on that same
  single selected card and nowhere else;
- **fewer than half** of the scraped profiles resolve to `profile_type:
  "UNKNOWN"` (no matching type badge) -- a strict majority (or all) of
  profiles landing in that bucket is treated as evidence the `badge-info`
  type-badge assumption itself broke (e.g. OctoBot renamed that CSS
  class), not that most profiles genuinely have no trader configured. See
  "Known gap" above for this heuristic's limits.
"""

import base64
import binascii
from typing import Any

import httpx2 as httpx
from bs4 import BeautifulSoup
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context

from octobot_mcp.context import get_client
from octobot_mcp.errors import (
    OctoBotClientError,
    OctoBotConnectionError,
    OctoBotMalformedResponseError,
    OctoBotServerError,
    OctoBotTimeoutError,
    ProfileConversionFailedError,
    ProfileDeleteRejectedError,
    ProfileImportFailedError,
    ProfileNotFoundError,
    ProfileScrapeContractBrokenError,
    ProfileSelectionFailedError,
    ProfileUpdateRejectedError,
)
from octobot_mcp.tools._shared import (
    _parse_content_disposition_filename,
    require_confirmation,
    with_error_handling,
)

_PROFILES_SELECTOR_PATH = "/profiles_selector"

#: Sent on every milestone-5 write request that can hit OctoBot's global 500
#: handler (`controllers/errors.py::internal_error`), which only returns a
#: readable JSON error string when the *request's* Content-Type was exactly
#: `application/json` -- otherwise it renders a full HTML error page.
#: Confirmed against source and reproduced live against the OctoBot 2.1.1
#: test instance: `GET /profiles_management/duplicate?profile_id=<bad-id>`
#: returns a 21KB HTML page without this header and the one-line JSON string
#: `"...: 'bad-id' (KeyError)"` with it. Harmless to send on a body-less GET.
_JSON_ERROR_HINT_HEADERS = {"Content-Type": "application/json"}

_VALID_CREATE_PROFILE_MODES = (
    "duplicate",
    "import_file",
    "import_url",
    "import_cloud_strategy",
)


#: Confirmed source-level mapping (see module docstring): any other/absent
#: badge text is a legitimate "neither trader enabled" state, not a broken
#: scrape -- mapped to "UNKNOWN" rather than raising.
_PROFILE_TYPE_BY_BADGE_TEXT = {
    "Real trading": "LIVE",
    "Simulated trading": "SIMULATOR",
}


async def _fetch_profiles_selector_html(client: httpx.AsyncClient) -> str:
    """Fetch `/profiles_selector`'s raw HTML body, classified like `request_json` (NFR-4).

    `octobot_mcp.client.request_json` can't be reused unchanged here: it
    decodes the response as JSON, and this route renders HTML (ADR-0006).
    This mirrors its timeout/connection/4xx/5xx classification branches for
    a text body instead, deliberately without touching `client.py`.
    """
    try:
        response = await client.request("GET", _PROFILES_SELECTOR_PATH)
    except httpx.TimeoutException as exc:
        raise OctoBotTimeoutError(
            f"Request to OctoBot timed out: GET {_PROFILES_SELECTOR_PATH}"
        ) from exc
    except httpx.HTTPError as exc:
        raise OctoBotConnectionError(
            f"Could not reach OctoBot: GET {_PROFILES_SELECTOR_PATH} ({exc})"
        ) from exc

    if response.status_code >= 500:
        raise OctoBotServerError(
            f"OctoBot returned server error {response.status_code} for "
            f"GET {_PROFILES_SELECTOR_PATH}",
            status_code=response.status_code,
        )
    if response.status_code >= 400:
        raise OctoBotClientError(
            f"OctoBot returned client error {response.status_code} for "
            f"GET {_PROFILES_SELECTOR_PATH}",
            status_code=response.status_code,
        )
    return response.text


def _parse_profiles_selector_html(html: str) -> list[dict[str, Any]]:
    """Parse `/profiles_selector`'s rendered HTML into profile dicts (fail-loud, NFR-12/INV-5).

    See this module's docstring for the exact DOM shape and the full list
    of assumptions asserted here. Raises `ProfileScrapeContractBrokenError`
    -- never returns an empty/partial list -- the instant any assumption
    doesn't hold.
    """
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.find_all("div", class_="profile-overview")
    if not cards:
        raise ProfileScrapeContractBrokenError(
            f"No profile-overview cards found on {_PROFILES_SELECTOR_PATH}. "
            "OctoBot always ships at least its built-in profiles, so this "
            "means the page structure has changed -- verify against the "
            "current OctoBot version (see octobot_mcp.tools.profiles's "
            "module docstring for the last-confirmed version)."
        )

    profiles: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    selected_ids: list[str] = []
    active_badge_ids: list[str] = []

    for card in cards:
        card_classes = card.get("class") or []
        is_selected = "profile-overview-selected" in card_classes

        details_divs = card.find_all("div", class_="profile-details")
        if len(details_divs) != 1:
            raise ProfileScrapeContractBrokenError(
                "Expected exactly one profile-details node per profile "
                f"card, found {len(details_divs)}."
            )
        profile_id = details_divs[0].get("data-id")
        if not profile_id:
            raise ProfileScrapeContractBrokenError(
                "A profile-details node is missing its data-id attribute."
            )
        if profile_id in seen_ids:
            raise ProfileScrapeContractBrokenError(
                f"Duplicate profile_id {profile_id!r} found across profile "
                "cards."
            )
        seen_ids.add(profile_id)

        name_span = card.find("span", id=f"profile-name-{profile_id}")
        description_span = card.find("span", id=f"profile-description-{profile_id}")
        if name_span is None or description_span is None:
            raise ProfileScrapeContractBrokenError(
                f"Missing name/description span for profile_id {profile_id!r}."
            )

        complexity_label = card.find(
            "label", attrs={"for": f"{profile_id}profile-complexity"}
        )
        risk_label = card.find("label", attrs={"for": f"{profile_id}profile-risk"})
        if complexity_label is None or risk_label is None:
            raise ProfileScrapeContractBrokenError(
                f"Missing complexity/risk label for profile_id {profile_id!r}."
            )
        complexity_badge = complexity_label.find_next_sibling("span", class_="badge")
        risk_badge = risk_label.find_next_sibling("span", class_="badge")
        if complexity_badge is None or risk_badge is None:
            raise ProfileScrapeContractBrokenError(
                f"Missing complexity/risk badge for profile_id {profile_id!r}."
            )

        type_badge = next(
            (
                span
                for span in card.find_all("span", class_="badge-info")
                if "float-right" in (span.get("class") or [])
            ),
            None,
        )
        type_text = type_badge.get_text(strip=True) if type_badge is not None else ""
        profile_type = _PROFILE_TYPE_BY_BADGE_TEXT.get(type_text, "UNKNOWN")

        if is_selected:
            selected_ids.append(profile_id)
        active_badges = [
            span
            for span in card.find_all("span", class_="badge-danger")
            if span.get_text(strip=True) == "active"
        ]
        if active_badges:
            active_badge_ids.append(profile_id)

        profiles.append(
            {
                "profile_id": profile_id,
                "name": name_span.get_text(strip=True),
                "description": description_span.get_text(strip=True),
                "risk": risk_badge.get_text(strip=True),
                "complexity": complexity_badge.get_text(strip=True),
                "profile_type": profile_type,
                "is_selected": is_selected,
            }
        )

    if len(selected_ids) != 1:
        raise ProfileScrapeContractBrokenError(
            "Expected exactly one profile card with the "
            f"profile-overview-selected class, found {len(selected_ids)}."
        )
    if active_badge_ids != selected_ids:
        raise ProfileScrapeContractBrokenError(
            "The 'active' badge cross-check disagrees with the "
            f"profile-overview-selected class: selected={selected_ids!r}, "
            f"active-badge={active_badge_ids!r}."
        )

    unknown_ids = [p["profile_id"] for p in profiles if p["profile_type"] == "UNKNOWN"]
    if unknown_ids and len(unknown_ids) * 2 >= len(profiles):
        # A strict majority (or all) of profiles resolving to "no matching
        # type badge" is itself suspicious: see this module's docstring
        # ("Known gap") -- a real OctoBot instance is very unlikely to have
        # most of its profiles with neither a real nor a simulated trader
        # enabled at once, so this is treated as the badge-info assumption
        # having broken (e.g. a renamed CSS class) rather than accepted as
        # a legitimate bulk "UNKNOWN" result.
        raise ProfileScrapeContractBrokenError(
            f"{len(unknown_ids)}/{len(profiles)} profiles have no "
            "recognizable trading-type badge (profile_type would be "
            "'UNKNOWN'): "
            f"{unknown_ids!r}. A majority of profiles in this state most "
            "likely means the 'float-right badge badge-info' type-badge "
            "assumption itself is broken (e.g. OctoBot renamed that CSS "
            "class), not that most profiles genuinely have no trader "
            "configured -- verify against the current OctoBot version."
        )

    return profiles


async def _fetch_and_parse_profiles(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """The one scrape both `list_profiles` and `get_profile` share (spec: "same scrape, filtered")."""
    html = await _fetch_profiles_selector_html(client)
    return _parse_profiles_selector_html(html)


# --- milestone 5: profile write tools (create/update/select/export) --------


async def _request_raw(
    client: httpx.AsyncClient, method: str, path: str, **kwargs: Any
) -> httpx.Response:
    """Perform one request without decoding JSON, classifying only transport failures.

    `octobot_mcp.client.request_json` can't be reused here (same reason as
    `_fetch_profiles_selector_html`, generalized): depending on the action
    and outcome, these routes return a bare JSON string, a JSON object, a
    full HTML page, an HTTP redirect with no useful body, or a binary zip
    -- never a single uniform shape `request_json` could decode. Only the
    transport-level failures (timeout, unreachable) are classified here;
    every call site classifies its own status codes, since e.g. a 302 means
    something different for `import_file` (a routine outcome) than for
    `duplicate` (never expected). NFR-4 is still satisfied: no raw httpx2
    exception ever escapes this function.
    """
    try:
        return await client.request(method, path, **kwargs)
    except httpx.TimeoutException as exc:
        raise OctoBotTimeoutError(f"Request to OctoBot timed out: {method} {path}") from exc
    except httpx.HTTPError as exc:
        raise OctoBotConnectionError(f"Could not reach OctoBot: {method} {path} ({exc})") from exc


def _extract_error_text(response: httpx.Response) -> str:
    """Best-effort extraction of OctoBot's own error text from a non-2xx response.

    Tries to decode a JSON body first (a bare string, or a dict carrying a
    `text` key -- both real shapes OctoBot's `profiles_management` actions
    and its global error handler return, confirmed against source and live).
    Falls back to the raw response text (e.g. an HTML error page, or
    werkzeug's own plain-text 4xx bodies) if the body isn't JSON.
    """
    try:
        body = response.json()
    except ValueError:
        return response.text.strip() or f"HTTP {response.status_code} with an empty body"
    if isinstance(body, dict) and "text" in body:
        return str(body["text"])
    return str(body)


def _find_new_profile(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    *,
    mode: str,
    detail: str,
) -> dict[str, Any]:
    """Identify the one profile that appeared between two `list_profiles` scrapes.

    `duplicate`, `import_file`, and `import_url` (unlike `import_cloud_strategy`)
    never return the new profile's id in their own OctoBot response -- see
    `create_profile`'s docstring for the source-verified detail -- so this
    tool determines it by diffing the existing Tier-B scrape immediately
    before and after the call, rather than a second/different scrape site.
    Raises `ProfileImportFailedError` (never guesses) if that diff doesn't
    find exactly one new profile: zero means the action likely failed
    silently; more than one means a concurrent profile change on this
    (possibly shared) OctoBot instance made the diff ambiguous.
    """
    before_ids = {profile["profile_id"] for profile in before}
    new_profiles = [profile for profile in after if profile["profile_id"] not in before_ids]
    if len(new_profiles) == 1:
        return new_profiles[0]
    found_ids = [profile["profile_id"] for profile in new_profiles]
    raise ProfileImportFailedError(
        f"create_profile(mode={mode!r}, {detail}): OctoBot's own response for "
        "this action never includes the new profile's id, so this tool "
        "identifies it by diffing list_profiles' scrape before/after the "
        "call. That diff found "
        f"{'no new profile' if not found_ids else f'{len(found_ids)} new profiles: {found_ids!r}'} "
        "instead of exactly one -- either the action silently failed, or "
        "another change to this OctoBot instance's profiles happened "
        "concurrently and made the diff ambiguous."
    )


@with_error_handling
async def list_profiles(ctx: Context) -> Any:
    """List every profile OctoBot knows about, including which one is selected.

    Tier-B (ADR-0006): scrapes `/profiles_selector` -- see this module's
    docstring for the verified DOM shape and fail-loud assumptions.
    Idempotent, read-only, not confirm-gated.

    `warning` is non-null only when one or more profiles have neither a
    real nor a simulated trader enabled in their config -- a legitimate
    OctoBot state (confirmed against source) this adapter reports as
    `profile_type: "UNKNOWN"` rather than failing loud, since it is not a
    broken-scrape condition.
    """
    profiles = await _fetch_and_parse_profiles(get_client(ctx))
    unknown_ids = [p["profile_id"] for p in profiles if p["profile_type"] == "UNKNOWN"]
    warning = (
        f"Profile(s) {', '.join(unknown_ids)} have neither a real nor a "
        "simulated trader enabled, so profile_type could not be "
        "determined from OctoBot's rendered page and is reported as "
        "'UNKNOWN' (see octobot_mcp.tools.profiles's module docstring)."
        if unknown_ids
        else None
    )
    return {"profiles": profiles, "warning": warning}


@with_error_handling
async def get_profile(ctx: Context, profile_id: str | None = None) -> Any:
    """Fetch one profile's details, defaulting to the currently selected profile.

    Calls the same scrape `list_profiles` uses and filters the result
    (spec: "same scrape, filtered") rather than scraping again. Raises
    `ProfileNotFoundError` if `profile_id` is given but matches no scraped
    profile -- distinct from `ProfileScrapeContractBrokenError`, which
    means the scrape itself is broken, not that this id doesn't exist.
    """
    profiles = await _fetch_and_parse_profiles(get_client(ctx))

    if profile_id is None:
        # `_parse_profiles_selector_html` guarantees exactly one selected
        # profile exists, or raises -- this can't fall through empty.
        return next(profile for profile in profiles if profile["is_selected"])

    for profile in profiles:
        if profile["profile_id"] == profile_id:
            return profile
    raise ProfileNotFoundError(f"No profile found with profile_id={profile_id!r}.")


def _require(value: Any, field_name: str, mode: str) -> Any:
    """Validate one `create_profile` mode's required argument was actually given."""
    if value is None:
        raise ProfileImportFailedError(
            f"create_profile(mode={mode!r}) requires {field_name!r}, but it was not provided."
        )
    return value


async def _create_profile_duplicate(client: httpx.AsyncClient, source_profile_id: str) -> dict[str, Any]:
    """`duplicate` mode: `GET /profiles_management/duplicate?profile_id=<id>` (spec [V], confirmed).

    OctoBot's success body is the literal JSON string `"Profile created"`
    (confirmed against source `controllers/configuration.py` and live) --
    it never includes the new profile's id or name, so `_find_new_profile`
    resolves it by diffing `list_profiles`' scrape (see that function's
    docstring). A bad `source_profile_id` raises an uncaught `KeyError`
    inside OctoBot (confirmed live: `.../duplicate?profile_id=does-not-exist`
    -> HTTP 500, body `"...: 'does-not-exist' (KeyError)"` with the
    `Content-Type: application/json` request header this call always sends).
    """
    before = await _fetch_and_parse_profiles(client)
    response = await _request_raw(
        client,
        "GET",
        "/profiles_management/duplicate",
        params={"profile_id": source_profile_id},
        headers=_JSON_ERROR_HINT_HEADERS,
    )
    if response.status_code >= 400:
        raise ProfileImportFailedError(
            f"OctoBot failed to duplicate profile_id={source_profile_id!r}: "
            f"{_extract_error_text(response)}"
        )
    after = await _fetch_and_parse_profiles(client)
    new_profile = _find_new_profile(
        before, after, mode="duplicate", detail=f"source_profile_id={source_profile_id!r}"
    )
    return {
        "profile_id": new_profile["profile_id"],
        "name": new_profile["name"],
        "message": "Profile created",
    }


async def _create_profile_import_file(
    client: httpx.AsyncClient, file_base64: str, filename: str
) -> dict[str, Any]:
    """`import_file` mode: `POST /profiles_management/import` (multipart `file`, spec [V]).

    **Spec correction (verified against source `controllers/configuration.py`,
    action `"import"`)**: this route ALWAYS responds with an HTTP redirect
    (302 to `/profile`, confirmed live) regardless of success or failure --
    it never returns JSON at all, unlike `"download"` below. Any outcome
    (including a failed import: the controller catches the failure
    internally and only flashes a warning into the redirect's session
    cookie, which this tool does not attempt to decode -- an
    itsdangerous-signed but unencrypted implementation detail, too fragile
    to build on) is therefore invisible at the HTTP level. This tool
    determines the outcome the same way as `duplicate`: diffing
    `list_profiles`' scrape before/after the call (see `_find_new_profile`).
    """
    try:
        file_bytes = base64.b64decode(file_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ProfileImportFailedError(
            f"create_profile(mode='import_file'): file_base64 is not valid base64 ({exc})."
        ) from exc

    before = await _fetch_and_parse_profiles(client)
    response = await _request_raw(
        client,
        "POST",
        "/profiles_management/import",
        files={"file": (filename, file_bytes)},
    )
    if response.status_code >= 400:
        raise ProfileImportFailedError(
            f"OctoBot failed to import profile file {filename!r}: {_extract_error_text(response)}"
        )
    after = await _fetch_and_parse_profiles(client)
    new_profile = _find_new_profile(before, after, mode="import_file", detail=f"filename={filename!r}")
    return {
        "profile_id": new_profile["profile_id"],
        "name": new_profile["name"],
        "message": "Profile created",
    }


async def _create_profile_import_url(client: httpx.AsyncClient, url: str) -> dict[str, Any]:
    """`import_url` mode: `POST /profiles_management/download` with form field `inputProfileLink` (spec [V]).

    **Spec correction -- a genuine OctoBot bug, not a misunderstanding of
    the spec (verified against source AND reproduced live against the
    OctoBot 2.1.1 test instance)**: `controllers/configuration.py`'s
    `"download"` action unconditionally evaluates
    `flask.request.json.get('strategy_id')` before ever checking whether a
    form-encoded `inputProfileLink` was supplied. Flask/Werkzeug raises
    `UnsupportedMediaType` (415) the instant `.json` is accessed on a
    request whose Content-Type isn't `application/json` -- and OctoBot's
    own template (`components/config/profiles.html`'s
    `.profile-download-form`, confirmed by reading it) submits this action
    as a plain `method="POST"` HTML form with no `enctype` override, i.e.
    `application/x-www-form-urlencoded`, the same shape this tool sends.
    Reproduced live: `curl -d "inputProfileLink=..."` AND
    `curl -F "inputProfileLink=..."` (urlencoded and multipart) both return
    an identical HTTP 415 with body "Did not attempt to load JSON data
    because the request Content-Type was not 'application/json'." There is
    no standard request shape that is simultaneously valid
    `application/x-www-form-urlencoded`/`multipart/form-data` (required for
    `.form.get('inputProfileLink')` to see the URL at all) AND
    `application/json` (required for the unconditional `.json.get(...)`
    call three lines later not to raise) -- so, as literally coded, this
    OctoBot route is presently unreachable via `import_url` by ANY HTTP
    client, including OctoBot's own browser UI. This tool still sends the
    spec-correct/OctoBot-UI-matching request rather than a nonstandard
    workaround, so `import_url` will fail with a `ProfileImportFailedError`
    wrapping that 415 message until OctoBot fixes this upstream.
    """
    before = await _fetch_and_parse_profiles(client)
    response = await _request_raw(
        client,
        "POST",
        "/profiles_management/download",
        data={"inputProfileLink": url},
    )
    if response.status_code >= 400:
        raise ProfileImportFailedError(
            f"OctoBot failed to import profile from url={url!r}: {_extract_error_text(response)}"
        )
    after = await _fetch_and_parse_profiles(client)
    new_profile = _find_new_profile(before, after, mode="import_url", detail=f"url={url!r}")
    return {
        "profile_id": new_profile["profile_id"],
        "name": new_profile["name"],
        "message": "Profile created",
    }


async def _create_profile_import_cloud_strategy(
    client: httpx.AsyncClient, strategy_id: str, name: str, description: str
) -> dict[str, Any]:
    """`import_cloud_strategy` mode: `POST /profiles_management/download` with a JSON body (spec [V]).

    Unlike the other 3 modes, OctoBot's own success response DOES include
    the new profile's id (`{"text": <message>, "profile_id": <id>}`,
    confirmed against source and live with a deliberately-invalid
    `strategy_id`, which instead returns HTTP 400 with `profile_id: ""` and
    a real error message: `"Error when importing profile: '<strategy_id>'"`)
    -- no scrape-diffing needed for this mode.
    """
    response = await _request_raw(
        client,
        "POST",
        "/profiles_management/download",
        json={"strategy_id": strategy_id, "name": name, "description": description},
    )
    if response.status_code >= 400:
        raise ProfileImportFailedError(
            f"OctoBot failed to import cloud strategy strategy_id={strategy_id!r}: "
            f"{_extract_error_text(response)}"
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise OctoBotMalformedResponseError(
            "OctoBot returned a 2xx response with a non-JSON body for "
            "POST /profiles_management/download (import_cloud_strategy)."
        ) from exc
    profile_id = body.get("profile_id") if isinstance(body, dict) else None
    if not profile_id:
        raise ProfileImportFailedError(
            "OctoBot reported success importing cloud strategy "
            f"strategy_id={strategy_id!r} but returned no profile_id: {body!r}."
        )
    return {
        "profile_id": profile_id,
        "name": name,
        "message": body.get("text", "Profile created") if isinstance(body, dict) else "Profile created",
    }


@with_error_handling
async def create_profile(
    ctx: Context,
    mode: str,
    source_profile_id: str | None = None,
    file_base64: str | None = None,
    filename: str | None = None,
    url: str | None = None,
    strategy_id: str | None = None,
    name: str | None = None,
    description: str | None = None,
) -> Any:
    """Create a new profile, one of 4 ways, discriminated by `mode`.

    Not confirm-gated (ADR-0003): creates new state, never destroys
    anything or touches credentials/live trading. Every mode's exact
    request shape was verified against OctoBot source
    (`controllers/configuration.py`'s `profiles_management` route) and,
    where practical, against the live test instance -- see each
    `_create_profile_*` helper's docstring for the per-mode detail and the
    two spec corrections found along the way (`import_file` never returns
    JSON; `import_url` is presently broken upstream). Only the arguments
    relevant to the chosen `mode` need to be supplied; the rest are
    ignored:

    - `mode="duplicate"`: requires `source_profile_id`.
    - `mode="import_file"`: requires `file_base64` (the file's raw bytes,
      base64-encoded) and `filename`.
    - `mode="import_url"`: requires `url`.
    - `mode="import_cloud_strategy"`: requires `strategy_id` and `name`;
      `description` is optional (defaults to `""`).

    Output: `{"profile_id": str, "name": str, "message": str}`.

    Raises `ProfileImportFailedError` for any failure in any mode
    (including a missing required argument for the chosen `mode`, or an
    unrecognized `mode`) -- OctoBot's own error text is included where one
    exists.
    """
    client = get_client(ctx)
    if mode == "duplicate":
        return await _create_profile_duplicate(
            client, _require(source_profile_id, "source_profile_id", mode)
        )
    if mode == "import_file":
        return await _create_profile_import_file(
            client,
            _require(file_base64, "file_base64", mode),
            _require(filename, "filename", mode),
        )
    if mode == "import_url":
        return await _create_profile_import_url(client, _require(url, "url", mode))
    if mode == "import_cloud_strategy":
        return await _create_profile_import_cloud_strategy(
            client,
            _require(strategy_id, "strategy_id", mode),
            _require(name, "name", mode),
            description or "",
        )
    raise ProfileImportFailedError(
        f"create_profile: unknown mode {mode!r}; expected one of {_VALID_CREATE_PROFILE_MODES!r}."
    )


@with_error_handling
async def update_profile(ctx: Context, profile_id: str, patch: dict[str, Any]) -> Any:
    """Update an existing profile's metadata (never the active/selected profile's name).

    Maps to `POST /profiles_management/update` with JSON body
    `{"id": profile_id, **patch}` (confirmed against source
    `models/profiles.py::update_profile()`, which reads exactly this shape).

    **Spec corrections, both verified against source and reproduced live**:
    - `patch["risk"]`/`patch["complexity"]` must be an int (or int-parseable
      string) matching OctoBot's enum ordinals -- **not** the display
      strings `list_profiles` returns (`"Low"`, `"Difficult"`, ...). Source
      (`octobot_commons.enums`): `ProfileRisk` is `LOW=1, MODERATE=2, HIGH=3`;
      `ProfileComplexity` is `EASY=1, MEDIUM=2, DIFFICULT=3`. Sending a
      display string (confirmed live) raises an uncaught `ValueError`
      inside OctoBot, surfaced by this tool as a `ProfileUpdateRejectedError`
      wrapping `"invalid literal for int() with base 10: 'Moderate'"`,
      HTTP 500 -- not the clean `UPDATE_REJECTED` 400 path.
    - `patch["config"]` is accepted by this tool's input schema for
      forward-compatibility with the documented spec shape, but is
      currently silently ignored by OctoBot's deployed route: the
      controller only ever calls `models.update_profile(id, data)` --
      never passing the third `json_profile_content` parameter that is the
      only thing `update_profile()` actually assigns to `profile.config`.
      There is no way for this tool to make `config` take effect against
      the current OctoBot version.
    - Renaming the currently-selected profile is rejected by OctoBot itself
      (`"Can't rename the active profile"`, confirmed live), surfaced here
      as `ProfileUpdateRejectedError` with that exact message.

    Output: `{"profile_id": str, "updated_fields": object}` (`updated_fields`
    echoes the `patch` this tool sent -- OctoBot's own success response is
    the same data echoed back, so there is nothing more to report).

    Not confirm-gated (ADR-0003): does not destroy anything or touch
    credentials/live trading.
    """
    client = get_client(ctx)
    response = await _request_raw(
        client,
        "POST",
        "/profiles_management/update",
        json={"id": profile_id, **patch},
    )
    if response.status_code >= 400:
        raise ProfileUpdateRejectedError(
            f"OctoBot rejected updating profile_id={profile_id!r}: {_extract_error_text(response)}"
        )
    return {"profile_id": profile_id, "updated_fields": patch}


@with_error_handling
async def select_profile(ctx: Context, profile_id: str) -> Any:
    """Select a profile as OctoBot's current/active profile.

    Maps to `GET /profile?select=<profile_id>` (spec [V]).

    **Spec correction, verified against source
    (`controllers/configuration.py::profile()`) and reproduced live**: this
    route always responds HTTP 200 with a full HTML page, whether or not
    the selection actually happened -- an unknown `profile_id` makes
    `models.select_profile()` raise `NoProfileError`, which the route
    catches internally, flashes a warning ("The requested profile no
    longer exists.") into an HTML-only, session-cookie-based flash message,
    and re-renders the PREVIOUSLY-selected profile's page, still with a 200
    status. There is no way to distinguish success from this silent-failure
    case from the HTTP response alone. This tool therefore always
    re-fetches the currently-selected profile afterward (the same scrape
    `list_profiles`/`get_profile` use) and raises
    `ProfileSelectionFailedError` if it doesn't match `profile_id`, rather
    than trusting the 200 status.

    Output: `{"profile_id": str, "selected": true}`. Idempotent (selecting
    the already-selected profile is a verified no-op on OctoBot's side) and
    not confirm-gated (ADR-0003).
    """
    client = get_client(ctx)
    response = await _request_raw(
        client,
        "GET",
        "/profile",
        params={"select": profile_id},
        headers=_JSON_ERROR_HINT_HEADERS,
    )
    if response.status_code >= 500:
        raise OctoBotServerError(
            f"OctoBot returned server error {response.status_code} selecting "
            f"profile_id={profile_id!r}: {_extract_error_text(response)}",
            status_code=response.status_code,
        )
    if response.status_code >= 400:
        raise OctoBotClientError(
            f"OctoBot returned client error {response.status_code} selecting "
            f"profile_id={profile_id!r}: {_extract_error_text(response)}",
            status_code=response.status_code,
        )

    profiles = await _fetch_and_parse_profiles(client)
    current = next((profile for profile in profiles if profile["is_selected"]), None)
    still_selected_id = current["profile_id"] if current is not None else None
    if still_selected_id != profile_id:
        raise ProfileSelectionFailedError(
            f"OctoBot did not select profile_id={profile_id!r} -- after the "
            f"request, the selected profile is still {still_selected_id!r}. "
            "This most likely means profile_id doesn't exist: verified "
            "against source, GET /profile?select=<id> silently keeps the "
            "previous profile selected on an unknown id rather than "
            "returning any failing HTTP status (see this tool's docstring)."
        )
    return {"profile_id": profile_id, "selected": True}


@with_error_handling
async def export_profile(ctx: Context, profile_id: str) -> Any:
    """Export a profile as a zip archive, base64-encoded.

    Maps to `GET /profiles_management/export?profile_id=<profile_id>`
    (spec [V]; `profile_id` confirmed as the exact query-param name against
    source `controllers/configuration.py`, action `"export"` -- not `id`).
    This route returns the zip's raw bytes directly (`flask.send_file`),
    not JSON, so this tool bypasses `octobot_mcp.client.request_json`
    (same reason as `_fetch_profiles_selector_html`) and base64-encodes the
    raw response body instead.

    `filename` is read from the response's `Content-Disposition` header
    (confirmed live: `attachment; filename=<profile name>_<YYYYMMDD-HHMMSS>.zip`,
    quoted only when the profile name isn't already a single HTTP token --
    e.g. it contains a space -- per RFC 6266/2183; both forms confirmed
    live, see `octobot_mcp.tools._shared._parse_content_disposition_filename`,
    moved there in milestone 13 so `octobot_mcp.tools.tentacles.export_logs`
    shares this same implementation) rather than reconstructed locally, so
    it always matches exactly what OctoBot named the file. Raises
    `OctoBotMalformedResponseError` if that header is missing or
    unparseable -- never returns a fabricated filename.

    Output: `{"profile_id": str, "filename": str, "content_base64": str}`.
    Read-only, not confirm-gated (ADR-0003). An unknown `profile_id` raises
    an uncaught `NoProfileError` inside OctoBot (HTTP 500, confirmed live
    with the `Content-Type: application/json` request header this call
    always sends for a readable error message).
    """
    client = get_client(ctx)
    response = await _request_raw(
        client,
        "GET",
        "/profiles_management/export",
        params={"profile_id": profile_id},
        headers=_JSON_ERROR_HINT_HEADERS,
    )
    if response.status_code >= 500:
        raise OctoBotServerError(
            f"OctoBot returned server error {response.status_code} exporting "
            f"profile_id={profile_id!r}: {_extract_error_text(response)}",
            status_code=response.status_code,
        )
    if response.status_code >= 400:
        raise OctoBotClientError(
            f"OctoBot returned client error {response.status_code} exporting "
            f"profile_id={profile_id!r}: {_extract_error_text(response)}",
            status_code=response.status_code,
        )

    content_disposition = response.headers.get("content-disposition", "")
    filename = _parse_content_disposition_filename(content_disposition)
    if not filename:
        raise OctoBotMalformedResponseError(
            f"OctoBot's export response for profile_id={profile_id!r} had no "
            f"parsable filename in its Content-Disposition header ({content_disposition!r})."
        )
    return {
        "profile_id": profile_id,
        "filename": filename,
        "content_base64": base64.b64encode(response.content).decode("ascii"),
    }


# --- milestone 6: the first two confirm-gated profile tools (ADR-0003) ------


@with_error_handling
async def delete_profile(ctx: Context, profile_id: str, confirm: bool = False) -> Any:
    """Permanently delete a profile. Confirm-gated (ADR-0003): irreversible.

    If `confirm` is not exactly `true`, no OctoBot call is made at all --
    this returns `require_confirmation`'s structured refusal (a normal
    return, not an error) instead.

    Once confirmed, maps to `POST /profiles_management/remove` with JSON
    body `{"id": profile_id}` (confirmed against source
    `controllers/configuration.py`'s `"remove"` action, which reads exactly
    `flask.request.get_json()["id"]` -- the spec's documented body shape is
    correct here, unlike `update_profile`'s in milestone 5).

    **Two failure modes confirmed against source
    (`models/profiles.py::remove_profile`), both surfaced as
    `ProfileDeleteRejectedError`**:
    - `profile_id` is the currently-selected/active profile: OctoBot itself
      rejects this (`"Can't remove the active profile"`), returned as HTTP
      400 with that exact string as the JSON body.
    - `profile_id` names a profile whose removal a `ProfileRemovalError`
      blocks (e.g. an in-use profile): also HTTP 400, wrapping that error's
      text.
    An unrecognized `profile_id` isn't checked by `remove_profile` before
    this tool ever reaches OctoBot: it's rejected locally as
    `ProfileNotFoundError` (via the same pre-fetched scrape used to look up
    `name`, below) rather than let OctoBot's own uncaught `KeyError` produce
    an HTTP 500.

    **Extra safety check, not strictly required by OctoBot's own response
    but added anyway**: OctoBot's 200 success body for this action is the
    same uninformative literal string `"Profile created"` copied from the
    unrelated `duplicate` action (confirmed against source) -- not
    "Profile deleted" or anything that actually confirms removal. Given
    that copy-paste bug, this tool re-checks via the same scrape
    `list_profiles`/`get_profile` use that `profile_id` is genuinely gone
    afterward, raising `ProfileDeleteRejectedError` (not trusting the 200)
    if it is still present.

    The profile's `name` is captured from a scrape taken *before* deletion
    (per this tool's spec entry), since after a real deletion it can no
    longer be found by scraping.

    Output on success: `{"profile_id": str, "name": str, "deleted": true}`.
    """
    if confirm is not True:
        return require_confirmation(
            "delete_profile",
            profile_id,
            "This permanently deletes the profile and cannot be undone.",
        )

    client = get_client(ctx)
    before = await _fetch_and_parse_profiles(client)
    matched = next((profile for profile in before if profile["profile_id"] == profile_id), None)
    if matched is None:
        raise ProfileNotFoundError(f"No profile found with profile_id={profile_id!r}.")

    response = await _request_raw(
        client,
        "POST",
        "/profiles_management/remove",
        json={"id": profile_id},
    )
    if response.status_code >= 500:
        raise OctoBotServerError(
            f"OctoBot returned server error {response.status_code} deleting "
            f"profile_id={profile_id!r}: {_extract_error_text(response)}",
            status_code=response.status_code,
        )
    if response.status_code >= 400:
        raise ProfileDeleteRejectedError(
            f"OctoBot rejected deleting profile_id={profile_id!r}: {_extract_error_text(response)}"
        )

    after = await _fetch_and_parse_profiles(client)
    if any(profile["profile_id"] == profile_id for profile in after):
        raise ProfileDeleteRejectedError(
            f"OctoBot reported success deleting profile_id={profile_id!r}, "
            "but it is still present after re-scraping /profiles_selector "
            "-- OctoBot's success response for this action is an "
            "uninformative literal string ('Profile created', copy-pasted "
            "from the unrelated 'duplicate' action; see this tool's "
            "docstring), so this tool never trusts it alone."
        )
    return {"profile_id": profile_id, "name": matched["name"], "deleted": True}


@with_error_handling
async def convert_profile_to_live(ctx: Context, profile_id: str, confirm: bool = False) -> Any:
    """Relabel a profile as OctoBot's internal `ProfileType.LIVE` and select it. Confirm-gated (ADR-0003).

    **This tool does NOT enable real-money trading -- confirmed against
    OctoBot source, not just suspected (resolves requirements doc open
    question #5).** Whether OctoBot actually places real orders is governed
    entirely by a separate config flag, `config.trader.enabled`, checked by
    OctoBot's own `is_real_trading(profile)` (semantically: returns
    `trading_util.is_trader_enabled(profile.config)`, itself
    `config[CONFIG_TRADER][CONFIG_ENABLED_OPTION]` -- both confirmed against
    source, not a verbatim one-line quote of the actual multi-statement
    function body). `models.convert_to_live_profile` and `models.select_profile`
    (the two functions this tool's route calls -- see "Spec correction #1"
    below) both leave `config.trader`/`config.trader-simulator` completely
    untouched; only `profile.profile_type` (a label) changes. The one
    HTTP-visible route this project found that DOES set `config.trader.enabled`
    (`_save_distribution_user_config`, reached via `save_prediction_market_configuration`
    in OctoBot's web-interface source) is registered only when the running
    OctoBot instance's distribution is `OctoBotDistribution.PREDICTION_MARKET`
    -- an `elif` branch mutually exclusive with the `DEFAULT` distribution
    this project assumes throughout (confirmed against
    `controllers/__init__.py::register()`); it has nothing to do with
    OctoBot's onboarding wizard (a separate, always-registered
    `controllers/welcome.py`). This route is therefore unreachable on the
    ordinary/default OctoBot instance this server targets, not because of
    this project's own "onboarding" out-of-scope exclusion (a previous
    version of this docstring cited that exclusion; it was the wrong
    reason, even though the practical conclusion below still holds).
    **As of this writing, this server has NO tool that can actually toggle
    real-vs-simulated trading on a default OctoBot instance.** If you need
    that, it currently requires editing `config.trader`/`config.trader-simulator`
    in the profile's own saved config file directly (outside this server)
    and re-importing/re-selecting the profile -- there is no safer,
    HTTP-API-driven path this server can offer today.

    If `confirm` is not exactly `true`, no OctoBot call is made at all --
    this returns `require_confirmation`'s structured refusal (a normal
    return, not an error) instead.

    Once confirmed, maps to `GET /profiles_management/use_as_live?profile_id=<id>`
    (confirmed against source `controllers/configuration.py`'s
    `"use_as_live"` action).

    **Spec correction #1, verified against source and reproduced live**:
    this route's one non-raising code path calls
    `models.convert_to_live_profile(profile_id)` then
    `models.select_profile(profile_id)`, flashes an HTML-only session
    message, and returns `flask.redirect(flask.url_for("profile"))` -- an
    HTTP 302 to `/profile` with no informative body at all, whether or not
    the conversion actually took effect. This is the same
    uninformative-GET-response category milestone 5 already found for
    `duplicate` (also a bare success marker) and `select_profile` (a 200
    that can silently mean "nothing changed"). Following the same pattern
    `select_profile` established, this tool never trusts the response
    status alone: it re-fetches via the same scrape `list_profiles`/
    `get_profile` use afterward and raises `ProfileConversionFailedError`
    if `profile_id` isn't selected there (see spec correction #2 for why
    only `is_selected`, not `profile_type`, is re-checked this way).

    **Spec correction #2 -- a genuine terminology collision in OctoBot's own
    data model, found live and confirmed against source, NOT just a
    misreading of this milestone's own instructions**: `is_selected`/
    `profile_type` were expected to both be independently re-verifiable
    post-hoc via the same scrape `list_profiles` uses (mirroring
    `select_profile`'s pattern). `is_selected` is: the
    `profile-overview-selected` CSS class this scrape already parses
    reliably reflects `models.select_profile(profile_id)`'s effect.
    `profile_type`, however, is NOT reverified here, because the scrape's
    `profile_type` field (`list_profiles`/`get_profile`'s "LIVE"/
    "SIMULATOR"/"UNKNOWN") and the `profile.profile_type` attribute
    `models.convert_to_live_profile` actually sets are two unrelated
    OctoBot concepts that merely share a confusingly similar name:
    - The scrape's `profile_type` comes from the `badge-info` badge text,
      which is the return value of `get_enabled_trader(profile)`
      (confirmed against source `flask_util/context_processor.py`):
      `"Real trading"` iff `trading_util.is_trader_enabled(profile.config)`,
      else `"Simulated trading"` iff `is_trader_simulator_enabled`, else no
      badge at all -- entirely about whether a real (vs. simulated)
      *trader* is enabled in that profile's OWN trading config.
    - `models.convert_to_live_profile` (confirmed against source
      `models/profiles.py`) only does `profile.profile_type =
      commons_enums.ProfileType.LIVE; profile.validate_and_save_config()`.
      `octobot_commons.enums.ProfileType` (confirmed against source) has
      exactly two members, `LIVE = "live"` and `BACKTESTING = "backtesting"`
      -- there is no `SIMULATOR` member at all, and this attribute is never
      rendered anywhere in `/profiles_selector`'s HTML (confirmed by reading
      `components/config/profiles.html` and `context_processor.py`: neither
      references `profile.profile_type`).

    Concretely (reproduced live against a freshly-duplicated, default
    SIMULATOR-trader profile): after a successful `convert_profile_to_live`
    call, the scrape's `profile_type` for that profile is still
    `"SIMULATOR"` -- `convert_to_live_profile` never touches the
    trader-enabled config the badge reflects. Gating this tool's success on
    `profile_type == "LIVE"` (as this milestone's own instructions
    originally called for) would therefore make it report failure on
    essentially every real invocation, which is worse than not checking it
    at all. There is no OctoBot HTTP-visible signal (Tier A or B) this
    server can use to independently confirm `profile.profile_type` flipped
    -- an acknowledged, documented gap (the same category as
    `update_profile`'s undeliverable `config` field from milestone 5), not
    a silently-accepted assumption. This tool's `"profile_type": "LIVE"` in
    its own output below is therefore an echo of what was requested (and,
    per `convert_to_live_profile`'s unconditional, non-branching
    implementation, reliably applied whenever this call doesn't raise --
    see spec correction #3), not an independently re-scraped fact.

    **Spec correction #3, on why a non-5xx response is nonetheless a
    reasonably strong signal for the `profile_type` half specifically**:
    unlike `remove_profile`'s check-and-return-`(result, err)` pattern,
    `convert_to_live_profile`'s body (confirmed against source) has no
    conditional branch that could skip the `profile.profile_type =
    ProfileType.LIVE` assignment -- it either runs to completion (assigns,
    then saves) or raises (propagating to the uncaught-500 path this tool
    already classifies as `OctoBotServerError`). A non-5xx response
    therefore does mean that assignment executed and was saved; the
    remaining genuine uncertainty this tool resolves by re-checking is only
    whether the immediately-following `models.select_profile(profile_id)`
    call (a separate function with its own historically-silent-failure mode
    for unknown ids, per `select_profile`'s docstring) actually took
    effect -- which is exactly what the `is_selected` re-check above
    verifies.

    An exception raised inside OctoBot's own handling (e.g. an unrecognized
    `profile_id`, which makes the underlying `get_profile()` raise) is not
    caught by this route at all, so it propagates to OctoBot's global error
    handler as an HTTP 500 -- classified here as `OctoBotServerError`, with
    the `Content-Type: application/json` request-header hint (same
    established technique as `duplicate`/`select_profile`/`export_profile`)
    so that handler's message is readable JSON instead of an HTML page.

    Output on success: `{"profile_id": str, "profile_type": "LIVE",
    "selected": true}`.
    """
    if confirm is not True:
        return require_confirmation(
            "convert_profile_to_live",
            profile_id,
            "This relabels the profile as OctoBot's internal LIVE type and "
            "selects it -- but does NOT itself enable real-money trading "
            "(confirmed against source: OctoBot's actual real-vs-simulated "
            "switch, config.trader.enabled, is a separate flag this call "
            "never touches). Still gated because it persistently changes "
            "the profile and its selection, and could otherwise mislead a "
            "caller into believing real trading is now active when it is "
            "not.",
        )

    client = get_client(ctx)
    response = await _request_raw(
        client,
        "GET",
        "/profiles_management/use_as_live",
        params={"profile_id": profile_id},
        headers=_JSON_ERROR_HINT_HEADERS,
    )
    if response.status_code >= 500:
        raise OctoBotServerError(
            f"OctoBot returned server error {response.status_code} converting "
            f"profile_id={profile_id!r} to LIVE: {_extract_error_text(response)}",
            status_code=response.status_code,
        )
    if response.status_code >= 400:
        raise OctoBotClientError(
            f"OctoBot returned client error {response.status_code} converting "
            f"profile_id={profile_id!r} to LIVE: {_extract_error_text(response)}",
            status_code=response.status_code,
        )

    profiles = await _fetch_and_parse_profiles(client)
    current = next((profile for profile in profiles if profile["profile_id"] == profile_id), None)
    if current is None or not current["is_selected"]:
        raise ProfileConversionFailedError(
            f"OctoBot did not select profile_id={profile_id!r} as expected "
            "after the request: "
            + (
                "the profile no longer appears in /profiles_selector at all."
                if current is None
                else f"is_selected={current['is_selected']!r}."
            )
            + " GET /profiles_management/use_as_live always responds with an "
            "uninformative redirect (verified against source), so this tool "
            "re-checks the resulting selection via the same scrape "
            "list_profiles/get_profile use rather than trusting the "
            "response status. (profile_type is deliberately NOT re-checked "
            "this way -- see this function's docstring, 'spec correction #2', "
            "for why the scrape's profile_type field cannot verify this "
            "action's actual effect.)"
        )
    return {"profile_id": profile_id, "profile_type": "LIVE", "selected": True}


def register(mcp: MCPServer) -> None:
    """Register this module's tools onto `mcp` (called once from `octobot_mcp.tools.register_all`)."""
    mcp.tool()(list_profiles)
    mcp.tool()(get_profile)
    mcp.tool()(create_profile)
    mcp.tool()(update_profile)
    mcp.tool()(select_profile)
    mcp.tool()(export_profile)
    mcp.tool()(delete_profile)
    mcp.tool()(convert_profile_to_live)
