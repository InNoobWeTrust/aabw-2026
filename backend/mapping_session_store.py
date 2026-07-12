"""Filesystem store for persisted checkpointed mapping sessions.

Each mapping session lives under:
    data/jobs/<job_id>/output/mapping_sessions/<session_id>/
        session.json
        checkpoints/<checkpoint_id>.json
        events.jsonl
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from domain.enums import CheckpointAuthor, MappingSessionStatus
from domain.mapping import MappingProfile
from domain.mapping_session import MappingCheckpoint, MappingSession, MappingSessionEvent


class FileSystemMappingSessionStore:
    """Persist mapping sessions, immutable checkpoints, and SSE events."""

    def __init__(self, jobs_root: Path) -> None:
        self._jobs_root = jobs_root
        self._jobs_root.mkdir(parents=True, exist_ok=True)

    def create_session(
        self,
        job_id: str,
        *,
        title: str | None = None,
    ) -> MappingSession:
        """Create a new mapping session without seeding checkpoints."""
        now = datetime.now(timezone.utc)
        session_id = uuid4().hex
        session = MappingSession(
            session_id=session_id,
            job_id=job_id,
            status=MappingSessionStatus.ACTIVE,
            current_checkpoint_id=None,
            created_at=now,
            updated_at=now,
            title=title,
        )
        self._write_session_json(session)
        self._append_event(
            MappingSessionEvent(
                at=now,
                job_id=job_id,
                session_id=session_id,
                event="status",
                payload={"status": MappingSessionStatus.ACTIVE.value},
            )
        )
        return session

    def get_session(self, job_id: str, session_id: str) -> MappingSession:
        """Read one persisted mapping session."""
        path = self._session_json_path(job_id, session_id)
        return MappingSession(**json.loads(path.read_text("utf-8")))

    def list_sessions(self, job_id: str) -> list[MappingSession]:
        """Return all mapping sessions for a job, newest first."""
        sessions_root = self._sessions_root(job_id)
        sessions: list[MappingSession] = []
        if not sessions_root.exists():
            return sessions
        for path in sorted(sessions_root.glob("*/session.json")):
            sessions.append(MappingSession(**json.loads(path.read_text("utf-8"))))
        sessions.sort(key=lambda s: s.created_at, reverse=True)
        return sessions

    def session_exists(self, job_id: str, session_id: str) -> bool:
        """Return True if a session.json exists for the given session."""
        return self._session_json_path(job_id, session_id).is_file()

    def update_session(self, job_id: str, session_id: str, **changes) -> MappingSession:
        """Atomically apply field updates to a mapping session."""
        session = self.get_session(job_id, session_id)
        for key, value in changes.items():
            setattr(session, key, value)
        session.updated_at = datetime.now(timezone.utc)
        self._write_session_json(session)
        return session

    def archive_session(self, job_id: str, session_id: str) -> MappingSession:
        """Mark a session archived so no new checkpoints can be added."""
        return self.update_session(
            job_id,
            session_id,
            status=MappingSessionStatus.ARCHIVED,
        )

    def create_checkpoint(
        self,
        session_id: str,
        job_id: str,
        *,
        author: CheckpointAuthor,
        mapping_profile: MappingProfile,
        summary: str | None = None,
        parent_checkpoint_id: str | None = None,
        metadata: dict | None = None,
    ) -> MappingCheckpoint:
        """Persist an immutable checkpoint and update the session's current pointer."""
        checkpoint_id = uuid4().hex
        checkpoint = MappingCheckpoint(
            checkpoint_id=checkpoint_id,
            session_id=session_id,
            job_id=job_id,
            author=author,
            mapping_profile=mapping_profile,
            summary=summary,
            parent_checkpoint_id=parent_checkpoint_id,
            created_at=datetime.now(timezone.utc),
            metadata=metadata or {},
        )
        self._write_checkpoint_json(session_id, job_id, checkpoint)
        self.update_session(job_id, session_id, current_checkpoint_id=checkpoint_id)
        self._append_event(
            MappingSessionEvent(
                at=datetime.now(timezone.utc),
                job_id=job_id,
                session_id=session_id,
                event="checkpoint_created",
                payload={
                    "checkpoint_id": checkpoint_id,
                    "author": author.value,
                    "current_checkpoint_id": checkpoint_id,
                },
            )
        )
        return checkpoint

    def get_checkpoint(self, job_id: str, session_id: str, checkpoint_id: str) -> MappingCheckpoint:
        """Read one persisted checkpoint."""
        path = self._checkpoint_json_path(job_id, session_id, checkpoint_id)
        return MappingCheckpoint(**json.loads(path.read_text("utf-8")))

    def list_checkpoints(self, job_id: str, session_id: str) -> list[MappingCheckpoint]:
        """Return all checkpoints in creation order (oldest first)."""
        checkpoints_dir = self._checkpoints_dir(job_id, session_id)
        checkpoints: list[MappingCheckpoint] = []
        if not checkpoints_dir.exists():
            return checkpoints
        for path in sorted(checkpoints_dir.glob("*.json")):
            checkpoints.append(MappingCheckpoint(**json.loads(path.read_text("utf-8"))))
        checkpoints.sort(key=lambda c: c.created_at)
        return checkpoints

    def restore_checkpoint(
        self, job_id: str, session_id: str, checkpoint_id: str
    ) -> MappingSession:
        """Set the given checkpoint as the session's current revision."""
        current = self.get_session(job_id, session_id)
        previous_id = current.current_checkpoint_id
        self.update_session(job_id, session_id, current_checkpoint_id=checkpoint_id)
        self._append_event(
            MappingSessionEvent(
                at=datetime.now(timezone.utc),
                job_id=job_id,
                session_id=session_id,
                event="checkpoint_restored",
                payload={
                    "previous_checkpoint_id": previous_id,
                    "current_checkpoint_id": checkpoint_id,
                },
            )
        )
        return self.get_session(job_id, session_id)

    def list_events(self, job_id: str, session_id: str) -> list[MappingSessionEvent]:
        """Return all persisted events for one session."""
        path = self._events_path(job_id, session_id)
        if not path.exists():
            return []
        events: list[MappingSessionEvent] = []
        for line in path.read_text("utf-8").splitlines():
            if not line.strip():
                continue
            events.append(MappingSessionEvent(**json.loads(line)))
        return events

    def _append_event(self, event: MappingSessionEvent) -> None:
        path = self._events_path(event.job_id, event.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(event.model_dump_json() + "\n")

    def _write_session_json(self, session: MappingSession) -> None:
        path = self._session_json_path(session.job_id, session.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = session.model_dump(mode="json")
        fd, tmp_path = tempfile.mkstemp(suffix=".json", prefix="mapping_session_", dir=path.parent)
        try:
            with open(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False, default=str)
            Path(tmp_path).replace(path)
        except Exception:
            Path(tmp_path).unlink(missing_ok=True)
            raise

    def _write_checkpoint_json(
        self, session_id: str, job_id: str, checkpoint: MappingCheckpoint
    ) -> None:
        path = self._checkpoint_json_path(job_id, session_id, checkpoint.checkpoint_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = checkpoint.model_dump(mode="json")
        fd, tmp_path = tempfile.mkstemp(suffix=".json", prefix="checkpoint_", dir=path.parent)
        try:
            with open(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False, default=str)
            Path(tmp_path).replace(path)
        except Exception:
            Path(tmp_path).unlink(missing_ok=True)
            raise

    def _sessions_root(self, job_id: str) -> Path:
        return self._jobs_root / job_id / "output" / "mapping_sessions"

    def _session_dir(self, job_id: str, session_id: str) -> Path:
        return self._sessions_root(job_id) / session_id

    def _session_json_path(self, job_id: str, session_id: str) -> Path:
        return self._session_dir(job_id, session_id) / "session.json"

    def _checkpoints_dir(self, job_id: str, session_id: str) -> Path:
        return self._session_dir(job_id, session_id) / "checkpoints"

    def _checkpoint_json_path(self, job_id: str, session_id: str, checkpoint_id: str) -> Path:
        return self._checkpoints_dir(job_id, session_id) / f"{checkpoint_id}.json"

    def _events_path(self, job_id: str, session_id: str) -> Path:
        return self._session_dir(job_id, session_id) / "events.jsonl"
