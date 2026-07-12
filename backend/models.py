"""Pydantic models: request/response schemas for auth, upload, job status, and dataset download.

Enums (JobStatus, PipelineStage, UserRole, QualityGrade) are imported from domain.enums
— backend.models is the HTTP boundary layer and must not redefine domain types.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from domain.enums import (
    AssistantMessageRole,
    AssistantSessionStatus,
    CalibrationDecision,
    CalibrationStatus,
    CalibrationVerdict,
    CheckpointAuthor,
    JobStatus,
    MappingSessionStatus,
    OrchestrationDecision,
    OrchestrationStatus,
    PipelineStage,
    RerunStatus,
    ReviewStage,
    ReviewStatus,
    ReviewVerdict,
)
from domain.orchestration import CaptureGuidancePayload


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


class OrchestrationSnapshotResponse(BaseModel):
    """Public-facing orchestration snapshot returned to the client."""

    job_id: str
    status: OrchestrationStatus
    provider: str
    sandbox: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    decision: OrchestrationDecision | None = None
    summary: str | None = None
    json_path: str | None = None
    error: str | None = None
    capture_guidance: CaptureGuidancePayload | None = None
    tuned_mapping_profile: dict | None = None
    evidence_manifest: dict = Field(default_factory=dict)
    metadata: dict = Field(default_factory=dict)


class MappingSessionCreateRequest(BaseModel):
    """Create a mapping refinement session for a completed job."""

    title: str | None = None


class MappingCheckpointCreateRequest(BaseModel):
    """Submit a new mapping checkpoint into a session."""

    mapping_profile: dict
    author: CheckpointAuthor
    summary: str | None = None
    metadata: dict = Field(default_factory=dict)


class MappingCheckpointRestoreRequest(BaseModel):
    """Restore one existing checkpoint as the current mapping revision."""

    checkpoint_id: str


class MappingSessionResponse(BaseModel):
    """Public-facing mapping session snapshot."""

    job_id: str
    session_id: str
    status: MappingSessionStatus
    current_checkpoint_id: str | None = None
    active_rerun_id: str | None = None
    latest_rerun_id: str | None = None
    latest_completed_rerun_id: str | None = None
    created_at: datetime
    updated_at: datetime
    title: str | None = None
    metadata: dict = Field(default_factory=dict)


class MappingCheckpointResponse(BaseModel):
    """Public-facing mapping checkpoint."""

    checkpoint_id: str
    session_id: str
    job_id: str
    author: CheckpointAuthor
    mapping_profile: dict
    summary: str | None = None
    parent_checkpoint_id: str | None = None
    created_at: datetime
    metadata: dict = Field(default_factory=dict)


class MappingSessionDetailResponse(BaseModel):
    """Mapping session plus its checkpoint history."""

    session: MappingSessionResponse
    checkpoints: list[MappingCheckpointResponse]
    reruns: list[RerunResponse] = Field(default_factory=list)


class MappingSessionListResponse(BaseModel):
    """Wrapper for listing mapping sessions attached to a job."""

    sessions: list[MappingSessionResponse]


class RerunTriggerRequest(BaseModel):
    """Trigger a versioned pipeline rerun from a restored checkpoint."""

    checkpoint_id: str | None = None
    metadata: dict = Field(default_factory=dict)


class RerunResponse(BaseModel):
    """Public-facing checkpoint-triggered rerun."""

    rerun_id: str
    version: int
    job_id: str
    session_id: str
    source_checkpoint_id: str
    status: RerunStatus
    mapping_profile: dict
    artifact_manifest: dict | None = None
    summary: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    metadata: dict = Field(default_factory=dict)


class RerunListResponse(BaseModel):
    """Wrapper for listing reruns under a mapping session."""

    reruns: list[RerunResponse]
