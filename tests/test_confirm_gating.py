"""Unit tests for the shared confirm-gating helper (docs/tasklist.md item 6, ADR-0003).

`require_confirmation` is tested here in isolation, since it's new shared,
reusable infrastructure milestone 12's `update_exchange_credentials`/
`clear_*_history` tools depend on later -- see
`docs/adr/0003-confirm-flag-gating.md` for the design this asserts against.
End-to-end confirm-gating behavior of its first two consumers
(`delete_profile`/`convert_profile_to_live`) is covered separately in
`tests/test_tools_profiles_gated.py`.
"""

from octobot_mcp.tools._shared import require_confirmation


def test_require_confirmation_names_the_action_target_and_reason():
    result = require_confirmation(
        "delete_profile", "profile-123", "This permanently deletes the profile and cannot be undone."
    )
    assert result["confirmed"] is False
    assert result["action"] == "delete_profile"
    assert result["target"] == "profile-123"
    assert result["reason"] == "This permanently deletes the profile and cannot be undone."


def test_require_confirmation_message_tells_the_caller_how_to_proceed():
    result = require_confirmation("delete_profile", "profile-123", "It is irreversible.")
    assert "delete_profile" in result["message"]
    assert "profile-123" in result["message"]
    assert "It is irreversible." in result["message"]
    assert "confirm=true" in result["message"]


def test_require_confirmation_is_a_plain_dict_not_an_exception():
    # ADR-0003: this is a normal return value, never raised/thrown.
    result = require_confirmation("convert_profile_to_live", "profile-456", "reason")
    assert isinstance(result, dict)
    assert set(result.keys()) == {"confirmed", "action", "target", "reason", "message"}
