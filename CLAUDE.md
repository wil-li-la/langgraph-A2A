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
python -m app.healthcare.medication_delivery 張小明 阿斯匹靈
```

Required env vars (copy from `.env.example`):
- `model_source=google` or `model_source=openai`
- `GOOGLE_API_KEY` or `OPENAI_API_KEY`

### Frontend (Node 20 + pnpm)
```bash
cd frontend
pnpm install
pnpm dev          # http://localhost:3000 (Turbo)
pnpm build
pnpm lint         # ESLint + TypeScript
```

Frontend connects to backend via `NEXT_PUBLIC_API_URL` (defaults to `http://localhost:9999`).

### Docker (both services)
```bash
cp backend/.env.example backend/.env  # fill in API keys
docker-compose up -d
```

## Architecture

### Backend (`backend/app/`)

- **`__main__.py`** — CLI entry point. Builds the AgentCard, wires A2AStarletteApplication with DefaultRequestHandler, adds workflow REST routes, starts Uvicorn.
- **`agent_executor.py`** — Bridges A2A protocol to LangGraph. `execute()` parses instructions via MockNLU, runs `MedicationDeliveryAgent`, returns artifacts. Special-cases capability query strings (Chinese/English).
- **`workflow_api.py`** — REST endpoints for the dashboard:
  - `GET /api/workflow` — graph structure (nodes + edges)
  - `POST /api/workflow/execute` — one-shot execution
  - `POST /api/workflow/execute/stream` — SSE streaming (node_start, node_end, log, done, error)
- **`camera_api.py`** — Video streaming endpoints for robot cameras (D405, D435if).
- **`healthcare/medication_delivery.py`** — The LangGraph `StateGraph`. 9 nodes (confirm_task → navigate_to_pharmacy → pickup_medication → navigate_to_patient → deliver → check_patient_identity → return_to_origin, with error_handler). Uses CURE robot skills (grasp, navigate, speak, listen, handover). Logs execution to Rerun.
- **`healthcare/mock_data.py`** — Mock patient/medication database + MockNLU (bilingual Chinese/English pattern matching).

### A2A Protocol

- Agent metadata: `GET /.well-known/agent-card.json` (also `/agent.json` for legacy)
- A2A JSON-RPC: `POST /` (`message/send` method)
- Uses `a2a-sdk` — implements `AgentExecutor`, `AgentCard`, `InMemoryTaskStore`, `BasePushNotificationSender`

### LangGraph State

`AgentState` TypedDict — key fields: `patient_name`, `medication_name`, `current_location`, `task_status`, `target_detected`, `identity_verified`, `identity_check_retries`. List fields (`errors`, `history`, `executed_nodes`) use `operator.add` reducer for append semantics across nodes.

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

### TODO: Robot-side goto service (Nav2 obstacle avoidance)

Port 5557 (goto, REQ/REP) **does not exist** in the current stretch3-zmq driver. It must be added to `stretch3-zmq` on the robot before `navigate_skill` can work.

What to add in `stretch3-zmq`:

1. **`driver/services/goto.py`** — new service:
   - ZMQ REP socket on `config.ports.goto` (5557)
   - Receives msgpack `{"x": float, "y": float, "theta": float}`
   - Calls `nav2_simple_commander.BasicNavigator.goToPose()` with the goal
   - Blocks until `navigator.isTaskComplete()`
   - Sends reply: `"ok"` on success, error string on failure

2. **`driver/__main__.py`** — start the service:
   - Import `goto_service` and launch it as a `threading.Thread` (same pattern as other services)

3. **`driver/config.py`** — ensure `ports.goto: int = 5557` is defined (may already be present)

Nav2 must be running on the robot (`ros2 launch stretch_nav2 navigation.launch.py`) for the goto service to work.

Mac-side `navigate_avoidance` in `cure/src/cure/skills/navigate.py` already sends the correct `{"x", "y", "theta"}` msgpack format and waits for `"ok"`. The original `navigate_skill` (direct velocity control) is unchanged.

---

### Key Config Notes

- Backend Dockerfile: port 9999, non-root user `appuser`
- Frontend Dockerfile: port 3000, Next.js standalone output
- `next.config.mjs` has `typescript: { ignoreBuildErrors: true }` — TypeScript errors don't block builds
- No test framework is configured; verification is manual/integration only
- Docker requires docker group membership: `sudo usermod -aG docker $USER && newgrp docker`
