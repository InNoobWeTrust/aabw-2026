"""Filesystem-persisted job store with atomic snapshots and append-only events.

Every job lives under ``jobs_root/<job_id>/`` containing:
    job.json    — canonical job state (atomic temp-file + replace)
    events.jsonl — append-only event log (one JSON object per line)
    upload/     — original uploaded video
    work/       — intermediate pipeline artifacts
    output/     — final packaged dataset
    logs/       — per-stage execution logs

This module is the single point of job state mutation. No other module may directly
mutate job directories, job.json, or events.jsonl.
"""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from domain.enums import JobStatus, PipelineStage, UserRole
from domain.jobs import JobEvent, JobOwner, JobSnapshot

_logger = logging.getLogger(__name__)


class FileSystemJobStore:
    """Concrete filesystem job store backed by ``data/jobs/<job_id>/`` directories.

    All public methods that read or write job state accept and return domain models.
    Write operations are atomic for ``job.json`` (temp-file + os.replace).
    Events are appended to ``events.jsonl`` as single-line JSON objects.

    The store is the **only** module permitted to read or write files under
    ``jobs_root/``. Backend routes and pipeline stages must go through this store
    for all job state access.
    """

    def __init__(self, jobs_root: Path) -> None:
        self._root = jobs_root
        self._root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def create_job(
        self,
        owner: JobOwner,
        original_filename: str,
        source_extension: str,
    ) -> JobSnapshot:
        """Create a new job directory tree and return the initial snapshot.

        Args:
            owner: The JobOwner carrying role and judge_session_id.
            original_filename: The original client-supplied filename.
            source_extension: The file extension (including leading dot).

        Returns:
            A JobSnapshot with status=QUEUED, stage=INGEST, progress=0.0.
        """
        job_id = uuid4().hex
        self._create_job_dirs(job_id)

        now = datetime.now(timezone.utc)
        snapshot = JobSnapshot(
            job_id=job_id,
            owner=owner,
            original_filename=original_filename,
            upload_path=str(self._job_dir(job_id) / "upload" / f"source{source_extension}"),
            output_dir=str(self._job_dir(job_id) / "output"),
            status=JobStatus.QUEUED,
            stage=PipelineStage.INGEST,
            progress=0.0,
            message="Job created",
            created_at=now,
            updated_at=now,
        )
        self._write_snapshot(snapshot)

        event = JobEvent(
            at=now,
            job_id=job_id,
            status=JobStatus.QUEUED,
            stage=PipelineStage.INGEST,
            message="Job created",
        )
        self.append_event(job_id, event)

        _logger.info("Created job %s (session=%s)", job_id, owner.judge_session_id)
        return snapshot

    def get_job(self, job_id: str) -> JobSnapshot:
        """Return the current snapshot for *job_id*.

        Raises:
            FileNotFoundError: If no job directory or job.json exists for *job_id*.
        """
        self._ensure_job_exists(job_id)
        return self._read_snapshot(job_id)

    def list_jobs_for_session(self, judge_session_id: str) -> list[JobSnapshot]:
        """Return every job owned by *judge_session_id*, newest first."""
        snapshots = self._scan_snapshots()
        filtered = [s for s in snapshots if s.owner.judge_session_id == judge_session_id]
        return sorted(filtered, key=lambda s: s.created_at, reverse=True)

    def list_all_jobs(self) -> list[JobSnapshot]:
        """Return every job in the store, newest first."""
        return sorted(self._scan_snapshots(), key=lambda s: s.created_at, reverse=True)

    def update_job(
        self,
        job_id: str,
        *,
        status: JobStatus | None = None,
        stage: PipelineStage | None = None,
        progress: float | None = None,
        message: str | None = None,
        queue_position: int | None = None,
        result: dict | None = None,
        completed_at: datetime | None = None,
    ) -> JobSnapshot:
        """Atomically update fields on the job snapshot.

        Only the keyword arguments that are not None are applied. The snapshot's
        ``updated_at`` is always refreshed to the current UTC time.

        Returns the updated snapshot.
        """
        self._ensure_job_exists(job_id)
        snapshot = self._read_snapshot(job_id)

        if status is not None:
            snapshot.status = status
        if stage is not None:
            snapshot.stage = stage
        if progress is not None:
            snapshot.progress = progress
        if message is not None:
            snapshot.message = message
        if queue_position is not None:
            snapshot.queue_position = queue_position
        if result is not None:
            snapshot.result = result
        if completed_at is not None:
            snapshot.completed_at = completed_at

        snapshot.updated_at = datetime.now(timezone.utc)
        self._write_snapshot(snapshot)
        return snapshot

    def append_event(self, job_id: str, event: JobEvent) -> None:
        """Append a single JSON line to the job's events.jsonl."""
        self._ensure_job_exists(job_id)
        events_path = self._events_path(job_id)
        events_path.parent.mkdir(parents=True, exist_ok=True)
        line = event.model_dump_json()
        with events_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def delete_job(self, job_id: str) -> None:
        """Recursively remove the entire job directory tree.

        Silently returns if the directory does not exist (already deleted).
        """
        job_dir = self._job_dir(job_id)
        if job_dir.exists():
            shutil.rmtree(job_dir)
            _logger.info("Deleted job %s", job_id)

    def job_exists(self, job_id: str) -> bool:
        """Return True if a job.json file exists for *job_id*."""
        return self._job_json_path(job_id).exists()

    def count_active_jobs_for_session(self, judge_session_id: str) -> int:
        """Return the number of QUEUED or RUNNING jobs for a judge session."""
        snapshots = self._scan_snapshots()
        count = 0
        for s in snapshots:
            if s.owner.judge_session_id != judge_session_id:
                continue
            if s.status.is_active():
                count += 1
        return count

    def list_jobs_by_status(self, statuses: set[JobStatus]) -> list[JobSnapshot]:
        """Return every job whose status is in *statuses*, newest first.

        Args:
            statuses: A set of JobStatus values to filter by.

        Returns:
            Snapshots matching any of the requested statuses, sorted by
            created_at descending.
        """
        snapshots = self._scan_snapshots()
        return sorted(
            [s for s in snapshots if s.status in statuses],
            key=lambda s: s.created_at,
            reverse=True,
        )

    def mark_running_jobs_failed_on_startup(self, reason: str = "worker_restarted") -> int:
        """Mark every RUNNING job as FAILED on worker restart.

        For each RUNNING job, atomically updates job.json to FAILED status,
        appends a ``worker_restarted`` event, and returns the count of
        affected jobs. Preserves stage and progress values.

        Args:
            reason: The ``failure_reason`` recorded in each event.

        Returns:
            The number of jobs that were transitioned from RUNNING to FAILED.
        """
        changed = 0
        now = datetime.now(timezone.utc)
        running = self.list_jobs_by_status({JobStatus.RUNNING})
        for snapshot in running:
            self.update_job(
                snapshot.job_id,
                status=JobStatus.FAILED,
                message="Worker restarted before job completed",
                completed_at=now,
            )
            event = JobEvent(
                at=now,
                job_id=snapshot.job_id,
                status=JobStatus.FAILED,
                stage=snapshot.stage,
                message="Worker restarted before job completed",
                failure_reason=reason,
            )
            self.append_event(snapshot.job_id, event)
            changed += 1
            _logger.info(
                "Marked job %s FAILED on startup (was RUNNING stage=%s)",
                snapshot.job_id,
                snapshot.stage.value,
            )
        return changed

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _job_dir(self, job_id: str) -> Path:
        return self._root / job_id

    def _job_json_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "job.json"

    def _events_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "events.jsonl"

    def _ensure_job_exists(self, job_id: str) -> None:
        if not self._job_json_path(job_id).is_file():
            raise FileNotFoundError(f"Job {job_id} not found")

    def _create_job_dirs(self, job_id: str) -> None:
        job_dir = self._job_dir(job_id)
        for subdir in ("upload", "work", "output", "logs"):
            (job_dir / subdir).mkdir(parents=True, exist_ok=True)

    def _write_snapshot(self, snapshot: JobSnapshot) -> None:
        """Atomically write snapshot to job.json using temp-file + replace."""
        json_path = self._job_json_path(snapshot.job_id)
        json_path.parent.mkdir(parents=True, exist_ok=True)

        payload = snapshot.model_dump(mode="json")
        fd, tmp_path = tempfile.mkstemp(suffix=".json", prefix="job_", dir=json_path.parent)
        try:
            with open(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False, default=str)
            Path(tmp_path).replace(json_path)
        except Exception:
            Path(tmp_path).unlink(missing_ok=True)
            raise

    def _read_snapshot(self, job_id: str) -> JobSnapshot:
        json_path = self._job_json_path(job_id)
        raw = json.loads(json_path.read_text(encoding="utf-8"))
        if isinstance(raw.get("owner"), dict):
            owner_raw = raw["owner"]
            raw["owner"] = JobOwner(
                role=UserRole(owner_raw["role"]),
                judge_session_id=owner_raw.get("judge_session_id"),
            )
        return JobSnapshot(**raw)

    def _scan_snapshots(self) -> list[JobSnapshot]:
        """Return all snapshots by scanning job.json files under the root."""
        snapshots: list[JobSnapshot] = []
        if not self._root.exists():
            return snapshots
        for json_path in sorted(self._root.glob("*/job.json")):
            try:
                snapshots.append(self._read_snapshot(json_path.parent.name))
            except Exception:
                _logger.exception("Failed to read snapshot at %s", json_path)
        return snapshots
