# Regeneration Pipeline: Phone Video to Robot Policy Data

**July 2026** | Core architecture for RoboData

> "We don't need the face or coloring for the robot. What we need is the 3D shape associated with movements."

---

## At a Glance

| Dimension | Redaction-first (old) | Regeneration-first (new) |
|---|---|---|
| Privacy | Blur faces (fragile, liable) | **No faces in output** (by construction) |
| Quality | Limited by phone sensor | **Limited by model** (often better) |
| Embodiment | One robot per pipeline | **Any robot** (swap URDF) |
| Contributor HW | Phone + UMI gripper + IMU | **Phone only** |
| Format | Fragmented by device | **Unified**: SMPL skeleton + scene mesh |

**Pipeline status:** 5 of 7 stages production-ready with MIT/Apache 2.0 tools. The 2 research-frontier stages (scene privacy, world model) can be deferred.

---

## Pipeline Overview

```
Phone video ──┬── [Step 1] Human Mesh Recovery ──────── ✅ SOLVED
              │     HybrIK-X (MIT) + PHALP tracking
              │     → SMPL-X skeleton [T × 55 × 3]
              │
              ├── [Step 2] Scene Reconstruction ──────── ✅ SOLVED (visual)
              │     COLMAP + 3DGS / Nerfacto + Depth-Anything-V2
              │     → Textured 3D mesh + camera poses
              │
              ├── [Step 3] Object State Extraction ───── ⚠️ PARTIAL
              │     FoundationPose (NVIDIA) + SAM2
              │     → Per-object 6-DoF pose [T × N × 7]
              │
              ├── [Step 4] Skeleton → Robot Retargeting ─ ✅ SOLVED
              │     pinocchio IK + robot URDF
              │     → Robot joint trajectory [T × J]
              │
              └── [Step 5] Render + Package ───────────── ✅ SOLVED
                    NVIDIA Kaolin + LeRobot
                    → LeRobot dataset (Parquet + MP4)
```

## Pipeline Status

| Stage | Status | Key Tool | License | Gap |
|---|---|---|---|---|
| 1. Human mesh recovery | ✅ Solved | HybrIK-X + PHALP | MIT | SMPL weights need MPII commercial license |
| 2. Scene reconstruction | ✅ Solved (visual) | COLMAP + Nerfacto/3DGS + Depth-Anything-V2 | BSD / Apache 2.0 | Texture anonymization unsolved |
| 3. Object state extraction | ⚠️ Partial | FoundationPose + SAM2 | NVIDIA / Apache 2.0 | Needs CAD templates; not fully automatic |
| 4. Skeleton → robot retargeting | ✅ Solved (single arm) | pinocchio IK + URDF | BSD | No universal model; per-robot config (~1 day) |
| 5. Render + package | ✅ Solved | LeRobot + Kaolin | Apache 2.0 | — |
| 6. Scene privacy anonymization | ⛔ Research | — | — | No tool replaces textures while preserving geometry |
| 7. Physics world model | ⛔ Research | Cosmos / Genie (closed) | — | Outputs pixels, not structured state |

---

## Step Details

### Step 1: Human Mesh Recovery

**Input:** RGB video frames (720–1080p, 30fps)
**Model:** HybrIK-X (MIT) + PHALP temporal tracking; alternative: HMR2.0 / 4D-Humans (MIT)
**Output:** SMPL-X parameters per frame:
- Body pose: 23 joints × 3 axis-angle = 69 DoF
- Hand pose: 15 joints × 2 hands = 30 joints (MANO)
- 3D joint positions: [T × 55 × 3]
- Camera pose per frame
**Compute:** ~2–3 min for 30s video on A100 GPU (server-side)
**Privacy:** Output contains NO face pixels, NO skin color, NO voice. Only abstract skeleton geometry.

### Step 2: Scene Reconstruction

**Input:** Same RGB video + optional ARKit LiDAR mesh
**Models:** COLMAP (BSD) → 3DGS/Nerfacto (Apache 2.0) + Depth-Anything-V2 depth priors + SAM2 object segmentation
**Alternative:** ARKit RoomPlan → parametric room mesh (iOS LiDAR only, real-time)
**Output:** Textured 3D scene mesh (.obj/.glb) + camera poses; objects segmented as separate meshes
**Compute:** 30–60 min on A100; ARKit RoomPlan is real-time on-device

**Privacy options:**
- **Option A:** Photorealistic 3DGS — highest quality but may retain identifiable scene textures
- **Option B:** ARKit RoomPlan parametric mesh only — walls/floor/furniture as geometry, no identifiable detail ← **recommended for MVP**
- **Option C:** Texture replacement via style transfer — research frontier

### Step 3: Object State Extraction

**Input:** RGB video + SAM2 object masks
**Model:** FoundationPose (NVIDIA, CVPR 2024) — 6D object pose from RGB + optional depth
**Output:** Per-frame object state [T × N_objects × 7] (x, y, z, quaternion)
**Gap:** Requires CAD models or templates for best results; not fully automatic for arbitrary household objects

### Step 4: Skeleton → Robot Retargeting

**Input:** SMPL wrist joint poses + object states
**Model:** pinocchio IK solver (BSD) + target robot URDF; DexMimicGen for dexterous hands

**For 7-DoF arm (e.g. Franka Panda):**
1. Extract wrist 3D position + orientation from SMPL-X joints
2. Define end-effector target = wrist pose
3. Solve IK per frame → robot joint angles [T × 7]
4. Apply joint limits, velocity smoothing, collision checking
5. Derive gripper state from MANO finger angles

**For other morphologies:**
- Bimanual: map left+right wrists → left+right end-effectors
- Dexterous hand: MANO → Allegro/Shadow via fingertip optimization (AnyTeleop approach)
- Humanoid: learned RL retargeting (HumanPlus approach)

**Compute:** CPU, <1s per trajectory
**Adding a new robot:** ~1 day per URDF config

### Step 5: Render + Package

**Input:** Robot joint trajectory + scene mesh + robot model
**Models:** NVIDIA Kaolin (Apache 2.0) for rendering; LeRobot (Apache 2.0) for packaging

**Output (LeRobot format):**
```
observation.state:      [robot_joint_positions, gripper]
action:                 [target_joint_positions, gripper_action]
observation.images.cam_high:  RGB from robot head camera
observation.images.cam_wrist: RGB from robot wrist camera
episode_metadata:       {task, scene_type, quality_score}
```

Rendered images show robot arm in reconstructed scene. NO human body, NO faces, NO environment identity.

---

## Why This Architecture

### Privacy is structural, not procedural
The regeneration pipeline discards identity by construction. No face-blur model to fail, no bystander detection to miss. The output is a skeleton — a wireframe mannequin. This eliminates BIPA/GDPR face-capture liability at the architectural level.

### Quality ceiling is the model, not the sensor
HybrIK-X can reconstruct accurate 3D pose from blurry, poorly-lit, partially-occluded video. The model is better than the sensor.

### One capture → any robot
The skeleton is embodiment-agnostic. A single video produces datasets for Franka, UR5e, Allegro, or humanoid — just swap the URDF in Step 4. No competitor offers this.

### Contributor friction → zero
Just a phone. No gripper, no wearable, no extra hardware. A 30s video of someone pouring water is sufficient input.

### Scene data is a free byproduct
The 3D scene mesh from Step 2 is independently valuable for sim-to-real training environments (Isaac Sim, MuJoCo).

---

## MVP Scope

| Component | Status | Effort |
|---|---|---|
| Human mesh recovery (HybrIK-X) | Ready | ~2–3 days |
| Scene reconstruction (RoomPlan or COLMAP+3DGS) | Ready | ~3–5 days |
| Skeleton → robot retargeting (pinocchio) | Ready | ~1–2 days per URDF |
| Robot POV renderer (Kaolin) | Ready | ~2–3 days |
| LeRobot packaging | Ready | ~1 day |
| Scene privacy anonymization | Research | Not in MVP |
| Object state extraction (FoundationPose) | Optional stretch | ~3–5 days |

**MVP demo:** Record 30s phone video of pouring water → upload → receive LeRobot dataset for Franka Panda. No face, no identity, no environment specifics.

**Default robot target:** Franka Panda (7-DoF) — academic standard. UR5e as fast-follow.

---

## Open Questions

1. **Latency:** Steps 1–5 take ~5–15 min on A100 per 30s video. Acceptable for UX?
2. **Rendered image quality:** Synthetic robot-POV images — are VLAs robust to this? (Pi-0 results suggest yes with domain randomization.)
3. **SMPL licensing:** MPII non-commercial license for model weights. Need commercial license or HybrIK-X joint-only path.
4. **Scene privacy for v1:** Option B (RoomPlan parametric mesh) is safest but least detailed. Sufficient for hackathon.
5. **Policy training validation:** Must prove regenerated data actually trains working policies: 5–10 episodes → ACT fine-tune → >50% sim success.
6. **Which robot first:** Franka Panda or UR5e? Defines initial market.

---

## Defensibility

1. **Composition is novel.** Each sub-model is open-source but no one has published this pipeline end-to-end.
2. **Network effects.** More contributors → more diverse skeletons × scenes × tasks → more buyers → more contributors.
3. **Cross-embodiment advantage.** Single video → any robot. Scale AI uses robot-specific collection; UMI uses specific gripper; AgiBot uses their fleet. We are the only path from phone video to YOUR robot.
4. **Regulatory moat.** When BIPA/GDPR enforcement hits robotics data, platforms with raw face capture face existential liability. Our pipeline never stores identity data.

---

## Sources

- HybrIK-X (MIT): github.com/Jeff-sjtu/HybrIK
- HMR2.0 / 4D-Humans (MIT): github.com/shubham-goel/4D-Humans
- SMPL / SMPL-X: Loper et al. 2015, Pavlakos et al. 2019 (MPII)
- 3DGS: Kerbl et al., SIGGRAPH 2023; github.com/graphdeco-inria/gaussian-splatting
- Depth-Anything-V2 (Apache 2.0): Yang et al., 2024
- SAM2 (Apache 2.0): Ravi et al., Meta 2024
- FoundationPose: Wen et al., CVPR 2024 (NVIDIA); github.com/NVlabs/FoundationPose
- pinocchio (BSD): github.com/stack-of-tasks/pinocchio
- Kaolin (Apache 2.0): github.com/NVIDIAGameWorks/kaolin
- LeRobot (Apache 2.0): github.com/huggingface/lerobot (25.7k stars)
- AnyTeleop: Qin et al., RSS 2023
- HumanPlus: Fu et al., CoRL 2024
- DexMimicGen: Jiang et al., ICRA 2025
- RDT-1B: arXiv:2410.07864
- Octo: arXiv:2405.12213
- GO-1: arXiv:2503.06669
- H-RDT: arXiv:2507.23523
