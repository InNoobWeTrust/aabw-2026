"""Tests for checkpointed mapping sessions and related routes."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.auth import create_access_token
from backend.mapping_session_service import MappingSessionService
from backend.mapping_session_store import FileSystemMappingSessionStore
from backend.routes import _job_store, _mapping_session_store, _queue_manager
from backend.server import create_app
from domain.auth import SessionIdentity
from domain.enums import CheckpointAuthor, JobStatus, PipelineStage, UserRole
from domain.jobs import JobOwner, JobSnapshot
from domain.mapping import MappingProfile


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(_queue_manager, "start", lambda: None)
    monkeypatch.setattr(_queue_manager, "recover_on_startup", lambda: 0)
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def mapping_job(tmp_path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    monkeypatch.setattr(_job_store, "_root", tmp_path)
    monkeypatch.setattr(_mapping_session_store, "_jobs_root", tmp_path)

    job_id = "mapping-job"
    session_id = "mapping-owner"
    job_dir = tmp_path / job_id
    (job_dir / "upload").mkdir(parents=True)
    (job_dir / "output").mkdir(parents=True)

    snapshot = JobSnapshot(
        job_id=job_id,
        owner=JobOwner(role=UserRole.JUDGE, judge_session_id=session_id),
        original_filename="input.mp4",
        upload_path=str(job_dir / "upload" / "input.mp4"),
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
                "mapping_profile": {
                    "profile_version": 1,
                    "handedness": "right",
                    "workspace_scale": 1.22,
                    "depth_scale": 1.0,
                    "axis_mapping": {"x": "-z", "y": "-x", "z": "y"},
                    "z_clamp_enabled": False,
                    "position_only": True,
                }
            }
        },
    )
    (job_dir / "job.json").write_text(snapshot.model_dump_json(by_alias=True))
    return job_id, session_id


def test_mapping_session_store_create_checkpoint_and_restore(tmp_path) -> None:
    store = FileSystemMappingSessionStore(tmp_path)
    session = store.create_session("job-1", title="Manual tune")
    baseline = store.create_checkpoint(
        session.session_id,
        "job-1",
        author=CheckpointAuthor.BASELINE,
        mapping_profile=MappingProfile(),
        summary="Baseline",
    )
    candidate = store.create_checkpoint(
        session.session_id,
        "job-1",
        author=CheckpointAuthor.MANUAL,
        mapping_profile=MappingProfile(workspace_scale=0.9),
        summary="Manual tweak",
        parent_checkpoint_id=baseline.checkpoint_id,
    )

    restored = store.restore_checkpoint("job-1", session.session_id, baseline.checkpoint_id)

    assert candidate.parent_checkpoint_id == baseline.checkpoint_id
    assert restored.current_checkpoint_id == baseline.checkpoint_id
    assert len(store.list_events("job-1", session.session_id)) >= 3


def test_mapping_session_service_seeds_baseline_checkpoint(tmp_path) -> None:
    jobs_root = tmp_path
    job_id = "service-job"
    job_dir = jobs_root / job_id
    (job_dir / "upload").mkdir(parents=True)
    (job_dir / "output").mkdir(parents=True)
    snapshot = JobSnapshot(
        job_id=job_id,
        owner=JobOwner(role=UserRole.JUDGE, judge_session_id="owner"),
        original_filename="input.mp4",
        upload_path=str(job_dir / "upload" / "input.mp4"),
        output_dir=str(job_dir / "output"),
        status=JobStatus.COMPLETED,
        stage=PipelineStage.FINALIZE,
        progress=1.0,
        message="Completed",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        result={"retarget": {"mapping_profile": MappingProfile().model_dump()}},
    )
    (job_dir / "job.json").write_text(snapshot.model_dump_json(by_alias=True))

    service = MappingSessionService(
        FileSystemMappingSessionStore(jobs_root),
        _job_store.__class__(jobs_root),
    )
    session, checkpoint = service.create_session(job_id, title="Seeded")

    assert session.current_checkpoint_id is None
    assert checkpoint.author == CheckpointAuthor.BASELINE
    assert checkpoint.mapping_profile.handedness == "right"


def test_mapping_session_routes_create_checkpoint_and_restore(
    client: TestClient,
    mapping_job: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id, session_id = mapping_job
    token = create_access_token(SessionIdentity(role=UserRole.JUDGE, judge_session_id=session_id))

    create_res = client.post(
        f"/api/jobs/{job_id}/mapping-sessions",
        headers={"Authorization": f"Bearer {token}"},
        json={"title": "Operator tuning"},
    )
    assert create_res.status_code == 200
    detail = create_res.json()
    mapping_session_id = detail["session"]["session_id"]
    baseline_checkpoint_id = detail["checkpoints"][0]["checkpoint_id"]

    checkpoint_res = client.post(
        f"/api/jobs/{job_id}/mapping-sessions/{mapping_session_id}/checkpoints",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "mapping_profile": {
                "profile_version": 1,
                "handedness": "right",
                "workspace_scale": 0.95,
                "depth_scale": 0.8,
                "axis_mapping": {"x": "-z", "y": "-x", "z": "y"},
                "z_clamp_enabled": True,
                "position_only": True,
            },
            "author": "manual",
            "summary": "Tighter manual workspace",
        },
    )
    assert checkpoint_res.status_code == 200
    assert len(checkpoint_res.json()["checkpoints"]) == 2

    restore_res = client.post(
        f"/api/jobs/{job_id}/mapping-sessions/{mapping_session_id}/restore",
        headers={"Authorization": f"Bearer {token}"},
        json={"checkpoint_id": baseline_checkpoint_id},
    )
    assert restore_res.status_code == 200
    assert restore_res.json()["session"]["current_checkpoint_id"] == baseline_checkpoint_id

    get_res = client.get(
        f"/api/jobs/{job_id}/mapping-sessions/{mapping_session_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_res.status_code == 200
    assert get_res.json()["session"]["session_id"] == mapping_session_id


def test_mapping_session_checkpoint_rejects_invalid_profile(
    client: TestClient,
    mapping_job: tuple[str, str],
) -> None:
    job_id, session_id = mapping_job
    token = create_access_token(SessionIdentity(role=UserRole.JUDGE, judge_session_id=session_id))

    create_res = client.post(
        f"/api/jobs/{job_id}/mapping-sessions",
        headers={"Authorization": f"Bearer {token}"},
        json={"title": "Operator tuning"},
    )
    mapping_session_id = create_res.json()["session"]["session_id"]

    bad_res = client.post(
        f"/api/jobs/{job_id}/mapping-sessions/{mapping_session_id}/checkpoints",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "mapping_profile": {
                "profile_version": 1,
                "handedness": "right",
                "workspace_scale": 1.0,
                "depth_scale": 1.0,
                "axis_mapping": {"x": "bad", "y": "-x", "z": "y"},
                "z_clamp_enabled": False,
                "position_only": True,
            },
            "author": "manual",
        },
    )
    assert bad_res.status_code == 422 or bad_res.status_code == 400
