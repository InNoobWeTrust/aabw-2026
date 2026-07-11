# RoboData — System Architecture

> **Status**: draft
> **Owner**: RoboData team
> **Created**: 2026-07-11

## Architecture Inventory

| Component | Technology | Role | Current State |
|---|---|---|---|
| Backend API | Python 3.10+ / FastAPI | HTTP server, auth, routes, static file mount | Exists: `backend/server.py`, `backend/routes.py`, `backend/auth.py` |
| Pipeline | Python, MediaPipe, OpenCV, pinocchio, pandas, pyarrow | Video → pose → retarget → evaluate → package | Exists: `pipeline/orchestrator.py` + stage modules |
| Domain (shared) | Python / Pydantic | Enums, shared job/review models, persistence schemas | Exists: `domain/` package |
| Frontend | Vanilla HTML/JS/CSS, static | Upload UI, job polling, dashboard, download | Exists: `frontend/index.html`, `app.js`, `style.css` |
| Job Store | Filesystem JSON + JSONL | Durable per-job state, event log | Exists: `backend/job_store.py` |
| Queue | In-process FIFO + filesystem-backed state | Dispatch order, per-session concurrency limit | Exists: `backend/queue_manager.py` + job-store coordination |
| Worker | Python async background task | Dequeue, run pipeline, write events | Exists in-process via queue manager + `_run_pipeline` |
| Auth | JWT (HS256) + hmac.compare_digest | Judge/admin password verification, token issuance/validation | Exists: `backend/auth.py` |
| Review Store | Filesystem JSON + JSONL | Persist pose/retarget review snapshots + SSE replay events | Exists: `backend/review_store.py` |
| Review Service | Python async + Featherless + Daytona | Async single-shot stage reviews with SSE-friendly persistence | Exists: `backend/review_service.py` |
| Reviewer Assistant | Python async bounded tool loop + Featherless + Daytona | Human reviewer chat assistance over persisted artifacts | Exists: `backend/assistant_service.py`, `backend/assistant_store.py` |
| Mapping Calibrator (planned) | Python async bounded agent + profile-driven retarget rerun | Agentic mapping profile selection and calibrated rerender | Planned: see `docs/specs/agentic-mapping-calibration.md` |
| Configuration | Pydantic Settings (`backend/config.py`) | Env var → typed settings, `.env` loading | Exists |
| Deployment | Docker (single container), Render Procfile | Production serving on `$PORT` | Exists: `Dockerfile`, `Procfile` |
| Observability | `logging` (stdlib) + persisted event streams | Structured pipeline/review/assistant events | Partial — persisted events exist; centralized tracing still minimal |
| External LLM Inference | Featherless | Review and assistant model inference | Optional; configured when API key is present |
| External Sandbox | Daytona | Sandbox execution for external review/assistant actions | Optional; configured when API key is present |

## Responsibility Split

### Backend API (`backend/`)
**Owns**: HTTP ingress, CORS, static file serving, password verification webhook, JWT issuance/validation FastAPI dependencies, route definitions, request validation, response serialization.

**Does NOT own**: Pipeline stage logic (delegates to `pipeline/orchestrator`), job state persistence (delegates to `domain/job_store`), queue management (delegates to `domain/queue`), worker lifecycle (delegates to `pipeline/worker`).

### Pipeline (`pipeline/`)
**Owns**: All video processing stages (ingest, preprocess, pose, retarget, evaluate, package, finalize), artifact generation, deterministic baseline mapping, and profile-driven calibrated retarget reruns once a mapping profile exists.

**Does NOT own**: HTTP handling, auth decisions, job visibility scoping, frontend rendering, or open-ended agent orchestration. Never imports from `backend/` for business decisions beyond configuration/service boundaries already exposed by the backend.

### Domain (`domain/`)
**Owns**: Canonical enums (`JobStatus`, `PipelineStage`, `ReviewStatus`, assistant session enums), shared Pydantic models (`Job`, `JobEvent`, `ReviewSnapshot`, assistant session/message records), and persistence-facing schemas shared between backend and pipeline.

**Does NOT own**: HTTP logic, pipeline computation, UI rendering, or provider SDK calls. Serves as the single source of truth for data definitions shared between `backend/` and `pipeline/`.

### Review Service
**Owns**: Asynchronous stage-review lifecycle, persisted review events, SSE-compatible replay streams, and provider/sandbox handoff for single-shot pose and retarget reviews.

**Does NOT own**: Main pipeline completion semantics, frontend rendering, or calibrated robot trajectory generation.

### Reviewer Assistant
**Owns**: Human-reviewer chat assistance, bounded tool loop, persisted assistant sessions, and SSE event streaming for interactive investigation.

**Does NOT own**: Canonical automated review verdicts, primary retarget computation, or unrestricted artifact access.

### Mapping Calibrator (planned)
**Owns**: Agentic mapping-profile suggestion, sparse correction-anchor proposals, baseline-vs-calibrated comparison inputs, and calibrated retarget rerun requests.

**Does NOT own**: Raw pose extraction, dense frame-by-frame robot joint generation as the source of truth, or open-ended multi-tool autonomy. It acts as a bounded calibration layer on top of deterministic retargeting.

### Frontend (`frontend/`)
**Owns**: All UI rendering — login form, upload widget (drag-and-drop), job card list, progress bar, download button, toast notifications, polling loop.

**Does NOT own**: Auth decisions (follows backend 401 responses), business logic, job state management (reads snapshots from API).

### Worker Process
**Owns**: Global single-instance background loop. Dequeues jobs from the FIFO queue, invokes `pipeline/orchestrator`, writes events to `events.jsonl`, transitions job state through the job store. Enforces per-session concurrency (one active job per `judge_session_id`).

**Does NOT own**: HTTP serving, auth, frontend rendering.

## Data Ownership

| Entity | Owned By | Schema Location | Access Pattern |
|---|---|---|---|
| `Job` | `domain/jobs.py` | `data/jobs/<id>/job.json` | Read/write via `JobStore` only; no direct filesystem access from arbitrary callers |
| `JobEvent` | `domain/jobs.py` | `data/jobs/<id>/events.jsonl` | Append-only via `JobStore`; read for timeline/audit |
| `JobStatus` enum | `domain/enums.py` | Python enum (queued, running, completed, failed, cancelled) | Imported by all modules; string literals prohibited outside enum definition |
| `PipelineStage` enum | `domain/enums.py` | Python enum (ingest, preprocess, pose, retarget, evaluate, package, finalize) | Imported by `pipeline/` and `domain/` |
| `ReviewSnapshot` | `domain/reviews.py` | `data/jobs/<id>/output/reviews/<stage>/review.json` | Read/write via `backend/review_store.py`; exposed over `/api/jobs/{id}/reviews/*` |
| `ReviewEvent` | `domain/reviews.py` | `data/jobs/<id>/output/reviews/<stage>/events.jsonl` | Append-only for SSE replay and persisted stage-review history |
| `AssistantSessionSnapshot` | `domain/reviews.py` | `data/jobs/<id>/output/assistant_sessions/<session_id>/session.json` | Read/write via `backend/assistant_store.py`; exposed over `/api/jobs/{id}/assistant/sessions/*` |
| `AssistantMessage` | `domain/reviews.py` | `data/jobs/<id>/output/assistant_sessions/<session_id>/messages.jsonl` | Append-only transcript; used for assistant context and UI reload |
| `MappingProfile` (planned) | `pipeline/` + future domain schema | `data/jobs/<id>/output/calibration/mapping_profile.json` | Produced by the mapping calibrator, then consumed by deterministic retarget rerun |
| `CalibrationDecision` (planned) | mapping calibrator | `data/jobs/<id>/output/calibration/decision.json` | API- and UI-visible summary of baseline vs calibrated mapping recommendation |
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
│  GET  /api/jobs/{id}/reviews/* ──► ReviewStore + SSE replay          │
│  GET  /api/jobs/{id}/assistant/* ──► AssistantStore + bounded loop   │
│  DELETE /api/jobs/{id}  ──► JobStore.delete                          │
│  GET  /api/jobs/{id}/download ──► stream zip from output/            │
└────────────┬─────────────────────────────┬───────────────────────────┘
             │ imports (never reverse)     │ calls
             ▼                             ▼
┌────────────────────────┐    ┌────────────────────────────────────────┐
│     domain/            │    │           pipeline/                    │
│                        │    │                                        │
│  jobs.py               │    │  worker / queue loop                   │
│    JobStatus (enum)    │◄───│    • Global background loop            │
│    PipelineStage (enum)│    │    • Dequeue from Queue                │
│    Job / JobEvent      │    │    • Invoke orchestrator               │
│                        │    │    • Write events → JobStore           │
│  reviews.py            │    │                                        │
│    ReviewSnapshot      │    │  orchestrator.py                       │
│    AssistantSession    │    │    • Stage sequencer                   │
│    AssistantMessage    │    │    • Progress callbacks                │
│                        │    │    • Error → JobStore.transition       │
│  enums.py              │    │                                        │
│    ReviewStage         │    │  ingest.py                             │
│    ReviewStatus        │    │    • Video validation + copy           │
│    Assistant enums     │    │                                        │
│                        │    │  preprocess.py                         │
│                        │    │    • Frame extraction (ffmpeg/OpenCV)  │
│                        │    │                                        │
│                        │    │  pose.py                               │
│                        │    │    • MediaPipe Pose landmarks          │
│                        │    │                                        │
└───────────┬────────────┘    │  retarget.py                           │
            │                 │    • Deterministic baseline mapping    │
            │ reads/writes    │    • profile-driven calibrated rerun   │
            ▼                 │                                        │
┌────────────────────────┐    │  evaluate.py                           │
│   data/ (filesystem)   │    │    • 5-gate quality (green/yellow/red) │
│                        │    │                                        │
│  data/jobs/<id>/       │    │  package.py                            │
│    job.json            │    │    • LeRobot format (Parquet + meta)   │
│    events.jsonl        │    │                                        │
│    upload/<video>      │    │  future mapping_calibrator.py          │
│    work/               │    │    • bounded agentic profile selection │
│      frames/           │    │    • baseline vs calibrated comparison │
│      pose/             │    └────────────────────────────────────────┘
│      trajectory/       │
│    output/             │
│      dataset_skeleton/ │
│      dataset_robot/    │
│      reviews/          │
│      assistant_sessions/│
│      calibration/      │
│    logs/               │
│                        │
│  data/queue/           │
│    <job_id>.json       │
└────────────────────────┘
```

## API Contract Strategy

- **Format**: OpenAPI 3.0, auto-generated by FastAPI from route decorators and Pydantic models. Accessible at `/docs` (Swagger UI) and `/openapi.json`.
- **Source of truth**: Backend route handlers (`backend/routes.py`) define the contract. Pydantic models in `backend/models.py` and shared enums/models in `domain/` define the wire format.
- **Frontend sync**: Manual for MVP — current frontend reads JSON responses directly. The planned Next.js frontend should eventually consume generated types from OpenAPI.
- **Drift detection**: `make test` / `pytest` run route-level integration tests. Manual visual inspection of `/docs` remains the fallback.
- **Breaking change policy**: New optional fields may be added freely. Field removal or type change requires a major version bump. `TokenResponse`, `JobResponse`, review snapshot payloads, and future mapping-calibration payloads are the stability boundary.
- **Auth contract**: `POST /api/auth/login` accepts `{"password": "..."}` and returns `{"access_token": "...", "token_type": "bearer"}`. All protected endpoints require `Authorization: Bearer <token>`. 401 on expired/invalid token, 403 on insufficient role.
- **Streaming contract**: Review and assistant endpoints use SSE for replayable event streams. The planned mapping calibrator should follow the same event model (`status`, `section`, `token`, `result`, `error`, `done`).

## Integration Modes

| Mode | Description | When Used |
|---|---|---|
| **Direct HTTP** | Frontend makes fetch() calls to `/api/*` on the same origin | Normal browser interaction (upload, poll, download) |
| **Polling** | Frontend polls `GET /api/jobs/{id}` every 2s while job is active | Job progress updates in the dashboard |
| **SSE streaming** | Frontend subscribes to `/reviews/*/stream`, `/assistant/*/stream`, and future `/mapping-calibration/stream` | Live review, assistant, and future calibration updates |
| **In-process dispatch** | Worker dequeues a job ID from the FIFO queue and calls `pipeline/orchestrator.run_pipeline()` synchronously within the same process | All pipeline execution (single-process modular monolith) |
| **Filesystem I/O** | Job store, queue, review/assistant stores, and pipeline stages read/write `data/` directories | All persistent state — no database, no message broker |
| **External sandboxed inference** | Backend review/assistant services optionally create Daytona sandboxes and call Featherless | Async single-shot reviews and bounded assistant turns when credentials exist |
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
- **Third-party API integrations** beyond the already-documented review/assistant hooks: broader Replicate/AWS Bedrock/Langfuse orchestration remains outside the core local-first artifact pipeline.
- **Agent-generated dense robot trajectories**: the planned mapping calibrator may suggest profiles and sparse anchors, but full unconstrained trajectory generation by the agent is explicitly out of scope.
