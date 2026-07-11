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
