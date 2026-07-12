"""Checkpoint-triggered deterministic rerun execution service.

Runs retarget -> evaluate -> package -> render on persisted pose artifacts
using a restored mapping checkpoint, without touching the baseline job result.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import numpy as np

from backend.checkpoint_rerun_store import FileSystemCheckpointRerunStore
from backend.config import settings
from backend.mapping_session_store import FileSystemMappingSessionStore
from domain.enums import RerunStatus
from domain.mapping_session import MappingSession

_logger = logging.getLogger(__name__)


class CheckpointRerunService:
    """Orchestrate versioned pipeline reruns from restored checkpoints."""

    def __init__(
        self,
        rerun_store: FileSystemCheckpointRerunStore,
        session_store: FileSystemMappingSessionStore,
    ) -> None:
        self._rerun_store = rerun_store
        self._session_store = session_store

    def trigger_rerun(
        self,
        session: MappingSession,
        checkpoint_id: str,
        *,
        metadata: dict | None = None,
    ) -> str:
        """Create a rerun record, mark it QUEUED, and schedule async execution.

        Returns the *rerun_id*.
        """
        checkpoint = self._session_store.get_checkpoint(
            session.job_id, session.session_id, checkpoint_id
        )
        rerun = self._rerun_store.create_rerun(
            session,
            source_checkpoint_id=checkpoint_id,
            mapping_profile=checkpoint.mapping_profile,
            metadata=metadata,
        )
        self._rerun_store.update_rerun(
            session.job_id,
            session.session_id,
            rerun.rerun_id,
            status=RerunStatus.QUEUED,
        )
        self._session_store.update_session(
            session.job_id,
            session.session_id,
            active_rerun_id=rerun.rerun_id,
            latest_rerun_id=rerun.rerun_id,
        )
        asyncio.create_task(
            self._execute_rerun(session.job_id, session.session_id, rerun.rerun_id)
        )
        return rerun.rerun_id

    async def _execute_rerun(
        self, job_id: str, session_id: str, rerun_id: str
    ) -> None:
        """Background task: retarget -> evaluate -> package -> render.

        Persists status transitions, artifact manifests, and session pointers
        through the rerun store and session store. On failure the error is
        recorded and the active_rerun_id pointer is cleared.
        """
        import asyncio as _asyncio

        loop = _asyncio.get_running_loop()
        rerun = self._rerun_store.get_rerun(job_id, session_id, rerun_id)

        try:
            self._rerun_store.update_rerun(
                job_id, session_id, rerun_id,
                status=RerunStatus.RUNNING,
            )

            pose_data = _load_pose_artifacts(job_id)
            if pose_data is None:
                raise RuntimeError(
                    "Cannot rerun: persisted pose artifacts not found for job"
                )

            from pipeline.evaluate import evaluate_trajectory
            from pipeline.package import package_lerobot
            from pipeline.render_sim import render_simulation_video
            from pipeline.retarget import retarget_to_robot

            profile = rerun.mapping_profile

            retarget_result = await loop.run_in_executor(
                None, retarget_to_robot, pose_data, settings.target_robot, profile
            )

            eval_result = await loop.run_in_executor(
                None,
                evaluate_trajectory,
                retarget_result["joint_trajectory"],
                settings.target_robot,
            )

            rerun_output_dir = (
                settings.jobs_dir
                / job_id
                / "output"
                / "mapping_sessions"
                / session_id
                / "reruns"
                / f"{rerun.version}_{rerun_id}"
                / "output"
            )
            rerun_output_dir.mkdir(parents=True, exist_ok=True)

            robot_dataset_dir = rerun_output_dir / "dataset_robot"
            metadata = {
                "job_id": job_id,
                "rerun_id": rerun_id,
                "robot": retarget_result["robot"],
                "quality": eval_result,
            }
            robot_pkg_result = await loop.run_in_executor(
                None,
                package_lerobot,
                retarget_result["joint_trajectory"],
                retarget_result["ee_trajectory"],
                metadata,
                str(robot_dataset_dir),
            )

            sim_video_path = rerun_output_dir / "simulation.mp4"
            await loop.run_in_executor(
                None,
                render_simulation_video,
                retarget_result["joint_trajectory"],
                sim_video_path,
            )

            artifacts = {
                "dataset_robot_dir": str(robot_dataset_dir),
                "simulation_video": str(sim_video_path),
                "evaluation": eval_result,
                "frame_count": retarget_result["frame_count"],
                "robot": retarget_result["robot"],
                "mapping_profile": retarget_result["mapping_profile"],
                "package": robot_pkg_result,
            }
            self._rerun_store.write_artifacts(rerun, artifacts)

            summary = (
                f"Rerun v{rerun.version} completed. "
                f"Quality: {eval_result['overall_grade']}, "
                f"{retarget_result['frame_count']} frames."
            )
            self._rerun_store.update_rerun(
                job_id, session_id, rerun_id,
                status=RerunStatus.COMPLETED,
                summary=summary,
                completed_at=datetime.now(timezone.utc),
            )
            self._session_store.update_session(
                job_id, session_id,
                latest_completed_rerun_id=rerun_id,
                active_rerun_id=None,
            )

        except Exception as exc:
            _logger.exception("Rerun %s failed", rerun_id)
            self._rerun_store.update_rerun(
                job_id, session_id, rerun_id,
                status=RerunStatus.FAILED,
                error=str(exc),
                completed_at=datetime.now(timezone.utc),
            )
            self._session_store.update_session(
                job_id, session_id,
                active_rerun_id=None,
            )


def _load_pose_artifacts(job_id: str) -> dict | None:
    """Load persisted pose artifacts from the job's work/pose directory.

    Returns None if the artifacts are missing or corrupt.
    """
    base = settings.jobs_dir / job_id / "work" / "pose"
    landmarks_path = base / "landmarks.npy"
    world_path = base / "world_landmarks.npy"
    confidence_path = base / "confidence.npy"
    if not (landmarks_path.exists() and world_path.exists() and confidence_path.exists()):
        return None
    try:
        landmarks = np.load(landmarks_path)
        world = np.load(world_path)
        confidence = np.load(confidence_path)
    except Exception:
        return None
    if world.size == 0:
        return None
    mask_path = base / "detected_frames_mask.npy"
    detected_mask = (
        np.load(mask_path) if mask_path.exists()
        else np.ones(world.shape[0], dtype=bool)
    )
    return {
        "landmarks": landmarks,
        "world_landmarks": world,
        "confidence": confidence,
        "detected_frames_mask": detected_mask,
    }
