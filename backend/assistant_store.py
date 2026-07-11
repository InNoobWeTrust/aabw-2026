"""Filesystem store for persisted reviewer-assistant chat sessions.

Each session lives under:
    data/jobs/<job_id>/output/assistant_sessions/<session_id>/
        session.json
        messages.jsonl
        events.jsonl
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from domain.enums import AssistantSessionStatus
from domain.reviews import AssistantEvent, AssistantMessage, AssistantSessionSnapshot


class FileSystemAssistantStore:
    """Persist assistant sessions, transcript messages, and SSE events."""

    def __init__(self, jobs_root: Path) -> None:
        self._jobs_root = jobs_root
        self._jobs_root.mkdir(parents=True, exist_ok=True)

    def create_session(
        self,
        job_id: str,
        *,
        provider: str,
        sandbox: str,
        title: str | None = None,
        metadata: dict | None = None,
    ) -> AssistantSessionSnapshot:
        """Create and persist a new assistant session."""
        now = datetime.now(timezone.utc)
        session_id = uuid4().hex
        snapshot = AssistantSessionSnapshot(
            job_id=job_id,
            session_id=session_id,
            status=AssistantSessionStatus.IDLE,
            provider=provider,
            sandbox=sandbox,
            created_at=now,
            updated_at=now,
            title=title,
            metadata=metadata or {},
        )
        self.write_snapshot(snapshot)
        self.append_event(
            AssistantEvent(
                at=now,
                job_id=job_id,
                session_id=session_id,
                event="status",
                payload={"status": AssistantSessionStatus.IDLE.value},
            )
        )
        return snapshot

    def get_session(self, job_id: str, session_id: str) -> AssistantSessionSnapshot:
        """Read one persisted assistant session snapshot."""
        path = self._session_json_path(job_id, session_id)
        return AssistantSessionSnapshot(**json.loads(path.read_text("utf-8")))

    def list_sessions(self, job_id: str) -> list[AssistantSessionSnapshot]:
        """Return all persisted assistant sessions for a job, newest first."""
        root = self._sessions_root(job_id)
        sessions: list[AssistantSessionSnapshot] = []
        if not root.exists():
            return sessions
        for path in sorted(root.glob("*/session.json")):
            sessions.append(AssistantSessionSnapshot(**json.loads(path.read_text("utf-8"))))
        sessions.sort(key=lambda s: s.created_at, reverse=True)
        return sessions

    def write_snapshot(self, snapshot: AssistantSessionSnapshot) -> None:
        """Atomically persist session.json."""
        path = self._session_json_path(snapshot.job_id, snapshot.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = snapshot.model_dump(mode="json")
        fd, tmp_path = tempfile.mkstemp(suffix=".json", prefix="assistant_", dir=path.parent)
        try:
            with open(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False, default=str)
            Path(tmp_path).replace(path)
        except Exception:
            Path(tmp_path).unlink(missing_ok=True)
            raise

    def update_session(self, job_id: str, session_id: str, **changes) -> AssistantSessionSnapshot:
        """Update and persist fields on a session snapshot."""
        snapshot = self.get_session(job_id, session_id)
        for key, value in changes.items():
            setattr(snapshot, key, value)
        snapshot.updated_at = datetime.now(timezone.utc)
        self.write_snapshot(snapshot)
        return snapshot

    def append_message(self, message: AssistantMessage) -> None:
        """Append one transcript message to messages.jsonl."""
        path = self._messages_path(message.job_id, message.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(message.model_dump_json() + "\n")

    def list_messages(self, job_id: str, session_id: str) -> list[AssistantMessage]:
        """Return all transcript messages for a session."""
        path = self._messages_path(job_id, session_id)
        if not path.exists():
            return []
        messages: list[AssistantMessage] = []
        for line in path.read_text("utf-8").splitlines():
            if not line.strip():
                continue
            messages.append(AssistantMessage(**json.loads(line)))
        return messages

    def append_event(self, event: AssistantEvent) -> None:
        """Append one assistant SSE event for replay."""
        path = self._events_path(event.job_id, event.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(event.model_dump_json() + "\n")

    def list_events(self, job_id: str, session_id: str) -> list[AssistantEvent]:
        """Return all persisted assistant events for one session."""
        path = self._events_path(job_id, session_id)
        if not path.exists():
            return []
        events: list[AssistantEvent] = []
        for line in path.read_text("utf-8").splitlines():
            if not line.strip():
                continue
            events.append(AssistantEvent(**json.loads(line)))
        return events

    def _sessions_root(self, job_id: str) -> Path:
        return self._jobs_root / job_id / "output" / "assistant_sessions"

    def _session_dir(self, job_id: str, session_id: str) -> Path:
        return self._sessions_root(job_id) / session_id

    def _session_json_path(self, job_id: str, session_id: str) -> Path:
        return self._session_dir(job_id, session_id) / "session.json"

    def _messages_path(self, job_id: str, session_id: str) -> Path:
        return self._session_dir(job_id, session_id) / "messages.jsonl"

    def _events_path(self, job_id: str, session_id: str) -> Path:
        return self._session_dir(job_id, session_id) / "events.jsonl"
