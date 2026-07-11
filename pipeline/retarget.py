"""Retarget skeleton to robot joint angles. Simplified mapping for MVP (no pinocchio)."""

import numpy as np

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


def _transform_mediapipe_to_robot(wrist_world):
    """Transform MediaPipe world coords to robot base frame.

    MediaPipe world: X=right, Y=up, Z=toward camera
    Robot base: X=forward, Y=left, Z=up
    """
    x_mp, y_mp, z_mp = wrist_world
    robot_x = -z_mp
    robot_y = -x_mp
    robot_z = y_mp
    return np.array([robot_x, robot_y, robot_z])


def _scale_to_workspace(ee_pos):
    """Scale human arm reach to robot workspace reach."""
    human_reach = 0.7
    scale = FRANKA_PANDA_REACH / human_reach
    return ee_pos * scale


def _geometric_ik(ee_target):
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


def _clamp_to_limits(q):
    """Clamp joint angles to Franka Panda limits."""
    limits = np.array(FRANKA_PANDA_JOINT_LIMITS)
    return np.clip(q, limits[:, 0], limits[:, 1])


def retarget_to_robot(pose_data: dict, robot: str = "franka_panda") -> dict:
    """Retarget 3D skeleton to robot joint trajectory.

    Uses simplified geometric mapping:
    - Extract right wrist trajectory (MediaPipe landmark 16)
    - Map XYZ position to robot workspace via scaling
    - Derive 7-DOF joint angles via geometric approximation

    Args:
        pose_data: Output of extract_pose_from_video()
        robot: Target robot name (only "franka_panda" supported in MVP)

    Returns:
        Dict with keys:
            - joint_trajectory: np.ndarray of shape [T, 7] (joint angles in radians)
            - ee_trajectory: np.ndarray of shape [T, 3] (end-effector positions)
            - frame_count: int
            - robot: str
    """
    if robot != "franka_panda":
        raise ValueError(f"Unsupported robot: {robot}. Only 'franka_panda' is supported in MVP.")

    world_landmarks = pose_data["world_landmarks"]
    if world_landmarks.shape[0] == 0:
        raise ValueError("No pose landmarks found in pose_data.")

    right_wrist = 16
    n_frames = world_landmarks.shape[0]

    joint_trajectory = np.zeros((n_frames, 7))
    ee_trajectory = np.zeros((n_frames, 3))

    for t in range(n_frames):
        wrist_mp = world_landmarks[t, right_wrist]
        ee_robot = _transform_mediapipe_to_robot(wrist_mp)
        ee_scaled = _scale_to_workspace(ee_robot)

        q = _geometric_ik(ee_scaled)
        q = _clamp_to_limits(q)

        joint_trajectory[t] = q
        ee_trajectory[t] = ee_scaled

    return {
        "joint_trajectory": joint_trajectory,
        "ee_trajectory": ee_trajectory,
        "frame_count": n_frames,
        "robot": robot,
    }
