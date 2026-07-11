# Plan: Agentic Two-Stage Review + Skeleton Export

## Goal
Upgrade RoboData from a single final robot-only output path into a dual-artifact, dual-review pipeline that:
1. exports usable **pose/skeleton-stage** artifacts and dataset,
2. exports **retargeted robot-joint** artifacts and dataset,
3. runs **two async LLM-backed reviews** via **Featherless + Daytona**,
4. streams each review over **SSE**,
5. leaves the frontend implementation to a separate specialized UI agent targeting **Next.js + React + CopilotKit**.

This plan covers architecture and backend logic only. UX implementation is out of scope here.

## Handoff Requirement
Before implementing backend changes, the implementation/code agent should copy this plan into `docs/specs/` (recommended filename: `docs/specs/agentic-two-stage-review-and-skeleton-export.md`) so the separate UX agent can use the same source-of-truth plan while working on the Next.js/CopilotKit slice.

---

## Locked Decisions

### Review architecture
- Use **Featherless** for LLM inference.
- Use **Daytona** for sandboxed review-agent execution.
- Use **two bounded review stages**:
  1. `pose_review`
  2. `retarget_review`
- Reviews are **async sub-jobs**, not part of main pipeline critical path.
- Reviews stream via **SSE**.
- Review prompts must stay within a hard **32k context budget**; no raw full artifact dumps.

### Frontend target (for backend contract design only)
- Assume future frontend is **Next.js + React + CopilotKit**.
- Backend must expose clean stream endpoints and persisted review snapshots suitable for isolated review components.
- Backend work must not depend on frontend rewrite landing first.

### Export policy
- Always export **skeleton-stage dataset** when pose extraction succeeds.
- Keep skeleton exportability separate from skeleton usability verdict.
- Produce both pose-stage visualization artifacts:
  - `skeleton_overlay.mp4`
  - `skeleton_preview.mp4`

### Product semantics
- Main upload job completes when pipeline artifacts are ready.
- Reviews are attached as sub-resources with their own lifecycle.
- A job may be:
  - robot-usable,
  - skeleton-usable only,
  - or rejectable at both stages.

---

## Contradictions Found In Current Code

1. The current “AI review” is **not** provider-backed.
   - `pipeline/staged_review.py::generate_ai_review()` is a local deterministic markdown generator.
   - No API key or external AI call is used.
2. The frontend already has an “AI Robotics Agent Review” panel title in `frontend/index.html`, but the review is not truly agentic and not streamed.
3. Current pipeline only packages the **retargeted robot dataset** and renders only the **final robot simulation**, so it cannot isolate where data quality breaks.

Implementation must treat the current review as a legacy fallback to be replaced or explicitly downgraded in naming.

---

## Architecture Changes

## 1. Main pipeline split into artifact branches

### Existing path
`video -> preprocess -> pose -> retarget -> evaluate -> package(robot) -> finalize`

### New path
`video -> preprocess -> pose`
- export pose artifacts
- export skeleton dataset
- enqueue `pose_review`

`pose -> retarget -> evaluate -> package(robot) -> render robot sim`
- export robot artifacts
- export robot dataset
- enqueue `retarget_review`

Main job then becomes `completed` once artifact generation is done, regardless of review completion.

---

## 2. New artifact model per job

Under `data/jobs/<job_id>/output/` introduce:

```text
output/
  dataset_skeleton/
    episode_000000.parquet
    meta.json
    stats.json
  dataset_robot/
    episode_000000.parquet
    meta.json
    stats.json
  skeleton_overlay.mp4
  skeleton_preview.mp4
  simulation.mp4
  reviews/
    pose/
      review.json
      review.md
      events.jsonl
    retarget/
      review.json
      review.md
      events.jsonl
```

### Notes
- Preserve existing `simulation.mp4` name for robot simulation unless a cleaner rename is desired by implementer.
- Review subdirectories must be append-only/event-friendly to support SSE replay and persisted snapshots.

---

## 3. Review sub-job state model

Introduce a review resource separate from job status.

### Suggested enum
`ReviewStatus = pending | running | completed | failed`

### Suggested stage keys
- `pose`
- `retarget`

### Review record shape
Persist one record per review stage, e.g. `review.json`:

```json
{
  "job_id": "...",
  "review_stage": "pose",
  "status": "running",
  "provider": "featherless",
  "sandbox": "daytona",
  "started_at": "...",
  "completed_at": null,
  "verdict": null,
  "summary": null,
  "markdown_path": "...",
  "json_path": "...",
  "error": null,
  "context_manifest": {
    "metrics_path": "...",
    "artifact_manifest_path": "..."
  }
}
```

### Verdict model
Use explicit verdicts, not just green/yellow/red:
- `approved`
- `usable_skeleton_only`
- `needs_review`
- `rejected`

For pose review, `usable_skeleton_only` is allowed if pose is good enough but retarget not yet judged.
For retarget review, `usable_skeleton_only` means pose export is acceptable but robot dataset is not.

---

## 4. SSE contract

Add stream endpoints:
- `GET /api/jobs/{job_id}/reviews/pose/stream`
- `GET /api/jobs/{job_id}/reviews/retarget/stream`

### Event types
Use SSE events with bounded structured payloads:
- `status`
- `section`
- `token`
- `result`
- `error`
- `done`

### Payload guidance
- `status`: lifecycle changes (`pending`, `running`, etc.)
- `section`: high-level section boundaries (summary, findings, recommendations)
- `token`: incremental text chunks for markdown rendering
- `result`: final structured verdict JSON
- `error`: failure payload
- `done`: terminal marker

### Persistence rule
All streamed content must also be persisted to disk so UI can reconnect or load snapshots without losing prior tokens.

---

## 5. Bounded review context design (32k-safe)

Do **not** send raw full trajectories, full parquet dumps, or long frame-by-frame traces.

Instead generate compact review inputs per stage.

### Pose review input bundle
Include only:
- pose summary metrics:
  - frame count
  - detection rate
  - missing landmark ratios
  - jitter metrics
  - per-keypoint stability summaries for shoulders/elbows/wrists
  - body visibility coverage
- artifact manifest:
  - paths to overlay and preview videos
  - thumbnail/sample frames if implemented later
- compact sampled skeleton sequence summary:
  - bounded downsample only
- export metadata for `dataset_skeleton`

### Retarget review input bundle
Include only:
- robot quality metrics:
  - joint limit violations
  - NaN count
  - max velocity
  - sudden jumps
  - mean jerk
  - completeness ratio
- retarget diagnostics:
  - clipping/saturation counts
  - any IK failure counts if available
- compact downsampled joint trajectory summary
- robot dataset manifest
- path to robot simulation video
- pose review verdict summary as context (not raw markdown)

### Prompt strategy
- deterministic system prompt templates stored locally
- strict output schema (JSON + markdown sections)
- truncate/summarize numeric arrays before prompt creation
- fail review sub-job if context would exceed budget rather than silently dropping random content

---

## Backend Implementation Tasks

## A. Review infrastructure
1. Add new review domain models/enums for status, stage, verdict.
2. Add filesystem review store helpers under job output/reviews.
3. Add review event append/replay helpers for SSE.
4. Add a review runner abstraction:
   - prepares bounded input bundle
   - dispatches sandbox work to Daytona
   - uses Featherless for inference
   - persists stream events and final outputs
5. Add configuration for:
   - Featherless API base/model/key env vars
   - Daytona workspace/project/token env vars
   - review timeout / token budget / max context bytes
6. Keep current rule-based review generator only as a fallback or remove it from primary path.

## B. Pose-stage artifact branch
1. Extend pose stage outputs to persist structured pose data under `work/pose/`.
2. Add `skeleton_overlay.mp4` generation.
3. Add `skeleton_preview.mp4` generation.
4. Add `package_lerobot_skeleton(...)` or equivalent generalized packager.
5. Write skeleton dataset to `output/dataset_skeleton/`.
6. Compute pose-review metrics suitable for bounded prompt input.
7. Enqueue async `pose_review` after skeleton artifacts are ready.

## C. Retarget-stage artifact branch
1. Keep retarget stage but treat it as independent from pose export success.
2. Rename/generalize current packaging so robot dataset goes to `output/dataset_robot/`.
3. Keep robot simulation generation (`simulation.mp4`) or rename to `robot_simulation.mp4` if implementer chooses and updates API consistently.
4. Expand retarget diagnostics available to reviewer (IK or clipping metadata if possible).
5. Enqueue async `retarget_review` after robot artifacts are ready.

## D. Main job result schema update
Extend main job result payload to include both artifact branches and review summaries.

### Suggested shape
```json
{
  "pose": {
    "frame_count": 0,
    "detection_rate": 0.0,
    "artifacts": {
      "overlay_video": "...",
      "preview_video": "..."
    },
    "dataset": {
      "output_dir": "...",
      "files": []
    },
    "review": {
      "status": "pending",
      "verdict": null
    }
  },
  "retarget": {
    "frame_count": 0,
    "robot": "franka_panda",
    "artifacts": {
      "simulation_video": "..."
    },
    "dataset": {
      "output_dir": "...",
      "files": []
    },
    "evaluation": {},
    "review": {
      "status": "pending",
      "verdict": null
    }
  }
}
```

Do not force the UI to parse legacy flat keys if the backend can provide a clearer nested shape. If backward compatibility is needed, temporarily include both old and new keys.

## E. API additions
Add non-UI backend endpoints only.

### New endpoints
- `GET /api/jobs/{job_id}/reviews`
  - returns pose + retarget review snapshot metadata
- `GET /api/jobs/{job_id}/reviews/{stage}`
  - returns persisted latest review snapshot
- `GET /api/jobs/{job_id}/reviews/{stage}/stream`
  - SSE stream
- `GET /api/jobs/{job_id}/artifacts`
  - artifact manifest for both branches
- `GET /api/jobs/{job_id}/downloads/{artifact_key}`
  - optional finer-grained artifact download

### Artifact keys to support
- `dataset_skeleton_zip`
- `dataset_robot_zip`
- `skeleton_overlay_video`
- `skeleton_preview_video`
- `robot_simulation_video`
- `pose_review_md`
- `retarget_review_md`

If fine-grained downloads are too much for one slice, at least expose artifact URLs in job detail response.

---

## UI/UX Handoff Requirements For Separate Agent

The UI agent will implement the framework migration and UX. This backend plan must support it.

### Target frontend architecture
- Next.js + React + CopilotKit
- isolated review panel component per stage
- route-safe auth/session reuse with existing JWT model

### Desired UX flow (for handoff, not implementation here)
Following ui-ux guidance, the UI should prioritize fast diagnostic clarity over dense raw data.

#### Review experience
- Two separate review cards/panels:
  - **Pose Extraction Review**
  - **Robot Retarget Review**
- Each panel shows:
  - live stream state
  - verdict badge
  - markdown body
  - artifact quick links
- Main status area should distinguish:
  - pipeline artifact generation
  - review generation

#### Comparison area
- Original video
- Skeleton overlay
- Skeleton preview
- Robot simulation

#### Dataset export area
- Skeleton dataset export
- Robot dataset export
- Clear labels indicating:
  - export available
  - usability verdict pending/completed
  - skeleton-only salvage path

### Recommended review panel contract
Frontend should be able to:
- open SSE stream lazily when details view opens
- recover from refresh using persisted review snapshot
- continue rendering from new stream tokens if review still running

Do not block backend implementation on UI migration.

---

## Failure Modes / Rules

## Review failures
- If Featherless fails, Daytona fails, prompt overflows, or SSE disconnects:
  - main job remains `completed` if artifacts exist
  - review sub-job becomes `failed`
  - persisted error is available via review snapshot endpoint

## Partial artifact success
- If pose succeeds but retarget fails:
  - skeleton dataset + visuals remain available
  - retarget branch may be absent or failed
  - pose review still runs
  - retarget review does not run or is marked skipped/failed with explicit reason

## Context overflow
- If bounded review input exceeds configured size:
  - fail review sub-job with explicit `context_budget_exceeded`
  - do not silently trim unknown sections beyond configured summarizers

## SSE reconnect
- Stream endpoint should support reconnect by replaying persisted events or at least returning latest snapshot + continuing live events.

---

## Affected Files / Areas

### Backend/domain
- `backend/routes.py`
- `backend/server.py`
- `backend/models.py` or `domain/*` depending on current canonical model location
- `backend/config.py`
- queue/review runner wiring

### Pipeline
- `pipeline/orchestrator.py`
- `pipeline/pose.py`
- `pipeline/package.py`
- new pose visualization module(s)
- review integration module(s)
- current `pipeline/staged_review.py` (replace/refactor)
- `pipeline/render_sim.py`

### Tests
- pipeline unit tests
- route/API tests
- SSE endpoint tests
- review snapshot tests
- artifact manifest/download tests

### Out of scope for this execution agent
- Next.js app creation/migration
- CopilotKit integration
- final visual polish / UX implementation

---

## Implementation Order

1. **Refactor result schema and artifact directory structure**
   - introduce dual output branches and review directories
2. **Implement skeleton dataset packaging**
   - create `dataset_skeleton`
3. **Implement pose visual artifacts**
   - overlay + preview
4. **Add review domain models/store**
   - review status, verdict, persisted snapshots/events
5. **Implement async review scheduling**
   - decouple from main job completion
6. **Implement Featherless + Daytona review runner**
   - bounded prompt creation and structured outputs
7. **Add SSE endpoints**
   - stream persisted + live review events
8. **Add artifact/review API responses**
   - job detail and manifest endpoints
9. **Update legacy review naming/behavior**
   - remove misleading “AI” semantics from old local-only generator or relegate to fallback
10. **Write/expand tests**

---

## Validation Plan

## Unit/integration
- skeleton dataset packaging writes valid parquet/meta/stats
- robot dataset packaging still passes existing checks
- skeleton overlay and preview files are created and readable
- review snapshot store writes/reads correctly
- SSE endpoint emits ordered event stream
- bounded prompt builder enforces context limit
- review failure does not fail completed main job
- pose-only success + retarget failure still preserves skeleton outputs

## End-to-end
Run a real uploaded video and verify:
1. main job reaches `completed`
2. `dataset_skeleton/` exists
3. `dataset_robot/` exists when retarget succeeds
4. `skeleton_overlay.mp4` exists
5. `skeleton_preview.mp4` exists
6. `simulation.mp4` exists
7. `pose_review` starts and streams over SSE
8. `retarget_review` starts and streams over SSE
9. one can observe a job where pose is usable but retarget is poor

## Contract checks for UI handoff
- review snapshot JSON matches documented shape
- SSE event names/payloads are stable
- artifact manifest gives enough information for isolated panels without path guessing

---

## Risks / Watchouts

- Featherless/Daytona integration complexity may exceed current backend structure if review orchestration is tightly coupled to pipeline code.
- Review prompts can easily overflow 32k if implementer passes raw arrays or raw markdown from prior stages.
- Existing frontend expects flat result keys; backend may need a compatibility transition period.
- Current code has overlapping orchestration logic in `backend/routes.py` and `pipeline/orchestrator.py`; implementation agent should avoid deepening that duplication and should converge toward one canonical execution path.
- The existing local `generate_ai_review()` naming is misleading; failing to rename or deprecate it will confuse product behavior.

---

## Non-Goals For This Slice
- Final UI migration to Next.js
- CopilotKit wiring
- high-polish visual UX implementation
- multi-agent swarm review
- multi-provider fallback orchestration beyond Featherless + Daytona
- changing auth/session model

---

## Suggested First Acceptance Scenario
A judge uploads a tabletop manipulation clip and, after processing:
- sees the original video,
- sees `skeleton_overlay.mp4`,
- sees `skeleton_preview.mp4`,
- downloads `dataset_skeleton.zip`,
- sees robot simulation separately,
- downloads `dataset_robot.zip` if available,
- watches **pose review** stream verdict and recommendations,
- watches **retarget review** stream verdict and recommendations,
- can distinguish “pose export is useful, robot retarget is not” from “whole job is unusable”.
