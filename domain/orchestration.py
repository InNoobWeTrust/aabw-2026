"""Canonical orchestration models for adaptive job-level decision sub-jobs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from domain.enums import OrchestrationDecision, OrchestrationStatus


class CaptureGuidancePayload(BaseModel):
    """Structured capture guidance returned for unsalvageable clips."""

    reason: str | None = None
    detection_rate: float | None = None
    missing_landmark_ratio: float | None = None
    suggestions: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class OrchestrationResultPayload(BaseModel):
    """Normalized final decision payload emitted by orchestration."""

    decision: OrchestrationDecision
    confidence: float | None = None
    summary: str
    risks: list[str] = Field(default_factory=list)
    tuned_mapping_profile: dict[str, Any] | None = None
    capture_guidance: CaptureGuidancePayload | None = None
    evidence_snapshot: dict[str, Any] = Field(default_factory=dict)


class OrchestrationProgressPayload(BaseModel):
    """Structured progress event emitted while orchestration is running."""

    phase: str
    message: str
    heartbeat: bool = False
    elapsed_seconds: int | None = None


class OrchestrationStatusPayload(BaseModel):
    """Structured status event payload for orchestration SSE."""

    status: OrchestrationStatus


class OrchestrationDonePayload(BaseModel):
    """Structured terminal event payload for orchestration SSE."""

    status: OrchestrationStatus


class OrchestrationSnapshot(BaseModel):
    """Point-in-time snapshot of one adaptive orchestration run for a job."""

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
    tuned_mapping_profile: dict[str, Any] | None = None
    evidence_manifest: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OrchestrationEvent(BaseModel):
    """Append-only orchestration event used for SSE replay and persistence."""

    at: datetime
    job_id: str
    event: str
    payload: dict[str, Any] = Field(default_factory=dict)
