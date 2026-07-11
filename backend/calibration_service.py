"""Async read-only mapping calibration orchestration with persisted SSE-friendly events."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import httpx

from backend.calibration_store import FileSystemCalibrationStore
from backend.config import settings
from domain.calibration import CalibrationEvent
from domain.enums import CalibrationDecision, CalibrationStatus, CalibrationVerdict
from domain.mapping import MappingProfile

_logger = logging.getLogger(__name__)


class CalibrationService:
    """Manage asynchronous mapping calibration runs and persisted event streams."""

    def __init__(self, calibration_store: FileSystemCalibrationStore) -> None:
        self._calibration_store = calibration_store
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def schedule_calibration(
        self,
        *,
        job_id: str,
        context_manifest: dict[str, Any],
        calibration_factory: Callable[[], dict[str, Any]],
    ) -> None:
        """Schedule one calibration run if it is not already active."""
        if job_id in self._tasks and not self._tasks[job_id].done():
            return

        self._calibration_store.create_calibration(
            job_id,
            provider=settings.review_provider_name,
            sandbox=settings.review_sandbox_name,
            context_manifest=context_manifest,
            metadata={"execution_mode": settings.review_execution_mode},
        )
        task = asyncio.create_task(
            self._run_calibration(job_id, context_manifest, calibration_factory)
        )
        self._tasks[job_id] = task

    async def _run_calibration(
        self,
        job_id: str,
        context_manifest: dict[str, Any],
        calibration_factory: Callable[[], dict[str, Any]],
    ) -> None:
        """Execute calibration, persist its decision JSON, and stream events."""
        self._calibration_store.update_calibration(
            job_id,
            status=CalibrationStatus.RUNNING,
            started_at=datetime.now(timezone.utc),
            error=None,
        )
        self._emit(job_id, "status", {"status": CalibrationStatus.RUNNING.value})
        try:
            if settings.review_execution_mode == "openai_compatible":
                try:
                    payload = await self._run_external_calibration(job_id, context_manifest)
                except Exception as exc:
                    _logger.warning(
                        "External mapping calibration failed for job %s; "
                        "falling back to local calibration: %s",
                        job_id,
                        exc,
                    )
                    payload = await asyncio.to_thread(calibration_factory)
            else:
                payload = await asyncio.to_thread(calibration_factory)

            summary = str(payload.get("summary", ""))
            if summary:
                for chunk in _chunk_text(summary, settings.review_stream_chunk_chars):
                    self._emit(job_id, "token", {"text": chunk})

            decision_path = self._calibration_store.write_decision_payload(job_id, payload)
            decision = payload.get("decision")
            verdict = payload.get("verdict")
            self._calibration_store.update_calibration(
                job_id,
                status=CalibrationStatus.COMPLETED,
                completed_at=datetime.now(timezone.utc),
                decision=CalibrationDecision(decision) if decision else None,
                verdict=CalibrationVerdict(verdict) if verdict else None,
                summary=summary or None,
                json_path=str(decision_path),
                metadata={
                    "execution_mode": settings.review_execution_mode,
                    "model": settings.review_model_name,
                },
            )
            self._emit(job_id, "result", payload)
            self._emit(job_id, "done", {"status": CalibrationStatus.COMPLETED.value})
        except Exception as exc:  # pragma: no cover - defensive async guard
            _logger.exception("Mapping calibration failed for job %s", job_id)
            self._calibration_store.update_calibration(
                job_id,
                status=CalibrationStatus.FAILED,
                completed_at=datetime.now(timezone.utc),
                error=str(exc),
            )
            self._emit(job_id, "error", {"detail": str(exc)})
            self._emit(job_id, "done", {"status": CalibrationStatus.FAILED.value})

    async def _run_external_calibration(
        self,
        job_id: str,
        context_manifest: dict[str, Any],
    ) -> dict[str, Any]:
        """Run calibration through a direct OpenAI-compatible chat-completions call."""
        return await _call_openai_compatible_json(
            system_message="You are a robotics mapping calibration agent. Return strict JSON only.",
            prompt=_build_calibration_prompt(job_id, context_manifest),
        )

    def _emit(self, job_id: str, event: str, payload: dict[str, Any]) -> None:
        self._calibration_store.append_event(
            CalibrationEvent(
                at=datetime.now(timezone.utc),
                job_id=job_id,
                event=event,
                payload=payload,
            )
        )


def build_mapping_calibration_factory(
    context_manifest: dict[str, Any],
) -> Callable[[], dict[str, Any]]:
    """Return a deterministic local calibration heuristic with a strict JSON contract."""

    def _factory() -> dict[str, Any]:
        pose_metrics = context_manifest.get("pose_metrics", {})
        retarget_metrics = context_manifest.get("retarget_metrics", {})
        sample_pack = context_manifest.get("mapping_context_samples", {})
        baseline_profile_data = (
            context_manifest.get("baseline_mapping_profile") or MappingProfile().model_dump()
        )
        baseline_profile = MappingProfile(**baseline_profile_data)

        detection_rate = float(pose_metrics.get("detection_rate", 0.0))
        missing_ratio = float(pose_metrics.get("missing_landmark_ratio", 1.0))
        wrist_jitter = float(
            ((pose_metrics.get("keypoints") or {}).get("right_wrist") or {}).get(
                "temporal_jitter", 0.0
            )
        )
        completeness_ratio = float(retarget_metrics.get("completeness_ratio", 0.0))
        sudden_jump_count = int(retarget_metrics.get("sudden_jump_count", 0))
        joint_limit_violations = int(retarget_metrics.get("joint_limit_violations", 0))
        nan_count = int(retarget_metrics.get("nan_count", 0))
        max_velocity = float(retarget_metrics.get("max_velocity", 0.0))
        mean_jerk = float(retarget_metrics.get("mean_jerk", 0.0))
        overall_grade = str(retarget_metrics.get("overall_grade", "red"))

        risks: list[str] = []
        if sample_pack.get("sample_count", 0) == 0:
            risks.append("no sampled visual evidence available")
        if detection_rate < 0.8:
            risks.append("pose detection coverage is below preferred threshold")
        if missing_ratio > 0.15:
            risks.append("landmark visibility is incomplete")
        if wrist_jitter > 0.05:
            risks.append("wrist trajectory appears noisy")
        if sudden_jump_count > 3:
            risks.append("robot trajectory contains sudden jumps")
        if max_velocity > 1.5:
            risks.append("robot velocity spikes suggest exaggerated workspace scaling")
        if nan_count > 0:
            risks.append("retarget output contains NaN values")
        if joint_limit_violations > 0:
            risks.append("joint limits were violated in the baseline run")

        if detection_rate < 0.45 or completeness_ratio < 0.35:
            decision = CalibrationDecision.REJECT
            verdict = CalibrationVerdict.REJECTED
            mapping_profile: dict[str, Any] | None = None
            confidence = 0.88
            summary = (
                "Pose evidence is too incomplete for a trustworthy robot mapping recommendation. "
                "Reject the clip or re-record it."
            )
        elif overall_grade == "green" and sudden_jump_count <= 1 and wrist_jitter <= 0.03:
            decision = CalibrationDecision.BASELINE_OK
            verdict = CalibrationVerdict.BASELINE_ACCEPTABLE
            mapping_profile = baseline_profile.model_dump()
            confidence = 0.79
            summary = (
                "Baseline mapping already appears stable across the sampled evidence. "
                "No calibration rerun is recommended."
            )
        elif nan_count == 0 and joint_limit_violations == 0 and completeness_ratio >= 0.7:
            tuned_profile = copy.deepcopy(baseline_profile)
            if wrist_jitter > 0.05 or sudden_jump_count >= 4:
                tuned_profile.depth_scale = min(tuned_profile.depth_scale, 0.65)
                tuned_profile.z_clamp_enabled = True
            if max_velocity > 1.5 or mean_jerk > 1.0:
                tuned_profile.workspace_scale *= 0.9
            decision = CalibrationDecision.RERUN_WITH_PROFILE
            verdict = CalibrationVerdict.ROBOT_MAPPING_SALVAGEABLE
            mapping_profile = tuned_profile.model_dump()
            confidence = 0.74
            summary = (
                "Pose extraction looks usable, but the baseline robot mapping appears "
                "overstretched or depth-unstable. Rerun deterministic retargeting with a "
                "damped profile."
            )
        else:
            decision = CalibrationDecision.SKELETON_ONLY
            verdict = CalibrationVerdict.SKELETON_ONLY
            mapping_profile = None
            confidence = 0.71
            summary = (
                "The human skeleton appears more trustworthy than the robot retarget output. "
                "Keep the clip as skeleton-usable only."
            )

        return {
            "decision": decision.value,
            "mapping_profile": mapping_profile,
            "anchors": [],
            "verdict": verdict.value,
            "confidence": confidence,
            "summary": summary,
            "risks": risks,
            "context_manifest": {
                "sample_count": sample_pack.get("sample_count", 0),
                "pose_review_summary": context_manifest.get("pose_review_summary"),
                "retarget_review_summary": context_manifest.get("retarget_review_summary"),
            },
        }

    return _factory


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


def _build_calibration_prompt(job_id: str, context_manifest: dict[str, Any]) -> str:
    compact_context = json.dumps(context_manifest, ensure_ascii=False, indent=2)
    if len(compact_context) > settings.review_max_context_chars:
        raise RuntimeError("context_budget_exceeded")

    decisions = [decision.value for decision in CalibrationDecision]
    verdicts = [verdict.value for verdict in CalibrationVerdict]
    return (
        f"Calibration stage: mapping_calibration\nJob ID: {job_id}\n"
        + "Allowed decisions: "
        + ", ".join(decisions)
        + "\nAllowed verdicts: "
        + ", ".join(verdicts)
        + "\n\nAnalyze the compact robotics mapping context and return strict JSON with keys: "
        + "decision, mapping_profile, anchors, verdict, confidence, summary, risks. "
        + "mapping_profile is required only when decision is rerun_with_profile.\n\n"
        + f"Context:\n{compact_context}"
    )
