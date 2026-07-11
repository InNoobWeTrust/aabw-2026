---
marp: true
theme: default
class: invert
paginate: true
backgroundColor: #0b0d10
color: #f5f7fa
header: "RoboData · AABW 2026 · Founder Mode Track"
footer: "Phone video → Robot dataset. No teleop rig, no GPU."
style: |
  section {
    font-family: 'Inter', 'Helvetica Neue', system-ui, -apple-system, sans-serif;
    font-size: 28px;
    padding: 56px 72px;
  }
  section.invert h1 {
    color: #7cf5c8;
    font-size: 56px;
    border-bottom: 3px solid #7cf5c8;
    padding-bottom: 12px;
  }
  section.invert h2 {
    color: #7cf5c8;
    font-size: 40px;
  }
  section.invert a {
    color: #7cf5c8;
  }
  section.invert strong {
    color: #ffd166;
  }
  section.lead {
    text-align: center;
  }
  section.lead h1 {
    border-bottom: none;
    font-size: 72px;
  }
  .small { font-size: 22px; opacity: 0.85; }
  .mono  { font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 22px; }
  .pipe  { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; font-size: 22px; }
  .pipe > span { background: #1a1f24; padding: 6px 12px; border-radius: 8px; border: 1px solid #2a3036; }
  .arrow { color: #7cf5c8; }
---

<!-- _class: lead -->
# ロボット転生 (Robot Reincarnation)

**Phone video → Robot training data.**
**No teleop rig. No robot fleet. No GPU.**

P6 · Physical World Data Layer · Founder Mode Track

<!--
Hi, we're [team]. We built RoboData. In the next 5 minutes:
a problem, a 60-second live demo, why it's agentic, why it's private,
and where it goes next.
-->

---

# Robots are data-starved. People are not.

> General-purpose robots need millions of trajectories.
> AgiBot World cost **$5M–$15M** to record 1M.
> A single teleop hour costs **$1–$5**.

Meanwhile **5 billion people** already carry the capture device in their pocket.

<!--
State the problem in one sentence BEFORE the solution. This is slide 1 — do not lead with tech stack.
Speak slowly on the dollar numbers. Pause after "in their pocket."
Rubric: Problem/Track Fit. P6: Physical World Data Layer.
-->

---

# What if a 30-second phone video *was* a training episode?

**Regenerate, don't collect.**

- Take the phone video
- Throw away the person
- Keep the 3D skeleton
- Retarget to a Franka arm
- Score it, ship LeRobot

> Upload → wait 2 min → download a zip your policy team can train on tonight.

<!--
The "aha" moment. 30 seconds. Land the inversion: we are not missing data,
we are missing an infrastructure layer.
Rubric: Creativity (regenerate vs collect) + Problem/Track Fit.
-->

---

# The pipeline

<div class="pipe">
  <span>Phone Video</span>
  <span class="arrow">→</span>
  <span>MediaPipe Pose</span>
  <span class="arrow">→</span>
  <span>3D Skeleton</span>
  <span class="arrow">→</span>
  <span>pinocchio IK</span>
  <span class="arrow">→</span>
  <span>5-Gate Eval</span>
  <span class="arrow">→</span>
  <span>LeRobot Dataset</span>
</div>

<div class="small">
Fallback chain: YOLO26-pose (2D) → 3D lifting, when local pose drops below threshold.
</div>

<!--
30 seconds. This is the map for the live demo — the judges will see the
same stages flip in the job card.
Rubric: Technical Execution.
-->

---

# Live demo

## Locally hosted (containerized deployment also available)

1. Open `http://<laptop-ip>:8080`
2. Drag-drop a 15-second phone clip
3. Watch the card flip: `queued → preprocess → pose → retarget → evaluate → package → done`
4. Open details → Check the evaluation and the agentic reasoning log
5. Download the .zip → Open to see the dataset in LeRobot format

<!--
60 seconds. REHEARSED. Do not narrate over silence.
If the live demo fails, open the pre-staged .zip on the desktop and
narrate: "this is what the live flow produced 30 seconds ago."
Rubric: Technical Execution (functional, credible, tested live) + Agentic AI Use
(stages are the orchestrator's next call).
-->

---

# Why this is *agentic*, not a wrapper

** Multiple agents. One SDK. Observable end-to-end.**

| Agent | Job |
|---|---|
| **Orchestrator (TBD)** | Decompose the job into staged calls. Plan the next action. Pick a fallback when a quality gate fails. |
| **Calibrator** | Second-pass review of low-confidence pose frames. Watch the segment. Propose corrected joint angles. |
| **Mapper (TBD)** | Multi-morphology retargeting (Franka → Aloha → UR5) via a configurable URDF registry. |

<!--
60 seconds. This is the slide the playbook says judges look at FIRST.
Do not abbreviate. If you are running long, cut the privacy slide, not this one.
Rubric: Agentic AI Use (planning + tool use + multi-step action).
-->

---

# Privacy by construction

**Regenerate the *scene*, not the *person*.**

- 33 3D landmarks in
- Everything else out
- No faces. No skin. No voice. No environment.
- Regenerate 3D videos using skeleton + object mesh, not the original pixels → Package as LeRobot dataset (Parquet + MP4) → Train on the policy team laptop.

> BIPA / GDPR face-capture liability is gone — by architecture, not by a redaction step we might forget.

<!--
30 seconds. Optional but cheap insurance against the BIPA/GDPR question in Q&A.
If you are over time, skip and answer it in Q&A instead.
Rubric: Impact + Track Fit (privacy-aware consent is explicit in the P6 brief).
-->

---

# What's next

The unit-economics flip for embodied AI.

1. **Multi-morphology retargeting** — Franka → Aloha → UR5, via a URDF registry
2. **Egocentric capture** — head-mounted cameras, much better hand-object signal
3. **Contributor marketplace** — upload, opt in to the public pool, earn credits
4. **Hosted multi-tenant** — real billing surface for buyers and sellers

> The bottleneck in embodied AI is not model architecture. It is **data supply.**

<!--
45 seconds. The playbook says: "show you're thinking past the hackathon."
This is that slide. Land the line about data supply — it ties back to slide 1.
Rubric: Impact / Usefulness.
-->

---

<!-- _class: lead -->
# Phone out. Capture. Upload. Train tonight.

**github.com/InNoobWeTrust/aabw-2026**

Thank you. Questions?

<!--
15 seconds. Say the elevator pitch one more time, verbatim. Then stop talking.
The silence is the transition. The 1-minute transition timer starts here.
Rubric: Pitch / Demo Clarity (memorable close).
-->

---

<!-- _class: lead -->
# Appendix

Speaker notes, Q&A defenses, and the pre-pitch checklist.

---

# Timing budget (5:00 hard cap)

| # | Slide | Time | Cumulative |
|---|---|---|---|
| 1 | Problem | 0:45 | 0:45 |
| 2 | Aha moment | 0:30 | 1:15 |
| 3 | Pipeline | 0:30 | 1:45 |
| 4 | Live demo | 1:00 | 2:45 |
| 5 | Agentic | 1:00 | 3:45 |
| 6 | Privacy | 0:30 | 4:15 |
| 7 | What's next | 0:30 | 4:45 |
| 8 | Close | 0:15 | **5:00** |

> **Cut order:** privacy → what's next → pipeline. Never cut the *agentic* slide.

---

# Q&A defenses

### "How is this agentic and not just a CV pipeline?"
> Orchestrator plans the next stage call from the previous one's output, picks fallbacks, retries. Calibrator re-examines low-confidence frames with reasoning. Both behind one SDK, traced in Langfuse. Planning + tool use + multi-step action.

### "What's the actual pass rate?"
> Just experimental stage, not tested on enough data, but if solved correctly (with the help of advanced LLM models as annotator / data mapper) then we can solve the incentive economics of crowdsourcing data for robotics.

---

# Q&A defenses (cont.)

### "BIPA / GDPR?"
> Never store the person. 3D landmarks in, everything else out. Privacy is structural, not a redaction step.

### "MediaPipe struggles with fingers."
> Agreed — egocentric capture is the roadmap. MVP signal drives a Franka end-effector trajectory, which is what the policy actually consumes.

### "How long per job?"
> ~2 minutes for a 30-second clip. MediaPipe local, pinocchio CPU. Agent calls are the bottleneck, one retry path.

---

# Q&A defenses (cont.)

### "Can it scale?"
> MVP is one worker, filesystem durable. Horizontal scaling = a Redis swap behind the same `JobStore` interface. The boundary is already drawn.

### "Why LeRobot?"
> 25.7k stars, Apache 2.0, working `lerobot-train`. Export layer is pluggable.

### "Did you use partner tools honestly?"
> Featherless + Kimi is the primary LLM path for both agents. OpenAI SDK is the integration. MediaPipe, pinocchio, YOLO, Hunyuan3D are documented fallbacks behind quality gates.

---

# Q&A defenses (cont.)

### "What would you build next week?"
> Egocentric capture + a real marketplace loop (upload, opt-in, earn credits, train on the pool). The MVP is the engine; the marketplace is the product.

### "Show me a failed job."
> `data/jobs/<id>/events.jsonl` — every stage writes a structured log; orchestrator reasoning and fallback decisions are timestamped.

---

# Pre-pitch checklist

The morning of, in this order:

- [ ] `uv sync --extra dev`
- [ ] `cp .env.example .env` — fill `JUDGE_ACCESS_PASSWORD`, `ADMIN_ACCESS_PASSWORD`, `JWT_SECRET_KEY`
- [ ] `uv run uvicorn backend.server:app --host 0.0.0.0 --port 8000`
- [ ] Open `http://<laptop-ip>:8000` from a *second* device (the phone) to prove LAN bind
- [ ] Run the full upload flow **twice** on the prepared clip — once to warm caches, once for the real demo
- [ ] Pre-stage the warm-up `.zip` on the desktop as a live-demo fallback
- [ ] Close every other camera/mic/CPU-heavy app
- [ ] Rehearse the 5-min script out loud, with a timer, three times. Cut anything over 5:00.
- [ ] **Record a backup demo video** of the working flow — wifi *will* hiccup

---

<!-- _class: lead -->
# Round 2 (if you advance)

- 12 min total. Same structure, two extensions.
- **+2 min** "Why now" — the embodied-AI capex story, the unit-economics flip
- **+2 min** Business model — marketplace, per-episode price floor vs. teleop cost, contributor credit loop
- Trim the live demo to 90 s. Pre-stage the .zip. **One owner narrates the whole thing — no mid-pitch handoffs.**
