# Track

Founder Mode Track powered by GenAI Fund

## Problem statement

P6: Physical World Data Layer

## * Project title

```
ロボット転生 (Robot Reincarnation)
```

## * Elevator pitch

```
Turn a 30-second phone video of someone doing a task into a LeRobot-format training dataset for robot manipulation — no teleop rig, no robot fleet, no GPU. Upload, wait two minutes, download a zip your policy team can train on tonight.

---

*Crowdsourcing economics*:
- If you contributed data to a crowdsourced collection and were approved, then you gain access to the collection for free. Collections available thanks to our sponsors.
- If you want your data to be private, then help us pay a small processing fee.
- If you want, you can sell your private collections to buyers in the marketplace (coming soon).

---

"Sell a kidney if you must, but keep your waifu!"
```

## Project Story

* About the project (Be sure to write what inspired you, what you learned, how you built your project, and the challenges you faced. Format your story in Markdown, with LaTeX support for math.)

```
## What it does
RoboData is a self-service web platform that ingests short phone videos of human manipulation tasks (pick, place, pour, stack) and regenerates them as robot-ready training data.

The pipeline runs six stages end-to-end: MediaPipe Pose extracts 33 3D landmarks, pinocchio solves inverse kinematics to retarget the human skeleton to a Franka Panda arm, a 5-gate quality evaluator scores kinematic feasibility, smoothness, and task completion, and the result is exported as a LeRobot dataset (Parquet episodes + MP4 videos) ready for `lerobot-train`.

The system is backed by filesystem-durable persistence under `data/jobs/<id>/`. We deliberately regenerate the *scene* (3D skeleton) rather than the *person*: no faces, no skin, no voice, no identifiable environment ever leave the upload. This makes the privacy posture structural, not a post-hoc redaction step.

### Inspiration
AgiBot World cost $5M–$15M to produce 1M trajectories. A single teleop hour costs $1–$5. Yet billions of people already carry a camera that captures exactly the motion signal a manipulation policy needs. We asked: what is the minimum we can do on a laptop to close that gap?

## How we built it
1. Studied prior winners of agentic and physical-AI hackathons to understand what a credible one-week build looks like.
2. Picked the narrowest slice that is technically defensible: phone video → 3D skeleton → IK → LeRobot. No 3D scene mesh, no multi-robot, no realtime — defer all of it.
3. Built a modular monolith (FastAPI + worker + static frontend) with a strict `backend / pipeline / domain` boundary and modularized approach so we could swap components without affecting the rest of the system.
4. "MVP first, scale later" — every stage has a working primary path and a clearly documented fallback..

### Challenges we ran into
- **Skeleton fidelity.** MediaPipe produce landmarks that are sometimes inaccurate or missing. We added a per-landmark visibility threshold and an LLM-driven "agent calibrator" that re-examines low-confidence frames and proposes corrected joint angles before IK is solved.
- **IK instability.** Standard pinocchio retargeting can explode easily. We added a 5-gate quality evaluator that checks for joint limits, self-collision, etc..
- **Other technical issues.**: queue durability, retries, LLM request thottled or timeout, and the usual web app deployment issues. 

### What we learned
The biggest lesson: the bottleneck in embodied AI is not model architecture — it is *data supply*. A regeneration pipeline that turns one phone clip into one training episode in under two minutes changes the unit economics of the whole field. We also learned that "agentic" in production means boring things: retries, idempotency, durable state, and a single integration point for the model. That is what we built.

### What's next
- Multi-morphology retargeting (Franka → Aloha → UR5) using agentic mapping and a multi-robot dataset format.
- A contributor marketplace: people upload, opt into the public pool, and earn credits for datasets they can later train on.
- Egocentric (head-mounted) capture support, which gives a much better hand-object signal than a third-person phone shot.
- Hosted multi-tenant mode with a real billing surface for buyers and sellers in the marketplace.
```

* Built with (Select the tools, frameworks, platforms, cloud services, databases, APIs, or models you used. Press Enter to add a custom tool.)

```
#FastAPI #uvicorn #Pydantic #MediaPipe #pinocchio #OpenAI-Compatible #LeRobot #Docker #uv #Ruff #pytest #JavaScript #HTML #CSS
```

## Links and media

* Demo URL

```
Run locally — see README Quick Start: `uv sync --extra dev && uv run uvicorn backend.server:app --host 0.0.0.0 --port 8000` then open `http://localhost:8000`. No hosted demo URL — the app runs on a laptop.
```

* GitHub / repository URL

```
https://github.com/InNoobWeTrust/aabw-2026
```

* Video demo link

```
https://raw.githubusercontent.com/InNoobWeTrust/aabw-2026/main/docs/pitch/media/Screen%20Recording%202026-07-12%20at%2004.04.43.mov
```

* Image gallery (JPG, PNG or GIF format, 5 MB max file size. For best results, use a 3:2 ratio. Up to 15 images.)

```
https://raw.githubusercontent.com/InNoobWeTrust/aabw-2026/main/docs/pitch/media/Screenshot%202026-07-12%20at%2004-02-56%20RoboData%20%E2%80%94%20Phone%20Video%20%E2%86%92%20Robot%20Dataset.png

https://raw.githubusercontent.com/InNoobWeTrust/aabw-2026/main/docs/pitch/media/Screenshot%202026-07-12%20at%2004-03-52%20RoboData%20%E2%80%94%20Phone%20Video%20%E2%86%92%20Robot%20Dataset.png
```

* Which AABW technology partner tools, platforms, or services did your team use in your project? (Select at least one AABW technology partner tool, platform, or service your team used. Some partners may reward teams based on the stacks used, so be clear, specific, and honest.)

```
#Featherless #Kimi
```

* Briefly explain how you used each technology partner's tools, platform or services. (Explain how you used the partner technologies you selected above.)

```
Primary LLM path runs through Featherless or Kimi provider (with option to switch to any compatible provider) for review/mapping agents:

1. **Agent calibrator** — a second-pass agent that reviews low-confidence pose frames flagged by the 5-gate evaluation, check the data result of the initial pose estimation, and proposes corrected joint angles or motion-blur markers before IK is re-solved.

2. **Agent mapper** — a second-pass agent that reviews the IK result and proposes a retargeted motion for a different robot morphology (Franka → Aloha → UR5) using a multi-robot dataset format.

All LLM calls go through a single `backend/llm_client.py` adapter built on the official OpenAI Python SDK pointed at the API endpoint, so we get one billing surface and one retry path for both agents.
```
