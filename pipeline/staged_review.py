"""Static dataset verification and deterministic review generators.

This module currently provides review fallbacks that can run without external
LLM credentials. They are used by the async review orchestration layer when the
Featherless + Daytona execution path is not configured.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from domain.enums import QualityGrade

# Joint limits for Franka Panda (from retarget.py)
FRANKA_PANDA_JOINT_LIMITS = [
    (-2.8973, 2.8973),
    (-1.7628, 1.7628),
    (-2.8973, 2.8973),
    (-3.0718, -0.0698),
    (-2.8973, 2.8973),
    (-0.0175, 3.7525),
    (-2.8973, 2.8973),
]


def run_static_checks(output_dir: str | Path) -> dict:
    """Run static validation checks on the packaged LeRobot dataset files.

    Args:
        output_dir: Directory containing the dataset outputs.

    Returns:
        Dict carrying verification results.
    """
    output_dir = Path(output_dir)
    checks = []
    overall_status = "passed"

    # Check 1: File existence
    parquet_path = output_dir / "episode_000000.parquet"
    meta_path = output_dir / "meta.json"
    stats_path = output_dir / "stats.json"

    files_exist = parquet_path.is_file() and meta_path.is_file() and stats_path.is_file()
    checks.append(
        {
            "name": "Dataset Files Existence",
            "passed": files_exist,
            "details": (
                "Found episode_000000.parquet, meta.json, and stats.json"
                if files_exist
                else "Missing one or more required dataset files"
            ),
        }
    )
    if not files_exist:
        overall_status = "failed"
        return {"status": overall_status, "checks": checks}

    # Check 2: meta.json validation
    meta_valid = False
    total_frames = 0
    try:
        meta_data = json.loads(meta_path.read_text(encoding="utf-8"))
        total_frames = meta_data.get("total_frames", 0)
        meta_valid = total_frames > 0 and meta_data.get("robot_type") == "franka_panda"
        details = (
            f"Valid meta.json: robot={meta_data.get('robot_type')}, frames={total_frames}"
            if meta_valid
            else "meta.json is missing total_frames or has incorrect robot_type"
        )
    except Exception as exc:
        details = f"Failed to parse meta.json: {exc}"

    checks.append({"name": "Metadata Verification", "passed": meta_valid, "details": details})
    if not meta_valid:
        overall_status = "failed"

    # Check 3: stats.json validation
    stats_valid = False
    try:
        stats_data = json.loads(stats_path.read_text(encoding="utf-8"))
        stats_valid = len(stats_data) == 7 and all(
            "min" in stats_data[f"joint_{i}"] for i in range(7)
        )
        details = (
            "Valid stats.json: contains statistics for all 7 joints"
            if stats_valid
            else "stats.json is missing required joint statistics"
        )
    except Exception as exc:
        details = f"Failed to parse stats.json: {exc}"

    checks.append(
        {"name": "Dataset Statistics Verification", "passed": stats_valid, "details": details}
    )
    if not stats_valid:
        overall_status = "failed"

    # Check 4: Parquet format and columns
    parquet_valid = False
    try:
        df = pd.read_parquet(str(parquet_path))
        required_cols = {"observation.state", "action", "timestamp", "episode_index", "frame_index"}
        has_cols = required_cols.issubset(df.columns)
        has_rows = len(df) == total_frames

        # Check for NaNs
        has_nans = df["observation.state"].apply(lambda x: np.any(np.isnan(x))).any()

        parquet_valid = has_cols and has_rows and not has_nans
        if not has_cols:
            details = f"Missing required columns: {required_cols - set(df.columns)}"
        elif not has_rows:
            details = (
                f"Row count mismatch: parquet has {len(df)} rows, meta.json expects {total_frames}"
            )
        elif has_nans:
            details = "Parquet dataset contains NaN values in state observations"
        else:
            details = f"Valid Parquet schema: {len(df)} frames verified"
    except Exception as exc:
        details = f"Failed to read Parquet: {exc}"

    checks.append(
        {"name": "Parquet Schema & Data Integrity", "passed": parquet_valid, "details": details}
    )
    if not parquet_valid:
        overall_status = "failed"

    return {"status": overall_status, "checks": checks}


def generate_ai_review(eval_result: dict, joint_trajectory: np.ndarray) -> str:
    """Generate a deterministic retarget-stage review report in Markdown format.

    Args:
        eval_result: Dictionary of metrics from evaluate_trajectory.
        joint_trajectory: [T, 7] numpy array of joint angles in radians.

    Returns:
        Markdown-formatted report text.
    """
    grade_str = eval_result.get("overall_grade", QualityGrade.RED.value)
    violations = eval_result.get("joint_limit_violations", 0)
    nan_count = eval_result.get("nan_count", 0)
    max_vel = eval_result.get("max_velocity", 0.0)
    jumps = eval_result.get("sudden_jump_count", 0)
    jerk = eval_result.get("mean_jerk", 0.0)
    completeness = eval_result.get("completeness_ratio", 0.0)

    # Compute proximity to limits
    min_dist_to_limit = float("inf")
    if joint_trajectory.size > 0:
        for j, (lo, hi) in enumerate(FRANKA_PANDA_JOINT_LIMITS):
            dist_lo = np.abs(joint_trajectory[:, j] - lo)
            dist_hi = np.abs(joint_trajectory[:, j] - hi)
            min_dist_to_limit = min(
                min_dist_to_limit, float(np.min(dist_lo)), float(np.min(dist_hi))
            )

    # Determine status header
    if grade_str == QualityGrade.GREEN.value:
        status_banner = "🟢 APPROVED (PRODUCTION READY)"
        summary_statement = (
            "This trajectory passes all automated verification gates and "
            "exhibits high kinematic quality."
        )
    elif grade_str == QualityGrade.YELLOW.value:
        status_banner = "🟡 NEEDS REVIEW (BORDERLINE QUALITY)"
        summary_statement = (
            "This trajectory is functional but exhibits some borderline metrics "
            "that could affect training stability."
        )
    else:
        status_banner = "🔴 REJECTED (INSUFFICIENT QUALITY)"
        summary_statement = (
            "This trajectory fails one or more critical quality gates. "
            "Training policies on this data is not recommended."
        )

    # Recommendations lists
    recs = []
    if violations > 0:
        recs.append(
            "- **Resolve joint limit violations**: The human demonstrator is "
            "moving outside the reachable joints of the Franka Panda. "
            "Re-align the demonstration workspace or adjust the retargeting scaling factors."
        )
    if nan_count > 0:
        recs.append(
            "- **Eliminate NaN values**: Missing pose estimations were detected. "
            "Ensure the demonstrator's hand and wrist are completely visible to the camera "
            "at all times, and avoid rapid occlusions."
        )
    if jumps > 0:
        recs.append(
            "- **Reduce sudden movement jumps**: Discontinuities were found in the trajectory. "
            "Ensure the camera is mounted stably and does not shake. Avoid rapid lighting "
            "changes that confuse MediaPipe."
        )
    if jerk > 1.5:
        recs.append(
            "- **Smooth demonstrator movement**: The trajectory exhibits high jerk. "
            "The demonstrator should move their arm more slowly and smoothly during the recording."
        )
    if completeness < 0.8:
        recs.append(
            "- **Increase recording duration**: The demonstration is shorter than "
            "the recommended 30 seconds. Aim to capture a complete, "
            "sustained execution of the task."
        )

    if not recs:
        recs.append("- No issues detected. This demonstration is fully ready for policy training.")

    recs_md = "\n".join(recs)

    # Table Status Helpers
    viol_status = "✅ Pass" if violations == 0 else "❌ Fail"
    nan_status = "✅ Pass" if nan_count == 0 else "❌ Fail"
    vel_status = "✅ Pass" if max_vel < 2.0 else "⚠️ Warning" if max_vel <= 3.0 else "❌ Fail"
    jump_status = "✅ Pass" if jumps < 5 else "⚠️ Warning" if jumps <= 15 else "❌ Fail"
    jerk_status = "✅ Pass" if jerk < 1.0 else "⚠️ Warning" if jerk <= 2.0 else "❌ Fail"
    comp_status = (
        "✅ Pass" if completeness > 0.90 else "⚠️ Warning" if completeness >= 0.75 else "❌ Fail"
    )

    # Analysis Helpers
    lim_analysis = (
        "The joint boundary buffer is within safe tolerances."
        if min_dist_to_limit > 0.1
        else (
            "The trajectory approaches the joint limit boundaries very closely; "
            "this may trigger torque limits or safety halts on the physical robot."
        )
    )

    jerk_desc = (
        "smooth and continuous"
        if jerk < 1.0
        else "moderately jerky"
        if jerk <= 2.0
        else "highly erratic"
    )

    vel_analysis = (
        "Joint acceleration profiles are stable."
        if max_vel < 2.0
        else ("High velocity peaks detected; ensure this is not caused by tracking jitter.")
    )

    # Compile Markdown Report
    report = f"""# Robot Retarget Review Report

## Verification Status: {status_banner}

{summary_statement}

---

## Kinematic Quality Summary

| Metric | Measured Value | Threshold / Target | Status |
| :--- | :--- | :--- | :--- |
| **Joint Limit Violations** | {violations} joints | 0 violations | {viol_status} |
| **NaN Detections** | {nan_count} frames | 0 frames | {nan_status} |
| **Max Velocity** | {max_vel:.3f} rad/f | < 2.0 rad/f | {vel_status} |
| **Sudden Jumps** | {jumps} frames | < 5 frames | {jump_status} |
| **Mean Jerk** | {jerk:.4f} rad/f³ | < 1.0 | {jerk_status} |
| **Completeness** | {completeness:.1%} | > 90.0% | {comp_status} |

---

## Detailed Kinematic Analysis

1. **Workspace & Limit Proximity**:
   - The arm joint coordinates came within **{min_dist_to_limit:.4f} rad** of boundary limit.
   - {lim_analysis}

2. **Trajectory Smoothness (Jerk & Velocity)**:
   - The measured mean jerk is **{jerk:.4f}**, reflecting a {jerk_desc} human demonstration.
   - The maximum joint velocity was **{max_vel:.3f} rad/frame**. {vel_analysis}

---

## Recommendations & Corrective Actions

{recs_md}
"""
    return report


def generate_pose_review(metrics: dict, artifact_manifest: dict) -> tuple[str, dict]:
    """Generate a deterministic pose-stage review and structured payload."""
    detection_rate = float(metrics.get("detection_rate", 0.0))
    avg_visibility = float(metrics.get("average_visibility", 0.0))
    missing_ratio = float(metrics.get("missing_landmark_ratio", 1.0))
    wrists = metrics.get("keypoints", {})
    left_wrist = wrists.get("left_wrist", {})
    right_wrist = wrists.get("right_wrist", {})
    wrist_jitter = max(
        float(left_wrist.get("temporal_jitter", 0.0)),
        float(right_wrist.get("temporal_jitter", 0.0)),
    )

    if detection_rate >= 0.9 and missing_ratio <= 0.2 and wrist_jitter <= 0.08:
        verdict = "approved"
        status_banner = "🟢 APPROVED"
        summary = "Pose extraction is stable enough to preserve and use the skeleton-stage dataset."
    elif detection_rate >= 0.6 and missing_ratio <= 0.5:
        verdict = "needs_review"
        status_banner = "🟡 NEEDS REVIEW"
        summary = (
            "Pose extraction produced usable motion traces, but landmark stability is borderline."
        )
    else:
        verdict = "rejected"
        status_banner = "🔴 REJECTED"
        summary = (
            "Pose extraction is too unstable for confident downstream use without "
            "manual inspection."
        )

    detection_status = (
        "✅ Pass" if detection_rate >= 0.9 else "⚠️ Warning" if detection_rate >= 0.6 else "❌ Fail"
    )
    visibility_status = (
        "✅ Pass" if avg_visibility >= 0.6 else "⚠️ Warning" if avg_visibility >= 0.4 else "❌ Fail"
    )
    missing_status = (
        "✅ Pass" if missing_ratio <= 0.2 else "⚠️ Warning" if missing_ratio <= 0.5 else "❌ Fail"
    )
    jitter_status = (
        "✅ Pass" if wrist_jitter <= 0.08 else "⚠️ Warning" if wrist_jitter <= 0.16 else "❌ Fail"
    )

    recommendations = []
    if detection_rate < 0.9:
        recommendations.append(
            "- Improve camera framing and keep the subject fully visible for the full clip."
        )
    if missing_ratio > 0.2:
        recommendations.append(
            "- Reduce self-occlusion and background clutter so landmark confidence stays high."
        )
    if wrist_jitter > 0.08:
        recommendations.append(
            "- Use a more stable recording or stronger temporal smoothing before retargeting."
        )
    if not recommendations:
        recommendations.append("- No major pose-stage issues detected.")

    recommendations_md = "\n".join(recommendations)
    report = f"""# Pose Extraction Review Report

## Verification Status: {status_banner}

{summary}

---

## Pose Quality Summary

| Metric | Measured Value | Target | Status |
| :--- | :--- | :--- | :--- |
| **Detection Rate** | {detection_rate:.1%} | > 90% | {detection_status} |
| **Average Visibility** | {avg_visibility:.3f} | > 0.60 | {visibility_status} |
| **Missing Landmark Ratio** | {missing_ratio:.1%} | < 20% | {missing_status} |
| **Max Wrist Jitter** | {wrist_jitter:.4f} | < 0.08 | {jitter_status} |

---

## Artifact Coverage

- Skeleton overlay: `{artifact_manifest.get("skeleton_overlay_video", "n/a")}`
- Skeleton preview: `{artifact_manifest.get("skeleton_preview_video", "n/a")}`
- Skeleton dataset: `{artifact_manifest.get("dataset_skeleton_dir", "n/a")}`

---

## Recommendations

{recommendations_md}
"""
    payload = {
        "stage": "pose",
        "verdict": verdict,
        "summary": summary,
        "metrics": {
            "detection_rate": detection_rate,
            "average_visibility": avg_visibility,
            "missing_landmark_ratio": missing_ratio,
            "max_wrist_jitter": wrist_jitter,
        },
        "artifact_manifest": artifact_manifest,
        "markdown": report,
    }
    return report, payload
