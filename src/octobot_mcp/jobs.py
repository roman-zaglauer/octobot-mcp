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
HTTP client is -- never a module-level global reached by name. Milestone 8
(docs/tasklist.md item 8, `octobot_mcp.tools.backtesting`) reaches the lock
via `get_job_store(ctx).backtest_execution_lock` from its background
watcher task, and adds three more `JobStore` mutator methods alongside
`start`/`complete`/`fail`/`cancel` -- `record_watcher_tick` (one watcher
tick's progress/error-count update), `confirm_cancelled` (finalizing a
running job's cancellation once OctoBot's stop action has been called),
and `append_error` (a small generic error-string appender both of the
above build on) -- plus `track_watcher_task`, which is not a state-machine
mutator at all but exists to solve a real asyncio correctness problem: see
its own docstring. Milestone 9 (docs/tasklist.md item 9) completes
`record_watcher_tick`'s own milestone-8 placeholder by having it call the
pure `octobot_mcp.eta.estimate_eta`/`bound_samples` functions to actually
compute `eta_seconds`/`eta_confidence` and bound `samples` -- this module
still does not talk to OctoBot/Socket.IO/HTTP itself; `octobot_mcp.eta` is
equally free of any such I/O.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from asyncio import Lock
from collections import deque
from dataclasses import dataclass, field
from typing import Literal

from octobot_mcp.eta import bound_samples, estimate_eta

_logger = logging.getLogger(__name__)

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

    `eta_seconds`/`eta_confidence`/`samples` default to `None`/`"low"`/an
    empty deque until a job actually starts running; `JobStore.
    record_watcher_tick` (milestone 9, docs/tasklist.md item 9) is what
    keeps them meaningful thereafter, via `octobot_mcp.eta.estimate_eta`.
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
    `octobot_mcp.context.get_job_store(ctx).backtest_execution_lock`.
    Milestone 8's `octobot_mcp.tools.backtesting` background watcher
    acquires it around the real OctoBot start/watch/stop sequence, always
    via `async with`, whose `__aexit__` guarantees release on every code
    path -- the functional equivalent of an explicit `try/finally:
    lock.release()` (INV-1).
    """

    def __init__(self) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self.backtest_execution_lock: Lock = Lock()
        #: Strong references to in-flight watcher tasks, keyed by job_id.
        #: See `track_watcher_task`'s docstring for why this exists.
        self._watcher_tasks: dict[str, asyncio.Task] = {}

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

    def confirm_cancelled(self, job_id: str) -> JobRecord | None:
        """Finalize a running job's cancellation once OctoBot's stop action has been called.

        Distinct from `cancel()`: that method is what a `cancel_job` tool
        call uses to *request* cancellation (setting `cancel_requested` for
        a `running` job, or transitioning a `queued` job to `cancelled`
        directly since it never touched the lock). This method is what
        milestone 8's watcher loop calls once it has actually noticed the
        flag, told OctoBot to stop, and is ready to finalize the record
        (spec's watcher step 5) -- a no-op (returns the record as-is) if
        the job isn't `running` (e.g. called twice, or the job already
        reached a different terminal state through some other path).
        """
        job = self._jobs.get(job_id)
        if job is None or job.state != "running":
            return job
        job.state = "cancelled"
        job.finished_at_monotonic = time.monotonic()
        job.finished_at_wall = time.time()
        return job

    def record_watcher_tick(
        self, job_id: str, *, elapsed_seconds: float, progress_percent: float, error_count: int
    ) -> JobRecord | None:
        """Apply one watcher-loop tick's `backtesting_status` payload to a running job.

        Spec's "Background watcher" step 3: `progress_percent` is always
        updated to the latest reported value; `(elapsed_seconds,
        progress_percent)` is appended to `samples` only if
        `progress_percent` changed from the last recorded sample (dedup --
        avoids overweighting periods where OctoBot hasn't updated progress
        yet), then bounded per `octobot_mcp.eta.bound_samples` (spec's ETA
        step 1: last 20 entries or last 60 seconds of elapsed span,
        whichever is fewer). OctoBot's own backtesting-error count
        (`error_count`, confirmed via source to be a raw int -- `bot_logging.
        get_backtesting_errors_count()` -- not a list of per-error
        messages, unlike this record's own `errors: list[str]` field) is
        recorded via `append_error` as one summarizing string, deduped the
        same way `append_error` dedupes any other message.

        `eta_seconds`/`eta_confidence` are (re)computed on every tick via
        `octobot_mcp.eta.estimate_eta(job.samples, elapsed_seconds)` --
        milestone 9 (docs/tasklist.md item 9), completing what this method
        deliberately left as a placeholder in milestone 8. `elapsed_seconds`
        (this tick's own watcher-loop-clock value, the same one used to
        build the sample tuple above) is passed as `estimate_eta`'s
        `current_elapsed` -- deliberately NOT `job.elapsed_seconds()`,
        which is timed from job *creation* rather than from when the
        watcher started watching; mixing those two clocks would make the
        staleness check (step 2) subtly wrong. Called even when no new
        sample was appended this tick (e.g. progress unchanged) so that a
        mid-run stall -- real time advancing with no new sample -- is what
        actually triggers the staleness downgrade, not just a `samples`
        mutation.

        Milestone 10 (docs/tasklist.md item 10, defense-in-depth per that
        milestone's own judge review) adds a terminal-state guard: a
        watcher loop calling this on an ALREADY-terminal job (e.g. one
        force-cancelled by a racing `cancel()` call between two ticks) is
        a no-op, returning the record as-is rather than mutating
        `progress_percent`/`samples`/`errors`/`eta_seconds` on a record
        that has already reached its final state.
        """
        job = self._jobs.get(job_id)
        if job is None:
            return None
        if job.state in TERMINAL_STATES:
            return job
        job.progress_percent = progress_percent
        if not job.samples or job.samples[-1][1] != progress_percent:
            job.samples.append((elapsed_seconds, progress_percent))
            bound_samples(job.samples)
        job.eta_seconds, job.eta_confidence = estimate_eta(job.samples, elapsed_seconds)
        if error_count > 0:
            self.append_error(
                job_id,
                f"OctoBot reported {error_count} error(s) during this backtesting run.",
            )
        return job

    def append_error(self, job_id: str, message: str) -> JobRecord | None:
        """Append one human-readable error string to a job's `errors` list.

        Deduped against only the immediately-previous entry (not the whole
        list), so a message that keeps recurring unchanged (e.g. the same
        `record_watcher_tick` error-count string on repeated ticks) doesn't
        grow `errors` unboundedly, while a genuinely new/different message
        is never dropped. Used by `record_watcher_tick` above and by the
        watcher's best-effort handling of a failed stop-backtesting call
        during a cancel (which does not, by itself, fail the job -- the
        record still transitions to `cancelled` via `confirm_cancelled`).
        """
        job = self._jobs.get(job_id)
        if job is None:
            return None
        if not job.errors or job.errors[-1] != message:
            job.errors.append(message)
        return job

    def track_watcher_task(self, job_id: str, task: "asyncio.Task") -> None:
        """Keep a strong reference to a job's background watcher task for its lifetime.

        `octobot_mcp.tools.backtesting.start_backtest` spawns the watcher
        via `asyncio.create_task(...)` and must return immediately without
        awaiting it (the whole point of the async job pattern, ADR-0002) --
        but a bare `asyncio.create_task(...)` result with no reference kept
        anywhere is a well-known asyncio gotcha: the task object can be
        garbage-collected mid-flight, silently cancelling it. This store
        outlives the tool call that created it, so it is where that
        reference lives instead.

        Also attaches a done-callback that (a) removes the task from this
        registry once it finishes, so this dict doesn't grow unboundedly
        over a long-lived session, and (b) logs (at ERROR, with a
        traceback) any exception that escaped the watcher's own
        try/except/finally -- this should never normally happen (INV-1's
        `state="failed"` path is the intended way a watcher failure
        surfaces), so if this callback ever logs anything, that is itself
        a bug in the watcher worth investigating, not an expected outcome.
        The task itself is never exposed through any tool's JSON output
        (e.g. `get_job_status`) -- it lives only in this internal dict.
        """
        self._watcher_tasks[job_id] = task

        def _on_done(finished_task: "asyncio.Task", *, _job_id: str = job_id) -> None:
            self._watcher_tasks.pop(_job_id, None)
            if finished_task.cancelled():
                return
            exc = finished_task.exception()
            if exc is not None:
                _logger.error(
                    "Watcher task for job %s exited with an unhandled exception "
                    "(this should have been caught by the watcher's own "
                    "try/except -- this is a bug in the watcher itself, not an "
                    "expected outcome): %r",
                    _job_id,
                    exc,
                    exc_info=exc,
                )

        task.add_done_callback(_on_done)

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
