# Code Quality Baseline — RoboData

This rule applies to **every file you write or modify**. No user request is required to activate it.

---

## Pre-Implementation Design Checkpoint

Before writing any new function, class, or module, answer all seven questions:

1. **Single responsibility** — What is the one thing this unit does? If you need "and", split it.
2. **Minimal interface** — What is the smallest surface callers need? Expose only that.
3. **Dependency direction** — Does this unit depend only on things in its allowed import set? (`domain/` is shared; `backend/` and `pipeline/` must not import each other.)
4. **Human traceability** — Can a reader follow the logic from names and structure alone?
5. **Deep Module encapsulation** — Does this module hide internal complexity behind a clean interface? If it is a shallow pass-through, merge or redesign.
6. **Interface-First specification** — Are the type signatures, enums, and contracts fully defined before implementation?
7. **Ambiguity policy** — For edge cases with multiple reasonable behaviors, what contract chooses? If none does, stop and clarify.

---

## Structural Limits

| Constraint | Value |
|---|---|
| Max nesting depth | 3 levels — extract deeper logic to named helpers |
| Max function length | 50 lines of logic (excludes declarations, annotations, blank lines) |
| Max parameters | 4 — use a typed config object beyond this |
| Guard clauses | Required — return/raise early; no `else` after `return` or `raise` |

---

## RoboData Layering Rules

### Import Boundaries (Hard Stops)

| Module | Allowed Imports | Forbidden Imports |
|---|---|---|
| `backend/` | `domain/`, `pipeline/` (orchestrator only, never individual stage modules) | — |
| `pipeline/` | `domain/` | `backend/` |
| `domain/` | standard library, pydantic | `backend/`, `pipeline/` |
| `frontend/` | static assets only | — |

### Architecture Enforcement

- **No business logic in routes.** `backend/routes.py` must be thin: extract parameters, delegate to domain/job-store and pipeline/orchestrator, return responses. Stage implementations, progress callbacks, retry logic, and job state machines live in `pipeline/`.
- **No pipeline stage logic in `backend/`.** Individual stages (`ingest`, `preprocess`, `pose`, `retarget`, `evaluate`, `package`, `finalize`) are owned by `pipeline/`.
- **Job state mutations only through `JobStore`.** No direct mutation of `job.json`, in-memory dicts, or filesystem artifacts bypassing the `domain/job_store.py` interface.
- **No cross-import between `backend/` and `pipeline/`.** If shared types are needed, they belong in `domain/`.

---

## Prohibited Patterns (Hard Stop)

| Prohibition | RoboData-Specific Context |
|---|---|
| Magic literal statuses | Never write `"pending"`, `"running"`, `"completed"`, `"failed"`, `"cancelled"`. Use `JobStatus` enum from `domain/models.py`. |
| Magic literal stages | Never write `"preprocessing"`, `"pose_estimation"`, `"retargeting"`, `"evaluating"`, `"packaging"`. Use `PipelineStage` enum from `domain/models.py`. |
| Magic literal roles | Never write `"admin"`, `"judge"` as raw strings. Use canonical role constants. |
| Silent except blocks | Every `except` must re-raise, log with context, wrap in a domain exception, or transition state through `JobStore`. `except: pass` or `except Exception: pass` is prohibited. |
| Bypassing JobStore | No module may mutate job dicts, in-memory caches, or filesystem job state except through the `JobStore` abstract interface. |
| Business logic in routes | Route handlers delegate; they do not implement stages, progress, or state machines. |
| Path string manipulation | Use `pathlib.Path`. Never `os.path` or raw string concatenation for filesystem paths. |
| Non-constant-time secret comparison | Passwords must use `hmac.compare_digest`. `==` on passwords or tokens is prohibited. |
| Judge isolation in the API layer | Judge query scoping is enforced in `domain/job_store_fs.py`. Never add a second filter in routes. |
| Silent semantic fallbacks | Returning `[]`, `null`, or partial success on ambiguous/failed cases without contract approval is prohibited. |
| Functions doing multiple things | Split at "and". One function = one responsibility. |
| Logic copied more than twice | Extract to a named shared function. |
| Global mutable state | Must justify with `// WHY: global — [justification]`. |
| Extend-by-parameter | Adding parameters to grow behavior — use composition instead. |

---

## Security Quality Gates

These apply specifically to RoboData auth and data handling:

1. **Password comparison**: `hmac.compare_digest(stored, provided)` — no other operator.
2. **JWT secret**: minimum 32 random bytes, generated via `openssl rand -hex 32`.
3. **`judge_session_id`**: opaque UUID, not derivable from user input. Generated server-side on first judge login.
4. **Judge isolation**: enforced in `domain/job_store_fs.py` via `judge_session_id` filter on all scoped queries. API layer must not add a redundant filter.
5. **Path traversal prevention**: download/output paths resolved relative to `data/jobs/<id>/output/`, not from user-supplied components.
6. **Upload validation**: extension whitelist (`.mp4`, `.mov`, `.avi`, `.webm`) + size limit (configurable, default 100 MB) + magic-byte validation before pipeline execution.
7. **No secret leakage to subprocess**: pipeline execution in subprocess/thread pools must not inherit environment variables containing secrets.
8. **`.env` is gitignored**. Never commit secrets. Exposed secrets trigger immediate rotation.

---

## Logging & Error Handling

- Use `logging.getLogger(__name__)` in every module. No `print()` for operational output.
- Every pipeline stage failure must log: job_id, stage name, exception type, message, traceback.
- Worker restart must log: previous state of running jobs, transition reason (`worker_restarted`).
- API errors return structured responses — never raw tracebacks to the client.
- `JobStore` write failures must raise domain exceptions (`JobStoreError`), not leak filesystem details.

---

## Technical Debt Markers

Use `// TODO(debt):` for acceptable deferrals. Must include: what is incomplete, why deferred, what triggers cleanup.

```
// TODO(debt): in-memory `_jobs` dict bypasses JobStore — deferred until domain/ migration — cleanup when domain/job_store.py is implemented
```

**Acceptable debt**: incomplete abstraction, deferred optimization, provisional business rule.

**NOT acceptable as debt**: silent error swallowing, god objects, security shortcuts, logic copied more than twice without extraction, `PENDING`/`PREPROCESSING`/`POSE_ESTIMATION` enum values (these are drift, not debt — migrate immediately).

---

## Refactoring Signal Markers

Mark smells you cannot fix in the current task:

```
// REFACTOR-SIGNAL: [pattern] — [description]
```

| Pattern | When to Mark |
|---|---|
| `feature-envy` | Function uses more data from another module than its own |
| `god-object` | Class/module owns more than one domain concept |
| `shotgun-surgery` | One logical change requires edits in 4+ files |
| `primitive-obsession` | Raw `str` where `JobStatus` or `PipelineStage` enum should be used |
| `implicit-coupling` | Two modules share undocumented state or rely on call ordering |
| `layer-violation` | `backend/` imports a pipeline stage directly, or `pipeline/` imports `backend/` |

---

## Naming Conventions

| Artifact | Convention | Example |
|---|---|---|
| Functions | Verb phrase | `enqueue_job`, `verify_password` |
| Variables | Noun phrase | `job_count`, `session_id` |
| Constants | SCREAMING_SNAKE_CASE | `MAX_VIDEO_SIZE_BYTES`, `DEFAULT_FPS` |
| Booleans | `is_` / `has_` / `can_` prefix | `is_valid`, `has_failed` |
| Files | lower_snake_case | `job_store.py`, `job_store_fs.py` |

**No single-letter names** except loop counters. No abbreviations unless standard in the Python ecosystem (`id`, `url`, `ctx`).
