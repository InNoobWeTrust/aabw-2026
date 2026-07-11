# RoboData MVP — Implementation Task Plan

> **Status**: draft
> **Owner**: RoboData team
> **Created**: 2026-07-11
> **Source**: `docs/specs/mvp-trd.md` — all 16 drift corrections plus new feature requirements

## Slice Strategy

Each slice is a **vertical tracer bullet** — it delivers an observable change from the user's perspective or a foundational capability that unblocks subsequent slices. Slices are ordered by dependency: each slice depends only on slices above it.

Done criteria are absolute. A slice is not done until every criterion is met and `make quality && make test` passes.

---

## Slice 0 — Foundation Alignment

**Goal**: Fix naming drift in the existing codebase so all subsequent work uses canonical terms. No new functionality. Pure rename + cleanup.

**Scope**: Backend models, routes, frontend JS, .env.example, Dockerfile duplicate line.

### Tasks

0.1. Rename `JobStatus.PENDING` → `JobStatus.QUEUED` with value `"queued"` in `backend/models.py`
0.2. Update all references to `JobStatus.PENDING` in `backend/routes.py` (line 205)
0.3. Update `ACTIVE_STATUSES` in `frontend/app.js:5` from `"pending"` → `"queued"`
0.4. Update `getStatusLabel` in `frontend/app.js:342-354` — add `queued: "Queued"`, remove `pending`
0.5. Update `getStatusColor` in `frontend/app.js:356-368` — same rename
0.6. Remove duplicate `COPY . .` line in `Dockerfile:23`
0.7. Rename `ACCESS_PASSWORD` → `JUDGE_PASSWORD` in `backend/config.py` and `.env.example`
0.8. Add `ADMIN_PASSWORD` to `backend/config.py` and `.env.example`
0.9. Rename `OUTPUT_DIR` → `DATA_DIR` (default `./data`) in `backend/config.py`; deprecate `output_dir` and `upload_dir` properties
0.10. Update `.gitignore` to add `data/` (replaces `uploads/*` and `outputs/*` gitignore lines)

### Done Criteria

- [ ] `JobStatus.QUEUED` exists, `JobStatus.PENDING` does not
- [ ] `frontend/app.js` references `"queued"` not `"pending"` in all status maps
- [ ] `.env.example` lists `JUDGE_PASSWORD` and `ADMIN_PASSWORD` (not `ACCESS_PASSWORD`)
- [ ] `Dockerfile` has exactly one `COPY . .` line
- [ ] `make quality && make test` passes (existing stub tests still pass)
- [ ] `make dev` starts the server without import errors
- [ ] Frontend login and upload still work (manual smoke test)

---

## Slice 1 — Domain Package

**Goal**: Create the `domain/` package with canonical enums, shared models, and interface definitions. No behavior change in backend or pipeline yet — just the data definitions and interfaces exist.

**Scope**: New `domain/` package with 5 modules.

### Tasks

1.1. Create `domain/__init__.py`
1.2. Create `domain/models.py`:
    - `JobStatus` enum: `QUEUED`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED`
    - `PipelineStage` enum: `INGEST`, `PREPROCESS`, `POSE`, `RETARGET`, `EVALUATE`, `PACKAGE`, `FINALIZE`
    - `Job` pydantic model (all fields per PR-2 in TRD)
    - `JobEvent` pydantic model (timestamp, event type, payload dict)
    - `JudgeSession` pydantic model (session_id, role)
1.3. Create `domain/exceptions.py`:
    - `JobNotFoundError(job_id)`
    - `SessionLimitError(judge_session_id)`
    - `InvalidStateTransitionError(job_id, from_status, to_status)`
1.4. Create `domain/job_store.py`:
    - `AbstractJobStore` ABC with methods: `create`, `get`, `list_by_session`, `list_all`, `update`, `delete`, `append_event`, `get_events`
1.5. Create `domain/queue.py`:
    - `AbstractQueue` ABC with methods: `enqueue`, `dequeue`, `peek`, `remove`, `count`, `has_active_for_session`

### Done Criteria

- [ ] `domain/models.py` has all 5 items (2 enums, 3 pydantic models)
- [ ] `domain/exceptions.py` has 3 exception classes
- [ ] `domain/job_store.py` has `AbstractJobStore` ABC with all 7 methods (abstract)
- [ ] `domain/queue.py` has `AbstractQueue` ABC with all 5 methods (abstract)
- [ ] `backend/models.py` imports and re-exports `JobStatus` from `domain/models.py` (backward compat)
- [ ] `make quality && make test` passes
- [ ] All enums/match the TRD naming exactly (`QUEUED` not `PENDING`, `INGEST` not `INGEST`)

---

## Slice 2 — Auth: Roles & Sessions

**Goal**: Implement dual-role JWT auth. Judges get anonymous `judge_session_id`; admins get `role=admin`. FastAPI dependencies enforce role checks. No job isolation yet (that's Slice 6).

**Scope**: `backend/auth.py`, `backend/config.py`, `backend/routes.py` (login only).

### Tasks

2.1. Create `backend/dependencies.py`:
    - `get_job_store()` → returns `JobStore` singleton
    - `get_current_user()` → validates JWT, returns payload (existing from `auth.py`)
    - `get_current_judge()` → calls `get_current_user`, asserts `role == "judge"`, returns `JudgeSession`
    - `get_current_admin()` → calls `get_current_user`, asserts `role == "admin"`
2.2. Update `backend/auth.py`:
    - `verify_judge_password(plain)` → `hmac.compare_digest(plain, settings.judge_password)`
    - `verify_admin_password(plain)` → `hmac.compare_digest(plain, settings.admin_password)`
    - `create_judge_token()` → creates JWT with `{"role": "judge", "judge_session_id": "<uuid>"}`
    - `create_admin_token()` → creates JWT with `{"role": "admin"}`
2.3. Update `POST /api/auth/login` in `backend/routes.py`:
    - Try `verify_admin_password` → if match, return `create_admin_token()`
    - Try `verify_judge_password` → if match, return `create_judge_token()`
    - If neither, return 401
2.4. Update `GET /api/auth/verify` — return role and session info from token
2.5. Write `tests/test_auth.py`:
    - `test_judge_login_success`
    - `test_admin_login_success`
    - `test_login_wrong_password`
    - `test_judge_token_has_session_id`
    - `test_admin_token_has_role`
    - `test_expired_token_returns_401`
    - `test_invalid_token_returns_401`
    - `test_judge_cannot_access_admin_endpoint` (use placeholder admin endpoint)

### Done Criteria

- [ ] `POST /api/auth/login` with `JUDGE_PASSWORD` → JWT with `role=judge`, `judge_session_id` (UUID)
- [ ] `POST /api/auth/login` with `ADMIN_PASSWORD` → JWT with `role=admin`
- [ ] `POST /api/auth/login` with wrong password → 401
- [ ] `get_current_judge` dependency extracts `judge_session_id` from JWT
- [ ] `get_current_admin` dependency asserts `role == "admin"`, raises 403 otherwise
- [ ] `tests/test_auth.py` has 8 tests, all passing
- [ ] `make quality && make test` passes
- [ ] `hmac.compare_digest` used in all password comparisons; never `==`

---

## Slice 3 — Filesystem Job Store

**Goal**: Replace the in-memory `_jobs` dict with durable filesystem persistence via `JobStore`. Every job lives in `data/jobs/<job_id>/` with `job.json` and `events.jsonl`.

**Scope**: `domain/job_store_fs.py`, `tests/test_job_store.py`, update `backend/dependencies.py`.

### Tasks

3.1. Create `domain/job_store_fs.py`:
    - `JobStoreFs(AbstractJobStore)` with `data_dir: Path`
    - `create(job: Job)` → writes `job.json`, appends creation event
    - `get(job_id)` → reads `job.json`, returns `Job`
    - `list_by_session(judge_session_id)` → lists all jobs, filters by session
    - `list_all()` → lists all jobs
    - `update(job_id, **kwargs)` → reads, merges, writes atomically (temp + rename)
    - `delete(job_id)` → removes entire `data/jobs/<job_id>/` tree
    - `append_event(job_id, event: JobEvent)` → appends line to `events.jsonl`
    - `get_events(job_id)` → reads all lines from `events.jsonl`
    - `scan_running_jobs()` → finds all jobs with `status == "running"`
    - `scan_queued_jobs()` → finds all jobs with `status == "queued"`, sorted by `created_at`
3.2. Update `backend/dependencies.py`:
    - `get_job_store()` instantiates `JobStoreFs(data_dir=settings.data_dir)`
3.3. Write `tests/test_job_store.py`:
    - `test_create_and_get` — create a job, read it back, verify all fields
    - `test_update_atomic` — update a job, verify old data not lost, new fields present
    - `test_list_by_session` — create 3 jobs (2 judge A, 1 judge B), verify list scoped
    - `test_list_all` — list returns all jobs
    - `test_delete_removes_directory` — delete job, assert directory gone
    - `test_append_and_read_events` — append 3 events, read them back in order
    - `test_scan_running_jobs` — create running job, scan finds it
    - `test_scan_queued_jobs` — create 2 queued jobs, scan returns in created_at order
    - `test_get_nonexistent_raises` — `JobNotFoundError`
    - `test_atomic_write_survives_crash` — write to temp, kill before rename, old version intact

### Done Criteria

- [ ] `JobStoreFs` passes all 10 tests in `tests/test_job_store.py`
- [ ] Job writes are atomic (temp-file + `os.replace`)
- [ ] Event log is append-only with valid JSON per line
- [ ] `data/jobs/` directory is gitignored
- [ ] `make quality && make test` passes
- [ ] No reference to `_jobs` dict remains (backward compat via `JobStoreFs` wrapped in in-memory adapter if needed — prefer direct replacement)

---

## Slice 4 — Queue & Worker

**Goal**: Replace fire-and-forget `asyncio.create_task` with a persisted FIFO queue and a background worker loop. Enforce per-session concurrency limit.

**Scope**: `domain/queue_fs.py`, `pipeline/worker.py`, `tests/test_queue.py`, `tests/test_worker.py`.

### Tasks

4.1. Create `domain/queue_fs.py`:
    - `QueueFs(AbstractQueue)` with `data_dir: Path`
    - `enqueue(job_id, metadata)` → writes `<timestamp>_<job_id>.json` to `data/queue/`
    - `dequeue()` → lists files sorted, pops the first one, returns `(job_id, metadata)`
    - `peek()` → same as dequeue but doesn't remove
    - `remove(job_id)` → deletes the queue file for a specific job
    - `count()` → counts files in queue directory
    - `has_active_for_session(judge_session_id)` → checks `JobStore` for any `queued` or `running` job for this session
4.2. Create `pipeline/worker.py`:
    - `Worker` class with `run()` async loop:
      1. Dequeue job from queue
      2. Check session limit → if active, skip (leave in queue)
      3. Transition job to `running` via `JobStore`
      4. Invoke `pipeline/orchestrator.run_pipeline(job_id, video_path, output_dir, status_callback)`
      5. On completion: transition to `completed`, append event
      6. On failure: transition to `failed`, append event with error
      7. Loop
    - `status_callback` bridges orchestrator progress to `JobStore.update`
    - Context manager for worker lifecycle (start/stop)
4.3. Wire worker into `backend/server.py` lifespan:
    - `startup`: create `Worker`, start it as `asyncio.Task`
    - `shutdown`: signal worker to stop, await graceful shutdown
4.4. Update `backend/routes.py` upload endpoint:
    - Replace `asyncio.create_task(_run_pipeline(...))` with `queue.enqueue(job_id, ...)`
    - Remove `_run_pipeline` function (pipeline logic now in `pipeline/worker.py` via orchestrator)
    - Add session limit check before enqueue → 409 if active job exists
4.5. Write `tests/test_queue.py`:
    - `test_enqueue_dequeue_fifo` — enqueue 3 jobs, dequeue returns in order
    - `test_remove_from_queue` — enqueue, remove, dequeue returns next
    - `test_count` — count reflects enqueue/remove
    - `test_has_active_for_session_true` — judge has a `queued` job
    - `test_has_active_for_session_false` — judge has only `completed` jobs
4.6. Write `tests/test_worker.py`:
    - `test_worker_picks_up_queued_job` — enqueue job, start worker, job becomes `completed`
    - `test_worker_transitions_running_on_start` — job in queue, worker starts, status → `running`
    - `test_worker_failure_transitions_to_failed` — mock orchestrator to raise, job becomes `failed`
    - `test_worker_respects_session_limit` — judge has active job, worker skips it
    - `test_worker_idles_on_empty_queue` — empty queue, worker waits, doesn't crash

### Done Criteria

- [ ] `POST /api/jobs/upload` enqueues job, returns 202, doesn't start pipeline inline
- [ ] Worker picks up job from queue, runs pipeline, transitions to `completed`
- [ ] Pipeline failure → job status `failed` with error recorded
- [ ] Judge with active job gets 409 on upload attempt
- [ ] Queue persists across server restarts (files on disk)
- [ ] `tests/test_queue.py` has 5 tests, all passing
- [ ] `tests/test_worker.py` has 5 tests, all passing
- [ ] `make quality && make test` passes
- [ ] No `asyncio.create_task` for pipeline dispatch remains in `backend/routes.py`

---

## Slice 5 — Recovery

**Goal**: On process startup, reconcile all `running` jobs to `failed(worker_restarted)` and rebuild the queue from `queued` jobs.

**Scope**: `domain/job_store_fs.py` (already has scan methods), `pipeline/worker.py` startup, `tests/test_worker.py`.

### Tasks

5.1. Add `reconcile_on_startup(job_store)` function to `pipeline/worker.py`:
    - Scan all jobs with `status == "running"` via `JobStore.scan_running_jobs()`
    - For each: set `status = "failed"`, `error = "worker_restarted"`, append event
    - Scan all jobs with `status == "queued"` via `JobStore.scan_queued_jobs()`
    - Rebuild queue: enqueue each queued job (ordered by `created_at`)
5.2. Call `reconcile_on_startup` in `backend/server.py` lifespan startup, before starting worker
5.3. Write `tests/test_worker.py` additions:
    - `test_restart_fails_running_job` — create a job with `status=running`, simulate restart, verify status → `failed`, `error == "worker_restarted"`
    - `test_restart_preserves_queued_jobs` — create 2 queued jobs, simulate restart, verify both still `queued` and in queue
    - `test_restart_rebuilds_queue` — create 3 queued jobs, restart, worker picks them up in FIFO order

### Done Criteria

- [ ] Kill the process while a job is `running`. Restart. That job is now `failed` with `error: "worker_restarted"`
- [ ] Kill the process while jobs are `queued`. Restart. Those jobs remain `queued` and are processed by the worker
- [ ] 3 tests in `tests/test_worker.py` for restart behavior, all passing
- [ ] `make quality && make test` passes

---

## Slice 6 — Judge-Scoped APIs

**Goal**: Judge sessions can only see, cancel, and delete their own jobs. Implement `cancel` endpoint. Admin still sees all (no regression from current behavior during transition).

**Scope**: `backend/routes.py` (all job endpoints), `backend/dependencies.py`.

### Tasks

6.1. Update `POST /api/jobs/upload`:
    - Store `judge_session_id` in `Job` at creation time
    - Use `get_current_user()` dep (any authenticated user can upload)
6.2. Update `GET /api/jobs`:
    - Judge: calls `JobStore.list_by_session(judge_session_id)`
    - Admin: calls `JobStore.list_all()`
    - Route accepts either dep (`get_current_judge` or `get_current_admin`)
6.3. Update `GET /api/jobs/{job_id}`:
    - Judge: `JobStore.get(job_id)`, then assert `job.judge_session_id == caller.judge_session_id`
    - Admin: `JobStore.get(job_id)` — no session filter
6.4. Implement `POST /api/jobs/{job_id}/cancel`:
    - Validate caller can access the job (judge: own job; admin: any)
    - Validate job is `queued` or `running`
    - Transition to `cancelled`, remove from queue if still queued, append event
6.5. Update `DELETE /api/jobs/{job_id}`:
    - Judge: only own jobs
    - Admin: any job
6.6. Update `GET /api/jobs/{job_id}/download`:
    - Judge: only own completed jobs
    - Admin: any completed job
6.7. Write `tests/test_isolation.py`:
    - `test_judge_a_sees_only_own_jobs` — create jobs for judge A and judge B, judge A lists, only sees own
    - `test_judge_a_cannot_get_judge_b_job` — judge A tries to GET judge B's job → 404
    - `test_judge_a_cannot_cancel_judge_b_job` → 404
    - `test_judge_a_cannot_delete_judge_b_job` → 404
    - `test_admin_sees_all_jobs` — admin lists, sees judge A + judge B jobs
    - `test_admin_can_cancel_any_job`
    - `test_admin_can_delete_any_job`
    - `test_cancel_queued_job` — cancel succeeds, status → `cancelled`, removed from queue
    - `test_cancel_running_job` — cancel signal set, worker picks it up
    - `test_cannot_cancel_completed_job` → 409
    - `test_cannot_cancel_failed_job` → 409

### Done Criteria

- [ ] Judge A uploads job. Judge B logs in (different session). Judge B's job list is empty
- [ ] Judge A cannot GET/DELETE/cancel Judge B's job (returns 404)
- [ ] Admin sees all jobs from all judges
- [ ] Cancel endpoint works: queued → cancelled, running → cancelled, terminal → 409
- [ ] `tests/test_isolation.py` has 11 tests, all passing
- [ ] `make quality && make test` passes

---

## Slice 7 — Admin APIs

**Goal**: Admin-specific endpoints for global visibility and management. Separate the admin dashboard experience if needed.

**Scope**: `backend/routes.py` (admin route prefix), `tests/test_isolation.py`.

### Tasks

7.1. Add `GET /api/admin/jobs` — same as `GET /api/jobs` but requires `get_current_admin`, always returns all jobs
7.2. Add `GET /api/admin/jobs/{job_id}` — admin-only job detail (can see any job)
7.3. Add `POST /api/admin/jobs/{job_id}/cancel` — admin-only cancel
7.4. Add `DELETE /api/admin/jobs/{job_id}` — admin-only delete
7.5. Update `tests/test_isolation.py`:
    - Test admin endpoints with admin token → success
    - Test admin endpoints with judge token → 403

### Done Criteria

- [ ] Admin endpoints all return 403 when called with judge token
- [ ] Admin endpoints work with admin token
- [ ] `make quality && make test` passes

---

## Slice 8 — Frontend Updates

**Goal**: Update frontend to support new status names, handle 409 session limit, display error messages from new API responses. No new visual design — just functional alignment.

**Scope**: `frontend/app.js`, `frontend/index.html`, `frontend/style.css`.

### Tasks

8.1. Update status constants:
    - `ACTIVE_STATUSES`: `"queued"` instead of `"pending"` (already done in Slice 0)
    - `TERMINAL_STATUSES`: add `"cancelled"`
8.2. Update `getStatusLabel` and `getStatusColor` maps:
    - `"cancelled"` → label `"Cancelled"`, color gray
    - Remove old stage-as-status entries (`"preprocessing"`, etc.) when backend stops sending them
8.3. Update progress bar:
    - Show indeterminate animation for `queued` state (waiting in queue)
    - Show percentage for `running` state
8.4. Handle 409 on upload:
    - Catch 409 response → show toast: "You already have an active job. Please wait for it to complete."
8.5. Add cancel button to job cards for `queued` and `running` jobs:
    - Click → `POST /api/jobs/{job_id}/cancel` → refresh card
8.6. Update empty state text: "No jobs yet. Upload a video to get started."
8.7. Write `tests/test_frontend_integration.py` (optional for this slice — manual smoke test acceptable):
    - Upload flow, cancel flow, download flow

### Done Criteria

- [ ] Frontend shows "Queued" for newly uploaded jobs (was "Pending")
- [ ] Upload during active job shows 409 error toast
- [ ] Cancel button appears on queued/running jobs and works
- [ ] Download button appears on completed jobs
- [ ] All status colors match the TRD spec
- [ ] Manual smoke test: login → upload → see progress → download zip (with --reload dev server)
- [ ] `make quality && make test` passes

---

## Slice 9 — Deployment Hardening

**Goal**: Production-ready Docker image, Render configuration, health check endpoint, data directory initialization.

**Scope**: `Dockerfile`, `Procfile`, `backend/server.py`, `backend/routes.py`.

### Tasks

9.1. Add `GET /api/health` → `{"status": "ok"}` (unauthenticated) in `backend/routes.py`
9.2. Fix `Dockerfile`:
    - Remove duplicate `COPY . .` (done in Slice 0)
    - Install dependencies via `pip install -e .` (single command, reads `pyproject.toml`)
    - Pre-download MediaPipe model weights in the build step (`python -c "import mediapipe; ..."`)
    - Add `HEALTHCHECK` instruction (optional, Render uses HTTP health check)
9.3. Verify `Procfile` is correct:
    - `web: uvicorn backend.server:app --host 0.0.0.0 --port $PORT`
9.4. Update `backend/server.py`:
    - `startup`: create `data/jobs/` and `data/queue/` directories (already ensures `upload_dir`/`output_dir`, update for new path)
9.5. Add `data/` directory with `.gitkeep` files:
    - `data/jobs/.gitkeep`
    - `data/queue/.gitkeep`
9.6. Update `.env.example`:
    - Add `DATA_DIR=./data`
    - Add `LOG_LEVEL=INFO`
9.7. Verify Docker build and run:
    - `make build` succeeds
    - `docker run -p 8000:8000 --env-file .env robodata:latest` starts and serves
    - `curl http://localhost:8000/api/health` returns `{"status": "ok"}`
9.8. Update `README.md` deployment section with current Docker mount paths

### Done Criteria

- [ ] `make build` succeeds
- [ ] `docker run` with env vars starts server
- [ ] `GET /api/health` returns 200
- [ ] `data/` directory is gitignored except `.gitkeep` files
- [ ] `Procfile` matches deployment target
- [ ] `make quality && make test` passes

---

## Slice 10 — Verification & Demo Readiness

**Goal**: End-to-end integration tests, demo scripts, edge case hardening. The system is demonstrably complete.

**Scope**: `tests/` (all), `test_e2e.sh`, `docs/`.

### Tasks

10.1. Write end-to-end integration test in `tests/test_e2e.py`:
    - Full flow: judge login → upload → poll until complete → download → verify zip contents
    - Admin flow: admin login → list all jobs → cancel a job → delete a job
    - Session isolation: two judges, verify isolation
    - Restart recovery: upload, kill mid-pipeline, restart, verify failed + queued behavior
10.2. Implement all stub test files:
    - `tests/test_auth.py` → real tests (from Slice 2)
    - `tests/test_pipeline.py` → real tests per pipeline stage
    - `tests/test_routes.py` → endpoint integration tests
    - `tests/test_orchestrator.py` → stage sequencing, callback behavior
10.3. Add `tests/conftest.py` with shared fixtures:
    - `tmp_data_dir` → temporary data directory
    - `job_store` → configured `JobStoreFs` with temp dir
    - `queue` → configured `QueueFs` with temp dir
    - `test_client` → FastAPI `TestClient` with test app
    - `judge_token` / `admin_token` → pre-generated JWTs for test sessions
    - `sample_video` → small synthetic video for pipeline tests
10.4. Update `Makefile`:
    - Add `make test-e2e` target for end-to-end tests
    - Add `make test-unit` target for fast unit tests
    - Add `make clean-data` target to wipe `data/` for fresh start
10.5. Create `test_e2e.sh` script (or update existing):
    - Start server with test env
    - Run curl-based smoke tests: login, upload, poll, download
    - Check exit codes
10.6. Edge case sweep:
    - Upload with no file → 422
    - Upload with zero-byte file → 400
    - Download non-existent job → 404
    - Download non-completed job → 400
    - Delete running job → 409 (or allowed — decide and document)
    - Path traversal attempt in download → 404/400
    - Upload with very long filename → handled gracefully
    - Concurrent uploads from same judge → only first succeeds
10.7. Performance smoke test:
    - Upload a 1-second video, time the full pipeline → verify < 60s (on dev machine)
10.8. Final documentation sweep:
    - Verify `docs/architecture.md` is current
    - Verify `docs/specs/mvp-trd.md` all items are marked complete or tracked
    - Update `README.md` API endpoint table for new endpoints (cancel, health)
    - Update `README.md` environment variables table

### Done Criteria

- [ ] `tests/test_e2e.py` passes — full judge + admin flow
- [ ] `tests/test_isolation.py` passes — 11 tests
- [ ] `tests/test_auth.py` passes — 8 tests
- [ ] `tests/test_job_store.py` passes — 10 tests
- [ ] `tests/test_queue.py` passes — 5 tests
- [ ] `tests/test_worker.py` passes — 8 tests (5 base + 3 restart)
- [ ] `tests/test_orchestrator.py` passes — at least 5 stage tests
- [ ] `tests/test_pipeline.py` passes — real tests per stage
- [ ] `tests/test_routes.py` passes — all endpoint integration tests
- [ ] `make test-e2e` passes
- [ ] `test_e2e.sh` exits 0
- [ ] All edge cases return appropriate HTTP status codes
- [ ] `make quality && make test` passes (all tests green)
- [ ] Manual demo: login as judge, upload video, watch progress, download zip, extract, verify LeRobot format

---

## Slice Dependency Graph

```
Slice 0 (Foundation)
  │
  ▼
Slice 1 (Domain Package)
  │
  ├──────────────────┐
  ▼                  ▼
Slice 2 (Auth)   Slice 3 (Job Store)
  │                  │
  └────────┬─────────┘
           ▼
      Slice 4 (Queue & Worker)
           │
           ▼
      Slice 5 (Recovery)
           │
           ▼
      Slice 6 (Judge APIs)
           │
           ▼
      Slice 7 (Admin APIs)
           │
           ▼
      Slice 8 (Frontend)
           │
           ▼
      Slice 9 (Deploy)
           │
           ▼
      Slice 10 (Verification)
```

Slices 2 and 3 can be done in parallel. All others are sequential.

## Estimated Effort

| Slice | Est. Hours | Critical Path? |
|---|---|---|
| 0 — Foundation | 1–2 | Yes |
| 1 — Domain | 2–4 | Yes |
| 2 — Auth | 3–5 | No (parallel with 3) |
| 3 — Job Store | 4–6 | No (parallel with 2) |
| 4 — Queue/Worker | 4–6 | Yes |
| 5 — Recovery | 1–2 | Yes |
| 6 — Judge APIs | 3–4 | Yes |
| 7 — Admin APIs | 1–2 | Yes |
| 8 — Frontend | 2–4 | Yes |
| 9 — Deploy | 2–3 | Yes |
| 10 — Verification | 4–6 | Yes |
| **Total** | **27–44** | |

---

## Notes

- After each slice, create a git commit with the slice number and summary. This creates a revertible checkpoint.
- Do NOT proceed to the next slice until the current slice's done criteria are all checked off.
- If a test reveals a design issue in a prior slice, go back and fix it before continuing forward.
- The `pipeline/orchestrator.py` already exists and is close to correct — the worker will wrap it, not rewrite it.
- All pipeline stages (ingest, preprocess, pose, retarget, evaluate, package, finalize) already have implementations in `pipeline/` — these are wrapped, not rewritten.
- `ingest.py` and `finalize.py` do not yet exist as separate modules. They may be created or folded into the existing stage modules depending on scope.
