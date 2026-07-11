# Test-Driven Development — RoboData

Applies to **all logic modules, services, validators, and algorithms**. Enforces Red-Green-Refactor with Clean-Room Context Isolation where subagents are available.

---

## Red-Green-Refactor Protocol

### Phase 1: RED — Write the test, confirm it fails
1. Define interface contracts (signatures, enums, type stubs).
2. Write the test file. Cover: happy path, boundary conditions, error states.
3. Run the test command. Confirm it fails (missing implementation or assertion failure).

### Phase 2: GREEN — Minimum implementation
1. Write the minimum code to make tests pass.
2. Do not add speculative features. KISS.
3. Run tests; confirm all pass.

### Phase 3: REFACTOR — Clean without regression
1. Clean up names, nesting, structure per `code-quality.md`.
2. Re-run tests; confirm no regressions.

### Clean-Room Isolation (subagent available)
When delegating to a subagent implementer:
- **Test Writer** (main agent): defines interfaces, writes tests, confirms RED.
- **Blind Implementer** (subagent): implements production code without reading test files. Diagnoses failures from test-runner output only.
- **Verification Gate** (main agent): review subagent implementation for hardcoded test values; audit transcript for test-file access.

---

## RoboData Minimum Test Coverage

These test categories are the non-negotiable coverage baseline. Every vertical slice must grow this suite.

### Auth Tests (`tests/test_auth.py`)

| Test | What It Verifies |
|---|---|
| `test_verify_judge_password_constant_time` | `hmac.compare_digest` used; timing-invariant |
| `test_verify_wrong_password_rejects` | Incorrect password returns 401 |
| `test_create_access_token_judge` | JWT contains `role=judge`, `judge_session_id` is UUID |
| `test_create_access_token_admin` | JWT contains `role=admin`, global visibility |
| `test_judge_session_id_is_stable` | Same password across logins yields same `judge_session_id` |
| `test_expired_token_rejected` | Expired JWT returns 401 |
| `test_malformed_token_rejected` | Tampered JWT returns 401 |
| `test_missing_auth_header_rejected` | No `Authorization` header returns 401 |

### Job Isolation Tests (`tests/test_isolation.py`)

| Test | What It Verifies |
|---|---|
| `test_judge_cannot_see_other_judge_jobs` | Judge A's list excludes Judge B's jobs |
| `test_judge_cannot_cancel_other_judge_job` | Judge A cancel on Judge B's job returns 404 |
| `test_judge_cannot_delete_other_judge_job` | Judge A delete on Judge B's job returns 404 |
| `test_judge_cannot_download_other_judge_dataset` | Judge A download on Judge B's job returns 404 |
| `test_admin_can_see_all_jobs` | Admin list includes jobs from all judges |
| `test_admin_can_cancel_any_job` | Admin cancel succeeds on any judge's job |
| `test_isolation_enforced_in_store_not_routes` | Direct `JobStore` call with wrong `judge_session_id` returns empty/raises; API route does not layer a second filter |

### JobStore Tests (`tests/test_job_store.py`)

| Test | What It Verifies |
|---|---|
| `test_create_job_persists_to_disk` | `create_job` writes valid `job.json` |
| `test_create_job_appends_event` | `create_job` appends to `events.jsonl` |
| `test_get_job_returns_domain_model` | `get_job` returns `Job` with correct fields |
| `test_get_job_not_found_raises` | Nonexistent id raises `JobNotFoundError` |
| `test_list_jobs_scoped_to_session` | `list_jobs(session_id=X)` returns only X's jobs |
| `test_update_status_transitions` | `queued → running → completed` flow persisted |
| `test_update_status_writes_event` | Status change appends event with timestamp |
| `test_cannot_transition_from_terminal` | `completed → running` raises `InvalidTransitionError` |
| `test_delete_job_removes_directory` | Delete removes `data/jobs/<id>/` tree |
| `test_session_limit_enforced` | Judge with `queued` job cannot create second job |

### Queue Tests (`tests/test_worker.py`)

| Test | What It Verifies |
|---|---|
| `test_enqueue_persists_to_disk` | `enqueue(job_id)` writes to queue directory |
| `test_dequeue_fifo_order` | Jobs dequeued in order they were enqueued |
| `test_empty_queue_returns_none` | Dequeue on empty queue returns `None` |
| `test_restart_fails_running_jobs` | Worker restart transitions `running` job to `failed` with reason `worker_restarted` |
| `test_restart_preserves_queued` | `queued` jobs remain `queued` after restart |
| `test_max_one_active_per_session` | Judge with `running` job cannot enqueue another |

### Pipeline Stage Tests (one per stage)

| Test File | Minimum Tests |
|---|---|
| `test_ingest.py` | Valid video copied; invalid extension rejected; oversize rejected; magic bytes validated |
| `test_preprocess.py` | Frames extracted at target FPS; output directory created; corrupt video handled |
| `test_pose.py` | Landmarks extracted; detection rate within bounds; empty frames handled |
| `test_retarget.py` | Joint trajectory correct shape [T,7]; EE trajectory correct shape [T,3]; NaN inputs handled |
| `test_evaluate.py` | Green/yellow/red grades correct; each metric gate tested; empty trajectory fails |
| `test_package.py` | Parquet file created; meta.json + stats.json present; empty trajectory handled |
| `test_finalize.py` | Cleanup removes work dir; summary written; status transitions to completed |

### Orchestrator Tests (`tests/test_orchestrator.py`)

| Test | What It Verifies |
|---|---|
| `test_full_pipeline_success` | All stages run in order; job ends as `completed` |
| `test_stage_failure_propagates` | Failing stage transitions job to `failed` with stage info |
| `test_stage_skipped_on_cancelled` | Cancelled job does not run remaining stages |
| `test_events_written_at_each_transition` | `events.jsonl` has entry for every stage entry/exit |

---

## Test Infrastructure

- Pytest with `asyncio_mode = "auto"` (configured in `pyproject.toml`).
- `tests/conftest.py` provides shared fixtures: `tmp_jobs_dir` (temp `data/jobs/`), `test_client` (FastAPI `TestClient`), `judge_token` / `admin_token` (pre-built JWTs), `mock_job_store` (in-memory implementation for fast tests).
- Use `pytest -v` to run all; `pytest tests/test_auth.py -v` for specific files.

---

## Exceptions

TDD may be bypassed for:
- Pure CSS / design changes
- Static configuration or JSON file edits
- Markdown documentation tasks
- Typo or rename-only operations (no logic change)
