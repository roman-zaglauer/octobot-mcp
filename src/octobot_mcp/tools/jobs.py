"""Job status/listing/cancellation tools (milestone 7, docs/tasklist.md item 7).

`get_job_status`, `list_jobs`, and `cancel_job` all read and mutate
`octobot_mcp.jobs.JobStore` records reached via `get_job_store(ctx)`
(NFR-11) -- none of them make an HTTP call or touch Socket.IO. This
milestone is state-machine scaffolding only: nothing yet creates a job via
a real OctoBot backtest submission (that's `start_backtest`/
`compare_strategies`, milestone 8+), so every job these tools operate on in
practice today is inserted into the store synthetically (by tests, or by a
future milestone once it exists).

None of these 3 tools are confirm-gated (ADR-0003's non-gated list):
`cancel_job` is reversible in effect (a cancelled run causes no data loss)
and `get_job_status`/`list_jobs` are read-only.

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
from octobot_mcp.jobs import JobRecord, JobStore
from octobot_mcp.tools._shared import with_error_handling


def _job_not_found(job_id: str) -> JobNotFoundError:
    return JobNotFoundError(
        f"No job found with id {job_id!r}. If the MCP server has restarted since "
        "this job was created, its in-memory record no longer exists -- this is "
        "expected, not a bug (the job store does not persist across restarts)."
    )


def _job_status_payload(store: JobStore, job: JobRecord, *, include_sub_jobs: bool) -> dict[str, Any]:
    """Build the `get_job_status` output shape (spec's `#### get_job_status(job_id)`).

    `elapsed_seconds` is computed live from the job's stored monotonic
    timestamp and the current time (`JobRecord.elapsed_seconds`), never
    stored statically. `sub_jobs` is included only when `include_sub_jobs`
    is true AND `job.kind == "compare_strategies"` -- `list_jobs` passes
    `include_sub_jobs=False` for its abbreviated shape regardless of kind.
    """
    payload: dict[str, Any] = {
        "job_id": job.job_id,
        "kind": job.kind,
        "state": job.state,
        "progress_percent": job.progress_percent,
        "elapsed_seconds": job.elapsed_seconds(),
        "eta_seconds": job.eta_seconds,
        "eta_confidence": job.eta_confidence,
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
