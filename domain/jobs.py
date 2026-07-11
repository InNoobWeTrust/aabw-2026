"""Pydantic models for jobs, progress, snapshots, and events.

These models are the canonical in-memory representation of a job throughout its
lifecycle. The filesystem persistence layer (domain/job_store_fs.py) serializes
and deserializes these models to/from job.json and events.jsonl.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from domain.enums import JobStatus, PipelineStage, UserRole


class JobOwner(BaseModel):
    """Identifies who submitted a job and under what authorization scope.

    Mirrors the relevant fields from SessionIdentity at submission time so that
    ownership can be checked without reconstructing the original token.
    """

    role: UserRole
    judge_session_id: str | None


class JobProgress(BaseModel):
    """Snapshot of a job's current pipeline progress at a point in time.

    This is a lightweight value object used for status polling. It carries the
    minimum fields needed to render a progress bar and status message in the
    frontend dashboard.
    """

    status: JobStatus
    stage: PipelineStage
    progress: float = Field(ge=0.0, le=1.0, description="Pipeline progress 0.0–1.0")
    message: str = ""
    failure_reason: str | None = None


class JobSnapshot(BaseModel):
    """A point-in-time, read-only view of a Job's full state.

    Returned by poll endpoints (GET /api/jobs/{job_id}) and used to render
    job cards in the frontend. This is a projection of the canonical job.json
    state — it is not the authoritative record, which lives on disk.

    Timestamps:
        created_at:   When the upload was accepted and the job ID assigned.
        updated_at:   Last mutation time (status change, stage advance, progress bump).
        completed_at: When the job reached a terminal state, or None if still active.
    """

    job_id: str
    owner: JobOwner
    original_filename: str
    upload_path: str
    output_dir: str
    status: JobStatus
    stage: PipelineStage
    progress: float = Field(ge=0.0, le=1.0)
    message: str = ""
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    queue_position: int | None = None
    result: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _queued_jobs_have_earliest_stage(self) -> JobSnapshot:
        """Ensure QUEUED jobs report a sensible stage.

        A QUEUED job has not started processing. By convention it reports the
        first pipeline stage (INGEST) as its stage — the stage it *will* enter
        when the worker picks it up.

        This is a documentation convention, not a hard constraint. The validator
        only logs a warning for unexpected combinations; it does not reject.
        """
        if self.status == JobStatus.QUEUED and self.stage not in (
            PipelineStage.INGEST,
            PipelineStage.PREPROCESS,
        ):
            self.message = self.message or (
                f"QUEUED job at stage {self.stage.value}; expected INGEST or PREPROCESS"
            )
        return self


class JobEvent(BaseModel):
    """An immutable record of a significant domain event in a job's lifecycle.

    Events are appended to events.jsonl in chronological order. They are the
    authoritative source for timeline reconstruction and audit trails.

    Common event types and their metadata:
        job_created        — after upload accepted
        stage_enter        — {"stage": "pose"}
        stage_exit         — {"stage": "pose", "result": "success"}
        stage_exit         — {"stage": "retarget", "result": "failed", "error": "..."}
        job_completed      — {"result": {...}}
        job_failed         — {"error": "...", "stage": "pose"}
        job_cancelled      — {}
        worker_restarted   — {}
    """

    at: datetime
    job_id: str
    status: JobStatus
    stage: PipelineStage
    message: str = ""
    failure_reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
