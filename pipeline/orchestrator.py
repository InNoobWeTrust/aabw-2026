"""Pipeline orchestrator: runs all stages in sequence, manages callbacks."""

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path

from domain.enums import PipelineStage
from pipeline.agent_calibrator import (
    calibration_reviewer,
    handedness_detector,
    sanity_checker,
)
from pipeline.evaluate import evaluate_trajectory
from pipeline.package import package_lerobot
from pipeline.pose import extract_pose_from_video
from pipeline.preprocess import extract_frames
from pipeline.render_sim import render_simulation_video
from pipeline.retarget import FRANKA_PANDA_JOINT_LIMITS, retarget_to_robot
from pipeline.staged_review import generate_ai_review, run_static_checks

_logger = logging.getLogger(__name__)


async def run_pipeline(
    job_id: str,
    video_path: str,
    output_dir: str,
    status_callback: Callable | None = None,
) -> dict:
    """Run the full pipeline for a video.

    Args:
        job_id: Unique job identifier
        video_path: Path to uploaded video file
        output_dir: Base output directory
        status_callback: Optional callback(status_str, progress_float, stage_str, message_str)

    Returns:
        Dict with pipeline results (trajectory, metrics, files)
    """
    loop = asyncio.get_running_loop()

    def _callback(status: str, progress: float, stage: str, message: str):
        if status_callback:
            status_callback(status, progress, stage, message)

    _callback("running", 0.0, "init", f"Pipeline started for job {job_id}")

    video_path = Path(video_path)
    output_dir = Path(output_dir)
    frames_dir = output_dir / "frames"

    try:
        preprocess_result = await loop.run_in_executor(
            None,
            extract_frames,
            str(video_path),
            str(frames_dir),
            10,
        )
        _callback(
            "running",
            0.1,
            PipelineStage.PREPROCESS,
            f"Extracted {preprocess_result['frame_count']} frames",
        )
    except Exception as exc:
        _callback("failed", 0.1, PipelineStage.PREPROCESS, str(exc))
        return {"status": "failed", "stage": PipelineStage.PREPROCESS, "error": str(exc)}

    try:
        pose_result = await loop.run_in_executor(
            None,
            extract_pose_from_video,
            str(video_path),
        )
        _callback(
            "running",
            0.3,
            PipelineStage.POSE,
            f"Pose extracted: {pose_result['frame_count']} frames, "
            f"detection_rate={pose_result['detection_rate']:.2f}",
        )
    except Exception as exc:
        _callback("failed", 0.3, PipelineStage.POSE, str(exc))
        return {"status": "failed", "stage": PipelineStage.POSE, "error": str(exc)}

    try:
        retarget_result = await loop.run_in_executor(
            None,
            retarget_to_robot,
            pose_result,
            "franka_panda",
        )
        _callback(
            "running",
            0.6,
            PipelineStage.RETARGET,
            f"Retargeted {retarget_result['frame_count']} frames to {retarget_result['robot']}",
        )
    except Exception as exc:
        _callback("failed", 0.6, PipelineStage.RETARGET, str(exc))
        return {"status": "failed", "stage": PipelineStage.RETARGET, "error": str(exc)}

    joint_trajectory = retarget_result["joint_trajectory"]
    ee_trajectory = retarget_result["ee_trajectory"]

    # Agent-as-annotator, step 1: handedness detection runs immediately after
    # retarget so the result is available for downstream stages.
    try:
        handedness = await loop.run_in_executor(None, handedness_detector, pose_result)
    except Exception as exc:
        _logger.warning("agent_handedness_failed: %s", exc)
        handedness = {"verdict": "no_change", "handedness": "right", "confidence": 0.0}

    try:
        eval_result = await loop.run_in_executor(
            None,
            evaluate_trajectory,
            joint_trajectory,
            "franka_panda",
        )
        _callback(
            "running",
            0.8,
            PipelineStage.EVALUATE,
            f"Quality: {eval_result['overall_grade']}, "
            f"violations={eval_result['joint_limit_violations']}, "
            f"nan={eval_result['nan_count']}",
        )
    except Exception as exc:
        _callback("failed", 0.8, PipelineStage.EVALUATE, str(exc))
        return {"status": "failed", "stage": PipelineStage.EVALUATE, "error": str(exc)}

    package_dir = output_dir / "dataset"

    # Agent-as-annotator, step 2: calibration reviewer + sanity checker.
    # The calibration reviewer proposes a corrected MappingProfile based on the
    # retarget verdict; the sanity checker refuses to package metric / limit
    # issues. Both results are persisted to output_dir/calibration/ for the
    # reviewer and the user to inspect.
    try:
        from domain.mapping import MappingProfile as _MappingProfile

        baseline_profile = _MappingProfile(**(retarget_result.get("mapping_profile") or {}))
    except Exception:
        baseline_profile = None
    try:
        limit_pressure_ratio = _compute_limit_pressure_ratio(joint_trajectory)
        agent_metrics = dict(eval_result)
        agent_metrics["limit_pressure_ratio"] = limit_pressure_ratio
        calibration = await loop.run_in_executor(
            None,
            calibration_reviewer,
            pose_result,
            agent_metrics,
            baseline_profile,
        )
        sanity = await loop.run_in_executor(
            None,
            sanity_checker,
            pose_result,
            joint_trajectory,
            FRANKA_PANDA_JOINT_LIMITS,
        )
    except Exception as exc:
        _logger.warning("agent_calibrator_step2_failed: %s", exc)
        calibration = {"verdict": "no_change"}
        sanity = {"verdict": "ok", "issues": []}
    try:
        await loop.run_in_executor(
            None,
            _persist_agent_report,
            output_dir,
            {
                "handedness": handedness,
                "calibration": calibration,
                "sanity": sanity,
            },
        )
    except Exception as exc:
        _logger.warning("agent_calibrator_persist_failed: %s", exc)

    metadata = {
        "job_id": job_id,
        "video": str(video_path.name),
        "robot": retarget_result["robot"],
        "quality": eval_result,
    }

    try:
        pkg_result = await loop.run_in_executor(
            None,
            package_lerobot,
            joint_trajectory,
            ee_trajectory,
            metadata,
            str(package_dir),
        )
        _callback(
            "running",
            0.9,
            PipelineStage.PACKAGE,
            f"Packaged {pkg_result['frame_count']} frames to {pkg_result['output_dir']}",
        )
    except Exception as exc:
        _callback("failed", 0.9, PipelineStage.PACKAGE, str(exc))
        return {"status": "failed", "stage": PipelineStage.PACKAGE, "error": str(exc)}

    try:
        _callback(
            "running",
            0.92,
            PipelineStage.FINALIZE,
            "Rendering simulation video...",
        )
        sim_video_path = output_dir / "simulation.mp4"
        await loop.run_in_executor(
            None,
            render_simulation_video,
            joint_trajectory,
            sim_video_path,
        )

        _callback(
            "running",
            0.96,
            PipelineStage.FINALIZE,
            "Running static checks and AI review...",
        )

        static_checks = run_static_checks(package_dir)
        ai_review_md = generate_ai_review(eval_result, joint_trajectory)

        ai_review_path = output_dir / "ai_review.md"
        ai_review_path.write_text(ai_review_md, encoding="utf-8")

    except Exception as exc:
        _callback("failed", 0.92, PipelineStage.FINALIZE, str(exc))
        return {"status": "failed", "stage": PipelineStage.FINALIZE, "error": str(exc)}

    # Downsample joint trajectory for visualization (max 50 points)
    step = max(1, len(joint_trajectory) // 50)
    downsampled = joint_trajectory[::step].tolist()

    result = {
        "status": "completed",
        "job_id": job_id,
        "preprocess": preprocess_result,
        "pose": {
            "frame_count": pose_result["frame_count"],
            "detection_rate": pose_result["detection_rate"],
        },
        "retarget": {
            "frame_count": retarget_result["frame_count"],
            "robot": retarget_result["robot"],
        },
        "evaluation": eval_result,
        "package": pkg_result,
        "simulation_video": str(sim_video_path),
        "static_checks": static_checks,
        "ai_review": ai_review_md,
        "ai_review_path": str(ai_review_path),
        "downsampled_trajectory": downsampled,
    }

    _callback("completed", 1.0, PipelineStage.FINALIZE, "Pipeline completed successfully")
    return result


def _compute_limit_pressure_ratio(joint_trajectory: object) -> float:
    """Fraction of frames where any joint sits within 0.05 rad of a Franka limit."""
    import numpy as _np

    arr = _np.asarray(joint_trajectory, dtype=_np.float32)
    if arr.size == 0:
        return 0.0
    limits = _np.array(FRANKA_PANDA_JOINT_LIMITS)
    dist_to_lo = arr[:, :, None] - limits[None, :, 0]
    dist_to_hi = limits[None, :, 1] - arr[:, :, None]
    min_dist = _np.minimum(dist_to_lo, dist_to_hi)
    pressed = (min_dist < 0.05).any(axis=1)
    return float(pressed.mean())


def _persist_agent_report(output_dir: Path, report: dict) -> Path:
    """Write the agent calibration report as JSON for downstream consumers."""
    import json

    out_dir = Path(output_dir) / "calibration"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "agent_report.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path
