# Quality Gates

> **Status**: draft
> **Owner**: RoboData team
> **Created**: 2026-07-11

## Command Matrix

| Command | Scope | Blocks Merge? | Timeout | Run By |
|---|---|---|---|---|
| `make fix` | Auto-format and fix lint violations | No (advisory) | — | Developer pre-commit, optionally automated |
| `make lint` | Ruff check + Ruff format check | **Yes** | 30s | CI on every push / `make quality` |
| `make quality` | `make lint` + Python compileall | **Yes** | 60s | CI (superset of lint), manual pre-merge |
| `make test` | Full pytest suite (`asyncio_mode = "auto"`) | **Yes** | 120s | CI on every push, manual pre-merge |
| `make dev` | Start dev server (hot reload) | No | manual | Local development only |
| `make dev-up` | Verify no external infra dependencies needed | No | — | Pre-flight check before local dev |
| `make build` | Docker image build | Yes (for deploy) | 300s | CI on tag/push to main, manual pre-deploy |

### Gate Chain (Full Pass)

```
make fix → make quality → make test → merge
```

The minimum passing bar for any PR is `make quality && make test`. `make fix` is advisory but should be run first to avoid style-only lint failures.

## Thresholds

### Lint (Ruff)

| Rule | Threshold | Notes |
|---|---|---|
| E, W (pycodestyle) | 0 violations | Enforced by `ruff check .` exit code |
| F (pyflakes) | 0 violations | Unused imports, undefined names |
| I (isort) | 0 violations | Import ordering must match |
| N (pep8-naming) | 0 violations | Naming conventions enforced |
| UP (pyupgrade) | 0 violations | Use modern Python syntax |
| B (flake8-bugbear) | 0 violations | Common bug patterns |
| A (flake8-builtins) | 0 violations | No shadowing builtins |
| SIM (flake8-simplify) | 0 violations | Simplify expressions |

All rules are zero-tolerance. `make lint` must exit 0.

### Test Coverage

| Target | Threshold |
|---|---|
| Auth tests | All JWT create/validate/expire/role paths covered |
| Routes tests | All 6 endpoints (login, verify, upload, list, get, delete, download) covered |
| Job store tests | CRUD + isolation + events append |
| Queue tests | Enqueue/dequeue FIFO order, per-session limit |
| Worker tests | Dispatch, failure transition, restart behavior |
| Isolation tests | Judge-to-judge data separation, admin visibility |
| Pipeline stage tests | Each stage tested independently with synthetic inputs |

No explicit line-coverage percentage target for MVP. Instead: every public method on every interface (`JobStore`, `Queue`, pipeline stages, auth deps) must have at least one passing test.

### Performance

| Metric | Threshold |
|---|---|
| API response (auth, list, get, delete) | p95 < 200ms |
| API response (upload) | p95 < 2s (excludes pipeline execution) |
| API response (download) | p95 < 5s for < 100MB dataset |
| Pipeline stage timeout | 300s per stage (hard kill after 5 min) |
| Full pipeline timeout | 600s (10 min) per job |

## Timeouts

| Operation | Timeout | Action on Exceed |
|---|---|---|
| Ruff lint | 30s | Fail CI step |
| Python compileall | 30s | Fail CI step |
| Full test suite | 120s | Fail CI step; investigate slow tests |
| Docker build | 300s | Fail deploy; check layer caching |
| Pipeline stage | 300s | Transition job to `failed` with reason `stage_timeout` |
| Full pipeline | 600s | Transition job to `failed` with reason `pipeline_timeout` |
| Worker idle loop | None (blocking dequeue) | Worker waits indefinitely for next job |
| Polling (frontend) | 2s interval, 3 retries | Stop polling, show toast |

## Escalation Path

```
Level 1: make fix (self-service)
  ↓ failure
Level 2: Read lint output, fix manually, re-run make quality
  ↓ persistent failure
Level 3: Open issue in repo with lint/test output attached
  ↓ pattern of failures
Level 4: Team discussion — is the rule too strict? Amend pyproject.toml [tool.ruff.lint]
```

Hotfix bypass: in case of a critical production issue, a maintainer may merge a PR that fails `make test` IF the failing tests are:
1. Unrelated to the hotfix (documented in PR description)
2. Pre-existing failures (not introduced by the hotfix)
3. Tracked in an open issue

The bypass must be logged in the PR and a follow-up issue created to fix the tests.

## Rollout Policy

| Environment | Gate Enforcement | Notes |
|---|---|---|
| Local dev | Advisory only | `make fix` before commit; `make quality && make test` before push |
| PR CI | Strict (blocking) | GitHub Actions / CI runs `make quality && make test` |
| Merge to main | Strict (blocking) | Same as PR CI — no bypass without documented exception |
| Deploy (Docker) | Strict | `make build` must succeed; image must boot and pass health check |
| Render auto-deploy | Strict | `Procfile` web process must start and bind `$PORT` |

### New Rule Introduction

1. Propose the rule in `pyproject.toml` `[tool.ruff.lint.select]`
2. Run `make lint` on the full codebase
3. Fix all new violations (or configure exceptions)
4. PR the rule change + fixes together
5. After merge, the rule is immediately enforced

### Test Suite Growth

- Every new `domain/` or `pipeline/` module must ship with a corresponding `tests/test_<module>.py`
- Every new route must have at least one integration test
- Test files that are stub-only (`# TODO: ...`) block merge — they must contain real tests

## Verification Commands

```bash
# Full quality gate (run before every push)
make quality && make test

# Check only lint
make lint

# Auto-fix formatting and import ordering
make fix

# Run a specific test file
uv run pytest tests/test_auth.py -v

# Run a specific test
uv run pytest tests/test_auth.py::test_login_success -v

# Run with verbose output and stop on first failure
uv run pytest -x -v
```
