"""Canonical review resource models for asynchronous stage reviews.

These models persist the lifecycle and outputs of review sub-jobs that are
attached to a completed pipeline job. A review is independent from the main job
status and may complete or fail after artifacts are already available.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from domain.enums import (
    AssistantMessageRole,
    AssistantSessionStatus,
    ReviewStage,
    ReviewStatus,
    ReviewVerdict,
)


class ReviewSnapshot(BaseModel):
    """Point-in-time snapshot of one stage review for a job."""

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
    context_manifest: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReviewEvent(BaseModel):
    """Append-only review event used for SSE replay and persistence."""

    at: datetime
    job_id: str
    review_stage: ReviewStage
    event: str
    payload: dict[str, Any] = Field(default_factory=dict)


class AssistantSessionSnapshot(BaseModel):
    """Point-in-time snapshot of one reviewer-assistant chat session."""

    job_id: str
    session_id: str
    status: AssistantSessionStatus
    provider: str
    sandbox: str
    created_at: datetime
    updated_at: datetime
    last_error: str | None = None
    title: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AssistantMessage(BaseModel):
    """Persisted transcript message in a reviewer-assistant chat session."""

    at: datetime
    job_id: str
    session_id: str
    role: AssistantMessageRole
    content: str
    name: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AssistantEvent(BaseModel):
    """Append-only SSE event for a reviewer-assistant session."""

    at: datetime
    job_id: str
    session_id: str
    event: str
    payload: dict[str, Any] = Field(default_factory=dict)
