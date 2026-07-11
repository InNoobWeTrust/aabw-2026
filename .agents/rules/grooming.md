# Grooming & Design Concept Alignment — RoboData

Applies to **all planning, requirements, and high-ambiguity implementation tasks**. Reverse-interview the user before any plan or implementation begins.

---

## The Reverse Interview

When activating this rule (plan request, ambiguous requirements, or `/grill-me`):

1. **Stop & Probe** — analyze for gaps, implicit assumptions, boundary conditions.
2. **Formulate 3–5 questions** — maximum 5, prioritized by architectural impact.
3. **Target RoboData's core dimensions** — see checklist below.
4. **Wait for answers** before drafting a plan, spec, or implementation.

### Standard Flow

Ask 3–5 questions from the checklist. Wait for the user's answers. Then proceed.

### Quick Tasks

Skip the interview for formatting, config values, typos, single-line changes. If one small ambiguity exists, ask inline without blocking.

### AFK / Automated Mode

Do not block waiting for input. Perform a **Self-Grooming Audit**:
```markdown
### Self-Grooming Audit (AFK)
- **Inferred Goal**: [what the task aims to achieve]
- **Codebase Constraints Identified**: [dependencies, existing helpers]
- **Assumptions Made**: [critical assumptions bypassing human review]
- **Perceived Risks & Mitigations**: [thread safety, backwards compat, etc.]
```

---

## RoboData Reverse Interview Checklist

Select 3–5 questions relevant to the task. Prioritize by architectural impact.

### Auth & Identity

1. **Does this change affect auth channels?** (judge shared-password channel vs admin separate-password channel vs both?) If adding a new endpoint, is it judge-scoped, admin-only, or unauthenticated?
2. **How are `judge_session_id` boundaries affected?** Does this feature create cross-session visibility that must be blocked at the `JobStore` layer?
3. **JWT payload shape:** are you adding claims? Must they be backward-compatible with existing tokens? Does the `sub` field change?

### Persistence & Job State

4. **Does this touch the `data/jobs/<id>/` layout?** Adding files under `work/`, `output/`, or `logs/`? Must the `JobStore` interface change?
5. **Can the change corrupt `job.json` if the process crashes mid-write?** Do you need atomic write-then-rename? Is `events.jsonl` append-order preserved?
6. **Is this a schema change to `Job` model?** Existing `job.json` files on disk must remain readable — is backward compat or migration needed?

### Queue & Worker

7. **Does this change the worker lifecycle?** Startup, shutdown, restart, crash recovery? Must the restart policy (`running → failed: worker_restarted`) be preserved?
8. **Session concurrency limit:** does this change the rule that a judge can have at most one `queued` or `running` job?
9. **FIFO ordering:** does this feature need priority queueing, or is strict FIFO sufficient?

### Pipeline & Stages

10. **Is this a new pipeline stage or a change to an existing stage contract?** What output artifact does the stage produce? What does the next stage consume?
11. **Resource cost:** does this stage use GPU, large memory, or network calls? What happens on timeout or OOM? Should it have a progress callback?
12. **Stage idempotency:** can this stage be re-run on the same inputs without side effects? Is resume-from-stage supported?

### Deployment & Operations

13. **Does this change what's mounted/persisted in Docker?** Is `data/` volume still the only persistent mount?
14. **Environment variables:** any new required env vars? Must they be added to `.env.example`, `backend/config.py`, and `Dockerfile`?
15. **Render platform compatibility:** does this break the single-process model (`Procfile: web`)?

### Frontend & UX

16. **What does the user see during this operation?** Loading state, progress bar, error state? Does the polling loop need a new field from `JobResponse`?
17. **Does this add a new UI route or page?** Static served from `frontend/` or a new SPA route?

### Security & Isolation

18. **Does this introduce a new data path that could leak across judge sessions?** Download, list, or streaming endpoints must scope to `judge_session_id`.
19. **Any new file paths derived from user input?** Must be resolved relative to canonical directories, not raw path components (path traversal).

---

## Edge Cases to Always Consider (RoboData Defaults)

Even if not explicitly asked, evaluate:
- What happens when `data/` disk is full?
- What happens when a job is cancelled mid-stage?
- What happens when the worker process crashes during a pipeline run?
- What happens when two judges share a password but have different `judge_session_id` values? (They should be isolated — same password does not mean same session.)
- What happens when `job.json` is malformed or missing?
