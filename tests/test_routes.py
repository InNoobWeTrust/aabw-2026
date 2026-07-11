"""Integration tests for backend video streaming and download routes."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.auth import create_access_token
from backend.routes import (
    _assistant_service,
    _assistant_store,
    _calibration_service,
    _calibration_store,
    _job_store,
    _queue_manager,
    _review_store,
)
from backend.server import create_app
from domain.auth import SessionIdentity
from domain.enums import (
    CalibrationDecision,
    CalibrationStatus,
    CalibrationVerdict,
    JobStatus,
    PipelineStage,
    UserRole,
)
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
    # Mock jobs directory in stores to point to tmp_path
    monkeypatch.setattr(_job_store, "_root", tmp_path)
    monkeypatch.setattr(_review_store, "_jobs_root", tmp_path)
    monkeypatch.setattr(_calibration_store, "_jobs_root", tmp_path)
    monkeypatch.setattr(_assistant_store, "_jobs_root", tmp_path)

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

    # 5. In demo mode, cross-session isolation is intentionally disabled.
    res = client.get(f"/api/jobs/{job_id}/video/original?token={other_judge_token}")
    assert res.status_code == 200

    # 6. In demo mode, unauthenticated local access is allowed.
    res = client.get(f"/api/jobs/{job_id}/video/original")
    assert res.status_code == 200


def test_artifact_and_review_endpoints(client, tmp_path, monkeypatch):
    """Artifact manifest, fine-grained downloads, and review snapshots should be accessible."""
    monkeypatch.setattr(_job_store, "_root", tmp_path)
    monkeypatch.setattr(_review_store, "_jobs_root", tmp_path)
    monkeypatch.setattr(_calibration_store, "_jobs_root", tmp_path)
    monkeypatch.setattr(_assistant_store, "_jobs_root", tmp_path)

    job_id = "test-job-uuid-2"
    session_id = "session-uuid-2"

    job_dir = tmp_path / job_id
    (job_dir / "upload").mkdir(parents=True)
    (job_dir / "output" / "dataset_skeleton").mkdir(parents=True)
    (job_dir / "output" / "dataset_robot").mkdir(parents=True)
    (job_dir / "output" / "reviews" / "pose").mkdir(parents=True)

    video_file = job_dir / "upload" / "input.mp4"
    video_file.write_text("original")
    (job_dir / "output" / "skeleton_overlay.mp4").write_text("overlay")
    (job_dir / "output" / "skeleton_preview.mp4").write_text("preview")
    (job_dir / "output" / "simulation.mp4").write_text("simulation")
    (job_dir / "output" / "dataset_skeleton" / "meta.json").write_text("{}")
    (job_dir / "output" / "dataset_robot" / "meta.json").write_text("{}")
    (job_dir / "output" / "reviews" / "pose" / "review.md").write_text("# Pose review")
    (job_dir / "output" / "reviews" / "pose" / "review.json").write_text(
        """{
  \"job_id\": \"test-job-uuid-2\",
  \"review_stage\": \"pose\",
  \"status\": \"completed\",
  \"provider\": \"featherless\",
  \"sandbox\": \"daytona\",
  \"started_at\": \"2026-07-11T00:00:00Z\",
  \"completed_at\": \"2026-07-11T00:00:01Z\",
  \"verdict\": \"approved\",
  \"summary\": \"ok\",
  \"markdown_path\": \"pose/review.md\",
  \"json_path\": \"pose/payload.json\",
  \"error\": null,
  \"context_manifest\": {},
  \"metadata\": {}
}
"""
    )
    (job_dir / "output" / "reviews" / "pose" / "events.jsonl").write_text(
        '{"at":"2026-07-11T00:00:00Z","job_id":"test-job-uuid-2","review_stage":"pose","event":"status","payload":{"status":"completed"}}\n'
    )

    owner = JobOwner(role=UserRole.JUDGE, judge_session_id=session_id)
    snapshot = JobSnapshot(
        job_id=job_id,
        owner=owner,
        original_filename="input.mp4",
        upload_path=str(video_file),
        output_dir=str(job_dir / "output"),
        status=JobStatus.COMPLETED,
        stage=PipelineStage.FINALIZE,
        progress=1.0,
        message="Completed",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        result={
            "pose": {"metrics": {"detection_rate": 1.0}},
            "retarget": {
                "mapping_profile": {
                    "profile_version": 1,
                    "handedness": "right",
                    "workspace_scale": 1.2214285714285714,
                }
            },
        },
    )
    (job_dir / "job.json").write_text(snapshot.model_dump_json(by_alias=True))

    judge_token = create_access_token(
        SessionIdentity(role=UserRole.JUDGE, judge_session_id=session_id)
    )

    manifest = client.get(
        f"/api/jobs/{job_id}/artifacts", headers={"Authorization": f"Bearer {judge_token}"}
    )
    assert manifest.status_code == 200
    assert "dataset_skeleton_zip_url" in manifest.json()["artifacts"]

    reviews = client.get(
        f"/api/jobs/{job_id}/reviews", headers={"Authorization": f"Bearer {judge_token}"}
    )
    assert reviews.status_code == 200
    assert reviews.json()["reviews"][0]["review_stage"] == "pose"

    review = client.get(
        f"/api/jobs/{job_id}/reviews/pose", headers={"Authorization": f"Bearer {judge_token}"}
    )
    assert review.status_code == 200
    assert review.json()["verdict"] == "approved"

    download = client.get(
        f"/api/jobs/{job_id}/downloads/skeleton_overlay_video",
        headers={"Authorization": f"Bearer {judge_token}"},
    )
    assert download.status_code == 200
    assert download.text == "overlay"

    zipped = client.get(
        f"/api/jobs/{job_id}/downloads/dataset_skeleton_zip",
        headers={"Authorization": f"Bearer {judge_token}"},
    )
    assert zipped.status_code == 200
    assert zipped.headers["content-type"] == "application/zip"


def test_assistant_session_routes(client, tmp_path, monkeypatch):
    """Assistant session routes should create, read, and append transcript state."""
    monkeypatch.setattr(_job_store, "_root", tmp_path)
    monkeypatch.setattr(_review_store, "_jobs_root", tmp_path)
    monkeypatch.setattr(_calibration_store, "_jobs_root", tmp_path)
    monkeypatch.setattr(_assistant_store, "_jobs_root", tmp_path)

    async def fake_submit(job_id, session_id, content):
        return None

    monkeypatch.setattr(
        _assistant_service, "submit_user_message", lambda job_id, session_id, content: None
    )

    job_id = "test-job-uuid-3"
    session_id = "session-uuid-3"
    job_dir = tmp_path / job_id
    (job_dir / "upload").mkdir(parents=True)
    (job_dir / "output").mkdir(parents=True)
    video_file = job_dir / "upload" / "input.mp4"
    video_file.write_text("original")

    owner = JobOwner(role=UserRole.JUDGE, judge_session_id=session_id)
    snapshot = JobSnapshot(
        job_id=job_id,
        owner=owner,
        original_filename="input.mp4",
        upload_path=str(video_file),
        output_dir=str(job_dir / "output"),
        status=JobStatus.COMPLETED,
        stage=PipelineStage.FINALIZE,
        progress=1.0,
        message="Completed",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        result={},
    )
    (job_dir / "job.json").write_text(snapshot.model_dump_json(by_alias=True))

    judge_token = create_access_token(
        SessionIdentity(role=UserRole.JUDGE, judge_session_id=session_id)
    )

    created = client.post(
        f"/api/jobs/{job_id}/assistant/sessions",
        headers={"Authorization": f"Bearer {judge_token}"},
        json={"title": "Review help", "message": "What should I inspect first?"},
    )
    assert created.status_code == 200
    session = created.json()["session"]
    assert session["title"] == "Review help"
    new_session_id = session["session_id"]

    listed = client.get(
        f"/api/jobs/{job_id}/assistant/sessions",
        headers={"Authorization": f"Bearer {judge_token}"},
    )
    assert listed.status_code == 200
    assert listed.json()["sessions"][0]["session_id"] == new_session_id

    posted = client.post(
        f"/api/jobs/{job_id}/assistant/sessions/{new_session_id}/messages",
        headers={"Authorization": f"Bearer {judge_token}"},
        json={"content": "Check the pose metrics."},
    )
    assert posted.status_code == 200

    detail = client.get(
        f"/api/jobs/{job_id}/assistant/sessions/{new_session_id}",
        headers={"Authorization": f"Bearer {judge_token}"},
    )
    assert detail.status_code == 200
    assert detail.json()["session"]["session_id"] == new_session_id


def test_job_result_includes_mapping_profile(client, tmp_path, monkeypatch):
    """Completed job result payload should include a mapping_profile in the retarget branch."""
    monkeypatch.setattr(_job_store, "_root", tmp_path)

    job_id = "test-job-mapping-1"
    session_id = "session-mapping-1"
    job_dir = tmp_path / job_id
    (job_dir / "upload").mkdir(parents=True)
    (job_dir / "output").mkdir(parents=True)
    video_file = job_dir / "upload" / "input.mp4"
    video_file.write_text("video")

    owner = JobOwner(role=UserRole.JUDGE, judge_session_id=session_id)
    snapshot = JobSnapshot(
        job_id=job_id,
        owner=owner,
        original_filename="input.mp4",
        upload_path=str(video_file),
        output_dir=str(job_dir / "output"),
        status=JobStatus.COMPLETED,
        stage=PipelineStage.FINALIZE,
        progress=1.0,
        message="Completed",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        result={
            "retarget": {
                "frame_count": 3,
                "robot": "franka_panda",
                "mapping_profile": {
                    "profile_version": 1,
                    "handedness": "right",
                    "wrist_landmark_index": 16,
                    "workspace_scale": 1.22,
                    "depth_scale": 1.0,
                },
            },
            "calibration": {
                "mapping_context_samples": {
                    "sample_count": 2,
                    "json_path": "/tmp/mapping_context_samples.json",
                    "samples": [],
                }
            },
        },
    )
    (job_dir / "job.json").write_text(snapshot.model_dump_json(by_alias=True))

    judge_token = create_access_token(
        SessionIdentity(role=UserRole.JUDGE, judge_session_id=session_id)
    )

    res = client.get(
        f"/api/jobs/{job_id}",
        headers={"Authorization": f"Bearer {judge_token}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert "retarget" in body["result"]
    assert "mapping_profile" in body["result"]["retarget"]
    assert body["result"]["retarget"]["mapping_profile"]["profile_version"] == 1
    assert body["result"]["calibration"]["mapping_context_samples"]["sample_count"] == 2


def test_mapping_calibration_endpoints(client, tmp_path, monkeypatch):
    """Mapping calibration routes should return snapshots, allow reruns, and stream events."""
    from backend.calibration_store import FileSystemCalibrationStore
    from domain.calibration import CalibrationEvent, CalibrationSnapshot

    monkeypatch.setattr(_job_store, "_root", tmp_path)
    monkeypatch.setattr(_calibration_store, "_jobs_root", tmp_path)

    job_id = "test-job-calibration-1"
    session_id = "session-calibration-1"
    job_dir = tmp_path / job_id
    (job_dir / "upload").mkdir(parents=True)
    (job_dir / "output" / "calibration").mkdir(parents=True)
    video_file = job_dir / "upload" / "input.mp4"
    video_file.write_text("video")

    snapshot = JobSnapshot(
        job_id=job_id,
        owner=JobOwner(role=UserRole.JUDGE, judge_session_id=session_id),
        original_filename="input.mp4",
        upload_path=str(video_file),
        output_dir=str(job_dir / "output"),
        status=JobStatus.COMPLETED,
        stage=PipelineStage.FINALIZE,
        progress=1.0,
        message="Completed",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        result={
            "pose": {"metrics": {"detection_rate": 0.95}},
            "retarget": {
                "evaluation": {"overall_grade": "yellow", "sudden_jump_count": 4},
                "mapping_profile": {
                    "profile_version": 1,
                    "handedness": "right",
                    "workspace_scale": 1.22,
                    "depth_scale": 1.0,
                    "z_clamp_enabled": False,
                    "position_only": True,
                    "axis_mapping": {"x": "-z", "y": "-x", "z": "y"},
                },
            },
            "calibration": {
                "mapping_context_samples": {
                    "sample_count": 2,
                    "json_path": str(
                        job_dir / "output" / "calibration" / "mapping_context_samples.json"
                    ),
                    "samples": [],
                }
            },
        },
    )
    (job_dir / "job.json").write_text(snapshot.model_dump_json(by_alias=True))

    store = FileSystemCalibrationStore(tmp_path)
    calibration_snapshot = CalibrationSnapshot(
        job_id=job_id,
        status=CalibrationStatus.COMPLETED,
        provider="openai_compatible",
        sandbox="local_process",
        decision=CalibrationDecision.RERUN_WITH_PROFILE,
        verdict=CalibrationVerdict.ROBOT_MAPPING_SALVAGEABLE,
        summary="Depth should be damped before rerun.",
        json_path=str(job_dir / "output" / "calibration" / "decision.json"),
    )
    store.write_snapshot(calibration_snapshot)
    store.write_decision_payload(
        job_id,
        {
            "decision": CalibrationDecision.RERUN_WITH_PROFILE.value,
            "verdict": CalibrationVerdict.ROBOT_MAPPING_SALVAGEABLE.value,
            "summary": "Depth should be damped before rerun.",
            "mapping_profile": {"profile_version": 1, "depth_scale": 0.65},
            "anchors": [],
            "confidence": 0.74,
            "risks": ["depth instability"],
        },
    )
    store.append_event(
        CalibrationEvent(
            at=datetime.now(timezone.utc),
            job_id=job_id,
            event="result",
            payload={"decision": CalibrationDecision.RERUN_WITH_PROFILE.value},
        )
    )

    judge_token = create_access_token(
        SessionIdentity(role=UserRole.JUDGE, judge_session_id=session_id)
    )

    get_res = client.get(
        f"/api/jobs/{job_id}/mapping-calibration",
        headers={"Authorization": f"Bearer {judge_token}"},
    )
    assert get_res.status_code == 200
    assert get_res.json()["decision"] == CalibrationDecision.RERUN_WITH_PROFILE.value

    streamed = client.get(
        f"/api/jobs/{job_id}/mapping-calibration/stream?token={judge_token}",
        headers={"Accept": "text/event-stream"},
    )
    assert streamed.status_code == 200
    assert "event: result" in streamed.text
    assert CalibrationDecision.RERUN_WITH_PROFILE.value in streamed.text

    called = {}

    def fake_schedule(*, job_id, context_manifest, calibration_factory):
        called["job_id"] = job_id
        called["context_manifest"] = context_manifest

    monkeypatch.setattr(_calibration_service, "schedule_calibration", fake_schedule)

    post_res = client.post(
        f"/api/jobs/{job_id}/mapping-calibration/run",
        headers={"Authorization": f"Bearer {judge_token}"},
    )
    assert post_res.status_code == 200
    assert called["job_id"] == job_id
    assert "pose_metrics" in called["context_manifest"]
    assert "retarget_metrics" in called["context_manifest"]
    assert "mapping_context_samples" in called["context_manifest"]
