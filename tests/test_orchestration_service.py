"""Tests for adaptive orchestration heuristics, persistence, and routes."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.auth import create_access_token
from backend.orchestration_service import (
    OrchestrationService,
    _normalize_orchestration_result_payload,
    build_evidence_manifest,
    build_orchestration_factory,
)
from backend.orchestration_store import FileSystemOrchestrationStore
from backend.routes import (
    _assistant_store,
    _calibration_store,
    _job_store,
    _mapping_session_store,
    _orchestration_service,
    _orchestration_store,
    _queue_manager,
    _review_store,
)
from backend.server import create_app
from domain.auth import SessionIdentity
from domain.enums import JobStatus, OrchestrationDecision, PipelineStage, UserRole
from domain.jobs import JobOwner, JobSnapshot


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(_queue_manager, "start", lambda: None)
    monkeypatch.setattr(_queue_manager, "recover_on_startup", lambda: 0)
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def completed_job(tmp_path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    monkeypatch.setattr(_job_store, "_root", tmp_path)
    monkeypatch.setattr(_review_store, "_jobs_root", tmp_path)
    monkeypatch.setattr(_calibration_store, "_jobs_root", tmp_path)
    monkeypatch.setattr(_orchestration_store, "_jobs_root", tmp_path)
    monkeypatch.setattr(_mapping_session_store, "_jobs_root", tmp_path)
    monkeypatch.setattr(_assistant_store, "_jobs_root", tmp_path)

    job_id = "orchestration-job"
    session_id = "judge-session"
    job_dir = tmp_path / job_id
    (job_dir / "upload").mkdir(parents=True)
    (job_dir / "output").mkdir(parents=True)
    (job_dir / "upload" / "input.mp4").write_text("video")

    owner = JobOwner(role=UserRole.JUDGE, judge_session_id=session_id)
    snapshot = JobSnapshot(
        job_id=job_id,
        owner=owner,
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
            "pose": {
                "detection_rate": 0.96,
                "metrics": {
                    "detection_rate": 0.96,
                    "missing_landmark_ratio": 0.03,
                },
            },
            "retarget": {
                "mapping_profile": {
                    "profile_version": 1,
                    "handedness": "right",
                    "workspace_scale": 1.22,
                    "depth_scale": 1.0,
                    "axis_mapping": {"x": "-z", "y": "-x", "z": "y"},
                    "z_clamp_enabled": False,
                    "position_only": True,
                },
                "evaluation": {
                    "overall_grade": "yellow",
                    "completeness_ratio": 0.93,
                    "sudden_jump_count": 5,
                    "nan_count": 0,
                    "joint_limit_violations": 0,
                    "max_velocity": 1.9,
                    "mean_jerk": 1.2,
                },
            },
        },
    )
    (job_dir / "job.json").write_text(snapshot.model_dump_json(by_alias=True))
    return job_id, session_id


def test_build_evidence_manifest_collects_cross_stage_fields() -> None:
    manifest = build_evidence_manifest(
        {
            "pose": {"metrics": {"detection_rate": 0.9, "missing_landmark_ratio": 0.1}},
            "retarget": {
                "mapping_profile": {"handedness": "right"},
                "evaluation": {"overall_grade": "green", "completeness_ratio": 1.0},
            },
        },
        {"verdict": "approved"},
        {"verdict": "needs_review"},
        {"decision": "baseline_ok"},
    )

    assert manifest["pose_metrics"]["detection_rate"] == 0.9
    assert manifest["evaluation_metrics"]["overall_grade"] == "green"
    assert manifest["pose_review"]["verdict"] == "approved"
    assert manifest["calibration"]["decision"] == "baseline_ok"


def test_orchestration_factory_returns_retry_capture_for_critical_failure() -> None:
    payload = build_orchestration_factory(
        {
            "pose_metrics": {"detection_rate": 0.32, "missing_landmark_ratio": 0.51},
            "evaluation_metrics": {"completeness_ratio": 0.2, "overall_grade": "red"},
            "pose_review": {"verdict": "rejected"},
            "retarget_review": None,
            "calibration": None,
            "baseline_mapping_profile": None,
        }
    )()

    assert payload["decision"] == OrchestrationDecision.RETRY_CAPTURE.value
    assert payload["capture_guidance"]["suggestions"]


def test_orchestration_factory_returns_rerun_with_profile_for_salvageable_motion() -> None:
    payload = build_orchestration_factory(
        {
            "pose_metrics": {"detection_rate": 0.92, "missing_landmark_ratio": 0.06},
            "evaluation_metrics": {
                "overall_grade": "yellow",
                "completeness_ratio": 0.95,
                "sudden_jump_count": 5,
                "nan_count": 0,
                "joint_limit_violations": 0,
                "max_velocity": 1.8,
                "mean_jerk": 1.1,
            },
            "pose_review": {"verdict": "approved"},
            "retarget_review": {"verdict": "needs_review"},
            "calibration": {"decision": "baseline_ok"},
            "baseline_mapping_profile": {
                "profile_version": 1,
                "handedness": "right",
                "workspace_scale": 1.22,
                "depth_scale": 1.0,
                "axis_mapping": {"x": "-z", "y": "-x", "z": "y"},
                "z_clamp_enabled": False,
                "position_only": True,
            },
        }
    )()

    assert payload["decision"] == OrchestrationDecision.RERUN_WITH_PROFILE.value
    assert payload["tuned_mapping_profile"]["workspace_scale"] < 1.22


def test_normalize_orchestration_result_payload_unwraps_provider_wrappers() -> None:
    normalized = _normalize_orchestration_result_payload(
        {
            "data": {
                "decision": "baseline_ok",
                "confidence": 0.85,
                "summary": "wrapped summary",
                "risks": ["one"],
                "evidence_snapshot": {"pose_metrics": {"detection_rate": 1.0}},
            }
        }
    )

    assert normalized.decision == OrchestrationDecision.BASELINE_OK
    assert normalized.summary == "wrapped summary"
    assert normalized.risks == ["one"]


def test_normalize_orchestration_result_payload_rejects_invalid_shapes() -> None:
    with pytest.raises(RuntimeError, match="orchestration_invalid_result_payload"):
        _normalize_orchestration_result_payload({"data": {"summary": "missing decision"}})


def test_orchestration_routes_schedule_and_read_snapshot(
    client: TestClient,
    completed_job: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id, session_id = completed_job
    token = create_access_token(SessionIdentity(role=UserRole.JUDGE, judge_session_id=session_id))

    def _fake_schedule(*, job_id: str, evidence_manifest: dict, orchestration_factory):
        _orchestration_store.create_orchestration(
            job_id,
            provider="local",
            sandbox="local",
            evidence_manifest=evidence_manifest,
            metadata={"execution_mode": "test"},
        )

    monkeypatch.setattr(_orchestration_service, "schedule_orchestration", _fake_schedule)

    run_res = client.post(
        f"/api/jobs/{job_id}/orchestration/run",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert run_res.status_code == 200
    assert run_res.json()["status"] == "pending"

    get_res = client.get(
        f"/api/jobs/{job_id}/orchestration",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_res.status_code == 200
    assert get_res.json()["job_id"] == job_id


def test_orchestration_run_route_works_with_real_scheduler(
    client: TestClient,
    completed_job: tuple[str, str],
) -> None:
    """The orchestration trigger should run inside an event loop, not a threadpool-only context."""
    job_id, session_id = completed_job
    token = create_access_token(
        SessionIdentity(role=UserRole.JUDGE, judge_session_id=session_id)
    )

    res = client.post(
        f"/api/jobs/{job_id}/orchestration/run",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    assert res.json()["status"] in {"pending", "running", "completed"}


@pytest.mark.asyncio
async def test_orchestration_service_emits_progress_events(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Long-running orchestration should emit human-readable progress events and heartbeats."""
    monkeypatch.setattr("backend.orchestration_service.settings.llm_api_key", None)
    monkeypatch.setattr("backend.orchestration_service.settings.featherless_api_key", None)
    monkeypatch.setattr("backend.orchestration_service.settings.llm_model_name", "test-model")

    store = FileSystemOrchestrationStore(tmp_path)
    service = OrchestrationService(store)

    def slow_factory() -> dict:
        import time

        time.sleep(2.7)
        return {
            "decision": "baseline_ok",
            "confidence": 0.9,
            "summary": "done",
            "risks": [],
            "capture_guidance": None,
            "tuned_mapping_profile": None,
        }

    service.schedule_orchestration(
        job_id="progress-job",
        evidence_manifest={"pose_metrics": {}, "evaluation_metrics": {}},
        orchestration_factory=slow_factory,
    )

    await asyncio.wait_for(service._tasks["progress-job"], timeout=10)

    events = store.list_events("progress-job")
    progress_events = [event for event in events if event.event == "progress"]
    messages = [str(event.payload.get("message", "")) for event in progress_events]

    assert any("Preparing orchestration run" in message for message in messages)
    assert any("Still running in phase" in message for message in messages)
    assert any("Orchestration completed" in message for message in messages)


def test_orchestration_run_requires_completed_job(
    client: TestClient,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_job_store, "_root", tmp_path)
    monkeypatch.setattr(_orchestration_store, "_jobs_root", tmp_path)

    job_id = "queued-job"
    session_id = "judge-session"
    job_dir = tmp_path / job_id
    (job_dir / "upload").mkdir(parents=True)
    (job_dir / "output").mkdir(parents=True)

    snapshot = JobSnapshot(
        job_id=job_id,
        owner=JobOwner(role=UserRole.JUDGE, judge_session_id=session_id),
        original_filename="input.mp4",
        upload_path=str(job_dir / "upload" / "input.mp4"),
        output_dir=str(job_dir / "output"),
        status=JobStatus.QUEUED,
        stage=PipelineStage.INGEST,
        progress=0.0,
        message="Queued",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    (job_dir / "job.json").write_text(snapshot.model_dump_json(by_alias=True))

    token = create_access_token(SessionIdentity(role=UserRole.JUDGE, judge_session_id=session_id))
    res = client.post(
        f"/api/jobs/{job_id}/orchestration/run",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 400
    assert "completed job" in res.json()["detail"]
