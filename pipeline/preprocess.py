"""Video preprocessing: extract frames at 10fps using OpenCV, validate video metadata."""

from pathlib import Path

import cv2


def extract_frames(
    video_path: str | Path,
    output_dir: str | Path,
    target_fps: int = 10,
) -> dict:
    """Extract frames from video at target_fps.

    Args:
        video_path: Path to input video file
        output_dir: Directory to write frames (created if needed)
        target_fps: Target frame rate (default 10)

    Returns:
        Dict with keys: frame_count, duration_s, original_fps, width, height, frame_paths

    Raises:
        FileNotFoundError: If video_path doesn't exist
        ValueError: If video cannot be read
    """
    video_path = Path(video_path)
    output_dir = Path(output_dir)

    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError(f"Cannot open video: {video_path}")

    original_fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    duration_s = total_frames / original_fps if original_fps > 0 else 0.0

    skip_interval = max(1, int(round(original_fps / target_fps)))

    output_dir.mkdir(parents=True, exist_ok=True)

    frame_paths = []
    frame_idx = 0
    saved_count = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_idx % skip_interval == 0:
            out_path = output_dir / f"frame_{saved_count:04d}.jpg"
            cv2.imwrite(str(out_path), frame)
            frame_paths.append(str(out_path))
            saved_count += 1

        frame_idx += 1

    cap.release()

    return {
        "frame_count": saved_count,
        "duration_s": duration_s,
        "original_fps": original_fps,
        "width": width,
        "height": height,
        "frame_paths": frame_paths,
    }
