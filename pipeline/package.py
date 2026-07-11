"""Package joint trajectory as LeRobot-format dataset (Parquet + metadata)."""

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
    """Package trajectory as LeRobot dataset.

    Args:
        joint_trajectory: [T, 7] joint angles
        ee_trajectory: [T, 3] end-effector positions
        metadata: dict with task info, quality score, etc.
        output_dir: Directory to write dataset files

    Returns:
        Dict with keys:
            - output_dir: str
            - files: list of file paths created
            - frame_count: int
            - robot: str
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    n_frames = joint_trajectory.shape[0]
    robot = metadata.get("robot", "franka_panda")

    df = pd.DataFrame(
        {
            "observation.state": [joint_trajectory[t].tolist() for t in range(n_frames)],
            "action": [
                joint_trajectory[t + 1].tolist()
                if t < n_frames - 1
                else joint_trajectory[t].tolist()
                for t in range(n_frames)
            ],
            "episode_index": [0] * n_frames,
            "frame_index": list(range(n_frames)),
            "timestamp": [t / 10.0 for t in range(n_frames)],
        }
    )

    parquet_path = output_dir / "episode_000000.parquet"
    df.to_parquet(str(parquet_path))

    meta = {
        "fps": 10,
        "robot_type": robot,
        "episodes": 1,
        "total_frames": n_frames,
        "metadata": metadata,
    }
    meta_path = output_dir / "meta.json"
    meta_path.write_text(json.dumps(meta, indent=2))

    stats = {}
    for j in range(joint_trajectory.shape[1]):
        col = joint_trajectory[:, j]
        stats[f"joint_{j}"] = {
            "min": float(np.min(col)),
            "max": float(np.max(col)),
            "mean": float(np.mean(col)),
            "std": float(np.std(col)),
        }
    stats_path = output_dir / "stats.json"
    stats_path.write_text(json.dumps(stats, indent=2))

    files = [str(parquet_path), str(meta_path), str(stats_path)]

    return {
        "output_dir": str(output_dir),
        "files": files,
        "frame_count": n_frames,
        "robot": robot,
    }
