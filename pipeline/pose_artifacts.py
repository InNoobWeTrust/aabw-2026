"""Pose-stage artifact generation: overlay video, preview video, and review metrics."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

POSE_CONNECTIONS = [
    (11, 12),
    (11, 13),
    (13, 15),
    (12, 14),
    (14, 16),
    (11, 23),
    (12, 24),
    (23, 24),
    (23, 25),
    (25, 27),
    (24, 26),
    (26, 28),
]

POSE_KEYPOINTS = {
    "left_shoulder": 11,
    "right_shoulder": 12,
    "left_elbow": 13,
    "right_elbow": 14,
    "left_wrist": 15,
    "right_wrist": 16,
}


def render_skeleton_overlay_video(
    video_path: str | Path,
    pose_result: dict,
    output_path: str | Path,
) -> Path:
    """Draw extracted pose landmarks on top of the original video."""
    video_path = Path(video_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video for overlay render: {video_path}")

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 640
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 480
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    writer = cv2.VideoWriter(
        str(output_path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (width, height),
    )
    landmarks = pose_result["landmarks"]
    confidence = pose_result["confidence"]
    detected_mask = pose_result.get("detected_frames_mask")

    frame_idx = 0
    while True:
        ret, frame = cap.read()
        if not ret or frame_idx >= len(landmarks):
            break

        points = landmarks[frame_idx]
        conf = confidence[frame_idx]
        detected = bool(detected_mask[frame_idx]) if detected_mask is not None else True
        _draw_pose(frame, points, conf, detected)
        cv2.putText(
            frame,
            f"Skeleton overlay | frame {frame_idx + 1}/{len(landmarks)}",
            (20, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        writer.write(frame)
        frame_idx += 1

    writer.release()
    cap.release()
    return output_path


def render_skeleton_preview_video(
    pose_result: dict,
    output_path: str | Path,
    fps: int = 10,
    width: int = 640,
    height: int = 480,
) -> Path:
    """Render a clean skeleton-only preview on a dark background."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(output_path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (width, height),
    )

    landmarks = pose_result["landmarks"]
    confidence = pose_result["confidence"]
    detected_mask = pose_result.get("detected_frames_mask")

    for idx, points in enumerate(landmarks):
        canvas = np.zeros((height, width, 3), dtype=np.uint8)
        canvas[:] = (15, 23, 42)
        conf = confidence[idx]
        detected = bool(detected_mask[idx]) if detected_mask is not None else True
        _draw_pose(canvas, points, conf, detected)
        cv2.putText(
            canvas,
            "Skeleton preview",
            (20, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (241, 245, 249),
            2,
            cv2.LINE_AA,
        )
        writer.write(canvas)

    writer.release()
    return output_path


def flatten_skeleton_features(pose_result: dict) -> np.ndarray:
    """Flatten world-space landmarks into a [T, 99] feature matrix."""
    world_landmarks = pose_result["world_landmarks"]
    if world_landmarks.size == 0:
        return np.empty((0, 99), dtype=np.float32)
    return world_landmarks.reshape(world_landmarks.shape[0], -1).astype(np.float32)


def compute_pose_review_metrics(pose_result: dict) -> dict:
    """Summarize pose stability and visibility for bounded review prompts."""
    confidence = pose_result["confidence"]
    world_landmarks = pose_result["world_landmarks"]
    detected_mask = pose_result.get("detected_frames_mask")
    frame_count = int(pose_result.get("frame_count", 0))
    detected_frame_count = int(pose_result.get("detected_frame_count", frame_count))
    detection_rate = float(pose_result.get("detection_rate", 0.0))

    avg_visibility = float(confidence.mean()) if confidence.size else 0.0
    missing_landmark_ratio = float((confidence <= 0.01).mean()) if confidence.size else 1.0

    metrics = {
        "frame_count": frame_count,
        "detected_frame_count": detected_frame_count,
        "detection_rate": detection_rate,
        "average_visibility": avg_visibility,
        "missing_landmark_ratio": missing_landmark_ratio,
        "body_visibility_coverage": float(detected_mask.mean())
        if detected_mask is not None and len(detected_mask)
        else detection_rate,
        "keypoints": {},
    }

    if world_landmarks.size:
        for name, idx in POSE_KEYPOINTS.items():
            series = world_landmarks[:, idx, :]
            delta = np.diff(series, axis=0)
            jitter = float(np.linalg.norm(delta, axis=1).mean()) if len(delta) else 0.0
            visibility = float(confidence[:, idx].mean()) if confidence.size else 0.0
            metrics["keypoints"][name] = {
                "mean_visibility": visibility,
                "temporal_jitter": jitter,
            }

    return metrics


def _draw_pose(
    frame: np.ndarray, points: np.ndarray, confidence: np.ndarray, detected: bool
) -> None:
    height, width = frame.shape[:2]
    link_color = (0, 212, 170) if detected else (100, 116, 139)
    point_color = (244, 63, 94) if detected else (148, 163, 184)

    for start, end in POSE_CONNECTIONS:
        if confidence[start] <= 0.01 or confidence[end] <= 0.01:
            continue
        p1 = _to_pixel(points[start], width, height)
        p2 = _to_pixel(points[end], width, height)
        cv2.line(frame, p1, p2, link_color, 2, cv2.LINE_AA)

    for idx, point in enumerate(points):
        if confidence[idx] <= 0.01:
            continue
        center = _to_pixel(point, width, height)
        cv2.circle(frame, center, 4, point_color, -1, cv2.LINE_AA)


def _to_pixel(point: np.ndarray, width: int, height: int) -> tuple[int, int]:
    x = int(np.clip(point[0], 0.0, 1.0) * (width - 1))
    y = int(np.clip(point[1], 0.0, 1.0) * (height - 1))
    return x, y
