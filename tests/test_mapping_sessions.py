"""Tests for checkpointed mapping sessions and related routes."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.auth import create_access_token
from backend.checkpoint_rerun_store import FileSystemCheckpointRerunStore
from backend.mapping_session_service import MappingSessionService
from backend.mapping_session_store import FileSystemMappingSessionStore
from backend.routes import _job_store, _mapping_session_store, _queue_manager, _rerun_store
from backend.server import create_app
from domain.auth import SessionIdentity
from domain.enums import CheckpointAuthor, JobStatus, PipelineStage, RerunStatus, UserRole
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
    monkeypatch.setattr(_rerun_store, "_jobs_root", tmp_path)

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


class TestCheckpointRerunStore:
    def test_create_rerun_increments_version(self, tmp_path) -> None:
        store = FileSystemMappingSessionStore(tmp_path)
        rerun_store = FileSystemCheckpointRerunStore(tmp_path)
        session = store.create_session("job-1", title="Rerun session")
        profile = MappingProfile(workspace_scale=1.0)

        r1 = rerun_store.create_rerun(
            session,
            source_checkpoint_id="cp-1",
            mapping_profile=profile,
        )
        r2 = rerun_store.create_rerun(
            session,
            source_checkpoint_id="cp-2",
            mapping_profile=MappingProfile(workspace_scale=0.8),
        )

        assert r1.version == 1
        assert r2.version == 2
        assert r1.status == RerunStatus.PENDING
        assert r2.source_checkpoint_id == "cp-2"
        assert r2.mapping_profile.workspace_scale == 0.8

    def test_get_rerun_and_list(self, tmp_path) -> None:
        store = FileSystemMappingSessionStore(tmp_path)
        rerun_store = FileSystemCheckpointRerunStore(tmp_path)
        session = store.create_session("job-1", title="Rerun session")

        r1 = rerun_store.create_rerun(
            session,
            source_checkpoint_id="cp-a",
            mapping_profile=MappingProfile(),
        )
        rerun_store.create_rerun(
            session,
            source_checkpoint_id="cp-b",
            mapping_profile=MappingProfile(depth_scale=0.5),
        )

        fetched = rerun_store.get_rerun("job-1", session.session_id, r1.rerun_id)
        assert fetched.rerun_id == r1.rerun_id

        all_reruns = rerun_store.list_reruns("job-1", session.session_id)
        assert len(all_reruns) == 2
        assert all_reruns[0].version == 2
        assert all_reruns[1].version == 1

    def test_update_rerun_atomically(self, tmp_path) -> None:
        store = FileSystemMappingSessionStore(tmp_path)
        rerun_store = FileSystemCheckpointRerunStore(tmp_path)
        session = store.create_session("job-1")
        rerun = rerun_store.create_rerun(
            session,
            source_checkpoint_id="cp-1",
            mapping_profile=MappingProfile(),
        )

        updated = rerun_store.update_rerun(
            "job-1",
            session.session_id,
            rerun.rerun_id,
            status=RerunStatus.RUNNING,
            summary="Running rerun",
        )
        assert updated.status == RerunStatus.RUNNING
        assert updated.summary == "Running rerun"

        fetched = rerun_store.get_rerun("job-1", session.session_id, rerun.rerun_id)
        assert fetched.status == RerunStatus.RUNNING

    def test_write_artifacts_persists_files(self, tmp_path) -> None:
        store = FileSystemMappingSessionStore(tmp_path)
        rerun_store = FileSystemCheckpointRerunStore(tmp_path)
        session = store.create_session("job-1")
        rerun = rerun_store.create_rerun(
            session,
            source_checkpoint_id="cp-1",
            mapping_profile=MappingProfile(),
        )

        artifacts = {"dataset_path": "/some/output", "frame_count": 240}
        updated = rerun_store.write_artifacts(rerun, artifacts)

        assert updated.artifact_manifest is not None
        assert updated.artifact_manifest.artifacts == artifacts
        assert updated.artifact_manifest.artifacts["dataset_path"] == "/some/output"

        rerun_dir = (
            tmp_path
            / "job-1"
            / "output"
            / "mapping_sessions"
            / session.session_id
            / "reruns"
            / f"{rerun.version}_{rerun.rerun_id}"
        )
        assert (rerun_dir / "rerun.json").is_file()
        assert (rerun_dir / "mapping_profile.json").is_file()
        assert (rerun_dir / "artifacts.json").is_file()

    def test_list_reruns_empty(self, tmp_path) -> None:
        rerun_store = FileSystemCheckpointRerunStore(tmp_path)
        assert rerun_store.list_reruns("job-1", "no-such-session") == []

    def test_rerun_dir_naming_includes_version_and_id(self, tmp_path) -> None:
        store = FileSystemMappingSessionStore(tmp_path)
        rerun_store = FileSystemCheckpointRerunStore(tmp_path)
        session = store.create_session("job-x")

        r = rerun_store.create_rerun(
            session,
            source_checkpoint_id="cp-src",
            mapping_profile=MappingProfile(),
        )
        assert r.version == 1
        assert r.rerun_id
        assert r.status == RerunStatus.PENDING


class TestMappingSessionServiceRerunCheck:
    def test_active_rerun_blocked_returns_none_when_no_active_rerun(self, tmp_path) -> None:
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
        session_store = FileSystemMappingSessionStore(jobs_root)
        service = MappingSessionService(
            session_store,
            _job_store.__class__(jobs_root),
        )
        session, _ = service.create_session(job_id, title="Rerun test")

        blocked = service.active_rerun_blocked(job_id, session.session_id)
        assert blocked is None

    def test_active_rerun_blocked_returns_reason_when_rerun_running(self, tmp_path) -> None:
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
        session_store = FileSystemMappingSessionStore(jobs_root)
        rerun_store = FileSystemCheckpointRerunStore(jobs_root)
        service = MappingSessionService(
            session_store,
            _job_store.__class__(jobs_root),
        )
        session, _ = service.create_session(job_id, title="Rerun test")
        rerun = rerun_store.create_rerun(
            session,
            source_checkpoint_id="cp-1",
            mapping_profile=MappingProfile(),
        )
        rerun_store.update_rerun(
            job_id,
            session.session_id,
            rerun.rerun_id,
            status=RerunStatus.RUNNING,
        )
        session_store.update_session(
            job_id,
            session.session_id,
            active_rerun_id=rerun.rerun_id,
        )

        blocked = service.active_rerun_blocked(job_id, session.session_id)
        assert blocked is not None
        assert "still running" in blocked

    def test_active_rerun_blocked_clears_when_rerun_completed(self, tmp_path) -> None:
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
        session_store = FileSystemMappingSessionStore(jobs_root)
        rerun_store = FileSystemCheckpointRerunStore(jobs_root)
        service = MappingSessionService(
            session_store,
            _job_store.__class__(jobs_root),
        )
        session, _ = service.create_session(job_id, title="Rerun test")
        rerun = rerun_store.create_rerun(
            session,
            source_checkpoint_id="cp-1",
            mapping_profile=MappingProfile(),
        )
        rerun_store.update_rerun(
            job_id,
            session.session_id,
            rerun.rerun_id,
            status=RerunStatus.COMPLETED,
            completed_at=datetime.now(timezone.utc),
        )
        session_store.update_session(
            job_id,
            session.session_id,
            active_rerun_id=rerun.rerun_id,
        )

        blocked = service.active_rerun_blocked(job_id, session.session_id)
        assert blocked is None

    def test_active_rerun_blocked_archived_session(self, tmp_path) -> None:
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
        session_store = FileSystemMappingSessionStore(jobs_root)
        service = MappingSessionService(
            session_store,
            _job_store.__class__(jobs_root),
        )
        session, _ = service.create_session(job_id, title="Rerun test")
        service.archive_session(job_id, session.session_id)

        blocked = service.active_rerun_blocked(job_id, session.session_id)
        assert blocked is not None
        assert "archived" in blocked.lower()


class TestRerunRoutes:
    def test_trigger_rerun_creates_record(
        self,
        client: TestClient,
        mapping_job: tuple[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        job_id, session_id = mapping_job
        token = create_access_token(
            SessionIdentity(role=UserRole.JUDGE, judge_session_id=session_id)
        )

        create_res = client.post(
            f"/api/jobs/{job_id}/mapping-sessions",
            headers={"Authorization": f"Bearer {token}"},
            json={"title": "Rerun test"},
        )
        assert create_res.status_code == 200
        detail = create_res.json()
        mapping_session_id = detail["session"]["session_id"]
        checkpoint_id = detail["checkpoints"][0]["checkpoint_id"]

        res = client.post(
            f"/api/jobs/{job_id}/mapping-sessions/{mapping_session_id}/reruns",
            headers={"Authorization": f"Bearer {token}"},
            json={"checkpoint_id": checkpoint_id},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["rerun_id"]
        assert body["version"] == 1
        assert body["session_id"] == mapping_session_id
        assert body["source_checkpoint_id"] == checkpoint_id

    def test_trigger_rerun_defaults_to_current_checkpoint(
        self,
        client: TestClient,
        mapping_job: tuple[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        job_id, session_id = mapping_job
        token = create_access_token(
            SessionIdentity(role=UserRole.JUDGE, judge_session_id=session_id)
        )

        create_res = client.post(
            f"/api/jobs/{job_id}/mapping-sessions",
            headers={"Authorization": f"Bearer {token}"},
            json={"title": "Rerun test"},
        )
        detail = create_res.json()
        mapping_session_id = detail["session"]["session_id"]

        res = client.post(
            f"/api/jobs/{job_id}/mapping-sessions/{mapping_session_id}/reruns",
            headers={"Authorization": f"Bearer {token}"},
            json={},
        )
        assert res.status_code == 200
        assert res.json()["source_checkpoint_id"] == detail["checkpoints"][0]["checkpoint_id"]

    def test_list_reruns(self, client: TestClient, mapping_job: tuple[str, str]) -> None:
        from backend.routes import _rerun_store

        job_id, session_id = mapping_job
        token = create_access_token(
            SessionIdentity(role=UserRole.JUDGE, judge_session_id=session_id)
        )

        create_res = client.post(
            f"/api/jobs/{job_id}/mapping-sessions",
            headers={"Authorization": f"Bearer {token}"},
            json={"title": "Rerun list test"},
        )
        mapping_session_id = create_res.json()["session"]["session_id"]

        session = _mapping_session_store.get_session(job_id, mapping_session_id)
        checkpoint = _mapping_session_store.list_checkpoints(job_id, mapping_session_id)[0]
        _rerun_store.create_rerun(
            session,
            source_checkpoint_id=checkpoint.checkpoint_id,
            mapping_profile=checkpoint.mapping_profile,
        )

        res = client.get(
            f"/api/jobs/{job_id}/mapping-sessions/{mapping_session_id}/reruns",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        body = res.json()
        assert len(body["reruns"]) == 1
        assert body["reruns"][0]["version"] == 1

    def test_get_rerun_detail(self, client: TestClient, mapping_job: tuple[str, str]) -> None:
        from backend.routes import _rerun_store

        job_id, session_id = mapping_job
        token = create_access_token(
            SessionIdentity(role=UserRole.JUDGE, judge_session_id=session_id)
        )

        create_res = client.post(
            f"/api/jobs/{job_id}/mapping-sessions",
            headers={"Authorization": f"Bearer {token}"},
            json={"title": "Rerun detail test"},
        )
        mapping_session_id = create_res.json()["session"]["session_id"]

        session = _mapping_session_store.get_session(job_id, mapping_session_id)
        checkpoint = _mapping_session_store.list_checkpoints(job_id, mapping_session_id)[0]
        rerun = _rerun_store.create_rerun(
            session,
            source_checkpoint_id=checkpoint.checkpoint_id,
            mapping_profile=checkpoint.mapping_profile,
        )

        res = client.get(
            f"/api/jobs/{job_id}/mapping-sessions/{mapping_session_id}/reruns/{rerun.rerun_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["rerun_id"] == rerun.rerun_id
        assert body["source_checkpoint_id"] == checkpoint.checkpoint_id

    def test_active_rerun_blocks_checkpoint_create(
        self,
        client: TestClient,
        mapping_job: tuple[str, str],
    ) -> None:
        from backend.routes import _rerun_store

        job_id, session_id = mapping_job
        token = create_access_token(
            SessionIdentity(role=UserRole.JUDGE, judge_session_id=session_id)
        )

        create_res = client.post(
            f"/api/jobs/{job_id}/mapping-sessions",
            headers={"Authorization": f"Bearer {token}"},
            json={"title": "Block test"},
        )
        mapping_session_id = create_res.json()["session"]["session_id"]

        session = _mapping_session_store.get_session(job_id, mapping_session_id)
        checkpoint = _mapping_session_store.list_checkpoints(job_id, mapping_session_id)[0]
        rerun = _rerun_store.create_rerun(
            session,
            source_checkpoint_id=checkpoint.checkpoint_id,
            mapping_profile=checkpoint.mapping_profile,
        )
        _rerun_store.update_rerun(
            job_id, mapping_session_id, rerun.rerun_id, status=RerunStatus.RUNNING
        )
        _mapping_session_store.update_session(
            job_id, mapping_session_id, active_rerun_id=rerun.rerun_id
        )

        res = client.post(
            f"/api/jobs/{job_id}/mapping-sessions/{mapping_session_id}/checkpoints",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "mapping_profile": {
                    "profile_version": 1,
                    "handedness": "right",
                    "workspace_scale": 0.9,
                    "depth_scale": 1.0,
                    "axis_mapping": {"x": "-z", "y": "-x", "z": "y"},
                    "z_clamp_enabled": False,
                    "position_only": True,
                },
                "author": "manual",
                "summary": "Should be blocked",
            },
        )
        assert res.status_code == 409

    def test_active_rerun_blocks_checkpoint_restore(
        self,
        client: TestClient,
        mapping_job: tuple[str, str],
    ) -> None:
        from backend.routes import _rerun_store

        job_id, session_id = mapping_job
        token = create_access_token(
            SessionIdentity(role=UserRole.JUDGE, judge_session_id=session_id)
        )

        create_res = client.post(
            f"/api/jobs/{job_id}/mapping-sessions",
            headers={"Authorization": f"Bearer {token}"},
            json={"title": "Block restore test"},
        )
        mapping_session_id = create_res.json()["session"]["session_id"]

        session = _mapping_session_store.get_session(job_id, mapping_session_id)
        checkpoint = _mapping_session_store.list_checkpoints(job_id, mapping_session_id)[0]
        rerun = _rerun_store.create_rerun(
            session,
            source_checkpoint_id=checkpoint.checkpoint_id,
            mapping_profile=checkpoint.mapping_profile,
        )
        _rerun_store.update_rerun(
            job_id, mapping_session_id, rerun.rerun_id, status=RerunStatus.RUNNING
        )
        _mapping_session_store.update_session(
            job_id, mapping_session_id, active_rerun_id=rerun.rerun_id
        )

        res = client.post(
            f"/api/jobs/{job_id}/mapping-sessions/{mapping_session_id}/restore",
            headers={"Authorization": f"Bearer {token}"},
            json={"checkpoint_id": checkpoint.checkpoint_id},
        )
        assert res.status_code == 409

    def test_rerun_missing_session_returns_404(
        self,
        client: TestClient,
        mapping_job: tuple[str, str],
    ) -> None:
        job_id, session_id = mapping_job
        token = create_access_token(
            SessionIdentity(role=UserRole.JUDGE, judge_session_id=session_id)
        )

        res = client.post(
            f"/api/jobs/{job_id}/mapping-sessions/nonexistent-session/reruns",
            headers={"Authorization": f"Bearer {token}"},
            json={"checkpoint_id": "cp-1"},
        )
        assert res.status_code == 404

    def test_rerun_missing_checkpoint_returns_404(
        self,
        client: TestClient,
        mapping_job: tuple[str, str],
    ) -> None:
        job_id, session_id = mapping_job
        token = create_access_token(
            SessionIdentity(role=UserRole.JUDGE, judge_session_id=session_id)
        )

        create_res = client.post(
            f"/api/jobs/{job_id}/mapping-sessions",
            headers={"Authorization": f"Bearer {token}"},
            json={"title": "Missing CP test"},
        )
        mapping_session_id = create_res.json()["session"]["session_id"]

        res = client.post(
            f"/api/jobs/{job_id}/mapping-sessions/{mapping_session_id}/reruns",
            headers={"Authorization": f"Bearer {token}"},
            json={"checkpoint_id": "nonexistent-checkpoint"},
        )
        assert res.status_code == 404

    def test_rerun_auth_isolation(
        self,
        client: TestClient,
        mapping_job: tuple[str, str],
    ) -> None:
        job_id, _session_owner = mapping_job
        owner_token = create_access_token(
            SessionIdentity(role=UserRole.JUDGE, judge_session_id=_session_owner)
        )
        other_token = create_access_token(
            SessionIdentity(role=UserRole.JUDGE, judge_session_id="other-judge")
        )

        create_res = client.post(
            f"/api/jobs/{job_id}/mapping-sessions",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={"title": "Isolation test"},
        )
        mapping_session_id = create_res.json()["session"]["session_id"]

        res = client.post(
            f"/api/jobs/{job_id}/mapping-sessions/{mapping_session_id}/reruns",
            headers={"Authorization": f"Bearer {other_token}"},
            json={"checkpoint_id": "cp-1"},
        )
        assert res.status_code == 404

    def test_mapping_session_detail_includes_reruns(
        self,
        client: TestClient,
        mapping_job: tuple[str, str],
    ) -> None:
        from backend.routes import _rerun_store

        job_id, session_id = mapping_job
        token = create_access_token(
            SessionIdentity(role=UserRole.JUDGE, judge_session_id=session_id)
        )

        create_res = client.post(
            f"/api/jobs/{job_id}/mapping-sessions",
            headers={"Authorization": f"Bearer {token}"},
            json={"title": "Detail with reruns"},
        )
        mapping_session_id = create_res.json()["session"]["session_id"]

        session = _mapping_session_store.get_session(job_id, mapping_session_id)
        checkpoint = _mapping_session_store.list_checkpoints(job_id, mapping_session_id)[0]
        _rerun_store.create_rerun(
            session,
            source_checkpoint_id=checkpoint.checkpoint_id,
            mapping_profile=checkpoint.mapping_profile,
        )

        res = client.get(
            f"/api/jobs/{job_id}/mapping-sessions/{mapping_session_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        body = res.json()
        assert len(body["reruns"]) == 1
        assert body["reruns"][0]["session_id"] == mapping_session_id
