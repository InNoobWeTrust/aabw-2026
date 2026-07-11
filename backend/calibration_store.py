"""Filesystem store for asynchronous mapping calibration sub-jobs.

Calibration data lives under:
    data/jobs/<job_id>/output/calibration/
        calibration.json
        decision.json
        events.jsonl
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from domain.calibration import CalibrationEvent, CalibrationSnapshot
from domain.enums import CalibrationStatus


class FileSystemCalibrationStore:
    """Persist mapping calibration snapshots, decisions, and events under a job output tree."""

    def __init__(self, jobs_root: Path) -> None:
        self._jobs_root = jobs_root
        self._jobs_root.mkdir(parents=True, exist_ok=True)

    def create_calibration(
        self,
        job_id: str,
        *,
        provider: str,
        sandbox: str,
        context_manifest: dict | None = None,
        metadata: dict | None = None,
    ) -> CalibrationSnapshot:
        """Create or replace a calibration snapshot in PENDING state."""
        snapshot = CalibrationSnapshot(
            job_id=job_id,
            status=CalibrationStatus.PENDING,
            provider=provider,
            sandbox=sandbox,
            context_manifest=context_manifest or {},
            metadata=metadata or {},
        )
        self.write_snapshot(snapshot)
        self.append_event(
            CalibrationEvent(
                at=datetime.now(timezone.utc),
                job_id=job_id,
                event="status",
                payload={"status": CalibrationStatus.PENDING.value},
            )
        )
        return snapshot

    def calibration_exists(self, job_id: str) -> bool:
        """Return True when the calibration snapshot exists on disk."""
        return self._calibration_json_path(job_id).is_file()

    def get_calibration(self, job_id: str) -> CalibrationSnapshot:
        """Read a persisted calibration snapshot."""
        return CalibrationSnapshot(
            **json.loads(self._calibration_json_path(job_id).read_text("utf-8"))
        )

    def write_snapshot(self, snapshot: CalibrationSnapshot) -> None:
        """Atomically persist calibration.json."""
        json_path = self._calibration_json_path(snapshot.job_id)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        payload = snapshot.model_dump(mode="json")
        fd, tmp_path = tempfile.mkstemp(
            suffix=".json",
            prefix="calibration_",
            dir=json_path.parent,
        )
        try:
            with open(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False, default=str)
            Path(tmp_path).replace(json_path)
        except Exception:
            Path(tmp_path).unlink(missing_ok=True)
            raise

    def update_calibration(self, job_id: str, **changes) -> CalibrationSnapshot:
        """Apply field updates to a calibration snapshot and persist it."""
        snapshot = self.get_calibration(job_id)
        for key, value in changes.items():
            setattr(snapshot, key, value)
        self.write_snapshot(snapshot)
        return snapshot

    def write_decision_payload(self, job_id: str, payload: dict) -> Path:
        """Persist the structured calibration decision payload and return its path."""
        path = self._decision_path(job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def append_event(self, event: CalibrationEvent) -> None:
        """Append one JSONL event line for SSE replay."""
        path = self._events_path(event.job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(event.model_dump_json() + "\n")

    def list_events(self, job_id: str) -> list[CalibrationEvent]:
        """Return all persisted events for one calibration run."""
        path = self._events_path(job_id)
        if not path.exists():
            return []
        events: list[CalibrationEvent] = []
        for line in path.read_text("utf-8").splitlines():
            if not line.strip():
                continue
            events.append(CalibrationEvent(**json.loads(line)))
        return events

    def _calibration_dir(self, job_id: str) -> Path:
        return self._jobs_root / job_id / "output" / "calibration"

    def _calibration_json_path(self, job_id: str) -> Path:
        return self._calibration_dir(job_id) / "calibration.json"

    def _decision_path(self, job_id: str) -> Path:
        return self._calibration_dir(job_id) / "decision.json"

    def _events_path(self, job_id: str) -> Path:
        return self._calibration_dir(job_id) / "events.jsonl"
