# AGENTS.md — RoboData

RoboData is a regeneration pipeline that converts 30-second phone videos of human manipulation tasks into robot-ready training datasets in LeRobot format. Users are lab researchers and robot operators who upload phone-captured video, receive automated pose extraction, IK retargeting, quality evaluation, and packaged dataset output through a web UI. The system serves two roles: anonymous judges who submit and track individual jobs, and administrators who have global visibility across all jobs.

---

## Source-of-Truth Hierarchy

When resolving any ambiguity about intended behavior, architecture boundaries, or domain rules, consult sources in this priority order. Later sources carry presumptive weight only where earlier sources are silent.

1. **`docs/specs/mvp-trd.md`** — canonical MVP technical requirements document. Defines scope, acceptance criteria, stage contracts, job lifecycle, and auth model. All implementation must be traceable to a TRD requirement.
2. **`docs/architecture.md`** — system architecture document. Defines modular boundary between API, worker/pipeline, shared domain, and frontend; data ownership; persistence strategy; deployment model.
3. **`docs/specs/task-plan.md`** — sequenced implementation plan with vertical slices. Defines work ordering, dependency chains, and testing gates.
4. **Existing code** — the current state of `backend/`, `pipeline/`, `frontend/`, and `tests/`. Code that contradicts higher-priority sources is drift to be corrected, not authority to be preserved.
5. **`docs/*.md` research notes** — exploratory documents (`docs/synthesis.md`, `docs/mvp-pipeline.md`, `docs/quality-evaluation-strategy.md`, `docs/capture-tech.md`, `docs/current-scene.md`, `docs/regeneration-pipeline.md`). Informative but non-normative.

---

## Project Rules

### Architecture

- RoboData is a **modular monolith**: one FastAPI deployable containing the API layer, the worker/pipeline domain, a shared domain package, and a static frontend served from the same process.
- The package layout is four top-level modules: `backend/` (API, auth, server), `pipeline/` (stage logic, orchestrator, worker), `domain/` (shared Pydantic models, enums, job-store interface, job-store implementations), `frontend/` (static HTML/CSS/JS).
- The `domain/` package owns all canonical enums (`JobStatus`, `PipelineStage`), shared models (`Job`, `JobEvent`), the `JobStore` abstract interface, and any value objects shared between `backend/` and `pipeline/`. Neither `backend/` nor `pipeline/` may import from each other; both may import from `domain/`.
- **Pipeline stage logic lives in `pipeline/`, never in `backend/routes.py`.** The routes module calls the pipeline orchestrator and the job store; it does not contain stage implementations, progress callbacks, or retry logic.
- **Job state mutations go through the job store only.** No module may directly mutate job dicts, in-memory caches, or filesystem representations bypassing the `JobStore` interface.

### Auth & Isolation

- **Two separate auth channels**: (1) a shared judge password that yields a JWT with `role=judge` and an anonymous `judge_session_id`; (2) a separate admin password that yields a JWT with `role=admin` and global visibility.
- Judge sessions can only see, cancel, and delete their own jobs. Judge queries are scoped to `judge_session_id` at the job-store level.
- Admin sessions see all jobs and may perform any operation.
- **Constant-time password comparison only.** Use `hmac.compare_digest`. Never use `==` for password or token comparison.

### Job Lifecycle

- Canonical job states: **`queued`**, **`running`**, **`completed`**, **`failed`**, **`cancelled`**. Any existing code using `PENDING` or similar is drift to be corrected.
- Canonical stages: **`ingest`**, **`preprocess`**, **`pose`**, **`retarget`**, **`evaluate`**, **`package`**, **`finalize`**.
- **Filesystem durable persistence, no database.** Every job lives under `data/jobs/<job_id>/` containing:
  - `job.json` — canonical job state (id, status, stage, progress, session_id, metadata, timestamps)
  - `events.jsonl` — append-only event log (state transitions, stage entries/exits, errors)
  - `upload/` — original uploaded video file
  - `work/` — intermediate pipeline artifacts (frames, pose data, trajectories)
  - `output/` — final packaged dataset
  - `logs/` — per-stage execution logs

### Worker & Queue

- One global worker process, persisted FIFO queue on disk under `data/queue/`.
- Max one active job per `judge_session_id`. A judge cannot submit a new job while they have a `queued` or `running` job.
- **Restart policy**: on worker restart, any `running` job transitions to `failed` with reason `worker_restarted`. `queued` jobs remain `queued`. No automatic resume of failed jobs.

### Data & Repo Hygiene

- **No raw video or generated job artifacts committed to git.** `uploads/`, `outputs/`, and `data/` are gitignored. `.gitkeep` sentinels in key directories are acceptable.
- `.env`, `.pem`, `.key`, `credentials.json`, `auth.json` are gitignored and must never be committed.

### Server Process

- **The server process must never be started or stopped by an agent without explicit user instruction.** Agents may inspect server code, suggest configuration, and write test clients, but must not call `uvicorn`, `docker run`, or process-management commands autonomously.

### Deployment

- Default deployment is a single Docker container with a persistent volume mounted at `/app/data`.
- Render Starter is the current default platform. The `Procfile` defines the web process.

---

## Tooling Rules

- **Python via `uv`-managed environment only.** All `python`, `pip`, `pytest`, `ruff`, `uvicorn` invocations must use `uv run`, which ensures the project's `.venv` is correct and synced.
- **`pyproject.toml` is the dependency source of truth.** Dependencies may only be added via edits to `pyproject.toml` followed by `uv sync`. For dev dependencies use `uv sync --extra dev`. The Dockerfile must be kept in sync.
- **Frontend dependencies (future):** The frontend is currently dependency-free static assets. If package-managed dependencies are introduced later, `bun` is the preferred package manager.
- **No ad hoc global installs** unless the user explicitly approves (`pip install --global`, `npm install -g`, `bun add -g`, `brew install` that affects the project toolchain).
- Ruff is the linter and formatter (configured in `pyproject.toml`). No separate `ruff.toml` or `.flake8`.
- Pytest is the test runner with `asyncio_mode = "auto"`.

---

## Agent Operating Rules

- **Default implementation skill: `code-craft`.** Load for any non-trivial code write, feature addition, or refactor.
- **Load `codebase-exploration` before major refactors** spanning three or more files.
- **Load `reviewer` for auth, isolation, or security-sensitive changes.** This includes any modification to `backend/auth.py`, permission checks, job visibility scoping, or token handling.
- **Run `make lint && make test` before calling work done.** If a `Makefile` does not yet exist, the equivalent is `uv run ruff check . && uv run pytest -v`. Create the `Makefile` if it is missing and you are the first to need it.

---

## Code Quality Rules

- **Nesting depth ≤ 3.** Extract deeper logic into well-named helper functions.
- **Function length ≤ 50 lines** unless a documented justification exists (e.g., a linear pipeline stage dispatcher that reads better as one function).
- **Guard clauses preferred.** Early returns for error/edge cases; avoid `else` branches after `return` or `raise`.
- **No magic literals** for statuses, stages, or roles. Use the canonical enums from `domain/models.py`. String literals like `"pending"`, `"running"`, `"judge"`, `"admin"` are prohibited outside enum definitions and their immediate tests.
- **No silent except blocks.** Every `except` must either re-raise, log with context, wrap in a domain exception, or transition state through the job store. Bare `except:` or `except Exception: pass` is prohibited.
- Use type hints on all public function signatures. Return types must be explicit.
- Prefer `pathlib.Path` over `os.path` and string path manipulation.

---

## Source Code Organization

### Target Layout

```
aabw-2026/
├── AGENTS.md                          # This file
├── GLOSSARY.md                        # Ubiquitous language definitions
├── Makefile                           # lint, test, format, run targets
├── pyproject.toml                     # Project metadata, dependencies, tool config
├── Dockerfile                         # Single-container production image
├── Procfile                           # Render platform process definition
├── .env.example                       # Template for required environment variables
├── .gitignore
├── README.md
│
├── backend/                           # FastAPI application layer
│   ├── __init__.py
│   ├── server.py                      # App factory, CORS, static mounts, lifespan
│   ├── config.py                      # Pydantic Settings from env vars
│   ├── routes.py                      # HTTP endpoints — thin; delegates to domain/job_store and pipeline/orchestrator
│   ├── auth.py                        # Password verification, JWT create/validate, FastAPI deps
│   └── dependencies.py                # FastAPI dependency injection (get_job_store, get_current_judge, get_current_admin)
│
├── domain/                            # Shared domain package (target — may not exist yet)
│   ├── __init__.py
│   ├── models.py                      # JobStatus enum, PipelineStage enum, Job, JobEvent, session models
│   ├── job_store.py                   # AbstractJobStore interface (ABC)
│   ├── job_store_fs.py                # Filesystem implementation of AbstractJobStore
│   ├── queue.py                       # FIFO queue abstraction and filesystem implementation
│   └── exceptions.py                  # Domain exceptions (JobNotFoundError, SessionLimitError, etc.)
│
├── pipeline/                          # Pipeline domain logic
│   ├── __init__.py
│   ├── orchestrator.py                # Stage sequencer — runs stages, manages callbacks
│   ├── worker.py                      # Background worker: dequeues jobs, invokes orchestrator, writes events
│   ├── ingest.py                      # Video validation, copy to data/jobs/<id>/upload/
│   ├── preprocess.py                  # Frame extraction, resolution normalization
│   ├── pose.py                        # MediaPipe Pose landmark extraction
│   ├── retarget.py                    # pinocchio IK retargeting
│   ├── evaluate.py                    # 5-gate quality evaluation
│   ├── package.py                     # LeRobot format export
│   └── finalize.py                    # Cleanup, summary generation, final state transition
│
├── frontend/                          # Static frontend served by FastAPI
│   ├── index.html
│   ├── app.js
│   └── style.css
│
├── tests/                             # Test suite (mirrors src layout)
│   ├── __init__.py
│   ├── conftest.py                    # Shared fixtures (temp job dirs, test client, mock store)
│   ├── test_auth.py                   # Password verification, JWT lifecycle, role claims
│   ├── test_routes.py                 # HTTP endpoint integration tests
│   ├── test_job_store.py              # AbstractJobStore contract tests against fs implementation
│   ├── test_worker.py                 # FIFO queue, dispatch, restart behavior
│   ├── test_orchestrator.py           # Stage sequencing, failure propagation
│   ├── test_preprocess.py
│   ├── test_pose.py
│   ├── test_retarget.py
│   ├── test_evaluate.py
│   ├── test_package.py
│   └── test_isolation.py             # Judge-to-judge isolation, admin visibility
│
├── data/                              # Runtime data (gitignored, mounted as volume in Docker)
│   ├── jobs/                          # Per-job directories: <job_id>/job.json + events.jsonl + upload/work/output/logs
│   └── queue/                         # Persisted FIFO queue state
│
├── docs/
│   ├── architecture.md                # System architecture document
│   ├── specs/                         # Specification documents (normative)
│   │   ├── mvp-trd.md                 # MVP Technical Requirements Document
│   │   └── task-plan.md               # Implementation plan with vertical slices
│   ├── synthesis.md                   # Research synthesis
│   ├── mvp-pipeline.md                # Pipeline design notes
│   ├── quality-evaluation-strategy.md
│   ├── capture-tech.md
│   ├── current-scene.md
│   └── regeneration-pipeline.md
│
└── uploads/                           # Legacy upload directory (gitignored, deprecated in favor of data/jobs/<id>/upload/)
```

### Import Rules

| Module | May Import From | Must NOT Import From |
|--------|----------------|---------------------|
| `backend/` | `domain/`, `pipeline/` (orchestrator only, not individual stages) | — |
| `pipeline/` | `domain/` | `backend/` |
| `domain/` | standard library, pydantic | `backend/`, `pipeline/` |
| `frontend/` | (static assets only) | — |

---

## Verification Commands

If a `Makefile` exists, use its targets. Otherwise, the canonical commands are:

```bash
# Lint and format check
uv run ruff check .

# Run all tests
uv run pytest -v

# Run a specific test file
uv run pytest tests/test_auth.py -v

# Type checking (when mypy/pyright is added)
# uv run mypy backend/ domain/ pipeline/
```

After any code change, run lint and tests before declaring work done.

---

## Security Rules

- Passwords are compared with `hmac.compare_digest` only. No other comparison operator is acceptable for secrets.
- JWT secrets (`JWT_SECRET_KEY`) must be at least 32 random bytes. Use `openssl rand -hex 32` to generate.
- `.env` and `.env.*` are in `.gitignore`. Never commit secrets. If secrets are accidentally exposed in conversation, instruct the user to rotate them immediately.
- Judge isolation is enforced at the data-access layer: `JobStore` queries for judge-scoped operations include a `judge_session_id` filter. The API layer must never implement its own filtering that could bypass the store.
- `judge_session_id` is an opaque, randomly generated string (UUID). It is not derivable from or correlatable with any user-provided data.
- A judge session can never read, cancel, or delete another judge's job. This is enforced in `domain/job_store_fs.py`, not in `backend/routes.py`.
- File uploads are validated for extension (whitelist: `.mp4`, `.mov`, `.avi`, `.webm`) and size (configurable, default 100 MB). File content magic bytes are validated before pipeline execution.
- Path traversal in download endpoints is prevented by resolving output paths relative to the job's canonical `data/jobs/<id>/output/` directory, not from user-supplied path components.
- Pipeline execution in subprocesses or thread pools must not inherit or leak environment variables containing secrets.
