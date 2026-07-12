"""Async adaptive orchestration with persisted SSE-friendly event streams.

Mirrors the calibration_service pattern: schedules an async sub-job, runs a
deterministic local heuristic by default, optionally delegates to the shared
OpenAI-compatible client, and persists snapshots + events for SSE replay.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from backend.config import settings
from backend.llm_client import request_chat_json
from backend.orchestration_store import FileSystemOrchestrationStore
from domain.enums import OrchestrationDecision, OrchestrationStatus
from domain.mapping import MappingProfile
from domain.orchestration import (
    OrchestrationDonePayload,
    OrchestrationEvent,
    OrchestrationProgressPayload,
    OrchestrationResultPayload,
    OrchestrationStatusPayload,
    OrchestrationTracePayload,
)

_logger = logging.getLogger(__name__)


class OrchestrationService:
    """Manage asynchronous adaptive orchestration runs and persisted event streams."""

    def __init__(self, orchestration_store: FileSystemOrchestrationStore) -> None:
        self._orchestration_store = orchestration_store
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def schedule_orchestration(
        self,
        *,
        job_id: str,
        evidence_manifest: dict[str, Any],
        orchestration_factory: Callable[[], dict[str, Any]],
    ) -> None:
        """Schedule one orchestration run if it is not already active."""
        if job_id in self._tasks and not self._tasks[job_id].done():
            return

        self._orchestration_store.create_orchestration(
            job_id,
            provider=settings.review_provider_name,
            sandbox=settings.review_sandbox_name,
            evidence_manifest=evidence_manifest,
            metadata={"execution_mode": settings.review_execution_mode},
        )
        task = asyncio.create_task(
            self._run_orchestration(job_id, evidence_manifest, orchestration_factory)
        )
        self._tasks[job_id] = task

    async def _run_orchestration(
        self,
        job_id: str,
        evidence_manifest: dict[str, Any],
        orchestration_factory: Callable[[], dict[str, Any]],
    ) -> None:
        """Execute orchestration, persist its decision JSON, and stream events."""
        started_at = datetime.now(timezone.utc)
        heartbeat_stop = asyncio.Event()
        phase_state: dict[str, str] = {"current": "starting"}

        self._orchestration_store.update_orchestration(
            job_id,
            status=OrchestrationStatus.RUNNING,
            started_at=started_at,
            error=None,
            metadata={
                "execution_mode": settings.review_execution_mode,
                "model": settings.review_model_name,
                "current_phase": phase_state["current"],
            },
        )
        self._emit(job_id, "status", OrchestrationStatusPayload(status=OrchestrationStatus.RUNNING))
        self._emit_trace(
            job_id,
            role="system",
            phase="starting",
            title="Orchestrator started",
            content=(
                f"Execution mode: {settings.review_execution_mode}. "
                f"Model: {settings.review_model_name}."
            ),
            metadata={"provider": settings.review_provider_name},
        )
        self._emit_trace(
            job_id,
            role="tool",
            phase="starting",
            title="Evidence pack ready",
            content=(
                "Loaded compact pose, retarget, review, and calibration evidence "
                "for evaluation."
            ),
            tool_name="build_evidence_manifest",
            metadata={"job_id": job_id},
        )
        self._emit_progress(
            job_id,
            phase_state,
            phase="starting",
            message="Preparing orchestration run and evidence context.",
        )
        heartbeat_task = asyncio.create_task(
            self._heartbeat_progress(job_id, heartbeat_stop, phase_state, started_at)
        )
        try:
            if settings.review_execution_mode == "openai_compatible":
                self._emit_progress(
                    job_id,
                    phase_state,
                    phase="external_request",
                    message=(
                        "Requesting external orchestration from the configured provider. "
                        "This may take a while when the model is thinking."
                    ),
                )
                self._emit_trace(
                    job_id,
                    role="tool",
                    phase="external_request",
                    title="LLM request dispatched",
                    content=(
                        "Submitted the orchestration prompt and evidence pack to "
                        "the configured provider."
                    ),
                    tool_name="request_chat_json",
                    metadata={
                        "provider": settings.review_provider_name,
                        "model": settings.review_model_name,
                    },
                )
                try:
                    raw_payload = await self._run_external_orchestration(job_id, evidence_manifest)
                    payload = _normalize_orchestration_result_payload(raw_payload)
                    self._emit_progress(
                        job_id,
                        phase_state,
                        phase="external_response",
                        message="Received structured decision payload from external orchestrator.",
                    )
                    self._emit_trace(
                        job_id,
                        role="ai",
                        phase="external_response",
                        title="AI assessment received",
                        content=(
                            f"Provider returned decision '{payload.decision.value}' "
                            "with confidence "
                            f"{payload.confidence if payload.confidence is not None else 'n/a'}."
                        ),
                        metadata={"risk_count": len(payload.risks)},
                    )
                except Exception as exc:
                    _logger.warning(
                        "External orchestration failed for job %s; "
                        "falling back to local heuristic: %s",
                        job_id,
                        exc,
                    )
                    self._emit_progress(
                        job_id,
                        phase_state,
                        phase="fallback_local",
                        message=(
                            "External orchestration failed or returned unusable output. "
                            "Falling back to local heuristic reasoning."
                        ),
                    )
                    self._emit_trace(
                        job_id,
                        role="system",
                        phase="fallback_local",
                        title="Fallback engaged",
                        content=(
                            "External provider output could not be used, so "
                            "deterministic local orchestration will finish the job."
                        ),
                        metadata={"error": str(exc)},
                    )
                    raw_payload = await asyncio.to_thread(orchestration_factory)
                    payload = _normalize_orchestration_result_payload(raw_payload)
            else:
                self._emit_progress(
                    job_id,
                    phase_state,
                    phase="local_heuristic",
                    message="Running deterministic local orchestration heuristic.",
                )
                self._emit_trace(
                    job_id,
                    role="tool",
                    phase="local_heuristic",
                    title="Local heuristic running",
                    content=(
                        "Evaluating evidence with deterministic orchestration "
                        "rules instead of an external model."
                    ),
                    tool_name="build_orchestration_factory",
                )
                raw_payload = await asyncio.to_thread(orchestration_factory)
                payload = _normalize_orchestration_result_payload(raw_payload)

            summary = payload.summary
            if summary:
                for chunk in _chunk_text(summary, settings.review_stream_chunk_chars):
                    self._emit(job_id, "token", {"text": chunk})

            self._emit_progress(
                job_id,
                phase_state,
                phase="persisting",
                message="Persisting orchestration decision and capture guidance artifacts.",
            )
            self._emit_trace(
                job_id,
                role="tool",
                phase="persisting",
                title="Persisting artifacts",
                content="Writing decision payload and any capture guidance to the job output tree.",
                tool_name="FileSystemOrchestrationStore",
            )
            decision_payload = payload.model_dump(mode="json")
            decision_path = self._orchestration_store.write_decision_payload(
                job_id,
                decision_payload,
            )
            tuned_mapping_profile = payload.tuned_mapping_profile
            capture_guidance = payload.capture_guidance

            if capture_guidance is not None:
                self._orchestration_store.write_capture_guidance(
                    job_id,
                    capture_guidance.model_dump(mode="json"),
                )

            self._orchestration_store.update_orchestration(
                job_id,
                status=OrchestrationStatus.COMPLETED,
                completed_at=datetime.now(timezone.utc),
                decision=payload.decision,
                summary=summary or None,
                json_path=str(decision_path),
                tuned_mapping_profile=tuned_mapping_profile,
                capture_guidance=capture_guidance,
                metadata={
                    "execution_mode": settings.review_execution_mode,
                    "model": settings.review_model_name,
                    "confidence": payload.confidence,
                    "risks": payload.risks,
                    "current_phase": "completed",
                },
            )
            self._emit_progress(
                job_id,
                phase_state,
                phase="completed",
                message="Orchestration completed and final decision is now available.",
            )
            self._emit_trace(
                job_id,
                role="decision",
                phase="completed",
                title="Decision recorded",
                content=payload.summary,
                metadata={
                    "decision": payload.decision.value,
                    "confidence": payload.confidence,
                    "risk_count": len(payload.risks),
                },
            )
            self._emit(job_id, "result", payload)
            self._emit(
                job_id,
                "done",
                OrchestrationDonePayload(status=OrchestrationStatus.COMPLETED),
            )
        except Exception as exc:
            _logger.exception("Orchestration failed for job %s", job_id)
            self._orchestration_store.update_orchestration(
                job_id,
                status=OrchestrationStatus.FAILED,
                completed_at=datetime.now(timezone.utc),
                error=str(exc),
                metadata={
                    "execution_mode": settings.review_execution_mode,
                    "model": settings.review_model_name,
                    "current_phase": "failed",
                },
            )
            self._emit_progress(
                job_id,
                phase_state,
                phase="failed",
                message=f"Orchestration failed: {exc}",
            )
            self._emit_trace(
                job_id,
                role="system",
                phase="failed",
                title="Run failed",
                content="The orchestration run terminated with an error.",
                metadata={"error": str(exc)},
            )
            self._emit(job_id, "error", {"detail": str(exc)})
            self._emit(
                job_id,
                "done",
                OrchestrationDonePayload(status=OrchestrationStatus.FAILED),
            )
        finally:
            heartbeat_stop.set()
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task

    async def _run_external_orchestration(
        self,
        job_id: str,
        evidence_manifest: dict[str, Any],
    ) -> dict[str, Any]:
        """Run orchestration through a direct OpenAI-compatible chat call."""
        return await request_chat_json(
            system_message=(
                "You are a robotics dataset orchestration agent. Return strict JSON only."
            ),
            prompt=_build_orchestration_prompt(job_id, evidence_manifest),
        )

    def _emit(
        self,
        job_id: str,
        event: str,
        payload: dict[str, Any]
        | OrchestrationResultPayload
        | OrchestrationDonePayload
        | OrchestrationStatusPayload
        | OrchestrationProgressPayload
        | OrchestrationTracePayload,
    ) -> None:
        if hasattr(payload, "model_dump"):
            encoded_payload = payload.model_dump(mode="json")
        else:
            encoded_payload = payload
        self._orchestration_store.append_event(
            OrchestrationEvent(
                at=datetime.now(timezone.utc),
                job_id=job_id,
                event=event,
                payload=encoded_payload,
            )
        )

    def _emit_progress(
        self,
        job_id: str,
        phase_state: dict[str, str],
        *,
        phase: str,
        message: str,
    ) -> None:
        """Persist a human-readable progress event and update the live phase marker."""
        phase_state["current"] = phase
        snapshot = self._orchestration_store.get_orchestration(job_id)
        metadata = dict(snapshot.metadata)
        metadata["current_phase"] = phase
        self._orchestration_store.update_orchestration(job_id, metadata=metadata)
        self._emit(
            job_id,
            "progress",
            OrchestrationProgressPayload(phase=phase, message=message),
        )

    def _emit_trace(
        self,
        job_id: str,
        *,
        role: str,
        phase: str,
        title: str,
        content: str,
        tool_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Emit a structured transcript event safe for user-visible execution tracing."""
        self._emit(
            job_id,
            "trace",
            OrchestrationTracePayload(
                role=role,
                phase=phase,
                title=title,
                content=content,
                tool_name=tool_name,
                metadata=metadata or {},
            ),
        )

    async def _heartbeat_progress(
        self,
        job_id: str,
        stop_event: asyncio.Event,
        phase_state: dict[str, str],
        started_at: datetime,
    ) -> None:
        """Emit periodic heartbeats so long-running orchestration never looks frozen."""
        while not stop_event.is_set():
            await asyncio.sleep(2.5)
            if stop_event.is_set():
                break
            elapsed_seconds = int((datetime.now(timezone.utc) - started_at).total_seconds())
            phase = phase_state.get("current", "running")
            self._emit(
                job_id,
                "progress",
                OrchestrationProgressPayload(
                    phase=phase,
                    message=(
                        f"Still running in phase '{phase}' after {elapsed_seconds}s. "
                        "Waiting for the current step to finish."
                    ),
                    heartbeat=True,
                    elapsed_seconds=elapsed_seconds,
                ),
            )


def build_orchestration_factory(
    evidence_manifest: dict[str, Any],
) -> Callable[[], dict[str, Any]]:
    """Return a deterministic local orchestration heuristic."""

    def _factory() -> dict[str, Any]:
        evidence = evidence_manifest
        pose_metrics = evidence.get("pose_metrics", {})
        eval_metrics = evidence.get("evaluation_metrics", {})
        pose_review = evidence.get("pose_review")
        retarget_review = evidence.get("retarget_review")
        calibration = evidence.get("calibration")
        baseline_profile_data = evidence.get("baseline_mapping_profile") or {}

        detection_rate = float(pose_metrics.get("detection_rate", 0.0))
        missing_ratio = float(pose_metrics.get("missing_landmark_ratio", 1.0))
        completeness_ratio = float(eval_metrics.get("completeness_ratio", 0.0))
        overall_grade = str(eval_metrics.get("overall_grade", "red"))
        joint_limit_violations = int(eval_metrics.get("joint_limit_violations", 0))
        nan_count = int(eval_metrics.get("nan_count", 0))
        max_velocity = float(eval_metrics.get("max_velocity", 0.0))
        mean_jerk = float(eval_metrics.get("mean_jerk", 0.0))
        sudden_jump_count = int(eval_metrics.get("sudden_jump_count", 0))

        risks: list[str] = []
        if detection_rate < 0.5:
            risks.append("pose detection coverage is critically low")
        if missing_ratio > 0.4:
            risks.append("landmark visibility is severely incomplete")
        if detection_rate < 0.8:
            risks.append("pose detection coverage is below preferred threshold")
        if completeness_ratio < 0.5:
            risks.append("trajectory completeness is critically low")
        if nan_count > 0:
            risks.append("retarget output contains NaN values")

        pose_review_rejected = pose_review is not None and pose_review.get("verdict") == "rejected"
        retarget_review_rejected = (
            retarget_review is not None and retarget_review.get("verdict") == "rejected"
        )
        cal_rejected = calibration is not None and calibration.get("decision") == "reject"

        is_critically_broken = (
            detection_rate < 0.4 or completeness_ratio < 0.3 or pose_review_rejected
        )

        if is_critically_broken or cal_rejected:
            return _build_retry_capture_payload(evidence, risks)

        if retarget_review_rejected or completeness_ratio < 0.45:
            return _build_skeleton_only_payload(evidence, risks)

        if (
            overall_grade == "green"
            and sudden_jump_count <= 1
            and joint_limit_violations == 0
            and nan_count == 0
            and detection_rate >= 0.85
        ):
            return _build_baseline_ok_payload(evidence, risks)

        return _build_rerun_profile_payload(
            evidence,
            risks,
            baseline_profile_data,
            joint_limit_violations,
            sudden_jump_count,
            max_velocity,
            mean_jerk,
        )

    return _factory


def _build_baseline_ok_payload(
    evidence: dict[str, Any],
    risks: list[str],
) -> dict[str, Any]:
    return {
        "decision": OrchestrationDecision.BASELINE_OK.value,
        "confidence": 0.85,
        "summary": (
            "All quality gates pass. The baseline pipeline produced a usable robot "
            "dataset. No rerun or salvage is recommended."
        ),
        "risks": risks,
        "tuned_mapping_profile": None,
        "capture_guidance": None,
        "evidence_snapshot": _compact_evidence(evidence),
    }


def _build_rerun_profile_payload(
    evidence: dict[str, Any],
    risks: list[str],
    baseline_profile_data: dict[str, Any],
    joint_limit_violations: int,
    sudden_jump_count: int,
    max_velocity: float,
    mean_jerk: float,
) -> dict[str, Any]:
    try:
        baseline_profile = MappingProfile(**baseline_profile_data)
    except Exception:
        baseline_profile = MappingProfile()
    tuned = copy.deepcopy(baseline_profile)
    if joint_limit_violations > 0 or sudden_jump_count >= 4:
        tuned.depth_scale = min(tuned.depth_scale, 0.65)
        tuned.z_clamp_enabled = True
    if max_velocity > 1.5 or mean_jerk > 1.0:
        tuned.workspace_scale *= 0.9
    return {
        "decision": OrchestrationDecision.RERUN_WITH_PROFILE.value,
        "confidence": 0.72,
        "summary": (
            "Retarget output is salvageable with a damped mapping profile. "
            "Rerun deterministic retargeting with the tuned profile below."
        ),
        "risks": risks,
        "tuned_mapping_profile": tuned.model_dump(),
        "capture_guidance": None,
        "evidence_snapshot": _compact_evidence(evidence),
    }


def _build_skeleton_only_payload(
    evidence: dict[str, Any],
    risks: list[str],
) -> dict[str, Any]:
    return {
        "decision": OrchestrationDecision.SKELETON_ONLY.value,
        "confidence": 0.78,
        "summary": (
            "The robot retarget branch is broken, but the human skeleton extraction "
            "is usable. Keep the clip as skeleton-only."
        ),
        "risks": risks,
        "tuned_mapping_profile": None,
        "capture_guidance": None,
        "evidence_snapshot": _compact_evidence(evidence),
    }


def _build_retry_capture_payload(
    evidence: dict[str, Any],
    risks: list[str],
) -> dict[str, Any]:
    guidance = _generate_capture_guidance(evidence, risks)
    return {
        "decision": OrchestrationDecision.RETRY_CAPTURE.value,
        "confidence": 0.81,
        "summary": (
            "The input clip is unsalvageable for robot dataset generation. "
            "Re-record with the provided capture guidance."
        ),
        "risks": risks,
        "tuned_mapping_profile": None,
        "capture_guidance": guidance,
        "evidence_snapshot": _compact_evidence(evidence),
    }


def _generate_capture_guidance(
    evidence: dict[str, Any],
    risks: list[str],
) -> dict[str, Any]:
    """Produce actionable capture guidance for an unsalvageable clip."""
    pose_metrics = evidence.get("pose_metrics", {})
    detection_rate = float(pose_metrics.get("detection_rate", 0.0))
    missing_ratio = float(pose_metrics.get("missing_landmark_ratio", 1.0))

    suggestions: list[str] = []
    if detection_rate < 0.5:
        suggestions.append(
            "Ensure the full body (head through feet) is visible in the frame "
            "throughout the recording."
        )
    if missing_ratio > 0.3:
        suggestions.append(
            "Reduce occlusions. Avoid objects crossing in front of the arm, and "
            "keep both hands visible when possible."
        )
    suggestions.append(
        "Position the camera at chest height with a clear, uncluttered "
        "background. Use consistent, controlled lighting."
    )
    suggestions.append(
        "Perform the manipulation task at a steady, unhurried pace facing the camera directly."
    )

    return {
        "reason": "insufficient_pose_coverage",
        "detection_rate": detection_rate,
        "missing_landmark_ratio": missing_ratio,
        "suggestions": suggestions,
        "risks": risks,
    }


def _compact_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    """Return a compact summary of the evidence for traceability."""
    pose_metrics = evidence.get("pose_metrics", {})
    eval_metrics = evidence.get("evaluation_metrics", {})
    return {
        "pose": {
            "detection_rate": pose_metrics.get("detection_rate"),
            "missing_landmark_ratio": pose_metrics.get("missing_landmark_ratio"),
        },
        "evaluation": {
            "overall_grade": eval_metrics.get("overall_grade", "red"),
            "completeness_ratio": eval_metrics.get("completeness_ratio"),
            "sudden_jump_count": eval_metrics.get("sudden_jump_count"),
            "nan_count": eval_metrics.get("nan_count"),
            "joint_limit_violations": eval_metrics.get("joint_limit_violations"),
        },
        "pose_review": evidence.get("pose_review"),
        "retarget_review": evidence.get("retarget_review"),
        "calibration": evidence.get("calibration"),
    }


def build_evidence_manifest(
    job_result: dict | None,
    pose_review_summary: dict | None,
    retarget_review_summary: dict | None,
    calibration_snapshot: dict | None,
) -> dict[str, Any]:
    """Build a compact evidence manifest from a completed job's artifacts."""
    result = job_result or {}
    pose = result.get("pose", {})
    retarget = result.get("retarget", {})

    pose_metrics = pose.get("metrics", {})
    evaluation = retarget.get("evaluation", {})
    baseline_mapping_profile = retarget.get("mapping_profile")

    return {
        "pose_metrics": {
            "detection_rate": (
                pose_metrics.get("detection_rate")
                if isinstance(pose_metrics, dict)
                else pose.get("detection_rate")
            ),
            "missing_landmark_ratio": (
                pose_metrics.get("missing_landmark_ratio")
                if isinstance(pose_metrics, dict)
                else None
            ),
        },
        "evaluation_metrics": {
            "overall_grade": evaluation.get("overall_grade", "red"),
            "completeness_ratio": evaluation.get("completeness_ratio", 0.0),
            "sudden_jump_count": evaluation.get("sudden_jump_count", 0),
            "nan_count": evaluation.get("nan_count", 0),
            "joint_limit_violations": evaluation.get("joint_limit_violations", 0),
            "max_velocity": evaluation.get("max_velocity", 0.0),
            "mean_jerk": evaluation.get("mean_jerk", 0.0),
        },
        "baseline_mapping_profile": baseline_mapping_profile,
        "pose_review": pose_review_summary,
        "retarget_review": retarget_review_summary,
        "calibration": calibration_snapshot,
        "job_has_robot_dataset": "dataset_robot" in retarget,
    }


def _chunk_text(text: str, width: int) -> list[str]:
    if width <= 0:
        return [text]
    return [text[i : i + width] for i in range(0, len(text), width)] or [""]


def _normalize_orchestration_result_payload(
    raw_payload: dict[str, Any],
) -> OrchestrationResultPayload:
    """Normalize provider/local outputs into the canonical orchestration result shape."""
    candidate = raw_payload
    for wrapper_key in ("data", "return"):
        wrapped = candidate.get(wrapper_key)
        if isinstance(wrapped, dict):
            candidate = wrapped
            break

    try:
        return OrchestrationResultPayload(**candidate)
    except ValidationError as exc:
        raise RuntimeError(f"orchestration_invalid_result_payload: {exc}") from exc


def _build_orchestration_prompt(job_id: str, evidence_manifest: dict[str, Any]) -> str:
    compact_context = json.dumps(evidence_manifest, ensure_ascii=False, indent=2)
    if len(compact_context) > settings.review_max_context_chars:
        raise RuntimeError("context_budget_exceeded")

    decisions = [d.value for d in OrchestrationDecision]
    return (
        f"Orchestration stage: adaptive\nJob ID: {job_id}\n"
        "Allowed decisions: " + ", ".join(decisions) + "\n\n"
        "Analyze the compact robotics pipeline evidence and return strict JSON with keys: "
        "decision, confidence, summary, risks, tuned_mapping_profile, capture_guidance, "
        "evidence_snapshot. tuned_mapping_profile is required only when decision is "
        "rerun_with_profile. capture_guidance is required only when decision is "
        "retry_capture. evidence_snapshot must be a compact subset of the input evidence.\n\n"
        f"Context:\n{compact_context}"
    )
