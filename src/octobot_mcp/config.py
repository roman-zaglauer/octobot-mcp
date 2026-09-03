"""Startup configuration loading (ADR-0004).

Per ADR-0004, the server targets exactly one OctoBot instance for its
whole process lifetime, configured via the `OCTOBOT_BASE_URL` environment
variable read once at startup. No tool/parameter ever supplies a
base_url -- see NFR-11 and octobot_mcp.client for how the resulting
config/client is threaded into tools via dependency injection instead of
a module-level global.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

_ENV_VAR = "OCTOBOT_BASE_URL"


class ConfigError(Exception):
    """The server's environment configuration is missing or invalid.

    Raised at startup (never mid-request) so misconfiguration fails
    clearly and immediately rather than surfacing as a confusing
    connectivity error later.
    """


@dataclass(frozen=True)
class OctoBotConfig:
    """Resolved configuration for the single OctoBot instance this process targets."""

    base_url: str


def load_config(env: dict[str, str] | None = None) -> OctoBotConfig:
    """Read and validate `OCTOBOT_BASE_URL` from the environment.

    Args:
        env: mapping to read from instead of `os.environ` (for tests).

    Raises:
        ConfigError: if the variable is unset, empty, or not a valid
            absolute http(s) URL.
    """
    source = os.environ if env is None else env
    raw_value = source.get(_ENV_VAR)

    if raw_value is None or not raw_value.strip():
        raise ConfigError(
            f"{_ENV_VAR} is not set. Set it to the OctoBot instance's base URL, "
            f"e.g. {_ENV_VAR}=http://192.168.178.100:5001"
        )

    parsed = urlparse(raw_value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ConfigError(
            f"{_ENV_VAR}={raw_value!r} is not a valid absolute http(s) URL. "
            f"Expected a form like http://host:port"
        )

    return OctoBotConfig(base_url=raw_value)
