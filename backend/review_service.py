"""Async review orchestration with persisted SSE-friendly event streams.

This module provides a hackathon-friendly review service that prefers a real
Featherless + Daytona execution path when credentials are configured, while
falling back to deterministic local review generation when those credentials are
absent. The fallback keeps review UX/test flows functional without making the
main pipeline depend on external services being available.
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
        task = asyncio.create_task(
            self._run_review(job_id, stage, context_manifest, review_factory)
        )
        self._tasks[key] = task

    async def _run_review(
        self,
        job_id: str,
        stage: ReviewStage,
        context_manifest: dict[str, Any],
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
            if settings.review_execution_mode == "featherless_daytona":
                markdown, payload = await self._run_external_review(job_id, stage, context_manifest)
            else:
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
        """Run the review inside Daytona and call Featherless from the sandbox."""
        sandbox_id = await self._create_daytona_sandbox()
        try:
            code = _build_daytona_review_program(job_id, stage, context_manifest)
            payload = await self._run_daytona_code(sandbox_id, code)
            markdown = str(payload.get("markdown", ""))
            if not markdown:
                raise RuntimeError("External review returned no markdown body")
            return markdown, payload
        finally:
            await self._delete_daytona_sandbox(sandbox_id)

    async def _create_daytona_sandbox(self) -> str:
        """Create a short-lived Daytona sandbox and return its id."""
        headers = {
            "Authorization": f"Bearer {settings.daytona_api_key}",
            "Content-Type": "application/json",
        }
        body: dict[str, Any] = {}
        if settings.daytona_project_id:
            body["projectId"] = settings.daytona_project_id
        async with httpx.AsyncClient(timeout=settings.review_timeout_seconds) as client:
            response = await client.post(
                f"{settings.daytona_base_url}/api/sandbox",
                headers=headers,
                json=body,
            )
            response.raise_for_status()
            data = response.json()
        sandbox_id = data.get("id") or data.get("sandboxId")
        if not sandbox_id:
            raise RuntimeError("Daytona sandbox creation returned no sandbox id")
        return str(sandbox_id)

    async def _run_daytona_code(self, sandbox_id: str, code: str) -> dict[str, Any]:
        """Execute Python code inside a Daytona sandbox and parse the JSON result."""
        headers = {"Content-Type": "application/json"}
        url = f"{settings.daytona_proxy_base_url}/toolbox/{sandbox_id}/process/code-run"
        async with httpx.AsyncClient(timeout=settings.review_timeout_seconds) as client:
            response = await client.post(url, headers=headers, json={"code": code})
            response.raise_for_status()
            data = response.json()
        result_text = data.get("result", "")
        if not result_text:
            raise RuntimeError("Daytona code run returned no result payload")
        return json.loads(result_text)

    async def _delete_daytona_sandbox(self, sandbox_id: str) -> None:
        """Best-effort sandbox cleanup."""
        headers = {"Authorization": f"Bearer {settings.daytona_api_key}"}
        async with httpx.AsyncClient(timeout=settings.review_timeout_seconds) as client:
            try:
                await client.delete(
                    f"{settings.daytona_base_url}/api/sandbox/{sandbox_id}",
                    headers=headers,
                )
            except Exception:  # pragma: no cover - cleanup should never break the pipeline
                _logger.warning("Failed to delete Daytona sandbox %s", sandbox_id)

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



def _build_daytona_review_program(
    job_id: str,
    stage: ReviewStage,
    context_manifest: dict[str, Any],
) -> str:
    """Return a Python program executed inside Daytona to call Featherless."""
    prompt = _build_review_prompt(job_id, stage, context_manifest)
    prompt_json = json.dumps(prompt)
    model_json = json.dumps(settings.review_model_name)
    api_key_json = json.dumps(settings.featherless_api_key)
    base_url_json = json.dumps(settings.featherless_base_url.rstrip("/"))
    timeout_json = json.dumps(settings.review_timeout_seconds)

    system_message_json = json.dumps(
        "You are a robotics dataset review agent. Return strict JSON only."
    )

    return f'''
import json
import urllib.request

PROMPT = {prompt_json}
MODEL = {model_json}
API_KEY = {api_key_json}
BASE_URL = {base_url_json}
TIMEOUT = {timeout_json}
SYSTEM_MESSAGE = {system_message_json}

body = json.dumps({{
    "model": MODEL,
    "messages": [
        {{"role": "system", "content": SYSTEM_MESSAGE}},
        {{"role": "user", "content": PROMPT}},
    ],
    "temperature": 0.2,
    "response_format": {{"type": "json_object"}},
}}).encode("utf-8")

request = urllib.request.Request(
    BASE_URL + "/v1/chat/completions",
    data=body,
    headers={{
        "Authorization": f"Bearer {{API_KEY}}",
        "Content-Type": "application/json",
    }},
    method="POST",
)

with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
    raw = json.loads(response.read().decode("utf-8"))

message = raw["choices"][0]["message"]["content"]
payload = json.loads(message)
print(json.dumps(payload))
'''



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
