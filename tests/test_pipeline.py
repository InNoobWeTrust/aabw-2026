"""Smoke tests for deterministic pipeline helpers."""

from __future__ import annotations

import numpy as np

from pipeline.evaluate import evaluate_trajectory


def test_evaluate_trajectory_returns_red_for_empty_input() -> None:
    """Empty trajectories should fail closed with a red quality grade."""
    result = evaluate_trajectory(np.empty((0, 7)))

    assert result["overall_grade"] == "red"
    assert result["completeness_ratio"] == 0.0
    assert result["nan_count"] == 0


def test_evaluate_trajectory_returns_expected_metrics_for_constant_signal() -> None:
    """A constant in-range trajectory should have zero jumps and non-red quality."""
    valid_pose = np.array([0.0, 0.0, 0.0, -0.5, 0.0, 0.5, 0.0])
    trajectory = np.tile(valid_pose, (300, 1))

    result = evaluate_trajectory(trajectory)

    assert result["joint_limit_violations"] == 0
    assert result["nan_count"] == 0
    assert result["sudden_jump_count"] == 0
    assert result["overall_grade"] in {"green", "yellow"}


def test_compute_panda_fk_home_pose() -> None:
    """Test Panda forward kinematics computation for all-zeros joint angles."""
    import pinocchio as pin

    from pipeline.render_sim import compute_panda_fk, find_panda_urdf

    urdf_path = find_panda_urdf()
    model = pin.buildModelFromUrdf(str(urdf_path))
    data = model.createData()

    q = np.zeros(7)
    pts = compute_panda_fk(q, model, data)

    # Output shape should be [10, 3] (10 keypoints in 3D)
    assert pts.shape == (10, 3)

    # Base is at origin
    assert np.allclose(pts[0], [0, 0, 0])

    # End effector matches accumulated translations
    expected_ee = np.array([0.088, 0.0, 0.8226])
    assert np.allclose(pts[-1], expected_ee, atol=1e-3)


def test_render_simulation_video(tmp_path) -> None:
    """Test that rendering a trajectory to video generates a file."""
    from pipeline.render_sim import render_simulation_video

    # 10 frames of constant pose
    valid_pose = np.array([0.0, 0.0, 0.0, -0.5, 0.0, 0.5, 0.0])
    trajectory = np.tile(valid_pose, (10, 1))

    video_file = tmp_path / "simulation.mp4"
    render_simulation_video(trajectory, video_file, fps=10, width=320, height=240)

    assert video_file.exists()
    assert video_file.stat().st_size > 0
