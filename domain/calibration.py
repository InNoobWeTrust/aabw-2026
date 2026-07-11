"""Canonical mapping-calibration models for async read-only calibration sub-jobs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from domain.enums import CalibrationDecision, CalibrationStatus, CalibrationVerdict


class CalibrationSnapshot(BaseModel):
    """Point-in-time snapshot of one mapping calibration run for a job."""

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
    context_manifest: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CalibrationEvent(BaseModel):
    """Append-only calibration event used for SSE replay and persistence."""

    at: datetime
    job_id: str
    event: str
    payload: dict[str, Any] = Field(default_factory=dict)
