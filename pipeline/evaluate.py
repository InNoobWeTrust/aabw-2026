"""Quality evaluation: deterministic metrics on joint trajectory."""

import numpy as np

from domain.enums import QualityGrade

_JOINT_LIMITS = [
    (-2.8973, 2.8973),
    (-1.7628, 1.7628),
    (-2.8973, 2.8973),
    (-3.0718, -0.0698),
    (-2.8973, 2.8973),
    (-0.0175, 3.7525),
    (-2.8973, 2.8973),
]


def _count_limit_violations(q):
    count = 0
    for j, (lo, hi) in enumerate(_JOINT_LIMITS):
        count += int(np.any(q[:, j] < lo) or np.any(q[:, j] > hi))
    return count


def evaluate_trajectory(joint_trajectory: np.ndarray, robot: str = "franka_panda") -> dict:
    """Compute quality metrics for a joint trajectory.

    Args:
        joint_trajectory: [T, 7] joint angles in radians
        robot: Robot name for joint limit lookup

    Returns:
        Dict with metrics:
            - joint_limit_violations: int
            - nan_count: int
            - max_velocity: float (rad/frame)
            - mean_jerk: float
            - sudden_jump_count: int (>0.3 rad/frame threshold)
            - completeness_ratio: float
            - overall_grade: "green" | "yellow" | "red"
    """
    if joint_trajectory.ndim != 2 or joint_trajectory.shape[0] == 0:
        return {
            "joint_limit_violations": 0,
            "nan_count": 0,
            "max_velocity": 0.0,
            "mean_jerk": 0.0,
            "sudden_jump_count": 0,
            "completeness_ratio": 0.0,
            "overall_grade": QualityGrade.RED.value,
        }

    n_frames, _ = joint_trajectory.shape

    velocities = np.diff(joint_trajectory, axis=0)
    accelerations = np.diff(velocities, axis=0)
    jerks = np.diff(accelerations, axis=0)

    joint_limit_violations = _count_limit_violations(joint_trajectory)
    nan_count = int(np.sum(np.isnan(joint_trajectory)))

    max_velocity = float(np.max(np.abs(velocities))) if velocities.size > 0 else 0.0

    mean_jerk = float(np.mean(np.abs(jerks))) if jerks.size > 0 else 0.0

    sudden_jump_count = (
        int(np.sum(np.abs(velocities) > 0.3, axis=0).max()) if velocities.size > 0 else 0
    )

    expected_frames = 300
    completeness_ratio = n_frames / expected_frames

    grades: list[QualityGrade] = []
    grades.append(QualityGrade.RED if joint_limit_violations > 0 else QualityGrade.GREEN)
    grades.append(QualityGrade.RED if nan_count > 0 else QualityGrade.GREEN)
    grades.append(
        QualityGrade.GREEN
        if max_velocity < 2.0
        else QualityGrade.YELLOW
        if max_velocity <= 3.0
        else QualityGrade.RED
    )
    grades.append(
        QualityGrade.GREEN
        if sudden_jump_count < 5
        else QualityGrade.YELLOW
        if sudden_jump_count <= 15
        else QualityGrade.RED
    )
    grades.append(
        QualityGrade.GREEN
        if mean_jerk < 1.0
        else QualityGrade.YELLOW
        if mean_jerk <= 2.0
        else QualityGrade.RED
    )
    grades.append(
        QualityGrade.GREEN
        if completeness_ratio > 0.90
        else QualityGrade.YELLOW
        if completeness_ratio >= 0.75
        else QualityGrade.RED
    )

    if QualityGrade.RED in grades:
        overall_grade = QualityGrade.RED
    elif QualityGrade.YELLOW in grades:
        overall_grade = QualityGrade.YELLOW
    else:
        overall_grade = QualityGrade.GREEN

    return {
        "joint_limit_violations": joint_limit_violations,
        "nan_count": nan_count,
        "max_velocity": max_velocity,
        "mean_jerk": mean_jerk,
        "sudden_jump_count": sudden_jump_count,
        "completeness_ratio": round(completeness_ratio, 4),
        "overall_grade": overall_grade.value,
    }
