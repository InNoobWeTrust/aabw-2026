# Adaptive Mapping Orchestrator for Partial-Robot Targets

> **Status**: proposed for immediate implementation
> **Owner**: RoboData team
> **Created**: 2026-07-12
> **Audience**: backend, pipeline, frontend, demo operations

## Problem

The current retarget stack assumes a mostly static mapping from MediaPipe pose output to a robot trajectory. That works acceptably for some clips, but it fails on an important demo case:

- the source video contains a **full human body**,
- the target robot represents only a **single controllable part** such as one arm/hand,
- the best usable signal is therefore the **intersection** between source motion and robot capability,
- deciding that intersection depends on scene semantics and evidence quality, not only on a static preset.

The golden MediaPipe example demonstrates this clearly: the extracted 3D skeleton is broadly correct, but the current mapping strategy still misconfigures the clip for a hand-only robot. That failure is not just a numeric tuning issue. It is a reasoning and evidence-selection problem.

## Design Goal

Add an **adaptive orchestration layer** that can:

1. inspect the whole job and all important artifacts,
2. reason about source-task intent versus target-robot capability,
3. invoke bounded review and mapping tools,
4. select or refine the motion intersection to retarget,
5. produce capture guidance when the clip is not salvageable,
6. optionally collaborate with a human through manual checkpoints and undo.

This orchestration layer must remain bounded, reproducible, and demo-safe. It is not allowed to replace deterministic pose extraction or generate arbitrary raw robot trajectories without constraints.

## Primary Insight

### Static mapping is insufficient for partial robots

A full-body human clip can still be useful for a hand-only robot, but only after we answer questions such as:

- Which limb is the real task carrier?
- Is torso motion contextual or essential?
- Should we preserve wrist path, end-effector intent, or grasp-plane motion?
- Is depth trustworthy enough to keep, or should the motion be planarized?
- Does the target robot need a body-frame reinterpretation before IK?

Those decisions should be made by a bounded orchestrator that sees evidence from the whole pipeline, not by a single hard-coded mapping preset.

## Proposed Architecture

### Responsibility split

#### Deterministic pipeline
**Owns**:
- video ingest and preprocessing,
- MediaPipe pose extraction,
- artifact generation,
- deterministic retarget execution,
- evaluation and packaging.

**Does not own**:
- semantic task interpretation,
- source-to-target capability intersection decisions,
- capture coaching,
- interactive undo/checkpoint workflows.

#### Adaptive orchestration service
**Owns**:
- evidence gathering across all pipeline stages,
- bounded tool use,
- orchestration of review agents and mapping agents,
- choosing between baseline, partial-body mapping, skeleton-only salvage, or reject,
- producing capture guidance and manual next-step suggestions,
- checkpoint lineage for reversible mapping experiments.

**Does not own**:
- raw pose extraction,
- unconstrained file browsing,
- direct arbitrary code execution,
- final packaging without deterministic revalidation.

#### Mapping agent
**Owns**:
- proposing a structured target-aware mapping plan,
- identifying the active source subgraph (for example right arm only),
- producing profile edits and optional sparse anchors,
- summarizing risks and confidence.

**Does not own**:
- final acceptance alone,
- direct silent overwrites of existing outputs,
- full dense trajectory generation as canonical truth.

#### Manual mapping workspace
**Owns**:
- showing evidence and current mapping state,
- allowing explicit edits to mapping profile and anchors,
- checkpoint save / restore / diff / undo,
- optionally delegating edits through assistant chat.

**Does not own**:
- hidden auto-commits of mapping decisions,
- bypassing deterministic rerun and validation.

## Evidence Model

The orchestrator needs a compact but cross-stage evidence pack.

### Required evidence
- source video keyframes
- 2D skeleton overlay keyframes
- 3D skeleton preview keyframes
- baseline robot simulation keyframes
- pose metrics
- retarget metrics
- baseline review summaries
- current mapping profile
- robot capability descriptor
- capture metadata when available

### New evidence abstractions

#### Source motion intersection summary
A compact description of which human joints and frames appear to carry the task.

Example fields:
- `active_chain`: `right_arm`
- `supporting_context`: `[torso_yaw, shoulder_frame]`
- `task_plane`: `tabletop_horizontal`
- `depth_reliability`: `low`
- `dominant_contact_zone`: `right_hand_workspace`

#### Target capability descriptor
A structured summary of what the robot can express.

Example fields:
- `target_class`: `single_arm_hand_only`
- `controllable_chains`: `[right_arm]`
- `excluded_source_regions`: `[legs, head, left_arm]`
- `preferred_motion_mode`: `position_only`
- `workspace_notes`: `tabletop reach, limited vertical authority`

#### Capture guidance report
Produced when the clip is weak or unsalvageable.

Example fields:
- `salvageability`: `retry_capture`
- `primary_failures`: `[occlusion, poor side angle, task leaves frame]`
- `recommended_camera_pose`: `3/4 front-right at chest height`
- `recommended_operator_behavior`: `keep manipulating hand visible for full clip`

## Orchestrator Loop

```ascii
completed job
    |
    v
build evidence pack
    |
    v
run orchestrator assessment
    |
    +--> baseline acceptable ---------> keep baseline
    |
    +--> partial mapping needed ------> invoke mapping agent
    |                                      |
    |                                      v
    |                              propose intersection-aware profile
    |                                      |
    |                                      v
    |                              deterministic rerun + compare
    |                                      |
    |                                      +--> improved -> adopt candidate revision
    |                                      |
    |                                      +--> not improved -> checkpoint + fallback
    |
    +--> skeleton only -----------> mark skeleton-usable + explain limits
    |
    +--> capture retry -----------> emit capture guidance
```

## Tooling Contract

The orchestrator must use a small, auditable tool surface.

### Read-only tools
- `get_job_summary`
- `get_artifact_manifest`
- `get_pose_metrics`
- `get_retarget_metrics`
- `get_pose_review`
- `get_retarget_review`
- `get_mapping_context_samples`
- `get_target_capability_descriptor`
- `get_current_mapping_revision`
- `get_revision_history`
- `get_capture_guidance_rules`

### Controlled write tools
- `propose_mapping_revision`
- `apply_mapping_revision`
- `rerun_mapping_revision`
- `create_mapping_checkpoint`
- `restore_mapping_checkpoint`
- `attach_operator_note`

### Safety constraints
- no unrestricted filesystem access
- no raw shell execution by the model
- every write creates or references a checkpoint
- promotion of a candidate revision requires deterministic rerun output
- final adoption requires persisted comparison evidence

## Persistence Model

Add a durable branch under each job:

```text
output/
  orchestration/
    snapshot.json
    events.jsonl
    evidence_manifest.json
    capture_guidance.json
    compare/
      latest.json
  mapping_sessions/
    session.json
    events.jsonl
    checkpoints/
      0001_baseline/
        mapping_profile.json
        anchors.json
        summary.json
      0002_agent_candidate/
        mapping_profile.json
        anchors.json
        summary.json
      0003_manual_edit/
        mapping_profile.json
        anchors.json
        summary.json
    revisions/
      current.json
      history.jsonl
```

## Data Contracts

### Orchestration snapshot
Tracks one bounded orchestration run.

Minimum fields:
- `job_id`
- `status`
- `decision`
- `summary`
- `recommended_action`
- `selected_source_region`
- `selected_target_mode`
- `checkpoint_id`
- `comparison_path`
- `capture_guidance_path`
- `metadata`

### Mapping checkpoint
An immutable restore point.

Minimum fields:
- `checkpoint_id`
- `parent_checkpoint_id`
- `created_by` (`baseline`, `orchestrator`, `assistant`, `manual`)
- `mapping_profile`
- `anchors`
- `reason`
- `comparison_summary`

### Mapping revision request
A mutable candidate that can later become a checkpoint.

Minimum fields:
- `session_id`
- `candidate_id`
- `base_checkpoint_id`
- `proposed_changes`
- `author`
- `summary`

## API Additions

### Orchestrator
- `GET /api/jobs/{job_id}/orchestration`
- `POST /api/jobs/{job_id}/orchestration/run`
- `GET /api/jobs/{job_id}/orchestration/stream`
- `GET /api/jobs/{job_id}/orchestration/evidence`
- `GET /api/jobs/{job_id}/orchestration/capture-guidance`

### Mapping workspace
- `GET /api/jobs/{job_id}/mapping-sessions`
- `POST /api/jobs/{job_id}/mapping-sessions`
- `GET /api/jobs/{job_id}/mapping-sessions/{session_id}`
- `POST /api/jobs/{job_id}/mapping-sessions/{session_id}/candidate`
- `POST /api/jobs/{job_id}/mapping-sessions/{session_id}/checkpoint`
- `POST /api/jobs/{job_id}/mapping-sessions/{session_id}/restore`
- `POST /api/jobs/{job_id}/mapping-sessions/{session_id}/rerun`

### Assistant-assisted editing
- extend existing assistant sessions with mapping-aware tools, or
- create a dedicated mapping assistant mode with the same transcript persistence pattern.

## UX for Demo

The demo needs one clear operator flow:

1. Upload clip.
2. Inspect source / overlay / skeletal 3D / robot preview.
3. Run orchestrator.
4. See one of four outcomes:
   - baseline is fine,
   - agent found a better partial-body mapping,
   - skeleton-only salvage,
   - recapture guidance.
5. If needed, open manual mapping workspace.
6. Save candidate checkpoint, rerun, compare, undo if worse.

### Minimal UI panels
- **Orchestrator Summary**: decision, confidence, risks, recommended next step
- **Evidence Strip**: synchronized keyframes for source / overlay / 3D / robot
- **Capability Match**: source region vs target capability intersection
- **Checkpoint Timeline**: baseline, agent candidate, manual variants
- **Manual Controls**: mapping profile JSON editor or form controls
- **Assistant Panel**: “make this more hand-only”, “undo last change”, “why did you reject depth?”

## Acceptance Criteria

The feature is successful when a reviewer can say:
- the system recognized that full-body input needed a hand-only interpretation,
- the orchestrator chose the correct motion intersection or explained why it could not,
- the resulting rerun is visibly more aligned with the target robot,
- capture guidance is actionable when salvage is not possible,
- manual edits are reversible through checkpoints.

Concrete signals:
- improved visual fit between source task-carrying limb and robot motion
- fewer retarget spikes and less irrelevant body motion transfer
- persisted agent/manual revision history under the job output tree
- ability to restore a previous mapping state without rerunning the full upstream pipeline

## Non-Goals

This proposal does **not** include:
- unconstrained agent-generated dense robot trajectories as canonical output
- replacing MediaPipe with a new VLM pose stack for the demo
- multi-job distributed orchestration
- generic CAD/URDF robot authoring
- full teleoperation UX
- open-ended video editing or frame painting tools

## Delivery Recommendation

Implement in parallel as four slices:

1. **Contracts + persistence** — orchestration snapshot, mapping session, checkpoint storage.
2. **Backend orchestrator** — evidence pack, bounded tool loop, capture guidance, candidate rerun compare.
3. **Manual mapping backend** — candidate edits, checkpoint restore, rerun endpoints, assistant tool extensions.
4. **Frontend workspace** — orchestration panel, checkpoint timeline, manual edit flow.

The existing read-only calibration service remains useful, but it should become a subordinate evidence input to the new orchestrator rather than the final decision-maker.
