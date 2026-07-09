# The Current Scene: Robotics Data, Competition, and Privacy Landscape

**July 2026** | Research synthesis for RoboData build challenge

---

## At a Glance

| Dimension | Key Finding |
|---|---|
| Data bottleneck | Robot VLAs need millions of demos at $1–$5 each; LLMs train on near-free web tokens |
| Largest open dataset | AgiBot World: 1M trajectories, ~$7M–$15M to collect |
| Few-shot era | RDT-1B fine-tunes with 1–5 demos; Octo with ~100 |
| Competitive gap | "Crowdsourced × Robotics-ready" quadrant is empty — no one does distributed participant-device collection for robotics |
| Closest analogue | Scale AI Physical AI Engine (enterprise, not crowdsourced) + Hivemapper (crowdsourced but driving-only) |
| Privacy risk | BIPA: $1K–$5K per captured face in Illinois; GDPR Art. 9: biometrics = special-category data |
| Regulatory timeline | EU AI Act enforcement phases in Aug 2026–2027; training-data provenance becomes mandatory |

---

## 1. The Data Problem

### The Asymmetry

| Dimension | LLMs | Robot VLAs |
|---|---|---|
| Training data source | Web (trillions of tokens) | Teleoperation, egocentric video, simulation |
| Marginal cost per sample | Near zero | $1–$5 / demo (human-in-the-loop) |
| Geographic/scene diversity | Global | Mostly pristine labs in a few universities |
| Behavior diversity | Near-infinite (web) | 217 tasks / 100 scenes (AgiBot World scale) |
| Model parameters | 7B–1T+ | 27M–55B |
| Key bottleneck | Compute (GPU) | Real-world data collection |

### What Data Robotics Needs

- **Teleoperation demos** — paired (observation, action) trajectories from human-controlled robots
- **Manipulation trajectories** — 7-DoF end-effector poses + gripper state per timestep
- **Egocentric video** — first-person RGB during tasks (HumanPlus used 40 hours)
- **VLA training triples** — (image, language instruction, action) for models like RT-2, Octo, Pi-0
- **Scene meshes** — 3D environments for sim-to-real and domain randomization
- **Contact/tactile signals** — force/torque during manipulation (AgiBot World includes these)

### Key Cost Figures

| Item | Cost |
|---|---|
| Single teleop demo (academic rate) | $1–$5 |
| Single teleop demo (minimum wage) | $0.50–$2 |
| ALOHA bimanual rig | ~$20K |
| Mobile ALOHA | ~$30K–$35K |
| DROID rig (Franka + cameras + Quest 2) | ~$45K–$60K |
| Franka Panda arm | ~$25K–$35K |
| DROID dataset (76K trajectories) | ~$1M–$3M total |
| AgiBot World (1M trajectories) | ~$7M–$15M |
| Pi-0 proprietary dataset | ~$5M–$20M+ |

### Pain Points

1. **Cost per demo** — human-in-the-loop at $1–$5 each; 50–100 demos needed per task for behavior cloning from scratch
2. **Time to collect** — DROID: 50 collectors × 12 months for 350 hours of data
3. **Sim-to-real gap** — cloth, fluids, contact-rich assembly, deformables are qualitatively different in sim vs. real; real data remains irreplaceable
4. **Embodiment mismatch** — Franka policy doesn't transfer to UR5e; cross-embodiment training needs per-robot fine-tuning data
5. **In-the-wild scarcity** — most datasets are pristine lab tabletops; real home diversity is essentially untouched
6. **Operator noise** — variation in teleoperation style confounds policy learning; GO-1-Pro debiasing gives gains equivalent to 2.5× more data

### The Few-Shot Era Changes Everything

| Model | Params | Fine-tuning demos needed | Key result |
|---|---|---|---|
| RDT-1B | 1.2B | 1–5 | 381 actions/sec; diffusion-based |
| Octo | 93M | ~100 | 52% higher success than next-best baseline |
| GO-1 | Undisclosed | ~70 (action expert only, 24 GB) | 30% improvement over Open-X; >60% on complex tasks |
| Pi-0 | 3B VLM + action expert | Undisclosed (proprietary) | 50 Hz flow matching; first autonomous laundry folding |

**Implication:** Every robotics team is now a potential buyer of small, diverse, task-structured datasets. The bottleneck shifted from "thousands of demos per task" to "tens of demos per task" — but those tens must be high-quality and diverse.

---

## 2. Competitive Landscape

### The Gap: Crowdsourced × Robotics-Ready

| | Crowdsourced | Enterprise |
|---|---|---|
| **Robotics-ready** (action-labeled, cross-embodiment) | **← EMPTY — RoboData opportunity** | Scale AI PhysAI Engine, Encord, Appen PhysAI, AgiBot World, HuggingFace/LeRobot, DROID, Open-X |
| **General CV** | Hivemapper, Mapillary, Ego4D | AWS Open Data, Kaggle, Roboflow, V7 |

### Key Players

| Player | What they do | Crowdsourced? | Robotics-ready? | Gap |
|---|---|---|---|---|
| **Scale AI** | Physical AI Data Engine; "Scale Harness" for egocentric collection; 1000+ hrs/day | Hybrid (hired workforce) | Yes (action-labeled) | Uses provided hardware, not participant devices |
| **Physical Intelligence** | VLA models (Pi-0); proprietary multi-robot dataset | No (internal fleet) | Yes | Closed data flywheel |
| **AgiBot World** | 1M+ trajectories open-sourced; GO-1 model | No (100-robot fleet) | Yes | Static dataset, no live contribution |
| **Encord** | Full-stack data platform for Physical AI | No (enterprise) | Yes | Users bring own data or use Encord operators |
| **Hivemapper** | Crowdsourced street-level mapping; 33% of global roads | Yes (DePIN/token) | No (driving imagery only) | No robotics relevance |
| **Ego4D** | 3,670 hrs egocentric video; 930 participants | Yes (academic) | Partial (no action labels) | One-shot study, project-provided cameras |
| **LeRobot / HF** | De facto robotics dataset standard (25.6k stars) | Open community | Yes (format/hosting) | Hosts data, doesn't generate it |
| **Appen** | 1M+ vetted contributors; Physical AI annotation | Crowdsourced workforce | Partial | Annotation/labeling, not collection |
| **Toloka AI** | "Data for robotics models" (actually digital agents) | Crowdsourced | No (agent data, not physical) | Not physical-world |

### What No One Does (as of July 2026)

- Continuous egocentric video from consumer smartphones for AI training
- Crowdsourced action-labeled manipulation demonstrations from participant devices
- Phone LiDAR/depth-based 3D capture at scale for robotics
- A data marketplace compensating individual contributors for robotics-relevant data
- Multi-modal sensor fusion from consumer devices for robotics datasets
- Privacy-respecting on-device processing before upload for training data

---

## 3. Regulatory & Privacy Backdrop

### Risk Matrix (no mitigation)

| Risk | Exposure | Likelihood |
|---|---|---|
| **BIPA class action** | $1K–$5K per face × millions of frames = existential | High (if US includes Illinois) |
| **GDPR enforcement** | Up to €20M or 4% global turnover | Medium-High |
| **CCPA data-breach** | $750/person/incident + damages | Medium |
| **EU AI Act non-compliance** | Prohibition from EU, fines up to 7% turnover | Medium (from Aug 2026–2027) |
| **Bystander discovery** | PR crisis, regulatory investigation | Medium |
| **Child in frame** | COPPA $50K/instance; GDPR Art. 8 | Medium |

### Key Regulations at a Glance

| Regulation | Jurisdiction | Key requirement for RoboData |
|---|---|---|
| **GDPR Art. 9** | EU | Biometrics = special-category; explicit consent required |
| **EU AI Act Art. 10** | EU (Aug 2026–2027) | Training-data provenance documentation mandatory for high-risk AI |
| **BIPA** | Illinois | Written consent + retention schedule; private right of action ($1K–$5K/violation) |
| **CCPA/CPRA** | California | Right to opt out of sale/sharing; biometrics = sensitive PI |
| **PIPL** | China | Separate consent per processing purpose; cross-border restrictions |
| **DPDP Act** | India | Consent-based; data fiduciary obligations |
| **COPPA** | US (federal) | Children under 13 need parental consent; $50K/violation |

### The Regeneration Advantage

The regeneration pipeline architecture (see `docs/regeneration-pipeline.md`) eliminates most privacy risk by construction:

| Data element | In raw capture? | In pipeline output? | Risk eliminated? |
|---|---|---|---|
| Face pixels | Yes | **No** (discarded after mesh recovery) | Yes |
| Skin color/texture | Yes | **No** (SMPL outputs geometry only) | Yes |
| Voice | Maybe | **No** (not processed) | Yes |
| Room identity | Yes | **Depends** (parametric mesh = safe; 3DGS = risk) | Partial |
| Skeleton joints | No | Yes | Low (non-identifiable) |

### Consent & Privacy Stack (MVP Blueprint)

| Component | Implementation |
|---|---|
| Consent ledger | Microservice keyed by contributor × dataset × purpose × timestamp; ISO 29184 receipts |
| Contributor app | Per-session consent before recording; granular toggles |
| On-device redaction | MediaPipe face detection + OpenCV blur before upload (defense-in-depth even with regeneration) |
| Provenance tracker | C2PA Content Credentials at capture; tamper-evident chain |
| Audit log | Append-only; required for GDPR Art. 30 and EU AI Act Art. 10 |
| Bystander complaint | Public form; PII removed within 72h of verified claim |

### Reference Blueprints

| Platform | Model |
|---|---|
| **Hivemapper** | Token-incentivized DePIN; HONEY per km; face/plate blur on ingestion |
| **Mozilla Common Voice** | CC-0; per-recording opt-in; volunteer moderator review |
| **Ego4D** | IRB-approved; per-clip DUA; tiered access; right to revoke |
| **Vana / Ocean Protocol** | Data DAO; compute-to-data; tokenized access |

---

## Appendix A: Dataset Profiles

### Open X-Embodiment / RT-X
- 1M+ trajectories, 22 embodiments, 527 skills, 60 datasets, 34 labs
- Modality: RGB, joint states, 7-DoF actions, language labels
- RT-1-X: 50% improvement over single-dataset models; RT-2-X (55B params): 3× emergent skill gain
- [arXiv 2310.08864](https://arxiv.org/abs/2310.08864)

### DROID
- 76K trajectories, 350 hours, 564 scenes, 84 tasks, 50 collectors × 12 months, 13 institutions
- Hardware: Franka Panda + 2× ZED 2 + ZED Mini + Quest 2; ~$45K–$60K per rig × 50 = ~$2.5M hardware
- Co-training: +22% absolute success in-distribution, +17% OOD
- [arXiv 2403.12945](https://arxiv.org/abs/2403.12945)

### BridgeData V2
- 60K trajectories (50K teleop + 10K scripted), 24 environments, 13 skills
- Hardware: WidowX 250 (~$5K–$10K per rig)
- Foundational for Octo pre-training
- [arXiv 2308.12952](https://arxiv.org/abs/2308.12952)

### AgiBot World
- **Largest open dataset:** 1,003,672 trajectories (~43.8 TB), 100 robots, 217 tasks, 5 domains
- Visual tactile sensors, 6-DoF dexterous hands, mobile dual-arm; LeRobot v2.1 format
- GO-1: 30% improvement over Open-X; >60% on complex tasks; outperforms RDT by 32%
- Estimated collection cost: $7M–$15M
- [arXiv 2503.06669](https://arxiv.org/abs/2503.06669)

### Pi-0 (Physical Intelligence)
- Proprietary: 8 robot types, hundreds of tasks, 50 Hz actions
- VLA: 3B VLM backbone + flow-matching action expert
- First autonomous laundry folding; ~97% bussing success
- Estimated cost: $5M–$20M+
- [physicalintelligence.company/blog/pi0](https://www.physicalintelligence.company/blog/pi0)

### RDT-1B (Tsinghua)
- 1.2B params; 46 datasets, 1M+ episodes pre-training + 6K ALOHA fine-tuning
- Few-shot: 1–5 demos for new skills; 381 actions/sec inference
- [rdt-robotics.github.io](https://rdt-robotics.github.io/rdt-robotics/)

## Appendix B: VLA Model Comparison

| Model | Params | Training data | Action space | Fine-tuning demos |
|---|---|---|---|---|
| RT-2-X | 55B | Open-X + VLM pre-train | Discretized tokens | N/A (Google internal) |
| Octo | 27M/93M | 800K episodes, 25 datasets | Normalized joint-space (multi-head) | ~100 |
| OpenVLA | 7B | Open-X + Prismatic-7B | Discretized action tokens | ~100 (LoRA) |
| Pi-0 | 3B + expert | Open-X + proprietary | Continuous delta EE (flow matching) | Undisclosed |
| RDT-1B | 1.2B | 46 datasets, 1M+ episodes | Unified action space | 1–5 |
| GO-1 | Undisclosed | 1M+ AgiBot World | Latent action | ~70 (action expert) |

## Appendix C: Simulation Platforms

| Simulator | Owner | License | Best for |
|---|---|---|---|
| Isaac Sim / Lab | NVIDIA | Apache 2.0 | GPU-parallel training, domain randomization |
| MuJoCo | Google DeepMind | Apache 2.0 | Fast contact dynamics, RL research |
| Newton | DeepMind + Disney + NVIDIA | Linux Foundation | Next-gen GPU physics for robotics |
| Genesis | — | — | High-speed parallel RL simulation |
| SAPIEN | — | Academic | Articulated object manipulation |
| Genie 2/3 | Google DeepMind | Closed | World model from video (research) |
| Cosmos | NVIDIA | Open model weights | World foundation models for physical AI |

**Why sim isn't enough:** Cloth, fluids, deformables, contact-rich assembly, and the long-tail of real-world physics (spills, crumbs, sticky grips) remain qualitatively different in sim vs. real. Pi-0's laundry folding dataset exists because simulation cannot fold shirts.

## Appendix D: Full Regulatory Detail

### GDPR (EU)
- Biometric data (faces, voice, gait, body geometry) = special-category under Art. 9; explicit consent required
- Art. 22 restricts automated decision-making; Art. 17 right to erasure; Art. 20 data portability
- Legitimate interest generally unavailable for special-category data

### EU AI Act (phased enforcement through Aug 2027)
- Art. 10: training-data provenance mandatory for high-risk AI
- Art. 50: transparency when AI processes biometric data
- Fines: up to €35M or 7% global turnover for prohibited practices

### Illinois BIPA
- Facebook $650M (2021), TikTok $92M (2025) settlements
- Written consent + retention schedule + private right of action ($1K–$5K per violation)
- Applies to ANY entity collecting biometrics in Illinois regardless of HQ location

### CCPA/CPRA (California)
- Applies to >$25M revenue or >100K residents' data/year
- Biometrics = sensitive PI; right to opt out of sale/sharing
- Training-data licensing could be interpreted as "sale"

### Consent Patterns
- **Dynamic consent** (Kaye et al., 2015): granular, revocable, per-purpose — recommended over clickwrap
- **Consent receipts** (ISO/IEC 29184): machine-readable records of who consented to what, when, for how long
- **Data dividends**: Vana (data DAO), OpenMined (federated), Ocean Protocol (compute-to-data)
- **Right to revoke**: contractual downstream-deletion obligations; clear retention windows; raw stream deletion after feature extraction

### Federated Learning Limitations for Robotics
- VLA models too large (3B–55B) for on-device fine-tuning
- Battery drain makes sustained participation unlikely
- Doesn't produce transferable datasets — only models
