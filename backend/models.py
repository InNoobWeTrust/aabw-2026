"""Pydantic models: request/response schemas for auth, upload, job status, and dataset download.

Enums (JobStatus, PipelineStage, UserRole, QualityGrade) are imported from domain.enums
— backend.models is the HTTP boundary layer and must not redefine domain types.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from domain.enums import JobStatus, PipelineStage


class LoginRequest(BaseModel):
    """Request body for password-based login."""

    password: str


class TokenResponse(BaseModel):
    """JWT token returned on successful authentication.

    Includes role and judge_session_id for frontend awareness so the UI can
    display the caller's identity context without decoding the JWT itself.
    """

    access_token: str
    token_type: str = "bearer"
    role: str = ""
    judge_session_id: str | None = None


class JobResponse(BaseModel):
    """Public-facing job status returned to the client.

    Uses the canonical JobStatus and PipelineStage from domain.enums. The
    current_stage field reflects the stage the pipeline is currently executing
    or last executed if the job has reached a terminal state.
    """

    job_id: str
    filename: str
    status: JobStatus
    progress: float = Field(ge=0.0, le=1.0, description="Pipeline progress 0.0–1.0")
    current_stage: PipelineStage = PipelineStage.INGEST
    message: str = ""
    created_at: datetime
    completed_at: datetime | None = None
    result: dict | None = None


class JobListResponse(BaseModel):
    """Wrapper for listing multiple jobs."""

    jobs: list[JobResponse]
    total: int


class SessionSummary(BaseModel):
    """Aggregated summary of all jobs owned by a single judge session."""

    judge_session_id: str
    total_jobs: int
    active_jobs: int
    completed_jobs: int
    failed_jobs: int


class SessionSummaryListResponse(BaseModel):
    """Wrapper for listing session summaries (admin-only)."""

    sessions: list[SessionSummary]
    total: int
