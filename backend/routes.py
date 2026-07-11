"""API routes: upload, pipeline trigger, status polling, download, and list endpoints."""

import io
import logging
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from backend.assistant_service import ReviewAssistantService
from backend.assistant_store import FileSystemAssistantStore
from backend.auth import (
    _decode_token,
    authenticate_password,
    create_access_token,
    oauth2_scheme,
    require_admin_identity,
    require_authenticated_identity,
    require_authenticated_identity_optional_query,
)
from backend.config import settings
from backend.job_store import FileSystemJobStore
from backend.models import (
    ArtifactManifestResponse,
    AssistantMessageCreateRequest,
    AssistantMessageResponse,
    AssistantSessionCreateRequest,
    AssistantSessionDetailResponse,
    AssistantSessionListResponse,
    AssistantSessionResponse,
    JobListResponse,
    JobResponse,
    LoginRequest,
    ReviewListResponse,
    ReviewSnapshotResponse,
    SessionSummary,
    SessionSummaryListResponse,
    TokenResponse,
)
from backend.queue_manager import InProcessQueueManager
from backend.review_service import (
    ReviewService,
    build_pose_review_factory,
    build_retarget_review_factory,
)
from backend.review_store import FileSystemReviewStore
from domain.auth import SessionIdentity
from domain.enums import AssistantSessionStatus, JobStatus, PipelineStage, ReviewStage
from domain.jobs import JobEvent, JobOwner, JobSnapshot
from domain.mapping import MappingProfile
from domain.reviews import AssistantMessage, AssistantSessionSnapshot, ReviewSnapshot
from pipeline.evaluate import evaluate_trajectory
from pipeline.package import package_lerobot, package_lerobot_skeleton
from pipeline.pose import extract_pose_from_video
from pipeline.pose_artifacts import (
    compute_pose_review_metrics,
    flatten_skeleton_features,
    render_skeleton_overlay_video,
    render_skeleton_preview_video,
)
from pipeline.preprocess import extract_frames
from pipeline.render_sim import render_simulation_video
from pipeline.retarget import retarget_to_robot
from pipeline.staged_review import run_static_checks

router = APIRouter()

_logger = logging.getLogger(__name__)

_job_store = FileSystemJobStore(settings.jobs_dir)
_review_store = FileSystemReviewStore(settings.jobs_dir)
_assistant_store = FileSystemAssistantStore(settings.jobs_dir)
_review_service = ReviewService(_review_store)
_assistant_service = ReviewAssistantService(_assistant_store, _job_store, _review_store)

ALLOWED_EXTENSIONS = {".mp4", ".mov", ".avi", ".webm"}


# --------------------------------------------------------------------------- #
# Route-local helpers
# --------------------------------------------------------------------------- #


def _owner_from_identity(identity: SessionIdentity) -> JobOwner:
    """Derive a JobOwner record from an authenticated SessionIdentity."""
    return JobOwner(role=identity.role, judge_session_id=identity.judge_session_id)


def _can_access_job(identity: SessionIdentity, snapshot: JobSnapshot) -> bool:
    """Return True if *identity* is permitted to access *snapshot*."""
    if identity.is_admin:
        return True
    if identity.judge_session_id is None:
        return False
    return snapshot.owner.judge_session_id == identity.judge_session_id


def _snapshot_to_response(snapshot: JobSnapshot) -> JobResponse:
    """Map a domain JobSnapshot to an HTTP JobResponse."""
    return JobResponse(
        job_id=snapshot.job_id,
        filename=snapshot.original_filename,
        status=snapshot.status,
        progress=snapshot.progress,
        current_stage=snapshot.stage,
        message=snapshot.message,
        created_at=snapshot.created_at,
        completed_at=snapshot.completed_at,
        result=snapshot.result,
    )


def _review_to_response(snapshot: ReviewSnapshot) -> ReviewSnapshotResponse:
    """Map a persisted review snapshot to an HTTP response model."""
    return ReviewSnapshotResponse(**snapshot.model_dump())


def _assistant_session_to_response(snapshot: AssistantSessionSnapshot) -> AssistantSessionResponse:
    """Map a persisted assistant session snapshot to an HTTP response model."""
    return AssistantSessionResponse(**snapshot.model_dump())


def _assistant_message_to_response(message: AssistantMessage) -> AssistantMessageResponse:
    """Map a persisted assistant message to an HTTP response model."""
    return AssistantMessageResponse(**message.model_dump())


def _artifact_manifest(job_id: str, snapshot: JobSnapshot) -> dict:
    """Return a stage-aware artifact manifest for a job."""
    output_dir = Path(snapshot.output_dir)
    reviews_dir = output_dir / "reviews"
    return {
        "original_video": snapshot.upload_path,
        "dataset_skeleton_dir": str(output_dir / "dataset_skeleton"),
        "dataset_robot_dir": str(output_dir / "dataset_robot"),
        "skeleton_overlay_video": str(output_dir / "skeleton_overlay.mp4"),
        "skeleton_preview_video": str(output_dir / "skeleton_preview.mp4"),
        "robot_simulation_video": str(output_dir / "simulation.mp4"),
        "pose_review_dir": str(reviews_dir / ReviewStage.POSE.value),
        "retarget_review_dir": str(reviews_dir / ReviewStage.RETARGET.value),
        "job_download_url": f"/api/jobs/{job_id}/download",
        "dataset_skeleton_zip_url": f"/api/jobs/{job_id}/downloads/dataset_skeleton_zip",
        "dataset_robot_zip_url": f"/api/jobs/{job_id}/downloads/dataset_robot_zip",
        "skeleton_overlay_video_url": f"/api/jobs/{job_id}/downloads/skeleton_overlay_video",
        "skeleton_preview_video_url": f"/api/jobs/{job_id}/downloads/skeleton_preview_video",
        "robot_simulation_video_url": f"/api/jobs/{job_id}/downloads/robot_simulation_video",
        "pose_review_md_url": f"/api/jobs/{job_id}/downloads/pose_review_md",
        "retarget_review_md_url": f"/api/jobs/{job_id}/downloads/retarget_review_md",
        "assistant_sessions_url": f"/api/jobs/{job_id}/assistant/sessions",
    }


def _review_stage_or_404(stage: str) -> ReviewStage:
    """Parse a string review stage or raise a 404-like HTTPException."""
    try:
        return ReviewStage(stage)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown review stage '{stage}'") from exc


def _artifact_path_for_key(snapshot: JobSnapshot, artifact_key: str) -> Path:
    """Resolve a known artifact key to an on-disk path under the job output tree."""
    output_dir = Path(snapshot.output_dir)
    mapping = {
        "dataset_skeleton_zip": output_dir / "dataset_skeleton",
        "dataset_robot_zip": output_dir / "dataset_robot",
        "skeleton_overlay_video": output_dir / "skeleton_overlay.mp4",
        "skeleton_preview_video": output_dir / "skeleton_preview.mp4",
        "robot_simulation_video": output_dir / "simulation.mp4",
        "pose_review_md": output_dir / "reviews" / ReviewStage.POSE.value / "review.md",
        "retarget_review_md": output_dir / "reviews" / ReviewStage.RETARGET.value / "review.md",
    }
    try:
        return mapping[artifact_key]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown artifact '{artifact_key}'") from exc


def _zip_path_response(path: Path, filename: str) -> StreamingResponse:
    """Return a StreamingResponse containing a zip of one file or directory."""
    if not path.exists():
        raise HTTPException(status_code=404, detail="Requested path not found")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if path.is_file():
            zf.write(path, path.name)
        else:
            for file_path in path.rglob("*"):
                if file_path.is_file():
                    zf.write(file_path, file_path.relative_to(path))
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _update_job(
    job_id: str,
    status: JobStatus,
    progress: float,
    stage: PipelineStage,
    message: str,
    failure_reason: str | None = None,
    result: dict | None = None,
) -> None:
    """Persist a pipeline progress update through the job store."""
    kwargs: dict = {
        "status": status,
        "progress": progress,
        "stage": stage,
        "message": message,
    }
    if result is not None:
        kwargs["result"] = result
    if status.is_terminal():
        kwargs["completed_at"] = datetime.now(timezone.utc)
    _job_store.update_job(job_id, **kwargs)

    event = JobEvent(
        at=datetime.now(timezone.utc),
        job_id=job_id,
        status=status,
        stage=stage,
        message=message,
        failure_reason=failure_reason,
    )
    _job_store.append_event(job_id, event)


def _schedule_pose_review(job_id: str) -> None:
    """Schedule async pose review for an artifact-complete job."""
    current_snapshot = _job_store.get_job(job_id)
    pose_context = _build_pose_review_context(job_id, current_snapshot)
    _review_service.schedule_review(
        job_id=job_id,
        stage=ReviewStage.POSE,
        context_manifest=pose_context,
        review_factory=build_pose_review_factory(
            metrics=pose_context["metrics"],
            artifact_manifest=pose_context["artifact_manifest"],
        ),
    )


def _schedule_retarget_review(
    job_id: str,
    eval_result: dict,
    joint_trajectory: np.ndarray,
) -> None:
    """Schedule async retarget review for an artifact-complete job."""
    current_snapshot = _job_store.get_job(job_id)
    artifact_manifest = _artifact_manifest(job_id, current_snapshot)
    pose_summary = None
    if _review_store.review_exists(job_id, ReviewStage.POSE):
        try:
            pose_snapshot = _review_store.get_review(job_id, ReviewStage.POSE)
            pose_summary = {
                "status": pose_snapshot.status.value,
                "verdict": pose_snapshot.verdict.value if pose_snapshot.verdict else None,
                "summary": pose_snapshot.summary,
            }
        except Exception:
            pose_summary = None
    _review_service.schedule_review(
        job_id=job_id,
        stage=ReviewStage.RETARGET,
        context_manifest={
            "metrics": {
                "joint_limit_violations": eval_result.get("joint_limit_violations", 0),
                "nan_count": eval_result.get("nan_count", 0),
                "max_velocity": eval_result.get("max_velocity", 0.0),
                "mean_jerk": eval_result.get("mean_jerk", 0.0),
                "sudden_jump_count": eval_result.get("sudden_jump_count", 0),
                "completeness_ratio": eval_result.get("completeness_ratio", 0.0),
            },
            "artifact_manifest": artifact_manifest,
        },
        review_factory=build_retarget_review_factory(
            eval_result=eval_result,
            joint_trajectory=joint_trajectory,
            artifact_manifest=artifact_manifest,
            pose_review_summary=pose_summary,
        ),
    )


def _build_pose_review_context(job_id: str, snapshot: JobSnapshot) -> dict:
    """Build a bounded pose-review context from persisted job results."""
    result = snapshot.result or {}
    pose = result.get("pose", {})
    return {
        "metrics": pose.get("metrics", {}),
        "artifact_manifest": _artifact_manifest(job_id, snapshot),
    }


def _complete_with_pose_only_result(
    *,
    job_id: str,
    snapshot: JobSnapshot,
    pose_result: dict,
    pose_metrics: dict,
    preprocess_result: dict,
    skeleton_overlay_path: Path,
    skeleton_preview_path: Path,
    skeleton_pkg_result: dict,
    retarget_error: str,
) -> None:
    """Complete the job with skeleton-only artifacts when retargeting fails."""
    result_payload = {
        "artifacts": _artifact_manifest(job_id, snapshot),
        "preprocess": preprocess_result,
        "pose": {
            "frame_count": pose_result["frame_count"],
            "detected_frame_count": pose_result["detected_frame_count"],
            "detection_rate": pose_result["detection_rate"],
            "metrics": pose_metrics,
            "artifacts": {
                "overlay_video": str(skeleton_overlay_path),
                "preview_video": str(skeleton_preview_path),
            },
            "dataset": skeleton_pkg_result,
            "review": {"status": "pending", "verdict": None},
        },
        "retarget": {
            "error": retarget_error,
            "review": {"status": "failed", "verdict": None},
        },
    }
    _update_job(
        job_id,
        JobStatus.COMPLETED,
        1.0,
        PipelineStage.RETARGET,
        "Pipeline completed with skeleton-only artifacts; retargeting failed",
        result=result_payload,
    )


async def _run_pipeline(job_id: str) -> None:
    """Run the full pipeline, persist dual artifact branches, and schedule async reviews."""
    import asyncio as _asyncio

    loop = _asyncio.get_running_loop()

    try:
        snapshot = _job_store.get_job(job_id)
    except FileNotFoundError:
        _logger.warning("Pipeline aborted: job %s no longer exists", job_id)
        return

    video_path = snapshot.upload_path
    out = Path(snapshot.output_dir)
    job_root = out.parent
    frames_dir = job_root / "work" / "frames"
    pose_work_dir = job_root / "work" / "pose"
    pose_work_dir.mkdir(parents=True, exist_ok=True)

    # Stage: PREPROCESS
    try:
        _update_job(
            job_id, JobStatus.RUNNING, 0.05, PipelineStage.PREPROCESS, "Extracting frames..."
        )
        preprocess_result = await loop.run_in_executor(
            None, extract_frames, video_path, str(frames_dir), 10
        )
        _update_job(
            job_id,
            JobStatus.RUNNING,
            0.15,
            PipelineStage.PREPROCESS,
            f"Extracted {preprocess_result['frame_count']} frames",
        )
    except Exception as exc:
        _update_job(
            job_id,
            JobStatus.FAILED,
            0.05,
            PipelineStage.PREPROCESS,
            str(exc),
            failure_reason=str(exc),
        )
        return

    # Stage: POSE + skeleton export
    try:
        _update_job(
            job_id, JobStatus.RUNNING, 0.25, PipelineStage.POSE, "Running MediaPipe Pose..."
        )
        pose_result = await loop.run_in_executor(None, extract_pose_from_video, video_path)
        np.save(pose_work_dir / "landmarks.npy", pose_result["landmarks"])
        np.save(pose_work_dir / "world_landmarks.npy", pose_result["world_landmarks"])
        np.save(pose_work_dir / "confidence.npy", pose_result["confidence"])

        skeleton_overlay_path = out / "skeleton_overlay.mp4"
        skeleton_preview_path = out / "skeleton_preview.mp4"
        await loop.run_in_executor(
            None,
            render_skeleton_overlay_video,
            video_path,
            pose_result,
            skeleton_overlay_path,
        )
        await loop.run_in_executor(
            None,
            render_skeleton_preview_video,
            pose_result,
            skeleton_preview_path,
        )

        skeleton_features = flatten_skeleton_features(pose_result)
        skeleton_dataset_dir = out / "dataset_skeleton"
        pose_metrics = compute_pose_review_metrics(pose_result)
        pose_metadata = {
            "job_id": job_id,
            "video": Path(video_path).name,
            "robot": "human_skeleton",
            "representation": "mediapipe_world_landmarks_flattened",
            "landmark_count": 33,
            "fps": 10,
            "quality": pose_metrics,
        }
        skeleton_pkg_result = await loop.run_in_executor(
            None,
            package_lerobot_skeleton,
            skeleton_features,
            pose_metadata,
            str(skeleton_dataset_dir),
        )
        detected_message = (
            "Detected pose in "
            f"{pose_result['detected_frame_count']}/{pose_result['frame_count']} frames "
            f"({pose_result['detection_rate']:.0%})"
        )
        _update_job(
            job_id,
            JobStatus.RUNNING,
            0.40,
            PipelineStage.POSE,
            detected_message,
        )
    except Exception as exc:
        _update_job(
            job_id, JobStatus.FAILED, 0.25, PipelineStage.POSE, str(exc), failure_reason=str(exc)
        )
        return

    # Stage: RETARGET
    try:
        _update_job(
            job_id, JobStatus.RUNNING, 0.50, PipelineStage.RETARGET, "Retargeting to robot..."
        )
        baseline_profile = MappingProfile()
        retarget_result = await loop.run_in_executor(
            None, retarget_to_robot, pose_result, settings.target_robot, baseline_profile
        )
        _update_job(
            job_id,
            JobStatus.RUNNING,
            0.65,
            PipelineStage.RETARGET,
            f"Generated {retarget_result['frame_count']} joint frames",
        )
    except Exception as exc:
        _complete_with_pose_only_result(
            job_id=job_id,
            snapshot=snapshot,
            pose_result=pose_result,
            pose_metrics=pose_metrics,
            preprocess_result=preprocess_result,
            skeleton_overlay_path=skeleton_overlay_path,
            skeleton_preview_path=skeleton_preview_path,
            skeleton_pkg_result=skeleton_pkg_result,
            retarget_error=str(exc),
        )
        _schedule_pose_review(job_id)
        return

    # Stage: EVALUATE
    try:
        _update_job(
            job_id,
            JobStatus.RUNNING,
            0.75,
            PipelineStage.EVALUATE,
            "Evaluating trajectory quality...",
        )
        eval_result = await loop.run_in_executor(
            None,
            evaluate_trajectory,
            retarget_result["joint_trajectory"],
            settings.target_robot,
        )
        _update_job(
            job_id,
            JobStatus.RUNNING,
            0.82,
            PipelineStage.EVALUATE,
            f"Quality: {eval_result['overall_grade']}",
        )
    except Exception as exc:
        _update_job(
            job_id,
            JobStatus.FAILED,
            0.75,
            PipelineStage.EVALUATE,
            str(exc),
            failure_reason=str(exc),
        )
        return

    # Stage: PACKAGE robot dataset
    try:
        _update_job(
            job_id, JobStatus.RUNNING, 0.88, PipelineStage.PACKAGE, "Packaging robot dataset..."
        )
        robot_dataset_dir = out / "dataset_robot"
        metadata = {
            "job_id": job_id,
            "video": Path(video_path).name,
            "robot": retarget_result["robot"],
            "quality": eval_result,
        }
        robot_pkg_result = await loop.run_in_executor(
            None,
            package_lerobot,
            retarget_result["joint_trajectory"],
            retarget_result["ee_trajectory"],
            metadata,
            str(robot_dataset_dir),
        )
    except Exception as exc:
        _update_job(
            job_id, JobStatus.FAILED, 0.88, PipelineStage.PACKAGE, str(exc), failure_reason=str(exc)
        )
        return

    # Stage: FINALIZE
    try:
        _update_job(
            job_id, JobStatus.RUNNING, 0.92, PipelineStage.FINALIZE, "Rendering simulation video..."
        )
        sim_video_path = out / "simulation.mp4"
        await loop.run_in_executor(
            None, render_simulation_video, retarget_result["joint_trajectory"], sim_video_path
        )

        _update_job(
            job_id, JobStatus.RUNNING, 0.96, PipelineStage.FINALIZE, "Finalizing artifacts..."
        )
        static_checks = run_static_checks(robot_dataset_dir)

        traj = retarget_result["joint_trajectory"]
        step = max(1, len(traj) // 50)
        downsampled = traj[::step].tolist()

        result_payload = {
            "artifacts": _artifact_manifest(job_id, snapshot),
            "preprocess": preprocess_result,
            "pose": {
                "frame_count": pose_result["frame_count"],
                "detected_frame_count": pose_result["detected_frame_count"],
                "detection_rate": pose_result["detection_rate"],
                "metrics": pose_metrics,
                "artifacts": {
                    "overlay_video": str(skeleton_overlay_path),
                    "preview_video": str(skeleton_preview_path),
                },
                "dataset": skeleton_pkg_result,
                "review": {"status": "pending", "verdict": None},
            },
            "retarget": {
                "frame_count": retarget_result["frame_count"],
                "robot": retarget_result["robot"],
                "evaluation": eval_result,
                "artifacts": {"simulation_video": str(sim_video_path)},
                "dataset": robot_pkg_result,
                "mapping_profile": retarget_result["mapping_profile"],
                "review": {"status": "pending", "verdict": None},
            },
            "static_checks": static_checks,
            "downsampled_trajectory": downsampled,
        }
        _update_job(
            job_id,
            JobStatus.COMPLETED,
            1.0,
            PipelineStage.FINALIZE,
            "Pipeline completed successfully",
            result=result_payload,
        )
    except Exception as exc:
        _update_job(
            job_id,
            JobStatus.FAILED,
            0.92,
            PipelineStage.FINALIZE,
            str(exc),
            failure_reason=str(exc),
        )
        return

    _schedule_pose_review(job_id)
    _schedule_retarget_review(job_id, eval_result, retarget_result["joint_trajectory"])


_queue_manager = InProcessQueueManager(
    job_store=_job_store,
    runner_factory=_run_pipeline,
    queue_root=settings.data_dir / "queue",
)


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #


@router.get("/health")
def health():
    """Unauthenticated health check for load balancers and orchestrators."""
    return {"status": "ok"}


# --------------------------------------------------------------------------- #
# Auth routes
# --------------------------------------------------------------------------- #


@router.post("/auth/login", response_model=TokenResponse)
def login(body: LoginRequest):
    """Verify the password and return a JWT access token with role and session identity."""
    identity = authenticate_password(body.password)
    token = create_access_token(identity)
    return TokenResponse(
        access_token=token,
        role=identity.role.value,
        judge_session_id=identity.judge_session_id,
    )


@router.get("/auth/verify")
def verify(token: str = Depends(oauth2_scheme)):
    """Return token validity, role, session identity, and expiration timestamp."""
    payload = _decode_token(token)
    return {
        "valid": True,
        "role": payload.get("role"),
        "judge_session_id": payload.get("judge_session_id"),
        "exp": payload.get("exp"),
    }


# --------------------------------------------------------------------------- #
# Job routes
# --------------------------------------------------------------------------- #


@router.post("/jobs/upload", response_model=JobResponse)
async def upload_video(
    video: UploadFile = File(...),
    identity: SessionIdentity = Depends(require_authenticated_identity),
):
    """Accept an uploaded video, validate it, create a job, and enqueue the pipeline."""
    ext = Path(video.filename or "video.mp4").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file extension '{ext}'. Allowed: {allowed}",
        )

    contents = await video.read()
    max_bytes = settings.max_video_size_mb * 1024 * 1024
    if len(contents) > max_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"File exceeds the {settings.max_video_size_mb}MB size limit",
        )

    if identity.is_judge and identity.judge_session_id is not None:
        active_count = _job_store.count_active_jobs_for_session(identity.judge_session_id)
        if active_count >= 1:
            raise HTTPException(
                status_code=409,
                detail="You already have an active job. Please wait for it to complete.",
            )

    owner = _owner_from_identity(identity)
    snapshot = _job_store.create_job(
        owner=owner,
        original_filename=video.filename or "video.mp4",
        source_extension=ext,
    )

    upload_path = Path(snapshot.upload_path)
    upload_path.parent.mkdir(parents=True, exist_ok=True)
    upload_path.write_bytes(contents)

    _queue_manager.enqueue(snapshot.job_id)

    return _snapshot_to_response(snapshot)


@router.get("/jobs", response_model=JobListResponse)
def list_jobs(identity: SessionIdentity = Depends(require_authenticated_identity)):
    """Return all jobs visible to the caller, sorted newest-first."""
    if identity.is_admin:
        snapshots = _job_store.list_all_jobs()
    elif identity.judge_session_id is not None:
        snapshots = _job_store.list_jobs_for_session(identity.judge_session_id)
    else:
        snapshots = []
    responses = [_snapshot_to_response(s) for s in snapshots]
    return JobListResponse(jobs=responses, total=len(responses))


@router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: str, identity: SessionIdentity = Depends(require_authenticated_identity)):
    """Return the status of a single job.

    Judges may only access their own jobs. Admins may access any job.
    Returns 404 (not 403) for inaccessible jobs to avoid existence leaks.
    """
    try:
        snapshot = _job_store.get_job(job_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found") from None
    if not _can_access_job(identity, snapshot):
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return _snapshot_to_response(snapshot)


@router.get("/jobs/{job_id}/video/original")
def get_original_video(
    job_id: str,
    identity: SessionIdentity = Depends(require_authenticated_identity_optional_query),
):
    """Stream/serve the original uploaded video file."""
    try:
        snapshot = _job_store.get_job(job_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found") from None

    if not _can_access_job(identity, snapshot):
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    video_path = Path(snapshot.upload_path)
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Original video file not found")

    return FileResponse(str(video_path))


@router.get("/jobs/{job_id}/video/simulation")
def get_simulation_video(
    job_id: str,
    identity: SessionIdentity = Depends(require_authenticated_identity_optional_query),
):
    """Stream/serve the rendered simulation video file."""
    try:
        snapshot = _job_store.get_job(job_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found") from None

    if not _can_access_job(identity, snapshot):
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    if snapshot.status != JobStatus.COMPLETED:
        raise HTTPException(
            status_code=400,
            detail=(
                "Simulation video only available for completed jobs "
                f"(current status: {snapshot.status.value})"
            ),
        )

    sim_path = Path(snapshot.output_dir) / "simulation.mp4"
    if not sim_path.exists():
        raise HTTPException(status_code=404, detail="Simulation video not found")

    return FileResponse(str(sim_path))


@router.get("/jobs/{job_id}/artifacts", response_model=ArtifactManifestResponse)
def get_artifacts(
    job_id: str,
    identity: SessionIdentity = Depends(require_authenticated_identity),
):
    """Return a manifest of available artifacts and convenience URLs for one job."""
    try:
        snapshot = _job_store.get_job(job_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found") from None
    if not _can_access_job(identity, snapshot):
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return ArtifactManifestResponse(artifacts=_artifact_manifest(job_id, snapshot))


@router.get("/jobs/{job_id}/reviews", response_model=ReviewListResponse)
def list_reviews(
    job_id: str,
    identity: SessionIdentity = Depends(require_authenticated_identity),
):
    """Return both stage review snapshots that currently exist for a job."""
    try:
        snapshot = _job_store.get_job(job_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found") from None
    if not _can_access_job(identity, snapshot):
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    reviews = []
    for stage in ReviewStage:
        if _review_store.review_exists(job_id, stage):
            reviews.append(_review_to_response(_review_store.get_review(job_id, stage)))
    return ReviewListResponse(reviews=reviews)


@router.get("/jobs/{job_id}/reviews/{stage}", response_model=ReviewSnapshotResponse)
def get_review(
    job_id: str,
    stage: str,
    identity: SessionIdentity = Depends(require_authenticated_identity),
):
    """Return the persisted snapshot for one review stage."""
    review_stage = _review_stage_or_404(stage)
    try:
        snapshot = _job_store.get_job(job_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found") from None
    if not _can_access_job(identity, snapshot):
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    if not _review_store.review_exists(job_id, review_stage):
        raise HTTPException(status_code=404, detail=f"Review '{stage}' not found")
    return _review_to_response(_review_store.get_review(job_id, review_stage))


@router.get("/jobs/{job_id}/reviews/{stage}/stream")
async def stream_review(
    job_id: str,
    stage: str,
    identity: SessionIdentity = Depends(require_authenticated_identity_optional_query),
):
    """Stream one review stage over Server-Sent Events with persisted replay."""
    review_stage = _review_stage_or_404(stage)
    try:
        snapshot = _job_store.get_job(job_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found") from None
    if not _can_access_job(identity, snapshot):
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    if not _review_store.review_exists(job_id, review_stage):
        raise HTTPException(status_code=404, detail=f"Review '{stage}' not found")

    async def _event_stream():
        import asyncio as _asyncio
        import json as _json

        sent = 0
        while True:
            events = _review_store.list_events(job_id, review_stage)
            while sent < len(events):
                event = events[sent]
                payload = _json.dumps(event.payload, ensure_ascii=False)
                yield f"event: {event.event}\ndata: {payload}\n\n"
                sent += 1
            review = _review_store.get_review(job_id, review_stage)
            if review.status.is_terminal():
                break
            await _asyncio.sleep(0.25)

    return StreamingResponse(_event_stream(), media_type="text/event-stream")


@router.get("/jobs/{job_id}/assistant/sessions", response_model=AssistantSessionListResponse)
def list_assistant_sessions(
    job_id: str,
    identity: SessionIdentity = Depends(require_authenticated_identity),
):
    """List persisted reviewer-assistant sessions for one job."""
    try:
        snapshot = _job_store.get_job(job_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found") from None
    if not _can_access_job(identity, snapshot):
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    sessions = [_assistant_session_to_response(s) for s in _assistant_store.list_sessions(job_id)]
    return AssistantSessionListResponse(sessions=sessions)


@router.post("/jobs/{job_id}/assistant/sessions", response_model=AssistantSessionDetailResponse)
def create_assistant_session(
    job_id: str,
    body: AssistantSessionCreateRequest,
    identity: SessionIdentity = Depends(require_authenticated_identity),
):
    """Create a reviewer-assistant session and optionally seed the first user message."""
    try:
        snapshot = _job_store.get_job(job_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found") from None
    if not _can_access_job(identity, snapshot):
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    session = _assistant_service.create_session(job_id, title=body.title)
    messages: list[AssistantMessageResponse] = []
    if body.message:
        _assistant_service.submit_user_message(job_id, session.session_id, body.message)
        messages = [
            _assistant_message_to_response(m)
            for m in _assistant_store.list_messages(job_id, session.session_id)
        ]
    return AssistantSessionDetailResponse(
        session=_assistant_session_to_response(
            _assistant_store.get_session(job_id, session.session_id)
        ),
        messages=messages,
    )


@router.get(
    "/jobs/{job_id}/assistant/sessions/{session_id}",
    response_model=AssistantSessionDetailResponse,
)
def get_assistant_session(
    job_id: str,
    session_id: str,
    identity: SessionIdentity = Depends(require_authenticated_identity),
):
    """Return one assistant session plus its transcript."""
    try:
        snapshot = _job_store.get_job(job_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found") from None
    if not _can_access_job(identity, snapshot):
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    try:
        session = _assistant_store.get_session(job_id, session_id)
    except FileNotFoundError:
        raise HTTPException(
            status_code=404, detail=f"Assistant session {session_id} not found"
        ) from None
    messages = [
        _assistant_message_to_response(m)
        for m in _assistant_store.list_messages(job_id, session_id)
    ]
    return AssistantSessionDetailResponse(
        session=_assistant_session_to_response(session),
        messages=messages,
    )


@router.post(
    "/jobs/{job_id}/assistant/sessions/{session_id}/messages",
    response_model=AssistantSessionDetailResponse,
)
def post_assistant_message(
    job_id: str,
    session_id: str,
    body: AssistantMessageCreateRequest,
    identity: SessionIdentity = Depends(require_authenticated_identity),
):
    """Append a user message and trigger one bounded assistant loop."""
    try:
        snapshot = _job_store.get_job(job_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found") from None
    if not _can_access_job(identity, snapshot):
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    try:
        _assistant_store.get_session(job_id, session_id)
    except FileNotFoundError:
        raise HTTPException(
            status_code=404, detail=f"Assistant session {session_id} not found"
        ) from None

    _assistant_service.submit_user_message(job_id, session_id, body.content)
    session = _assistant_store.get_session(job_id, session_id)
    messages = [
        _assistant_message_to_response(m)
        for m in _assistant_store.list_messages(job_id, session_id)
    ]
    return AssistantSessionDetailResponse(
        session=_assistant_session_to_response(session),
        messages=messages,
    )


@router.get("/jobs/{job_id}/assistant/sessions/{session_id}/stream")
async def stream_assistant_session(
    job_id: str,
    session_id: str,
    identity: SessionIdentity = Depends(require_authenticated_identity_optional_query),
):
    """Stream reviewer-assistant events for one session over SSE."""
    try:
        snapshot = _job_store.get_job(job_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found") from None
    if not _can_access_job(identity, snapshot):
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    try:
        _assistant_store.get_session(job_id, session_id)
    except FileNotFoundError:
        raise HTTPException(
            status_code=404, detail=f"Assistant session {session_id} not found"
        ) from None

    async def _event_stream():
        import asyncio as _asyncio
        import json as _json

        sent = 0
        while True:
            events = _assistant_store.list_events(job_id, session_id)
            while sent < len(events):
                event = events[sent]
                payload = _json.dumps(event.payload, ensure_ascii=False)
                yield f"event: {event.event}\ndata: {payload}\n\n"
                sent += 1
            session = _assistant_store.get_session(job_id, session_id)
            if session.status in (AssistantSessionStatus.IDLE, AssistantSessionStatus.FAILED):
                break
            await _asyncio.sleep(0.25)

    return StreamingResponse(_event_stream(), media_type="text/event-stream")


@router.get("/jobs/{job_id}/download")
def download_dataset(
    job_id: str, identity: SessionIdentity = Depends(require_authenticated_identity)
):
    """Stream a zip archive of the complete output directory."""
    try:
        snapshot = _job_store.get_job(job_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found") from None
    if not _can_access_job(identity, snapshot):
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    if snapshot.status != JobStatus.COMPLETED:
        raise HTTPException(
            status_code=400,
            detail=f"Job {job_id} is not complete (status: {snapshot.status.value})",
        )

    return _zip_path_response(Path(snapshot.output_dir), f"{job_id}.zip")


@router.get("/jobs/{job_id}/downloads/{artifact_key}")
def download_artifact(
    job_id: str,
    artifact_key: str,
    identity: SessionIdentity = Depends(require_authenticated_identity_optional_query),
):
    """Download a specific artifact or zipped dataset branch for a job."""
    try:
        snapshot = _job_store.get_job(job_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found") from None
    if not _can_access_job(identity, snapshot):
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    target = _artifact_path_for_key(snapshot, artifact_key)
    if artifact_key.endswith("_zip"):
        if not target.exists():
            raise HTTPException(status_code=404, detail=f"Artifact '{artifact_key}' not found")
        return _zip_path_response(target, f"{job_id}_{artifact_key}.zip")

    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"Artifact '{artifact_key}' not found")
    return FileResponse(str(target))


@router.delete("/jobs/{job_id}")
def delete_job(job_id: str, identity: SessionIdentity = Depends(require_authenticated_identity)):
    """Remove a job from the store and delete all its data from disk.

    Judges may only delete their own jobs. Admins may delete any job.
    Returns 404 for inaccessible or nonexistent jobs.
    """
    try:
        snapshot = _job_store.get_job(job_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found") from None
    if not _can_access_job(identity, snapshot):
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    if snapshot.status == JobStatus.RUNNING:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot delete job {job_id} while it is running",
        )

    _job_store.delete_job(job_id)
    return {"deleted": True}


# --------------------------------------------------------------------------- #
# Admin routes
# --------------------------------------------------------------------------- #


@router.get("/admin/jobs", response_model=JobListResponse)
def list_all_jobs_admin(
    identity: SessionIdentity = Depends(require_admin_identity),
):
    """Return all jobs in the store (admin only, unfiltered)."""
    snapshots = _job_store.list_all_jobs()
    responses = [_snapshot_to_response(s) for s in snapshots]
    return JobListResponse(jobs=responses, total=len(responses))


@router.get("/admin/sessions", response_model=SessionSummaryListResponse)
def list_sessions_admin(
    identity: SessionIdentity = Depends(require_admin_identity),
):
    """Aggregate job counts grouped by judge_session_id (admin only).

    Sessions without a judge_session_id (e.g. admin-submitted jobs or legacy
    jobs with None) are excluded from the summary.
    """
    snapshots = _job_store.list_all_jobs()
    buckets: dict[str, dict[str, int]] = defaultdict(
        lambda: {"total": 0, "active": 0, "completed": 0, "failed": 0}
    )

    for s in snapshots:
        sid = s.owner.judge_session_id
        if sid is None:
            continue
        buckets[sid]["total"] += 1
        if s.status.is_active():
            buckets[sid]["active"] += 1
        elif s.status == JobStatus.COMPLETED:
            buckets[sid]["completed"] += 1
        elif s.status == JobStatus.FAILED:
            buckets[sid]["failed"] += 1

    sessions = [
        SessionSummary(
            judge_session_id=sid,
            total_jobs=counts["total"],
            active_jobs=counts["active"],
            completed_jobs=counts["completed"],
            failed_jobs=counts["failed"],
        )
        for sid, counts in buckets.items()
    ]
    sessions.sort(key=lambda x: x.judge_session_id)
    return SessionSummaryListResponse(sessions=sessions, total=len(sessions))
