"""Unit tests for milestone 14's per-job-transition logging
(docs/tasklist.md item 14, spec's Observability section) --
`octobot_mcp.jobs.JobStore`'s `_log_job_transition`, called from every
mutator method that actually changes a job's `state` field, and only then
(never on a no-op).
"""

import logging

import pytest

from octobot_mcp.jobs import JobStore

_LOGGER_NAME = "octobot_mcp.jobs"


def _transition_records(caplog):
    return [r.getMessage() for r in caplog.records if r.name == _LOGGER_NAME]


# --- start: queued -> running ---------------------------------------------------


def test_start_logs_a_transition_from_queued_to_running(caplog):
    store = JobStore()
    job = store.create_job("backtest")

    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        store.start(job.job_id)

    [message] = _transition_records(caplog)
    assert f"'job_id': '{job.job_id}'" in message
    assert "'kind': 'backtest'" in message
    assert "'from_state': 'queued'" in message
    assert "'to_state': 'running'" in message
    assert "'progress_percent': 0.0" in message


def test_start_on_an_already_running_job_is_a_no_op_and_logs_nothing(caplog):
    store = JobStore()
    job = store.create_job("backtest")
    store.start(job.job_id)

    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        store.start(job.job_id)  # already running -- no-op.

    assert _transition_records(caplog) == []


# --- complete: running -> completed ---------------------------------------------


def test_complete_logs_a_transition_from_running_to_completed(caplog):
    store = JobStore()
    job = store.create_job("backtest")
    store.start(job.job_id)

    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        store.complete(job.job_id, result={"report": {}})

    [message] = _transition_records(caplog)
    assert "'from_state': 'running'" in message
    assert "'to_state': 'completed'" in message
    assert "'progress_percent': 100.0" in message


def test_complete_on_a_queued_job_is_a_no_op_and_logs_nothing(caplog):
    store = JobStore()
    job = store.create_job("backtest")

    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        store.complete(job.job_id, result={})  # never started -- no-op.

    assert _transition_records(caplog) == []


# --- fail: queued/running -> failed ---------------------------------------------


def test_fail_logs_a_transition_from_running_to_failed(caplog):
    store = JobStore()
    job = store.create_job("backtest")
    store.start(job.job_id)

    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        store.fail(job.job_id, error="boom")

    [message] = _transition_records(caplog)
    assert "'from_state': 'running'" in message
    assert "'to_state': 'failed'" in message


def test_fail_on_an_already_terminal_job_is_a_no_op_and_logs_nothing(caplog):
    store = JobStore()
    job = store.create_job("backtest")
    store.start(job.job_id)
    store.fail(job.job_id, error="first failure")

    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        store.fail(job.job_id, error="second failure")  # already terminal -- no-op.

    assert _transition_records(caplog) == []


# --- cancel: queued -> cancelled (direct), running -> only flags a request -------


def test_cancel_on_a_queued_job_logs_a_transition_to_cancelled(caplog):
    store = JobStore()
    job = store.create_job("backtest")

    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        store.cancel(job.job_id)

    [message] = _transition_records(caplog)
    assert "'from_state': 'queued'" in message
    assert "'to_state': 'cancelled'" in message


def test_cancel_on_a_running_job_only_sets_a_flag_and_logs_no_transition(caplog):
    """`cancel()`'s `"running"` branch never mutates `state` itself (only
    `cancel_requested`) -- per this milestone's own explicit instruction,
    that is NOT a state transition and must not be logged as one."""
    store = JobStore()
    job = store.create_job("backtest")
    store.start(job.job_id)

    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        store.cancel(job.job_id)

    assert store.get(job.job_id).state == "running"
    assert store.get(job.job_id).cancel_requested is True
    assert _transition_records(caplog) == []


def test_cancel_on_an_already_terminal_job_is_a_no_op_and_logs_nothing(caplog):
    store = JobStore()
    job = store.create_job("backtest")
    store.cancel(job.job_id)  # queued -> cancelled.

    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        store.cancel(job.job_id)  # already terminal -- no-op.

    assert _transition_records(caplog) == []


# --- confirm_cancelled: running -> cancelled ------------------------------------


def test_confirm_cancelled_logs_a_transition_from_running_to_cancelled(caplog):
    store = JobStore()
    job = store.create_job("backtest")
    store.start(job.job_id)
    store.cancel(job.job_id)  # sets cancel_requested, no log (see test above).

    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        store.confirm_cancelled(job.job_id)

    [message] = _transition_records(caplog)
    assert "'from_state': 'running'" in message
    assert "'to_state': 'cancelled'" in message


def test_confirm_cancelled_on_a_queued_job_is_a_no_op_and_logs_nothing(caplog):
    store = JobStore()
    job = store.create_job("backtest")

    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        store.confirm_cancelled(job.job_id)  # never running -- no-op.

    assert _transition_records(caplog) == []
