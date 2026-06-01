# CONTEXT

Ubiquitous language for the medication-delivery robot system. Glossary only —
no implementation detail. When code and this file disagree on a term's meaning,
this file wins; fix the code's naming or fix this file, don't let them drift.

## Domain

- **Medication delivery** — the end-to-end task: navigate to pharmacy, pick the
  medication, navigate to the patient, verify the patient's identity, hand over,
  return to origin. The product's reason to exist.
- **Patient** — the person who receives the medication. Identified by name; their
  name is **verbally confirmed** at delivery before handover (see below — this is
  self-attestation, not identity verification).
- **Medication** — the item picked at the pharmacy and handed to the patient. Note:
  the system does **not** verify the picked item *is* the right drug — it trusts the
  pharmacy location to hold the correct medication.
- **Verbal name confirmation** — the gate before handover. The robot asks the person
  to say their name and checks the patient's name appears in the reply. This is
  **self-attestation, not identity verification**: anyone who knows (or guesses) the
  patient's name passes — there is no biometric, badge, or face check. Retries up to
  3 times, then the task fails. (The code historically calls this "identity
  verification" / `identity_verified`; that name overstates the guarantee.)

## Control planes (ways to drive the robot — not interchangeable)

- **Workflow** — the *scripted* control plane. A fixed LangGraph DAG (9 nodes,
  order fixed at compile time). Deterministic. This is what A2A callers invoke
  (`mode="auto"`) and what the dashboard runs (`mode="manual"`). "The workflow"
  always means this DAG.
- **Agent** — the *LLM-driven* control plane. A ReAct loop that picks tool calls
  per turn instead of following a DAG. Same robot Skills, different controller.
  "The agent" never means "the workflow." Off by default (`LLM_PROVIDER=none`).
- **Teleop** — direct *human* control via a WebSocket relay (browser → backend →
  robot), bypassing both autonomous planes. Not a third autonomous mode — the manual
  override. While teleop is active, autonomous nav is stood down.
- **Manual vs Auto mode** — a Workflow run's `mode`: **manual** (dashboard-driven —
  the patient's name reply comes from the browser via `browser_input`) vs **auto**
  (A2A-driven — the reply comes from the robot's on-device ASR). Same DAG; only the
  human-input source differs.

## Robot capabilities

- **Skill** — one robot capability exposed to both control planes: `navigate`,
  `grasp`, `speak`, `listen`, `handover`. Sourced from the `cure` library, spoken
  to the robot over ZMQ. Not to be confused with Claude Code "skills."
- **DRY_RUN** — execution that validates and logs but does not move hardware. A
  Skill in dry-run reports its success criteria without touching the robot.
- **RobotGuard** — the **Agent's** safety bound: deterministic precondition checks
  (e.g. may-navigate, location tracking) plus a per-task tool-call budget, enforced
  in the tool wrappers outside the LLM (the LLM never receives the guard, so it
  can't bypass it). It exists *because* the Agent is non-deterministic. The
  **Workflow** runs without a RobotGuard — its safety bound is structural: the fixed
  DAG's edge order and retry limits. So "guarded" applies to the Agent path only.

## Cameras (two distinct families — keep them separate)

- **Room cameras** — the 16 fixed Basler ceiling cameras in the ED305 lab, viewed
  in the dashboard's camera grid. Canonical term is "room cameras" regardless of
  the vendor word "pylon" or the position word "overhead" that appear in older
  code and docs. The current transport is MediaMTX (WebRTC on LAN, HLS over the
  tunnel); everything else is a being-retired fallback.
- **Robot cameras** — the cameras on the robot body: the head camera and the wrist
  (gripper) camera. Used for grasping and detection, *not* for room monitoring.
  Distinct family from Room cameras; never merge the two in UI or docs.
  - **Head camera** = the **arducam** (ZMQ 6000). The names "top camera", "d435if",
    and ports 6010/6011 that still appear in code are **legacy/dead** — the d435if
    head depth-cam was decommissioned and FUNMAP deleted 6010-6013. When code says
    "head camera (d435if)" it means the arducam now. The head-frame capture path
    still targets the dead ports and is broken until repointed (known issue — see
    `docs/simplification-plan.md`).
  - **Wrist camera** = the **d405** (ZMQ 6002), RGB+depth.

## Detection (the word "detection" is overloaded — two senses)

- **YOLO-World detection** — open-vocabulary geometric detection (bounding boxes)
  running continuously on Robot-camera streams. Standard `vision_msgs` schema so
  it can feed navigation/obstacle consumers. This is the live, always-on detector.
- **VLM detection** — a vision-language model (Qwen2.5-VL) asked, on demand, to
  find named objects and remember where it saw them. Serves the Agent's "where did
  I see X?" recall, not navigation. Slower, semantic, episodic.
- **Scene memory** — the persisted record of what the VLM detected, keyed by the
  robot's location, so the Agent can recall without re-detecting. Distinct from
  navigation's map.

## Navigation

- **On-robot nav (FUNMAP)** — localization (AMCL) and the static map living on the
  robot itself; the dashboard reaches it over the robot's ZMQ surface and submits
  goals to the robot's `goto`.
- **Lab nav stack** — the nvblox + Nav2 + bridge processes that historically ran on
  the lab laptop (in the `isaac_ros_dev` container). Whether it still plans for the
  robot or has been fully superseded by on-robot nav is **unresolved** — see
  `docs/simplification-plan.md`. Do not assert either way until verified live.
- **AMCL pose** — the robot's believed map-frame position, published live. The
  authority for the dashboard's robot dot once localization has converged. A
  user-set pose is only a **seed** that AMCL overrides on its next tick. The pose's
  `source` field (`user` | `amcl` | `nav_result`) says which authority set it — only
  `amcl` is live localization; treat a `user` source as a possibly-stale seed (read
  the live amcl_pose stream directly for closed-loop control).
- **Location** — a named place the robot can be sent to. Three layers, do not
  conflate: (1) the *friendly name* the user/LLM says ("pharmacy"); (2) the
  *catalog key* it maps to (`KNOWN_LOCATIONS`/cure config — e.g. "medicine"), which
  just declares the name is valid; (3) the **taught pose** — the actual (x, y, θ).
- **Taught pose** — an (x, y, θ) captured via the dashboard's teach-and-save UI and
  stored per-workflow on disk. This is the **single source of truth for navigation
  goals**. cure config holds only placeholder poses; a catalog name with no taught
  pose yields "location not taught" and the robot won't go.

## Protocols

- **A2A** — the Agent-to-Agent protocol the backend speaks so other agents can
  request a delivery. A2A callers send a **structured** `{patient, medicine}`
  payload (no free-text parsing, no defaults — missing fields are rejected) and run
  the auto-mode Workflow. Free-text instruction parsing (**MockNLU**) belongs to the
  *dashboard* one-shot path only, never the A2A path.
