"""Pose-stage artifact generation: overlay video, preview video, and review metrics."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

try:
    import mediapipe as mp

    _POSE_CONNECTIONS = list(mp.solutions.pose.POSE_CONNECTIONS)
except Exception:
    _POSE_CONNECTIONS = [
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

POSE_CONNECTIONS = sorted((int(start), int(end)) for start, end in _POSE_CONNECTIONS)
RENDER_VISIBILITY_THRESHOLD = 0.35

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
    """Draw extracted pose landmarks on top of the original video.

    Uses ``landmarks`` (image-normalized, in [0, 1]) which corresponds directly to
    pixel coordinates of the source frame — correct for an overlay. Do not pass
    ``world_landmarks`` here; they are in meters and require a scale/translate.
    """
    import subprocess

    video_path = Path(video_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video for overlay render: {video_path}")

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 640
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 480
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    temp_path = output_path.with_suffix(".raw.mp4")

    writer = cv2.VideoWriter(
        str(temp_path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (width, height),
    )
    landmarks = pose_result["landmarks"]
    confidence = pose_result["confidence"]
    detected_mask = pose_result.get("detected_frames_mask")

    frame_idx = 0
    try:
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
    finally:
        writer.release()
        cap.release()

    # Transcode raw video to H.264 browser-compatible format using FFmpeg
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(temp_path),
                "-vcodec",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-loglevel",
                "error",
                str(output_path),
            ],
            check=True,
        )
        temp_path.unlink(missing_ok=True)
    except Exception:
        if temp_path.exists():
            temp_path.rename(output_path)

    return output_path


def _compute_world_to_canvas_transform(
    world_landmarks: np.ndarray, width: int, height: int
) -> tuple[float, float, float]:
    """Compute (scale, tx, ty) to map hip-centered metric landmarks into a canvas.

    Returns scale in pixels per meter and pixel-space (tx, ty) to translate the
    hip origin to the canvas center. The scale is chosen so that the bounding box
    of detected landmarks fills the canvas without clipping.
    """
    if world_landmarks.size == 0:
        return 200.0, width / 2.0, height / 2.0
    mask = np.isfinite(world_landmarks).all(axis=2)
    valid_points = world_landmarks[mask]
    if valid_points.size == 0:
        return 200.0, width / 2.0, height / 2.0
    hip_x = float(valid_points[:, 0].mean())
    hip_y = float(valid_points[:, 1].mean())
    centered = valid_points - np.array([hip_x, hip_y, 0.0], dtype=np.float32)
    max_abs = float(np.abs(centered[:, :2]).max()) or 0.5
    # Leave 10% margin on each side.
    scale = 0.45 * min(width, height) / max_abs
    tx = width / 2.0
    ty = height / 2.0
    return scale, tx, ty


def render_skeleton_preview_video(
    pose_result: dict,
    output_path: str | Path,
    fps: int = 10,
    width: int = 640,
    height: int = 480,
) -> Path:
    """Render a clean skeleton-only preview on a dark background.

    Uses ``world_landmarks`` (metric, hip-centered). The pose is centered at the
    canvas origin and scaled so the bounding box fills the frame. ``landmarks``
    (image-normalized) are NOT used here — that coordinate space is reserved for
    the overlay drawn on top of the source video.
    """
    import subprocess

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    temp_path = output_path.with_suffix(".raw.mp4")

    writer = cv2.VideoWriter(
        str(temp_path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (width, height),
    )

    world_landmarks = pose_result["world_landmarks"]
    confidence = pose_result["confidence"]
    detected_mask = pose_result.get("detected_frames_mask")

    if world_landmarks.size:
        scale, tx, ty = _compute_world_to_canvas_transform(world_landmarks, width, height)
    else:
        scale, tx, ty = 200.0, width / 2.0, height / 2.0

    try:
        for idx, points in enumerate(world_landmarks):
            canvas = np.zeros((height, width, 3), dtype=np.uint8)
            canvas[:] = (15, 23, 42)
            conf = confidence[idx]
            detected = bool(detected_mask[idx]) if detected_mask is not None else True
            _draw_pose_world(canvas, points, conf, detected, scale, tx, ty)
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
    finally:
        writer.release()

    # Transcode raw video to H.264 browser-compatible format using FFmpeg
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(temp_path),
                "-vcodec",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-loglevel",
                "error",
                str(output_path),
            ],
            check=True,
        )
        temp_path.unlink(missing_ok=True)
    except Exception:
        if temp_path.exists():
            temp_path.rename(output_path)

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
        if confidence[start] < RENDER_VISIBILITY_THRESHOLD:
            continue
        if confidence[end] < RENDER_VISIBILITY_THRESHOLD:
            continue
        p1 = _to_pixel(points[start], width, height)
        p2 = _to_pixel(points[end], width, height)
        if p1 is None or p2 is None:
            continue
        cv2.line(frame, p1, p2, link_color, 2, cv2.LINE_AA)

    for idx, point in enumerate(points):
        if confidence[idx] < RENDER_VISIBILITY_THRESHOLD:
            continue
        center = _to_pixel(point, width, height)
        if center is None:
            continue
        cv2.circle(frame, center, 4, point_color, -1, cv2.LINE_AA)


def _to_pixel(point: np.ndarray, width: int, height: int) -> tuple[int, int] | None:
    x, y = float(point[0]), float(point[1])
    if not np.isfinite(x) or not np.isfinite(y):
        return None
    if x < 0.0 or x > 1.0 or y < 0.0 or y > 1.0:
        return None
    return int(x * (width - 1)), int(y * (height - 1))


def _draw_pose_world(
    frame: np.ndarray,
    points: np.ndarray,
    confidence: np.ndarray,
    detected: bool,
    scale: float,
    tx: float,
    ty: float,
) -> None:
    """Draw metric world landmarks onto a canvas using a fixed pixel transform.

    The transform (scale, tx, ty) is computed once per video by
    ``_compute_world_to_canvas_transform`` so the skeleton is centered and fills
    the canvas. This is the world-space analog of ``_draw_pose``.
    """
    height, width = frame.shape[:2]
    link_color = (0, 212, 170) if detected else (100, 116, 139)
    point_color = (244, 63, 94) if detected else (148, 163, 184)

    def _project(point: np.ndarray) -> tuple[int, int] | None:
        x, y = float(point[0]), float(point[1])
        if not np.isfinite(x) or not np.isfinite(y):
            return None
        px = int(tx + x * scale)
        # MediaPipe world-landmark Y already increases downward relative to the
        # source frame/body layout for our persisted pose data, so do not invert
        # it here. Inverting again flips the 3D skeleton upside down.
        py = int(ty + y * scale)
        if px < 0 or px >= width or py < 0 or py >= height:
            return None
        return px, py

    for start, end in POSE_CONNECTIONS:
        if confidence[start] < RENDER_VISIBILITY_THRESHOLD:
            continue
        if confidence[end] < RENDER_VISIBILITY_THRESHOLD:
            continue
        p1 = _project(points[start])
        p2 = _project(points[end])
        if p1 is None or p2 is None:
            continue
        cv2.line(frame, p1, p2, link_color, 2, cv2.LINE_AA)

    for idx, point in enumerate(points):
        if confidence[idx] < RENDER_VISIBILITY_THRESHOLD:
            continue
        center = _project(point)
        if center is None:
            continue
        cv2.circle(frame, center, 4, point_color, -1, cv2.LINE_AA)
