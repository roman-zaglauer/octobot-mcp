"""Config-loading tests (ADR-0004): OCTOBOT_BASE_URL env var handling."""

import pytest

from octobot_mcp.config import ConfigError, load_config


def test_load_config_reads_base_url_from_env():
    config = load_config(env={"OCTOBOT_BASE_URL": "http://192.168.178.100:5001"})
    assert config.base_url == "http://192.168.178.100:5001"


def test_load_config_missing_env_var_fails_clearly():
    with pytest.raises(ConfigError, match="OCTOBOT_BASE_URL"):
        load_config(env={})


def test_load_config_empty_env_var_fails_clearly():
    with pytest.raises(ConfigError, match="OCTOBOT_BASE_URL"):
        load_config(env={"OCTOBOT_BASE_URL": "   "})


@pytest.mark.parametrize(
    "malformed_value",
    [
        "not-a-url",
        "192.168.178.100:5001",
        "ftp://192.168.178.100:5001",
        "http://",
    ],
)
def test_load_config_malformed_env_var_fails_clearly(malformed_value: str):
    with pytest.raises(ConfigError, match="OCTOBOT_BASE_URL"):
        load_config(env={"OCTOBOT_BASE_URL": malformed_value})
