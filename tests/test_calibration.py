"""Tests for read-only mapping calibration heuristics and persistence contracts."""

from __future__ import annotations

from backend.calibration_service import build_mapping_calibration_factory
from domain.enums import CalibrationDecision, CalibrationVerdict


def test_mapping_calibration_factory_returns_baseline_ok_for_clean_run() -> None:
    """A strong baseline should remain baseline_ok with the existing mapping profile."""
    context = {
        "pose_metrics": {
            "detection_rate": 0.97,
            "missing_landmark_ratio": 0.04,
            "keypoints": {
                "right_wrist": {"temporal_jitter": 0.01},
            },
        },
        "retarget_metrics": {
            "overall_grade": "green",
            "joint_limit_violations": 0,
            "nan_count": 0,
            "max_velocity": 0.3,
            "mean_jerk": 0.15,
            "sudden_jump_count": 0,
            "completeness_ratio": 1.0,
        },
        "baseline_mapping_profile": {
            "profile_version": 1,
            "handedness": "right",
            "workspace_scale": 1.2214285714285714,
            "depth_scale": 1.0,
            "axis_mapping": {"x": "-z", "y": "-x", "z": "y"},
            "z_clamp_enabled": False,
            "position_only": True,
        },
        "mapping_context_samples": {"sample_count": 8, "samples": []},
    }

    payload = build_mapping_calibration_factory(context)()

    assert payload["decision"] == CalibrationDecision.BASELINE_OK.value
    assert payload["verdict"] == CalibrationVerdict.BASELINE_ACCEPTABLE.value
    assert payload["mapping_profile"]["depth_scale"] == 1.0
    assert payload["anchors"] == []


def test_mapping_calibration_factory_returns_rerun_profile_for_salvageable_motion() -> None:
    """A noisy but salvageable run should recommend rerun_with_profile."""
    context = {
        "pose_metrics": {
            "detection_rate": 0.88,
            "missing_landmark_ratio": 0.18,
            "keypoints": {
                "right_wrist": {"temporal_jitter": 0.08},
            },
        },
        "retarget_metrics": {
            "overall_grade": "yellow",
            "joint_limit_violations": 0,
            "nan_count": 0,
            "max_velocity": 1.6,
            "mean_jerk": 1.1,
            "sudden_jump_count": 6,
            "completeness_ratio": 0.92,
        },
        "baseline_mapping_profile": {
            "profile_version": 1,
            "handedness": "right",
            "workspace_scale": 1.2214285714285714,
            "depth_scale": 1.0,
            "axis_mapping": {"x": "-z", "y": "-x", "z": "y"},
            "z_clamp_enabled": False,
            "position_only": True,
        },
        "mapping_context_samples": {"sample_count": 8, "samples": []},
    }

    payload = build_mapping_calibration_factory(context)()

    assert payload["decision"] == CalibrationDecision.RERUN_WITH_PROFILE.value
    assert payload["verdict"] == CalibrationVerdict.ROBOT_MAPPING_SALVAGEABLE.value
    assert payload["mapping_profile"]["depth_scale"] < 1.0
    assert payload["mapping_profile"]["workspace_scale"] < 1.2214285714285714
    assert payload["confidence"] > 0.0
    assert payload["risks"]


def test_mapping_calibration_factory_returns_skeleton_only_when_robot_branch_is_not_salvageable() -> (
    None
):
    """Severely unstable robot mapping with usable pose should downgrade to skeleton_only."""
    context = {
        "pose_metrics": {
            "detection_rate": 0.73,
            "missing_landmark_ratio": 0.12,
            "keypoints": {
                "right_wrist": {"temporal_jitter": 0.04},
            },
        },
        "retarget_metrics": {
            "overall_grade": "red",
            "joint_limit_violations": 2,
            "nan_count": 0,
            "max_velocity": 3.2,
            "mean_jerk": 2.4,
            "sudden_jump_count": 14,
            "completeness_ratio": 0.61,
        },
        "baseline_mapping_profile": {
            "profile_version": 1,
            "handedness": "right",
            "workspace_scale": 1.2214285714285714,
            "depth_scale": 1.0,
            "axis_mapping": {"x": "-z", "y": "-x", "z": "y"},
            "z_clamp_enabled": False,
            "position_only": True,
        },
        "mapping_context_samples": {"sample_count": 8, "samples": []},
    }

    payload = build_mapping_calibration_factory(context)()

    assert payload["decision"] == CalibrationDecision.SKELETON_ONLY.value
    assert payload["verdict"] == CalibrationVerdict.SKELETON_ONLY.value
    assert payload["mapping_profile"] is None
    assert payload["anchors"] == []
