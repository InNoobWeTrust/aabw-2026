# Handoff Context — RoboData

Handoff files live under `.agents/handoffs/` (repo-local, resolved from git root). Use `session-handoff` skill for save/restore operations. This rule defines the checkpoint format and triggers.

---

## Restore Triggers

Restore a handoff only when:
- The user asks to resume, continue, restore, or load a handoff.
- The current task depends on a named previous session or branch handoff.
- Context is clearly missing and a handoff path is provided.

---

## Save Triggers

Save or update a handoff when:
- The user asks for a handoff, checkpoint, or session summary.
- A major milestone completes and future continuation is likely:
  - A vertical slice from `slicing.md` is finished (all tests passing, lint clean).
  - A `domain/` interface is finalized and merged.
  - The `JobStore` implementation passes all contract tests.
  - Auth layer is complete and all 8 auth tests pass.
- Context length risk is high (many files touched, multi-slice work).
- The user signals session end or device switch.

---

## RoboData Handoff Format

Every handoff file must be self-contained. Template:

```markdown
# Handoff: [Brief Title] — [YYYY-MM-DD]

## Goal
[One sentence: what was being built / fixed / migrated.]

## Status
[Current state: which slice(s) complete, which in progress, which blocked.]

## Key Decisions
- [Decision 1 — with rationale]
- [Decision 2 — with rationale]

## Files Touched
| File | Change |
|---|---|
| `domain/models.py` | Added `JobStatus`, `PipelineStage` enums |
| `domain/job_store.py` | Defined `AbstractJobStore` ABC |
| `tests/test_job_store.py` | 10 contract tests written (all passing) |

## Current Drift State
[Any known drift in the touched code path. List files with `PENDING`, `PREPROCESSING`, etc.]

## Verification
- [ ] `make lint` passes
- [ ] `make test` passes (N tests)
- [ ] [Other slice-specific verification]

## Blockers
- [Blocker 1 — what's blocking, what's needed to unblock]
- NONE (if no blockers)

## Next Actions
1. [Immediate next step — specific file/module, not vague area]
2. [Following step]
3. [Following step]
```

---

## Active Slice Tracking

When a handoff is saved mid-slice, include:

```markdown
## Active Slice
- **Slice**: [Slice N — Name from slicing.md]
- **Completed sub-steps**: [e.g., JobStore ABC defined, FS implementation written, 8/10 tests pass]
- **Failing tests**: [e.g., test_cannot_transition_from_terminal — assertion on error type]
- **Next file to touch**: [e.g., domain/job_store_fs.py:142 — status transition validation]
```

---

## Handoff Naming

```
.agents/handoffs/<slice-id>-<short-description>-<YYYYMMDD>.md
```

Examples:
- `.agents/handoffs/slice1-auth-jwt-20260711.md`
- `.agents/handoffs/slice2-jobstore-fs-20260711.md`
- `.agents/handoffs/drift-fix-pending-to-queued-20260711.md`
