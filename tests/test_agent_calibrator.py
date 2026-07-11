"""Tests for the agent-as-annotator calibration pipeline.

These tests pin down the behavior of the three agent roles:
- ``handedness_detector`` — picks the dominant arm from wrist motion energy.
- ``calibration_reviewer`` — proposes a corrected MappingProfile when the
  baseline looks wrong; returns no_change when it looks right.
- ``sanity_checker`` — refuses to package a mock / unit-cube pose or a trajectory
  pinned against joint limits.
"""

from __future__ import annotations

import numpy as np

from domain.enums import QualityGrade
from domain.mapping import MappingProfile
from pipeline.agent_calibrator import (
    calibration_reviewer,
    handedness_detector,
    sanity_checker,
)
from pipeline.retarget import FRANKA_PANDA_JOINT_LIMITS


def _build_pose(
    right_wrist_traj: np.ndarray,
    left_wrist_traj: np.ndarray,
) -> dict:
    """Build a minimal pose dict with explicit wrist trajectories for both arms."""
    n = right_wrist_traj.shape[0]
    world = np.zeros((n, 33, 3), dtype=np.float32)
    # Shoulders roughly at the body origin, above the robot base.
    world[:, 11, :] = [0.0, 0.0, 0.3]  # left shoulder
    world[:, 12, :] = [0.0, 0.0, 0.3]  # right shoulder
    # Elbows fixed.
    world[:, 13, :] = [-0.25, 0.0, 0.3]
    world[:, 14, :] = [0.25, 0.0, 0.3]
    world[:, 15, :] = left_wrist_traj
    world[:, 16, :] = right_wrist_traj
    conf = np.ones((n, 33), dtype=np.float32)
    return {
        "landmarks": world.copy(),
        "world_landmarks": world,
        "confidence": conf,
        "frame_count": n,
        "detected_frame_count": n,
        "detection_rate": 1.0,
        "detected_frames_mask": np.ones(n, dtype=bool),
    }


def test_handedness_detector_picks_right_when_right_moves_more() -> None:
    """The right wrist moves more than the left, so the agent says 'right'."""
    t = np.linspace(0, 1, 30)
    right = np.stack([0.5 + 0.2 * t, np.zeros_like(t), np.full_like(t, 0.2)], axis=1)
    left = np.stack([-0.5 * np.ones_like(t), np.zeros_like(t), np.full_like(t, 0.2)], axis=1)
    pose = _build_pose(right, left)

    result = handedness_detector(pose)
    assert result["handedness"] == "right"
    assert result["confidence"] > 0.5
    assert result["verdict"] == "handedness_detected"


def test_handedness_detector_picks_left_when_left_moves_more() -> None:
    t = np.linspace(0, 1, 30)
    right = np.stack([0.5 * np.ones_like(t), np.zeros_like(t), np.full_like(t, 0.2)], axis=1)
    left = np.stack([-0.5 - 0.2 * t, np.zeros_like(t), np.full_like(t, 0.2)], axis=1)
    pose = _build_pose(right, left)

    result = handedness_detector(pose)
    assert result["handedness"] == "left"
    assert result["confidence"] > 0.5


def test_handedness_detector_handles_empty_input() -> None:
    pose = {
        "world_landmarks": np.empty((0, 33, 3), dtype=np.float32),
        "confidence": np.empty((0, 33), dtype=np.float32),
    }
    result = handedness_detector(pose)
    assert result["verdict"] == "no_change"
    assert result["confidence"] == 0.0


def test_calibration_reviewer_returns_no_change_for_clean_run() -> None:
    """A clean RED-less run returns no_change and the baseline profile."""
    t = np.linspace(0, 1, 30)
    right = np.stack([0.5 + 0.1 * t, np.zeros_like(t), np.full_like(t, 0.2)], axis=1)
    left = np.stack([-0.5 * np.ones_like(t), np.zeros_like(t), np.full_like(t, 0.2)], axis=1)
    pose = _build_pose(right, left)

    result = calibration_reviewer(pose, retarget_metrics={"overall_grade": "green"})
    assert result["verdict"] == "no_change"
    assert result["mapping_profile"]["handedness"] == "right"


def test_calibration_reviewer_flips_handedness_with_high_confidence() -> None:
    """When the left arm clearly moves more, the agent proposes left-handedness."""
    t = np.linspace(0, 1, 30)
    right = np.stack([0.5 * np.ones_like(t), np.zeros_like(t), np.full_like(t, 0.2)], axis=1)
    left = np.stack([-0.5 - 0.2 * t, np.zeros_like(t), np.full_like(t, 0.2)], axis=1)
    pose = _build_pose(right, left)

    result = calibration_reviewer(pose, retarget_metrics={"overall_grade": "green"})
    assert result["verdict"] == "rerun_with_profile"
    assert result["mapping_profile"]["handedness"] == "left"
    assert "handedness" in result["adjustments"]


def test_calibration_reviewer_shrinks_workspace_when_pressed_against_limits() -> None:
    """When the IK trajectory is clamped on most frames, the agent reduces scale."""
    t = np.linspace(0, 1, 30)
    right = np.stack([0.5 + 0.1 * t, np.zeros_like(t), np.full_like(t, 0.2)], axis=1)
    left = np.stack([-0.5 * np.ones_like(t), np.zeros_like(t), np.full_like(t, 0.2)], axis=1)
    pose = _build_pose(right, left)
    metrics = {
        "overall_grade": QualityGrade.RED.value,
        "limit_pressure_ratio": 0.7,
    }

    result = calibration_reviewer(pose, retarget_metrics=metrics)
    assert result["verdict"] == "rerun_with_profile"
    profile = result["mapping_profile"]
    assert profile["workspace_scale"] < MappingProfile().workspace_scale
    assert profile["z_clamp_enabled"] is True


def test_sanity_checker_accepts_metric_pose_within_limits() -> None:
    """A clean metric pose + in-limits trajectory is accepted."""
    t = np.linspace(0, 1, 30)
    right = np.stack([0.5 + 0.1 * t, np.zeros_like(t), np.full_like(t, 0.2)], axis=1)
    left = np.stack([-0.5 * np.ones_like(t), np.zeros_like(t), np.full_like(t, 0.2)], axis=1)
    pose = _build_pose(right, left)
    trajectory = np.zeros((30, 7), dtype=np.float32)
    # All joints safely away from limits.
    trajectory[:, 0] = 0.1
    trajectory[:, 1] = -0.2
    trajectory[:, 2] = 1.0
    trajectory[:, 3] = -1.2
    trajectory[:, 4] = 0.1
    trajectory[:, 5] = 1.0
    trajectory[:, 6] = 0.0

    result = sanity_checker(pose, trajectory, franka_limits=FRANKA_PANDA_JOINT_LIMITS)
    assert result["verdict"] == "ok"


def test_sanity_checker_rejects_unit_cube_pose() -> None:
    """The old broken mock distribution (max abs <= 1) is rejected."""
    rng = np.random.RandomState(7)
    world = rng.rand(20, 33, 3).astype(np.float32)  # all in [0, 1]
    pose = {
        "world_landmarks": world,
        "confidence": np.ones((20, 33), dtype=np.float32),
    }
    trajectory = np.zeros((20, 7), dtype=np.float32)
    result = sanity_checker(pose, trajectory)
    assert result["verdict"] == "reject"
    assert any("unit-cube" in issue for issue in result["issues"])


def test_sanity_checker_rejects_trajectory_pinned_to_limits() -> None:
    """A trajectory sitting on a joint limit on >40% of frames is rejected."""
    world = np.full((20, 33, 3), 0.3, dtype=np.float32)
    pose = {
        "world_landmarks": world,
        "confidence": np.ones((20, 33), dtype=np.float32),
    }
    # All frames at joint 1 upper limit (1.7628).
    trajectory = np.zeros((20, 7), dtype=np.float32)
    trajectory[:, 1] = FRANKA_PANDA_JOINT_LIMITS[1][1]
    result = sanity_checker(pose, trajectory, franka_limits=FRANKA_PANDA_JOINT_LIMITS)
    assert result["verdict"] == "reject"
    assert any("limits" in issue for issue in result["issues"])


def test_sanity_checker_rejects_nan_trajectory() -> None:
    """NaN in the trajectory is rejected."""
    world = np.full((20, 33, 3), 0.3, dtype=np.float32)
    pose = {
        "world_landmarks": world,
        "confidence": np.ones((20, 33), dtype=np.float32),
    }
    trajectory = np.zeros((20, 7), dtype=np.float32)
    trajectory[5, 0] = np.nan
    result = sanity_checker(pose, trajectory)
    assert result["verdict"] == "reject"


def test_sanity_checker_rejects_all_zero_trajectory() -> None:
    """A trajectory with no motion at all is rejected."""
    world = np.full((20, 33, 3), 0.3, dtype=np.float32)
    pose = {
        "world_landmarks": world,
        "confidence": np.ones((20, 33), dtype=np.float32),
    }
    trajectory = np.zeros((20, 7), dtype=np.float32)
    result = sanity_checker(pose, trajectory)
    assert result["verdict"] == "reject"
    assert any("all zeros" in issue for issue in result["issues"])
