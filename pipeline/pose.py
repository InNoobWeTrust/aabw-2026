"""3D pose estimation using MediaPipe Tasks API (PoseLandmarker).

Returns frame-aligned arrays matching the source video's frame count, so downstream
artifacts (skeleton overlays, previews, IK retarget) preserve timing correspondence.

Coordinate conventions (important — see also ``pose_artifacts._draw_pose``):
- ``landmarks[T, 33, 3]``: image-normalized (x, y in [0, 1] of frame width/height; z is
  relative depth in the same scale). Used for drawing overlays on top of the source frame.
- ``world_landmarks[T, 33, 3]``: metric 3D landmarks in meters, hip-centered (MediaPipe's
  standard). Used for IK retarget and world-space skeleton previews. The renderer must
  scale and translate these into canvas pixels — never treat x/y in meters as pixels.

Frames without a successful detection are filled with the last detected pose (or zeros
before the first detection) while ``detected_frames_mask`` and ``confidence`` arrays
preserve detection quality. Mock fallback is opt-in for tests only; production code
expects the live MediaPipe backend and raises if it is missing.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import cv2
import numpy as np

_logger = logging.getLogger(__name__)

# Public so tests and consumers can identify which backend produced the data.
POSE_BACKEND_MEDIAPIPE_TASKS = "mediapipe_tasks"
POSE_BACKEND_MOCK = "mock"

# Official MediaPipe Pose Landmarker (Lite) — small, fast, accurate enough for the MVP.
# URL is stable; model is ~5 MB.
POSE_LANDMARKER_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
    "pose_landmarker_lite/float16/latest/pose_landmarker_lite.task"
)
POSE_LANDMARKER_NUM_LANDMARKS = 33

_MEDIAPIPE_IMPORT_ERROR: Exception | None = None
try:
    import mediapipe as mp
    from mediapipe.tasks import python as mp_python  # noqa: F401
    from mediapipe.tasks.python import vision as mp_vision  # noqa: F401

    _MP_TASKS_AVAILABLE = True
except Exception as _exc:  # pragma: no cover - import guard
    _MP_TASKS_AVAILABLE = False
    _MEDIAPIPE_IMPORT_ERROR = _exc


def _default_model_path() -> Path:
    """Return the on-disk path used to cache the PoseLandmarker model."""
    return Path(os.environ.get("ROBODATA_POSE_MODEL_PATH", "data/pose_landmarker.task"))


def ensure_pose_landmarker_model(
    model_path: str | Path | None = None,
    *,
    force_download: bool = False,
) -> Path:
    """Ensure the PoseLandmarker ``.task`` model file exists locally.

    Downloads from the official MediaPipe CDN on first use and caches to disk.
    Raises ``RuntimeError`` if the download fails so the caller can fail the job loudly.
    """
    target = Path(model_path) if model_path is not None else _default_model_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.is_file() and not force_download:
        return target

    try:
        import httpx  # local import: httpx is always in deps, but keep it lazy
    except ImportError as exc:  # pragma: no cover - dep missing
        raise RuntimeError("httpx is required to download the pose landmarker model") from exc

    _logger.info("Downloading PoseLandmarker model to %s", target)
    try:
        with httpx.stream(
            "GET", POSE_LANDMARKER_MODEL_URL, follow_redirects=True, timeout=60.0
        ) as resp:
            resp.raise_for_status()
            with target.open("wb") as fh:
                for chunk in resp.iter_bytes(chunk_size=64 * 1024):
                    if chunk:
                        fh.write(chunk)
    except Exception as exc:
        # Don't leave a half-written file lying around.
        if target.exists():
            target.unlink(missing_ok=True)
        raise RuntimeError(f"Failed to download PoseLandmarker model: {exc}") from exc

    return target


def _mock_pose_result(total_frames: int) -> dict[str, Any]:
    """Deterministic mock used only by tests; production code must use the live backend."""
    rng = np.random.RandomState(42)
    frame_count = max(total_frames, 1)
    return {
        "landmarks": rng.rand(frame_count, POSE_LANDMARKER_NUM_LANDMARKS, 3).astype(np.float32),
        "world_landmarks": rng.rand(frame_count, POSE_LANDMARKER_NUM_LANDMARKS, 3).astype(
            np.float32
        ),
        "confidence": rng.rand(frame_count, POSE_LANDMARKER_NUM_LANDMARKS).astype(np.float32),
        "frame_count": frame_count,
        "detected_frame_count": frame_count,
        "detection_rate": 1.0,
        "detected_frames_mask": np.ones(frame_count, dtype=bool),
        "pose_backend": POSE_BACKEND_MOCK,
    }


def _looks_like_mock_distribution(world_landmarks: np.ndarray) -> bool:
    """Return True if the world landmarks look like the legacy unit-cube mock.

    Real human right upper-arm length (shoulder→elbow) is ~0.25–0.30 m. The
    legacy mock produced uniform [0, 1] values where the typical
    shoulder→elbow distance is ~0.33. Any median below 0.10 m is treated as
    non-metric.
    """
    if world_landmarks.size == 0 or world_landmarks.shape[0] == 0:
        return False
    shoulder = world_landmarks[:, 12, :]
    elbow = world_landmarks[:, 14, :]
    seg = np.linalg.norm(shoulder - elbow, axis=1)
    seg = seg[np.isfinite(seg)]
    if seg.size == 0:
        return True
    return float(np.median(seg)) < 0.10


def _build_landmarker(model_path: str | Path) -> Any:
    """Construct a configured PoseLandmarker (VIDEO mode for frame-by-frame processing)."""
    if not _MP_TASKS_AVAILABLE:
        raise RuntimeError(
            "MediaPipe Tasks API is not importable in this environment. "
            "Install `mediapipe>=0.10` and retry."
        )
    base_options = mp_python.BaseOptions(model_asset_path=str(model_path))
    options = mp_vision.PoseLandmarkerOptions(
        base_options=base_options,
        running_mode=mp_vision.RunningMode.VIDEO,
        num_poses=1,
        min_pose_detection_confidence=0.5,
        min_pose_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    return mp_vision.PoseLandmarker.create_from_options(options)


def extract_pose_from_video(
    video_path: str | Path,
    *,
    allow_mock: bool = False,
    model_path: str | Path | None = None,
) -> dict[str, Any]:
    """Extract 3D pose landmarks from a video using MediaPipe PoseLandmarker.

    Args:
        video_path: Path to the source video file.
        allow_mock: If True, fall back to ``_mock_pose_result`` when the MediaPipe
            backend is unavailable. **Test-only.** Production callers must pass False
            so that a missing backend surfaces as a hard error.
        model_path: Optional override for the PoseLandmarker ``.task`` model file.
            Defaults to ``data/pose_landmarker.task`` (downloaded on first use).

    Returns:
        Dict with frame-aligned arrays and metadata:
          - ``landmarks``: image-normalized (T, 33, 3) float32 in [0, 1].
          - ``world_landmarks``: metric (T, 33, 3) float32 in meters, hip-centered.
          - ``confidence``: (T, 33) float32 visibility in [0, 1].
          - ``detected_frames_mask``: (T,) bool — True where pose was detected this frame.
          - ``frame_count``, ``detected_frame_count``, ``detection_rate``.
          - ``pose_backend``: ``"mediapipe_tasks"`` or ``"mock"``.

    Raises:
        FileNotFoundError: If ``video_path`` does not exist.
        RuntimeError: If the video cannot be opened, the model cannot be loaded, or
            the MediaPipe backend is unavailable and ``allow_mock`` is False.
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    if not _MP_TASKS_AVAILABLE:
        cap.release()
        if allow_mock:
            _logger.warning(
                "MediaPipe Tasks unavailable; returning mock pose result for %s", video_path
            )
            return _mock_pose_result(int(cap.get(cv2.CAP_PROP_FRAME_COUNT)))
        raise RuntimeError(
            "MediaPipe Tasks backend is unavailable. Cannot extract real pose. "
            f"Original import error: {_MEDIAPIPE_IMPORT_ERROR}"
        )

    try:
        resolved_model = ensure_pose_landmarker_model(model_path)
        landmarker = _build_landmarker(resolved_model)
    except Exception as exc:
        cap.release()
        if allow_mock:
            _logger.warning(
                "PoseLandmarker init failed (%s); returning mock pose result for %s",
                exc,
                video_path,
            )
            return _mock_pose_result(int(cap.get(cv2.CAP_PROP_FRAME_COUNT)))
        raise RuntimeError(f"Failed to initialize PoseLandmarker: {exc}") from exc

    fps = float(cap.get(cv2.CAP_PROP_FPS)) or 30.0
    # MediaPipe requires monotonically increasing timestamps in milliseconds.
    frame_interval_ms = int(round(1000.0 / max(fps, 1e-3)))

    landmarks_list: list[np.ndarray] = []
    world_landmarks_list: list[np.ndarray] = []
    confidence_list: list[np.ndarray] = []
    detected_mask: list[bool] = []
    detected_count = 0

    last_landmarks = np.zeros((POSE_LANDMARKER_NUM_LANDMARKS, 3), dtype=np.float32)
    last_world_landmarks = np.zeros((POSE_LANDMARKER_NUM_LANDMARKS, 3), dtype=np.float32)

    frame_idx = 0
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            timestamp_ms = frame_idx * frame_interval_ms
            detection_result = landmarker.detect_for_video(mp_image, timestamp_ms)

            if detection_result.pose_landmarks:
                # pose_landmarks is image-normalized; pose_world_landmarks is metric.
                lm = np.array(
                    [[lm.x, lm.y, lm.z] for lm in detection_result.pose_landmarks[0]],
                    dtype=np.float32,
                )
                if detection_result.pose_world_landmarks:
                    wlm = np.array(
                        [[lm.x, lm.y, lm.z] for lm in detection_result.pose_world_landmarks[0]],
                        dtype=np.float32,
                    )
                else:
                    wlm = last_world_landmarks.copy()
                conf = np.array(
                    [lm.visibility for lm in detection_result.pose_landmarks[0]],
                    dtype=np.float32,
                )
                last_landmarks = lm
                last_world_landmarks = wlm
                detected_count += 1
                detected_mask.append(True)
            else:
                lm = last_landmarks.copy()
                wlm = last_world_landmarks.copy()
                conf = np.zeros((POSE_LANDMARKER_NUM_LANDMARKS,), dtype=np.float32)
                detected_mask.append(False)

            landmarks_list.append(lm)
            world_landmarks_list.append(wlm)
            confidence_list.append(conf)
            frame_idx += 1
    finally:
        cap.release()
        # PoseLandmarker doesn't expose an explicit close in all versions; rely on GC.
        del landmarker

    frame_count = len(landmarks_list)
    detection_rate = detected_count / frame_count if frame_count > 0 else 0.0

    if not landmarks_list:
        return {
            "landmarks": np.empty((0, POSE_LANDMARKER_NUM_LANDMARKS, 3), dtype=np.float32),
            "world_landmarks": np.empty((0, POSE_LANDMARKER_NUM_LANDMARKS, 3), dtype=np.float32),
            "confidence": np.empty((0, POSE_LANDMARKER_NUM_LANDMARKS), dtype=np.float32),
            "frame_count": 0,
            "detected_frame_count": 0,
            "detection_rate": 0.0,
            "detected_frames_mask": np.empty((0,), dtype=bool),
            "pose_backend": POSE_BACKEND_MEDIAPIPE_TASKS,
        }

    world_landmarks_arr = np.stack(world_landmarks_list)

    # Defensive: if MediaPipe returned something that looks like the legacy
    # unit-cube mock distribution, raise loudly instead of producing garbage.
    if _looks_like_mock_distribution(world_landmarks_arr):
        raise RuntimeError(
            "PoseLandmarker returned non-metric world landmarks "
            "(shoulder→elbow segment below 0.10 m). This matches the legacy "
            "mock distribution. Refusing to retarget IK on non-metric data."
        )

    return {
        "landmarks": np.stack(landmarks_list),
        "world_landmarks": world_landmarks_arr,
        "confidence": np.stack(confidence_list),
        "frame_count": frame_count,
        "detected_frame_count": detected_count,
        "detection_rate": detection_rate,
        "detected_frames_mask": np.array(detected_mask, dtype=bool),
        "pose_backend": POSE_BACKEND_MEDIAPIPE_TASKS,
    }
