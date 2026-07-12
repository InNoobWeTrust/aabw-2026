# Implementation Plan — Adaptive Mapping Orchestrator

> **Status**: active planning
> **Owner**: RoboData team
> **Created**: 2026-07-12
> **Source**: `docs/engineering/adaptive-mapping-orchestrator.md`

## Delivery Strategy

We are optimizing for **demo readiness**, not perfect generality.

That means:
- preserve the deterministic pipeline as the execution backbone,
- add bounded orchestration on top,
- ship the smallest manual override workflow that is visibly useful,
- prefer revisioned filesystem state over complicated infrastructure.

## Critical path

```ascii
contracts/persistence
        |
        +-------------------+
        |                   |
        v                   v
backend orchestrator   manual mapping backend
        |                   |
        +---------+---------+
                  |
                  v
            frontend workspace
                  |
                  v
            demo validation pass
```

## Parallel Slice Breakdown

### Slice A — Orchestration contracts and persistence
**Goal**: create the durable state model for orchestration runs, mapping sessions, revisions, and checkpoints.

**Scope**:
- `domain/` models and enums
- backend stores under `backend/`
- response/request models
- basic read/write route scaffolding

**Files likely touched**:
- `domain/enums.py`
- new `domain/orchestration.py`
- new `domain/mapping_session.py`
- new `backend/orchestration_store.py`
- new `backend/mapping_session_store.py`
- `backend/models.py`
- `backend/routes.py`
- tests for stores and route contracts

**Done when**:
- orchestration snapshot can be persisted per job
- mapping session can be created per job
- immutable checkpoints can be saved and restored in storage
- route contracts exist for listing/getting sessions and checkpoints
- tests cover persistence and API serialization

**Verification**:
- `uv run pytest -q tests/test_orchestration_store.py tests/test_mapping_sessions.py tests/test_routes.py`

### Slice B — Adaptive orchestrator backend
**Goal**: add a bounded orchestration service that builds evidence, reasons about partial-target mapping, requests a mapping candidate, reruns deterministically, and writes comparison + capture guidance.

**Scope**:
- orchestration service
- evidence manifest builder
- target capability descriptor
- local fallback heuristics
- OpenAI-compatible tool-based action loop
- orchestration SSE stream

**Files likely touched**:
- new `backend/orchestration_service.py`
- new `backend/orchestration_tools.py`
- new `backend/orchestration_prompts.py`
- `backend/llm_client.py`
- `backend/routes.py`
- `pipeline/retarget.py`
- possibly `pipeline/calibration_samples.py`
- tests for orchestrator decisions and fallback behavior

**Done when**:
- completed job can trigger an orchestration run
- run emits persisted events and final snapshot
- orchestrator can choose among `baseline_ok`, `rerun_with_profile`, `skeleton_only`, `retry_capture`
- deterministic rerun compare artifacts are persisted
- capture guidance is produced for weak clips

**Verification**:
- targeted pytest for orchestrator service
- one real-job smoke test against saved demo artifacts

### Slice C — Manual mapping sessions, checkpoints, and assistant edits
**Goal**: let an operator or assistant create candidate revisions, save checkpoints, rerun safely, and undo by restoring prior state.

**Scope**:
- mapping session service
- checkpoint lifecycle
- restore/rerun endpoints
- assistant tool expansion for mapping edits
- transcript-safe audit trail

**Files likely touched**:
- new `backend/mapping_session_service.py`
- `backend/assistant_service.py`
- `backend/routes.py`
- `backend/models.py`
- new domain models for revision/checkpoint payloads
- tests for checkpoint lineage and restore semantics

**Done when**:
- baseline checkpoint is created for a completed job
- agent/manual candidate can be saved as a new checkpoint
- restore endpoint switches current revision to an older checkpoint
- rerun endpoint can regenerate compare artifacts from any checkpoint
- assistant can propose or apply bounded mapping edits through persisted messages/events

**Verification**:
- store tests
- route tests
- assistant tool-loop tests for allowed mapping actions

### Slice D — Frontend orchestration and manual mapping workspace
**Goal**: expose the orchestration result and a usable manual correction flow in the existing Next.js demo UI.

**Scope**:
- orchestration summary panel
- evidence viewer integration
- checkpoint timeline
- mapping profile editor / form
- rerun / restore controls
- assistant chat integration for mapping mode

**Files likely touched**:
- `frontend/app/page.tsx`
- new `frontend/components/OrchestrationPanel.tsx`
- new `frontend/components/MappingWorkspace.tsx`
- new `frontend/components/CheckpointTimeline.tsx`
- `frontend/components/AssistantChat.tsx`
- `frontend/components/VideoInspector.tsx`

**Done when**:
- a completed job shows orchestrator status and decision
- user can inspect baseline vs candidate compare data
- user can save a manual edit checkpoint
- user can restore a prior checkpoint
- UI works after `npm run build`

**Verification**:
- `npm run build`
- manual smoke test through `/jobs/<id>`

## Worktree Plan

Use isolated worktrees so slices can advance in parallel without stomping each other.

### Worktree 1
- **Branch**: `feature/orchestration-contracts`
- **Owns**: Slice A
- **Can overlap with**: nobody on backend route signatures without coordination

### Worktree 2
- **Branch**: `feature/orchestrator-backend`
- **Owns**: Slice B
- **Depends on**: Slice A contracts; may stub until rebased/cherry-picked

### Worktree 3
- **Branch**: `feature/mapping-sessions`
- **Owns**: Slice C
- **Depends on**: Slice A contracts; may initially use temporary scaffolding

### Worktree 4
- **Branch**: `feature/orchestration-ui`
- **Owns**: Slice D
- **Depends on**: request/response contracts from Slice A plus mock-friendly payloads

## Dependency Rules

To keep parallel execution safe:
- Slice A defines canonical enums, stores, and API schema names.
- Slices B and C may proceed with small temporary shims if Slice A is not merged yet.
- Slice D should mock the expected API responses and avoid blocking on full backend completion.
- No slice should rename existing calibration endpoints without explicit coordination.

## Demo-First Acceptance Sequence

### Milestone 1 — Backend contracts land
Visible outcome:
- persisted orchestration and mapping-session state exists on disk

### Milestone 2 — Orchestrator decisions land
Visible outcome:
- one button can analyze a completed job and return a bounded decision with guidance

### Milestone 3 — Checkpointed edits land
Visible outcome:
- operator can save/restore mapping states and rerun without re-uploading

### Milestone 4 — UI lands
Visible outcome:
- end-to-end demo in the browser with baseline vs candidate workflow

## Suggested Initial Task Order Today

1. Write docs and freeze naming.
2. Create worktrees for slices A-D.
3. Dispatch Slice A and Slice D immediately because their contracts can be defined fastest.
4. Dispatch Slice B and Slice C with explicit assumptions about provisional contracts.
5. Periodically reconcile schema drift by copying the canonical contract from Slice A into the other worktrees.

## Risks and Mitigations

### Risk: contract drift across worktrees
**Mitigation**: Slice A owns a compact canonical schema table; every other slice must echo it exactly.

### Risk: orchestrator becomes too open-ended
**Mitigation**: keep explicit tool names, bounded turns, and deterministic rerun gate.

### Risk: manual editing breaks reproducibility
**Mitigation**: every apply action creates an immutable checkpoint and stores author + reason.

### Risk: UI blocks on backend completion
**Mitigation**: build UI against mock payloads and feature-flag empty states.

## Canonical decision vocabulary

Use these exact decision values unless Slice A changes them explicitly:
- `baseline_ok`
- `rerun_with_profile`
- `skeleton_only`
- `retry_capture`

Use these exact checkpoint authors unless Slice A changes them explicitly:
- `baseline`
- `orchestrator`
- `assistant`
- `manual`

## Exit criteria for demo readiness

The feature is demo-ready when:
- the golden MediaPipe example can be analyzed by the orchestrator,
- the system identifies that a hand-only interpretation is needed,
- at least one candidate rerun is persisted and viewable,
- the operator can undo to baseline,
- the UI clearly explains when recapture is the better choice.
