"""In-process FIFO queue manager with filesystem lock for safe job dispatch.

Provides a single-worker FIFO queue that dispatches queued jobs one at a time
through a configurable runner factory. Uses filelock for mutual exclusion around
job selection and an asyncio.Event for efficient wake-on-enqueue signaling.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path

from filelock import FileLock

from domain.enums import JobStatus
from domain.jobs import JobSnapshot

_logger = logging.getLogger(__name__)


class InProcessQueueManager:
    """Single-worker FIFO queue that dispatches queued jobs through a runner factory.

    Only one job runs at a time. The runner factory is responsible for transitioning
    the job from QUEUED to RUNNING (the queue manager does *not* mutate status).
    An in-memory ``_active_task_job_id`` prevents duplicate launches.

    Designed for in-process use — does not coordinate between multiple workers.
    The filelock protects the job-selection critical section and could be extended
    for multi-process scenarios.

    Args:
        job_store: Filesystem-backed job store for reading/writing job state.
        runner_factory: Async callable ``(job_id) -> None`` that executes the pipeline.
        queue_root: Directory for the queue lock file.
    """

    def __init__(
        self,
        job_store: object,
        runner_factory: Callable[[str], Awaitable[None]],
        queue_root: Path,
    ) -> None:
        self._job_store = job_store
        self._runner_factory = runner_factory
        self._queue_root = queue_root
        self._queue_root.mkdir(parents=True, exist_ok=True)

        self._lock_path = self._queue_root / "queue.lock"
        self._lock = FileLock(str(self._lock_path))

        self._wake_event = asyncio.Event()
        self._active_task_job_id: str | None = None
        self._pump_task: asyncio.Task[None] | None = None
        self._started = False

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        """Start the background pump loop (idempotent).

        The pump continuously checks for queued jobs and dispatches the next
        eligible job through the runner factory. Only one pump task may run
        at a time.
        """
        if self._started:
            return
        self._started = True
        self._pump_task = asyncio.create_task(self._pump())
        _logger.info("Queue pump started")

    def enqueue(self, job_id: str) -> None:
        """Wake the pump loop so it checks for newly queued jobs.

        The caller is responsible for ensuring the job is in QUEUED status
        before calling this method.
        """
        _logger.debug("Enqueue signal for job %s", job_id)
        self._wake_event.set()

    def recover_on_startup(self) -> int:
        """Mark all RUNNING jobs as FAILED with reason ``worker_restarted``.

        Returns the number of jobs that were transitioned to FAILED.
        """
        from backend.job_store import FileSystemJobStore

        if not isinstance(self._job_store, FileSystemJobStore):
            return 0
        return self._job_store.mark_running_jobs_failed_on_startup(reason="worker_restarted")

    def list_active_job_ids(self) -> list[str]:
        """Return job IDs of all QUEUED and RUNNING jobs."""
        try:
            store: object = self._job_store
            snapshots: list[JobSnapshot] = store.list_jobs_by_status(  # type: ignore[union-attr]
                {JobStatus.QUEUED, JobStatus.RUNNING}
            )
        except AttributeError:
            return []
        return [s.job_id for s in snapshots]

    # ------------------------------------------------------------------ #
    # Internal pump
    # ------------------------------------------------------------------ #

    async def _pump(self) -> None:
        """Core pump loop — runs until cancelled.

        On each iteration, if no job is currently active, the pump acquires the
        filelock, scans for the oldest queued job, and dispatches it through the
        runner factory. Uses a short timeout on the wake event to enable periodic
        rescanning for jobs that may have been enqueued outside the signal path.
        """
        while True:
            if self._active_task_job_id is None:
                await self._dispatch_next()

            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._wake_event.wait(), timeout=5.0)
            self._wake_event.clear()

    async def _dispatch_next(self) -> None:
        """Select the oldest QUEUED job and launch it through the runner factory.

        Acquires the filelock for the selection critical section. Skips jobs
        that were cancelled or deleted between enqueue time and dispatch time.
        """
        with self._lock:
            snapshots = self._scan_queued()
            if not snapshots:
                return

            next_snapshot = snapshots[0]
            job_id = next_snapshot.job_id

            try:
                current = self._job_store.get_job(job_id)  # type: ignore[union-attr]
            except FileNotFoundError:
                _logger.debug("Skipping job %s: no longer exists", job_id)
                return

            if current.status != JobStatus.QUEUED:
                _logger.debug(
                    "Skipping job %s: status is %s (expected QUEUED)",
                    job_id,
                    current.status.value,
                )
                return

            self._active_task_job_id = job_id
            _logger.info("Dispatching job %s", job_id)

        await self._run_and_clear(job_id)

    async def _run_and_clear(self, job_id: str) -> None:
        """Execute the runner factory for *job_id*, then clear the active marker."""
        try:
            await self._runner_factory(job_id)
        except Exception:
            _logger.exception("Pipeline runner raised for job %s", job_id)
        finally:
            self._active_task_job_id = None

    def _scan_queued(self) -> list[JobSnapshot]:
        """Return all QUEUED snapshots sorted by created_at (oldest first)."""
        snapshots: list[JobSnapshot] = self._job_store.list_jobs_by_status(  # type: ignore[union-attr]
            {JobStatus.QUEUED}
        )
        snapshots.sort(key=lambda s: s.created_at)
        return snapshots
