# Domain Package — Responsibility & Public Contracts

The `domain/` package is the authoritative source of truth for all shared
types, models, and interfaces in the RoboData platform.

## What Belongs Here

| Module | Contents | Public Contract |
|--------|----------|-----------------|
| `domain/enums.py` | `UserRole`, `JobStatus`, `PipelineStage`, `QualityGrade` | String enums with helper methods. Used everywhere. |
| `domain/auth.py` | `SessionIdentity` | Pydantic model extracted from JWT claims. |
| `domain/jobs.py` | `JobOwner`, `JobProgress`, `JobSnapshot`, `JobEvent` | Canonical job state models. |
| `domain/exceptions.py` | `JobNotFoundError`, `SessionLimitError`, … | Domain exception hierarchy (future slices). |
| `domain/job_store.py` | `AbstractJobStore` | ABC for persistence layer (future slices). |
| `domain/queue.py` | `AbstractQueue` | ABC for scheduling layer (future slices). |

## What Does NOT Belong Here

- HTTP request/response schemas → `backend/models.py`
- Pipeline stage implementations → `pipeline/`
- FastAPI dependencies, route handlers → `backend/`
- Frontend rendering logic → `frontend/`

## Naming Canon

Every term used in this package must match the canonical names defined in
`GLOSSARY.md`. Specifically:

| Canonical | Prohibited |
|-----------|-----------|
| `JobStatus.QUEUED` | `PENDING`, `pending` |
| `PipelineStage.INGEST` | `ingesting` |
| `PipelineStage.PREPROCESS` | `preprocessing` |
| `PipelineStage.POSE` | `pose_estimation` |
| `PipelineStage.RETARGET` | `retargeting` |
| `PipelineStage.EVALUATE` | `evaluating` |
| `PipelineStage.PACKAGE` | `packaging` |
| `PipelineStage.FINALIZE` | `finalizing` |

## Import Rules

```
backend/  ──imports──►  domain/  ◄──imports──  pipeline/
   │                                                  │
   └──── never import each other ─────────────────────┘
```

- `backend/` and `pipeline/` may both import from `domain/`.
- Neither `backend/` nor `pipeline/` may import from each other.
- `domain/` must not import from `backend/` or `pipeline/`.
- `domain/` may import from the standard library and Pydantic only.

## Versioning

The `token_version` field on `SessionIdentity` supports future JWT key
rotation. When the JWT signing key changes, increment `token_version` so
that existing tokens can be detected as stale. Current version: **1**.
