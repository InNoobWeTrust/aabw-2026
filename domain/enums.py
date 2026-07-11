"""Canonical enumerations for the RoboData domain.

These enums are the single source of truth for roles, job lifecycle states,
pipeline processing phases, and quality classifications. All other packages
(backend, pipeline, frontend) must import from here — never define their own
copies or use string literals for these values.
"""

import enum


class UserRole(str, enum.Enum):
    """Authorization role carried in a JWT and enforced by FastAPI dependencies.

    JUDGE:  Anonymous session-scoped user who submits and tracks individual jobs.
            A judge can only see their own jobs (scoped by judge_session_id).

    ADMIN:  Global-scope user with visibility into all jobs and the ability to
            manage any job regardless of owner.
    """

    JUDGE = "judge"
    ADMIN = "admin"


class JobStatus(str, enum.Enum):
    """Coarse-grained lifecycle state of a Job.

    A Job has exactly one status at any moment. Status describes *where* the
    Job is in its lifecycle, while PipelineStage describes *what is running*
    during the RUNNING state. The two are orthogonal concepts.

    Canonical transitions:
        QUEUED → RUNNING → COMPLETED
        QUEUED → RUNNING → FAILED
        QUEUED → CANCELLED
        RUNNING → CANCELLED (best-effort signal checked between stages)
    """

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    def is_terminal(self) -> bool:
        """Return True if this status is a terminal (non-progressing) state."""
        return self in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED)

    def is_active(self) -> bool:
        """Return True if the job is still occupying a queue/worker slot."""
        return self in (JobStatus.QUEUED, JobStatus.RUNNING)


class PipelineStage(str, enum.Enum):
    """Ordered processing phase within the video-to-dataset pipeline.

    Stages run in fixed order: INGEST → PREPROCESS → POSE → RETARGET →
    EVALUATE → PACKAGE → FINALIZE. Each stage produces a well-defined output
    artifact consumed by the next stage.

    A Job's stage is independent of its overall JobStatus. A failed Job reports
    which stage it failed in; a completed Job records stage-level timing.
    """

    INGEST = "ingest"
    PREPROCESS = "preprocess"
    POSE = "pose"
    RETARGET = "retarget"
    EVALUATE = "evaluate"
    PACKAGE = "package"
    FINALIZE = "finalize"

    def progress_weight(self) -> float:
        """Weight this stage contributes to overall pipeline progress (0..1).

        Each stage returns an equal slice of the total progress bar.
        Returns the fraction one stage represents of the full pipeline.
        """
        return 1.0 / len(PipelineStage)

    def next_stage(self) -> "PipelineStage | None":
        """Return the next stage in the pipeline, or None if this is the last stage."""
        members = list(PipelineStage)
        try:
            idx = members.index(self)
        except ValueError:
            return None
        if idx + 1 < len(members):
            return members[idx + 1]
        return None


class QualityGrade(str, enum.Enum):
    """Traffic-light classification of JointTrajectory quality.

    GREEN:   Passes all quality thresholds. Dataset is production-ready.
    YELLOW:  Marginal quality — passes minimum thresholds but some metrics are
             borderline. Dataset is usable but should be reviewed.
    RED:     Fails one or more critical checks. Dataset is not recommended for
             training without manual cleanup or re-recording.
    """

    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


class ReviewStage(str, enum.Enum):
    """Bounded review stage attached to a completed pipeline job.

    POSE reviews the extracted human skeleton outputs and decides whether the
    pose-stage dataset is useful. RETARGET reviews the mapped robot-joint
    artifacts and decides whether the robot dataset is usable.
    """

    POSE = "pose"
    RETARGET = "retarget"


class ReviewStatus(str, enum.Enum):
    """Lifecycle state for an asynchronous review sub-job."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

    def is_terminal(self) -> bool:
        """Return True if the review no longer produces new events."""
        return self in (ReviewStatus.COMPLETED, ReviewStatus.FAILED)


class CalibrationStatus(str, enum.Enum):
    """Lifecycle state for an asynchronous mapping calibration sub-job."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

    def is_terminal(self) -> bool:
        """Return True if the calibration no longer produces new events."""
        return self in (CalibrationStatus.COMPLETED, CalibrationStatus.FAILED)


class ReviewVerdict(str, enum.Enum):
    """Stage-level usability verdict emitted by review agents."""

    APPROVED = "approved"
    USABLE_SKELETON_ONLY = "usable_skeleton_only"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"


class CalibrationDecision(str, enum.Enum):
    """Decision label emitted by the mapping calibrator."""

    BASELINE_OK = "baseline_ok"
    RERUN_WITH_PROFILE = "rerun_with_profile"
    SKELETON_ONLY = "skeleton_only"
    REJECT = "reject"


class CalibrationVerdict(str, enum.Enum):
    """High-level usability verdict emitted by the mapping calibrator."""

    BASELINE_ACCEPTABLE = "baseline_acceptable"
    ROBOT_MAPPING_SALVAGEABLE = "robot_mapping_salvageable"
    SKELETON_ONLY = "skeleton_only"
    REJECTED = "rejected"


class AssistantSessionStatus(str, enum.Enum):
    """Lifecycle state for an interactive reviewer-assistant session."""

    IDLE = "idle"
    RUNNING = "running"
    FAILED = "failed"


class AssistantMessageRole(str, enum.Enum):
    """Role labels persisted in a reviewer-assistant conversation transcript."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
