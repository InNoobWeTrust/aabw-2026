"""Async review orchestration with persisted SSE-friendly event streams.

This module provides a hackathon-friendly review service that is designed around
Featherless + Daytona configuration, while falling back to deterministic local
review generation when those credentials are not configured. The fallback keeps
review UX/test flows functional without making the main pipeline depend on an
external provider being available.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from backend.config import settings
from backend.review_store import FileSystemReviewStore
from domain.enums import ReviewStage, ReviewStatus, ReviewVerdict
from domain.reviews import ReviewEvent
from pipeline.staged_review import generate_ai_review, generate_pose_review

_logger = logging.getLogger(__name__)


class ReviewService:
    """Manage asynchronous stage reviews and their persisted event streams."""

    def __init__(self, review_store: FileSystemReviewStore) -> None:
        self._review_store = review_store
        self._tasks: dict[tuple[str, str], asyncio.Task[None]] = {}

    def schedule_review(
        self,
        *,
        job_id: str,
        stage: ReviewStage,
        context_manifest: dict[str, Any],
        review_factory: Callable[[], tuple[str, dict[str, Any]]],
    ) -> None:
        """Schedule one review stage if it is not already running."""
        key = (job_id, stage.value)
        if key in self._tasks and not self._tasks[key].done():
            return

        provider = settings.review_provider_name
        sandbox = settings.review_sandbox_name
        self._review_store.create_review(
            job_id,
            stage,
            provider=provider,
            sandbox=sandbox,
            context_manifest=context_manifest,
            metadata={"execution_mode": settings.review_execution_mode},
        )
        task = asyncio.create_task(self._run_review(job_id, stage, review_factory))
        self._tasks[key] = task

    async def _run_review(
        self,
        job_id: str,
        stage: ReviewStage,
        review_factory: Callable[[], tuple[str, dict[str, Any]]],
    ) -> None:
        """Execute a review, persist its markdown/json outputs, and stream events."""
        started_at = datetime.now(timezone.utc)
        self._review_store.update_review(
            job_id,
            stage,
            status=ReviewStatus.RUNNING,
            started_at=started_at,
            error=None,
        )
        self._emit(job_id, stage, "status", {"status": ReviewStatus.RUNNING.value})
        try:
            markdown, payload = await asyncio.to_thread(review_factory)
            self._emit(job_id, stage, "section", {"name": "summary"})
            for chunk in _chunk_text(markdown, settings.review_stream_chunk_chars):
                self._emit(job_id, stage, "token", {"text": chunk})

            md_path = self._review_store.write_markdown(job_id, stage, markdown)
            json_path = self._review_store.write_json_payload(job_id, stage, payload)
            verdict = payload.get("verdict")
            summary = payload.get("summary")

            self._review_store.update_review(
                job_id,
                stage,
                status=ReviewStatus.COMPLETED,
                completed_at=datetime.now(timezone.utc),
                verdict=ReviewVerdict(verdict) if verdict else None,
                summary=summary,
                markdown_path=str(md_path),
                json_path=str(json_path),
                metadata={
                    "execution_mode": settings.review_execution_mode,
                    "model": settings.review_model_name,
                },
            )
            self._emit(job_id, stage, "result", payload)
            self._emit(job_id, stage, "done", {"status": ReviewStatus.COMPLETED.value})
        except Exception as exc:  # pragma: no cover - defensive async guard
            _logger.exception("Review %s failed for job %s", stage.value, job_id)
            self._review_store.update_review(
                job_id,
                stage,
                status=ReviewStatus.FAILED,
                completed_at=datetime.now(timezone.utc),
                error=str(exc),
            )
            self._emit(job_id, stage, "error", {"detail": str(exc)})
            self._emit(job_id, stage, "done", {"status": ReviewStatus.FAILED.value})

    def _emit(self, job_id: str, stage: ReviewStage, event: str, payload: dict[str, Any]) -> None:
        self._review_store.append_event(
            ReviewEvent(
                at=datetime.now(timezone.utc),
                job_id=job_id,
                review_stage=stage,
                event=event,
                payload=payload,
            )
        )


def build_pose_review_factory(
    *,
    metrics: dict[str, Any],
    artifact_manifest: dict[str, Any],
) -> Callable[[], tuple[str, dict[str, Any]]]:
    """Return a callable that produces a pose-stage review payload."""

    def _factory() -> tuple[str, dict[str, Any]]:
        markdown, payload = generate_pose_review(metrics, artifact_manifest)
        return markdown, payload

    return _factory


def build_retarget_review_factory(
    *,
    eval_result: dict[str, Any],
    joint_trajectory,
    artifact_manifest: dict[str, Any],
    pose_review_summary: dict[str, Any] | None = None,
) -> Callable[[], tuple[str, dict[str, Any]]]:
    """Return a callable that produces a retarget-stage review payload."""

    def _factory() -> tuple[str, dict[str, Any]]:
        markdown = generate_ai_review(eval_result, joint_trajectory)
        verdict = _retarget_verdict(eval_result)
        summary = (
            f"Retarget review verdict: {verdict.value.replace('_', ' ')} based on "
            f"overall grade {eval_result.get('overall_grade', 'unknown')}."
        )
        payload = {
            "stage": ReviewStage.RETARGET.value,
            "verdict": verdict.value,
            "summary": summary,
            "artifact_manifest": artifact_manifest,
            "pose_review_summary": pose_review_summary,
            "metrics": {
                "joint_limit_violations": eval_result.get("joint_limit_violations", 0),
                "nan_count": eval_result.get("nan_count", 0),
                "max_velocity": eval_result.get("max_velocity", 0.0),
                "mean_jerk": eval_result.get("mean_jerk", 0.0),
                "sudden_jump_count": eval_result.get("sudden_jump_count", 0),
                "completeness_ratio": eval_result.get("completeness_ratio", 0.0),
            },
            "markdown": markdown,
        }
        return markdown, payload

    return _factory


def _retarget_verdict(eval_result: dict[str, Any]) -> ReviewVerdict:
    grade = str(eval_result.get("overall_grade", "red"))
    if grade == "green":
        return ReviewVerdict.APPROVED
    if grade == "yellow":
        return ReviewVerdict.NEEDS_REVIEW
    return ReviewVerdict.USABLE_SKELETON_ONLY


def _chunk_text(text: str, width: int) -> list[str]:
    if width <= 0:
        return [text]
    return [text[i : i + width] for i in range(0, len(text), width)] or [""]
