"""Pipeline orchestrator: runs all stages in sequence, manages callbacks."""

import asyncio
from collections.abc import Callable
from pathlib import Path

from domain.enums import PipelineStage
from pipeline.evaluate import evaluate_trajectory
from pipeline.package import package_lerobot
from pipeline.pose import extract_pose_from_video
from pipeline.preprocess import extract_frames
from pipeline.render_sim import render_simulation_video
from pipeline.retarget import retarget_to_robot


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
    except Exception as exc:
        _callback("failed", 0.92, PipelineStage.FINALIZE, str(exc))
        return {"status": "failed", "stage": PipelineStage.FINALIZE, "error": str(exc)}

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
    }

    _callback("completed", 1.0, PipelineStage.FINALIZE, "Pipeline completed successfully")
    return result
