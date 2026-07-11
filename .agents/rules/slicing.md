# Vertical Slicing — RoboData

Applies to **all feature implementation, system migration, and multi-component tasks**. Decompose work into end-to-end vertical slices, not horizontal layers.

---

## Principles

- A **vertical slice** implements one narrow user journey cutting through all layers: schema → store → route → UI (or schema → store → pipeline stage → output).
- Each slice is fully functional, verifiable, and testable on its own.
- Build incrementally — do not write schema for future slices.
- Each slice should feel like a mini-release.

---

## RoboData Vertical Slice Catalog

The canonical implementation order and dependency chain:

### Slice 0: Project Foundation (prerequisite)
**Goal**: Working project scaffold with lint, test, and run targets.
- `Makefile` with `lint`, `test`, `format`, `run` targets
- `.env.example` with required vars
- `pyproject.toml` dependencies installable
- All four top-level packages (`backend/`, `pipeline/`, `domain/`, `frontend/`) exist with `__init__.py`
- `AGENTS.md` and `GLOSSARY.md` present
- Empty `tests/conftest.py` with test infrastructure (temp dirs, fixtures)
**Verification**: `make lint && make test` passes (0 tests, 0 lint errors)

### Slice 1: Auth & Session Model
**Goal**: Password-based login, JWT issuance, role claims, FastAPI dependencies.
- `domain/models.py`: `JobStatus` enum, `PipelineStage` enum (canonical values only — no drift)
- `backend/auth.py`: `verify_password` (hmac), `create_access_token`, `get_current_judge`, `get_current_admin`
- `backend/config.py`: Pydantic Settings with `JUDGE_PASSWORD`, `ADMIN_PASSWORD`, `JWT_SECRET_KEY`
- `backend/routes.py`: `POST /api/login` with judge/admin password discrimination
- `frontend/`: login form → token storage → authenticated API calls
**Tests**: `test_auth.py` — all 8 minimum tests from `tdd.md`
**Verification**: Judge login → JWT with `role=judge` + `judge_session_id`. Admin login → JWT with `role=admin`. Wrong password → 401.

### Slice 2: Job Store (Filesystem Persistence)
**Goal**: Abstract `JobStore` interface + filesystem implementation. No pipeline yet.
- `domain/job_store.py`: `AbstractJobStore` ABC (create, get, list, update_status, delete)
- `domain/job_store_fs.py`: filesystem implementation (reads/writes `job.json`, appends `events.jsonl`)
- `domain/exceptions.py`: `JobNotFoundError`, `InvalidTransitionError`, `SessionLimitError`
- `domain/models.py`: `Job` model, `JobEvent` model
- `data/jobs/<job_id>/` layout: `job.json`, `events.jsonl`, `upload/`, `work/`, `output/`, `logs/`
**Tests**: `test_job_store.py` — all minimum tests from `tdd.md`
**Verification**: Create job → `job.json` on disk. List scoped to session → only that session's jobs.

### Slice 3: Upload & Ingest
**Goal**: Video upload endpoint, validation, ingest stage.
- `backend/routes.py`: `POST /api/jobs` — upload, validate, create job, enqueue
- `pipeline/ingest.py`: validate extension, size, magic bytes; copy to `data/jobs/<id>/upload/`
- Judge session limit enforcement (max 1 `queued`/`running` per session)
**Tests**: `test_ingest.py` + upload route tests in `test_routes.py`
**Verification**: Upload MP4 → job created with `status=queued`. Oversize/AVI → rejected. Second upload while queued → rejected.

### Slice 4: Queue & Worker (Background Dispatch)
**Goal**: FIFO queue, background worker, job dispatch.
- `domain/queue.py`: FIFO queue abstraction + filesystem impl (`data/queue/`)
- `pipeline/worker.py`: dequeue loop, invoke orchestrator, write events
- Worker restart policy: `running → failed: worker_restarted`
- Worker lifecycle: start on server startup, graceful shutdown
**Tests**: `test_worker.py` — all minimum tests from `tdd.md`
**Verification**: Enqueue job → worker picks up FIFO. Kill worker → running job failed, queued jobs preserved.

### Slice 5: Orchestrator + Pipeline Stages (Empty)
**Goal**: Stage sequencer that runs stages in order, manages callbacks, handles failure.
- `pipeline/orchestrator.py`: `run_pipeline` — iterate stages, call progress callback, catch failures
- All 7 stage modules as stubs (accept inputs, return placeholder outputs)
- `events.jsonl` entries for stage entry/exit
**Tests**: `test_orchestrator.py` — stage sequencing, failure propagation, cancel mid-pipeline
**Verification**: Orchestrator runs stub stages in order. Stub failure → job transitions to `failed`.

### Slice 6: Pipeline Stages (Real Implementations)
**Goal**: Each stage implemented end-to-end with real processing.
- `pipeline/preprocess.py`: frame extraction, resolution normalization
- `pipeline/pose.py`: MediaPipe Pose landmark extraction
- `pipeline/retarget.py`: pinocchio IK retargeting → `JointTrajectory`
- `pipeline/evaluate.py`: 5-gate quality evaluation → `QualityGrade`
- `pipeline/package.py`: LeRobot v2 format export → `LeRobotDataset`
- `pipeline/finalize.py`: cleanup, summary, final state transition
**Tests**: One test file per stage — all minimum tests from `tdd.md`
**Verification**: Full pipeline end-to-end on a small test video. Output LeRobot dataset is valid.

### Slice 7: Job Visibility & Admin Oversight
**Goal**: Judge sees own jobs; admin sees all. Cancel/delete/download endpoints.
- `GET /api/jobs` — scoped to judge or admin
- `POST /api/jobs/{id}/cancel` — cancel queued/running job
- `DELETE /api/jobs/{id}` — delete job and artifacts
- `GET /api/jobs/{id}/download` — download LeRobot dataset as zip
- `GET /api/jobs/{id}` — job status poll (JobSnapshot)
**Tests**: `test_isolation.py` — all minimum tests from `tdd.md`
**Verification**: Judge A cannot see/cancel/delete/download Judge B's jobs. Admin can see/act on all.

### Slice 8: Frontend (Full UI)
**Goal**: Complete web UI for judges and admins.
- Login page (password → token)
- Job submission (drag-drop upload, progress bar)
- Job list with polling (status, stage, progress)
- Download button for completed jobs
- Admin dashboard (all jobs, cancel any, delete any)
**Verification**: End-to-end manual test: login → upload → poll → wait for completion → download.

---

## Slice Execution Rules

1. **One slice at a time**. Do not start Slice N+1 until Slice N is complete and verified.
2. **Each slice adds tests**. The test suite grows with every slice.
3. **Commit after each slice** (if committing is enabled). Small, atomic changesets.
4. **Between slices, run `make lint && make test`**. The suite must stay green.
5. **If a slice exposes prior code drift**, fix it within that slice's scope. Do not carry drift forward.
