"""API routes: upload, pipeline trigger, status polling, download, and list endpoints."""

import io
import logging
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

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
    JobListResponse,
    JobResponse,
    LoginRequest,
    SessionSummary,
    SessionSummaryListResponse,
    TokenResponse,
)
from backend.queue_manager import InProcessQueueManager
from domain.auth import SessionIdentity
from domain.enums import JobStatus, PipelineStage
from domain.jobs import JobEvent, JobOwner, JobSnapshot
from pipeline.evaluate import evaluate_trajectory
from pipeline.package import package_lerobot
from pipeline.pose import extract_pose_from_video
from pipeline.preprocess import extract_frames
from pipeline.render_sim import render_simulation_video
from pipeline.retarget import retarget_to_robot
from pipeline.staged_review import generate_ai_review, run_static_checks

router = APIRouter()

_logger = logging.getLogger(__name__)

_job_store = FileSystemJobStore(settings.jobs_dir)

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


async def _run_pipeline(job_id: str) -> None:
    """Run the full pipeline stages, updating the job store at each step.

    Pipeline stages run synchronously within a thread-pool executor.
    Progress and status are persisted through the job store after every stage.
    """
    import asyncio as _asyncio

    loop = _asyncio.get_running_loop()

    try:
        snapshot = _job_store.get_job(job_id)
    except FileNotFoundError:
        _logger.warning("Pipeline aborted: job %s no longer exists", job_id)
        return

    video_path = snapshot.upload_path
    out = Path(snapshot.output_dir)
    frames_dir = out.parent / "work" / "frames"

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

    # Stage: POSE
    try:
        _update_job(
            job_id, JobStatus.RUNNING, 0.25, PipelineStage.POSE, "Running MediaPipe Pose..."
        )
        pose_result = await loop.run_in_executor(None, extract_pose_from_video, video_path)
        _update_job(
            job_id,
            JobStatus.RUNNING,
            0.40,
            PipelineStage.POSE,
            (
                f"Detected pose in {pose_result['frame_count']} frames "
                f"({pose_result['detection_rate']:.0%})"
            ),
        )
    except Exception as exc:
        _update_job(
            job_id,
            JobStatus.FAILED,
            0.25,
            PipelineStage.POSE,
            str(exc),
            failure_reason=str(exc),
        )
        return

    # Stage: RETARGET
    try:
        _update_job(
            job_id, JobStatus.RUNNING, 0.50, PipelineStage.RETARGET, "Retargeting to robot..."
        )
        retarget_result = await loop.run_in_executor(
            None, retarget_to_robot, pose_result, settings.target_robot
        )
        _update_job(
            job_id,
            JobStatus.RUNNING,
            0.65,
            PipelineStage.RETARGET,
            f"Generated {retarget_result['frame_count']} joint frames",
        )
    except Exception as exc:
        _update_job(
            job_id,
            JobStatus.FAILED,
            0.50,
            PipelineStage.RETARGET,
            str(exc),
            failure_reason=str(exc),
        )
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
            None, evaluate_trajectory, retarget_result["joint_trajectory"], settings.target_robot
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

    # Stage: PACKAGE
    try:
        _update_job(
            job_id, JobStatus.RUNNING, 0.88, PipelineStage.PACKAGE, "Packaging LeRobot dataset..."
        )
        package_dir = out / "dataset"
        metadata = {
            "job_id": job_id,
            "video": Path(video_path).name,
            "robot": retarget_result["robot"],
            "quality": eval_result,
        }
        pkg_result = await loop.run_in_executor(
            None,
            package_lerobot,
            retarget_result["joint_trajectory"],
            retarget_result["ee_trajectory"],
            metadata,
            str(package_dir),
        )
    except Exception as exc:
        _update_job(
            job_id,
            JobStatus.FAILED,
            0.88,
            PipelineStage.PACKAGE,
            str(exc),
            failure_reason=str(exc),
        )
        return

    # Stage: FINALIZE
    try:
        _update_job(
            job_id,
            JobStatus.RUNNING,
            0.92,
            PipelineStage.FINALIZE,
            "Rendering simulation video...",
        )
        sim_video_path = out / "simulation.mp4"
        await loop.run_in_executor(
            None,
            render_simulation_video,
            retarget_result["joint_trajectory"],
            sim_video_path,
        )

        _update_job(
            job_id,
            JobStatus.RUNNING,
            0.96,
            PipelineStage.FINALIZE,
            "Running static checks and AI review...",
        )

        static_checks = run_static_checks(package_dir)
        ai_review_md = generate_ai_review(eval_result, retarget_result["joint_trajectory"])

        ai_review_path = out / "ai_review.md"
        ai_review_path.write_text(ai_review_md, encoding="utf-8")

        # Downsample joint trajectory for visualization (max 50 points)
        traj = retarget_result["joint_trajectory"]
        step = max(1, len(traj) // 50)
        downsampled = traj[::step].tolist()

        result_payload = {
            "evaluation": eval_result,
            "package": pkg_result,
            "output_dir": str(package_dir),
            "simulation_video": str(sim_video_path),
            "static_checks": static_checks,
            "ai_review": ai_review_md,
            "ai_review_path": str(ai_review_path),
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


@router.get("/jobs/{job_id}/download")
def download_dataset(
    job_id: str, identity: SessionIdentity = Depends(require_authenticated_identity)
):
    """Stream a zip archive of the pipeline output directory.

    Only completed and accessible jobs may be downloaded.
    """
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

    output_dir = Path(snapshot.output_dir)
    if not output_dir.exists():
        raise HTTPException(status_code=404, detail="Output directory not found")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in output_dir.rglob("*"):
            if file_path.is_file():
                arcname = file_path.relative_to(output_dir)
                zf.write(file_path, arcname)
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{job_id}.zip"'},
    )


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
