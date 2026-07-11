"""Retarget skeleton to robot joint angles using two-link analytic IK on
shoulder→elbow→wrist 3D vectors.

The geometric solver derives joint angles from the actual MediaPipe segment
vectors, not from a fixed robot base origin. This means the solver works in the
subject's own metric scale and naturally adapts to body size. The ``MappingProfile``
is only used for axis permutation and a final workspace-scale transform that maps
the human reach into the robot's reach (Franka Panda ≈ 0.855 m).
"""

from __future__ import annotations

import numpy as np

from domain.mapping import MappingProfile

FRANKA_PANDA_JOINT_LIMITS = [
    (-2.8973, 2.8973),
    (-1.7628, 1.7628),
    (-2.8973, 2.8973),
    (-3.0718, -0.0698),
    (-2.8973, 2.8973),
    (-0.0175, 3.7525),
    (-2.8973, 2.8973),
]

FRANKA_PANDA_REACH = 0.855
FRANKA_PANDA_SHOULDER_HEIGHT = 0.333  # base of arm above base origin (m)

# MediaPipe Pose landmark indices for the right arm chain.
_RIGHT_SHOULDER = 12
_RIGHT_ELBOW = 14
_RIGHT_WRIST = 16
_LEFT_SHOULDER = 11
_LEFT_ELBOW = 13
_LEFT_WRIST = 15

_AXIS_INDEX: dict[str, int] = {"x": 0, "y": 1, "z": 2}

# Minimum segment length to trust the IK solution (m). Below this the arm
# configuration is degenerate and we fall back to the previous valid frame.
_MIN_SEGMENT_LENGTH = 0.05


def _resolve_axis_expression(expr: str, vec: np.ndarray) -> float:
    """Resolve a single axis mapping expression (e.g. '-z') to a scalar."""
    sign = -1.0 if expr.startswith("-") else 1.0
    axis = expr[-1]
    idx = _AXIS_INDEX[axis]
    return sign * float(vec[idx])


def _transform_to_robot_axes(wrist_world: np.ndarray, profile: MappingProfile) -> np.ndarray:
    """Apply axis permutation (MediaPipe → robot base) to a metric world point."""
    adjusted = wrist_world.copy()
    adjusted[2] *= profile.depth_scale
    am = profile.axis_mapping
    return np.array(
        [
            _resolve_axis_expression(am.x, adjusted),
            _resolve_axis_expression(am.y, adjusted),
            _resolve_axis_expression(am.z, adjusted),
        ]
    )


def _two_link_ik(
    shoulder_robot: np.ndarray,
    elbow_robot: np.ndarray,
    wrist_robot: np.ndarray,
) -> np.ndarray:
    """Solve Franka Panda joint angles from shoulder/elbow/wrist positions in robot frame.

    Strategy (analytic, 7-DOF approximation):
    - joint1 (base yaw) = atan2(wrist_y, wrist_x) so the arm points at the wrist.
    - Treat the planar chain (shoulder, elbow, wrist) projected onto the plane
      containing those three points. joint2 is the shoulder pitch derived from
      the law of cosines, joint3 from the elbow angle.
    - The remaining joints (4–7) are set to a fixed neutral pose (Franka "ready"
      posture) that keeps the end-effector roughly aligned with the wrist.

    Returns a 7-vector of joint angles in radians. Caller is responsible for
    clamping to joint limits.
    """
    # Base yaw: arm rotates so wrist is in the XZ plane.
    joint1 = float(np.arctan2(wrist_robot[1], wrist_robot[0]))

    # Planar projection: rotate the arm chain by -joint1 around Z so it lies in
    # the XZ plane (Y is the arm's vertical).
    cos1, sin1 = np.cos(-joint1), np.sin(-joint1)

    def to_xz(p: np.ndarray) -> np.ndarray:
        return np.array([p[0] * cos1 - p[1] * sin1, p[2]])

    s = to_xz(shoulder_robot)
    e = to_xz(elbow_robot)
    w = to_xz(wrist_robot)

    upper = e - s
    lower = w - e
    upper_len = float(np.linalg.norm(upper))
    lower_len = float(np.linalg.norm(lower))
    if upper_len < _MIN_SEGMENT_LENGTH or lower_len < _MIN_SEGMENT_LENGTH:
        # Degenerate configuration: return a safe neutral pose.
        return np.array([joint1, 0.0, np.pi / 2, -np.pi / 2, 0.0, np.pi / 2, 0.0])

    # Shoulder pitch: angle of the upper arm above horizontal in the XZ plane.
    # In robot frame, +Z is up. We measure from +X axis (forward) toward +Z (up).
    shoulder_angle = float(np.arctan2(upper[1], upper[0]))
    # Elbow angle: angle between upper arm and lower arm (straight = 0, folded = +pi).
    cos_elbow = float(np.dot(-upper, lower) / (upper_len * lower_len))
    cos_elbow = np.clip(cos_elbow, -1.0, 1.0)
    elbow_interior = float(np.arccos(cos_elbow))
    # Franka joint2 measures from horizontal; sign convention is reversed.
    joint2 = -(shoulder_angle)
    joint3 = np.pi - elbow_interior

    # Neutral wrist orientation (Franka "ready" pose).
    joint4 = -np.pi / 2
    joint5 = 0.0
    joint6 = np.pi / 2
    joint7 = 0.0

    return np.array([joint1, joint2, joint3, joint4, joint5, joint6, joint7])


def _clamp_to_limits(q: np.ndarray) -> np.ndarray:
    """Clamp joint angles to Franka Panda limits."""
    limits = np.array(FRANKA_PANDA_JOINT_LIMITS)
    return np.clip(q, limits[:, 0], limits[:, 1])


_Z_CLAMP_MIN = 0.05
_Z_CLAMP_MAX = 1.0


def _apply_workspace_transform(ee_robot: np.ndarray, profile: MappingProfile) -> np.ndarray:
    """Scale the human reach into the robot's workspace and optionally clamp Z."""
    ee_robot = ee_robot * profile.workspace_scale
    if profile.z_clamp_enabled:
        ee_robot[2] = np.clip(ee_robot[2], _Z_CLAMP_MIN, _Z_CLAMP_MAX)
    return ee_robot


def _resolve_arm_indices(profile: MappingProfile) -> tuple[int, int, int]:
    """Return (shoulder_idx, elbow_idx, wrist_idx) for the active arm."""
    if profile.handedness == "right":
        return _RIGHT_SHOULDER, _RIGHT_ELBOW, _RIGHT_WRIST
    return _LEFT_SHOULDER, _LEFT_ELBOW, _LEFT_WRIST


def _wrist_index(profile: MappingProfile) -> int:
    if profile.wrist_landmark_index is not None:
        return profile.wrist_landmark_index
    return _RIGHT_WRIST if profile.handedness == "right" else _LEFT_WRIST


def retarget_to_robot(
    pose_data: dict,
    robot: str = "franka_panda",
    profile: MappingProfile | None = None,
) -> dict:
    """Retarget 3D skeleton to robot joint trajectory with profile-driven mapping.

    Uses two-link analytic IK on the actual shoulder→elbow→wrist 3D vectors
    extracted from MediaPipe world landmarks. This solver is metric, adapts to
    the subject's body size, and does not require a hardcoded shoulder height.

    Args:
        pose_data: Output of ``extract_pose_from_video()``.
        robot: Target robot name (only ``"franka_panda"`` supported in MVP).
        profile: Mapping profile controlling axis permutation, scaling, and
            handedness. Uses the default profile if None.

    Returns:
        Dict with keys:
            - ``joint_trajectory``: np.ndarray of shape [T, 7] (joint angles in radians).
            - ``ee_trajectory``: np.ndarray of shape [T, 3] (end-effector positions).
            - ``frame_count``: int
            - ``robot``: str
            - ``mapping_profile``: dict (the serialized profile used).
    """
    if robot != "franka_panda":
        raise ValueError(f"Unsupported robot: {robot}. Only 'franka_panda' is supported in MVP.")

    if profile is None:
        profile = MappingProfile()

    world_landmarks = pose_data["world_landmarks"]
    if world_landmarks.shape[0] == 0:
        raise ValueError("No pose landmarks found in pose_data.")

    shoulder_idx, elbow_idx, wrist_idx = _resolve_arm_indices(profile)
    n_frames = world_landmarks.shape[0]
    detected_mask = pose_data.get("detected_frames_mask")

    joint_trajectory = np.zeros((n_frames, 7))
    ee_trajectory = np.zeros((n_frames, 3))
    last_valid_q = np.array([0.0, 0.0, np.pi / 2, -np.pi / 2, 0.0, np.pi / 2, 0.0])

    for t in range(n_frames):
        # Skip IK for undetected frames: carry the last valid solution forward.
        if detected_mask is not None and not bool(detected_mask[t]):
            joint_trajectory[t] = last_valid_q
            ee_trajectory[t] = ee_trajectory[t - 1] if t > 0 else np.zeros(3)
            continue

        shoulder_world = world_landmarks[t, shoulder_idx]
        elbow_world = world_landmarks[t, elbow_idx]
        wrist_world = world_landmarks[t, wrist_idx]

        shoulder_robot = _apply_workspace_transform(
            _transform_to_robot_axes(shoulder_world, profile), profile
        )
        elbow_robot = _apply_workspace_transform(
            _transform_to_robot_axes(elbow_world, profile), profile
        )
        wrist_robot = _apply_workspace_transform(
            _transform_to_robot_axes(wrist_world, profile), profile
        )

        q = _two_link_ik(shoulder_robot, elbow_robot, wrist_robot)
        q = _clamp_to_limits(q)

        joint_trajectory[t] = q
        ee_trajectory[t] = wrist_robot
        last_valid_q = q

    return {
        "joint_trajectory": joint_trajectory,
        "ee_trajectory": ee_trajectory,
        "frame_count": n_frames,
        "robot": robot,
        "mapping_profile": profile.model_dump(),
        "wrist_landmark_index": _wrist_index(profile),
    }
