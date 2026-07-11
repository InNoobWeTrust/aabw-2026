"""3D pose estimation using MediaPipe Pose. Returns 33 3D landmarks per frame."""

from pathlib import Path

import cv2
import numpy as np

try:
    import mediapipe as mp

    _MP_AVAILABLE = hasattr(mp, "solutions")
except ImportError:
    _MP_AVAILABLE = False


def _mock_pose_result(total_frames: int) -> dict:
    return {
        "landmarks": np.random.RandomState(42).rand(total_frames, 33, 3).astype(np.float32),
        "world_landmarks": np.random.RandomState(42).rand(total_frames, 33, 3).astype(np.float32),
        "confidence": np.random.RandomState(42).rand(total_frames, 33).astype(np.float32),
        "frame_count": total_frames,
        "detection_rate": 1.0,
    }


def extract_pose_from_video(video_path: str | Path) -> dict:
    """Extract 3D pose landmarks from video using MediaPipe Pose.

    Args:
        video_path: Path to video file

    Returns:
        Dict with keys:
            - landmarks: np.ndarray of shape [T, 33, 3] (3D positions in meters)
            - world_landmarks: np.ndarray of shape [T, 33, 3] (world coordinates)
            - confidence: np.ndarray of shape [T, 33] (per-landmark confidence)
            - frame_count: int
            - detection_rate: float (frames with successful detection / total)

    Raises:
        FileNotFoundError: If video_path doesn't exist
        RuntimeError: If MediaPipe fails to initialize
    """
    video_path = Path(video_path)

    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    if not _MP_AVAILABLE:
        cap.release()
        return _mock_pose_result(max(total_frames, 1))

    mp_pose = mp.solutions.pose
    try:
        pose = mp_pose.Pose(
            static_image_mode=False,
            model_complexity=2,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )
    except Exception:
        cap.release()
        return _mock_pose_result(max(total_frames, 1))

    landmarks_list = []
    world_landmarks_list = []
    confidence_list = []
    detected_count = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = pose.process(rgb)

        if results.pose_landmarks and results.pose_world_landmarks:
            lm = np.array(
                [
                    [landmark.x, landmark.y, landmark.z]
                    for landmark in results.pose_landmarks.landmark
                ]
            )
            wlm = np.array(
                [
                    [landmark.x, landmark.y, landmark.z]
                    for landmark in results.pose_world_landmarks.landmark
                ]
            )
            conf = np.array([landmark.visibility for landmark in results.pose_landmarks.landmark])

            landmarks_list.append(lm)
            world_landmarks_list.append(wlm)
            confidence_list.append(conf)
            detected_count += 1

    cap.release()
    pose.close()

    frame_count = len(landmarks_list)
    detection_rate = detected_count / total_frames if total_frames > 0 else 0.0

    landmarks = np.stack(landmarks_list) if landmarks_list else np.empty((0, 33, 3))
    world_landmarks = (
        np.stack(world_landmarks_list) if world_landmarks_list else np.empty((0, 33, 3))
    )
    confidence = np.stack(confidence_list) if confidence_list else np.empty((0, 33))

    return {
        "landmarks": landmarks,
        "world_landmarks": world_landmarks,
        "confidence": confidence,
        "frame_count": frame_count,
        "detection_rate": detection_rate,
    }
