# RoboData: Research Synthesis & Recommendation

**July 2026** | Executive decision document for the build challenge

**Companion documents:**
- `docs/mvp-pipeline.md` — MVP pipeline with hosted APIs (hackathon build plan) ← **START HERE for hackathon**
- `docs/quality-evaluation-strategy.md` — quality evaluation gates (automated + LLM + human review)
- `docs/regeneration-pipeline.md` — full regeneration-first architecture (production vision)
- `docs/current-scene.md` — data needs, competitive landscape, regulatory backdrop
- `docs/capture-tech.md` — consumer device sensor capabilities & robotics-readiness
- `docs/problem_statement/Physical World Data Layer.md` — original problem statement

---

## The Problem

Robotics and embodied AI are bottlenecked by **real-world interaction data**. Every demo costs $1–$5 in human time. Frontier datasets cost $5M–$15M. The annual global supply of all robot manipulation data is likely <100M trajectories — orders of magnitude short of where embodied AI needs to be.

## The Opportunity

The "crowdsourced × robotics-ready" quadrant of the competitive landscape is **empty**. No one does distributed participant-device-based collection with action labeling and robotics-ready export. The closest analogue (Scale AI) uses enterprise hardware, not participant phones.

## Why Now

1. **~1.5B+ smartphones** have depth-capable sensors (LiDAR, ARCore depth, TrueDepth)
2. **Human video → robot policy** is validated (UMI, HumanPlus, Physical Intelligence)
3. **LeRobot** (25.6k stars) is the de facto dataset standard
4. **Few-shot fine-tuning** (RDT-1B: 1–5 demos; Octo: ~100) makes every robotics team a dataset buyer
5. **Hivemapper** proved the DePIN crowdsourcing model for physical-world data

---

## Recommendation: Regeneration Pipeline

**Build "Phone video → Robot policy data" using a generative 3D reconstruction pipeline.**

A contributor records a 30s phone video of a task. The platform runs a regeneration pipeline that extracts skeleton geometry and scene mesh, retargets to any target robot, and outputs a LeRobot-format dataset. No faces, no identity, no environment specifics in the output.

| Dimension | UMI-as-a-Service (old) | Regeneration Pipeline (recommended) |
|---|---|---|
| Contributor hardware | Phone + UMI gripper + IMU | **Phone only** |
| Privacy | Face blurring (procedural) | **No faces in output** (structural) |
| Quality ceiling | Phone sensor | **Model** (often better) |
| Embodiment | One robot per run | **Any robot** (swap URDF) |
| Dataset value | Single-embodiment | **Cross-embodiment** |

**Pipeline:** Phone video → HybrIK-X skeleton → COLMAP/3DGS scene → deterministic baseline retarget (with future profile-driven calibration) → LeRobot dataset

**Hackathon MVP:** Phone video → MediaPipe Pose / fallback pose → deterministic baseline retarget → LeRobot dataset, with an incremental roadmap toward an **agentic mapping calibrator** that improves retargeting by proposing structured mapping profiles rather than generating raw trajectories. See `docs/mvp-pipeline.md` and `docs/specs/agentic-mapping-calibration.md`.

**Status:** 5 of 7 pipeline stages are production-ready with MIT/Apache 2.0 tools today. See `docs/regeneration-pipeline.md` for full architecture.

### MVP Scope

| Component | Status | Effort |
|---|---|---|
| Human mesh recovery (HybrIK-X) | Ready | ~2–3 days |
| Scene reconstruction (RoomPlan or 3DGS) | Ready | ~3–5 days |
| Skeleton → robot retargeting baseline | Ready | ~1–2 days per URDF |
| Robot POV renderer (Kaolin) | Ready | ~2–3 days |
| LeRobot packaging | Ready | ~1 day |
| Scene privacy anonymization | Research | Not in MVP |
| Object state extraction | Optional | ~3–5 days |

**Demo:** Record 30s phone video → upload → LeRobot dataset for Franka Panda → fine-tune ACT policy in simulation.

---

## Open Questions

1. **Latency:** 5–15 min on A100 per 30s video — acceptable for contributor UX?
2. **Rendered image quality:** Are VLAs robust to synthetic robot-POV renders? (Pi-0 evidence suggests yes.)
3. **Default robot target:** Franka Panda (academic standard) or UR5e?
4. **Mapping calibration scope:** How much correction should come from deterministic profile tuning versus bounded agentic calibration?
5. **SMPL licensing:** MPII non-commercial; need commercial license or HybrIK-X joint-only path
6. **Scene privacy for v1:** RoomPlan parametric mesh (safest) vs. 3DGS (richest)?
7. **Policy validation:** Must prove regenerated data trains working policies (>50% success in sim)

---

## Non-Goals

- Robot teleoperation (we're a data layer, not a control system)
- Real-time policy training (we produce datasets, not deployed policies)
- Multi-embodiment retargeting in the capture app (done centrally in pipeline)
- Full agent-generated dense robot trajectories as training truth (bounded mapping calibration may tune deterministic retargeting, but does not replace it)
- Tokenomics / data dividend system in v1 (volunteer contributors + CC-BY license)
- Wearables / smart glasses / VR capture in v1 (phone-only; device-agnostic architecture)
- Bystander consent capture (regeneration eliminates bystanders by construction)

---

## Risks

| Risk | Mitigation |
|---|---|
| Regeneration quality insufficient for policy training | Validate with 5–10 episodes → ACT fine-tune → sim success rate |
| SMPL commercial licensing blocks launch | Verify HybrIK-X joint-only path; budget MPII license |
| Scene reconstruction leaks environment identity | Use RoomPlan parametric mesh (no textures) for v1 |
| No robotics team trusts crowdsourced data | Open-source pipeline; partner with academic lab for validation |
| Cold start — no contributors | Recruit from local robotics meetups; accept lower quality bar transparently |
| Scope creep to "full platform" | Hard boundary: v1 = one task (pouring) end-to-end. Everything else is roadmap. |

---

## What To Do Next

1. Decide on regeneration pipeline as MVP scope
2. Confirm Franka Panda as default robot target + pouring as reference task
3. Validate end-to-end proof-of-concept: phone video → HybrIK-X → deterministic baseline retarget → LeRobot → ACT fine-tune in sim
4. Implement quality evaluation gates (automated metrics + LLM visual assessment)
5. Move to product spec / requirements
