# Skill Compliance — RoboData

Loading a skill's `SKILL.md` is a binding commitment to execute its complete workflow. Complexity, length, and effort are not valid reasons to skip steps.

---

## Core Rules

- Read the full `SKILL.md` before beginning work.
- Identify every step marked mandatory, required, must, or hard stop.
- Execute every mandatory step in order.
- Produce the exact artifacts the skill specifies.
- Report blockers explicitly — never silently skip.
- Never announce "I'll use a simplified version" — this is a violation.

---

## RoboData Skill Routing

### `code-craft` — Default for Implementation

**Load for**: any non-trivial code write, feature addition, refactor, or restructuring touching 1+ files.

**Skip for**: formatting, config values, typos, rename-only with no logic changes.

**RoboData-specific triggers**:
- Adding/changing any `domain/`, `pipeline/`, or `backend/` module
- Introducing new enums, models, or store methods
- Refactoring existing drifted code (e.g., `PENDING` → `QUEUED`)
- Implementing any pipeline stage
- Adding FastAPI routes or dependencies

### `codebase-exploration` — Before Major Refactors

**Load for**: navigation spanning 3+ files, tracing feature flow, mapping architecture, understanding an unfamiliar module.

**Skip for**: files already known to the agent.

**RoboData-specific triggers**:
- Understanding `backend/routes.py` pipeline dispatch before refactoring it out to orchestrator
- Mapping the current import graph to verify no `backend/` ↔ `pipeline/` violations
- Finding all drift sites (`PENDING`, `PREPROCESSING`, `POSE_ESTIMATION`, etc.) across the codebase
- Auditing `data/jobs/` layout to verify compliance with target structure

### `reviewer` — Auth, Isolation, Security-Sensitive Changes

**Load for**: explicit reviews AND any modification to auth, permission, visibility, or token handling.

**Skip for**: pure implementation with no security surface.

**RoboData-specific triggers** (always load):
- Any change to `backend/auth.py` (password verification, JWT create/validate, FastAPI deps)
- Adding or modifying role-checking logic (`get_current_judge`, `get_current_admin`)
- Job visibility scoping changes (`judge_session_id` filtering)
- Token claim changes (adding/removing JWT payload fields)
- New protected routes (which role can access?)
- Download/export endpoints (path traversal risk)
- File upload handling (extension/size validation, magic bytes)
- Any code using `hmac.compare_digest` or password comparison

### `requirements-driven-dev` — Spec-Driven Planning

**Load for**: PRD, TRD, BDD specs, acceptance criteria, user stories, ambiguous feature plans.

**Skip for**: well-scoped implementation from existing plan or TRD.

**RoboData-specific triggers**:
- Creating or updating `docs/specs/mvp-trd.md`
- Defining new pipeline stage contracts
- Designing the `JobStore` interface or `Job` model schema
- Planning queue/worker behavior changes
- Adding new auth roles or auth channels

### `architecture-writer` — Architecture Documentation

**Load for**: generating or updating `docs/architecture.md`.

**Skip for**: minor code changes not affecting architecture.

**RoboData-specific triggers**:
- Changing module boundaries (e.g., moving logic from `backend/` to `pipeline/` or `domain/`)
- Adding new top-level packages
- Changing data ownership (which module owns `Job` state, which owns pipeline artifacts)
- Updating import rules or dependency directions

### `systematic-investigation` — Debugging & Root Cause

**Load for**: tricky bugs, failure investigation, root cause analysis, pre-mortem.

**RoboData-specific triggers**:
- "Why does the worker pick up the wrong job?"
- "Why is judge A seeing judge B's jobs?"
- "Why does the pipeline hang at stage X?"
- Investigating `job.json` corruption or `events.jsonl` ordering bugs

### `session-handoff` — Checkpoint & Resume

**Load for**: saving context, switching devices/branches, checkpointing after milestones.

**RoboData-specific triggers**:
- After completing a vertical slice from `slicing.md`
- Before a long-running pipeline test that requires session continuity
- When switching between auth, job-store, and pipeline workstreams

---

## Composition

- Default: `code-craft` alone for implementation tasks.
- Add `reviewer` as a security lens when touching auth, isolation, or data-handling code.
- Add `codebase-exploration` before major refactors across 3+ files.
- Do not compose more than 2 skills.

---

## Self-Check Before Final Output

- [ ] I executed the complete workflow defined in the skill's `SKILL.md`
- [ ] I did not skip any step marked mandatory, required, must, or hard stop
- [ ] I produced all artifacts the skill's workflow specifies
- [ ] I used the minimum number of models/agents/personas the skill requires
