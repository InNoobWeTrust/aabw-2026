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
from domain.enums import JobStatus, PipelineStage, QualityGrade, UserRole
from domain.jobs import JobEvent, JobOwner, JobProgress, JobSnapshot
from domain.mapping import AxisMapping, MappingProfile

__all__ = [
    "AxisMapping",
    "JobEvent",
    "JobOwner",
    "JobProgress",
    "JobSnapshot",
    "JobStatus",
    "MappingProfile",
    "PipelineStage",
    "QualityGrade",
    "SessionIdentity",
    "UserRole",
]
