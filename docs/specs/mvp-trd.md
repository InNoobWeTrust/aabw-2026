# TRD: RoboData MVP — Core Platform

> **Status**: draft
> **Owner**: RoboData team
> **Created**: 2026-07-11

## Parent PRD

This TRD is the authoritative technical specification for the RoboData MVP. No separate PRD exists; the product intent is captured inline in the Objective and Functional Requirements sections.

## Objective

Build a self-service web platform where lab researchers and robot operators upload 30-second phone-captured videos of human manipulation tasks, receive automated pose extraction → IK retargeting → quality evaluation → LeRobot dataset packaging, and download the resulting dataset — all through a browser with no local tool installation. The system must support anonymous judge sessions (judges see only their own jobs) and administrators with global visibility.

## Scope

### In Scope

1. Dual-role auth: judge (shared password → anonymous `judge_session_id`) and admin (separate password → `role=admin`)
2. Video upload with extension whitelist (`.mp4`, `.mov`, `.avi`, `.webm`) and size validation (configurable, default 100 MB)
3. Seven-stage pipeline: `ingest` → `preprocess` → `pose` → `retarget` → `evaluate` → `package` → `finalize`
4. Filesystem durable persistence at `data/jobs/<job_id>/` containing `job.json` (canonical state), `events.jsonl` (append-only log), `upload/`, `work/`, `output/`, `logs/`
5. One global background worker process with persisted FIFO queue at `data/queue/`
6. Per-session concurrency limit: max one active (`queued` or `running`) job per `judge_session_id`
7. Restart recovery: any `running` job transitions to `failed` with reason `worker_restarted` on worker startup; `queued` jobs remain `queued`
8. Judge-scoped APIs: judge can only list/get/cancel/delete their own jobs
9. Admin APIs: admin can list/get/cancel/delete any job
10. Static frontend: login form, upload with drag-and-drop, job card dashboard with progress bars, download button
11. Single Docker container deployment on Render with persistent disk mounted at `/app/data`
12. Structured logging: per-job `logs/` directory, event log in `events.jsonl`

### Out of Scope (Explicit Non-Goals)

1. Multi-worker horizontal scaling, distributed queues, Redis, or message brokers
2. Database integration (PostgreSQL, SQLite) — filesystem only
3. User registration, OAuth, SSO, or multi-tenant organizations
4. Job retry-on-failure (failed jobs stay failed; users re-upload)
5. Video streaming or partial uploads (chunked upload)
6. Real-time WebSocket progress (polling only)
7. Email notifications, webhooks, or external integrations
8. CUDA/GPU acceleration (CPU-only pipeline for MVP)
9. Multiple robot morphologies beyond Franka Panda (configurable, but only one per deploy)
10. Rate limiting, DDoS protection, or advanced network security beyond input validation
11. Multi-region or CDN deployment

## System Context

```ascii
┌──────────┐       ┌─────────────────────┐       ┌──────────────┐
│  Judge   │──────►│                     │       │   Admin      │
│ (browser)│       │  RoboData Platform  │◄──────│  (browser)   │
└──────────┘       │  (FastAPI + Worker) │       └──────────────┘
                   │                     │
                   │  ┌───────────────┐  │
                   │  │ Pipeline      │  │
                   │  │ (MediaPipe +  │  │
                   │  │  pinocchio)   │  │
                   │  └───────────────┘  │
                   │                     │
                   │  ┌───────────────┐  │
                   │  │ Filesystem    │  │
                   │  │ (data/        │  │
                   │  │  jobs/ queue/)│  │
                   │  └───────────────┘  │
                   └─────────────────────┘

External Dependencies (all local, no network calls at runtime):
  - MediaPipe Pose (bundled model weights)
  - pinocchio (bundled URDF for Franka Panda)
  - OpenCV / ffmpeg (system packages in Docker image)
```

## Architecture Decisions

### ADR-1: Modular Monolith

- **Context**: The system has clear domain boundaries (API, pipeline, shared domain, frontend) but does not need distributed deployment for MVP.
- **Decision**: Single FastAPI process that serves the API, mounts the static frontend, and runs the background worker in-process. The code is organized into four packages (`backend/`, `pipeline/`, `domain/`, `frontend/`) with strict import rules.
- **Rationale**: Zero network overhead between components, simple deployment (one `Procfile` line), easy local development. The package boundary is enforced by convention (no `backend/` → `pipeline/` imports), making future extraction to separate services mechanical rather than architectural.
- **Alternatives Considered**:
  - Separate worker process (systemd/supervisor) — adds deployment complexity for no MVP benefit; in-process async worker is sufficient.
  - Microservices with message broker — over-engineered for a single-worker MVP.

### ADR-2: Filesystem Persistence

- **Context**: Job state must survive process restarts. A database adds operational burden (schema migrations, connection pooling, backups).
- **Decision**: Every job lives in `data/jobs/<job_id>/` on disk. State is JSON (`job.json`), events are JSONL (`events.jsonl`). The `JobStore` abstraction wraps filesystem I/O. No database.
- **Rationale**: Simple to inspect (`cat job.json`), no external dependency, trivially portable across deployments. JSONL append-only log provides audit trail without write locks. Atomic writes to `job.json` use temp-file + rename.
- **Alternatives Considered**:
  - SQLite — same local-first property but adds schema management and ORM dependency. JSON files are more transparent for debugging.
  - Redis — adds infrastructure dependency; in-memory storage violates durability requirement.

### ADR-3: Dual-Role Auth with Anonymous Sessions

- **Context**: The system serves both anonymous judges (who submit and track individual jobs) and administrators (who have global visibility). Judges should be anonymous — no registration, no email.
- **Decision**: Two separate passwords. Judge password creates a JWT with `role=judge` and a random `judge_session_id` (UUID). Admin password creates a JWT with `role=admin`. Judge sessions can only see their own jobs; admin sessions see all. Constant-time password comparison via `hmac.compare_digest`.
- **Rationale**: Anonymous judges eliminate PII collection and registration friction. Session-scoped access prevents judges from seeing each other's submissions. Admin channel supports contest management, debugging, and global oversight.
- **Alternatives Considered**:
  - Single shared password with no role distinction — fails the admin visibility requirement.
  - Per-judge registration with email/password — adds PII liability and UX friction for contest/event use case.
  - API keys — less ergonomic for browser-based upload than JWT in Bearer header.

### ADR-4: Global Worker with FIFO Queue

- **Context**: Pipeline execution is CPU-bound (MediaPipe, pinocchio). Multiple concurrent jobs would contend for CPU and memory on a single-instance deployment.
- **Decision**: One global background worker. Persisted FIFO queue on disk at `data/queue/`. Worker dequeues one job at a time, runs it to completion or failure, then dequeues the next. Per-session limit: max one active job per `judge_session_id`. New submissions from a session with an active job are rejected (409 Conflict).
- **Rationale**: FIFO ensures fairness. Per-session limit prevents a single judge from monopolizing the queue while still allowing concurrent judges to queue work. Single worker prevents CPU thrashing on a single-instance deployment.
- **Alternatives Considered**:
  - Fire-and-forget `asyncio.create_task` (current implementation) — no queue, no durability, jobs lost on restart.
  - Thread pool with N concurrent workers — MediaPipe and OpenCV are not GIL-releasing for all operations; concurrent execution may not actually parallelize.

### ADR-5: Restart Policy — Fail Running, Keep Queued

- **Context**: On worker restart (process crash, deploy, infrastructure restart), the worker must reconcile state.
- **Decision**: On startup, scan all jobs. Any job with `status=running` transitions to `failed` with `reason: worker_restarted`. Jobs with `status=queued` remain `queued`. No automatic resume.
- **Rationale**: A `running` job was mid-pipeline — its work directory is in an unknown state. Failing it is safe; the judge can re-upload. `queued` jobs haven't started yet, so they're safe to keep. This is a fail-closed policy.
- **Alternatives Considered**:
  - Resume `running` jobs from the last completed stage — requires checkpointing every stage output, adding complexity for an MVP. Judges can re-upload cheaply.
  - Leave `running` as `running` — may block the queue indefinitely if the worker doesn't restart.

## Functional Requirements

### FR-1: Video Upload

- **FR-1.1**: Accept `POST /api/jobs/upload` with `multipart/form-data` containing a `video` file.
- **FR-1.2**: Validate file extension against whitelist: `.mp4`, `.mov`, `.avi`, `.webm`.
- **FR-1.3**: Validate file size against configurable `MAX_VIDEO_SIZE_MB` (default 100 MB).
- **FR-1.4**: Validate video duration against configurable `MAX_VIDEO_DURATION_SECONDS` (default 30s).
- **FR-1.5**: On validation failure, return 400 with a human-readable error message.
- **FR-1.6**: On success, persist the video to `data/jobs/<job_id>/upload/<original_filename>`, create a `job.json` with `status=queued`, enqueue the job ID, and return the `JobResponse`.

### FR-2: Pipeline Execution (7 Stages)

- **FR-2.1**: Stage order is fixed: `ingest` → `preprocess` → `pose` → `retarget` → `evaluate` → `package` → `finalize`.
- **FR-2.2**: Each stage transition writes a `JobEvent` to `events.jsonl`.
- **FR-2.3**: Progress (0.0–1.0) is updated in `job.json` after each stage.
- **FR-2.4**: On stage failure, the job transitions to `failed` with the stage name and error message recorded in `job.json` and `events.jsonl`. Subsequent stages are skipped.
- **FR-2.5**: Intermediate artifacts (frames, pose data, trajectories) are written to `data/jobs/<job_id>/work/`.
- **FR-2.6**: Final LeRobot dataset is written to `data/jobs/<job_id>/output/`.
- **FR-2.7**: Stage-specific logging is written to `data/jobs/<job_id>/logs/<stage>.log`.

### FR-3: Job Status Polling

- **FR-3.1**: `GET /api/jobs/{job_id}` returns the current `JobResponse` with status, progress, current stage, message, and timestamps.
- **FR-3.2**: Judge-scoped: only returns the job if `job.judge_session_id == caller.judge_session_id`.
- **FR-3.3**: Admin-scoped: returns any job.

### FR-4: Job Listing

- **FR-4.1**: `GET /api/jobs` returns all jobs visible to the caller, sorted newest-first.
- **FR-4.2**: Judge sees only their own jobs (`judge_session_id` match).
- **FR-4.3**: Admin sees all jobs.

### FR-5: Job Cancellation

- **FR-5.1**: `POST /api/jobs/{job_id}/cancel` transitions a `queued` job to `cancelled`.
- **FR-5.2**: Cancelling a `running` job is a best-effort signal; the worker checks for cancellation between stages.
- **FR-5.3**: Terminal jobs (`completed`, `failed`, `cancelled`) cannot be cancelled (return 409).

### FR-6: Job Deletion

- **FR-6.1**: `DELETE /api/jobs/{job_id}` removes the job directory from disk (`data/jobs/<job_id>/`).
- **FR-6.2**: Only terminal or cancelled jobs may be deleted.
- **FR-6.3**: Judge-scoped: only own jobs. Admin-scoped: any job.

### FR-7: Dataset Download

- **FR-7.1**: `GET /api/jobs/{job_id}/download` streams a zip of `data/jobs/<job_id>/output/`.
- **FR-7.2**: Only available for `completed` jobs.
- **FR-7.3**: Path traversal prevention: resolved paths must be children of the job's output directory.

### FR-8: Queue Management

- **FR-8.1**: The queue is FIFO.
- **FR-8.2**: No judge may have more than one job in `queued` or `running` state simultaneously.
- **FR-8.3**: A submit attempt while the judge has an active job returns 409 Conflict.

## Auth / Authorization Requirements

### AR-1: Password Verification

- **AR-1.1**: Two environment variables: `JUDGE_PASSWORD` and `ADMIN_PASSWORD`. Both are mandatory at startup.
- **AR-1.2**: `POST /api/auth/login` accepts `{"password": "<value>"}`.
- **AR-1.3**: Password comparison uses `hmac.compare_digest` exclusively. No other comparison operator.
- **AR-1.4**: If the password matches `JUDGE_PASSWORD`, the JWT payload includes `{"role": "judge", "judge_session_id": "<random UUID>"}`.
- **AR-1.5**: If the password matches `ADMIN_PASSWORD`, the JWT payload includes `{"role": "admin"}`.
- **AR-1.6**: If the password matches neither, return 401.

### AR-2: JWT Lifecycle

- **AR-2.1**: Tokens are signed with HS256 using `JWT_SECRET_KEY` (minimum 32 random bytes).
- **AR-2.2**: Token expiry is configurable via `JWT_EXPIRY_HOURS` (default 24).
- **AR-2.3**: Expired tokens return 401 with `"Token has expired"`.
- **AR-2.4**: Malformed tokens return 401 with `"Invalid token"`.

### AR-3: Role Enforcement

- **AR-3.1**: FastAPI dependency `get_current_judge` validates JWT, asserts `role == "judge"`.
- **AR-3.2**: FastAPI dependency `get_current_admin` validates JWT, asserts `role == "admin"`.
- **AR-3.3**: Judge endpoints use `get_current_judge`. Admin endpoints use `get_current_admin`.
- **AR-3.4**: Shared endpoints (e.g., upload) accept either role via `get_current_user`.
- **AR-3.5**: Role mismatch returns 403.

### AR-4: Session Isolation

- **AR-4.1**: `judge_session_id` is a random UUID generated at login time. It is not derivable from any user-provided data.
- **AR-4.2**: Judge-scoped job queries filter by `judge_session_id` at the `JobStore` level.
- **AR-4.3**: The API layer must never implement its own filtering that could bypass the store-level filter.
- **AR-4.4**: A judge can never read, cancel, or delete another judge's job.

## Persistence Requirements

### PR-1: Job Directory Structure

```
data/jobs/<job_id>/
├── job.json          # Canonical job state (id, status, stage, progress, session_id, metadata, timestamps)
├── events.jsonl      # Append-only ordered event log
├── upload/
│   └── <filename>    # Original uploaded video
├── work/
│   ├── frames/       # Extracted frame JPEGs
│   ├── pose/         # Pose landmarks
│   └── trajectory/   # Joint trajectory data
├── output/
│   └── dataset/      # LeRobot format (Parquet + meta.json + stats.json)
└── logs/
    ├── ingest.log
    ├── preprocess.log
    ├── pose.log
    ├── retarget.log
    ├── evaluate.log
    ├── package.log
    └── finalize.log
```

### PR-2: `job.json` Schema

| Field | Type | Description |
|---|---|---|
| `job_id` | `str` | UUID hex, assigned at upload |
| `status` | `JobStatus` | One of: `queued`, `running`, `completed`, `failed`, `cancelled` |
| `stage` | `PipelineStage or null` | Current stage if status is `running`; stage of failure if `failed` |
| `progress` | `float` | 0.0–1.0 |
| `judge_session_id` | `str` | UUID of the judge who submitted this job |
| `filename` | `str` | Original uploaded filename |
| `message` | `str` | Human-readable status message |
| `created_at` | `str` (ISO 8601) | Job creation timestamp |
| `started_at` | `str or null` | When worker picked up the job |
| `completed_at` | `str or null` | When job reached terminal state |
| `result` | `dict or null` | Summary of pipeline output (quality grade, output path, etc.) |
| `error` | `str or null` | Error message if status is `failed` |

### PR-3: `events.jsonl` Format

Each line is a JSON object:
```json
{"timestamp": "2026-07-11T08:00:00Z", "event": "job_created", "status": "queued"}
{"timestamp": "2026-07-11T08:00:01Z", "event": "stage_enter", "stage": "ingest"}
{"timestamp": "2026-07-11T08:00:05Z", "event": "stage_exit", "stage": "ingest", "result": "success"}
{"timestamp": "2026-07-11T08:05:00Z", "event": "stage_exit", "stage": "pose", "result": "failed", "error": "No person detected in video"}
```

### PR-4: Atomicity

- **PR-4.1**: `job.json` writes use temp-file + `os.replace()` for atomic replacement.
- **PR-4.2**: `events.jsonl` appends use line-buffered writes. Append is not atomic across lines, but each line is self-contained JSON.
- **PR-4.3**: Directory creation uses `os.makedirs(..., exist_ok=True)`.

### PR-5: Cleanup

- **PR-5.1**: Job deletion (`DELETE`) removes the entire `data/jobs/<job_id>/` tree.
- **PR-5.2**: No automatic TTL-based cleanup for MVP. Admin may delete jobs manually.

## Queue / Worker Requirements

### QW-1: Queue Structure

- **QW-1.1**: Persisted at `data/queue/`. Each entry is a file `<timestamp>_<job_id>.json` containing `{"job_id": "...", "enqueued_at": "..."}`.
- **QW-1.2**: FIFO ordering by enqueue timestamp.
- **QW-1.3**: The queue is reconstructed on startup by listing files sorted by name.

### QW-2: Worker Lifecycle

- **QW-2.1**: Worker starts as an `asyncio` background task in the FastAPI lifespan.
- **QW-2.2**: Worker runs a loop: dequeue → validate (no active job for this session) → run pipeline → dequeue next.
- **QW-2.3**: If the queue is empty, the worker waits (poll interval 1s).
- **QW-2.4**: Worker is the only entity that writes pipeline progress and stage transitions to the job store.

### QW-3: Session Concurrency

- **QW-3.1**: Before dequeuing a new job, the worker checks if the submitting `judge_session_id` already has a `queued` or `running` job.
- **QW-3.2**: If yes, skip this entry and leave it in the queue (it will be checked again on next dequeue cycle).
- **QW-3.3**: On API upload, check before enqueuing: if the judge already has an active job, return 409.

### QW-4: Worker Failure

- **QW-4.1**: If the worker crashes mid-pipeline, the process restarts (uvicorn auto-restart or Docker restart policy).
- **QW-4.2**: On restart, apply ADR-5 restart policy: `running` → `failed(worker_restarted)`.
- **QW-4.3**: The queue persists on disk, surviving worker restarts.

## Recovery Requirements

### RR-1: Startup Reconciliation

- **RR-1.1**: On process startup (FastAPI lifespan `startup` event), run reconciliation:
  1. List all `data/jobs/*/job.json` files.
  2. For each job with `status == "running"`: set `status = "failed"`, `error = "worker_restarted"`, append event to `events.jsonl`.
  3. For each job with `status == "queued"`: no change.
- **RR-1.2**: After reconciliation, rebuild the queue from `queued` jobs (ordered by `created_at`).
- **RR-1.3**: Start the worker.

### RR-2: Crash During Write

- **RR-2.1**: `job.json` atomically replaced — a crash during write leaves the previous version intact.
- **RR-2.2**: `events.jsonl` appends are line-buffered — a crash may leave a partial last line. Readers tolerate truncated final lines.

### RR-3: Data Directory Missing

- **RR-3.1**: On startup, create `data/jobs/` and `data/queue/` if they don't exist.
- **RR-3.2**: If a job's directory exists but `job.json` is missing or corrupt, log an error and skip that entry.

## API Contract Requirements

### ACR-1: Endpoint Inventory

| Method | Path | Auth | Role | Description |
|---|---|---|---|---|
| `POST` | `/api/auth/login` | None | — | Exchange password for JWT |
| `GET` | `/api/auth/verify` | JWT | any | Verify token validity |
| `POST` | `/api/jobs/upload` | JWT | any | Upload video, enqueue job |
| `GET` | `/api/jobs` | JWT | judge/admin | List visible jobs |
| `GET` | `/api/jobs/{job_id}` | JWT | judge/admin | Get job status |
| `POST` | `/api/jobs/{job_id}/cancel` | JWT | judge/admin | Cancel queued/running job |
| `DELETE` | `/api/jobs/{job_id}` | JWT | judge/admin | Delete job and files |
| `GET` | `/api/jobs/{job_id}/download` | JWT | judge/admin | Download dataset zip |

### ACR-2: Response Models

All responses use the canonical `JobStatus` and `PipelineStage` enums from `domain/models.py`. String literals for statuses (`"pending"`, `"preprocessing"`) are prohibited.

### ACR-3: Error Responses

| Status | Meaning | Body |
|---|---|---|
| 400 | Validation error | `{"detail": "<human-readable message>"}` |
| 401 | Missing or invalid token | `{"detail": "Not authenticated"}` / `"Token has expired"` / `"Invalid token"` |
| 403 | Insufficient role | `{"detail": "Admin access required"}` |
| 404 | Job not found | `{"detail": "Job <id> not found"}` |
| 409 | Session limit (active job exists) | `{"detail": "You already have an active job"}` |
| 409 | Invalid state transition | `{"detail": "Cannot cancel a completed job"}` |

## Frontend Requirements

### FE-1: Login

- **FE-1.1**: Single password field + "Authenticate" button.
- **FE-1.2**: On success, store JWT in `localStorage`, show dashboard.
- **FE-1.3**: On failure, show error message below the form.
- **FE-1.4**: On page load, verify existing token. If valid, skip login.

### FE-2: Upload

- **FE-2.1**: Drag-and-drop zone with click-to-browse fallback.
- **FE-2.2**: File type validation before upload (`.mp4`, `.mov`, `.avi`, `.webm`).
- **FE-2.3**: Show selected filename, upload button, clear button.
- **FE-2.4**: On upload, show spinner, then toast on success/failure.

### FE-3: Dashboard

- **FE-3.1**: Job cards showing: filename, status badge, progress bar, current stage, elapsed time.
- **FE-3.2**: Active jobs auto-poll every 2s.
- **FE-3.3**: Completed jobs show download button.
- **FE-3.4**: Delete button on every card with fade-out animation.
- **FE-3.5**: Empty state: "No jobs yet. Upload a video to get started."

### FE-4: Download

- **FE-4.1**: Download button triggers browser download of the dataset zip.
- **FE-4.2**: Filename derived from original video name.

### FE-5: Visual Design

- **FE-5.1**: Dark theme (background `#0f172a`, cards `#1e293b`).
- **FE-5.2**: Status colors: queued (amber), running (blue), completed (green), failed (red), cancelled (gray).
- **FE-5.3**: Progress bar with color transitions.
- **FE-5.4**: Toast notifications for success/error feedback.

## Deployment Requirements

### DR-1: Docker Image

- **DR-1.1**: Base image: `python:3.11-slim`.
- **DR-1.2**: System packages: `ffmpeg`, `libgl1-mesa-glx`, `libglib2.0-0`.
- **DR-1.3**: Python dependencies installed from `pyproject.toml` (not inline in Dockerfile).
- **DR-1.4**: Expose port 8000.
- **DR-1.5**: CMD: `uvicorn backend.server:app --host 0.0.0.0 --port 8000`.

### DR-2: Render Deployment

- **DR-2.1**: `Procfile`: `web: uvicorn backend.server:app --host 0.0.0.0 --port $PORT`.
- **DR-2.2**: Persistent disk mounted at `/app/data`.
- **DR-2.3**: Environment variables injected via Render dashboard (not committed).
- **DR-2.4**: Health check: `GET /api/auth/verify` (returns 401 without token, confirming the process is alive).

### DR-3: Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `JUDGE_PASSWORD` | Yes | — | Shared password for judge login |
| `ADMIN_PASSWORD` | Yes | — | Password for admin login |
| `JWT_SECRET_KEY` | Yes | — | HMAC secret (min 32 bytes) |
| `JWT_EXPIRY_HOURS` | No | `24` | Token lifetime |
| `MAX_VIDEO_DURATION_SECONDS` | No | `30` | Max video duration |
| `MAX_VIDEO_SIZE_MB` | No | `100` | Max upload size |
| `DATA_DIR` | No | `./data` | Root for `jobs/` and `queue/` |
| `TARGET_ROBOT` | No | `franka_panda` | Robot URDF target |
| `HOST` | No | `0.0.0.0` | Server bind address |
| `PORT` | No | `8000` | Server port |

### DR-4: Alternate Deployments (Noted, Not Required for MVP)

- **DR-4.1**: AWS ECS / GCP Cloud Run with persistent EFS / Filestore volume: replace Render persistent disk with cloud NFS mount.
- **DR-4.2**: Bare metal: `pip install -e . && uvicorn backend.server:app` with `DATA_DIR` pointing to a local path.
- **DR-4.3**: Docker Compose: add `volumes:` mount for `./data:/app/data`.

## Observability / Logging Requirements

### OL-1: Structured Logging

- **OL-1.1**: Use Python `logging` with structured format: `%(asctime)s [%(levelname)s] %(name)s: %(message)s`.
- **OL-1.2**: Log level configurable via `LOG_LEVEL` env var (default `INFO`).

### OL-2: Per-Job Logs

- **OL-2.1**: Each pipeline stage writes to `data/jobs/<job_id>/logs/<stage>.log`.
- **OL-2.2**: Log files are plain text (not JSON) for human readability during debugging.

### OL-3: Event Log

- **OL-3.1**: `events.jsonl` records machine-readable state transitions: job creation, stage entry/exit, failure, completion, cancellation, deletion.
- **OL-3.2**: Each event has `timestamp` (ISO 8601 UTC), `event` (event type string), and contextual fields.

### OL-4: Error Reporting

- **OL-4.1**: Pipeline exceptions are logged with full traceback to the stage log file and summarized in `job.json` `error` field.
- **OL-4.2**: Unhandled worker exceptions are logged to stderr (captured by container runtime).
- **OL-4.3**: No external error tracking service (Sentry, etc.) for MVP.

### OL-5: Health Check

- **OL-5.1**: `GET /api/health` returns `{"status": "ok"}` (unauthenticated).
- **OL-5.2**: Used by Render for liveness check.

## Acceptance Criteria

### AC-1: Auth

- **AC-1.1**: Judge logs in with `JUDGE_PASSWORD` → receives JWT with `role=judge` and a `judge_session_id`.
- **AC-1.2**: Admin logs in with `ADMIN_PASSWORD` → receives JWT with `role=admin`.
- **AC-1.3**: Wrong password returns 401.
- **AC-1.4**: Expired JWT returns 401 with clear message.
- **AC-1.5**: Judge cannot access admin-only endpoints (403).

### AC-2: Upload + Pipeline

- **AC-2.1**: Upload a valid `.mp4` → job is created, returns 200 with `JobResponse`.
- **AC-2.2**: Upload invalid extension → 400.
- **AC-2.3**: Upload oversized file → 400.
- **AC-2.4**: Pipeline runs to completion → job status transitions through `queued` → `running` (with stage updates) → `completed`.
- **AC-2.5**: Pipeline encounters error → job transitions to `failed` with stage and error recorded.
- **AC-2.6**: Completed jobs produce a downloadable zip.

### AC-3: Judge Isolation

- **AC-3.1**: Judge A uploads a job. Judge B logs in (different `judge_session_id`). Judge B's job list is empty.
- **AC-3.2**: Judge A cannot access Judge B's job by guessing the job ID (404).
- **AC-3.3**: Admin sees all jobs from all judges.

### AC-4: Session Limit

- **AC-4.1**: Judge with an active job attempts upload → 409.
- **AC-4.2**: Judge with a completed job can upload a new one.

### AC-5: Restart Recovery

- **AC-5.1**: Kill the process mid-pipeline. Restart. The `running` job is now `failed` with `error: worker_restarted`.
- **AC-5.2**: A `queued` job remains `queued` after restart and is picked up by the worker.

### AC-6: Cancel + Delete

- **AC-6.1**: Cancel a `queued` job → status becomes `cancelled`.
- **AC-6.2**: Delete a completed job → directory removed from disk, job not in list result.
- **AC-6.3**: Cannot cancel a `completed` job (409).

### AC-7: Frontend

- **AC-7.1**: Login form appears. Successful login shows dashboard.
- **AC-7.2**: Drag-and-drop upload works. Progress bar updates during pipeline execution.
- **AC-7.3**: Download button appears when job completes. Clicking it downloads a zip.

### AC-8: Deployment

- **AC-8.1**: `docker build -t robodata .` succeeds.
- **AC-8.2**: `docker run` with env vars starts the server.
- **AC-8.3**: `GET /api/health` returns 200.

## Risks and Mitigations

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| MediaPipe memory exhaustion on large videos | Pipeline crash, job failed | Medium | Enforce 30s max duration; run pipeline in thread executor with timeout |
| Filesystem corruption on disk full | Data loss | Low | Configurable `DATA_DIR`; Render persistent disk has monitoring; job.json atomic writes minimize corruption window |
| JWT secret leakage | All sessions compromised | Low | `.env` gitignored; JWT expiry limits exposure window; admin can rotate secret |
| Queue starvation (one judge submits many jobs) | Other judges wait indefinitely | Medium | Per-session concurrency limit prevents any judge from holding more than one queue slot |
| Frontend polling storms under load | Server CPU waste | Low | 2s poll interval is conservative; for single-instance MVP, concurrent pollers are bounded by number of browser tabs |
| pinocchio URDF missing or malformed | All retargeting fails | Low | Bundled URDF for Franka Panda in the Docker image; validated at startup |

## Traceability — Repo Drift That Must Be Corrected

The following items in the current codebase contradict this TRD and must be corrected during implementation:

| # | Drift | Location | Required Change |
|---|---|---|---|
| D1 | `JobStatus.PENDING = "pending"` — should be `QUEUED = "queued"` | `backend/models.py:25` | Rename enum member; update all references in routes, frontend, and tests |
| D2 | Stage names leaked into `JobStatus` enum (`PREPROCESSING`, `POSE_ESTIMATION`, etc.) | `backend/models.py:26-31` | Decouple into separate `JobStatus` (lifecycle) and `PipelineStage` (processing phase) enums |
| D3 | `current_stage` uses legacy stage name strings `"preprocessing"`, `"pose_estimation"`, etc. | `backend/routes.py:60-129` | Use `PipelineStage` enum values: `ingest`, `preprocess`, `pose`, `retarget`, `evaluate`, `package`, `finalize` |
| D4 | Bare `"sub": "admin"` JWT claim — no `judge_session_id`, no `role` field | `backend/routes.py:157` | Add `judge_session_id` (UUID) for judge logins; change to `role` claim |
| D5 | In-memory `_jobs` dict — no durability | `backend/routes.py:34` | Replace with `JobStore` interface backed by filesystem (`data/jobs/<id>/job.json`) |
| D6 | Fire-and-forget `asyncio.create_task` — no queue, no worker, no restart safety | `backend/routes.py:211-218` | Replace with `Queue` enqueue + background `Worker` dequeue loop |
| D7 | `get_current_user` returns raw payload with no role assertion | `backend/auth.py:28-39` | Add `get_current_judge` and `get_current_admin` dependencies that assert `role` claim |
| D8 | No judge isolation — all users see all jobs | `backend/routes.py:223-227` | Store `judge_session_id` on job; filter `list_jobs` and `get_job` by session for judge role |
| D9 | `UPLOAD_DIR` and `OUTPUT_DIR` are top-level `uploads/` and `outputs/` — not under `data/jobs/<id>/` | `backend/config.py:15-16` | Migrate to `DATA_DIR/jobs/<id>/upload/` and `DATA_DIR/jobs/<id>/output/` |
| D10 | No `domain/` package exists | — | Create `domain/` with `models.py` (enums, Job, JobEvent), `job_store.py` (ABC), `job_store_fs.py`, `queue.py`, `exceptions.py` |
| D11 | Pipeline logic lives in `backend/routes.py` `_run_pipeline` | `backend/routes.py:52-139` | Pipeline stage dispatch must live in `pipeline/`; `backend/routes.py` only calls the orchestrator via the worker |
| D12 | Frontend uses `"pending"` in `ACTIVE_STATUSES` and `getStatusLabel` | `frontend/app.js:5,344` | Change to `"queued"` and update all status/label maps |
| D13 | Test files are stub-only (`# TODO`) | `tests/test_auth.py`, `tests/test_pipeline.py` | Implement real tests for auth, routes, job store, queue, worker, isolation, and each pipeline stage |
| D14 | No `Makefile` target for `make quality` includes type checking | `Makefile:14` | Add `mypy` or `pyright` step (tracked but not blocking for MVP — add when type stubs for mediapipe/opencv are available) |
| D15 | `docker build` duplicates `COPY . .` twice | `Dockerfile:22-23` | Remove duplicate line |
| D16 | No unauthenticated health check endpoint | `backend/routes.py` | Add `GET /api/health` returning `{"status": "ok"}` |

## Child BDD Specs

Planned but not yet written:
- `docs/specs/behavior-auth.md` — Login, token validation, role enforcement, session isolation
- `docs/specs/behavior-upload.md` — Video validation, upload flow, session limit enforcement
- `docs/specs/behavior-pipeline.md` — Stage sequencing, progress reporting, failure handling
- `docs/specs/behavior-queue.md` — FIFO ordering, per-session concurrency, restart recovery
- `docs/specs/behavior-download.md` — Dataset packaging, zip streaming, path traversal prevention

## Notes

- The `.env.example` currently has a single `ACCESS_PASSWORD`. It must be split into `JUDGE_PASSWORD` and `ADMIN_PASSWORD`.
- The `OUTPUT_DIR` and `UPLOAD_DIR` settings will be deprecated in favor of `DATA_DIR` with the `data/jobs/<id>/` structure.
- MediaPipe model weights are downloaded at first use and cached; the Docker image should pre-download them in the build step to avoid cold-start latency.
- All API endpoints currently return application/json. The download endpoint is the only one returning `application/zip`.
