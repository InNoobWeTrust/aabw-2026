"""Mapping session service with checkpoint lifecycle management.

Seeds a baseline checkpoint from the job's current mapping profile and exposes
bounded operations: create session, add checkpoint, restore checkpoint, archive.
"""

from __future__ import annotations

from backend.job_store import FileSystemJobStore
from backend.mapping_session_store import FileSystemMappingSessionStore
from domain.enums import CheckpointAuthor, MappingSessionStatus
from domain.mapping import MappingProfile
from domain.mapping_session import MappingCheckpoint, MappingSession


class MappingSessionService:
    """Orchestrate checkpoint lifecycle for a completed job."""

    def __init__(
        self,
        session_store: FileSystemMappingSessionStore,
        job_store: FileSystemJobStore,
    ) -> None:
        self._session_store = session_store
        self._job_store = job_store

    def create_session(
        self, job_id: str, *, title: str | None = None
    ) -> tuple[MappingSession, MappingCheckpoint]:
        """Create a new mapping session seeded with a baseline checkpoint."""
        session = self._session_store.create_session(job_id, title=title)
        baseline_profile = self._current_job_mapping_profile(job_id)
        checkpoint = self._session_store.create_checkpoint(
            session_id=session.session_id,
            job_id=job_id,
            author=CheckpointAuthor.BASELINE,
            mapping_profile=baseline_profile,
            summary="Baseline checkpoint seeded from the job's current mapping profile.",
            parent_checkpoint_id=None,
        )
        return session, checkpoint

    def add_checkpoint(
        self,
        job_id: str,
        session_id: str,
        *,
        author: CheckpointAuthor,
        mapping_profile: MappingProfile,
        summary: str | None = None,
        metadata: dict | None = None,
    ) -> MappingCheckpoint:
        """Append a new immutable checkpoint to an active session."""
        session = self._session_store.get_session(job_id, session_id)
        if session.status == MappingSessionStatus.ARCHIVED:
            raise ValueError(
                f"Session {session_id} is archived and does not accept new checkpoints"
            )
        parent_id = session.current_checkpoint_id
        return self._session_store.create_checkpoint(
            session_id=session_id,
            job_id=job_id,
            author=author,
            mapping_profile=mapping_profile,
            summary=summary,
            parent_checkpoint_id=parent_id,
            metadata=metadata,
        )

    def restore_checkpoint(
        self, job_id: str, session_id: str, checkpoint_id: str
    ) -> MappingSession:
        """Restore a previously created checkpoint as the current revision."""
        return self._session_store.restore_checkpoint(job_id, session_id, checkpoint_id)

    def archive_session(self, job_id: str, session_id: str) -> MappingSession:
        """Archive a session so no further edits can be made."""
        return self._session_store.archive_session(job_id, session_id)

    def active_rerun_blocked(self, job_id: str, session_id: str) -> str | None:
        """Return a human-readable reason if rerun cannot be triggered, or None if it can.

        A rerun is blocked when the session already has an active (queued or
        running) rerun. This prevents concurrent re-execution over the same
        session state.
        """
        session = self._session_store.get_session(job_id, session_id)
        if session.status == MappingSessionStatus.ARCHIVED:
            return f"Session {session_id} is archived and does not accept new reruns"
        if session.status == MappingSessionStatus.FAILED:
            return f"Session {session_id} has failed and does not accept new reruns"
        if session.active_rerun_id is None:
            return None
        from backend.checkpoint_rerun_store import FileSystemCheckpointRerunStore

        rerun_store = FileSystemCheckpointRerunStore(self._session_store._jobs_root)
        try:
            active_rerun = rerun_store.get_rerun(job_id, session_id, session.active_rerun_id)
        except FileNotFoundError:
            return None
        if active_rerun.status.is_active():
            return (
                f"Rerun {active_rerun.rerun_id} (v{active_rerun.version}) "
                f"is still {active_rerun.status.value}"
            )
        return None

    def _current_job_mapping_profile(self, job_id: str) -> MappingProfile:
        """Extract the job's current mapping profile or return a default."""
        snapshot = self._job_store.get_job(job_id)
        result = snapshot.result or {}
        retarget = result.get("retarget", {})
        profile_data = retarget.get("mapping_profile")
        if profile_data is not None and isinstance(profile_data, dict):
            return MappingProfile(**profile_data)
        return MappingProfile()
