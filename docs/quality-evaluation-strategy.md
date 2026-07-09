# Robot Trajectory Quality Evaluation Strategy

**RoboData Hackathon** | Structured approach for combining automated metrics, LLM assessment, and human-in-the-loop review

---

## 1. LLM-Based Trajectory Evaluation

### 1.1 Can a Multimodal LLM Evaluate a Rendered Robot Trajectory?

**Yes, with strong evidence.** Two key papers validate this directly:

- **CriticGPT** (Liu et al., AAAI 2024): Trained a multimodal LLM to watch trajectory videos of robot manipulation tasks and provide preference feedback. The model generalizes to unseen tasks and its reward model surpasses pre-trained representation models for policy guidance. Key finding: MLLMs can serve as automated critics by watching rendered robot videos.

- **SGV / Agreement Bias** (Andrade et al., ICLR 2026): Evaluated MLLM verifiers across web navigation, computer use, and robotics — 13+ models, 28+ designs, thousands of trajectories. Found a critical limitation: MLLMs strongly over-validate agent behavior ("agreement bias") — they tend to approve trajectories rather than detect failures. Their SGV method improves failure detection by 25pp and accuracy by 14pp. Key finding: naive MLLM evaluation is unreliable; you need self-grounded verification.

**For RoboData specifically**, the LLM trajectory evaluation works as follows:

```
Input: Rendered video of robot arm executing the retargeted trajectory (Kaolin/NVIDIA renderer)
       OR side-by-side: original human video (left) + robot trajectory render (right)
Prompt: "Evaluate this robot trajectory. Rate smoothness (1-5), detect anomalies,
        compare against the human reference motion."
Output: JSON quality report with scores, anomaly flags, textual justification
```

### 1.2 Anomaly Detection Capabilities

| Anomaly Type | Can LLM Detect via Video? | Can LLM Detect via Sequence Data? | Notes |
|---|---|---|---|
| Sudden joint jumps | **Yes** — visible as teleporting arm segments | **Yes** — if joint angles provided as text/tabular | Frame-to-frame L1 > 0.15 rad for Franka Panda |
| Oscillations | **Yes** — visible jittering/shaking | **Yes** — if joint velocity is provided | Check zero-crossing rate of velocity derivatives |
| Stuck joints | **Yes** — arm segment doesn't move | **Yes** — near-zero velocity for >0.5s | Compare per-joint variance |
| Joint limit violation | **Partial** — hard to see exact angles | **Yes** — direct comparison against URDF limits | Absolute; doesn't need LLM |
| IK failure (NaN joints) | **Yes** — arm disappears or contorts | **Yes** — any NaN in array | Absolute; doesn't need LLM |
| Wrong handedness | **Yes** — uses wrong arm | **Yes** — wrist trajectory side mismatch | LLM provides contextual understanding |
| Task incompletion | **Yes** — doesn't reach target area | **Partial** — needs object state | LLM is strongest here |
| Unnatural posture | **Yes** — elbow through body, etc. | **No** — joint angles alone miss context | LLM spatial reasoning |

### 1.3 Visual Comparison: Human Video vs Robot Trajectory

**Side-by-side rendered video:**
- Left panel: original human video (MediaPipe skeleton overlay optional)
- Right panel: Kaolin/NVIDIA-rendered robot arm executing retargeted trajectory
- Synchronized at 10fps, both panels same frame index

**What an LLM can judge:**
- **Motion similarity**: "The robot's end-effector follows a generally similar path to the human wrist, but the elbow rises 15° higher on frames 30-45"
- **Temporal alignment**: "The robot reaches the pour position 8 frames (~0.8s) after the human"  
- **Quality cues**: smooth acceleration/deceleration, natural-looking arm posture, no self-collision, correct grasp approach angle
- **Gross failures**: "The robot arm is stationary between frames 120-180" (IK failure), "The arm teleports at frame 57" (tracking loss)

**What an LLM cannot reliably judge:**
- Sub-degree joint angle accuracy from rendered video alone
- Millisecond-level temporal alignment
- Subtle force application (grasp tightness)
- Whether the trajectory would succeed in physical simulation

**SGV mitigation for agreement bias:** Before showing the trajectory, ask the LLM to generate priors about what a good pouring trajectory should look like (smooth arc, wrist at pouring height, gradual tilt). Then evaluate against those self-generated priors rather than rating in isolation. This cuts agreement bias significantly.

---

## 2. Automated Quality Metrics (Deterministic)

These are zero-cost, instant, and should run BEFORE LLM or human review.

### 2.1 Joint History Quality

```python
def compute_joint_metrics(joint_trajectory: np.ndarray, joint_limits: dict) -> dict:
    """
    joint_trajectory: [T × J] joint angles in radians
    joint_limits: {joint_idx: (lower, upper)} from URDF
    """
    T, J = joint_trajectory.shape
    
    velocities = np.diff(joint_trajectory, axis=0)  # [T-1 × J]
    accelerations = np.diff(velocities, axis=0)      # [T-2 × J]
    jerks = np.diff(accelerations, axis=0)            # [T-3 × J]
    
    return {
        # Joint limit violations
        "joint_limit_violations": _count_limit_violations(joint_trajectory, joint_limits),
        "joint_limit_violation_frames": _violation_frames(joint_trajectory, joint_limits),
        
        # Velocity smoothness
        "max_joint_velocity": float(np.max(np.abs(velocities))),
        "velocity_smoothness": float(1.0 / (1.0 + np.std(velocities, axis=0).mean())),
        "sudden_jump_count": int(np.sum(np.abs(velocities) > 0.3, axis=0).max()),  # >0.3 rad/frame = 3 rad/s at 10fps
        
        # Jerk (derivative of acceleration) — lower is smoother
        "mean_jerk": float(np.mean(np.abs(jerks))),
        "max_jerk": float(np.max(np.abs(jerks))),
        "jerk_score": float(1.0 / (1.0 + np.mean(np.abs(jerks)))),
        
        # Oscillation detection
        "oscillation_count": _count_zero_crossings(accelerations),
        "stuck_joint_frames": _count_stuck_joints(velocities, threshold=0.001, min_frames=5),
        
        # NaN / invalid values
        "nan_count": int(np.sum(np.isnan(joint_trajectory))),
        "inf_count": int(np.sum(np.isinf(joint_trajectory))),
        
        # Completeness
        "total_duration_s": float(T / 10.0),  # 10fps
        "expected_duration_s": 30.0,  # from 30s video
        "completeness_ratio": float(T / 300),  # 300 frames expected at 10fps
    }
```

### 2.2 Thresholds for RoboData (Franka Panda, 10fps)

| Metric | Green (pass) | Yellow (flag) | Red (reject) |
|---|---|---|---|
| Joint limit violations | 0 | 0 | >0 (hard reject) |
| NaN joints | 0 | 0 | >0 (hard reject) |
| Max joint velocity | < 2.0 rad/s | 2.0–3.0 rad/s | > 3.0 rad/s |
| Sudden jumps (>0.3 rad/frame) | < 5 | 5–15 | > 15 |
| Mean jerk | < 1.0 | 1.0–2.0 | > 2.0 |
| Oscillation count | < 3 | 3–10 | > 10 |
| Stuck joints (>5 frames) | < 3 | 3–8 | > 8 |
| Completeness ratio | > 0.90 | 0.75–0.90 | < 0.75 |
| Detection failure rate | < 5% | 5–15% | > 15% |

### 2.3 End-Effector Metrics

```python
def compute_ee_metrics(ee_trajectory: np.ndarray) -> dict:
    """
    ee_trajectory: [T × 3] end-effector position (meters, robot base frame)
    """
    velocities = np.linalg.norm(np.diff(ee_trajectory, axis=0), axis=1)
    reach = np.max(ee_trajectory, axis=0) - np.min(ee_trajectory, axis=0)
    
    return {
        "total_path_length_m": float(np.sum(velocities)),
        "max_speed_ms": float(np.max(velocities) * 10),  # 10fps
        "workspace_span_m": float(np.linalg.norm(reach)),
        "is_in_workspace": _check_workspace(ee_trajectory, max_reach=0.855),
        "final_position_m": ee_trajectory[-1].tolist(),
    }
```

---

## 3. LLM-Based Quality Assessment

### 3.1 Two-Stage LLM Pipeline

**Stage 1: Quality Gate (pre-IK)**
- Input: 8 keyframes from the phone video
- Already implemented in the orchestrator (see `mvp-pipeline.md`)
- Validates: video quality, lighting, single person, frontal pose
- Output: routing.json with quality score and pipeline decisions

**Stage 2: Trajectory Validator (post-IK) — NEW**
- Input: side-by-side rendered video OR joint angle sequence as text
- Validates: trajectory smoothness, task completion, human-robot motion similarity
- Output: trajectory quality report with pass/fail and anomaly flags

### 3.2 LLM Prompt Design for Trajectory Validation

```
System:
You are a robot trajectory quality evaluator. You review robot joint trajectories 
generated from human motion capture and assess whether they are suitable for 
training imitation learning policies.

You will receive:
1. A rendered video showing the robot arm executing the trajectory (right panel)
   alongside the original human video (left panel), synchronized at 10fps
   OR
2. A joint angle sequence [T x 7] for a Franka Panda arm with timestamps

Evaluate along these dimensions and output JSON:

{
  "smoothness": {
    "score": 1-5,
    "justification": "string",
    "issues": ["jitter_frames_30-35", "sudden_accel_frame_78"]
  },
  "human_similarity": {
    "score": 1-5,
    "justification": "string",
    "deviations": ["elbow_higher_frames_40-55", "wrist_path_mismatch_end"]
  },
  "task_completion": {
    "score": 1-5,
    "justification": "string",
    "reached_target": true/false,
    "proper_grasp_orientation": true/false
  },
  "anomalies": [
    {"type": "joint_jump", "frame_range": [120, 121], "severity": "high"},
    {"type": "stuck_joint", "frame_range": [200, 230], "severity": "medium"}
  ],
  "overall_score": 1-5,
  "pass": true/false,
  "human_review_recommended": true/false,
  "review_reason": "string (if review recommended)"
}

CRITICAL: Before evaluating, state your expectations for a good trajectory 
given the task. Then compare the actual trajectory against those expectations. 
This is the SGV (self-grounded verification) technique to avoid agreement bias.

DO NOT approve a trajectory that:
- Teleports (joint jumps > 0.15 rad between consecutive frames)
- Contains NaN or invalid joint values
- Violates joint limits
- Shows the robot arm stationary while the human is moving
- Fails to reach the task target area
- Oscillates or shakes visibly
```

### 3.3 Cost and Latency

| Model | Cost per Evaluation | Latency | Visual Input |
|---|---|---|---|
| Claude Sonnet (Bedrock) | ~$0.02–0.05 | 3–8s (video) | Up to 20 frames as images |
| Gemini 2.5 Pro | ~$0.01–0.03 | 2–5s | Native video (30s clip) |
| GPT-4o | ~$0.03–0.06 | 4–10s | Up to 20 frames as images |

For the hackathon: **Gemini 2.5 Pro** is best since it natively accepts video input, eliminating the need to sample keyframes. Claude via Bedrock works well with 8 keyframe images.

### 3.4 Limitations of LLM Evaluation

1. **Agreement bias**: LLMs approve 60-80% of trajectories even when defective (SGV reduces this but doesn't eliminate it)
2. **Inconsistent scoring**: Same trajectory can get 3/5 one run and 5/5 the next (use temperature=0, but variance persists)
3. **Cannot validate physics**: No collision detection from video alone; no force/torque evaluation
4. **Task context required**: Needs explicit task description ("pouring water from pitcher into glass with right hand")
5. **Cost at scale**: LLM evaluation adds $0.01–0.06/video — negligible for hackathon, meaningful at 100K+ videos

---

## 4. Human-in-the-Loop Review Patterns

### 4.1 How Existing Platforms Handle Quality Review

| Platform | Review Model | Reviewer Pool | Review Trigger |
|---|---|---|---|
| **Scale AI PhysAI** | Expert operator review; dedicated QA workforce | In-house trained reviewers | Every trajectory |
| **Encord** | Task-specific review queues; consensus scoring | Client's team or Encord operations | Configurable thresholds |
| **DROID** | Self-review by data collectors; post-hoc filtering | Same collectors | Filtering by policy training failure |
| **Hivemapper** | AI pre-filter → community vote → expert arbitration | Token-incentivized community + core reviewers | AI flags only |
| **Mozilla Common Voice** | Volunteer moderation; per-clip review | Open community | Random sample + flagged clips |

### 4.2 Minimal Viable Review Interface (Hackathon)

**Single-page web app** with three panels:

```
┌─────────────────────────────────────────────────────────────┐
│  RoboData Trajectory Review                          [v0.1] │
├──────────────────────────┬──────────────────────────────────┤
│                          │                                  │
│   Original Human Video   │   Robot Trajectory Render        │
│   (30s, synced 10fps)   │   (Kaolin/NVIDIA, synced)       │
│                          │                                  │
│   [▶ Play/Pause]         │   [▶ Play/Pause]                │
│   [◀◀] [▶▶] 5s skip     │   [◀◀] [▶▶] 5s skip            │
│                          │                                  │
├──────────────────────────┴──────────────────────────────────┤
│  Playback: ●───────○────────○───────●  (0s / 30s)          │
├─────────────────────────────────────────────────────────────┤
│  Quality Scores                    Anomalies Detected        │
│  ┌──────────┬──────────┬────────┐ ┌────────────────────────┐ │
│  │Smoothness│ Similarity│Task OK │ │⚠ Frame 45: joint jump  │ │
│  │  ████░ 4 │  ███░░ 3  │███░░ 3│ │⚠ Frame 120: oscillation│ │
│  └──────────┴──────────┴────────┘ └────────────────────────┘ │
├─────────────────────────────────────────────────────────────┤
│  [✓ Accept]  [✗ Reject]  [↻ Flag for Review]  [Skip →]    │
│  Reason (if rejected): [___________________________]        │
└─────────────────────────────────────────────────────────────┘
```

**Reviewer workload per trajectory:** 30-60 seconds (watching synced video + marking decision)

### 4.3 What to Show the Human Reviewer

- **Must show:** Side-by-side video (human source + robot render), overall quality scores from automated metrics and LLM
- **Should show:** Anomaly markers on the scrubber bar (red for critical, yellow for warning), task classification, completeness %
- **Optional:** Joint angle plot overlay (too technical for non-roboticists, but useful for expert review)
- **Do NOT show:** Raw joint angle arrays, IK solver logs, MediaPipe confidence values (clutter; hide behind "Details" toggle)

### 4.4 Review Tier System

| Tier | Reviewer | Volume | Decision |
|---|---|---|---|
| **Tier 0: Auto-pass** | Automated metrics green + LLM confidence ≥ 4 | ~60–70% of trajectories | Accept without review |
| **Tier 1: Quick review** | Any human (crowd, non-expert) | ~20–30% | Side-by-side visual check, 30s |
| **Tier 2: Expert review** | Roboticist / team member | ~5–10% | Detailed inspection of flagged anomalies |
| **Tier 3: Reject** | Automated metrics red | ~1–3% | Auto-reject, re-process or re-capture |

---

## 5. Agent-Assisted Evaluation Workflow

### 5.1 Full Pipeline with Evaluation Gates

```
Phone Video (30s)
    │
    ▼
┌──────────────────────────────────────────────────┐
│  GATE 0: Input Quality (Orchestrator LLM)        │
│  - Lighting, blur, occlusion, frontal pose?      │
│  - Task classification confidence > 0.7?         │
│  - PASS → continue | FAIL → reject/re-record     │
└───────────────┬──────────────────────────────────┘
                │ PASS
                ▼
┌──────────────────────────────────────────────────┐
│  POSE: MediaPipe Pose (primary) or YOLO fallback │
│  - Detection confidence per frame                │
│  - Missing frame % < 5%?                         │
│  - Landmark jitter < 5cm?                        │
└───────────────┬──────────────────────────────────┘
                │
                ▼
┌──────────────────────────────────────────────────┐
│  GATE 1: Pose Quality (automated)                │
│  - frames_missing / total < 0.05                 │
│  - mean_landmark_jitter < 0.05m                  │
│  - PASS → IK | FAIL → YOLO fallback              │
└───────────────┬──────────────────────────────────┘
                │ PASS
                ▼
┌──────────────────────────────────────────────────┐
│  IK: pinocchio retargeting                       │
│  - IK convergence < 200 iters per frame          │
│  - Output joint trajectory [T × 7]               │
└───────────────┬──────────────────────────────────┘
                │
                ▼
┌──────────────────────────────────────────────────┐
│  GATE 2: Deterministic Metrics (automated)       │
│  - Joint limits: 0 violations                    │
│  - NaN count: 0                                  │
│  - Max velocity < 3 rad/s                        │
│  - Completeness > 0.75                           │
│  - Jerk score > 0.5                              │
│  - PASS → LLM review | FAIL → auto-reject        │
└───────────────┬──────────────────────────────────┘
                │ PASS
                ▼
┌──────────────────────────────────────────────────┐
│  GATE 3: LLM Visual Assessment (Gemini/Claude)   │
│  - Render robot trajectory video                 │
│  - SGV prompt: expectations + evaluation         │
│  - Overall score ≥ 3/5?                          │
│  - No critical anomalies?                        │
│  - Score 4-5 → auto-accept                       │
│  - Score 3   → human review Tier 1               │
│  - Score 1-2 → human review Tier 2 or reject     │
└───────────────┬──────────────────────────────────┘
                │
                ▼
┌──────────────────────────────────────────────────┐
│  GATE 4: Human Review (when flagged)             │
│  - Side-by-side video comparison                 │
│  - Accept / Reject / Flag decision               │
│  - Record reason for rejected trajectories       │
└───────────────┬──────────────────────────────────┘
                │ ACCEPT
                ▼
         LeRobot Dataset
```

### 5.2 What the Agent Flags for Human Attention

The agent (orchestrator + automated metrics) should flag trajectories when:

| Condition | Flag Level | Human Action |
|---|---|---|
| Automated metrics all green AND LLM score ≥ 4 | None (auto-accept) | No review needed |
| Automated metrics green but LLM score = 3 | Low (Tier 1) | 30s visual check |
| One yellow metric (e.g., 5-15 sudden jumps) | Medium (Tier 1) | Focused review of flagged frames |
| Multiple yellow metrics | High (Tier 2) | Expert review |
| LLM detects anomaly but metrics are green | High (Tier 2) | Resolve disagreement |
| Any red metric | Critical (reject) | Re-process pipeline or re-capture |

### 5.3 Flag Escalation Logic

```python
def determine_review_tier(automated_results: dict, llm_results: dict) -> str:
    autom = automated_results
    llm = llm_results
    
    # Hard rejects — no LLM or human needed
    if autom["nan_count"] > 0 or autom["joint_limit_violations"] > 0:
        return "TIER_3_REJECT"
    if autom["completeness_ratio"] < 0.75:
        return "TIER_3_REJECT"
    
    # Count yellow/red flags
    flags = 0
    if autom["sudden_jump_count"] > 5: flags += 1
    if autom["sudden_jump_count"] > 15: flags += 2
    if autom["oscillation_count"] > 3: flags += 1
    if autom["stuck_joint_frames"] > 3: flags += 1
    if autom["mean_jerk"] > 1.0: flags += 1
    if autom["max_joint_velocity"] > 2.0: flags += 1
    if autom["detection_failure_rate"] > 0.05: flags += 1
    
    llm_score = llm.get("overall_score", 3)
    llm_anomalies = len(llm.get("anomalies", []))
    
    # LLM + automated disagreement is a strong signal
    disagreement = (llm_score <= 2 and flags == 0) or (llm_score >= 4 and flags >= 3)
    
    if flags >= 5 or (disagreement and llm_score <= 2):
        return "TIER_3_REJECT"  # auto-reject
    if flags >= 3 or disagreement or llm_anomalies >= 3:
        return "TIER_2_EXPERT"  # expert review
    if flags >= 1 or llm_score <= 3 or llm_anomalies > 0:
        return "TIER_1_QUICK"   # quick human review
    return "TIER_0_AUTO_ACCEPT"
```

### 5.4 Hackathon-Scope Workflow

For the 4-day hackathon, implement:

1. **GATE 0** (already in orchestrator) — video quality gate
2. **GATE 2** (NEW, ~2 hours) — deterministic metrics in Python, output JSON
3. **GATE 3** (NEW, ~3 hours) — LLM visual assessment via Gemini/Claude, prompt engineering
4. **Review UI** (NEW, ~4 hours) — minimal Flask/Streamlit web app with side-by-side video
5. **GATE 4** — manual (team members review during demo)

**Skip for hackathon:** Tier 2 expert review pipeline, automated rendering pipeline (mock the render), consensus scoring across multiple reviewers.

---

## 6. Integration with RoboData Pipeline

### 6.1 Where Quality Gates Fit in MVP

```
scripts/
├── extract_frames.py        # Step 1: Video preprocessing
├── orchestrate.py           # Step 0: GATE 0 — LLM quality gate
├── pose_3d.py               # Step 2: MediaPipe Pose → GATE 1 (pose quality)
├── retarget.py              # Step 5-6: pinocchio IK
├── evaluate_metrics.py      # NEW: GATE 2 — Deterministic metrics
├── evaluate_llm.py          # NEW: GATE 3 — LLM visual assessment
├── review_server.py         # NEW: GATE 4 — Human review UI (Flask/Streamlit)
└── package.py               # Step 7: LeRobot packaging
```

### 6.2 Orchestrator Output Extension

Add to the existing orchestrator JSON schema:

```json
{
  "...existing fields...": "...",
  
  "trajectory_evaluation": {
    "automated_metrics": {
      "joint_limit_violations": 0,
      "nan_count": 0,
      "max_joint_velocity": 1.2,
      "sudden_jump_count": 2,
      "mean_jerk": 0.45,
      "oscillation_count": 1,
      "stuck_joint_frames": 0,
      "completeness_ratio": 0.98,
      "overall_grade": "green"
    },
    "llm_assessment": {
      "model": "gemini-2.5-pro",
      "smoothness_score": 4,
      "human_similarity_score": 3,
      "task_completion_score": 4,
      "overall_score": 4,
      "anomalies_detected": 1,
      "review_recommended": true,
      "review_tier": "TIER_1_QUICK"
    },
    "final_disposition": "pending_human_review",
    "evaluation_cost": 0.03
  }
}
```

### 6.3 Rendering Robot Trajectory Video

For LLM review, render the robot trajectory as video. Two options for hackathon:

**Quick hackathon approach (recommended):**
```python
# Use matplotlib animation — no GPU, no Kaolin, 2-minute setup
import matplotlib.pyplot as plt
import matplotlib.animation as animation
import numpy as np

def render_trajectory_quick(joint_trajectory, urdf_path, output_path):
    """Quick matplotlib-based robot arm rendering for LLM review."""
    # Forward kinematics per frame → end-effector + joint positions
    # Plot arm skeleton for each frame → mp4 output
    # Overlay on black background with grid
    pass
```

**Production approach (post-hackathon):**
Use NVIDIA Kaolin + 3DGS scene mesh for photorealistic rendering (see `regeneration-pipeline.md` Step 5).

### 6.4 Side-by-Side Video Generation

```python
def generate_comparison_video(human_video_path, robot_render_path, output_path):
    """Side-by-side: human video (left) + robot render (right), synchronized."""
    # Use ffmpeg hstack filter
    # ffmpeg -i human.mp4 -i robot.mp4 -filter_complex hstack comparison.mp4
    pass
```

---

## 7. References

### Papers
- **CriticGPT** — Liu et al., "Enhancing Robotic Manipulation with AI Feedback from Multimodal Large Language Models", AAAI 2024 RL+LLMs Workshop. [arXiv:2402.14245](https://arxiv.org/abs/2402.14245)
- **SGV / Agreement Bias** — Andrade et al., "Let's Think in Two Steps: Mitigating Agreement Bias in MLLMs with Self-Grounded Verification", ICLR 2026. [arXiv:2507.11662](https://arxiv.org/abs/2507.11662)
- **VLA Safety Survey** — Li et al., "Vision-Language-Action Safety: Threats, Challenges, Evaluations, and Mechanisms", arXiv 2026. [arXiv:2604.23775](https://arxiv.org/abs/2604.23775)
- **VLM-Guided Experience Replay** — Sharony et al., "VLM-Guided Experience Replay", arXiv 2026. [arXiv:2602.01915](https://arxiv.org/abs/2602.01915)

### Platforms
- **Scale AI Physical AI Engine**: Enterprise robotics data collection with expert QA review. https://scale.com/physical-ai
- **Encord Physical AI**: Full-stack data platform for physical AI with operator review queues.

### Project Documents
- `docs/mvp-pipeline.md` — Current MVP pipeline (GATE 0 already implemented)
- `docs/regeneration-pipeline.md` — Full production architecture with Kaolin rendering (Step 5)
- `docs/synthesis.md` — Executive summary and scope decisions
