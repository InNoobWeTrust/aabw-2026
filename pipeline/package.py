"""Package trajectories as LeRobot-style datasets (Parquet + metadata).

This module supports both robot-joint datasets and intermediate skeleton-stage
exports using the same tabular contract: ``observation.state`` contains the
current state vector and ``action`` contains the next-step target vector.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


def package_lerobot(
    joint_trajectory: np.ndarray,
    ee_trajectory: np.ndarray,
    metadata: dict,
    output_dir: str | Path,
) -> dict:
    """Package a retargeted robot trajectory as a LeRobot dataset."""
    return package_state_action_dataset(
        state_trajectory=joint_trajectory,
        action_trajectory=None,
        metadata=metadata,
        output_dir=output_dir,
        robot=metadata.get("robot", "franka_panda"),
        fps=10,
        stats_prefix="joint",
        extras={"ee_trajectory_shape": list(ee_trajectory.shape)},
    )


def package_lerobot_skeleton(
    skeleton_trajectory: np.ndarray,
    metadata: dict,
    output_dir: str | Path,
) -> dict:
    """Package flattened skeleton features as an intermediate dataset artifact."""
    return package_state_action_dataset(
        state_trajectory=skeleton_trajectory,
        action_trajectory=None,
        metadata=metadata,
        output_dir=output_dir,
        robot=metadata.get("robot", "human_skeleton"),
        fps=metadata.get("fps", 10),
        stats_prefix="feature",
        extras={
            "representation": metadata.get("representation", "human_skeleton"),
            "landmark_count": metadata.get("landmark_count", 33),
        },
    )


def package_state_action_dataset(
    *,
    state_trajectory: np.ndarray,
    action_trajectory: np.ndarray | None,
    metadata: dict,
    output_dir: str | Path,
    robot: str,
    fps: int,
    stats_prefix: str,
    extras: dict | None = None,
) -> dict:
    """Write a generic state/action dataset compatible with existing APIs."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    n_frames = int(state_trajectory.shape[0])
    if n_frames == 0:
        raise ValueError("Cannot package an empty trajectory.")

    if action_trajectory is None:
        action_trajectory = np.vstack([state_trajectory[1:], state_trajectory[-1:]])

    df = pd.DataFrame(
        {
            "observation.state": [state_trajectory[t].tolist() for t in range(n_frames)],
            "action": [action_trajectory[t].tolist() for t in range(n_frames)],
            "episode_index": [0] * n_frames,
            "frame_index": list(range(n_frames)),
            "timestamp": [t / float(fps) for t in range(n_frames)],
        }
    )

    parquet_path = output_dir / "episode_000000.parquet"
    df.to_parquet(str(parquet_path))

    meta = {
        "fps": fps,
        "robot_type": robot,
        "episodes": 1,
        "total_frames": n_frames,
        "metadata": metadata,
    }
    if extras:
        meta.update(extras)
    meta_path = output_dir / "meta.json"
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    stats = {}
    for j in range(state_trajectory.shape[1]):
        col = state_trajectory[:, j]
        stats[f"{stats_prefix}_{j}"] = {
            "min": float(np.min(col)),
            "max": float(np.max(col)),
            "mean": float(np.mean(col)),
            "std": float(np.std(col)),
        }
    stats_path = output_dir / "stats.json"
    stats_path.write_text(json.dumps(stats, indent=2), encoding="utf-8")

    files = [str(parquet_path), str(meta_path), str(stats_path)]
    return {
        "output_dir": str(output_dir),
        "files": files,
        "frame_count": n_frames,
        "robot": robot,
    }
