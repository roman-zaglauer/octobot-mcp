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

from typing import Any

import httpx2 as httpx
from bs4 import BeautifulSoup
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context

from octobot_mcp.context import get_client
from octobot_mcp.errors import (
    OctoBotClientError,
    OctoBotConnectionError,
    OctoBotServerError,
    OctoBotTimeoutError,
    ProfileNotFoundError,
    ProfileScrapeContractBrokenError,
)
from octobot_mcp.tools._shared import with_error_handling

_PROFILES_SELECTOR_PATH = "/profiles_selector"

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


def register(mcp: MCPServer) -> None:
    """Register this module's tools onto `mcp` (called once from `octobot_mcp.tools.register_all`)."""
    mcp.tool()(list_profiles)
    mcp.tool()(get_profile)
