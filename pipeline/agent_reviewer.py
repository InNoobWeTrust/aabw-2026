"""Agent-as-reviewer: turn the three agent_calibrator roles into a streamed review.

This module is the *local* review path for the async review service. Instead of
templating a static markdown report, the local path now actually inspects the
job's pose data + retarget metrics using the deterministic agent roles from
``pipeline.agent_calibrator`` and reports the reasoning back to the UI as
``progress`` and ``token`` events.

The behaviour is intentionally streaming so the user sees real activity
("🧠 Agent: detecting handedness...", "🧠 Agent: sanity-checking trajectory...",
"🧠 Agent: review complete.") and the markdown is built up step by step. This
replaces the previous "instant" templated review that gave no feedback.

The module is a pure function over its inputs: side effects only flow through
the two injected callbacks. This makes it trivial to unit-test without touching
the filesystem, the HTTP layer, or the review store.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import numpy as np

from domain.enums import QualityGrade, ReviewVerdict
from domain.mapping import MappingProfile
from pipeline.agent_calibrator import (
    calibration_reviewer,
    handedness_detector,
    sanity_checker,
)
from pipeline.retarget import FRANKA_PANDA_JOINT_LIMITS

_logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str], None]


def _safe_call(role_name: str, fn: Callable[..., dict], *args: Any, **kwargs: Any) -> dict:
    """Run one agent role, capturing exceptions so a single failure is non-fatal."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("agent role %s failed: %s", role_name, exc)
        return {
            "verdict": "skipped",
            "summary": f"Agent role '{role_name}' failed: {exc}",
        }


def _verdict_from_signals(
    eval_grade: str,
    sanity: dict,
    calibration: dict,
) -> ReviewVerdict:
    """Combine the three agent signals into a single ReviewVerdict.

    - Sanity rejection always wins (we never approve broken data).
    - Otherwise we trust the calibration reviewer's verdict if it proposed
      changes, else fall back to the kinematic grade.
    """
    if sanity.get("verdict") == "reject":
        return ReviewVerdict.REJECTED
    if calibration.get("verdict") == "rerun_with_profile":
        return ReviewVerdict.NEEDS_REVIEW
    grade = str(eval_grade).lower()
    if grade == QualityGrade.GREEN.value:
        return ReviewVerdict.APPROVED
    if grade == QualityGrade.YELLOW.value:
        return ReviewVerdict.NEEDS_REVIEW
    return ReviewVerdict.USABLE_SKELETON_ONLY


def _build_markdown(
    handedness: dict,
    calibration: dict,
    sanity: dict,
    verdict: ReviewVerdict,
    eval_grade: str,
) -> str:
    """Assemble the final markdown report from the three role results."""
    lines: list[str] = []
    status_banner = {
        ReviewVerdict.APPROVED: "🟢 APPROVED",
        ReviewVerdict.NEEDS_REVIEW: "🟡 NEEDS REVIEW",
        ReviewVerdict.REJECTED: "🔴 REJECTED",
        ReviewVerdict.USABLE_SKELETON_ONLY: "🟠 USABLE SKELETON ONLY",
    }.get(verdict, str(verdict.value))
    lines.append("# Retarget Stage Review")
    lines.append("")
    lines.append(f"## Verdict: {status_banner}")
    lines.append("")
    lines.append(f"**Overall kinematic grade:** `{eval_grade}`")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Agent Inspection Trace")
    lines.append("")
    lines.append("### 1. Handedness detection")
    lines.append("")
    lines.append(
        handedness.get("summary", "No summary available.")
        if handedness.get("verdict") != "skipped"
        else f"_Handedness detection skipped: {handedness.get('summary', 'unknown error')}_"
    )
    lines.append("")
    lines.append("### 2. Calibration review")
    lines.append("")
    if calibration.get("verdict") == "rerun_with_profile":
        lines.append("The agent proposed corrections to the MappingProfile:")
        lines.append("")
        for key, change in (calibration.get("adjustments") or {}).items():
            lines.append(f"- **{key}**: {change}")
        lines.append("")
        lines.append(calibration.get("summary", ""))
    elif calibration.get("verdict") == "skipped":
        lines.append(f"_Calibration review skipped: {calibration.get('summary', 'unknown error')}_")
    else:
        lines.append("Baseline MappingProfile looked correct; no corrections proposed.")
    lines.append("")
    lines.append("### 3. Sanity check")
    lines.append("")
    issues = sanity.get("issues") or []
    if sanity.get("verdict") == "ok":
        lines.append("- ✅ Pose landmarks look metric (real human scale, not unit-cube mock).")
        lines.append("- ✅ Joint trajectory stays within Franka Panda limits.")
        lines.append("- ✅ Trajectory has finite, non-zero motion.")
    else:
        for issue in issues:
            lines.append(f"- ❌ {issue}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Recommendations")
    lines.append("")
    if verdict == ReviewVerdict.REJECTED:
        lines.append(
            "- **Do not use this trajectory for policy training.** The pose data "
            "or IK output failed the agent's sanity check. Re-record the demonstration "
            "or check that MediaPipe Tasks is producing real (metric) landmarks."
        )
    elif verdict == ReviewVerdict.NEEDS_REVIEW:
        lines.append(
            "- **Review the agent's proposed MappingProfile corrections** in the "
            "calibration artifacts. If the corrections look correct, apply them "
            "and re-run the retarget step."
        )
    elif verdict == ReviewVerdict.USABLE_SKELETON_ONLY:
        lines.append(
            "- The skeleton branch is usable for training; the robot branch is not. "
            "Use the skeleton dataset and skip the robot dataset for this job."
        )
    else:
        lines.append("- No issues detected. This trajectory is ready for policy training.")
    return "\n".join(lines) + "\n"


def run_agent_review(
    pose_data: dict,
    eval_result: dict,
    joint_trajectory: np.ndarray,
    mapping_profile: MappingProfile | None = None,
    *,
    on_progress: ProgressCallback | None = None,
) -> dict:
    """Run the three agent roles and produce a review payload.

    Args:
        pose_data: Output of ``extract_pose_from_video`` (must include
            ``world_landmarks`` and ``detected_frames_mask``).
        eval_result: Output of ``pipeline.evaluate.evaluate_trajectory``.
        joint_trajectory: [T, 7] joint angles in radians.
        mapping_profile: Optional MappingProfile used for the retarget. The
            calibration reviewer uses it as the baseline.
        on_progress: Optional callback invoked with each agent's progress line.
            The frontend uses these to show "🧠 Agent: ..." indicators above the
            streamed markdown.

    Returns:
        Dict with keys: ``verdict`` (str), ``summary`` (str), ``markdown`` (str),
        ``agent_evidence`` (dict of three role results), ``payload`` (dict
        suitable for review_store).
    """
    progress: ProgressCallback = on_progress or (lambda _msg: None)

    progress("🧠 Agent: starting retarget review")
    progress("🧠 Agent: detecting handedness from arm motion energy")
    handedness = _safe_call("handedness_detector", handedness_detector, pose_data)
    progress(f"🧠 Agent: handedness → {handedness.get('handedness', 'unknown')}")

    progress("🧠 Agent: reviewing calibration against retarget metrics")
    baseline = mapping_profile or MappingProfile()
    eval_metrics = dict(eval_result or {})
    calibration = _safe_call(
        "calibration_reviewer",
        calibration_reviewer,
        pose_data,
        eval_metrics,
        baseline,
    )
    progress(f"🧠 Agent: calibration → {calibration.get('verdict', 'unknown')}")

    progress("🧠 Agent: sanity-checking pose metrics and trajectory")
    sanity = _safe_call(
        "sanity_checker",
        sanity_checker,
        pose_data,
        joint_trajectory,
        FRANKA_PANDA_JOINT_LIMITS,
    )
    progress(f"🧠 Agent: sanity → {sanity.get('verdict', 'unknown')}")

    eval_grade = str(eval_metrics.get("overall_grade", QualityGrade.RED.value))
    verdict = _verdict_from_signals(eval_grade, sanity, calibration)
    markdown = _build_markdown(handedness, calibration, sanity, verdict, eval_grade)

    summary = (
        f"Agent review verdict: {verdict.value}. "
        f"Sanity: {sanity.get('verdict', 'unknown')}. "
        f"Calibration: {calibration.get('verdict', 'unknown')}. "
        f"Handedness: {handedness.get('handedness', 'unknown')}."
    )
    progress(f"🧠 Agent: review complete → {verdict.value}")
    payload = {
        "stage": "retarget",
        "verdict": verdict.value,
        "summary": summary,
        "agent_evidence": {
            "handedness": handedness,
            "calibration": calibration,
            "sanity": sanity,
        },
        "metrics": {
            "joint_limit_violations": eval_metrics.get("joint_limit_violations", 0),
            "nan_count": eval_metrics.get("nan_count", 0),
            "max_velocity": eval_metrics.get("max_velocity", 0.0),
            "mean_jerk": eval_metrics.get("mean_jerk", 0.0),
            "sudden_jump_count": eval_metrics.get("sudden_jump_count", 0),
            "completeness_ratio": eval_metrics.get("completeness_ratio", 0.0),
        },
        "markdown": markdown,
    }
    return {
        "verdict": verdict.value,
        "summary": summary,
        "markdown": markdown,
        "agent_evidence": payload["agent_evidence"],
        "payload": payload,
    }
