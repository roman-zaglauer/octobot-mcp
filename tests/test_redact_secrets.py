"""Unit tests for the shared redaction helper (docs/tasklist.md item 12, INV-4/NFR-6).

`redact_secrets` is tested here in isolation, mirroring
`tests/test_confirm_gating.py`'s treatment of `require_confirmation` --
end-to-end behavior of its one consumer (`update_exchange_credentials`) is
covered separately in `tests/test_tools_update_exchange_credentials.py`.
"""

from octobot_mcp.tools._shared import REDACTION_MARKER, redact_secrets


def test_redacts_a_single_occurrence_of_a_secret():
    result = redact_secrets("error: bad api key sk_live_12345 rejected", ["sk_live_12345"])
    assert "sk_live_12345" not in result
    assert REDACTION_MARKER in result


def test_redacts_every_occurrence_of_every_secret():
    text = "key=sk_ABC secret=sec_XYZ, retrying with key=sk_ABC failed too"
    result = redact_secrets(text, ["sk_ABC", "sec_XYZ"])
    assert "sk_ABC" not in result
    assert "sec_XYZ" not in result
    assert result.count(REDACTION_MARKER) == 3


def test_ignores_none_and_empty_secrets_without_corrupting_the_text():
    text = "nothing sensitive here"
    result = redact_secrets(text, [None, "", None])
    assert result == text


def test_returns_text_unchanged_when_no_secret_is_present():
    text = "a totally unrelated error message"
    result = redact_secrets(text, ["some-secret-not-in-text"])
    assert result == text


def test_handles_a_mix_of_real_and_absent_secrets():
    text = "api_key=abc123 succeeded, api_password absent"
    result = redact_secrets(text, ["abc123", None, ""])
    assert "abc123" not in result
    assert REDACTION_MARKER in result
