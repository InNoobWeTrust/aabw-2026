"""Domain models for checkpointed mapping sessions.

A mapping session lets operators and assistants propose, review, and restore
immutable checkpoints that carry full MappingProfile configurations. Each
checkpoint is an auditable point-in-time snapshot of mapping parameters.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from domain.enums import CheckpointAuthor, MappingSessionStatus, RerunStatus
from domain.mapping import MappingProfile


class MappingCheckpoint(BaseModel):
    """Immutable point-in-time snapshot of a mapping profile configuration."""

    checkpoint_id: str
    session_id: str
    job_id: str
    author: CheckpointAuthor
    mapping_profile: MappingProfile
    summary: str | None = None
    parent_checkpoint_id: str | None = None
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class MappingSession(BaseModel):
    """An interactive checkpointed session attached to a completed job."""

    session_id: str
    job_id: str
    status: MappingSessionStatus
    current_checkpoint_id: str | None = None
    active_rerun_id: str | None = None
    latest_rerun_id: str | None = None
    latest_completed_rerun_id: str | None = None
    created_at: datetime
    updated_at: datetime
    title: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MappingSessionEvent(BaseModel):
    """Append-only event for SSE replay and audit."""

    at: datetime
    job_id: str
    session_id: str
    event: str
    payload: dict[str, Any] = Field(default_factory=dict)


class RerunArtifactManifest(BaseModel):
    """Persisted manifest of artifacts produced by one rerun execution."""

    rerun_id: str
    session_id: str
    job_id: str
    version: int
    artifacts: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime | None = None


class RerunRecord(BaseModel):
    """A single versioned pipeline rerun triggered from a restored checkpoint."""

    rerun_id: str
    version: int
    job_id: str
    session_id: str
    source_checkpoint_id: str
    status: RerunStatus
    mapping_profile: MappingProfile
    artifact_manifest: RerunArtifactManifest | None = None
    summary: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
