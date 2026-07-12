"""RoboData shared domain package.

The domain package is the canonical source of truth for:
    - Enums:       UserRole, JobStatus, PipelineStage, QualityGrade
    - Auth models: SessionIdentity
    - Job models:  JobOwner, JobProgress, JobSnapshot, JobEvent
    - Interfaces:  AbstractJobStore, AbstractQueue (future slices)
    - Exceptions:  JobNotFoundError, SessionLimitError, etc. (future slices)

Import rules:
    - backend/  imports from domain/ (enums, models)
    - pipeline/ imports from domain/ (enums, models)
    - domain/   imports from neither backend/ nor pipeline/
    - frontend/ is static — it does not import Python packages

Never define your own copy of these enums or models in backend/ or pipeline/.
String literals for statuses, stages, or roles are prohibited outside their
enum definitions and immediate tests.
"""

from domain.auth import SessionIdentity
from domain.calibration import CalibrationEvent, CalibrationSnapshot
from domain.enums import (
    CalibrationDecision,
    CalibrationStatus,
    CalibrationVerdict,
    CheckpointAuthor,
    JobStatus,
    MappingSessionStatus,
    OrchestrationDecision,
    OrchestrationStatus,
    PipelineStage,
    QualityGrade,
    UserRole,
)
from domain.jobs import JobEvent, JobOwner, JobProgress, JobSnapshot
from domain.mapping import AxisMapping, MappingProfile
from domain.mapping_session import MappingCheckpoint, MappingSession, MappingSessionEvent
from domain.orchestration import (
    CaptureGuidancePayload,
    OrchestrationDonePayload,
    OrchestrationEvent,
    OrchestrationProgressPayload,
    OrchestrationResultPayload,
    OrchestrationSnapshot,
    OrchestrationStatusPayload,
)

__all__ = [
    "AxisMapping",
    "CalibrationDecision",
    "CalibrationEvent",
    "CalibrationSnapshot",
    "CalibrationStatus",
    "CalibrationVerdict",
    "CheckpointAuthor",
    "JobEvent",
    "JobOwner",
    "JobProgress",
    "JobSnapshot",
    "CaptureGuidancePayload",
    "JobStatus",
    "MappingCheckpoint",
    "MappingProfile",
    "MappingSession",
    "MappingSessionEvent",
    "MappingSessionStatus",
    "OrchestrationDecision",
    "OrchestrationDonePayload",
    "OrchestrationEvent",
    "OrchestrationProgressPayload",
    "OrchestrationResultPayload",
    "OrchestrationSnapshot",
    "OrchestrationStatus",
    "OrchestrationStatusPayload",
    "PipelineStage",
    "QualityGrade",
    "SessionIdentity",
    "UserRole",
]
