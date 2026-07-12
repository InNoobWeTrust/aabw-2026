"""Filesystem store for adaptive orchestration sub-jobs.

Orchestration data lives under:
    data/jobs/<job_id>/output/orchestration/
        orchestration.json
        decision.json
        capture_guidance.json
        events.jsonl
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from domain.enums import OrchestrationStatus
from domain.orchestration import OrchestrationEvent, OrchestrationSnapshot


class FileSystemOrchestrationStore:
    """Persist orchestration snapshots, decisions, and events under a job output tree."""

    def __init__(self, jobs_root: Path) -> None:
        self._jobs_root = jobs_root
        self._jobs_root.mkdir(parents=True, exist_ok=True)

    def create_orchestration(
        self,
        job_id: str,
        *,
        provider: str,
        sandbox: str,
        evidence_manifest: dict | None = None,
        metadata: dict | None = None,
    ) -> OrchestrationSnapshot:
        """Create or replace an orchestration snapshot in pending state."""
        self._reset_run_artifacts(job_id)
        snapshot = OrchestrationSnapshot(
            job_id=job_id,
            status=OrchestrationStatus.PENDING,
            provider=provider,
            sandbox=sandbox,
            evidence_manifest=evidence_manifest or {},
            metadata=metadata or {},
        )
        self.write_snapshot(snapshot)
        self.append_event(
            OrchestrationEvent(
                at=datetime.now(timezone.utc),
                job_id=job_id,
                event="status",
                payload={"status": OrchestrationStatus.PENDING.value},
            )
        )
        return snapshot

    def orchestration_exists(self, job_id: str) -> bool:
        """Return True when the orchestration snapshot exists on disk."""
        return self._orchestration_json_path(job_id).is_file()

    def get_orchestration(self, job_id: str) -> OrchestrationSnapshot:
        """Read a persisted orchestration snapshot."""
        return OrchestrationSnapshot(
            **json.loads(self._orchestration_json_path(job_id).read_text("utf-8"))
        )

    def write_snapshot(self, snapshot: OrchestrationSnapshot) -> None:
        """Atomically persist orchestration.json."""
        json_path = self._orchestration_json_path(snapshot.job_id)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        payload = snapshot.model_dump(mode="json")
        fd, tmp_path = tempfile.mkstemp(
            suffix=".json",
            prefix="orchestration_",
            dir=json_path.parent,
        )
        try:
            with open(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False, default=str)
            Path(tmp_path).replace(json_path)
        except Exception:
            Path(tmp_path).unlink(missing_ok=True)
            raise

    def update_orchestration(self, job_id: str, **changes) -> OrchestrationSnapshot:
        """Apply field updates to an orchestration snapshot and persist it."""
        snapshot = self.get_orchestration(job_id)
        for key, value in changes.items():
            setattr(snapshot, key, value)
        self.write_snapshot(snapshot)
        return snapshot

    def write_decision_payload(self, job_id: str, payload: dict) -> Path:
        """Persist the structured orchestration decision payload and return its path."""
        path = self._decision_path(job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def write_capture_guidance(self, job_id: str, guidance: dict) -> Path:
        """Persist capture guidance for retry-capture decisions and return its path."""
        path = self._capture_guidance_path(job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(guidance, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def append_event(self, event: OrchestrationEvent) -> None:
        """Append one JSONL event line for SSE replay."""
        path = self._events_path(event.job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(event.model_dump_json() + "\n")

    def list_events(self, job_id: str) -> list[OrchestrationEvent]:
        """Return all persisted events for one orchestration run."""
        path = self._events_path(job_id)
        if not path.exists():
            return []
        events: list[OrchestrationEvent] = []
        for line in path.read_text("utf-8").splitlines():
            if not line.strip():
                continue
            events.append(OrchestrationEvent(**json.loads(line)))
        return events

    def _reset_run_artifacts(self, job_id: str) -> None:
        """Remove per-run files so a new orchestration starts with a clean event log."""
        for path in (
            self._events_path(job_id),
            self._decision_path(job_id),
            self._capture_guidance_path(job_id),
        ):
            path.unlink(missing_ok=True)

    def _orchestration_dir(self, job_id: str) -> Path:
        return self._jobs_root / job_id / "output" / "orchestration"

    def _orchestration_json_path(self, job_id: str) -> Path:
        return self._orchestration_dir(job_id) / "orchestration.json"

    def _decision_path(self, job_id: str) -> Path:
        return self._orchestration_dir(job_id) / "decision.json"

    def _capture_guidance_path(self, job_id: str) -> Path:
        return self._orchestration_dir(job_id) / "capture_guidance.json"

    def _events_path(self, job_id: str) -> Path:
        return self._orchestration_dir(job_id) / "events.jsonl"
