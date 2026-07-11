"""Smoke tests for deterministic pipeline helpers."""

from __future__ import annotations

import numpy as np
import pandas as pd

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


def test_static_checks_missing_files(tmp_path) -> None:
    """Static checks should fail when required files are missing."""
    from pipeline.staged_review import run_static_checks

    res = run_static_checks(tmp_path)
    assert res["status"] == "failed"
    assert any(c["name"] == "Dataset Files Existence" and not c["passed"] for c in res["checks"])


def test_static_checks_valid_data(tmp_path) -> None:
    """Static checks should pass when all files are correctly structured."""
    import json

    from pipeline.staged_review import run_static_checks

    # Create dummy files
    meta = {
        "fps": 10,
        "robot_type": "franka_panda",
        "episodes": 1,
        "total_frames": 5,
    }
    (tmp_path / "meta.json").write_text(json.dumps(meta))

    stats = {f"joint_{i}": {"min": 0.0, "max": 1.0} for i in range(7)}
    (tmp_path / "stats.json").write_text(json.dumps(stats))

    df = pd.DataFrame(
        {
            "observation.state": [[0.0] * 7] * 5,
            "action": [[0.0] * 7] * 5,
            "timestamp": [0.1 * i for i in range(5)],
            "episode_index": [0] * 5,
            "frame_index": list(range(5)),
        }
    )
    df.to_parquet(str(tmp_path / "episode_000000.parquet"))

    res = run_static_checks(tmp_path)
    assert res["status"] == "passed"
    assert all(c["passed"] for c in res["checks"])


def test_generate_ai_review() -> None:
    """AI Review should generate valid markdown with different statuses based on metrics."""
    from pipeline.staged_review import generate_ai_review

    eval_green = {
        "overall_grade": "green",
        "joint_limit_violations": 0,
        "nan_count": 0,
        "max_velocity": 0.5,
        "mean_jerk": 0.2,
        "sudden_jump_count": 0,
        "completeness_ratio": 1.0,
    }
    trajectory = np.zeros((10, 7))
    report_green = generate_ai_review(eval_green, trajectory)
    assert "APPROVED" in report_green
    assert "✅ Pass" in report_green

    eval_red = {
        "overall_grade": "red",
        "joint_limit_violations": 2,
        "nan_count": 5,
        "max_velocity": 3.5,
        "mean_jerk": 2.5,
        "sudden_jump_count": 8,
        "completeness_ratio": 0.5,
    }
    report_red = generate_ai_review(eval_red, trajectory)
    assert "REJECTED" in report_red
    assert "❌ Fail" in report_red
    assert "Resolve joint limit violations" in report_red
    assert "Eliminate NaN values" in report_red
