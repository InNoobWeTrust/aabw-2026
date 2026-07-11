"""Integration tests for backend video streaming and download routes."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.auth import create_access_token
from backend.routes import _job_store, _queue_manager
from backend.server import create_app
from domain.auth import SessionIdentity
from domain.enums import JobStatus, PipelineStage, UserRole
from domain.jobs import JobOwner, JobSnapshot


@pytest.fixture
def client(monkeypatch):
    # Prevent background worker loop from running during testing
    monkeypatch.setattr(_queue_manager, "start", lambda: None)
    monkeypatch.setattr(_queue_manager, "recover_on_startup", lambda: 0)

    app = create_app()
    with TestClient(app) as c:
        yield c


def test_video_endpoints_access_control(client, tmp_path, monkeypatch):
    # Mock jobs directory in job store to point to tmp_path
    monkeypatch.setattr(_job_store, "_root", tmp_path)

    job_id = "test-job-uuid-1"
    session_id = "session-uuid-1"

    # Set up dummy job directory and files
    job_dir = tmp_path / job_id
    job_dir.mkdir()

    upload_dir = job_dir / "upload"
    upload_dir.mkdir()
    video_file = upload_dir / "input.mp4"
    video_file.write_text("original-video-data")

    output_dir = job_dir / "output"
    output_dir.mkdir()
    sim_file = output_dir / "simulation.mp4"
    sim_file.write_text("simulation-video-data")

    # Save job.json representation
    owner = JobOwner(role=UserRole.JUDGE, judge_session_id=session_id)
    job_state = JobSnapshot(
        job_id=job_id,
        owner=owner,
        original_filename="input.mp4",
        upload_path=str(video_file),
        output_dir=str(output_dir),
        status=JobStatus.COMPLETED,
        stage=PipelineStage.FINALIZE,
        progress=1.0,
        message="Completed",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        result={"static_checks": {"status": "passed"}},
    )
    (job_dir / "job.json").write_text(job_state.model_dump_json(by_alias=True))

    # Generate tokens
    judge_identity = SessionIdentity(role=UserRole.JUDGE, judge_session_id=session_id)
    judge_token = create_access_token(judge_identity)

    other_judge_identity = SessionIdentity(role=UserRole.JUDGE, judge_session_id="other-session-2")
    other_judge_token = create_access_token(other_judge_identity)

    admin_identity = SessionIdentity(role=UserRole.ADMIN, judge_session_id=None)
    admin_token = create_access_token(admin_identity)

    # 1. Access original video via Header Auth (Judge)
    res = client.get(
        f"/api/jobs/{job_id}/video/original",
        headers={"Authorization": f"Bearer {judge_token}"},
    )
    assert res.status_code == 200
    assert res.text == "original-video-data"

    # 2. Access simulation video via Query Param Auth (Judge)
    res = client.get(f"/api/jobs/{job_id}/video/simulation?token={judge_token}")
    assert res.status_code == 200
    assert res.text == "simulation-video-data"

    # 3. Access original video via Query Param Auth (Admin)
    res = client.get(f"/api/jobs/{job_id}/video/original?token={admin_token}")
    assert res.status_code == 200
    assert res.text == "original-video-data"

    # 4. Access simulation video via Header Auth (Admin)
    res = client.get(
        f"/api/jobs/{job_id}/video/simulation",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res.status_code == 200
    assert res.text == "simulation-video-data"

    # 5. Access isolation: other judge gets 404 (existence leaks avoided)
    res = client.get(f"/api/jobs/{job_id}/video/original?token={other_judge_token}")
    assert res.status_code == 404

    # 6. Unauthenticated request gets 401
    res = client.get(f"/api/jobs/{job_id}/video/original")
    assert res.status_code == 401
