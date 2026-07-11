# Skill Wiring (Compositions)

Common multi-skill compositions for RoboData development workflow.

## Greenfield Feature

```
project-foundation → architecture-writer → requirements-driven-dev → code-craft → reviewer
```

1. **`project-foundation`** — bootstrap AGENTS.md, GLOSSARY.md, Makefile, quality gates (if not present).
2. **`architecture-writer`** — define responsibility split, data flow, API contracts, non-goals.
3. **`requirements-driven-dev`** — TRD with acceptance criteria, BDD specs, traceability matrix.
4. **`code-craft`** — implement with SOLID, KISS, modularity, vertical-slice TDD.
5. **`reviewer`** — multi-lens review (security, edge-case, code-quality, design-rigor).

Each stage gates the next: do not implement before specs are approved, do not review before tests pass.

## Bug Fix

```
systematic-investigation → code-craft → reviewer
```

1. **`systematic-investigation`** — 5 Whys, Fishbone, or OODA loop; isolate root cause with evidence.
2. **`code-craft`** — minimal fix with regression test; respect existing module boundaries.
3. **`reviewer`** — verify fix does not regress adjacent behavior; edge-case lens if touching parser/validator.

## Auth Change

```
systematic-investigation → code-craft → reviewer (security lens)
```

Always compose with `reviewer` security lens for any auth.py, token handling, password comparison, or permission check change. Reviewer enforces: `hmac.compare_digest`, constant-time paths, JWT claim validation, session isolation, no secret leakage.

## Queue / Store Change

```
code-craft → reviewer (edge-case lens)
```

Any change to `domain/job_store_fs.py`, `domain/queue.py`, or `pipeline/worker.py` must pass edge-case review: restart behavior, FIFO ordering, concurrent session limits, failed/cancelled transitions, partial writes, missing directories.

## Refactor (3+ files)

```
codebase-exploration → code-craft → reviewer (design-rigor lens)
```

Map existing call chains, dependency graph, and import boundaries first. Then refactor with `code-craft`. Review for design-rigor: module coupling, import rule compliance (backend ⇏ pipeline), interface stability.

## Pipeline Stage Addition

```
codebase-exploration → code-craft → reviewer (edge-case lens)
```

Trace the orchestrator contract (stage interface, progress callback signature, error propagation rules) before adding a new stage. Edge-case review covers: partial failure, idempotency, artifact cleanup, event logging.

## Wiring Principles

- **Gating**: each stage produces a verified artifact; do not proceed without it.
- **Composition limit**: at most one primary skill + one review/safety lens per invocation.
- **Re-entrant**: if a review finds issues, loop back to the preceding implementation skill; do not start a new chain.
