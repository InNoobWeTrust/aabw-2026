"""Tests for profile-driven retargeting with deterministic output."""

from __future__ import annotations

import numpy as np

from domain.mapping import AxisMapping, MappingProfile
from pipeline.retarget import retarget_to_robot


def _make_pose_data(n_frames: int = 4) -> dict:
    """Build minimal valid pose_data for retarget testing."""
    world = np.zeros((n_frames, 33, 3), dtype=np.float32)
    landmarks = world.copy()
    conf = np.ones((n_frames, 33), dtype=np.float32)
    return {
        "landmarks": landmarks,
        "world_landmarks": world,
        "confidence": conf,
        "frame_count": n_frames,
        "detected_frame_count": n_frames,
        "detection_rate": 1.0,
        "detected_frames_mask": np.ones(n_frames, dtype=bool),
    }


class TestDefaultProfilePreservesOldBehavior:
    """Default MappingProfile must produce identical output to the old hardcoded path."""

    def test_no_profile_gives_same_result_as_default_profile(self) -> None:
        """retarget_to_robot with profile=None uses the default profile."""
        pose_data = _make_pose_data(3)
        result_none = retarget_to_robot(pose_data)
        result_default = retarget_to_robot(pose_data, profile=MappingProfile())

        np.testing.assert_array_equal(
            result_none["joint_trajectory"], result_default["joint_trajectory"]
        )
        np.testing.assert_array_equal(result_none["ee_trajectory"], result_default["ee_trajectory"])

    def test_default_profile_is_serialized_in_result(self) -> None:
        """The mapping_profile key is present in retarget output."""
        pose_data = _make_pose_data(2)
        result = retarget_to_robot(pose_data)
        assert "mapping_profile" in result
        mp = result["mapping_profile"]
        assert mp["profile_version"] == 1
        assert mp["handedness"] == "right"
        assert mp["wrist_landmark_index"] is None
        assert mp["workspace_scale"] > 0
        assert mp["depth_scale"] == 1.0
        assert not mp["z_clamp_enabled"]
        assert mp["position_only"] is True

    def test_output_shape_and_types(self) -> None:
        """Retarget output has expected shapes and dtypes."""
        pose_data = _make_pose_data(5)
        result = retarget_to_robot(pose_data)
        assert result["frame_count"] == 5
        assert result["robot"] == "franka_panda"
        assert result["joint_trajectory"].shape == (5, 7)
        assert result["ee_trajectory"].shape == (5, 3)


class TestNonDefaultProfileChangesOutput:
    """Supplying a non-default MappingProfile must produce different retarget output."""

    def test_different_workspace_scale_changes_ee(self) -> None:
        """A modified workspace_scale shifts the end-effector trajectory."""
        world = np.zeros((3, 33, 3), dtype=np.float32)
        # Non-degenerate right arm so the IK has real segments to solve.
        world[:, 12, :] = [0.0, 0.0, 0.3]
        world[:, 14, :] = [0.25, 0.0, 0.3]
        world[:, 16, :] = [0.5, 0.3, 0.2]
        pose_data = _make_pose_data(3)
        pose_data["world_landmarks"] = world
        pose_data["landmarks"] = world.copy()

        default = retarget_to_robot(pose_data, profile=MappingProfile())
        new_scale = 0.5
        scaled = retarget_to_robot(pose_data, profile=MappingProfile(workspace_scale=new_scale))
        # EE position scales linearly with workspace_scale.
        assert not np.allclose(default["ee_trajectory"], scaled["ee_trajectory"])
        np.testing.assert_allclose(
            scaled["ee_trajectory"],
            default["ee_trajectory"] * (new_scale / MappingProfile().workspace_scale),
            atol=1e-5,
        )

    def test_different_depth_scale_changes_ee(self) -> None:
        """A modified depth_scale changes the transformed trajectory deterministically."""
        world = np.zeros((3, 33, 3), dtype=np.float32)
        world[:, 16, :] = [0.5, 0.3, 0.2]
        pose_data = _make_pose_data(3)
        pose_data["world_landmarks"] = world
        pose_data["landmarks"] = world.copy()

        default = retarget_to_robot(pose_data, profile=MappingProfile())
        deep = retarget_to_robot(pose_data, profile=MappingProfile(depth_scale=2.0))
        assert not np.allclose(default["ee_trajectory"], deep["ee_trajectory"])

    def test_left_handedness_uses_different_landmark(self) -> None:
        """Handedness 'left' tracks landmark 15 instead of 16."""
        world = np.zeros((3, 33, 3), dtype=np.float32)
        world[:, 15, :] = [1.0, 0.0, 0.0]
        world[:, 16, :] = [0.0, 0.0, 0.0]
        pose_data = _make_pose_data(3)
        pose_data["world_landmarks"] = world
        pose_data["landmarks"] = world.copy()

        default = retarget_to_robot(pose_data, profile=MappingProfile())
        lefty = retarget_to_robot(pose_data, profile=MappingProfile(handedness="left"))

        np.testing.assert_allclose(default["ee_trajectory"], 0.0, atol=1e-6)
        assert not np.allclose(lefty["ee_trajectory"], 0.0)

    def test_identity_axis_mapping_produces_different_output(self) -> None:
        """AxisMapping(x='x', y='y', z='z') is a different transform."""
        world = np.zeros((3, 33, 3), dtype=np.float32)
        world[:, 16, :] = [0.1, 0.2, 0.3]
        pose_data = _make_pose_data(3)
        pose_data["world_landmarks"] = world
        pose_data["landmarks"] = world.copy()

        default = retarget_to_robot(pose_data, profile=MappingProfile())
        identity = retarget_to_robot(
            pose_data,
            profile=MappingProfile(axis_mapping=AxisMapping(x="x", y="y", z="z")),
        )

        assert not np.allclose(default["ee_trajectory"], identity["ee_trajectory"])

    def test_explicit_wrist_landmark_overrides_handedness(self) -> None:
        """Explicit wrist_landmark_index takes precedence over handedness."""
        world = np.zeros((3, 33, 3), dtype=np.float32)
        world[:, 15, :] = [1.0, 0.0, 0.0]
        world[:, 16, :] = [0.0, 0.5, 0.0]
        pose_data = _make_pose_data(3)
        pose_data["world_landmarks"] = world
        pose_data["landmarks"] = world.copy()

        left_override = retarget_to_robot(
            pose_data,
            profile=MappingProfile(handedness="right", wrist_landmark_index=15),
        )
        right_override = retarget_to_robot(
            pose_data,
            profile=MappingProfile(handedness="left", wrist_landmark_index=16),
        )

        assert not np.allclose(left_override["ee_trajectory"], 0.0)
        assert not np.allclose(right_override["ee_trajectory"], 0.0)
        assert not np.allclose(left_override["ee_trajectory"], right_override["ee_trajectory"])

    def test_z_clamp_enabled_restricts_z_range(self) -> None:
        """z_clamp_enabled=True should keep Z within [0.05, 1.0]."""
        world = np.zeros((10, 33, 3), dtype=np.float32)
        world[:, 16, :] = [0.0, 5.0, 0.0]
        pose_data = _make_pose_data(10)
        pose_data["world_landmarks"] = world
        pose_data["landmarks"] = world.copy()

        profile = MappingProfile(
            z_clamp_enabled=True,
            axis_mapping=AxisMapping(x="y", y="-x", z="z"),
        )
        result = retarget_to_robot(pose_data, profile=profile)
        z_vals = result["ee_trajectory"][:, 2]
        assert np.all(z_vals >= 0.05)
        assert np.all(z_vals <= 1.0)
        assert np.max(z_vals) <= 1.0

    def test_profile_appears_in_output(self) -> None:
        """The non-default profile is serialized into the result."""
        custom = MappingProfile(handedness="left", depth_scale=0.5)
        result = retarget_to_robot(_make_pose_data(2), profile=custom)
        assert result["mapping_profile"]["handedness"] == "left"
        assert result["mapping_profile"]["depth_scale"] == 0.5


class TestMappingProfileDomain:
    """Unit tests for the MappingProfile model itself."""

    def test_default_profile_factory(self) -> None:
        """MappingProfile() creates a valid default with all required fields."""
        mp = MappingProfile()
        d = mp.model_dump()
        assert d["profile_version"] == 1
        assert d["handedness"] == "right"
        assert isinstance(d["axis_mapping"], dict)

    def test_profile_json_roundtrip(self) -> None:
        """Profile survives model_dump → model_validate round-trip."""
        original = MappingProfile(depth_scale=0.8, z_clamp_enabled=True)
        reloaded = MappingProfile.model_validate(original.model_dump())
        assert reloaded.depth_scale == 0.8
        assert reloaded.z_clamp_enabled is True
        assert reloaded.axis_mapping.x == original.axis_mapping.x

    def test_invalid_handedness_is_rejected(self) -> None:
        """Only 'left' and 'right' handedness values are valid."""
        try:
            MappingProfile(handedness="center")
        except Exception as exc:
            assert "left" in str(exc) or "right" in str(exc)
        else:
            raise AssertionError("Expected invalid handedness to fail validation")

    def test_duplicate_axis_mapping_is_rejected(self) -> None:
        """AxisMapping must use each MediaPipe source axis at most once."""
        try:
            AxisMapping(x="x", y="x", z="z")
        except Exception as exc:
            assert "each source axis" in str(exc)
        else:
            raise AssertionError("Expected duplicate axis mapping to fail validation")
