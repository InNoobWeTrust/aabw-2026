"""Headless 3D rendering of the Franka Panda executing joint trajectories using Pinocchio."""

import logging
import subprocess
import sys
from pathlib import Path

import cv2
import matplotlib
import numpy as np

# Use headless backend
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pinocchio as pin

_logger = logging.getLogger(__name__)


def find_panda_urdf() -> Path:
    """Locate the panda.urdf file inside the virtual environment or python packages.

    Returns:
        Path to the panda.urdf file.

    Raises:
        FileNotFoundError: If the URDF file cannot be found in python packages.
    """
    import site

    # Try site-packages paths first
    paths_to_check = sys.path + site.getsitepackages()
    for p in paths_to_check:
        site_path = Path(p)
        if "site-packages" in site_path.parts or site_path.name == "site-packages":
            # Check inside cmeel.prefix (default for cmeel-packaged assets)
            urdf = (
                site_path
                / "cmeel.prefix"
                / "share"
                / "example-robot-data"
                / "robots"
                / "panda_description"
                / "urdf"
                / "panda.urdf"
            )
            if urdf.is_file():
                return urdf
            # Check direct share path
            urdf2 = (
                site_path
                / "share"
                / "example-robot-data"
                / "robots"
                / "panda_description"
                / "urdf"
                / "panda.urdf"
            )
            if urdf2.is_file():
                return urdf2

    # Recursive fallback search in site-packages directories
    for p in sys.path:
        site_path = Path(p)
        if site_path.name == "site-packages":
            candidates = list(site_path.glob("**/panda.urdf"))
            if candidates:
                return candidates[0]

    raise FileNotFoundError("Could not locate panda.urdf in python packages.")


def compute_panda_fk(q: np.ndarray, model: pin.Model, data: pin.Data) -> np.ndarray:
    """Compute the 3D coordinates of all joints of a Franka Panda robot using Pinocchio.

    Args:
        q: [7] joint angles in radians.
        model: Loaded Pinocchio model.
        data: Pinocchio data structure.

    Returns:
        [10, 3] numpy array containing XYZ coordinates for:
        [link0, link1, link2, link3, link4, link5, link6, link7, hand, hand_tcp]
    """
    q_full = np.zeros(9)
    q_full[:7] = q  # Franka Panda has 7 joints for the arm; last 2 are fingers.

    pin.forwardKinematics(model, data, q_full)
    pin.updateFramePlacements(model, data)

    frame_names = [
        "panda_link0",
        "panda_link1",
        "panda_link2",
        "panda_link3",
        "panda_link4",
        "panda_link5",
        "panda_link6",
        "panda_link7",
        "panda_hand",
        "panda_hand_tcp",
    ]

    positions = []
    for name in frame_names:
        frame_id = model.getFrameId(name)
        positions.append(data.oMf[frame_id].translation)

    return np.array(positions)


def render_simulation_video(
    joint_trajectory: np.ndarray,
    output_path: str | Path,
    fps: int = 10,
    width: int = 640,
    height: int = 480,
) -> Path:
    """Render the joint trajectory to an MP4 video of the Franka Panda moving in 3D.

    Uses Pinocchio for forward kinematics, Matplotlib for rendering,
    and FFmpeg to transcode to H.264 MP4.

    Args:
        joint_trajectory: [T, 7] numpy array of joint angles in radians.
        output_path: Path to write the final browser-playable MP4 file.
        fps: Frames per second (default 10).
        width: Video width (default 640).
        height: Video height (default 480).

    Returns:
        Path to the generated MP4 file.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    n_frames = joint_trajectory.shape[0]
    if n_frames == 0:
        raise ValueError("Cannot render empty joint trajectory.")

    # Load robot model
    urdf_path = find_panda_urdf()
    model = pin.buildModelFromUrdf(str(urdf_path))
    data = model.createData()

    # Write a temporary video file with raw mp4v codec
    temp_path = output_path.parent / f"temp_raw_{output_path.name}"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(temp_path), fourcc, fps, (width, height))

    # Setup matplotlib dark theme figure
    fig = plt.figure(figsize=(width / 100, height / 100), dpi=100)
    fig.patch.set_facecolor("#0f172a")

    ax = fig.add_subplot(111, projection="3d")

    # Pre-calculate forward kinematics for all frames to draw the EE trail
    all_joint_positions = [
        compute_panda_fk(joint_trajectory[t], model, data) for t in range(n_frames)
    ]
    ee_trail = np.array([pts[-1] for pts in all_joint_positions])

    try:
        for t in range(n_frames):
            ax.clear()
            ax.set_facecolor("#0f172a")

            # Style pane borders and grid
            ax.grid(True, color="#334155", linestyle=":")
            ax.xaxis.pane.fill = False
            ax.yaxis.pane.fill = False
            ax.zaxis.pane.fill = False
            ax.xaxis.pane.set_edgecolor("#1e293b")
            ax.yaxis.pane.set_edgecolor("#1e293b")
            ax.zaxis.pane.set_edgecolor("#1e293b")

            # Tick colors
            ax.tick_params(colors="#64748b", labelsize=8)

            # Ground grid representation
            grid_lims = np.linspace(-0.8, 0.8, 5)
            grid_x, grid_y = np.meshgrid(grid_lims, grid_lims)
            grid_z = np.zeros_like(grid_x)
            ax.plot_wireframe(grid_x, grid_y, grid_z, color="#1e293b", alpha=0.6, linewidth=1)

            # End-Effector Trail (amber glow)
            if t > 0:
                ax.plot(
                    ee_trail[:t, 0],
                    ee_trail[:t, 1],
                    ee_trail[:t, 2],
                    color="#fbbf24",
                    alpha=0.7,
                    linewidth=2,
                    label="EE Trail",
                )

            # Current arm position
            pts = all_joint_positions[t]  # [10, 3]

            # Links (thick teal lines)
            ax.plot(
                pts[:, 0],
                pts[:, 1],
                pts[:, 2],
                color="#00d4aa",
                linewidth=5,
                solid_capstyle="round",
                solid_joinstyle="round",
                label="Franka Panda",
            )

            # Joints (rose points for the 7 arm joints + hand link)
            ax.scatter(
                pts[:-1, 0],
                pts[:-1, 1],
                pts[:-1, 2],
                color="#f43f5e",
                s=40,
                depthshade=False,
            )

            # End-Effector (gold star at TCP)
            ax.scatter(
                pts[-1, 0],
                pts[-1, 1],
                pts[-1, 2],
                color="#fbbf24",
                marker="*",
                s=120,
                depthshade=False,
            )

            # Labels and limits
            ax.set_xlim(-0.9, 0.9)
            ax.set_ylim(-0.9, 0.9)
            ax.set_zlim(0.0, 1.2)

            ax.set_xlabel("X (m)", color="#64748b", fontsize=8)
            ax.set_ylabel("Y (m)", color="#64748b", fontsize=8)
            ax.set_zlabel("Z (m)", color="#64748b", fontsize=8)

            # Title / text annotations
            ax.text2D(
                0.02,
                0.95,
                "Franka Panda Simulation",
                transform=ax.transAxes,
                color="#f1f5f9",
                fontsize=11,
                fontweight="bold",
            )
            ax.text2D(
                0.02,
                0.90,
                f"Frame: {t}/{n_frames}  |  Time: {t / 10.0:.1f}s",
                transform=ax.transAxes,
                color="#94a3b8",
                fontsize=9,
            )

            # View angles: isometric viewpoint
            ax.view_init(elev=25, azim=45)

            # Draw canvas and write to video
            fig.canvas.draw()
            rgba_buffer = fig.canvas.buffer_rgba()
            frame_np = np.asarray(rgba_buffer)
            frame_bgr = cv2.cvtColor(frame_np, cv2.COLOR_RGBA2BGR)
            writer.write(frame_bgr)

    finally:
        plt.close(fig)
        writer.release()

    # Transcode raw video to H.264 browser-compatible format using FFmpeg
    try:
        _logger.info("Transcoding simulation video to browser-compatible H.264")
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
        # Delete the temporary raw file
        temp_path.unlink(missing_ok=True)
    except Exception:
        _logger.exception("Failed to transcode video with FFmpeg. Renaming raw video to final.")
        if temp_path.exists():
            temp_path.rename(output_path)

    return output_path
