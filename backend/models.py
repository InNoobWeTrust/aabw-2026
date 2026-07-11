"""Pydantic models: request/response schemas for auth, upload, job status, and dataset download.

Enums (JobStatus, PipelineStage, UserRole, QualityGrade) are imported from domain.enums
— backend.models is the HTTP boundary layer and must not redefine domain types.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from domain.enums import (
    AssistantMessageRole,
    AssistantSessionStatus,
    CalibrationDecision,
    CalibrationStatus,
    CalibrationVerdict,
    JobStatus,
    PipelineStage,
    ReviewStage,
    ReviewStatus,
    ReviewVerdict,
)


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


class ReviewSnapshotResponse(BaseModel):
    """Public-facing stage review snapshot returned to the client."""

    job_id: str
    review_stage: ReviewStage
    status: ReviewStatus
    provider: str
    sandbox: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    verdict: ReviewVerdict | None = None
    summary: str | None = None
    markdown_path: str | None = None
    json_path: str | None = None
    error: str | None = None
    context_manifest: dict = Field(default_factory=dict)
    metadata: dict = Field(default_factory=dict)


class ReviewListResponse(BaseModel):
    """Container for both stage reviews attached to one job."""

    reviews: list[ReviewSnapshotResponse]


class CalibrationSnapshotResponse(BaseModel):
    """Public-facing mapping calibration snapshot returned to the client."""

    job_id: str
    status: CalibrationStatus
    provider: str
    sandbox: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    decision: CalibrationDecision | None = None
    verdict: CalibrationVerdict | None = None
    summary: str | None = None
    json_path: str | None = None
    error: str | None = None
    context_manifest: dict = Field(default_factory=dict)
    metadata: dict = Field(default_factory=dict)


class ArtifactManifestResponse(BaseModel):
    """Manifest of generated artifacts for a completed or partially completed job."""

    artifacts: dict


class AssistantSessionCreateRequest(BaseModel):
    """Create a reviewer-assistant session, optionally with a seed user message."""

    message: str | None = None
    title: str | None = None


class AssistantMessageCreateRequest(BaseModel):
    """Submit a user message into an existing reviewer-assistant session."""

    content: str


class AssistantSessionResponse(BaseModel):
    """Public-facing reviewer-assistant session snapshot."""

    job_id: str
    session_id: str
    status: AssistantSessionStatus
    provider: str
    sandbox: str
    created_at: datetime
    updated_at: datetime
    last_error: str | None = None
    title: str | None = None
    metadata: dict = Field(default_factory=dict)


class AssistantMessageResponse(BaseModel):
    """Public-facing reviewer-assistant transcript message."""

    at: datetime
    job_id: str
    session_id: str
    role: AssistantMessageRole
    content: str
    name: str | None = None
    metadata: dict = Field(default_factory=dict)


class AssistantSessionDetailResponse(BaseModel):
    """Reviewer-assistant session plus transcript."""

    session: AssistantSessionResponse
    messages: list[AssistantMessageResponse]


class AssistantSessionListResponse(BaseModel):
    """Wrapper for listing reviewer-assistant sessions attached to a job."""

    sessions: list[AssistantSessionResponse]
