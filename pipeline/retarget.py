"""Retarget skeleton to robot joint angles. Profile-driven mapping for MVP (no pinocchio)."""

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

_AXIS_INDEX: dict[str, int] = {"x": 0, "y": 1, "z": 2}


def _resolve_axis_expression(expr: str, mp_vec: np.ndarray) -> float:
    """Resolve a single axis mapping expression to a scalar.

    Parses expressions like '-z', 'x', '-y' and extracts the corresponding
    component from the MediaPipe world coordinate vector, applying sign
    inversion when the expression starts with '-'.
    """
    sign = -1.0 if expr.startswith("-") else 1.0
    axis = expr[-1]
    idx = _AXIS_INDEX[axis]
    return sign * float(mp_vec[idx])


def _transform_mediapipe_to_robot(wrist_world: np.ndarray, profile: MappingProfile) -> np.ndarray:
    """Transform MediaPipe world coords to robot base frame using axis mapping profile.

    The profile's axis_mapping defines how each robot axis maps to a MediaPipe
    axis expression (e.g. x='-z' means robot X = -MediaPipe Z, reflecting the
    default coordinate frame convention).
    """
    adjusted_world = wrist_world.copy()
    adjusted_world[2] *= profile.depth_scale

    am = profile.axis_mapping
    return np.array(
        [
            _resolve_axis_expression(am.x, adjusted_world),
            _resolve_axis_expression(am.y, adjusted_world),
            _resolve_axis_expression(am.z, adjusted_world),
        ]
    )


def _geometric_ik(ee_target: np.ndarray) -> np.ndarray:
    """Approximate 7-DOF IK using shoulder-elbow-wrist geometry.

    Args:
        ee_target: [x, y, z] end-effector position in robot base frame

    Returns:
        q: [7] joint angles in radians
    """
    x, y, z = ee_target

    shoulder_z = 0.333
    upper_arm = 0.316
    forearm = 0.384

    joint1 = np.arctan2(y, x)

    r_xy = np.sqrt(x**2 + y**2)
    z_rel = z - shoulder_z
    dist = np.sqrt(r_xy**2 + z_rel**2)

    dist = np.clip(dist, 0.01, upper_arm + forearm - 0.01)

    cos_elbow = (upper_arm**2 + forearm**2 - dist**2) / (2 * upper_arm * forearm)
    cos_elbow = np.clip(cos_elbow, -1.0, 1.0)
    joint3 = np.arccos(cos_elbow)

    shoulder_base_angle = np.arctan2(z_rel, r_xy) if dist > 0.01 else 0.0
    cos_shoulder = (upper_arm**2 + dist**2 - forearm**2) / (2 * upper_arm * dist)
    cos_shoulder = np.clip(cos_shoulder, -1.0, 1.0)
    shoulder_inner = np.arccos(cos_shoulder)
    joint2 = -(shoulder_base_angle - shoulder_inner)

    joint4 = -np.pi / 2

    joint5 = 0.0

    joint6 = np.pi / 4

    joint7 = 0.0

    q = np.array([joint1, joint2, joint3, joint4, joint5, joint6, joint7])
    return q


def _clamp_to_limits(q: np.ndarray) -> np.ndarray:
    """Clamp joint angles to Franka Panda limits."""
    limits = np.array(FRANKA_PANDA_JOINT_LIMITS)
    return np.clip(q, limits[:, 0], limits[:, 1])


_Z_CLAMP_MIN = 0.05
_Z_CLAMP_MAX = 1.0


def _apply_profile_transforms(
    wrist_mp: np.ndarray,
    profile: MappingProfile,
) -> np.ndarray:
    """Apply the full position transform chain from a mapping profile.

    Order: depth scale (MediaPipe Z) → axis mapping → workspace scale → optional z clamp.
    """
    ee_robot = _transform_mediapipe_to_robot(wrist_mp, profile)

    ee_robot *= profile.workspace_scale

    if profile.z_clamp_enabled:
        ee_robot[2] = np.clip(ee_robot[2], _Z_CLAMP_MIN, _Z_CLAMP_MAX)

    return ee_robot


def _compute_wrist_landmark_index(profile: MappingProfile) -> int:
    """Resolve the actual landmark index from handedness and index fields.

    If ``profile.wrist_landmark_index`` is explicitly set, it takes precedence.
    Otherwise, handedness determines the index:
      - 'right' → 16
      - 'left'  → 15
    """
    if profile.wrist_landmark_index is not None:
        return profile.wrist_landmark_index
    profile_map: dict[str, int] = {"right": 16, "left": 15}
    return profile_map[profile.handedness]


def retarget_to_robot(
    pose_data: dict,
    robot: str = "franka_panda",
    profile: MappingProfile | None = None,
) -> dict:
    """Retarget 3D skeleton to robot joint trajectory with profile-driven mapping.

    Uses simplified geometric mapping:
    - Extract wrist trajectory from the landmark specified in profile
    - Transform via axis mapping and scaling from profile
    - Derive 7-DOF joint angles via geometric approximation

    Args:
        pose_data: Output of extract_pose_from_video()
        robot: Target robot name (only "franka_panda" supported in MVP)
        profile: Mapping profile controlling coordinate transform, scale,
                 handedness, and clamping. Uses default profile if None.

    Returns:
        Dict with keys:
            - joint_trajectory: np.ndarray of shape [T, 7] (joint angles in radians)
            - ee_trajectory: np.ndarray of shape [T, 3] (end-effector positions)
            - frame_count: int
            - robot: str
            - mapping_profile: dict (the serialized profile used)
    """
    if robot != "franka_panda":
        raise ValueError(f"Unsupported robot: {robot}. Only 'franka_panda' is supported in MVP.")

    if profile is None:
        profile = MappingProfile()

    world_landmarks = pose_data["world_landmarks"]
    if world_landmarks.shape[0] == 0:
        raise ValueError("No pose landmarks found in pose_data.")

    landmark_idx = _compute_wrist_landmark_index(profile)
    n_frames = world_landmarks.shape[0]

    joint_trajectory = np.zeros((n_frames, 7))
    ee_trajectory = np.zeros((n_frames, 3))

    for t in range(n_frames):
        wrist_mp = world_landmarks[t, landmark_idx]
        ee_scaled = _apply_profile_transforms(wrist_mp, profile)

        q = _geometric_ik(ee_scaled)
        q = _clamp_to_limits(q)

        joint_trajectory[t] = q
        ee_trajectory[t] = ee_scaled

    return {
        "joint_trajectory": joint_trajectory,
        "ee_trajectory": ee_trajectory,
        "frame_count": n_frames,
        "robot": robot,
        "mapping_profile": profile.model_dump(),
    }
