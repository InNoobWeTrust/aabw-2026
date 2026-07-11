"""Tests for reviewer-assistant bounded tool loop behavior."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.assistant_service import ReviewAssistantService
from backend.assistant_store import FileSystemAssistantStore
from backend.job_store import FileSystemJobStore
from backend.review_store import FileSystemReviewStore
from domain.enums import (
    AssistantMessageRole,
    AssistantSessionStatus,
    JobStatus,
    PipelineStage,
    UserRole,
)
from domain.jobs import JobOwner


@pytest.mark.asyncio
async def test_assistant_local_loop_uses_tool_then_final(tmp_path, monkeypatch):
    """Local assistant mode should produce a tool message and a final assistant response."""
    job_store = FileSystemJobStore(tmp_path)
    review_store = FileSystemReviewStore(tmp_path)
    assistant_store = FileSystemAssistantStore(tmp_path)
    service = ReviewAssistantService(assistant_store, job_store, review_store)

    owner = JobOwner(role=UserRole.JUDGE, judge_session_id="judge-1")
    snapshot = job_store.create_job(owner, "input.mp4", ".mp4")
    job_store.update_job(
        snapshot.job_id,
        status=JobStatus.COMPLETED,
        stage=PipelineStage.FINALIZE,
        progress=1.0,
        message="done",
        result={"pose": {"metrics": {"detection_rate": 1.0}}, "retarget": {}},
        completed_at=datetime.now(timezone.utc),
    )

    monkeypatch.setattr("backend.assistant_service.settings.featherless_api_key", None)
    monkeypatch.setattr("backend.assistant_service.settings.daytona_api_key", None)

    session = service.create_session(snapshot.job_id, title="Assist")
    service.submit_user_message(snapshot.job_id, session.session_id, "Help me review this job")
    await service._tasks[(snapshot.job_id, session.session_id)]

    persisted = assistant_store.get_session(snapshot.job_id, session.session_id)
    assert persisted.status == AssistantSessionStatus.IDLE

    messages = assistant_store.list_messages(snapshot.job_id, session.session_id)
    roles = [m.role for m in messages]
    assert AssistantMessageRole.USER in roles
    assert AssistantMessageRole.TOOL in roles
    assert AssistantMessageRole.ASSISTANT in roles


def test_assistant_tool_reads_missing_review_gracefully(tmp_path):
    """Missing stage reviews should return a compact sentinel payload."""
    job_store = FileSystemJobStore(tmp_path)
    review_store = FileSystemReviewStore(tmp_path)
    assistant_store = FileSystemAssistantStore(tmp_path)
    service = ReviewAssistantService(assistant_store, job_store, review_store)

    owner = JobOwner(role=UserRole.JUDGE, judge_session_id="judge-1")
    snapshot = job_store.create_job(owner, "input.mp4", ".mp4")
    result = service._tool_get_pose_review(snapshot.job_id, {})
    assert result["status"] == "missing"
    assert result["stage"] == "pose"
