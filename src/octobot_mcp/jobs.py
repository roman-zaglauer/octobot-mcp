"""In-memory job store for backtest/compare_strategies jobs (milestone 7,
docs/tasklist.md item 7).

Per docs/specs/octobot-mcp-tool-spec.md's "Job store design" section, this
module is *pure infrastructure*: a `JobRecord` data model, an in-memory
`JobStore` holding those records, the process-wide `backtest_execution_lock`
(ADR-0002) reflecting OctoBot's real one-run-at-a-time constraint, and
NFR-9's TTL/cap retention eviction. It intentionally does NOT talk to
OctoBot, Socket.IO, or HTTP at all -- no such call exists anywhere in this
module. Milestone 8 wires `start_backtest`/`compare_strategies` and the
Socket.IO watcher loop on top of `JobStore.create_job`/`start`/`complete`/
`fail`; this milestone's own tests instead drive `JobRecord`s through their
state transitions directly (synthetic/fake transitions), and
`octobot_mcp.tools.jobs` (`get_job_status`/`list_jobs`/`cancel_job`) reads
and mutates records this store already holds.

Reachability (NFR-11): a `JobStore` instance lives on `AppContext.jobs`
(see `octobot_mcp.context`), built once at server startup by
`app_lifespan` and threaded into every tool call the same way the shared
HTTP client is -- never a module-level global reached by name. A future
milestone 8 tool reaches the lock via `get_job_store(ctx).backtest_execution_lock`.
"""

from __future__ import annotations

import time
import uuid
from asyncio import Lock
from collections import deque
from dataclasses import dataclass, field
from typing import Literal

JobKind = Literal["backtest", "compare_strategies"]
JobState = Literal["queued", "running", "completed", "failed", "cancelled"]
EtaConfidence = Literal["low", "medium", "high"]

#: NFR-9 retention: terminal records older than this (seconds, wall-clock
#: doesn't matter here since eviction only compares monotonic deltas) are
#: evicted on every `JobStore.create_job` call.
JOB_TTL_SECONDS = 3600

#: NFR-9 retention: once TTL eviction has run, the oldest terminal records
#: beyond this count are evicted too. Non-terminal jobs never count against
#: (or are evicted by) this cap.
JOB_RETENTION_CAP = 50

#: States from which a job never transitions again.
TERMINAL_STATES: frozenset[JobState] = frozenset({"completed", "failed", "cancelled"})


@dataclass
class JobRecord:
    """One backtest or compare_strategies job's full state (spec's "Data model").

    `created_at`/`started_at`/`finished_at` are each stored as a
    monotonic/wall-clock pair (`*_monotonic` via `time.monotonic()`,
    `*_wall` via `time.time()`) per the spec: monotonic for reliable
    elapsed-time math (immune to system clock changes), wall-clock kept
    alongside since the spec calls for the pair. `started_at_*`/
    `finished_at_*` are `None` until the job actually reaches `"running"`/
    a terminal state.

    `eta_seconds`/`eta_confidence`/`samples` exist now with milestone-9-safe
    defaults (`None`/`"low"`/empty deque) -- their real computation (the ETA
    heuristic) is milestone 9's job, not this one.
    """

    job_id: str
    kind: JobKind
    state: JobState
    created_at_monotonic: float
    created_at_wall: float
    started_at_monotonic: float | None = None
    started_at_wall: float | None = None
    finished_at_monotonic: float | None = None
    finished_at_wall: float | None = None
    progress_percent: float = 0.0
    samples: deque[tuple[float, float]] = field(default_factory=deque)
    eta_seconds: float | None = None
    eta_confidence: EtaConfidence = "low"
    errors: list[str] = field(default_factory=list)
    result: dict | None = None
    child_job_ids: list[str] | None = None
    cancel_requested: bool = False
    octobot_run_source: str | None = None

    def elapsed_seconds(self) -> float:
        """Elapsed time since creation, computed live (never stored statically).

        Uses the monotonic clock throughout so it is immune to system clock
        changes. Once the job has reached a terminal state, elapsed time is
        pinned to `finished_at_monotonic` so it stops growing after the job
        is done; otherwise it is measured against the current time.
        """
        end = self.finished_at_monotonic if self.finished_at_monotonic is not None else time.monotonic()
        return end - self.created_at_monotonic


class JobStore:
    """In-memory `JobRecord` store plus the shared backtest execution lock.

    Owns `backtest_execution_lock` (ADR-0002's process-wide `asyncio.Lock`
    reflecting OctoBot's one-run-at-a-time constraint) so it is reachable
    the same dependency-injected way as everything else (NFR-11): via
    `octobot_mcp.context.get_job_store(ctx).backtest_execution_lock`. Nothing
    in this milestone acquires it around a real OctoBot call yet -- that is
    milestone 8's job; this milestone only makes sure the lock exists and is
    structurally in place.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self.backtest_execution_lock: Lock = Lock()

    def create_job(
        self, kind: JobKind, *, child_job_ids: list[str] | None = None
    ) -> JobRecord:
        """Create a new `queued` job, evicting stale/excess terminal records first.

        Per NFR-9, eviction runs on every job-creation event: milestone 8's
        `start_backtest`/`compare_strategies` will call this method (which is
        why eviction lives here rather than in a separately-invoked method),
        and this milestone's own tests call it directly to exercise both the
        state machine and the eviction policy.
        """
        self._evict_stale_terminal_jobs()
        now_monotonic = time.monotonic()
        now_wall = time.time()
        job = JobRecord(
            job_id=str(uuid.uuid4()),
            kind=kind,
            state="queued",
            created_at_monotonic=now_monotonic,
            created_at_wall=now_wall,
            child_job_ids=child_job_ids,
        )
        self._jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> JobRecord | None:
        """Look up one job by id, or `None` if it doesn't exist (e.g. server restart)."""
        return self._jobs.get(job_id)

    def list_jobs(
        self, *, state: JobState | None = None, kind: JobKind | None = None
    ) -> list[JobRecord]:
        """List jobs, optionally filtered by `state` and/or `kind`, insertion order."""
        return [
            job
            for job in self._jobs.values()
            if (state is None or job.state == state) and (kind is None or job.kind == kind)
        ]

    def start(self, job_id: str, *, octobot_run_source: str | None = None) -> JobRecord | None:
        """Transition a `queued` job to `running`. No-op (returns as-is) if not `queued`."""
        job = self._jobs.get(job_id)
        if job is None or job.state != "queued":
            return job
        job.state = "running"
        job.started_at_monotonic = time.monotonic()
        job.started_at_wall = time.time()
        job.octobot_run_source = octobot_run_source
        return job

    def complete(self, job_id: str, *, result: dict) -> JobRecord | None:
        """Transition a `running` job to `completed`, setting `result` exactly once (INV-3)."""
        job = self._jobs.get(job_id)
        if job is None or job.state != "running":
            return job
        job.state = "completed"
        job.progress_percent = 100.0
        job.result = result
        job.finished_at_monotonic = time.monotonic()
        job.finished_at_wall = time.time()
        return job

    def fail(self, job_id: str, *, error: str) -> JobRecord | None:
        """Transition a `queued`/`running` job to `failed`, recording `error`."""
        job = self._jobs.get(job_id)
        if job is None or job.state in TERMINAL_STATES:
            return job
        job.state = "failed"
        job.errors.append(error)
        job.finished_at_monotonic = time.monotonic()
        job.finished_at_wall = time.time()
        return job

    def cancel(self, job_id: str) -> JobRecord | None:
        """Apply `cancel_job`'s state-machine rules for this milestone.

        - Already terminal: no-op, returns the record as-is (idempotent).
        - `queued` (waiting on the lock, never started): cancel directly.
        - `running`: only sets `cancel_requested = True` -- milestone 8's
          watcher loop is what actually tells OctoBot to stop and then
          transitions the record to `cancelled` once that is confirmed; this
          milestone never fabricates that transition itself.
        """
        job = self._jobs.get(job_id)
        if job is None or job.state in TERMINAL_STATES:
            return job
        if job.state == "queued":
            job.state = "cancelled"
            job.finished_at_monotonic = time.monotonic()
            job.finished_at_wall = time.time()
        elif job.state == "running":
            job.cancel_requested = True
        return job

    def _evict_stale_terminal_jobs(self) -> None:
        """NFR-9: evict terminal records past TTL, then past the retention cap.

        Non-terminal (`queued`/`running`) jobs are never evicted, regardless
        of age or count.
        """
        now = time.monotonic()
        for job_id, job in list(self._jobs.items()):
            if (
                job.state in TERMINAL_STATES
                and job.finished_at_monotonic is not None
                and now - job.finished_at_monotonic > JOB_TTL_SECONDS
            ):
                del self._jobs[job_id]

        terminal = [job for job in self._jobs.values() if job.state in TERMINAL_STATES]
        excess = len(terminal) - JOB_RETENTION_CAP
        if excess > 0:
            terminal.sort(key=lambda job: job.finished_at_monotonic or job.created_at_monotonic)
            for job in terminal[:excess]:
                del self._jobs[job.job_id]
