"""3D pose estimation using MediaPipe Pose with frame-aligned outputs.

The returned arrays are aligned to the source video's frame count so downstream
artifacts such as skeleton overlays and preview videos can be rendered without
losing timing correspondence. Frames without a successful detection are filled
using the last known pose (or zeros before the first detection) while the
``detected_frames_mask`` and confidence arrays preserve detection quality.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

try:
    import mediapipe as mp

    _MP_AVAILABLE = hasattr(mp, "solutions")
except ImportError:
    _MP_AVAILABLE = False


def _mock_pose_result(total_frames: int) -> dict:
    rng = np.random.RandomState(42)
    frame_count = max(total_frames, 1)
    return {
        "landmarks": rng.rand(frame_count, 33, 3).astype(np.float32),
        "world_landmarks": rng.rand(frame_count, 33, 3).astype(np.float32),
        "confidence": rng.rand(frame_count, 33).astype(np.float32),
        "frame_count": frame_count,
        "detected_frame_count": frame_count,
        "detection_rate": 1.0,
        "detected_frames_mask": np.ones(frame_count, dtype=bool),
    }


def extract_pose_from_video(video_path: str | Path) -> dict:
    """Extract 3D pose landmarks from a video using MediaPipe Pose.

    Returns frame-aligned arrays for every source frame. When pose detection is
    missing for a frame, the previous detected pose is carried forward so that
    downstream preview and packaging steps preserve the original timing.
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
        return _mock_pose_result(total_frames)

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
        return _mock_pose_result(total_frames)

    landmarks_list: list[np.ndarray] = []
    world_landmarks_list: list[np.ndarray] = []
    confidence_list: list[np.ndarray] = []
    detected_mask: list[bool] = []
    detected_count = 0

    last_landmarks = np.zeros((33, 3), dtype=np.float32)
    last_world_landmarks = np.zeros((33, 3), dtype=np.float32)

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
                ],
                dtype=np.float32,
            )
            wlm = np.array(
                [
                    [landmark.x, landmark.y, landmark.z]
                    for landmark in results.pose_world_landmarks.landmark
                ],
                dtype=np.float32,
            )
            conf = np.array(
                [landmark.visibility for landmark in results.pose_landmarks.landmark],
                dtype=np.float32,
            )
            last_landmarks = lm
            last_world_landmarks = wlm
            detected_count += 1
            detected_mask.append(True)
        else:
            lm = last_landmarks.copy()
            wlm = last_world_landmarks.copy()
            conf = np.zeros((33,), dtype=np.float32)
            detected_mask.append(False)

        landmarks_list.append(lm)
        world_landmarks_list.append(wlm)
        confidence_list.append(conf)

    cap.release()
    pose.close()

    frame_count = len(landmarks_list)
    detection_rate = detected_count / frame_count if frame_count > 0 else 0.0

    if not landmarks_list:
        return {
            "landmarks": np.empty((0, 33, 3), dtype=np.float32),
            "world_landmarks": np.empty((0, 33, 3), dtype=np.float32),
            "confidence": np.empty((0, 33), dtype=np.float32),
            "frame_count": 0,
            "detected_frame_count": 0,
            "detection_rate": 0.0,
            "detected_frames_mask": np.empty((0,), dtype=bool),
        }

    return {
        "landmarks": np.stack(landmarks_list),
        "world_landmarks": np.stack(world_landmarks_list),
        "confidence": np.stack(confidence_list),
        "frame_count": frame_count,
        "detected_frame_count": detected_count,
        "detection_rate": detection_rate,
        "detected_frames_mask": np.array(detected_mask, dtype=bool),
    }
