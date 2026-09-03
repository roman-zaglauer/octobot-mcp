"""Unit tests for the job status/listing/cancellation tools (milestone 7).

Every job here is inserted into the store synthetically via
`JobStore.create_job`/`start`/`complete`/`cancel` directly -- there is no
`start_backtest` yet (milestone 8+), so this is exactly the
"synthetic/fake job transitions" the milestone's demo criterion calls for.
Each handler asserts OctoBot is never called, proving these 3 tools make
no HTTP (or Socket.IO) call at all.
"""

import httpx2 as httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from octobot_mcp.jobs import JobStore
from octobot_mcp.tools.jobs import cancel_job, get_job_status, list_jobs
from tests.test_tools_shared import make_ctx


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _no_octobot_calls_ctx(store: JobStore):
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("get_job_status/list_jobs/cancel_job must never call OctoBot")

    return make_ctx(httpx.MockTransport(handler), jobs=store)


# --- get_job_status --------------------------------------------------------------


@pytest.mark.anyio
async def test_get_job_status_reports_a_queued_backtest_job():
    store = JobStore()
    job = store.create_job("backtest")

    result = await get_job_status(_no_octobot_calls_ctx(store), job_id=job.job_id)

    assert result["job_id"] == job.job_id
    assert result["kind"] == "backtest"
    assert result["state"] == "queued"
    assert result["progress_percent"] == 0.0
    assert result["eta_seconds"] is None
    assert result["eta_confidence"] == "low"
    assert result["errors"] == []
    assert isinstance(result["elapsed_seconds"], float)
    assert "sub_jobs" not in result


@pytest.mark.anyio
async def test_get_job_status_elapsed_seconds_is_computed_live_not_stored():
    store = JobStore()
    job = store.create_job("backtest")

    first = await get_job_status(_no_octobot_calls_ctx(store), job_id=job.job_id)
    # Simulate time passing without mutating any stored field.
    job.created_at_monotonic -= 5.0
    second = await get_job_status(_no_octobot_calls_ctx(store), job_id=job.job_id)

    assert second["elapsed_seconds"] >= first["elapsed_seconds"] + 5.0


@pytest.mark.anyio
async def test_get_job_status_pins_elapsed_seconds_once_a_job_is_terminal():
    store = JobStore()
    job = store.create_job("backtest")
    store.start(job.job_id)
    store.complete(job.job_id, result={"report": {}})
    completed_elapsed = job.elapsed_seconds()

    result = await get_job_status(_no_octobot_calls_ctx(store), job_id=job.job_id)
    assert result["state"] == "completed"
    assert result["elapsed_seconds"] == pytest.approx(completed_elapsed)


@pytest.mark.anyio
async def test_get_job_status_includes_sub_jobs_for_compare_strategies():
    store = JobStore()
    child_a = store.create_job("backtest")
    child_b = store.create_job("backtest")
    store.start(child_a.job_id)
    parent = store.create_job(
        "compare_strategies", child_job_ids=[child_a.job_id, child_b.job_id]
    )

    result = await get_job_status(_no_octobot_calls_ctx(store), job_id=parent.job_id)

    assert result["kind"] == "compare_strategies"
    assert result["sub_jobs"] == [
        {"job_id": child_a.job_id, "state": "running", "progress_percent": 0.0},
        {"job_id": child_b.job_id, "state": "queued", "progress_percent": 0.0},
    ]


@pytest.mark.anyio
async def test_get_job_status_sub_jobs_is_empty_list_when_no_children_exist_yet():
    store = JobStore()
    parent = store.create_job("compare_strategies")  # no child_job_ids given

    result = await get_job_status(_no_octobot_calls_ctx(store), job_id=parent.job_id)
    assert result["sub_jobs"] == []


@pytest.mark.anyio
async def test_get_job_status_raises_job_not_found_for_unknown_id():
    store = JobStore()

    with pytest.raises(ToolError) as exc_info:
        await get_job_status(_no_octobot_calls_ctx(store), job_id="does-not-exist")

    assert "JobNotFoundError" in str(exc_info.value)
    assert "restarted" in str(exc_info.value)


# --- list_jobs ---------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_jobs_returns_abbreviated_shape_without_sub_jobs():
    store = JobStore()
    child = store.create_job("backtest")
    parent = store.create_job("compare_strategies", child_job_ids=[child.job_id])

    result = await list_jobs(_no_octobot_calls_ctx(store))

    assert {j["job_id"] for j in result["jobs"]} == {child.job_id, parent.job_id}
    for entry in result["jobs"]:
        assert "sub_jobs" not in entry


@pytest.mark.anyio
async def test_list_jobs_filters_by_state_and_kind():
    store = JobStore()
    queued_backtest = store.create_job("backtest")
    running_backtest = store.create_job("backtest")
    store.start(running_backtest.job_id)
    queued_compare = store.create_job("compare_strategies")

    only_running = await list_jobs(_no_octobot_calls_ctx(store), state="running")
    assert [j["job_id"] for j in only_running["jobs"]] == [running_backtest.job_id]

    only_backtest = await list_jobs(_no_octobot_calls_ctx(store), kind="backtest")
    assert {j["job_id"] for j in only_backtest["jobs"]} == {
        queued_backtest.job_id,
        running_backtest.job_id,
    }

    only_completed = await list_jobs(_no_octobot_calls_ctx(store), state="completed")
    assert only_completed["jobs"] == []

    assert queued_compare.job_id not in {j["job_id"] for j in only_backtest["jobs"]}


@pytest.mark.anyio
async def test_list_jobs_on_an_empty_store_returns_an_empty_list():
    store = JobStore()
    result = await list_jobs(_no_octobot_calls_ctx(store))
    assert result == {"jobs": []}


# --- cancel_job ----------------------------------------------------------------------


@pytest.mark.anyio
async def test_cancel_job_cancels_a_queued_job_directly():
    store = JobStore()
    job = store.create_job("backtest")

    result = await cancel_job(_no_octobot_calls_ctx(store), job_id=job.job_id)

    assert result["state"] == "cancelled"
    assert store.get(job.job_id).state == "cancelled"
    assert store.get(job.job_id).cancel_requested is False


@pytest.mark.anyio
async def test_cancel_job_on_a_running_job_only_sets_the_flag_not_the_state():
    store = JobStore()
    job = store.create_job("backtest")
    store.start(job.job_id)

    result = await cancel_job(_no_octobot_calls_ctx(store), job_id=job.job_id)

    # Not fabricated as cancelled -- milestone 8's watcher owns that transition.
    assert result["state"] == "running"
    assert store.get(job.job_id).cancel_requested is True


@pytest.mark.anyio
async def test_cancel_job_is_idempotent_on_an_already_terminal_job():
    store = JobStore()
    job = store.create_job("backtest")
    store.start(job.job_id)
    store.complete(job.job_id, result={"report": {}})

    result = await cancel_job(_no_octobot_calls_ctx(store), job_id=job.job_id)

    assert result["state"] == "completed"  # unchanged, not an error


@pytest.mark.anyio
async def test_cancel_job_raises_job_not_found_for_unknown_id():
    store = JobStore()

    with pytest.raises(ToolError) as exc_info:
        await cancel_job(_no_octobot_calls_ctx(store), job_id="does-not-exist")

    assert "JobNotFoundError" in str(exc_info.value)
