"""Unit tests for the milestone-5 profile write tools (docs/tasklist.md item 5).

`create_profile`/`update_profile`/`select_profile`/`export_profile` -- see
`src/octobot_mcp/tools/profiles.py`'s per-tool docstrings for the exact
request/response shapes these tests assert, all verified against OctoBot
source and (where practical) the live test instance as part of this
milestone's implementation.

Minimal, hand-built `/profiles_selector` fixtures (via `_profiles_selector_html`
below) are used here rather than the large real `profiles_selector_valid.html`
fixture `test_tools_profiles.py` uses: `create_profile`'s `duplicate`/
`import_file`/`import_url` modes and `select_profile` all re-run the real
`_fetch_and_parse_profiles` scrape (not a mock of it) before/after their
action call, so each still exercises milestone 4's real fail-loud parser --
just against a small, fully-controlled fixture that's easy to reason about
one new profile card at a time.
"""

import base64
import json

import httpx2 as httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from octobot_mcp.tools.profiles import (
    create_profile,
    export_profile,
    select_profile,
    update_profile,
)
from tests.test_tools_shared import make_ctx


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _profile_card(profile_id: str, name: str, *, selected: bool = False) -> str:
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
    <span class="float-right badge badge-info">Simulated trading</span>
    {active_badge}
  </div>
</div>
"""


def _profiles_selector_html(*cards: str) -> str:
    return f"<!DOCTYPE html><html><body>{''.join(cards)}</body></html>"


_ACTIVE_ID = "active-profile-id"
_SOURCE_ID = "source-profile-id"
_NEW_ID = "newly-created-profile-id"

_BEFORE_HTML = _profiles_selector_html(
    _profile_card(_ACTIVE_ID, "Active Profile", selected=True),
    _profile_card(_SOURCE_ID, "Source Profile"),
)
_AFTER_HTML = _profiles_selector_html(
    _profile_card(_ACTIVE_ID, "Active Profile", selected=True),
    _profile_card(_SOURCE_ID, "Source Profile"),
    _profile_card(_NEW_ID, "Source Profile_(copy)"),
)


# --- create_profile: mode="duplicate" ---------------------------------------


@pytest.mark.anyio
async def test_create_profile_duplicate_succeeds_and_diffs_the_new_profile_id():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/profiles_selector":
            html = _BEFORE_HTML if not getattr(handler, "called", False) else _AFTER_HTML
            handler.called = True
            return httpx.Response(200, text=html)
        assert request.url.path == "/profiles_management/duplicate"
        assert dict(request.url.params) == {"profile_id": _SOURCE_ID}
        assert request.headers["content-type"] == "application/json"
        return httpx.Response(200, json="Profile created")

    result = await create_profile(
        make_ctx(httpx.MockTransport(handler)), mode="duplicate", source_profile_id=_SOURCE_ID
    )
    assert result == {
        "profile_id": _NEW_ID,
        "name": "Source Profile_(copy)",
        "message": "Profile created",
    }


@pytest.mark.anyio
async def test_create_profile_duplicate_classifies_failure_with_octobot_error_text():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/profiles_selector":
            return httpx.Response(200, text=_BEFORE_HTML)
        assert request.url.path == "/profiles_management/duplicate"
        return httpx.Response(500, json="'does-not-exist' (KeyError)")

    with pytest.raises(ToolError) as exc_info:
        await create_profile(
            make_ctx(httpx.MockTransport(handler)), mode="duplicate", source_profile_id="does-not-exist"
        )
    assert "ProfileImportFailedError" in str(exc_info.value)
    assert "KeyError" in str(exc_info.value)


@pytest.mark.anyio
async def test_create_profile_duplicate_fails_loud_when_no_new_profile_is_found():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/profiles_selector":
            return httpx.Response(200, text=_BEFORE_HTML)  # same before/after: no diff
        assert request.url.path == "/profiles_management/duplicate"
        return httpx.Response(200, json="Profile created")

    with pytest.raises(ToolError) as exc_info:
        await create_profile(
            make_ctx(httpx.MockTransport(handler)), mode="duplicate", source_profile_id=_SOURCE_ID
        )
    assert "ProfileImportFailedError" in str(exc_info.value)
    assert "no new profile" in str(exc_info.value)


@pytest.mark.anyio
async def test_create_profile_missing_required_field_for_mode_raises_without_calling_octobot():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("OctoBot should never be called when a required field is missing")

    with pytest.raises(ToolError) as exc_info:
        await create_profile(make_ctx(httpx.MockTransport(handler)), mode="duplicate")
    assert "ProfileImportFailedError" in str(exc_info.value)
    assert "source_profile_id" in str(exc_info.value)


@pytest.mark.anyio
async def test_create_profile_unknown_mode_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("OctoBot should never be called for an unknown mode")

    with pytest.raises(ToolError) as exc_info:
        await create_profile(make_ctx(httpx.MockTransport(handler)), mode="not-a-real-mode")
    assert "ProfileImportFailedError" in str(exc_info.value)


# --- create_profile: mode="import_file" --------------------------------------


@pytest.mark.anyio
async def test_create_profile_import_file_succeeds_despite_octobot_always_redirecting():
    # Spec correction (see profiles.py's _create_profile_import_file
    # docstring): this action always 302-redirects regardless of outcome,
    # so success must be detected by diffing, not by the response itself.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/profiles_selector":
            html = _BEFORE_HTML if not getattr(handler, "called", False) else _AFTER_HTML
            handler.called = True
            return httpx.Response(200, text=html)
        assert request.url.path == "/profiles_management/import"
        assert request.method == "POST"
        return httpx.Response(302, headers={"Location": "/profile"})

    result = await create_profile(
        make_ctx(httpx.MockTransport(handler)),
        mode="import_file",
        file_base64=base64.b64encode(b"fake zip bytes").decode("ascii"),
        filename="my_profile.zip",
    )
    assert result == {
        "profile_id": _NEW_ID,
        "name": "Source Profile_(copy)",
        "message": "Profile created",
    }


@pytest.mark.anyio
async def test_create_profile_import_file_fails_loud_when_octobot_redirect_hides_a_failure():
    # Same always-302 response, but no new profile actually appeared --
    # this must NOT be reported as a fabricated success.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/profiles_selector":
            return httpx.Response(200, text=_BEFORE_HTML)
        assert request.url.path == "/profiles_management/import"
        return httpx.Response(302, headers={"Location": "/profile"})

    with pytest.raises(ToolError) as exc_info:
        await create_profile(
            make_ctx(httpx.MockTransport(handler)),
            mode="import_file",
            file_base64=base64.b64encode(b"not a real zip").decode("ascii"),
            filename="broken.zip",
        )
    assert "ProfileImportFailedError" in str(exc_info.value)


@pytest.mark.anyio
async def test_create_profile_import_file_rejects_invalid_base64():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("OctoBot should never be called with unparseable base64")

    with pytest.raises(ToolError) as exc_info:
        await create_profile(
            make_ctx(httpx.MockTransport(handler)),
            mode="import_file",
            file_base64="not-valid-base64!!!",
            filename="broken.zip",
        )
    assert "ProfileImportFailedError" in str(exc_info.value)


# --- create_profile: mode="import_url" ---------------------------------------


@pytest.mark.anyio
async def test_create_profile_import_url_succeeds():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/profiles_selector":
            html = _BEFORE_HTML if not getattr(handler, "called", False) else _AFTER_HTML
            handler.called = True
            return httpx.Response(200, text=html)
        assert request.url.path == "/profiles_management/download"
        assert request.method == "POST"
        assert request.headers["content-type"] == "application/x-www-form-urlencoded"
        return httpx.Response(200, json={"text": "imported", "profile_id": ""})

    result = await create_profile(
        make_ctx(httpx.MockTransport(handler)),
        mode="import_url",
        url="https://example.com/profile.zip",
    )
    assert result == {
        "profile_id": _NEW_ID,
        "name": "Source Profile_(copy)",
        "message": "Profile created",
    }


@pytest.mark.anyio
async def test_create_profile_import_url_classifies_the_known_upstream_415():
    # Reproduces the genuine OctoBot bug documented in
    # _create_profile_import_url's docstring: a form-encoded-only request
    # gets an HTTP 415 from OctoBot's own `flask.request.json` access.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/profiles_selector":
            return httpx.Response(200, text=_BEFORE_HTML)
        assert request.url.path == "/profiles_management/download"
        return httpx.Response(
            415,
            text="Did not attempt to load JSON data because the request "
            "Content-Type was not 'application/json'.",
        )

    with pytest.raises(ToolError) as exc_info:
        await create_profile(
            make_ctx(httpx.MockTransport(handler)),
            mode="import_url",
            url="https://example.com/profile.zip",
        )
    assert "ProfileImportFailedError" in str(exc_info.value)
    assert "application/json" in str(exc_info.value)


# --- create_profile: mode="import_cloud_strategy" ----------------------------


@pytest.mark.anyio
async def test_create_profile_import_cloud_strategy_succeeds_without_diffing():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/profiles_management/download"
        assert request.method == "POST"
        assert json.loads(request.content) == {
            "strategy_id": "strat-1",
            "name": "My Strategy",
            "description": "desc",
        }
        return httpx.Response(
            200, json={"text": "My Strategy profile successfully imported.", "profile_id": "cloud-profile-1"}
        )

    result = await create_profile(
        make_ctx(httpx.MockTransport(handler)),
        mode="import_cloud_strategy",
        strategy_id="strat-1",
        name="My Strategy",
        description="desc",
    )
    assert result == {
        "profile_id": "cloud-profile-1",
        "name": "My Strategy",
        "message": "My Strategy profile successfully imported.",
    }


@pytest.mark.anyio
async def test_create_profile_import_cloud_strategy_classifies_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/profiles_management/download"
        return httpx.Response(
            400, json={"text": "Error when importing profile: 'bogus-strategy-id'", "profile_id": ""}
        )

    with pytest.raises(ToolError) as exc_info:
        await create_profile(
            make_ctx(httpx.MockTransport(handler)),
            mode="import_cloud_strategy",
            strategy_id="bogus-strategy-id",
            name="Bogus",
        )
    assert "ProfileImportFailedError" in str(exc_info.value)
    assert "bogus-strategy-id" in str(exc_info.value)


# --- update_profile -----------------------------------------------------------


@pytest.mark.anyio
async def test_update_profile_sends_id_merged_with_patch_and_echoes_updated_fields():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/profiles_management/update"
        assert request.method == "POST"
        body = json.loads(request.content)
        assert body == {"id": _SOURCE_ID, "name": "Renamed", "risk": 2}
        return httpx.Response(200, json=body)

    result = await update_profile(
        make_ctx(httpx.MockTransport(handler)),
        profile_id=_SOURCE_ID,
        patch={"name": "Renamed", "risk": 2},
    )
    assert result == {"profile_id": _SOURCE_ID, "updated_fields": {"name": "Renamed", "risk": 2}}


@pytest.mark.anyio
async def test_update_profile_classifies_rejection_of_renaming_the_active_profile():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json="Can't rename the active profile")

    with pytest.raises(ToolError) as exc_info:
        await update_profile(
            make_ctx(httpx.MockTransport(handler)),
            profile_id=_ACTIVE_ID,
            patch={"name": "Should Not Work"},
        )
    assert "ProfileUpdateRejectedError" in str(exc_info.value)
    assert "Can't rename the active profile" in str(exc_info.value)


@pytest.mark.anyio
async def test_update_profile_classifies_an_invalid_enum_ordinal_as_rejection():
    # Spec correction: risk/complexity must be int ordinals, not display
    # strings -- an invalid value crashes OctoBot with an uncaught
    # ValueError (HTTP 500), still surfaced as ProfileUpdateRejectedError.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500, json="invalid literal for int() with base 10: 'Moderate'"
        )

    with pytest.raises(ToolError) as exc_info:
        await update_profile(
            make_ctx(httpx.MockTransport(handler)),
            profile_id=_SOURCE_ID,
            patch={"risk": "Moderate"},
        )
    assert "ProfileUpdateRejectedError" in str(exc_info.value)
    assert "invalid literal for int()" in str(exc_info.value)


# --- select_profile -------------------------------------------------------------


@pytest.mark.anyio
async def test_select_profile_succeeds_when_the_post_check_confirms_selection():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/profile":
            assert dict(request.url.params) == {"select": _SOURCE_ID}
            return httpx.Response(200, text="<html>full profile page</html>")
        assert request.url.path == "/profiles_selector"
        # After selecting _SOURCE_ID, it must show as the selected card.
        html = _profiles_selector_html(
            _profile_card(_ACTIVE_ID, "Active Profile"),
            _profile_card(_SOURCE_ID, "Source Profile", selected=True),
        )
        return httpx.Response(200, text=html)

    result = await select_profile(make_ctx(httpx.MockTransport(handler)), profile_id=_SOURCE_ID)
    assert result == {"profile_id": _SOURCE_ID, "selected": True}


@pytest.mark.anyio
async def test_select_profile_fails_loud_when_octobot_silently_keeps_the_old_selection():
    # Spec correction: GET /profile?select=<id> returns 200 even when the
    # id doesn't exist and the previous profile stays selected.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/profile":
            return httpx.Response(200, text="<html>full profile page</html>")
        assert request.url.path == "/profiles_selector"
        return httpx.Response(200, text=_BEFORE_HTML)  # _ACTIVE_ID still selected

    with pytest.raises(ToolError) as exc_info:
        await select_profile(make_ctx(httpx.MockTransport(handler)), profile_id="does-not-exist")
    assert "ProfileSelectionFailedError" in str(exc_info.value)
    assert _ACTIVE_ID in str(exc_info.value)


@pytest.mark.anyio
async def test_select_profile_classifies_5xx():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(ToolError):
        await select_profile(make_ctx(httpx.MockTransport(handler)), profile_id=_SOURCE_ID)


# --- export_profile -------------------------------------------------------------


@pytest.mark.anyio
async def test_export_profile_returns_base64_content_and_the_real_filename():
    zip_bytes = b"PK\x03\x04fake zip content"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/profiles_management/export"
        assert dict(request.url.params) == {"profile_id": _SOURCE_ID}
        return httpx.Response(
            200,
            content=zip_bytes,
            headers={
                "Content-Type": "application/zip",
                "Content-Disposition": 'attachment; filename="Source Profile_20260903-114701.zip"',
            },
        )

    result = await export_profile(make_ctx(httpx.MockTransport(handler)), profile_id=_SOURCE_ID)
    assert result["profile_id"] == _SOURCE_ID
    assert result["filename"] == "Source Profile_20260903-114701.zip"
    assert base64.b64decode(result["content_base64"]) == zip_bytes


@pytest.mark.anyio
async def test_export_profile_parses_an_unquoted_content_disposition_filename():
    # Regression test: Werkzeug/Flask only quotes the filename parameter
    # when it isn't already a bare HTTP token -- a profile name with no
    # spaces (e.g. OctoBot's own stock `daily_trading` default profile)
    # produces an UNQUOTED Content-Disposition header. A regex matching
    # only `filename="..."` silently fails this very common case.
    zip_bytes = b"PK\x03\x04another fake zip"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=zip_bytes,
            headers={
                "Content-Type": "application/zip",
                "Content-Disposition": "attachment; filename=daily_trading_20260903-120547.zip",
            },
        )

    result = await export_profile(make_ctx(httpx.MockTransport(handler)), profile_id=_SOURCE_ID)
    assert result["filename"] == "daily_trading_20260903-120547.zip"
    assert base64.b64decode(result["content_base64"]) == zip_bytes


@pytest.mark.anyio
async def test_export_profile_fails_loud_when_content_disposition_is_missing():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"PK\x03\x04no header")

    with pytest.raises(ToolError) as exc_info:
        await export_profile(make_ctx(httpx.MockTransport(handler)), profile_id=_SOURCE_ID)
    assert "OctoBotMalformedResponseError" in str(exc_info.value)


@pytest.mark.anyio
async def test_export_profile_classifies_5xx_for_an_unknown_profile_id():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json="'does-not-exist' (KeyError)")

    with pytest.raises(ToolError) as exc_info:
        await export_profile(make_ctx(httpx.MockTransport(handler)), profile_id="does-not-exist")
    assert "OctoBotServerError" in str(exc_info.value)
