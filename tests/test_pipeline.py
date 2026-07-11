"""Smoke tests for deterministic pipeline helpers."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pandas as pd

from pipeline.evaluate import evaluate_trajectory
from pipeline.pose_artifacts import (
    _draw_pose_world,
    compute_pose_review_metrics,
    flatten_skeleton_features,
)


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
    """Retarget review should generate valid markdown with different statuses."""
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


def test_pose_review_metrics_and_flatten() -> None:
    """Pose metrics and flattening should preserve frame count and detect stability."""
    world = np.zeros((4, 33, 3), dtype=np.float32)
    world[:, 15, 0] = [0.0, 0.01, 0.02, 0.03]
    world[:, 16, 0] = [0.0, 0.02, 0.04, 0.06]
    pose_result = {
        "landmarks": world.copy(),
        "world_landmarks": world,
        "confidence": np.ones((4, 33), dtype=np.float32),
        "frame_count": 4,
        "detected_frame_count": 4,
        "detection_rate": 1.0,
        "detected_frames_mask": np.array([True, True, True, True]),
    }

    metrics = compute_pose_review_metrics(pose_result)
    flat = flatten_skeleton_features(pose_result)

    assert metrics["frame_count"] == 4
    assert metrics["detection_rate"] == 1.0
    assert "left_wrist" in metrics["keypoints"]
    assert flat.shape == (4, 99)


def test_draw_pose_world_preserves_downward_y_body_order() -> None:
    """World-space preview rendering should not flip a body upside down."""
    frame = np.zeros((120, 120, 3), dtype=np.uint8)
    points = np.zeros((33, 3), dtype=np.float32)
    confidence = np.ones((33,), dtype=np.float32)

    # MediaPipe-style downward Y ordering: head above shoulders above hips above ankles.
    points[0] = [0.0, -0.4, 0.0]  # nose
    points[11] = [-0.1, -0.2, 0.0]  # left shoulder
    points[12] = [0.1, -0.2, 0.0]  # right shoulder
    points[23] = [-0.05, 0.1, 0.0]  # left hip
    points[24] = [0.05, 0.1, 0.0]  # right hip
    points[27] = [-0.05, 0.5, 0.0]  # left ankle
    points[28] = [0.05, 0.5, 0.0]  # right ankle

    _draw_pose_world(frame, points, confidence, True, scale=60.0, tx=60.0, ty=60.0)

    nose_y = int(60.0 + points[0, 1] * 60.0)
    ankle_y = int(60.0 + points[27, 1] * 60.0)
    assert nose_y < ankle_y
    assert frame[nose_y, 60].any() or frame[nose_y, 59].any() or frame[nose_y, 61].any()
    assert frame[ankle_y, 57].any() or frame[ankle_y, 58].any() or frame[ankle_y, 59].any()


def test_package_lerobot_skeleton(tmp_path) -> None:
    """Skeleton packaging should emit the same core dataset file trio."""
    from pipeline.package import package_lerobot_skeleton

    features = np.random.RandomState(0).rand(5, 99).astype(np.float32)
    result = package_lerobot_skeleton(
        features,
        {
            "robot": "human_skeleton",
            "fps": 10,
            "representation": "mediapipe_world_landmarks_flattened",
            "landmark_count": 33,
        },
        tmp_path,
    )

    assert result["frame_count"] == 5
    assert (tmp_path / "episode_000000.parquet").exists()
    assert (tmp_path / "meta.json").exists()
    assert (tmp_path / "stats.json").exists()


def test_generate_pose_review() -> None:
    """Pose review should return structured markdown and verdict payload."""
    from pipeline.staged_review import generate_pose_review

    markdown, payload = generate_pose_review(
        {
            "detection_rate": 0.95,
            "average_visibility": 0.8,
            "missing_landmark_ratio": 0.05,
            "keypoints": {
                "left_wrist": {"temporal_jitter": 0.01},
                "right_wrist": {"temporal_jitter": 0.02},
            },
        },
        {
            "skeleton_overlay_video": "overlay.mp4",
            "skeleton_preview_video": "preview.mp4",
            "dataset_skeleton_dir": "dataset_skeleton",
        },
    )

    assert "Pose Extraction Review Report" in markdown
    assert payload["verdict"] == "approved"


def _write_test_video(path: Path, frame_count: int, seed: int) -> None:
    """Write a deterministic small MP4 test video with visible per-frame variation."""
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        10,
        (96, 72),
    )
    assert writer.isOpened(), f"Failed to open test video writer for {path}"

    try:
        for frame_idx in range(frame_count):
            frame = np.zeros((72, 96, 3), dtype=np.uint8)
            frame[:, :] = (
                (seed * 31 + frame_idx * 7) % 255,
                (seed * 47 + frame_idx * 11) % 255,
                (seed * 59 + frame_idx * 13) % 255,
            )
            cv2.putText(
                frame,
                f"{seed}:{frame_idx}",
                (5, 38),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )
            writer.write(frame)
    finally:
        writer.release()


def test_generate_mapping_context_samples_creates_synchronized_evidence_pack(tmp_path) -> None:
    """Calibration samples should use one shared frame index set across all four videos."""
    from pipeline.calibration_samples import generate_mapping_context_samples

    original = tmp_path / "original.mp4"
    overlay = tmp_path / "overlay.mp4"
    preview = tmp_path / "preview.mp4"
    simulation = tmp_path / "simulation.mp4"
    calibration_dir = tmp_path / "calibration"

    _write_test_video(original, frame_count=10, seed=1)
    _write_test_video(overlay, frame_count=10, seed=2)
    _write_test_video(preview, frame_count=10, seed=3)
    _write_test_video(simulation, frame_count=10, seed=4)

    manifest = generate_mapping_context_samples(
        original_video_path=original,
        skeleton_overlay_video_path=overlay,
        skeleton_preview_video_path=preview,
        robot_simulation_video_path=simulation,
        calibration_dir=calibration_dir,
        requested_sample_count=8,
    )

    assert manifest["sample_count"] == 8
    assert manifest["json_path"] == str(calibration_dir / "mapping_context_samples.json")
    assert (calibration_dir / "mapping_context_samples.json").exists()
    assert len(manifest["samples"]) == 8

    frame_indices = [sample["frame_index"] for sample in manifest["samples"]]
    assert frame_indices == sorted(frame_indices)
    assert len(set(frame_indices)) == len(frame_indices)
    assert frame_indices[0] == 0
    assert frame_indices[-1] == 9

    for sample in manifest["samples"]:
        assert set(sample["artifacts"]) == {"original", "overlay", "preview", "robot_simulation"}
        for artifact in sample["artifacts"].values():
            assert artifact["frame_index"] == sample["frame_index"]
            assert Path(artifact["image_path"]).exists()
            assert artifact["width"] == 96
            assert artifact["height"] == 72


def test_generate_mapping_context_samples_uses_shortest_video_length(tmp_path) -> None:
    """Synchronized sampling should clamp to the shortest available evidence video."""
    from pipeline.calibration_samples import generate_mapping_context_samples

    original = tmp_path / "original.mp4"
    overlay = tmp_path / "overlay.mp4"
    preview = tmp_path / "preview.mp4"
    simulation = tmp_path / "simulation.mp4"

    _write_test_video(original, frame_count=12, seed=1)
    _write_test_video(overlay, frame_count=12, seed=2)
    _write_test_video(preview, frame_count=6, seed=3)
    _write_test_video(simulation, frame_count=12, seed=4)

    manifest = generate_mapping_context_samples(
        original_video_path=original,
        skeleton_overlay_video_path=overlay,
        skeleton_preview_video_path=preview,
        robot_simulation_video_path=simulation,
        calibration_dir=tmp_path / "calibration",
        requested_sample_count=8,
    )

    frame_indices = [sample["frame_index"] for sample in manifest["samples"]]
    assert manifest["sample_count"] == 6
    assert frame_indices[0] == 0
    assert frame_indices[-1] == 5
