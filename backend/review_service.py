"""Async review orchestration with persisted SSE-friendly event streams.

This module prefers a generic OpenAI-compatible provider when credentials are
configured, while falling back to deterministic local review generation when no
provider is available. The fallback keeps review UX/test flows functional
without making the main pipeline depend on external services.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import httpx

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
        review_factory_builder: Callable[
            [Callable[[str, dict[str, Any]], None]],
            Callable[[], tuple[str, dict[str, Any]]],
        ],
    ) -> None:
        """Schedule one review stage if it is not already running.

        Args:
            review_factory_builder: ``(emit) -> factory`` callable. The service
                calls this with an emit hook that forwards events through
                ``self._emit`` (so they are persisted and replayed by the SSE
                endpoint), and uses the returned factory as the actual work.
        """
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
        task = asyncio.create_task(
            self._run_review(job_id, stage, context_manifest, review_factory_builder)
        )
        self._tasks[key] = task

    async def _run_review(
        self,
        job_id: str,
        stage: ReviewStage,
        context_manifest: dict[str, Any],
        review_factory_builder: Callable[
            [Callable[[str, dict[str, Any]], None]],
            Callable[[], tuple[str, dict[str, Any]]],
        ],
    ) -> None:
        """Execute a review, persist its markdown/json outputs, and stream events.

        The builder is given an ``emit`` hook that forwards events through
        ``self._emit`` so the review store and SSE endpoint see them. The
        factory itself runs inside ``asyncio.to_thread`` (CPU-bound local
        review work); LLM-driven reviews run in ``_run_external_review``.
        """
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
            if settings.review_execution_mode == "openai_compatible":
                try:
                    markdown, payload = await self._run_external_review(
                        job_id, stage, context_manifest
                    )
                except Exception as exc:
                    _logger.warning(
                        "External review failed for %s on job %s; falling back to local review: %s",
                        stage.value,
                        job_id,
                        exc,
                    )

                    def _emit_factory(event: str, event_payload: dict[str, Any]) -> None:
                        self._emit(job_id, stage, event, event_payload)

                    review_factory = review_factory_builder(_emit_factory)
                    markdown, payload = await asyncio.to_thread(review_factory)
            else:

                def _emit_factory(event: str, event_payload: dict[str, Any]) -> None:
                    self._emit(job_id, stage, event, event_payload)

                review_factory = review_factory_builder(_emit_factory)
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

    async def _run_external_review(
        self,
        job_id: str,
        stage: ReviewStage,
        context_manifest: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        """Run the review through a direct OpenAI-compatible chat-completions call."""
        payload = await _call_openai_compatible_json(
            system_message="You are a robotics dataset review agent. Return strict JSON only.",
            prompt=_build_review_prompt(job_id, stage, context_manifest),
        )
        markdown = str(payload.get("markdown", ""))
        if not markdown:
            raise RuntimeError("External review returned no markdown body")
        return markdown, payload

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
    pose_data: dict[str, Any] | None = None,
    mapping_profile: dict[str, Any] | None = None,
) -> Callable[[Callable[[str, dict[str, Any]], None]], Callable[[], tuple[str, dict[str, Any]]]]:
    """Return a builder that produces a retarget-stage review factory.

    The returned builder accepts an ``emit`` callable (the service will pass its
    own forwarder) and returns the actual review factory. The local path now
    actually runs the three ``agent_calibrator`` roles (handedness detection,
    calibration review, sanity check) and emits ``progress`` events as it
    inspects the data, so the user sees real activity instead of a templated
    report appearing in milliseconds.
    """

    def _builder(
        emit: Callable[[str, dict[str, Any]], None],
    ) -> Callable[[], tuple[str, dict[str, Any]]]:
        def _factory() -> tuple[str, dict[str, Any]]:
            # If we have real pose data, run the agent reviewer. Otherwise fall
            # back to the legacy templated review (for backwards compatibility
            # with jobs that pre-date the agent wiring).
            if pose_data is not None and "world_landmarks" in pose_data:
                from domain.mapping import MappingProfile

                profile_obj = None
                if mapping_profile:
                    try:
                        profile_obj = MappingProfile(**mapping_profile)
                    except Exception:
                        profile_obj = None

                from pipeline.agent_reviewer import run_agent_review

                agent_result = run_agent_review(
                    pose_data=pose_data,
                    eval_result=eval_result,
                    joint_trajectory=joint_trajectory,
                    mapping_profile=profile_obj,
                    on_progress=lambda message: _safe_emit(emit, "progress", {"message": message}),
                )
                return agent_result["markdown"], agent_result["payload"]

            # Legacy fallback: template-only review.
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

    return _builder


def _safe_emit(
    emit: Callable[[str, dict[str, Any]], None],
    event: str,
    payload: dict[str, Any],
) -> None:
    """Forward one event through ``emit``, ignoring emit failures."""
    try:
        emit(event, payload)
    except Exception:  # noqa: BLE001 - never let emit failure abort the review
        _logger.warning("retarget review emit failed for event=%s", event)


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


async def _call_openai_compatible_json(
    *,
    system_message: str,
    prompt: str,
) -> dict[str, Any]:
    """Call an OpenAI-compatible chat-completions endpoint and parse strict JSON."""
    api_key = settings.effective_llm_api_key
    if not api_key:
        raise RuntimeError("llm_provider_not_configured")

    body = {
        "model": settings.review_model_name,
        "messages": [
            {"role": "system", "content": system_message},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    async with httpx.AsyncClient(timeout=settings.review_timeout_seconds) as client:
        response = await client.post(
            f"{settings.effective_llm_base_url}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=body,
        )
        response.raise_for_status()
        raw = response.json()

    message = raw["choices"][0]["message"]["content"]
    if isinstance(message, list):
        text_parts = [part.get("text", "") for part in message if isinstance(part, dict)]
        message = "".join(text_parts)
    return json.loads(message)


def _build_review_prompt(job_id: str, stage: ReviewStage, context_manifest: dict[str, Any]) -> str:
    """Build a bounded structured prompt for a stage review."""
    compact_context = json.dumps(context_manifest, ensure_ascii=False, indent=2)
    if len(compact_context) > settings.review_max_context_chars:
        raise RuntimeError("context_budget_exceeded")

    verdicts = [
        ReviewVerdict.APPROVED.value,
        ReviewVerdict.USABLE_SKELETON_ONLY.value,
        ReviewVerdict.NEEDS_REVIEW.value,
        ReviewVerdict.REJECTED.value,
    ]
    return (
        f"Review stage: {stage.value}\n"
        f"Job ID: {job_id}\n"
        "Allowed verdicts: "
        + ", ".join(verdicts)
        + "\n\n"
        + "Analyze the following compact robotics dataset review context and return JSON "
        + "with keys: stage, verdict, summary, metrics, artifact_manifest, markdown. "
        + "The markdown must be a concise report suitable for direct UI rendering.\n\n"
        + f"Context:\n{compact_context}"
    )
