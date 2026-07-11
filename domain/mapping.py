"""Mapping profile model for deterministic retarget configuration."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class AxisMapping(BaseModel):
    """Defines how MediaPipe world axes map to robot base frame axes.

    Each field is a string expression where:
    - The sign character (optional '-' prefix) indicates direction inversion
    - The axis character ('x', 'y', or 'z') indicates the source MediaPipe axis

    Examples:
        AxisMapping(x="-z", y="-x", z="y")  — default MediaPipe→robot mapping
        AxisMapping(x="x",  y="y",  z="z")  — identity mapping (no transform)
    """

    x: str = Field(default="-z", description="Source axis for robot X")
    y: str = Field(default="-x", description="Source axis for robot Y")
    z: str = Field(default="y", description="Source axis for robot Z")

    @field_validator("x", "y", "z")
    @classmethod
    def validate_axis_expression(cls, value: str) -> str:
        """Allow only simple signed axis expressions such as ``x`` or ``-z``."""
        axis = value[1:] if value.startswith("-") else value
        if axis not in {"x", "y", "z"}:
            raise ValueError("Axis expression must reference x, y, or z")
        if value.startswith("-") and len(value) != 2:
            raise ValueError("Signed axis expressions must look like '-x', '-y', or '-z'")
        if not value.startswith("-") and len(value) != 1:
            raise ValueError("Unsigned axis expressions must be exactly one axis character")
        return value

    @model_validator(mode="after")
    def validate_unique_source_axes(self) -> AxisMapping:
        """Require a one-to-one mapping from MediaPipe axes to robot axes."""
        source_axes = [expr[-1] for expr in (self.x, self.y, self.z)]
        if len(set(source_axes)) != 3:
            raise ValueError("AxisMapping must use each source axis at most once")
        return self


class MappingProfile(BaseModel):
    """Deterministic configuration controlling how human pose maps to robot motion.

    The default profile reproduces the existing hardcoded wrist-only retarget
    behavior. Non-default profiles enable future agentic calibration to adjust
    mapping assumptions without changing pipeline code.
    """

    profile_version: int = Field(
        default=1,
        ge=1,
        description="Schema version for forward compatibility",
    )
    handedness: Literal["right", "left"] = Field(
        default="right",
        description="Which arm to track when no explicit wrist index is provided",
    )
    wrist_landmark_index: int | None = Field(
        default=None,
        ge=0,
        le=32,
        description="Explicit MediaPipe landmark index for end-effector tracking",
    )
    workspace_scale: float = Field(
        default=1.2214285714285714,
        gt=0.0,
        description="Scale factor from human reach to robot workspace",
    )
    depth_scale: float = Field(
        default=1.0,
        gt=0.0,
        description="Multiplier applied to the MediaPipe camera-depth axis before remapping",
    )
    axis_mapping: AxisMapping = Field(
        default_factory=AxisMapping,
        description="MediaPipe→robot coordinate frame axis permutation",
    )
    z_clamp_enabled: bool = Field(
        default=False,
        description="Clamp EE Z coordinate to valid robot workspace range",
    )
    position_only: bool = Field(
        default=True,
        description=(
            "Reserved for future solver upgrades; the current geometric baseline always uses "
            "position-only retargeting"
        ),
    )
