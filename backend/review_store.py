"""Filesystem review store for asynchronous job-attached stage reviews.

Review data lives under:
    data/jobs/<job_id>/output/reviews/<stage>/
        review.json
        review.md
        events.jsonl

The store persists both snapshot state and append-only event streams so SSE
clients can reconnect and replay prior tokens or section updates.
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from domain.enums import ReviewStage, ReviewStatus
from domain.reviews import ReviewEvent, ReviewSnapshot


class FileSystemReviewStore:
    """Persist review snapshots and events under a job's output directory."""

    def __init__(self, jobs_root: Path) -> None:
        self._jobs_root = jobs_root
        self._jobs_root.mkdir(parents=True, exist_ok=True)

    def create_review(
        self,
        job_id: str,
        stage: ReviewStage,
        *,
        provider: str,
        sandbox: str,
        context_manifest: dict | None = None,
        metadata: dict | None = None,
    ) -> ReviewSnapshot:
        """Create or replace a review snapshot in PENDING state."""
        snapshot = ReviewSnapshot(
            job_id=job_id,
            review_stage=stage,
            status=ReviewStatus.PENDING,
            provider=provider,
            sandbox=sandbox,
            context_manifest=context_manifest or {},
            metadata=metadata or {},
        )
        self.write_snapshot(snapshot)
        self.append_event(
            ReviewEvent(
                at=datetime.now(timezone.utc),
                job_id=job_id,
                review_stage=stage,
                event="status",
                payload={"status": ReviewStatus.PENDING.value},
            )
        )
        return snapshot

    def get_review(self, job_id: str, stage: ReviewStage) -> ReviewSnapshot:
        """Read a persisted review snapshot."""
        return ReviewSnapshot(
            **json.loads(self._review_json_path(job_id, stage).read_text("utf-8"))
        )

    def review_exists(self, job_id: str, stage: ReviewStage) -> bool:
        """Return True when the stage snapshot exists on disk."""
        return self._review_json_path(job_id, stage).is_file()

    def write_snapshot(self, snapshot: ReviewSnapshot) -> None:
        """Atomically persist review.json."""
        json_path = self._review_json_path(snapshot.job_id, snapshot.review_stage)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        payload = snapshot.model_dump(mode="json")
        fd, tmp_path = tempfile.mkstemp(suffix=".json", prefix="review_", dir=json_path.parent)
        try:
            with open(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False, default=str)
            Path(tmp_path).replace(json_path)
        except Exception:
            Path(tmp_path).unlink(missing_ok=True)
            raise

    def update_review(self, job_id: str, stage: ReviewStage, **changes) -> ReviewSnapshot:
        """Apply field updates to a review snapshot and persist it."""
        snapshot = self.get_review(job_id, stage)
        for key, value in changes.items():
            setattr(snapshot, key, value)
        self.write_snapshot(snapshot)
        return snapshot

    def append_event(self, event: ReviewEvent) -> None:
        """Append one JSONL event line for SSE replay."""
        path = self._events_path(event.job_id, event.review_stage)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(event.model_dump_json() + "\n")

    def list_events(self, job_id: str, stage: ReviewStage) -> list[ReviewEvent]:
        """Return all persisted events for one review stage."""
        path = self._events_path(job_id, stage)
        if not path.exists():
            return []
        events: list[ReviewEvent] = []
        for line in path.read_text("utf-8").splitlines():
            if not line.strip():
                continue
            events.append(ReviewEvent(**json.loads(line)))
        return events

    def write_markdown(self, job_id: str, stage: ReviewStage, markdown: str) -> Path:
        """Persist final markdown artifact and return its path."""
        path = self._review_md_path(job_id, stage)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8")
        return path

    def write_json_payload(self, job_id: str, stage: ReviewStage, payload: dict) -> Path:
        """Persist final structured review payload and return its path."""
        path = self._review_payload_path(job_id, stage)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def _review_dir(self, job_id: str, stage: ReviewStage) -> Path:
        return self._jobs_root / job_id / "output" / "reviews" / stage.value

    def _review_json_path(self, job_id: str, stage: ReviewStage) -> Path:
        return self._review_dir(job_id, stage) / "review.json"

    def _review_md_path(self, job_id: str, stage: ReviewStage) -> Path:
        return self._review_dir(job_id, stage) / "review.md"

    def _review_payload_path(self, job_id: str, stage: ReviewStage) -> Path:
        return self._review_dir(job_id, stage) / "payload.json"

    def _events_path(self, job_id: str, stage: ReviewStage) -> Path:
        return self._review_dir(job_id, stage) / "events.jsonl"
