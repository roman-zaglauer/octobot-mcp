"""Unit tests for the Tier-B profile scrape adapter (milestone 4, ADR-0006).

`PROFILES_SELECTOR_HTML` is a real captured snapshot of the live test
instance's `/profiles_selector` response (OctoBot 2.1.1, trimmed to 3 of
its 24 real profile cards -- one built-in/slug-id, unselected; one
UUID-id, currently selected; one UUID-id, unselected -- see
`tests/fixtures/profiles_selector_valid.html`), not a hand-authored guess.
The broken-fixture tests mutate specific, known substrings of that same
real snapshot to violate exactly one structural assumption each, per
NFR-12's acceptance criterion ("100% of a test suite that mutates the
fixture HTML's expected nodes triggers the distinct exception, not a
fallback empty list").
"""

from pathlib import Path

import httpx2 as httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from octobot_mcp.tools.profiles import get_profile, list_profiles
from tests.test_tools_shared import make_ctx

FIXTURES_DIR = Path(__file__).parent / "fixtures"
PROFILES_SELECTOR_HTML = (FIXTURES_DIR / "profiles_selector_valid.html").read_text()

_ARBITRAGE_ID = "arbitrage_trading"
_SELECTED_ID = "a94936b1-9106-4cdc-9433-dc7dd2ccdd87"
_COPY_ID = "150e69b2-518f-4101-95fe-efe87754bbc6"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _html_handler(html: str, *, status_code: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/profiles_selector"
        return httpx.Response(status_code, text=html)

    return handler


# --- list_profiles: happy path ------------------------------------------------


@pytest.mark.anyio
async def test_list_profiles_extracts_all_fields_for_every_profile():
    result = await list_profiles(make_ctx(httpx.MockTransport(_html_handler(PROFILES_SELECTOR_HTML))))

    assert result["warning"] is None
    profiles_by_id = {p["profile_id"]: p for p in result["profiles"]}
    assert set(profiles_by_id) == {_ARBITRAGE_ID, _SELECTED_ID, _COPY_ID}

    arbitrage = profiles_by_id[_ARBITRAGE_ID]
    assert arbitrage["name"] == "Arbitrage Trading"
    assert arbitrage["risk"] == "Low"
    assert arbitrage["complexity"] == "Difficult"
    assert arbitrage["profile_type"] == "SIMULATOR"
    assert arbitrage["is_selected"] is False

    selected = profiles_by_id[_SELECTED_ID]
    assert selected["profile_type"] == "LIVE"
    assert selected["is_selected"] is True

    copy = profiles_by_id[_COPY_ID]
    assert copy["is_selected"] is False


@pytest.mark.anyio
async def test_list_profiles_marks_exactly_one_profile_selected():
    result = await list_profiles(make_ctx(httpx.MockTransport(_html_handler(PROFILES_SELECTOR_HTML))))
    selected = [p for p in result["profiles"] if p["is_selected"]]
    assert [p["profile_id"] for p in selected] == [_SELECTED_ID]


# --- list_profiles / get_profile: OctoBot connectivity failures pass through NFR-4 ---


@pytest.mark.anyio
async def test_list_profiles_classifies_5xx_as_tool_error():
    with pytest.raises(ToolError):
        await list_profiles(make_ctx(httpx.MockTransport(_html_handler("", status_code=500))))


# --- get_profile ---------------------------------------------------------------


@pytest.mark.anyio
async def test_get_profile_by_id_returns_matching_profile():
    result = await get_profile(
        make_ctx(httpx.MockTransport(_html_handler(PROFILES_SELECTOR_HTML))),
        profile_id=_ARBITRAGE_ID,
    )
    assert result["profile_id"] == _ARBITRAGE_ID
    assert result["name"] == "Arbitrage Trading"


@pytest.mark.anyio
async def test_get_profile_omitted_id_returns_the_selected_profile():
    result = await get_profile(make_ctx(httpx.MockTransport(_html_handler(PROFILES_SELECTOR_HTML))))
    assert result["profile_id"] == _SELECTED_ID
    assert result["is_selected"] is True


@pytest.mark.anyio
async def test_get_profile_unknown_id_raises_profile_not_found_not_scrape_broken():
    with pytest.raises(ToolError) as exc_info:
        await get_profile(
            make_ctx(httpx.MockTransport(_html_handler(PROFILES_SELECTOR_HTML))),
            profile_id="does-not-exist",
        )
    assert "ProfileNotFoundError" in str(exc_info.value)
    assert "ProfileScrapeContractBrokenError" not in str(exc_info.value)


# --- NFR-12/INV-5: fail-loud on a broken scrape, never a silent empty/partial result ---


@pytest.mark.anyio
async def test_list_profiles_fails_loud_when_no_cards_found():
    broken_html = PROFILES_SELECTOR_HTML.replace("profile-overview", "profile-overview-renamed")
    with pytest.raises(ToolError) as exc_info:
        await list_profiles(make_ctx(httpx.MockTransport(_html_handler(broken_html))))
    assert "ProfileScrapeContractBrokenError" in str(exc_info.value)


@pytest.mark.anyio
async def test_list_profiles_fails_loud_when_data_id_attributes_are_stripped():
    broken_html = PROFILES_SELECTOR_HTML.replace("data-id=", "data-removed=")
    with pytest.raises(ToolError) as exc_info:
        await list_profiles(make_ctx(httpx.MockTransport(_html_handler(broken_html))))
    assert "ProfileScrapeContractBrokenError" in str(exc_info.value)


@pytest.mark.anyio
async def test_list_profiles_fails_loud_when_zero_cards_are_selected():
    broken_html = PROFILES_SELECTOR_HTML.replace(
        'class="col-12 col-md-6 col-lg-4 px-1 profile-overview profile-overview-selected"',
        'class="col-12 col-md-6 col-lg-4 px-1 profile-overview"',
    )
    with pytest.raises(ToolError) as exc_info:
        await list_profiles(make_ctx(httpx.MockTransport(_html_handler(broken_html))))
    assert "ProfileScrapeContractBrokenError" in str(exc_info.value)


@pytest.mark.anyio
async def test_list_profiles_fails_loud_when_two_cards_are_selected():
    # Flip the first (currently unselected) card's class to also carry
    # profile-overview-selected, so two cards -- not exactly one -- claim
    # to be selected.
    broken_html = PROFILES_SELECTOR_HTML.replace(
        'class="col-12 col-md-6 col-lg-4 px-1 profile-overview">',
        'class="col-12 col-md-6 col-lg-4 px-1 profile-overview profile-overview-selected">',
        1,
    )
    with pytest.raises(ToolError) as exc_info:
        await list_profiles(make_ctx(httpx.MockTransport(_html_handler(broken_html))))
    assert "ProfileScrapeContractBrokenError" in str(exc_info.value)


@pytest.mark.anyio
async def test_list_profiles_fails_loud_when_active_badge_disagrees_with_selected_class():
    # The selected card's corroborating "active" badge is removed while the
    # profile-overview-selected class stays -- the two signals disagree.
    broken_html = PROFILES_SELECTOR_HTML.replace(
        '<span class="badge badge-danger float-right d-none d-md-block">active</span>',
        "",
    )
    with pytest.raises(ToolError) as exc_info:
        await list_profiles(make_ctx(httpx.MockTransport(_html_handler(broken_html))))
    assert "ProfileScrapeContractBrokenError" in str(exc_info.value)


@pytest.mark.anyio
async def test_list_profiles_fails_loud_when_name_span_is_missing():
    broken_html = PROFILES_SELECTOR_HTML.replace(f'id="profile-name-{_ARBITRAGE_ID}"', 'id="renamed"')
    with pytest.raises(ToolError) as exc_info:
        await list_profiles(make_ctx(httpx.MockTransport(_html_handler(broken_html))))
    assert "ProfileScrapeContractBrokenError" in str(exc_info.value)


@pytest.mark.anyio
async def test_list_profiles_never_returns_empty_or_partial_list_on_broken_scrape():
    """The core NFR-12 invariant: a broken scrape raises, it never falls back
    to an empty/partial list a caller could mistake for "no profiles exist"."""
    broken_html = PROFILES_SELECTOR_HTML.replace("profile-overview", "profile-overview-renamed")
    ctx = make_ctx(httpx.MockTransport(_html_handler(broken_html)))
    try:
        result = await list_profiles(ctx)
    except ToolError:
        pass
    else:
        pytest.fail(f"expected ToolError, got a result instead: {result!r}")


@pytest.mark.anyio
async def test_list_profiles_fails_loud_when_duplicate_profile_details_node_in_one_card():
    # A second, well-formed profile-details node appears nested inside the
    # same card (e.g. a rendering glitch/duplicate include) -- exactly-one
    # -per-card is violated. Kept a self-closed sibling (rather than just
    # duplicating the opening tag unclosed) so this mutation stays scoped
    # to the one card and doesn't also disturb the other two via lenient
    # HTML auto-closing.
    details_tag = f'<div class="profile-details d-flex pb-1" data-id="{_ARBITRAGE_ID}">'
    duplicate_node = '<div class="profile-details d-flex pb-1" data-id="arbitrage_trading-duplicate-node"></div>'
    broken_html = PROFILES_SELECTOR_HTML.replace(details_tag, details_tag + duplicate_node, 1)
    with pytest.raises(ToolError) as exc_info:
        await list_profiles(make_ctx(httpx.MockTransport(_html_handler(broken_html))))
    assert "ProfileScrapeContractBrokenError" in str(exc_info.value)


@pytest.mark.anyio
async def test_list_profiles_fails_loud_when_data_id_duplicated_across_two_cards():
    # The copy card's data-id is overwritten to collide with the (already
    # seen) arbitrage_trading card's id.
    broken_html = PROFILES_SELECTOR_HTML.replace(
        f'<div class="profile-details d-flex pb-1" data-id="{_COPY_ID}">',
        f'<div class="profile-details d-flex pb-1" data-id="{_ARBITRAGE_ID}">',
    )
    with pytest.raises(ToolError) as exc_info:
        await list_profiles(make_ctx(httpx.MockTransport(_html_handler(broken_html))))
    assert "ProfileScrapeContractBrokenError" in str(exc_info.value)


@pytest.mark.anyio
async def test_list_profiles_fails_loud_when_complexity_label_is_missing():
    label = (
        '<label class="my-auto" for="arbitrage_tradingprofile-complexity">\n'
        "      Complexity:\n"
        "    </label>"
    )
    assert label in PROFILES_SELECTOR_HTML
    broken_html = PROFILES_SELECTOR_HTML.replace(label, "")
    with pytest.raises(ToolError) as exc_info:
        await list_profiles(make_ctx(httpx.MockTransport(_html_handler(broken_html))))
    assert "ProfileScrapeContractBrokenError" in str(exc_info.value)


@pytest.mark.anyio
async def test_list_profiles_fails_loud_when_risk_badge_sibling_is_missing():
    # The risk *label* stays; only its sibling <span class="badge"> (the
    # value) is removed -- find_next_sibling must come back None and trip
    # the fail-loud check, not silently skip the field.
    risk_badge = '<span class="badge text-light bg-rating-1">\n        Low\n    </span>'
    assert risk_badge in PROFILES_SELECTOR_HTML
    broken_html = PROFILES_SELECTOR_HTML.replace(risk_badge, "")
    with pytest.raises(ToolError) as exc_info:
        await list_profiles(make_ctx(httpx.MockTransport(_html_handler(broken_html))))
    assert "ProfileScrapeContractBrokenError" in str(exc_info.value)


# --- profile_type "UNKNOWN": legitimate per-profile state vs. suspicious majority ---


@pytest.mark.anyio
async def test_list_profiles_reports_unknown_type_for_one_badge_absent_profile_without_raising():
    # Remove only the copy profile's type badge (the template omits it
    # entirely when neither a real nor a simulated trader is enabled, per
    # this module's docstring) -- a minority (1 of 3) UNKNOWN profiles must
    # parse successfully, not raise.
    marker_idx = PROFILES_SELECTOR_HTML.index(f'data-id="{_COPY_ID}"')
    badge = (
        '<span class="float-right badge badge-info">\n'
        "                            Real trading\n"
        "                        </span>"
    )
    badge_idx = PROFILES_SELECTOR_HTML.index(badge, marker_idx)
    mutated_html = (
        PROFILES_SELECTOR_HTML[:badge_idx] + PROFILES_SELECTOR_HTML[badge_idx + len(badge) :]
    )

    result = await list_profiles(make_ctx(httpx.MockTransport(_html_handler(mutated_html))))

    profiles_by_id = {p["profile_id"]: p for p in result["profiles"]}
    assert profiles_by_id[_COPY_ID]["profile_type"] == "UNKNOWN"
    # The other two profiles are unaffected.
    assert profiles_by_id[_ARBITRAGE_ID]["profile_type"] == "SIMULATOR"
    assert profiles_by_id[_SELECTED_ID]["profile_type"] == "LIVE"
    assert result["warning"] is not None
    assert _COPY_ID in result["warning"]


@pytest.mark.anyio
async def test_list_profiles_fails_loud_when_a_majority_of_profiles_are_unknown_type():
    # Renaming the badge-info class simulates a real OctoBot CSS/UI change:
    # every profile's type badge stops matching, so every profile would
    # otherwise silently degrade to "UNKNOWN" -- indistinguishable from the
    # legitimate per-profile "no trader enabled" state. This must raise
    # instead of being accepted as 3/3 legitimately-untyped profiles.
    broken_html = PROFILES_SELECTOR_HTML.replace("badge-info", "badge-primary")
    with pytest.raises(ToolError) as exc_info:
        await list_profiles(make_ctx(httpx.MockTransport(_html_handler(broken_html))))
    assert "ProfileScrapeContractBrokenError" in str(exc_info.value)
