"""Generate synchronized calibration evidence samples from pipeline videos.

This module creates a small, deterministic evidence pack for future mapping
calibration and for human inspection of whether the side-by-side outputs make
sense at the same moments in time.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass(frozen=True)
class VideoArtifact:
    """A readable video evidence source.

    Args:
        key: Stable artifact key used in the JSON manifest.
        video_path: On-disk MP4 path.
        frame_count: Total readable frame count.
        width: Video frame width in pixels.
        height: Video frame height in pixels.
        fps: Frames per second reported by the decoder.
    """

    key: str
    video_path: Path
    frame_count: int
    width: int
    height: int
    fps: float


@dataclass(frozen=True)
class SampledFrameArtifact:
    """A single sampled frame exported from one evidence video."""

    key: str
    video_path: str
    image_path: str
    frame_index: int
    timestamp_seconds: float
    width: int
    height: int


@dataclass(frozen=True)
class MappingContextSample:
    """One synchronized timestamp represented across all evidence artifacts."""

    sample_index: int
    frame_index: int
    timestamp_seconds: float
    artifacts: dict[str, SampledFrameArtifact]


@dataclass(frozen=True)
class MappingContextSamplesManifest:
    """Persisted bounded evidence pack for later calibration or human review."""

    requested_sample_count: int
    sample_count: int
    synchronized_frame_count: int
    sources: dict[str, dict[str, str | int | float]]
    samples_dir: str
    json_path: str
    samples: list[MappingContextSample]

    def to_dict(self) -> dict:
        """Return a JSON-serializable representation of the manifest."""
        return asdict(self)


def generate_mapping_context_samples(
    *,
    original_video_path: str | Path,
    skeleton_overlay_video_path: str | Path,
    skeleton_preview_video_path: str | Path,
    robot_simulation_video_path: str | Path,
    calibration_dir: str | Path,
    requested_sample_count: int = 8,
) -> dict:
    """Generate a synchronized bounded evidence pack from the four output videos.

    The implementation uses a single shared frame index set so humans and future
    calibration agents can compare the original motion, skeleton overlay,
    skeleton preview, and robot simulation at the exact same moments.

    Args:
        original_video_path: Uploaded source video path.
        skeleton_overlay_video_path: Rendered overlay video path.
        skeleton_preview_video_path: Rendered skeleton-only preview path.
        robot_simulation_video_path: Rendered robot simulation path.
        calibration_dir: Output directory for JSON manifest and JPEG samples.
        requested_sample_count: Maximum number of synchronized timestamps.

    Returns:
        JSON-serializable manifest describing the generated evidence pack.

    Raises:
        FileNotFoundError: If any required video path is missing.
        RuntimeError: If a video cannot be opened or a sampled frame cannot be read.
        ValueError: If no frames are available for synchronized sampling.
    """
    if requested_sample_count <= 0:
        raise ValueError("requested_sample_count must be positive")

    calibration_root = Path(calibration_dir)
    samples_dir = calibration_root / "samples"
    samples_dir.mkdir(parents=True, exist_ok=True)

    artifacts = [
        _inspect_video("original", original_video_path),
        _inspect_video("overlay", skeleton_overlay_video_path),
        _inspect_video("preview", skeleton_preview_video_path),
        _inspect_video("robot_simulation", robot_simulation_video_path),
    ]

    synchronized_frame_count = min(artifact.frame_count for artifact in artifacts)
    if synchronized_frame_count <= 0:
        raise ValueError("Cannot generate mapping context samples from empty videos")

    frame_indices = _select_sample_frame_indices(
        frame_count=synchronized_frame_count,
        requested_sample_count=requested_sample_count,
    )

    samples: list[MappingContextSample] = []
    for sample_index, frame_index in enumerate(frame_indices):
        sample_artifacts: dict[str, SampledFrameArtifact] = {}
        for artifact in artifacts:
            frame = _read_frame_at_index(artifact.video_path, frame_index)
            image_path = (
                samples_dir / f"{artifact.key}_{sample_index:02d}_frame_{frame_index:04d}.jpg"
            )
            _write_jpeg(image_path, frame)
            sample_artifacts[artifact.key] = SampledFrameArtifact(
                key=artifact.key,
                video_path=str(artifact.video_path),
                image_path=str(image_path),
                frame_index=frame_index,
                timestamp_seconds=frame_index / artifact.fps if artifact.fps > 0 else 0.0,
                width=artifact.width,
                height=artifact.height,
            )

        samples.append(
            MappingContextSample(
                sample_index=sample_index,
                frame_index=frame_index,
                timestamp_seconds=(frame_index / artifacts[0].fps) if artifacts[0].fps > 0 else 0.0,
                artifacts=sample_artifacts,
            )
        )

    manifest = MappingContextSamplesManifest(
        requested_sample_count=requested_sample_count,
        sample_count=len(samples),
        synchronized_frame_count=synchronized_frame_count,
        sources={
            artifact.key: {
                "video_path": str(artifact.video_path),
                "frame_count": artifact.frame_count,
                "width": artifact.width,
                "height": artifact.height,
                "fps": artifact.fps,
            }
            for artifact in artifacts
        },
        samples_dir=str(samples_dir),
        json_path=str(calibration_root / "mapping_context_samples.json"),
        samples=samples,
    )

    manifest_path = calibration_root / "mapping_context_samples.json"
    manifest_path.write_text(json.dumps(manifest.to_dict(), indent=2))
    return manifest.to_dict()


def _inspect_video(key: str, video_path: str | Path) -> VideoArtifact:
    """Open a video and return its readable metadata."""
    path = Path(video_path)
    if not path.exists():
        raise FileNotFoundError(f"Required calibration video not found: {path}")

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open calibration video: {path}")

    try:
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 10.0)
    finally:
        cap.release()

    return VideoArtifact(
        key=key,
        video_path=path,
        frame_count=frame_count,
        width=width,
        height=height,
        fps=fps,
    )


def _select_sample_frame_indices(*, frame_count: int, requested_sample_count: int) -> list[int]:
    """Choose a small sorted unique set of synchronized frame indices."""
    if frame_count <= 0:
        raise ValueError("frame_count must be positive")

    sample_count = min(frame_count, requested_sample_count)
    raw_indices = np.linspace(0, frame_count - 1, num=sample_count, dtype=int).tolist()
    deduped = sorted(set(int(idx) for idx in raw_indices))

    if len(deduped) == sample_count:
        return deduped

    for idx in range(frame_count):
        if idx not in deduped:
            deduped.append(idx)
        if len(deduped) == sample_count:
            break
    return sorted(deduped)


def _read_frame_at_index(video_path: Path, frame_index: int) -> np.ndarray:
    """Read one frame at a precise index from a video file."""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open calibration video for frame sampling: {video_path}")

    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ok, frame = cap.read()
    finally:
        cap.release()

    if not ok or frame is None:
        raise RuntimeError(f"Failed to read frame {frame_index} from {video_path}")
    return frame


def _write_jpeg(image_path: Path, frame: np.ndarray) -> None:
    """Write one sampled evidence frame as a JPEG image."""
    ok = cv2.imwrite(str(image_path), frame)
    if not ok:
        raise RuntimeError(f"Failed to write calibration sample image: {image_path}")
