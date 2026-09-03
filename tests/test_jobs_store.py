"""Unit tests for `octobot_mcp.jobs.JobStore` (milestone 7, docs/tasklist.md item 7).

Pure state-machine/eviction tests: every job here is created and
transitioned synthetically, directly against the store -- no HTTP,
Socket.IO, or OctoBot call exists anywhere in `octobot_mcp.jobs`, so none
is exercised (or mockable) here. Covers this milestone's stated demo
criteria: a fake job driven queued->running->completed and
queued->cancelled, plus TTL and retention-cap eviction.
"""

import asyncio
import time

import pytest

from octobot_mcp.jobs import (
    JOB_RETENTION_CAP,
    JOB_TTL_SECONDS,
    TERMINAL_STATES,
    JobStore,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


# --- basic creation -----------------------------------------------------------


def test_create_job_returns_a_queued_record_with_milestone_9_safe_eta_defaults():
    store = JobStore()
    job = store.create_job("backtest")

    assert job.state == "queued"
    assert job.kind == "backtest"
    assert job.progress_percent == 0.0
    assert job.eta_seconds is None
    assert job.eta_confidence == "low"
    assert list(job.samples) == []
    assert job.errors == []
    assert job.result is None
    assert job.cancel_requested is False
    assert job.octobot_run_source is None
    assert job.started_at_monotonic is None
    assert job.finished_at_monotonic is None
    # job_id is a real uuid4 string, and unique per call.
    other = store.create_job("backtest")
    assert job.job_id != other.job_id


def test_create_job_stores_both_monotonic_and_wall_clock_timestamps():
    store = JobStore()
    job = store.create_job("backtest")

    assert isinstance(job.created_at_monotonic, float)
    assert isinstance(job.created_at_wall, float)


def test_create_job_accepts_child_job_ids_for_compare_strategies():
    store = JobStore()
    child_a = store.create_job("backtest")
    child_b = store.create_job("backtest")
    parent = store.create_job("compare_strategies", child_job_ids=[child_a.job_id, child_b.job_id])

    assert parent.child_job_ids == [child_a.job_id, child_b.job_id]


def test_backtest_execution_lock_exists_and_is_a_real_asyncio_lock():
    store = JobStore()
    assert isinstance(store.backtest_execution_lock, asyncio.Lock)


# --- demo criterion 1: queued -> running -> completed --------------------------


def test_fake_job_drives_through_queued_running_completed():
    store = JobStore()
    job = store.create_job("backtest")
    assert job.state == "queued"

    started = store.start(job.job_id, octobot_run_source="run-123")
    assert started is not None
    assert started.state == "running"
    assert started.started_at_monotonic is not None
    assert started.started_at_wall is not None
    assert started.octobot_run_source == "run-123"

    completed = store.complete(job.job_id, result={"report": {}, "trades": []})
    assert completed is not None
    assert completed.state == "completed"
    assert completed.progress_percent == 100.0
    assert completed.result == {"report": {}, "trades": []}
    assert completed.finished_at_monotonic is not None
    assert completed.finished_at_wall is not None

    # Re-fetching returns the same, now-terminal record.
    assert store.get(job.job_id) is completed


def test_start_is_a_noop_on_a_job_that_is_not_queued():
    store = JobStore()
    job = store.create_job("backtest")
    store.start(job.job_id)
    running_snapshot_source = job.octobot_run_source

    # Calling start() again on an already-running job changes nothing.
    result = store.start(job.job_id, octobot_run_source="different-source")
    assert result.state == "running"
    assert result.octobot_run_source == running_snapshot_source


def test_complete_is_a_noop_on_a_job_that_is_not_running():
    store = JobStore()
    job = store.create_job("backtest")  # still queued, never started

    result = store.complete(job.job_id, result={"report": {}})
    assert result.state == "queued"
    assert result.result is None


def test_fail_transitions_a_running_job_and_records_the_error():
    store = JobStore()
    job = store.create_job("backtest")
    store.start(job.job_id)

    failed = store.fail(job.job_id, error="boom: connection reset")
    assert failed.state == "failed"
    assert failed.errors == ["boom: connection reset"]
    assert failed.finished_at_monotonic is not None


def test_fail_is_a_noop_on_an_already_terminal_job():
    store = JobStore()
    job = store.create_job("backtest")
    store.start(job.job_id)
    store.complete(job.job_id, result={})

    result = store.fail(job.job_id, error="too late")
    assert result.state == "completed"
    assert result.errors == []


# --- demo criterion 2: queued -> cancelled --------------------------------------


def test_fake_job_drives_through_queued_cancelled():
    store = JobStore()
    job = store.create_job("backtest")

    cancelled = store.cancel(job.job_id)
    assert cancelled is not None
    assert cancelled.state == "cancelled"
    assert cancelled.finished_at_monotonic is not None
    assert cancelled.cancel_requested is False  # cancelled directly, no flag needed


def test_cancel_on_a_running_job_only_sets_the_flag_and_does_not_change_state():
    store = JobStore()
    job = store.create_job("backtest")
    store.start(job.job_id)

    result = store.cancel(job.job_id)
    assert result.state == "running"  # unchanged: milestone 8's watcher does the rest
    assert result.cancel_requested is True


def test_cancel_is_idempotent_on_an_already_terminal_job():
    store = JobStore()
    job = store.create_job("backtest")
    store.start(job.job_id)
    store.complete(job.job_id, result={})

    result = store.cancel(job.job_id)
    assert result.state == "completed"  # unchanged, not an error


def test_cancel_on_unknown_job_id_returns_none():
    store = JobStore()
    assert store.cancel("does-not-exist") is None


def test_get_on_unknown_job_id_returns_none():
    store = JobStore()
    assert store.get("does-not-exist") is None


# --- list_jobs filtering --------------------------------------------------------


def test_list_jobs_filters_by_state_and_kind():
    store = JobStore()
    backtest_queued = store.create_job("backtest")
    backtest_running = store.create_job("backtest")
    store.start(backtest_running.job_id)
    compare_queued = store.create_job("compare_strategies")

    assert {j.job_id for j in store.list_jobs()} == {
        backtest_queued.job_id,
        backtest_running.job_id,
        compare_queued.job_id,
    }
    assert [j.job_id for j in store.list_jobs(state="running")] == [backtest_running.job_id]
    assert {j.job_id for j in store.list_jobs(kind="backtest")} == {
        backtest_queued.job_id,
        backtest_running.job_id,
    }
    assert store.list_jobs(state="completed") == []


# --- retention eviction (NFR-9) -------------------------------------------------


def test_eviction_caps_terminal_jobs_at_the_retention_cap():
    store = JobStore()
    completed_ids = []
    for _ in range(JOB_RETENTION_CAP + 10):
        job = store.create_job("backtest")
        store.start(job.job_id)
        store.complete(job.job_id, result={})
        completed_ids.append(job.job_id)

    # One more creation call triggers eviction against everything above.
    newest = store.create_job("backtest")

    terminal = [j for j in store.list_jobs() if j.state in TERMINAL_STATES]
    assert len(terminal) <= JOB_RETENTION_CAP
    # The very first completed jobs (oldest) are the ones evicted.
    assert store.get(completed_ids[0]) is None
    # The newest job (non-terminal) always survives.
    assert store.get(newest.job_id) is not None


def test_eviction_never_removes_non_terminal_jobs_even_when_over_cap_and_old():
    store = JobStore()
    queued_ids = []
    for _ in range(JOB_RETENTION_CAP + 20):
        job = store.create_job("backtest")
        # Artificially age every queued job well past the TTL.
        job.created_at_monotonic -= JOB_TTL_SECONDS * 2
        queued_ids.append(job.job_id)

    # Trigger eviction once more.
    store.create_job("backtest")

    for job_id in queued_ids:
        assert store.get(job_id) is not None, "non-terminal jobs must never be evicted"


def test_eviction_removes_terminal_jobs_older_than_the_ttl():
    store = JobStore()
    old_job = store.create_job("backtest")
    store.start(old_job.job_id)
    store.complete(old_job.job_id, result={})
    # Artificially push this completed job's finish time past the TTL.
    old_job.finished_at_monotonic = time.monotonic() - (JOB_TTL_SECONDS + 1)

    recent_job = store.create_job("backtest")
    store.start(recent_job.job_id)
    store.complete(recent_job.job_id, result={})

    # Trigger eviction once more via a new creation call.
    store.create_job("backtest")

    assert store.get(old_job.job_id) is None
    assert store.get(recent_job.job_id) is not None


def test_eviction_keeps_terminal_jobs_within_ttl_and_under_cap():
    store = JobStore()
    job = store.create_job("backtest")
    store.start(job.job_id)
    store.complete(job.job_id, result={})

    store.create_job("backtest")  # trigger eviction; well within TTL and cap

    assert store.get(job.job_id) is not None
