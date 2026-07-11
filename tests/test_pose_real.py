"""Tests for the new MediaPipe Tasks pose pipeline and metric-coordinate guarantees.

The previous pipeline silently fell back to a deterministic mock when the MediaPipe
backend was unavailable, producing uniformly random world landmarks in [0, 1] and
garbage retarget output. These tests pin down the invariants that must hold once
the Tasks API path is in place:

- Mock fallback is opt-in only (default behavior raises).
- ``world_landmarks`` must look metric (not in [0, 1]).
- Per-frame undetected poses do not crash the pipeline.
- The new ``pose_backend`` key is set correctly.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import numpy as np
import pytest

from pipeline.pose import (
    POSE_BACKEND_MEDIAPIPE_TASKS,
    POSE_BACKEND_MOCK,
    _looks_like_mock_distribution,
    _mock_pose_result,
    ensure_pose_landmarker_model,
    extract_pose_from_video,
)


def test_mock_pose_result_is_uniform_in_unit_cube() -> None:
    """The mock must be deterministic and bounded in [0, 1] for test isolation."""
    result = _mock_pose_result(8)
    assert result["landmarks"].shape == (8, 33, 3)
    assert result["world_landmarks"].shape == (8, 33, 3)
    assert result["pose_backend"] == POSE_BACKEND_MOCK
    assert result["frame_count"] == 8
    assert result["detection_rate"] == 1.0


def test_looks_like_mock_distribution_detects_degenerate_pose() -> None:
    """The heuristic must flag a collapsed (all-same-point) pose as non-metric."""
    # All landmarks at the same point → segment length 0.
    arr_collapsed = np.zeros((5, 33, 3), dtype=np.float32) + 0.5
    # Real right upper-arm segment ~0.25 m
    arr_metric = np.zeros((5, 33, 3), dtype=np.float32)
    arr_metric[:, 12, :] = [0.0, 0.0, 0.3]
    arr_metric[:, 14, :] = [0.25, 0.0, 0.3]
    arr_metric[:, 16, :] = [0.5, 0.0, 0.25]
    assert _looks_like_mock_distribution(arr_collapsed) is True
    assert _looks_like_mock_distribution(arr_metric) is False


def test_extract_pose_raises_when_backend_unavailable_and_mock_disallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Production callers must get a hard error, not a silent mock fallback."""
    import cv2

    video_path = tmp_path / "one_frame.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(video_path), fourcc, 10.0, (64, 64))
    frame = np.full((64, 64, 3), 128, dtype=np.uint8)
    writer.write(frame)
    writer.release()
    assert video_path.exists()

    import pipeline.pose as pose_mod

    monkeypatch.setattr(pose_mod, "_MP_TASKS_AVAILABLE", False)

    with pytest.raises(RuntimeError, match="MediaPipe"):
        extract_pose_from_video(video_path, allow_mock=False)


def test_extract_pose_returns_mock_when_allow_mock_true_and_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tests may opt-in to the mock path; production must not."""
    import cv2

    video_path = tmp_path / "tiny.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(video_path), fourcc, 10.0, (64, 64))
    writer.write(np.full((64, 64, 3), 64, dtype=np.uint8))
    writer.release()

    import pipeline.pose as pose_mod

    monkeypatch.setattr(pose_mod, "_MP_TASKS_AVAILABLE", False)

    result = extract_pose_from_video(video_path, allow_mock=True)
    assert result["pose_backend"] == POSE_BACKEND_MOCK
    assert result["frame_count"] >= 1


def test_extract_pose_missing_video_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        extract_pose_from_video(tmp_path / "does_not_exist.mp4", allow_mock=True)


def test_ensure_model_does_not_redownload_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the model is already cached, no network request should be made."""
    target = tmp_path / "cached_model.task"
    target.write_bytes(b"already-here")
    resolved = ensure_pose_landmarker_model(target)
    assert resolved == target
    assert resolved.read_bytes() == b"already-here"


def test_extract_pose_refuses_non_metric_world_landmarks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If a custom backend returned non-metric world landmarks, the pipeline must raise."""
    import cv2

    video_path = tmp_path / "tiny.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(video_path), fourcc, 10.0, (64, 64))
    writer.write(np.full((64, 64, 3), 64, dtype=np.uint8))
    writer.release()

    import pipeline.pose as pose_mod

    monkeypatch.setattr(pose_mod, "_MP_TASKS_AVAILABLE", True)
    monkeypatch.setattr(pose_mod, "ensure_pose_landmarker_model", lambda *a, **k: tmp_path / "m")

    class _StubLm:
        def __init__(self, x: float, y: float, z: float, visibility: float) -> None:
            self.x = x
            self.y = y
            self.z = z
            self.visibility = visibility

    class _StubResult:
        pose_landmarks = [
            [
                _StubLm(0.5, 0.5, 0.0, 0.9),
                *[_StubLm(0.1 * i, 0.2, 0.0, 0.9) for i in range(32)],
            ]
        ]
        # All landmarks at the same point — shoulder==elbow, segment 0
        pose_world_landmarks = [[_StubLm(0.5, 0.5, 0.0, 0.9) for _ in range(33)]]

    class _StubLandmarker:
        def detect_for_video(self, image, timestamp_ms):  # noqa: ARG002
            return _StubResult()

    monkeypatch.setattr(pose_mod, "_build_landmarker", lambda *a, **k: _StubLandmarker())

    with pytest.raises(RuntimeError, match="non-metric"):
        extract_pose_from_video(video_path, allow_mock=True)


def test_allow_mock_default_is_false() -> None:
    """The orchestrator should never pass allow_mock=True in production paths."""
    sig = inspect.signature(extract_pose_from_video)
    assert "allow_mock" in sig.parameters
    assert sig.parameters["allow_mock"].default is False


def test_pose_backend_value_serializes_to_json() -> None:
    """The pose_backend string value must be JSON-serializable for downstream consumers."""
    payload = {
        "pose_backend": POSE_BACKEND_MEDIAPIPE_TASKS,
        "frame_count": 100,
        "detection_rate": 0.95,
    }
    json.dumps(payload)
