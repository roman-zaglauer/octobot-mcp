"""Job status/listing/cancellation tools (milestone 7, docs/tasklist.md item 7;
extended by milestone 10, docs/tasklist.md item 10, for `compare_strategies`
jobs' combined progress/ETA).

`get_job_status`, `list_jobs`, and `cancel_job` all read and mutate
`octobot_mcp.jobs.JobStore` records reached via `get_job_store(ctx)`
(NFR-11) -- none of them make an HTTP call or touch Socket.IO. Milestone 7
was state-machine scaffolding only: nothing yet created a job via a real
OctoBot backtest submission, so every job these tools operated on then was
inserted into the store synthetically (by tests). Milestone 8
(`start_backtest`) and milestone 10 (`compare_strategies`,
`octobot_mcp.tools.backtesting`) are what actually create real jobs now.

None of these 3 tools are confirm-gated (ADR-0003's non-gated list):
`cancel_job` is reversible in effect (a cancelled run causes no data loss)
and `get_job_status`/`list_jobs` are read-only.

## `compare_strategies` combined progress/ETA: computed fresh at read time

Per milestone 10's own design guidance, a `compare_strategies` parent job's
`progress_percent`/`eta_seconds`/`eta_confidence` are deliberately NEVER
stored/mutated on the parent's own `JobRecord` as its children tick
(`_run_backtest_watcher`'s per-child watcher loop, milestone 8, has no
notion of "its parent" and stays that way). Instead, `_job_status_payload`
below computes them fresh, every time a `compare_strategies` job is read,
from the CURRENT state of its children (`job.child_job_ids` + `store.get`),
via the pure `octobot_mcp.eta.estimate_combined_progress`/
`estimate_combined_eta` functions -- always up to date, and keeps
milestone 8's child-watcher code entirely unchanged.

No `from __future__ import annotations` here: see
`octobot_mcp.tools._shared.with_error_handling`'s docstring for why
postponed annotations would silently break this module's Context
injection.
"""

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context

from octobot_mcp.context import get_job_store
from octobot_mcp.errors import JobNotFoundError
from octobot_mcp.eta import EtaConfidence, estimate_combined_eta, estimate_combined_progress
from octobot_mcp.jobs import JobRecord, JobStore
from octobot_mcp.tools._shared import with_error_handling


def _job_not_found(job_id: str) -> JobNotFoundError:
    return JobNotFoundError(
        f"No job found with id {job_id!r}. If the MCP server has restarted since "
        "this job was created, its in-memory record no longer exists -- this is "
        "expected, not a bug (the job store does not persist across restarts)."
    )


def _compare_strategies_progress_and_eta(
    store: JobStore, job: JobRecord
) -> tuple[float, float | None, EtaConfidence]:
    """Combined `progress_percent`/`eta_seconds`/`eta_confidence` for one
    `compare_strategies` job, computed fresh from its children's CURRENT
    state (spec's ETA algorithm step 7 + the combined progress formula --
    see `octobot_mcp.eta`'s docstring). Children no longer present in the
    store (e.g. evicted -- shouldn't normally happen while the parent
    itself is non-terminal, but guarded anyway) are simply skipped.
    """
    children = [child for child_id in (job.child_job_ids or []) if (child := store.get(child_id)) is not None]
    total_children = len(children)
    completed = [child for child in children if child.state == "completed"]
    running = next((child for child in children if child.state == "running"), None)
    not_yet_started = sum(1 for child in children if child.state == "queued")

    combined_progress = estimate_combined_progress(
        completed_children=len(completed),
        total_children=total_children,
        current_child_progress=running.progress_percent if running is not None else 0.0,
    )

    durations = [
        child.finished_at_monotonic - child.started_at_monotonic
        for child in completed
        if child.finished_at_monotonic is not None and child.started_at_monotonic is not None
    ]
    average_child_duration = (sum(durations) / len(durations)) if durations else None

    combined_eta, combined_confidence = estimate_combined_eta(
        completed_children=len(completed),
        children_not_yet_started=not_yet_started,
        current_child_eta_seconds=running.eta_seconds if running is not None else None,
        current_child_eta_confidence=running.eta_confidence if running is not None else "low",
        average_child_duration=average_child_duration,
    )
    return combined_progress, combined_eta, combined_confidence


def _job_status_payload(store: JobStore, job: JobRecord, *, include_sub_jobs: bool) -> dict[str, Any]:
    """Build the `get_job_status` output shape (spec's `#### get_job_status(job_id)`).

    `elapsed_seconds` is computed live from the job's stored monotonic
    timestamp and the current time (`JobRecord.elapsed_seconds`), never
    stored statically. For a `compare_strategies` job, `progress_percent`/
    `eta_seconds`/`eta_confidence` are likewise computed live, from its
    children's current state (`_compare_strategies_progress_and_eta`
    above) rather than read off the parent's own (never-updated) fields --
    see this module's docstring. `sub_jobs` is included only when
    `include_sub_jobs` is true AND `job.kind == "compare_strategies"` --
    `list_jobs` passes `include_sub_jobs=False` for its abbreviated shape
    regardless of kind.
    """
    if job.kind == "compare_strategies":
        progress_percent, eta_seconds, eta_confidence = _compare_strategies_progress_and_eta(store, job)
    else:
        progress_percent, eta_seconds, eta_confidence = job.progress_percent, job.eta_seconds, job.eta_confidence
    payload: dict[str, Any] = {
        "job_id": job.job_id,
        "kind": job.kind,
        "state": job.state,
        "progress_percent": progress_percent,
        "elapsed_seconds": job.elapsed_seconds(),
        "eta_seconds": eta_seconds,
        "eta_confidence": eta_confidence,
        "errors": list(job.errors),
    }
    if include_sub_jobs and job.kind == "compare_strategies":
        sub_jobs = []
        for child_id in job.child_job_ids or []:
            child = store.get(child_id)
            if child is not None:
                sub_jobs.append(
                    {
                        "job_id": child.job_id,
                        "state": child.state,
                        "progress_percent": child.progress_percent,
                    }
                )
        payload["sub_jobs"] = sub_jobs
    return payload


@with_error_handling
async def get_job_status(ctx: Context, job_id: str) -> Any:
    """Fetch one job's current status from the in-memory job store.

    Maps to no OctoBot route at all -- purely an in-memory lookup
    (docs/specs/octobot-mcp-tool-spec.md's job inventory table lists this
    tool's "Maps to" as "in-memory job store only"). Read-only, not
    confirm-gated. Raises `JobNotFoundError` (surfaced as a `ToolError` by
    `with_error_handling`) for an unknown `job_id`, noting the server may
    have restarted (ADR-0002 risk table) -- an unknown id is expected in
    that case, not a bug.
    """
    store = get_job_store(ctx)
    job = store.get(job_id)
    if job is None:
        raise _job_not_found(job_id)
    return _job_status_payload(store, job, include_sub_jobs=True)


@with_error_handling
async def list_jobs(ctx: Context, state: str | None = None, kind: str | None = None) -> Any:
    """List jobs known to the in-memory job store, optionally filtered.

    Maps to no OctoBot route -- purely an in-memory listing. `state`
    (`"queued"|"running"|"completed"|"failed"|"cancelled"`) and `kind`
    (`"backtest"|"compare_strategies"`) are both optional filters; omit
    either (or both) to not filter on it. Read-only, not confirm-gated.
    Each entry uses `get_job_status`'s shape minus `sub_jobs` (spec's
    `#### list_jobs(state?, kind?)`).
    """
    store = get_job_store(ctx)
    jobs = store.list_jobs(state=state, kind=kind)  # type: ignore[arg-type]
    return {"jobs": [_job_status_payload(store, job, include_sub_jobs=False) for job in jobs]}


@with_error_handling
async def cancel_job(ctx: Context, job_id: str) -> Any:
    """Cancel a job, or report its existing terminal state if already finished.

    Not confirm-gated (ADR-0003's explicit non-gated list): reversible, no
    data loss. Idempotent: cancelling an already-terminal job just returns
    its existing status, never an error.

    This milestone implements only the state-machine transitions that don't
    require calling OctoBot (no HTTP/Socket.IO call happens here, or
    anywhere else in this module):
    - `queued` (waiting on `backtest_execution_lock`, never started):
      cancelled directly.
    - `running`: only sets `cancel_requested = True` on the record.
      Milestone 8's watcher loop is what will actually notice the flag,
      tell OctoBot to stop, and transition the record to `cancelled` once
      that's confirmed -- this tool deliberately does NOT fabricate an
      immediate `state="cancelled"` for a running job, since nothing here
      is actually stopping it.

    Raises `JobNotFoundError` for an unknown `job_id` (same as
    `get_job_status`).
    """
    store = get_job_store(ctx)
    job = store.get(job_id)
    if job is None:
        raise _job_not_found(job_id)
    store.cancel(job_id)
    return _job_status_payload(store, job, include_sub_jobs=True)


def register(mcp: MCPServer) -> None:
    """Register this module's tools onto `mcp` (called once from `octobot_mcp.tools.register_all`)."""
    mcp.tool()(get_job_status)
    mcp.tool()(list_jobs)
    mcp.tool()(cancel_job)
