# Ubiquitous Language — RoboData

Applies to **all implementation, design, naming, and codebase-modifying tasks**. All code, docs, logs, UI labels, and API contracts must use the canonical terms defined in `GLOSSARY.md`.

---

## Protocol

1. **Locate the glossary** — `GLOSSARY.md` at the project root. This is the single source of truth for domain vocabulary.
2. **Inspect before coding** — read `GLOSSARY.md` before naming any new class, variable, file, endpoint, or UI element.
3. **Align exactly** — use the canonical term as defined. Do not invent synonyms.
4. **Extend with approval** — if a new domain concept is needed, propose the term, get user approval, then append to `GLOSSARY.md`.

---

## Canonical Terms (Summary from GLOSSARY.md)

| Canonical Term | Definition (abbreviated) | Prohibited Aliases |
|---|---|---|
| **Job** | End-to-end video→dataset processing request | task, workitem, record |
| **JobStatus** | Lifecycle state: `queued`, `running`, `completed`, `failed`, `cancelled` | **`pending`** (must be `queued`); stage names as statuses |
| **PipelineStage** | Ordered phase: `ingest`, `preprocess`, `pose`, `retarget`, `evaluate`, `package`, `finalize` | preprocessing, pose_estimation, retargeting, evaluating, packaging |
| **JudgeSession** | Identity scope for an anonymous judge, identified by `judge_session_id` | session, user_session, admin_session |
| **AdminRole** | Authorization claim granting global visibility | admin, superuser, operator |
| **AccessToken** | Signed JWT with `sub`, `exp` claims | jwt, bearer_token, auth_token, session_token |
| **SourceVideo** | Raw uploaded video file | video, input, source, clip, footage |
| **FrameSet** | Extracted still frames at target FPS | frames, extracted_frames, images |
| **PoseLandmarks** | Per-frame 33-body-landmark 3D skeleton | pose, skeleton, keypoints, landmarks |
| **JointTrajectory** | Time-series of 7-DOF robot joint angles [T,7] | trajectory, joint_angles, q_traj |
| **QualityGrade** | Traffic-light: `green`, `yellow`, `red` | score, rating, level, pass/fail |
| **LeRobotDataset** | Final packaged Parquet output + meta/stats JSON | dataset, output, result, package |
| **JobSnapshot** | Immutable point-in-time view of a Job | job_status, poll_result, status_response |
| **EventLog** | Append-only `events.jsonl` — job lifecycle events | log, history, timeline, audit_trail |
| **Queue** | FIFO job scheduling mechanism | task_queue, worker_pool, celery, broker |

---

## Drift Enforcement (Hard Stops)

### 1. `pending` → `queued`

**Prohibited**: `JobStatus.PENDING`, `status="pending"`, label "Pending" in UI, `"pending"` in ACTIVE_STATUSES.
**Required**: `JobStatus.QUEUED`, `status="queued"`, label "Queued".
**Rationale**: "pending" suggests unacknowledged; "queued" means accepted and waiting for a worker slot.

Existing code with `PENDING` is drift to be corrected. New code must never introduce it.

### 2. Stage names as statuses

**Prohibited**: `JobStatus.PREPROCESSING`, `JobStatus.POSE_ESTIMATION`, `JobStatus.RETARGETING`, `JobStatus.EVALUATING`, `JobStatus.PACKAGING`, `current_stage="preprocessing"`, etc.
**Required**: `JobStatus.RUNNING` while processing; `current_stage` uses `PipelineStage` enum values (`ingest`, `preprocess`, `pose`, `retarget`, `evaluate`, `package`, `finalize`).
**Rationale**: Status and stage are orthogonal. A Job is `running` while moving through stages.

### 3. Bare admin subject

**Prohibited**: `create_access_token({"sub": "admin"})` with no session context.
**Required**: JWT payload includes `judge_session_id` (UUID) and scoped role claim.

---

## Naming in Code

- Enum members: `JobStatus.QUEUED`, `PipelineStage.INGEST`, etc.
- Variables: `job_id`, `session_id`, `judge_session_id` (not `judge_id` or `user_id`).
- API fields: `status`, `current_stage` (not `state`, `phase`).
- UI labels: match the canonical term exactly. "Job" not "Task", "Queued" not "Pending".
- Log messages: `"Job <id> transitioning to stage ingest"` (not `"Task <id> entering preprocessing step"`).

---

## When You Find Drift

Existing code that uses prohibited aliases (e.g., `JobStatus.PENDING`, `current_stage="preprocessing"`) is **technical drift to be corrected**. When working near drifted code:

1. Do not propagate the drift in new code.
2. Mark with: `// DRIFT: "pending" → must be "queued" per GLOSSARY.md`
3. Fix if the scope of your change already touches that code path.
4. If out of scope, log it as technical debt with the drift marker above.
