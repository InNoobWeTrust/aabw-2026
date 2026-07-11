# Backend

FastAPI application layer for RoboData. Serves HTTP endpoints, handles authentication, and manages the job store.

## Responsibilities

- **`routes.py`** — Thin HTTP handlers. Delegates all job state mutations to the job store. Never mutates persisted job state directly.
- **`auth.py`** — Password verification, JWT creation/validation, FastAPI auth dependencies.
- **`config.py`** — Pydantic Settings from environment variables.
- **`job_store.py`** — `FileSystemJobStore`: filesystem-persisted job store under `data/jobs/<job_id>/`. This is the **only** module permitted to read or write job directories, `job.json`, and `events.jsonl`. All other modules must go through it for job state access.
- **`models.py`** — HTTP boundary models (request/response schemas). Delegates canonical types to `domain/`.
- **`server.py`** — App factory, CORS, static file serving, startup directory initialization.

## Rules

- Routes never import individual pipeline stages. Pipeline execution is dispatched through the orchestrator or (during transition) through `_run_pipeline` which is considered legacy and will be extracted.
- Routes never mutate job directories directly. All mutations go through `FileSystemJobStore`.
- Judge isolation is enforced by the store-level filter (`list_jobs_for_session`) and access checks in routes. The API layer adds 404 (not 403) for inaccessible jobs to avoid existence leaks.
- Canonical enums (`JobStatus`, `PipelineStage`, `UserRole`) are imported from `domain/`. String literals for statuses, stages, or roles are prohibited.
