"""This server's own logging configuration (task 17, FR-29, NFR-16).

Milestone 14 added structured per-call/per-job-transition
`logging.getLogger(__name__)` calls (see `tools/_shared.py`, `jobs.py`,
`tools/backtesting.py`, `tools/data_collection.py`) but never configured any
handler anywhere in the process. With zero handlers, Python's root logger
falls back to `logging.lastResort`, which only ever surfaces `WARNING`+
records to stderr -- every `INFO`/`DEBUG` line milestone 14 added was
silently dropped. `configure_logging()` closes that gap.

Since this is a stdio MCP server, stdout is reserved for the JSON-RPC
transport -- writing logs there would corrupt the protocol stream. So this
attaches a `RotatingFileHandler` (bounded per NFR-16) plus a `StreamHandler`
on stderr fixed at `WARNING`+ (independent of the configured level), so a
crashing process still surfaces something to whatever the MCP client
captures from the subprocess, without flooding stderr with per-call `INFO`
lines.

Mirrors `config.py`'s `OCTOBOT_BASE_URL` convention: one module constant per
env var, read once, sane validated defaults. Unlike `config.py`, resolution
happens as a side effect (creating the log directory, attaching handlers)
rather than just returning a value -- so this must ONLY ever be called from
`server.py::main()`, before `mcp.run()`, never at import time. Importing any
tool module under `pytest` (or any other embedding) must never create a log
file or attach a handler.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path

_LOG_FILE_ENV_VAR = "OCTOBOT_MCP_LOG_FILE"
_LOG_LEVEL_ENV_VAR = "OCTOBOT_MCP_LOG_LEVEL"
_LOG_MAX_BYTES_ENV_VAR = "OCTOBOT_MCP_LOG_MAX_BYTES"
_LOG_BACKUP_COUNT_ENV_VAR = "OCTOBOT_MCP_LOG_BACKUP_COUNT"

DEFAULT_LOG_FILE = str(Path.home() / ".octobot-mcp" / "server.log")
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_LOG_MAX_BYTES = 5_000_000
DEFAULT_LOG_BACKUP_COUNT = 3

_STDERR_LEVEL = logging.WARNING

_FORMATTER = logging.Formatter(
    fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
)


def configure_logging(env: dict[str, str] | None = None) -> None:
    """Attach a rotating file handler (+ a stderr warning handler) to the root logger.

    Call exactly once, from `server.py::main()`, before `mcp.run()` -- never
    at module import time (see module docstring).

    Args:
        env: mapping to read env vars from instead of `os.environ` (for tests).
    """
    source = os.environ if env is None else env

    log_file = source.get(_LOG_FILE_ENV_VAR, DEFAULT_LOG_FILE)
    log_level = source.get(_LOG_LEVEL_ENV_VAR, DEFAULT_LOG_LEVEL)
    max_bytes = int(source.get(_LOG_MAX_BYTES_ENV_VAR, DEFAULT_LOG_MAX_BYTES))
    backup_count = int(source.get(_LOG_BACKUP_COUNT_ENV_VAR, DEFAULT_LOG_BACKUP_COUNT))

    log_path = Path(log_file).expanduser()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    file_handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=max_bytes, backupCount=backup_count
    )
    file_handler.setFormatter(_FORMATTER)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(_STDERR_LEVEL)
    stderr_handler.setFormatter(_FORMATTER)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(stderr_handler)
