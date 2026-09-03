"""Unit tests for the milestone-6 confirm-gated profile tools (docs/tasklist.md item 6).

`delete_profile`/`convert_profile_to_live` -- see
`src/octobot_mcp/tools/profiles.py`'s per-tool docstrings for the exact
request/response shapes asserted here, verified against OctoBot source as
part of this milestone's implementation. Each tool gets a refusal test
(asserting no HTTP call at all reaches OctoBot when `confirm` is omitted or
`false`) and a success test (with `confirm=true`), per ADR-0003 and
docs/tasklist.md item 6's stated demo criterion.
"""

import json

import httpx2 as httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from octobot_mcp.tools.profiles import convert_profile_to_live, delete_profile
from tests.test_tools_shared import make_ctx


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _profile_card(
    profile_id: str, name: str, *, selected: bool = False, profile_type_badge: str = "Simulated trading"
) -> str:
    """One minimal `profile-overview` card satisfying every fail-loud assertion
    `_parse_profiles_selector_html` makes (see that function's docstring)."""
    selected_class = " profile-overview-selected" if selected else ""
    active_badge = '<span class="badge badge-danger">active</span>' if selected else ""
    return f"""
<div class="col-12 col-md-6 col-lg-4 px-1 profile-overview{selected_class}">
  <div class="profile-details d-flex pb-1" data-id="{profile_id}">
    <span id="profile-name-{profile_id}">{name}</span>
    <span id="profile-description-{profile_id}">{name} description</span>
    <label class="my-auto" for="{profile_id}profile-complexity">Complexity:</label>
    <span class="badge">Easy</span>
    <label class="my-auto" for="{profile_id}profile-risk">Risk:</label>
    <span class="badge">Low</span>
    <span class="float-right badge badge-info">{profile_type_badge}</span>
    {active_badge}
  </div>
</div>
"""


def _profiles_selector_html(*cards: str) -> str:
    return f"<!DOCTYPE html><html><body>{''.join(cards)}</body></html>"


_ACTIVE_ID = "active-profile-id"
_TARGET_ID = "target-profile-id"

_BEFORE_HTML = _profiles_selector_html(
    _profile_card(_ACTIVE_ID, "Active Profile", selected=True),
    _profile_card(_TARGET_ID, "Target Profile"),
)
_AFTER_DELETE_HTML = _profiles_selector_html(
    _profile_card(_ACTIVE_ID, "Active Profile", selected=True),
)


# --- delete_profile -----------------------------------------------------------


@pytest.mark.anyio
async def test_delete_profile_refuses_without_confirm_and_makes_no_octobot_call():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("OctoBot should never be called when confirm is not true")

    result = await delete_profile(make_ctx(httpx.MockTransport(handler)), profile_id=_TARGET_ID)
    assert result == {
        "confirmed": False,
        "action": "delete_profile",
        "target": _TARGET_ID,
        "reason": "This permanently deletes the profile and cannot be undone.",
        "message": result["message"],
    }
    assert "confirm=true" in result["message"]


@pytest.mark.anyio
async def test_delete_profile_refuses_when_confirm_is_explicitly_false():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("OctoBot should never be called when confirm=false")

    result = await delete_profile(
        make_ctx(httpx.MockTransport(handler)), profile_id=_TARGET_ID, confirm=False
    )
    assert result["confirmed"] is False


@pytest.mark.anyio
async def test_delete_profile_succeeds_when_confirmed_and_the_post_check_confirms_removal():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/profiles_selector":
            html = _BEFORE_HTML if not getattr(handler, "called", False) else _AFTER_DELETE_HTML
            handler.called = True
            return httpx.Response(200, text=html)
        assert request.url.path == "/profiles_management/remove"
        assert request.method == "POST"
        assert json.loads(request.content) == {"id": _TARGET_ID}
        return httpx.Response(200, json="Profile created")

    result = await delete_profile(
        make_ctx(httpx.MockTransport(handler)), profile_id=_TARGET_ID, confirm=True
    )
    assert result == {"profile_id": _TARGET_ID, "name": "Target Profile", "deleted": True}


@pytest.mark.anyio
async def test_delete_profile_rejects_unknown_profile_id_without_calling_remove():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/profiles_selector":
            return httpx.Response(200, text=_BEFORE_HTML)
        raise AssertionError("OctoBot should never be asked to remove an unknown profile_id")

    with pytest.raises(ToolError) as exc_info:
        await delete_profile(
            make_ctx(httpx.MockTransport(handler)), profile_id="does-not-exist", confirm=True
        )
    assert "ProfileNotFoundError" in str(exc_info.value)


@pytest.mark.anyio
async def test_delete_profile_classifies_rejection_of_removing_the_active_profile():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/profiles_selector":
            return httpx.Response(200, text=_BEFORE_HTML)
        return httpx.Response(400, json="Can't remove the active profile")

    with pytest.raises(ToolError) as exc_info:
        await delete_profile(make_ctx(httpx.MockTransport(handler)), profile_id=_ACTIVE_ID, confirm=True)
    assert "ProfileDeleteRejectedError" in str(exc_info.value)
    assert "Can't remove the active profile" in str(exc_info.value)


@pytest.mark.anyio
async def test_delete_profile_fails_loud_when_octobot_reports_success_but_profile_still_present():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/profiles_selector":
            return httpx.Response(200, text=_BEFORE_HTML)  # unchanged before AND after
        return httpx.Response(200, json="Profile created")

    with pytest.raises(ToolError) as exc_info:
        await delete_profile(make_ctx(httpx.MockTransport(handler)), profile_id=_TARGET_ID, confirm=True)
    assert "ProfileDeleteRejectedError" in str(exc_info.value)
    assert "still present" in str(exc_info.value)


@pytest.mark.anyio
async def test_delete_profile_classifies_5xx():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/profiles_selector":
            return httpx.Response(200, text=_BEFORE_HTML)
        return httpx.Response(500, json="'target-profile-id' (KeyError)")

    with pytest.raises(ToolError) as exc_info:
        await delete_profile(make_ctx(httpx.MockTransport(handler)), profile_id=_TARGET_ID, confirm=True)
    assert "OctoBotServerError" in str(exc_info.value)


# --- convert_profile_to_live ---------------------------------------------------


@pytest.mark.anyio
async def test_convert_profile_to_live_refuses_without_confirm_and_makes_no_octobot_call():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("OctoBot should never be called when confirm is not true")

    result = await convert_profile_to_live(make_ctx(httpx.MockTransport(handler)), profile_id=_TARGET_ID)
    assert result["confirmed"] is False
    assert result["action"] == "convert_profile_to_live"
    assert result["target"] == _TARGET_ID
    assert "LIVE" in result["reason"]
    assert "confirm=true" in result["message"]


@pytest.mark.anyio
async def test_convert_profile_to_live_refuses_when_confirm_is_explicitly_false():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("OctoBot should never be called when confirm=false")

    result = await convert_profile_to_live(
        make_ctx(httpx.MockTransport(handler)), profile_id=_TARGET_ID, confirm=False
    )
    assert result["confirmed"] is False


@pytest.mark.anyio
async def test_convert_profile_to_live_succeeds_when_the_post_check_confirms_selection():
    # Spec correction (see convert_profile_to_live's docstring, "spec
    # correction #2"): the scrape's profile_type badge reflects a
    # completely different OctoBot concept (trader-enabled config) than
    # the profile.profile_type attribute this action sets, and is
    # confirmed (live) to stay "Simulated trading" even after a genuinely
    # successful conversion -- so success must NOT require profile_type to
    # read "LIVE" in the scrape, only that the profile ends up selected.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/profiles_selector":
            html = _profiles_selector_html(
                _profile_card(_ACTIVE_ID, "Active Profile"),
                _profile_card(
                    _TARGET_ID, "Target Profile", selected=True, profile_type_badge="Simulated trading"
                ),
            )
            return httpx.Response(200, text=html)
        assert request.url.path == "/profiles_management/use_as_live"
        assert dict(request.url.params) == {"profile_id": _TARGET_ID}
        assert request.headers["content-type"] == "application/json"
        # OctoBot's real response is an uninformative 302 redirect regardless of outcome.
        return httpx.Response(302, headers={"Location": "/profile"})

    result = await convert_profile_to_live(
        make_ctx(httpx.MockTransport(handler)), profile_id=_TARGET_ID, confirm=True
    )
    assert result == {"profile_id": _TARGET_ID, "profile_type": "LIVE", "selected": True}


@pytest.mark.anyio
async def test_convert_profile_to_live_fails_loud_when_the_post_check_shows_no_selection_change():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/profiles_selector":
            return httpx.Response(200, text=_BEFORE_HTML)  # _TARGET_ID still not selected
        return httpx.Response(302, headers={"Location": "/profile"})

    with pytest.raises(ToolError) as exc_info:
        await convert_profile_to_live(
            make_ctx(httpx.MockTransport(handler)), profile_id=_TARGET_ID, confirm=True
        )
    assert "ProfileConversionFailedError" in str(exc_info.value)


@pytest.mark.anyio
async def test_convert_profile_to_live_classifies_5xx_for_an_unknown_profile_id():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json="'does-not-exist' (KeyError)")

    with pytest.raises(ToolError) as exc_info:
        await convert_profile_to_live(
            make_ctx(httpx.MockTransport(handler)), profile_id="does-not-exist", confirm=True
        )
    assert "OctoBotServerError" in str(exc_info.value)
