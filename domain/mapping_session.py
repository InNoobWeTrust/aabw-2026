"""Domain models for checkpointed mapping sessions.

A mapping session lets operators and assistants propose, review, and restore
immutable checkpoints that carry full MappingProfile configurations. Each
checkpoint is an auditable point-in-time snapshot of mapping parameters.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from domain.enums import CheckpointAuthor, MappingSessionStatus
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
