"""Filesystem store for versioned checkpoint-triggered pipeline reruns.

Each rerun lives under the session tree:
    data/jobs/<job_id>/output/mapping_sessions/<session_id>/reruns/<version>_<rerun_id>/
        rerun.json
        mapping_profile.json
        artifacts.json
"""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from domain.enums import RerunStatus
from domain.mapping import MappingProfile
from domain.mapping_session import MappingSession, RerunArtifactManifest, RerunRecord


class FileSystemCheckpointRerunStore:
    """Persist versioned reruns under mapping session directories."""

    def __init__(self, jobs_root: Path) -> None:
        self._jobs_root = jobs_root
        self._jobs_root.mkdir(parents=True, exist_ok=True)

    def create_rerun(
        self,
        session: MappingSession,
        *,
        source_checkpoint_id: str,
        mapping_profile: MappingProfile,
        metadata: dict[str, Any] | None = None,
    ) -> RerunRecord:
        """Create a new versioned rerun record with the next monotonic version number.

        The version increments from the session's ``latest_rerun_id``, or starts at 1
        for the first rerun. Returns the persisted :class:`RerunRecord`.
        """
        version = self._next_version(session)
        rerun_id = uuid4().hex
        now = datetime.now(timezone.utc)
        artifact_manifest = RerunArtifactManifest(
            rerun_id=rerun_id,
            session_id=session.session_id,
            job_id=session.job_id,
            version=version,
        )
        record = RerunRecord(
            rerun_id=rerun_id,
            version=version,
            job_id=session.job_id,
            session_id=session.session_id,
            source_checkpoint_id=source_checkpoint_id,
            status=RerunStatus.PENDING,
            mapping_profile=mapping_profile,
            artifact_manifest=artifact_manifest,
            created_at=now,
            updated_at=now,
            metadata=metadata or {},
        )
        self._write_rerun_json(record)
        self._write_mapping_profile_json(record)
        self._write_artifacts_json(record)
        return record

    def get_rerun(
        self, job_id: str, session_id: str, rerun_id: str
    ) -> RerunRecord:
        """Read one persisted rerun record."""
        path = self._rerun_json_path(job_id, session_id, rerun_id)
        return RerunRecord(**json.loads(path.read_text("utf-8")))

    def list_reruns(
        self, job_id: str, session_id: str
    ) -> list[RerunRecord]:
        """Return all reruns for a session, newest first."""
        reruns_root = self._reruns_root(job_id, session_id)
        records: list[RerunRecord] = []
        if not reruns_root.exists():
            return records
        for path in sorted(reruns_root.glob("*/rerun.json")):
            records.append(RerunRecord(**json.loads(path.read_text("utf-8"))))
        records.sort(key=lambda r: r.version, reverse=True)
        return records

    def update_rerun(
        self, job_id: str, session_id: str, rerun_id: str, **changes: Any
    ) -> RerunRecord:
        """Atomically apply field updates to a rerun record."""
        record = self.get_rerun(job_id, session_id, rerun_id)
        for key, value in changes.items():
            setattr(record, key, value)
        record.updated_at = datetime.now(timezone.utc)
        self._write_rerun_json(record)
        return record

    def write_artifacts(
        self,
        record: RerunRecord,
        artifacts: dict[str, Any],
    ) -> RerunRecord:
        """Persist an artifact manifest update for a rerun."""
        manifest = record.artifact_manifest or RerunArtifactManifest(
            rerun_id=record.rerun_id,
            session_id=record.session_id,
            job_id=record.job_id,
            version=record.version,
        )
        manifest.artifacts = artifacts
        manifest.updated_at = datetime.now(timezone.utc)
        record.artifact_manifest = manifest
        record.updated_at = datetime.now(timezone.utc)
        self._write_artifacts_json(record)
        self._write_rerun_json(record)
        return record

    def _next_version(self, session: MappingSession) -> int:
        """Determine the next monotonic version for a new rerun in this session."""
        reruns = self.list_reruns(session.job_id, session.session_id)
        if not reruns:
            return 1
        return reruns[0].version + 1

    def _rerun_dir(
        self, job_id: str, session_id: str, rerun_id: str, version: int | None = None
    ) -> Path:
        if version is not None:
            return self._reruns_root(job_id, session_id) / f"{version}_{rerun_id}"
        return self._find_rerun_dir(job_id, session_id, rerun_id)

    def _reruns_root(self, job_id: str, session_id: str) -> Path:
        return (
            self._jobs_root
            / job_id
            / "output"
            / "mapping_sessions"
            / session_id
            / "reruns"
        )

    def _rerun_json_path(
        self, job_id: str, session_id: str, rerun_id: str, version: int | None = None
    ) -> Path:
        return self._rerun_dir(job_id, session_id, rerun_id, version) / "rerun.json"

    def _mapping_profile_path(
        self, job_id: str, session_id: str, rerun_id: str, version: int | None = None
    ) -> Path:
        return (
            self._rerun_dir(job_id, session_id, rerun_id, version)
            / "mapping_profile.json"
        )

    def _artifacts_path(
        self, job_id: str, session_id: str, rerun_id: str, version: int | None = None
    ) -> Path:
        return (
            self._rerun_dir(job_id, session_id, rerun_id, version) / "artifacts.json"
        )

    def _write_rerun_json(self, record: RerunRecord) -> None:
        path = self._rerun_json_path(
            record.job_id, record.session_id, record.rerun_id, record.version
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(path, record.model_dump(mode="json"))

    def _write_mapping_profile_json(self, record: RerunRecord) -> None:
        path = self._mapping_profile_path(
            record.job_id, record.session_id, record.rerun_id, record.version
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(path, record.mapping_profile.model_dump(mode="json"))

    def _write_artifacts_json(self, record: RerunRecord) -> None:
        if record.artifact_manifest is None:
            return
        path = self._artifacts_path(
            record.job_id, record.session_id, record.rerun_id, record.version
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(path, record.artifact_manifest.model_dump(mode="json"))


    def _find_rerun_dir(self, job_id: str, session_id: str, rerun_id: str) -> Path:
        root = self._reruns_root(job_id, session_id)
        candidates = list(root.glob(f"*_{rerun_id}")) if root.exists() else []
        if len(candidates) == 1:
            return candidates[0]
        if not candidates:
            return root / rerun_id
        raise FileNotFoundError(
            f"Multiple rerun directories found for {job_id}/{session_id}/{rerun_id}"
        )


def _atomic_write_json(path: Path, payload: Any) -> None:
    """Write a JSON payload atomically via tempfile + rename."""
    fd, tmp_path = tempfile.mkstemp(
        suffix=".json", prefix="rerun_", dir=path.parent
    )
    try:
        with open(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False, default=str)
        Path(tmp_path).replace(path)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise
