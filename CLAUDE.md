# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

A medication delivery robot system: a Python LangGraph A2A agent backend + Next.js dashboard frontend. The backend implements the Agent-to-Agent (A2A) protocol using a LangGraph StateGraph to orchestrate a robot workflow (navigate → pick medication → verify patient identity → deliver). The frontend visualizes the workflow execution in real time.

## Development Commands

### Backend (Python 3.12)
```bash
cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e .
python -m app --host localhost --port 9999

# Test workflow directly (bypasses A2A)
python -m app.workflows.medication_delivery 張小明 阿斯匹靈
```

Required env vars (copy from `.env.example`):
- `model_source=google` or `model_source=openai`
- `GOOGLE_API_KEY` or `OPENAI_API_KEY`

### Frontend (Node 20 + pnpm)
```bash
cd frontend
pnpm install
pnpm dev          # http://localhost:3000 (Turbo) — local dev only
pnpm build        # produces static out/ (output: 'export')
pnpm lint         # ESLint + TypeScript
```

Frontend connects to backend via `NEXT_PUBLIC_API_URL` (defaults to `http://localhost:9999`).

### Production deployment

- **Frontend**: hosted on **Cloudflare Pages** as a static export (24/7, no laptop uptime needed). `next.config.mjs` has `output: 'export'`. Pages auto-builds `frontend/` on every push to `main`. `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_ROBOT_HOST`, and `NEXT_PUBLIC_ROOM_CAMERAS_URL` are set in the Pages project's env vars and baked at build time.
- **Backend**: runs on the lab laptop and is exposed via **Cloudflare Tunnel** at `stretch-api.<domain>`. See root README for tunnel config.
- Docker has been removed for the backend (no Dockerfiles, no compose for the A2A service) — use the venv install path in `backend/INSTALL.md`. The GPU YOLO-World detection nodes still run inside the `isaac_ros_dev` container (it has the only working torch+CUDA+ultralytics env on this host). Navigation no longer runs in the container — it moved to the robot (FUNMAP).

### Lab daily run

`./start.sh` launches the whole lab stack in one tmux session (`stretch-lab`):
backend, MediaMTX (room cams), cam_bridge (legacy fallback), room_cameras, the GPU
YOLO-World detection windows (head + wrist, inside `isaac_ros_dev`), and the
frontend. Attach with `tmux attach -t stretch-lab`.

Prereqs the script does **not** start for you: the `isaac_ros_dev` container must
be up (it runs the GPU YOLO windows), and the Stretch3 driver must be running on
the robot (`ssh stretch-se3-3099.local`, then `uv run python -m stretch3_zmq.driver`).

> **Nav is on the robot now.** The old lab nav stack (`run_nav.sh` / `nav.launch.py`
> / nvblox / Nav2 / `nav_service` / lab-AMCL) was removed 2026-06-01 — on-robot
> FUNMAP fully replaced it; navigation plans on the robot (goto:5557). The
> container is still needed, but only for GPU YOLO detection.

### Room cameras (MediaMTX — sole transport)

The ED305 lab has 16 fixed Basler room cameras (canonical term: **room cameras** —
see `CONTEXT.md`). Each camera host emits H264 RTSP (`rtsp://192.168.1.13:8554/cam_N`
right, `rtsp://192.168.1.56:8554/cam_N` left). **MediaMTX** (`backend/mediamtx/`, a
single Go binary) pulls those RTSP feeds and re-muxes them to WebRTC (WHEP, LAN) and
HLS-LL (tunnel) — zero transcoding on this host. See `docs/camera-architecture.md`
and `docs/adr/0001-mediamtx-sole-room-camera-transport.md`.

```bash
cd backend/mediamtx
./run.sh                                 # RTSP :8554, HLS :8888, WebRTC :8889 (+ICE :8189)
```

The frontend (`frontend/components/room-cameras-grid.tsx`, page `/cameras`) reads
`NEXT_PUBLIC_MEDIAMTX_WEBRTC_URL` (LAN) or `NEXT_PUBLIC_MEDIAMTX_HLS_URL` (tunnel) and
renders the 13-tile grid. WebRTC ICE is pinned to `eno2`
(`webrtcIPsFromInterfacesList`) to avoid docker-bridge ICE candidates blacking out
tiles. TLS uses an mkcert cert at `backend/mediamtx/cert.pem` (gitignored).

> The prior fallbacks — `backend/cam_bridge/` (NVENC h264), `backend/room_cameras/`
> (ROS2→MJPEG :9997), and direct web_video_server MJPEG — were removed 2026-06-01.

## Architecture

### Backend (`backend/app/`)

- **`__main__.py`** — CLI entry point. Builds the AgentCard, wires A2AStarletteApplication with DefaultRequestHandler, adds workflow REST routes, starts Uvicorn.
- **`api/a2a.py`** — Bridges the A2A protocol to LangGraph. `MedicationAgentExecutor.execute()` extracts a **structured** payload from the inbound Message — a `DataPart` (or JSON `TextPart`) of `{"patient": str, "medicine": str}` — with **no free-text NLU, no regex fallback, and no hardcoded defaults**: missing/empty fields raise `InvalidParamsError`. It runs `MedicationDeliveryAgent` in `mode="auto"` and returns artifacts. The blocking workflow run is offloaded via `asyncio.to_thread` so the event loop stays free. (MockNLU is used only by the dashboard's `/api/workflow/execute` path, not here.)
- **`api/workflow.py`** — REST endpoints for the dashboard and A2A callers:
  - `GET /api/workflow` — graph structure (nodes + edges)
  - `POST /api/workflow/execute` — one-shot execution (manual mode, uses MockNLU)
  - `POST /api/workflow/execute/stream` — SSE streaming (node_start, node_end, log, done, error)
  - `POST /api/a2a/execute` — the A2A endpoint, body `{"patient": str, "medicine": str}`, skips NLU parsing, runs in auto mode. This is the only endpoint A2A callers should use.
- **`api/agent.py`** — REST/SSE endpoints for the LLM-driven agentic path:
  - `GET /api/agent/info` — what tools the agent has, which LLM, availability
  - `POST /api/agent/execute` — one-shot agent run, body `{"task": str, "budget"?: int}`
  - `POST /api/agent/execute/stream` — SSE: `started`, `agent_message`, `tool_call`, `tool_result`, `log`, `done`, `error`
- **`api/camera.py`** — Video streaming endpoints for robot cameras (D405, D435if).
- **`api/teleop.py`** — WebSocket relay for direct teleoperation.
- **`workflows/medication_delivery.py`** — The LangGraph `StateGraph`. 9 nodes (confirm_task → navigate_to_pharmacy → pickup_medication → navigate_to_patient → deliver → check_patient_identity → return_to_origin, with error_handler). Uses CURE robot skills (grasp, navigate, speak, listen, handover). Logs execution to Rerun. Hand-written DAG; node order is fixed at compile time.
- **`agents/delivery_agent.py`** — Generalist ReAct agent built with `langgraph.prebuilt.create_react_agent`. Same skills, different control: the LLM picks tool calls per turn instead of following a fixed DAG.
- **`tools/cure_tools.py`** — LangChain `@tool` wrappers around the CURE skills used by the agent. Honors a `RobotGuard` for preconditions and budget; honors `DRY_RUN=1` env to bypass hardware while logging validation criteria.
- **`safety/guard.py`** — `RobotGuard` enforced outside the LLM (preconditions + per-task tool-call budget), scoped per-task via contextvars.
- **`llm/factory.py`** — Provider factory: `LLM_PROVIDER` env picks `none|ollama|openai|google|anthropic`. Default `none` keeps the LLM-driven path off and the scripted workflow byte-identical.
- **`mock_data.py`** — Mock patient/medication database + MockNLU (LLM-first via `app.llm`, falls back to bilingual Chinese/English keyword matching when the LLM is disabled).

### A2A Protocol

- Agent metadata: `GET /.well-known/agent-card.json` (also `/agent.json` for legacy)
- A2A JSON-RPC: `POST /` (`message/send` method)
- Uses `a2a-sdk` — implements `AgentExecutor`, `AgentCard`, `InMemoryTaskStore`, `BasePushNotificationSender`

### LangGraph State

`AgentState` TypedDict — key fields: `patient_name`, `medication_name`, `current_location`, `task_status`, `target_detected`, `identity_verified`, `identity_check_retries`, `mode` (`"auto"` for A2A / `"manual"` for dashboard). List fields (`errors`, `history`, `executed_nodes`) use `operator.add` reducer for append semantics across nodes.

Conditional routing: confirm_task success → pharmacy path; any failure → error_handler_node → END. Identity check retries up to 3 times before failing.

### Frontend (`frontend/app/`)

App Router structure. Main page (`page.tsx`) renders `RobotDashboard` which uses the `useWorkflow` hook (`hooks/use-workflow.ts`) for all state. Layout:
- **ConnectPanel** — robot selection (stretch3/kinova/franka), connection status, skills list
- **WorkflowGraph** — live LangGraph visualization with active node highlighting
- **VideoPanel** — 4 camera streams (RGB + depth)

API client in `lib/api.ts`: `fetchWorkflow()`, `executeWorkflow()`, `executeWorkflowStream()` (SSE consumer). Falls back to mock data (`lib/mock-data.ts`) if backend is unavailable.

### Robot Hardware Layer (stretch3-zmq)

The `cure` skill library communicates with the physical robot over ZeroMQ. The driver must be running on the robot before executing any workflow that uses real hardware.

**Start the driver on the robot:**
```bash
ssh stretch-se3-3099.local -l hello-robot
cd Desktop/stretch3-zmq/
uv run python -m stretch3_zmq.driver --config config.yaml
```

**Source:** https://github.com/lnfu/stretch3-zmq (private, accessible to team)

**Install `stretch3-zmq-core` (client package, no hardware deps):**
```bash
# LFS budget exceeded on GitHub — clone with LFS skip then install
GIT_LFS_SKIP_SMUDGE=1 git clone https://github.com/lnfu/stretch3-zmq.git /tmp/stretch3-zmq
.venv/bin/pip install /tmp/stretch3-zmq/packages/core
```

**Install `cure` skills library:**
```bash
GIT_LFS_SKIP_SMUDGE=1 git clone --branch no-detection https://github.com/lnfu/cure.git /tmp/cure-no-detection
.venv/bin/pip install --no-deps /tmp/cure-no-detection
# Then install remaining cure deps:
.venv/bin/pip install rerun-sdk pyzmq scipy
```

**Architecture:** The driver is a multi-threaded ZMQ broker running on the robot. Skills connect as remote clients:

| Skill | ZMQ Pattern | Port |
|---|---|---|
| `navigate_skill` | REQ/REP (goto) | 5557 |
| `grasp_skill` | SUB (command/servo) + PUB (status) | 5556/5558/5555 |
| `speak_skill` | REQ/REP (submit) + PUB (status) | 6101/6102 |
| `listen_skill` | REQ/REP | 6103 |
| `handover_skill` | REQ/REP + servo | 5557/5558 |
| Camera streams | PUB/SUB | 6000–6002 |

The driver publishes robot state at 15 Hz on port 5555. Robot hostname: `stretch-se3-3099.local`, SSH user: `hello-robot`.

Config lives on the robot at `Desktop/stretch3-zmq/config.yaml` — defines ports, TTS provider (fish_audio), ASR (deepgram, language: zh-TW, mic: DJI MIC MINI), and motion limits for all 8 joints.

### Robot-side goto service (SHIPPED 2026-05-18)

Port 5557 (goto, REQ/REP) **exists** on the robot — shipped on stretch3-zmq branch
`feat/nvblox-robot-driver-migration`. It accepts an absolute pose goal as msgpack
`{"x": float, "y": float, "theta": float}` and replies `"ok"` on success or a
structured `"<status>: <reason>"` string on failure (`no_path`, `timeout`,
`obstructed`, `bad_target`, …). The legacy velocity endpoint (`{"linear","angular"}`)
moved to port **5559** as `goto_velocity`. Full wire contract:
`docs/stretch_server_goto_refactor.md` and `docs/steretch3_protocol/protocols.md`.

> **Lab nav stack is dead** (confirmed 2026-06-01). On-robot FUNMAP fully replaced
> the lab nvblox/Nav2/`nav_service` stack; goto:5557 plans on the robot. The
> `start.sh` nav window + `nav_bridge/run_nav.sh`/`nav.launch.py`/`sensors_bridge`/
> `cmdvel_bridge`/`nav_service` are legacy — scheduled for removal, see
> `docs/simplification-plan.md` item 8b. The YOLO files in `nav_bridge/` stay (live
> perception, not nav).

Mac-side `navigate_avoidance` in `cure/src/cure/skills/navigate.py` sends the
`{"x", "y", "theta"}` goal and waits for `"ok"`. The original `navigate_skill`
(direct velocity control) is unchanged.

---

### Key Config Notes

- Backend serves on port 9999 (set via `--port` flag or `PORT` env var)
- Frontend builds to a static `out/` folder via `output: 'export'` and is served by Cloudflare Pages — no Node runtime in production
- `next.config.mjs` has `typescript: { ignoreBuildErrors: true }` — TypeScript errors don't block builds
- `next.config.mjs` has `images: { unoptimized: true }` — required for static export
- No test framework is configured; verification is manual/integration only
