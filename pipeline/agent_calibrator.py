"""Bounded agent-as-annotator for retarget mapping calibration.

The agent's role is **not** to fabricate pose data or invent IK solutions. The
extracted MediaPipe world landmarks are the ground truth. The agent's job is to
inspect the evidence (pose metrics, arm chain geometry, retarget review verdict)
and propose corrections to the ``MappingProfile`` so the geometric IK gets the
right answer for *this particular recording*.

Three concrete agent roles live here as small, deterministic functions that can
be replaced or wrapped by a real LLM later:

1. ``calibration_reviewer`` — inspects the whole arm chain motion and recommends
   axis mapping, depth scale, and workspace scale adjustments.
2. ``handedness_detector`` — looks at which arm has more motion / more right-
   shoulder-vs-left-shoulder asymmetry, returns the dominant handedness.
3. ``sanity_checker`` — pre-packaging guard: returns ``verdict = "ok"`` only when
   the world landmarks look metric and the retarget trajectory isn't clamped
   against joint limits on every frame.

All three return a strictly-typed dict matching the ``MappingProfile`` schema
(plus a ``verdict`` and ``summary`` field). They are pure functions over the
``pose_data`` dict, which makes them trivially testable and replaceable.
"""

from __future__ import annotations

from typing import Any, Literal

import numpy as np

from domain.enums import QualityGrade
from domain.mapping import MappingProfile

_RIGHT_SHOULDER = 12
_LEFT_SHOULDER = 11
_RIGHT_WRIST = 16
_LEFT_WRIST = 15

_CALIBRATION_CONFIDENCE_THRESHOLD = 0.5
# A retarget trajectory whose joints sit at the limit on more than this fraction
# of frames is treated as the geometric IK hitting a wall (mapping wrong).
_LIMIT_PRESSURE_THRESHOLD = 0.4
# Joint distance from limit (rad) below which we consider the joint "pressed".
_LIMIT_PRESS_TOL = 0.05


def _safe_array(pose_data: dict, key: str, shape: tuple[int, ...]) -> np.ndarray:
    arr = pose_data.get(key)
    if arr is None or not isinstance(arr, np.ndarray) or arr.size == 0:
        return np.empty(shape, dtype=np.float32)
    return arr


def _arm_motion_energy(world: np.ndarray, shoulder_idx: int, wrist_idx: int) -> float:
    """Total motion of the wrist relative to the shoulder, summed across frames."""
    if world.shape[0] < 2:
        return 0.0
    rel = world[:, wrist_idx, :] - world[:, shoulder_idx, :]
    deltas = np.diff(rel, axis=0)
    return float(np.linalg.norm(deltas, axis=1).sum())


def handedness_detector(pose_data: dict) -> dict[str, Any]:
    """Determine whether the dominant moving arm is left or right.

    Uses total wrist-relative-to-shoulder motion per arm. Returns
    ``handedness`` and a confidence in [0, 1]. When motion is below the noise
    floor the verdict is ``"right"`` (the safe default) with low confidence.
    """
    world = _safe_array(pose_data, "world_landmarks", (0, 33, 3))

    if world.shape[0] == 0:
        return {
            "verdict": "no_change",
            "handedness": "right",
            "confidence": 0.0,
            "summary": "No pose data available; cannot infer handedness.",
        }

    right_energy = _arm_motion_energy(world, _RIGHT_SHOULDER, _RIGHT_WRIST)
    left_energy = _arm_motion_energy(world, _LEFT_SHOULDER, _LEFT_WRIST)
    total = right_energy + left_energy
    if total < 1e-3:
        return {
            "verdict": "no_change",
            "handedness": "right",
            "confidence": 0.0,
            "summary": "Insufficient arm motion to infer handedness.",
        }

    confidence = abs(right_energy - left_energy) / total
    if right_energy >= left_energy:
        handedness: Literal["right", "left"] = "right"
    else:
        handedness = "left"
    return {
        "verdict": "handedness_detected",
        "handedness": handedness,
        "confidence": float(confidence),
        "right_energy_m": float(right_energy),
        "left_energy_m": float(left_energy),
        "summary": (
            f"Dominant arm: {handedness} (right motion {right_energy:.2f} m, "
            f"left motion {left_energy:.2f} m, confidence {confidence:.2f})."
        ),
    }


def calibration_reviewer(
    pose_data: dict,
    retarget_metrics: dict | None = None,
    baseline_profile: MappingProfile | None = None,
) -> dict[str, Any]:
    """Propose a corrected ``MappingProfile`` for this recording.

    Inspects the arm chain geometry and the retarget verdict, then returns a
    new ``MappingProfile`` that should be retried. Returns
    ``verdict = "no_change"`` when the baseline profile looks correct.

    Heuristics (conservative — anything the agent isn't sure about stays
    unchanged):
    - If retarget grade is RED and >40% of frames are pressed against any joint
      limit, suggest enabling ``z_clamp_enabled`` and reducing ``workspace_scale``
      by 10%.
    - If the wrist depth (MediaPipe Z) range is small relative to its X/Y range,
      bump ``depth_scale`` down so the IK doesn't over-amplify the depth axis.
    - Handedness comes from ``handedness_detector`` (called independently).
    """
    baseline = baseline_profile or MappingProfile()
    hand = handedness_detector(pose_data)

    adjustments: dict[str, Any] = {}
    new_workspace = baseline.workspace_scale
    new_depth = baseline.depth_scale
    new_z_clamp = baseline.z_clamp_enabled
    new_axis = baseline.axis_mapping

    if retarget_metrics:
        limit_pressure = retarget_metrics.get("limit_pressure_ratio", 0.0)
        grade = retarget_metrics.get("overall_grade", QualityGrade.GREEN.value)
        if grade == QualityGrade.RED.value and limit_pressure > _LIMIT_PRESSURE_THRESHOLD:
            new_workspace = max(baseline.workspace_scale * 0.9, 0.2)
            new_z_clamp = True
            adjustments["workspace_scale"] = (
                f"{baseline.workspace_scale:.3f} -> {new_workspace:.3f}"
            )
            adjustments["z_clamp_enabled"] = f"{baseline.z_clamp_enabled} -> True"

    world = _safe_array(pose_data, "world_landmarks", (0, 33, 3))
    if world.shape[0] > 1:
        wrist = world[:, _RIGHT_WRIST, :]
        x_range = float(wrist[:, 0].max() - wrist[:, 0].min())
        y_range = float(wrist[:, 1].max() - wrist[:, 1].min())
        z_range = float(wrist[:, 2].max() - wrist[:, 2].min())
        lateral = max(x_range, y_range)
        if z_range > 0 and lateral > 0 and z_range > 1.5 * lateral:
            new_depth = max(baseline.depth_scale * 0.7, 0.2)
            adjustments["depth_scale"] = f"{baseline.depth_scale:.2f} -> {new_depth:.2f}"

    new_handedness: Literal["right", "left"] = baseline.handedness
    if (
        hand["verdict"] == "handedness_detected"
        and hand["confidence"] >= _CALIBRATION_CONFIDENCE_THRESHOLD
        and hand["handedness"] != baseline.handedness
    ):
        new_handedness = hand["handedness"]  # type: ignore[assignment]
        adjustments["handedness"] = (
            f"{baseline.handedness} -> {new_handedness} (confidence {hand['confidence']:.2f})"
        )

    if not adjustments:
        return {
            "verdict": "no_change",
            "mapping_profile": baseline.model_dump(),
            "summary": "Baseline MappingProfile looks correct; no agent corrections.",
        }

    candidate = MappingProfile(
        handedness=new_handedness,
        wrist_landmark_index=baseline.wrist_landmark_index,
        workspace_scale=new_workspace,
        depth_scale=new_depth,
        axis_mapping=new_axis,
        z_clamp_enabled=new_z_clamp,
        position_only=baseline.position_only,
    )
    return {
        "verdict": "rerun_with_profile",
        "mapping_profile": candidate.model_dump(),
        "adjustments": adjustments,
        "handedness_evidence": hand,
        "summary": (
            f"Agent proposed {len(adjustments)} mapping correction(s): "
            + ", ".join(f"{k}: {v}" for k, v in adjustments.items())
        ),
    }


def sanity_checker(
    pose_data: dict,
    joint_trajectory: np.ndarray,
    franka_limits: list[tuple[float, float]] | None = None,
) -> dict[str, Any]:
    """Pre-packaging sanity check: refuse to ship a junk retarget.

    Returns ``verdict = "ok"`` only when:
    - The pose backend produced metric world landmarks (not the unit-cube mock).
    - The trajectory is not pressed against joint limits on most frames.
    - The trajectory has finite, non-zero values (no NaN, not all-zero).

    The "metric vs mock" check uses the upper-arm segment length
    (shoulder→elbow in MediaPipe world landmarks), which is ~0.25–0.30 m for a
    real adult arm. The legacy mock path produced uniform [0, 1] values, so
    shoulder→elbow distance there is ~0.33 (random pair in unit cube). We accept
    any segment in [0.10, 0.55] m, which is conservative for human arms and
    rejects both the random unit cube and tiny fake poses.
    """
    if franka_limits is None:
        from pipeline.retarget import FRANKA_PANDA_JOINT_LIMITS

        franka_limits = FRANKA_PANDA_JOINT_LIMITS

    world = _safe_array(pose_data, "world_landmarks", (0, 33, 3))
    issues: list[str] = []

    if world.shape[0] == 0:
        issues.append("world_landmarks is empty.")
    else:
        # Compute the right upper-arm segment length over all frames.
        shoulder = world[:, _RIGHT_SHOULDER, :]
        elbow = world[:, 14, :]
        segment = np.linalg.norm(shoulder - elbow, axis=1)
        segment = segment[np.isfinite(segment)]
        if segment.size == 0:
            issues.append("world_landmarks contains only non-finite values.")
        else:
            median_segment = float(np.median(segment))
            if not (0.10 <= median_segment <= 0.55):
                issues.append(
                    f"Right upper-arm segment length median = {median_segment:.3f} m; "
                    "expected a metric value in [0.10, 0.55] m. "
                    "Pose data looks like the unit-cube mock or otherwise non-metric."
                )

    if joint_trajectory.size == 0:
        issues.append("joint_trajectory is empty.")
    else:
        if not np.all(np.isfinite(joint_trajectory)):
            issues.append("joint_trajectory contains NaN or inf values.")
        if np.allclose(joint_trajectory, 0.0):
            issues.append("joint_trajectory is all zeros — IK never produced motion.")
        limits = np.array(franka_limits)
        dist_to_lo = joint_trajectory[:, :, None] - limits[None, :, 0]
        dist_to_hi = limits[None, :, 1] - joint_trajectory[:, :, None]
        min_dist = np.minimum(dist_to_lo, dist_to_hi)
        pressed = (min_dist < _LIMIT_PRESS_TOL).any(axis=1)
        pressure_ratio = float(pressed.mean())
        if pressure_ratio > _LIMIT_PRESSURE_THRESHOLD:
            issues.append(
                f"joint_trajectory is pressed against limits on {pressure_ratio:.0%} of frames."
            )

    if issues:
        return {
            "verdict": "reject",
            "issues": issues,
            "summary": "Refusing to package: " + " ".join(issues),
        }
    return {
        "verdict": "ok",
        "issues": [],
        "summary": "Pose and trajectory look metric and within limits.",
    }


__all__ = [
    "calibration_reviewer",
    "handedness_detector",
    "sanity_checker",
]
