# RoboData — System Architecture

> **Status**: draft
> **Owner**: RoboData team
> **Created**: 2026-07-11

## Architecture Inventory

| Component | Technology | Role | Current State |
|---|---|---|---|
| Backend API | Python 3.10+ / FastAPI | HTTP server, auth, routes, static file mount | Exists: `backend/server.py`, `backend/routes.py`, `backend/auth.py` |
| Pipeline | Python, MediaPipe, OpenCV, pinocchio, pandas, pyarrow | Video → pose → retarget → evaluate → package | Exists: `pipeline/orchestrator.py` + 5 stage modules |
| Domain (shared) | Python / Pydantic | Enums, models, job-store interface, queue abstraction | **Does not exist yet** — currently models live in `backend/models.py`, no `domain/` package |
| Frontend | Vanilla HTML/JS/CSS, static | Upload UI, job polling, dashboard, download | Exists: `frontend/index.html`, `app.js`, `style.css` |
| Job Store | Filesystem JSON + JSONL | Durable per-job state, event log | **Does not exist yet** — currently an in-memory `_jobs` dict in `backend/routes.py:34` |
| Queue | Filesystem FIFO | Dispatch order, per-session concurrency limit | **Does not exist yet** — currently `asyncio.create_task` fire-and-forget in `backend/routes.py:211` |
| Worker | Python async process | Dequeue, run pipeline, write events | **Does not exist yet** — currently inline `_run_pipeline` in `backend/routes.py:52` |
| Auth | JWT (HS256) + hmac.compare_digest | Password verification, token issuance/validation | Exists but single-role: `backend/auth.py` — needs dual-role (judge + admin) |
| Configuration | Pydantic Settings (`backend/config.py`) | Env var → typed settings, `.env` loading | Exists |
| Deployment | Docker (single container), Render Procfile | Production serving on `$PORT` | Exists: `Dockerfile`, `Procfile` |
| Observability | `logging` (stdlib) | Structured pipeline logs | Minimal — no structured events, no per-job log files |

## Responsibility Split

### Backend API (`backend/`)
**Owns**: HTTP ingress, CORS, static file serving, password verification webhook, JWT issuance/validation FastAPI dependencies, route definitions, request validation, response serialization.

**Does NOT own**: Pipeline stage logic (delegates to `pipeline/orchestrator`), job state persistence (delegates to `domain/job_store`), queue management (delegates to `domain/queue`), worker lifecycle (delegates to `pipeline/worker`).

### Pipeline (`pipeline/`)
**Owns**: All video processing stages (ingest, preprocess, pose, retarget, evaluate, package, finalize), orchestrator that sequences stages, worker that dequeues from FIFO queue and invokes orchestrator.

**Does NOT own**: HTTP handling, auth decisions, job visibility scoping, frontend rendering. Never imports from `backend/`.

### Domain (`domain/`)
**Owns**: Canonical enums (`JobStatus`, `PipelineStage`), shared Pydantic models (`Job`, `JobEvent`), `AbstractJobStore` interface, filesystem `JobStore` implementation, `Queue` abstraction and filesystem implementation, domain exceptions.

**Does NOT own**: HTTP logic, pipeline computation, UI rendering. Serves as the single source of truth for data definitions shared between `backend/` and `pipeline/`.

### Frontend (`frontend/`)
**Owns**: All UI rendering — login form, upload widget (drag-and-drop), job card list, progress bar, download button, toast notifications, polling loop.

**Does NOT own**: Auth decisions (follows backend 401 responses), business logic, job state management (reads snapshots from API).

### Worker Process
**Owns**: Global single-instance background loop. Dequeues jobs from the FIFO queue, invokes `pipeline/orchestrator`, writes events to `events.jsonl`, transitions job state through the job store. Enforces per-session concurrency (one active job per `judge_session_id`).

**Does NOT own**: HTTP serving, auth, frontend rendering.

## Data Ownership

| Entity | Owned By | Schema Location | Access Pattern |
|---|---|---|---|
| `Job` | `domain/models.py` | `data/jobs/<id>/job.json` | Read/write via `JobStore` only; no direct filesystem access from `backend/` or `pipeline/` |
| `JobEvent` | `domain/models.py` | `data/jobs/<id>/events.jsonl` | Append-only via `JobStore`; read for timeline/audit |
| `JobStatus` enum | `domain/models.py` | Python enum (queued, running, completed, failed, cancelled) | Imported by all modules; string literals prohibited outside enum definition |
| `PipelineStage` enum | `domain/models.py` | Python enum (ingest, preprocess, pose, retarget, evaluate, package, finalize) | Imported by `pipeline/` and `domain/`; never referenced in `backend/routes.py` directly |
| `AccessToken` (JWT) | `backend/auth.py` | Stateless — validated via HMAC signature | Issued by `POST /api/auth/login`, validated by FastAPI dependency on every protected route |
| `judge_session_id` | Generated in `backend/auth.py` at login | Embedded in JWT claims; stored in `job.json` per-job | Judge-scoped queries filter by this value at the `JobStore` level |
| Source video file | `backend/routes.py` (upload handler) | `data/jobs/<id>/upload/<filename>` | Written once at upload; read by pipeline stages; never mutated |
| Pipeline artifacts (frames, pose, trajectory) | `pipeline/` stage modules | `data/jobs/<id>/work/` | Produced and consumed within pipeline; no API endpoint reads them directly |
| LeRobot dataset output | `pipeline/package.py` | `data/jobs/<id>/output/` | Packaged by pipeline; served via download endpoint with path-traversal protection |
| Queue entries | `domain/queue.py` | `data/queue/` | Read/write via `Queue` abstraction only |

## Data Flow

```ascii
┌──────────────────────────────────────────────────────────────────────┐
│                          FRONTEND (Browser)                          │
│  index.html + app.js + style.css                                     │
│  • Login form (password → JWT)                                       │
│  • Upload widget (drag-and-drop, multipart POST)                     │
│  • Job cards (poll GET /api/jobs/{id} every 2s)                     │
│  • Download button (GET /api/jobs/{id}/download)                     │
└────────────────────────────┬─────────────────────────────────────────┘
                             │  HTTP/1.1 (JSON + multipart/form-data)
                             │  Authorization: Bearer <JWT>
                             ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     BACKEND API (FastAPI, port 8000)                  │
│                                                                      │
│  POST /api/auth/login   ──► verify_password() ──► create_access_token│
│  POST /api/jobs/upload  ──► validate + persist video                 │
│                             ──► enqueue job (via Queue)              │
│  GET  /api/jobs         ──► JobStore.list (scoped by judge_session)  │
│  GET  /api/jobs/{id}    ──► JobStore.get                             │
│  DELETE /api/jobs/{id}  ──► JobStore.delete                          │
│  GET  /api/jobs/{id}/download ──► stream zip from output/            │
└────────────┬─────────────────────────────┬───────────────────────────┘
             │ imports (never reverse)     │ calls
             ▼                             ▼
┌────────────────────────┐    ┌────────────────────────────────────────┐
│     domain/            │    │           pipeline/                    │
│                        │    │                                        │
│  models.py             │    │  worker.py                             │
│    JobStatus (enum)    │◄───│    • Global background loop            │
│    PipelineStage (enum)│    │    • Dequeue from Queue                │
│    Job (pydantic)      │    │    • Invoke orchestrator               │
│    JobEvent (pydantic) │    │    • Write events → JobStore           │
│    JudgeSession        │    │                                        │
│                        │    │  orchestrator.py                       │
│  job_store.py (ABC)    │    │    • Stage sequencer                   │
│  job_store_fs.py       │    │    • Progress callbacks                │
│    read/write job.json │    │    • Error → JobStore.transition       │
│    append events.jsonl │    │                                        │
│                        │    │  ingest.py                             │
│  queue.py (ABC)        │    │    • Video validation + copy           │
│  queue_fs.py           │    │                                        │
│    read/write queue/   │    │  preprocess.py                         │
│                        │    │    • Frame extraction (ffmpeg/OpenCV)  │
│  exceptions.py         │    │                                        │
│    JobNotFoundError    │    │  pose.py                               │
│    SessionLimitError   │    │    • MediaPipe Pose landmarks           │
│                        │    │                                        │
└───────────┬────────────┘    │  retarget.py                           │
            │                 │    • pinocchio IK                       │
            │ reads/writes    │                                        │
            ▼                 │  evaluate.py                           │
┌────────────────────────┐    │    • 5-gate quality (green/yellow/red) │
│   data/ (filesystem)   │    │                                        │
│                        │    │  package.py                            │
│  data/jobs/<id>/       │    │    • LeRobot format (Parquet + meta)   │
│    job.json            │    │                                        │
│    events.jsonl        │    │  finalize.py                           │
│    upload/<video>      │    │    • Cleanup, summary, state→completed │
│    work/               │    └────────────────────────────────────────┘
│      frames/           │
│      pose/             │
│      trajectory/       │
│    output/             │
│      dataset/          │
│    logs/               │
│                        │
│  data/queue/           │
│    <job_id>.json       │
└────────────────────────┘
```

## API Contract Strategy

- **Format**: OpenAPI 3.0, auto-generated by FastAPI from route decorators and Pydantic models. Accessible at `/docs` (Swagger UI) and `/openapi.json`.
- **Source of truth**: Backend route handlers (`backend/routes.py`) define the contract. Pydantic models in `domain/models.py` define the wire format.
- **Frontend sync**: Manual for MVP — `frontend/app.js` reads the OpenAPI spec for endpoint paths and response shapes. A future `make api-sync` could generate TypeScript types.
- **Drift detection**: `make test` runs integration tests against the API. Manual visual inspection of `/docs`.
- **Breaking change policy**: New optional fields may be added freely. Field removal or type change requires a major version bump. The `TokenResponse` and `JobResponse` shapes are the stability boundary.
- **Auth contract**: `POST /api/auth/login` accepts `{"password": "..."}` and returns `{"access_token": "...", "token_type": "bearer"}`. All protected endpoints require `Authorization: Bearer <token>`. 401 on expired/invalid token, 403 on insufficient role.

## Integration Modes

| Mode | Description | When Used |
|---|---|---|
| **Direct HTTP** | Frontend makes fetch() calls to `/api/*` on the same origin | Normal browser interaction (upload, poll, download) |
| **Polling** | Frontend polls `GET /api/jobs/{id}` every 2s while job is active | Job progress updates in the dashboard |
| **In-process dispatch** | Worker dequeues a job ID from the FIFO queue and calls `pipeline/orchestrator.run_pipeline()` synchronously within the same process | All pipeline execution (single-process modular monolith) |
| **Filesystem I/O** | Job store, queue, pipeline stages read/write `data/` directories | All persistent state — no database, no message broker |
| **Docker volume mount** | `data/` directory mounted as a persistent volume on the host or cloud disk | Production persistence across container restarts |
| **Render persistent disk** | Render platform mounts a persistent disk at `/app/data` | Default deployment target |

## Non-Goals

This architecture document does NOT cover:

- **Horizontal scaling**: The MVP runs one process, one worker. Multi-worker or multi-instance scaling (with distributed locking, shared queue) is explicitly out of scope for v0.1.
- **Database integration**: PostgreSQL, SQLite, Redis — all explicitly rejected in favor of filesystem durability for MVP simplicity.
- **Cloud-native orchestration**: Kubernetes, service mesh, autoscaling — the deployment target is a single Docker container.
- **Mobile app architecture**: No native mobile client exists or is planned.
- **CI/CD pipeline design**: Covered in `docs/engineering/quality-gates.md` and future CI docs.
- **Performance SLAs**: Pipeline throughput targets are documented in the TRD, not here.
- **Historical migration plans**: The repo is pre-MVP; there is no production data to migrate.
- **Third-party API integrations** (Replicate, AWS Bedrock, Langfuse): These are stretch-goal dependencies mentioned in `README.md` but not part of core MVP architecture. The core path uses only local MediaPipe + pinocchio.
