# Agentic Mapping Calibration

> **Status**: proposed, implementation staged
> **Owner**: RoboData team
> **Created**: 2026-07-11
> **Scope**: backend mapping-calibration architecture and incremental delivery plan

## Problem Statement

The current pipeline can produce pose landmarks that are visually plausible on the source video while still generating poor skeleton previews and unstable robot retargeting. The main failure is not always pose detection itself. In many cases the failure is the mapping from detected human motion into a robot-friendly motion representation.

A purely static retargeting stack is too brittle because it cannot infer scene semantics like:
- camera viewpoint,
- whether depth should be trusted,
- whether the motion mostly lies in a tabletop plane,
- whether wrist-only mapping is semantically insufficient,
- whether the skeleton export is still usable even when robot retargeting is not.

We want to preserve the existing deterministic pipeline while adding an **agentic calibration layer** that behaves like a bounded human reviewer/calibrator.

---

## Design Goal

Add a new **agentic mapping calibrator** that:
1. reads compact artifact summaries and sampled visual evidence,
2. proposes a structured mapping profile and optional sparse corrections,
3. reruns deterministic retargeting using that profile,
4. preserves both the baseline and calibrated outputs for comparison,
5. remains bounded, reproducible, and hackathon-safe.

This agent is **not** the primary retargeter. It is a calibration and correction layer on top of deterministic retargeting.

---

## Non-Goals

This slice does **not** attempt to:
- replace pose extraction with a fully agentic vision system,
- generate per-frame robot joints directly from the LLM,
- stream unrestricted chain-of-thought,
- allow arbitrary filesystem browsing or unrestricted code execution,
- replace the single-shot pose/retarget stage reviews,
- solve full physics validity or collision checking.

---

## Key Decision

### Use the agent for calibration, not dense trajectory generation

The agent should output:
- mapping profile,
- correction anchors,
- confidence,
- salvage recommendation,
- structured rationale.

The agent should **not** output:
- a full `[T × 7]` joint trajectory as the canonical result,
- unconstrained freeform action sequences,
- open-ended tool usage over all artifacts.

### Why

A mapping profile is:
- reproducible,
- compact enough for a 32k context budget,
- easy to persist and diff,
- easy to apply deterministically,
- easy to compare against baseline.

A full agent-generated robot trajectory would be:
- expensive,
- hard to validate,
- difficult to reproduce,
- highly sensitive to prompt drift,
- dangerous as training data.

---

## High-Level Architecture

### Existing path

`video -> pose -> skeleton artifacts -> wrist-only retarget -> robot dataset`

### New path

`video -> pose -> skeleton artifacts -> baseline deterministic retarget -> mapping calibrator -> calibrated deterministic retarget -> dual comparison artifacts`

The mapping calibrator becomes a separate async sub-job similar in spirit to pose review and retarget review, but it produces **control/config outputs** rather than only narrative review outputs.

---

## Core Concepts

## 1. Baseline Mapping

The current deterministic retargeter runs first and produces:
- baseline robot trajectory,
- baseline simulation,
- baseline metrics.

This remains valuable because it gives the calibrator a concrete failure mode to inspect.

## 2. Mapping Profile

The calibrator emits a structured mapping profile instead of raw robot motion.

### Example shape

```json
{
  "profile_version": 1,
  "source_pose_representation": "mediapipe_world_landmarks",
  "handedness": "right",
  "body_frame_strategy": "shoulder_aligned",
  "task_plane": "tabletop_horizontal",
  "depth_trust": "low",
  "depth_scale": 0.55,
  "workspace_scale": 0.72,
  "use_landmarks": [
    "right_shoulder",
    "right_elbow",
    "right_wrist",
    "left_shoulder"
  ],
  "arm_model": "shoulder_elbow_wrist",
  "pre_retarget_smoothing": {
    "type": "ema",
    "alpha_wrist": 0.2,
    "alpha_elbow": 0.25,
    "alpha_shoulder": 0.15
  },
  "post_ik_smoothing": {
    "enabled": true,
    "joint_alpha": 0.2,
    "velocity_clip_rads": 0.2
  },
  "z_clamp": true,
  "position_only": true,
  "confidence": 0.82
}
```

## 3. Sparse Correction Anchors

The agent may emit optional sparse corrections for key moments rather than editing every frame.

### Example

```json
{
  "anchors": [
    {
      "frame": 18,
      "depth_scale": 0.6,
      "note": "wrist depth appears exaggerated"
    },
    {
      "frame": 61,
      "body_yaw_offset_deg": -15,
      "note": "body frame rotated relative to camera"
    }
  ]
}
```

Deterministic code interpolates between anchors if it chooses to support them.

## 4. Calibration Verdict

The calibrator also emits a simple decision label:
- `baseline_ok`
- `rerun_with_profile`
- `skeleton_only`
- `reject`

This makes it easy to separate:
- narrative review,
- artifact usability,
- retarget correction decisions.

---

## Inputs to the Calibrator

The calibrator must stay within a bounded context budget.

### Allowed evidence
- sampled original frames,
- sampled skeleton overlay frames,
- sampled skeleton preview frames,
- sampled baseline robot simulation frames,
- compact pose metrics,
- compact retarget metrics,
- baseline mapping config,
- pose review summary,
- retarget review summary.

### Not allowed as raw prompt payload
- entire video frame sequences,
- full parquet dumps,
- full per-frame long numeric arrays,
- unrestricted raw image corpus.

### Sampling strategy
For hackathon scope, use approximately:
- 8–12 keyframes total,
- 2–3 moments from early / middle / late trajectory,
- 1–2 moments around the worst detected metric anomalies.

---

## Tool Model

The calibrator should be bounded and tool-based.

### Recommended tool set
- `get_job_summary`
- `get_artifact_manifest`
- `get_pose_metrics`
- `get_retarget_metrics`
- `get_pose_review`
- `get_retarget_review`
- `get_mapping_context_samples`
- `get_current_mapping_profile`

### Future optional tool
- `rerun_calibrated_mapping(profile)`

This tool should eventually be called by backend orchestration, not directly by the model, unless we explicitly promote it to a safe write action.

---

## Expected Agent Output Contract

The agent must return strict JSON.

### Phase 1 output contract

```json
{
  "decision": "rerun_with_profile",
  "mapping_profile": {},
  "anchors": [],
  "verdict": "robot_mapping_salvageable",
  "confidence": 0.8,
  "summary": "Pose is acceptable, but wrist-only depth is unstable and should be suppressed.",
  "risks": [
    "camera depth ambiguity",
    "right wrist partially occluded"
  ]
}
```

### Notes
- `mapping_profile` is required when `decision = rerun_with_profile`
- `anchors` may be empty
- `summary` must be concise and UI-safe
- all values must be serializable and persisted under the job output tree

---

## Artifact Model Changes

Under each job output tree, add a calibration branch:

```text
output/
  baseline/
    simulation.mp4
    mapping_profile.json
    metrics.json
  calibrated/
    simulation.mp4
    mapping_profile.json
    metrics.json
  calibration/
    decision.json
    events.jsonl
    samples/
      original_*.jpg
      overlay_*.jpg
      skeleton_*.jpg
      robot_*.jpg
```

### Minimum MVP rule
If we want a smaller first increment, we can avoid physically moving all old baseline artifacts and instead persist only:
- `calibration/decision.json`
- `calibration/mapping_profile.json`
- `calibration/events.jsonl`
- `calibration/samples/`

Then the rerun writes:
- `simulation_calibrated.mp4`
- `dataset_robot_calibrated/`

---

## API Additions

These are backend-only contracts for the future UI.

### New endpoints
- `GET /api/jobs/{job_id}/mapping-calibration`
- `POST /api/jobs/{job_id}/mapping-calibration/run`
- `GET /api/jobs/{job_id}/mapping-calibration/stream`
- `GET /api/jobs/{job_id}/mapping-calibration/samples`

### Optional compare endpoints
- `GET /api/jobs/{job_id}/artifacts/compare`

### UX expectations
The future UI should be able to show:
- baseline simulation,
- calibrated simulation,
- selected keyframe evidence,
- mapping profile JSON,
- concise agent summary.

---

## Incremental Delivery Plan

## Increment 0 — Docs and contracts only
Deliverables:
- this spec,
- architecture updates,
- MVP pipeline updates,
- quality evaluation doc updates.

No behavioral change yet.

## Increment 1 — Deterministic profile-driven retarget config
Deliverables:
- introduce a `mapping_profile` object in code,
- make retargeter configurable by profile,
- no agent yet,
- one or two manual preset profiles for debugging.

Goal:
- prove that the retargeter can improve when given better assumptions.

## Increment 2 — Sample generation for calibration
Deliverables:
- generate compact sampled frames from:
  - original,
  - skeleton overlay,
  - skeleton preview,
  - baseline robot sim,
- persist `mapping_context_samples.json`.

Goal:
- build bounded evidence packs for a future agent.

## Increment 3 — Agentic calibrator (read-only)
Deliverables:
- async calibration sub-job,
- SSE stream,
- bounded tool loop,
- JSON output contract,
- no automatic rerun yet.

Goal:
- produce a usable mapping profile suggestion.

## Increment 4 — Deterministic rerun from agent profile
Deliverables:
- backend applies returned mapping profile,
- reruns calibrated retarget,
- writes calibrated robot artifacts,
- exposes baseline vs calibrated comparison.

Goal:
- real measurable improvement path.

## Increment 5 — Sparse correction anchors
Deliverables:
- support anchor-based local corrections,
- deterministic interpolation between anchors,
- compare profile-only vs profile+anchors.

Goal:
- improve difficult clips without letting the agent emit full trajectories.

---

## Acceptance Criteria

A successful implementation should let a reviewer say:
- “the baseline mapping is bad,”
- “the agent recommended a different mapping profile,”
- “the rerun is visibly better,”
- “the robot dataset is now usable OR still only skeleton-usable.”

Concrete signals:
- lower `sudden_jump_count`,
- lower wrist/EE noise,
- better visual alignment between source pose and robot motion,
- preserved reproducibility via persisted profile JSON.

---

## Risks

- The agent may overfit to sampled frames and miss failures between samples.
- The agent may recommend unstable profiles if the output schema is too unconstrained.
- If we skip deterministic profile-driven retarget support first, the agent output will not be actionable.
- Vision context can still grow too large if sample generation is not aggressively bounded.

---

## Recommendation

Proceed incrementally.

The next implementation step should be **Increment 1**:
- make the retargeter profile-driven,
- keep the current static path as the baseline default,
- document and persist mapping assumptions explicitly.

That gives us a safe deterministic foundation before introducing a full agentic calibration loop.
