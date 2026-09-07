"""Unit tests for `octobot_mcp.logging_config.configure_logging` (task 17,
docs/tasklist.md item 17, FR-29/NFR-16).

Covers: the rotating file handler's default and env-overridden bounds, that
a log line written after `configure_logging()` actually appears in the
target file, and -- the most important invariant per FR-29's own acceptance
criterion -- that importing package modules under `pytest` never attaches a
handler to the root logger as a side effect.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import subprocess
import sys

import pytest

from octobot_mcp.logging_config import (
    DEFAULT_LOG_BACKUP_COUNT,
    DEFAULT_LOG_MAX_BYTES,
    configure_logging,
)


@pytest.fixture(autouse=True)
def _reset_root_logger():
    """Every test gets a clean root logger, and any handlers `configure_logging`
    attaches are closed and detached afterward (avoids leaking open file
    handles / cross-test pollution, since the root logger is process-global)."""
    root_logger = logging.getLogger()
    original_handlers = list(root_logger.handlers)
    original_level = root_logger.level
    yield
    for handler in list(root_logger.handlers):
        if handler not in original_handlers:
            root_logger.removeHandler(handler)
            handler.close()
    root_logger.setLevel(original_level)


def _file_handlers(root_logger: logging.Logger) -> list[logging.handlers.RotatingFileHandler]:
    return [h for h in root_logger.handlers if isinstance(h, logging.handlers.RotatingFileHandler)]


# --- rotating file handler bounds -----------------------------------------------


def test_configure_logging_attaches_rotating_file_handler_with_default_bounds(tmp_path):
    log_file = tmp_path / "server.log"

    configure_logging(env={"OCTOBOT_MCP_LOG_FILE": str(log_file)})

    [handler] = _file_handlers(logging.getLogger())
    assert handler.maxBytes == DEFAULT_LOG_MAX_BYTES
    assert handler.backupCount == DEFAULT_LOG_BACKUP_COUNT


def test_configure_logging_respects_env_overridden_bounds(tmp_path):
    log_file = tmp_path / "server.log"

    configure_logging(
        env={
            "OCTOBOT_MCP_LOG_FILE": str(log_file),
            "OCTOBOT_MCP_LOG_MAX_BYTES": "1234",
            "OCTOBOT_MCP_LOG_BACKUP_COUNT": "7",
        }
    )

    [handler] = _file_handlers(logging.getLogger())
    assert handler.maxBytes == 1234
    assert handler.backupCount == 7


def test_configure_logging_creates_missing_parent_directory(tmp_path):
    log_file = tmp_path / "nested" / "dir" / "server.log"

    configure_logging(env={"OCTOBOT_MCP_LOG_FILE": str(log_file)})

    assert log_file.parent.is_dir()


def test_configure_logging_attaches_a_stderr_warning_handler_independent_of_level(tmp_path):
    log_file = tmp_path / "server.log"
    root_logger = logging.getLogger()
    handlers_before = set(root_logger.handlers)

    configure_logging(
        env={"OCTOBOT_MCP_LOG_FILE": str(log_file), "OCTOBOT_MCP_LOG_LEVEL": "DEBUG"}
    )

    new_handlers = [h for h in root_logger.handlers if h not in handlers_before]
    stream_handlers = [
        h
        for h in new_handlers
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.handlers.RotatingFileHandler)
    ]
    [stderr_handler] = stream_handlers
    assert stderr_handler.level == logging.WARNING
    assert stderr_handler.stream is sys.stderr


# --- a log line actually lands in the file --------------------------------------


def test_a_log_line_written_after_configuration_appears_in_the_file(tmp_path):
    log_file = tmp_path / "server.log"
    configure_logging(env={"OCTOBOT_MCP_LOG_FILE": str(log_file)})

    logger = logging.getLogger("octobot_mcp.tools._shared")
    logger.info("test message: {'tool_name': 'get_job_status', 'outcome': 'ok'}")

    for handler in logging.getLogger().handlers:
        handler.flush()

    contents = log_file.read_text()
    assert "test message" in contents
    assert "octobot_mcp.tools._shared" in contents
    assert "INFO" in contents


def test_debug_lines_are_dropped_below_the_configured_level(tmp_path):
    log_file = tmp_path / "server.log"
    configure_logging(
        env={"OCTOBOT_MCP_LOG_FILE": str(log_file), "OCTOBOT_MCP_LOG_LEVEL": "INFO"}
    )

    logger = logging.getLogger("octobot_mcp.some_module")
    logger.debug("should not appear")
    logger.info("should appear")

    for handler in logging.getLogger().handlers:
        handler.flush()

    contents = log_file.read_text()
    assert "should not appear" not in contents
    assert "should appear" in contents


# --- FR-29: no side effect at import time ---------------------------------------


def test_importing_package_modules_never_attaches_a_handler_to_the_root_logger():
    """Regression test for FR-29's own acceptance criterion: importing tool
    modules under `pytest` must never create a log file or attach a handler
    as a side effect -- only an explicit `configure_logging()` call may.

    Runs in a fresh subprocess rather than in-process: within the same
    pytest process, `logging.basicConfig()` is a no-op once ANY handler
    already exists (e.g. one pytest's own log-capture plugin installed, or
    one left behind by an earlier test/import in this same session), which
    would make an in-process before/after handler diff blind to a real
    `basicConfig()`-at-import-time regression. A clean subprocess has no
    such pre-existing handler, so this actually catches it (verified by
    hand: temporarily adding a stray `logging.basicConfig()` to a module
    imported here makes this test fail).
    """
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import logging\n"
                "import octobot_mcp.jobs\n"
                "import octobot_mcp.server\n"
                "import octobot_mcp.tools._shared\n"
                "import octobot_mcp.tools.backtesting\n"
                "import octobot_mcp.tools.data_collection\n"
                "print(len(logging.getLogger().handlers))\n"
            ),
        ],
        env={**os.environ, "OCTOBOT_BASE_URL": "http://octobot.example"},
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == "0", (
        f"expected zero root-logger handlers after import-only, got stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )


def test_importing_octobot_mcp_server_never_changes_the_root_logger_level():
    """Regression test for the root-logger LEVEL half of the same import-time
    side effect `test_importing_package_modules_never_attaches_a_handler_to_the_root_logger`
    covers for handlers.

    The `mcp` SDK's own `MCPServer.__init__` calls
    `logging.basicConfig(level="INFO", handlers=[...])` on a root logger with
    no handlers yet -- per `logging.basicConfig`'s own semantics this doesn't
    just attach a handler, it ALSO unconditionally calls
    `root.setLevel("INFO")`, a separate side effect `server.py`'s
    handler-diff workaround must also undo. Python's root logger is
    constructed as `RootLogger(WARNING)` (confirmed against `logging`
    module source), so its true untouched default is `logging.WARNING`
    (30) -- NOT `logging.NOTSET` (0), which only non-root loggers default
    to. Runs in a fresh subprocess for the same reason as the handler test
    above: an in-process check could be blind to a real regression if any
    earlier test already touched the root logger's level.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import logging\n"
                "import octobot_mcp.server\n"
                "print(logging.getLogger().level)\n"
            ),
        ],
        env={**os.environ, "OCTOBOT_BASE_URL": "http://octobot.example"},
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == str(logging.WARNING), (
        f"expected root logger level to remain logging.WARNING ({logging.WARNING}) after "
        f"import-only, got stdout={result.stdout!r} stderr={result.stderr!r}"
    )
