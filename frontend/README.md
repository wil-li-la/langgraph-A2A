# Frontend — Robot Task Dashboard

Next.js dashboard for visualizing and controlling the medication delivery robot workflow in real time. Also hosts the teleop page with WebSocket-streamed camera feeds + joint controls.

## Stack

- **Next.js 15** (App Router, Turbopack)
- **React 19**
- **Tailwind CSS** + **shadcn/ui**
- **pnpm**

## Production deployment

Hosted on **Cloudflare Pages** as a static export (`output: 'export'` + `images: { unoptimized: true }` in `next.config.mjs`). Pages auto-builds `frontend/` on every push to `main` — `pnpm build` produces a static `out/` folder that Pages serves 24/7. There is no Node runtime in production.

Pages project env vars (baked into the build):

- `NEXT_PUBLIC_API_URL=https://stretch-api.your-domain.com` — Cloudflare-tunneled backend

`NEXT_PUBLIC_ROBOT_HOST` is optional — when unset, the Robot IP input pre-fills with the hardcoded lab default `192.168.1.38` (see `contexts/robot-connection.tsx`). Set it only to override the default for a different deployment. Robot connection is still an optional add-on — the user has to click Connect.

The dashboard has zero server-side features (no API routes, no SSR), so the static export loses nothing.

## Local development

```bash
cp .env.example .env.local   # set NEXT_PUBLIC_API_URL
pnpm install
pnpm dev          # http://localhost:3000
pnpm build        # produces ./out (same artifact Cloudflare Pages publishes)
pnpm lint         # ESLint + TypeScript
```

`NEXT_PUBLIC_API_URL` options — see `.env.example`:

- `http://localhost:9999` — local dev on same machine
- `http://192.168.1.X:9999` — LAN (no HTTPS; mic APIs disabled on other devices)
- `https://stretch-api.your-domain.com` — via Cloudflare Tunnel (mic works on iPad/phone)

For backend tunnel setup see the root [README](../README.md#cloudflare-tunnel-setup-backend-exposure).

## Dashboard state machine

The dashboard operates as a three-state machine derived from existing flags in `contexts/workflow-context.tsx`:

| State | Controls | Teleop link |
|---|---|---|
| **IDLE** | `START` button, click-any-node to start from there | unlocked |
| **RUNNING** | `STOP` (graceful — pauses after current node finishes) | locked 🔒 |
| **PAUSED** | `RESUME from <node>`, click-any-node to resume from there | unlocked |

Pauses come from three sources, handled identically in the UI: user-pressed STOP, node routed to `handle_error`, and `await_input` sub-state (browser voice/text prompt during `check_identity`).

## Structure

```
app/
├── layout.tsx               # Mounts WorkflowProvider + RobotConnectionProvider app-wide
├── page.tsx                 # Renders <RobotDashboard />
├── teleop/page.tsx          # Teleop route
└── globals.css

components/
├── robot-dashboard.tsx      # Top-level dashboard layout
├── nav-bar.tsx              # Title + robot IP + Teleop link (locked while RUNNING)
├── workflow-controls.tsx    # START/STOP/RESUME panel; three IDLE/RUNNING/PAUSED variants
├── workflow-graph.tsx       # Live LangGraph SVG, clickable nodes in IDLE + PAUSED, auto-scroll to active
├── skills-panel.tsx         # Horizontal skill chips ● grasp ● listen ● speak ● handover ● navigate
├── voice-input.tsx          # Dual-mode: hold-to-talk (Web Speech API) + text input
├── pause-guide.tsx          # Post-pause guidance (amber for user-stop, red for error)
├── video-panel.tsx          # 3-cam grid (realsense, gripper, nav map)
└── teleop/                  # Teleop-specific UI (StatusBar, SpeedScale, joint sliders, joystick, …)

contexts/
├── workflow-context.tsx     # All workflow state — survives /teleop navigation
└── robot-connection.tsx     # Robot IP input + WebSocket connection

hooks/
├── use-workflow.ts          # Thin re-export of useWorkflowContext()
└── use-teleop.ts            # WebSocket connection to /ws/teleop; camera frame decoding

lib/
├── api.ts                   # REST + SSE client
├── teleop-protocol.ts       # Binary [camera_id][jpeg] frame parser, status JSON parser
└── mock-data.ts             # Fallback when backend is unreachable
```

## API client (`lib/api.ts`)

| Function | Endpoint | Purpose |
|---|---|---|
| `fetchWorkflow()` | `GET /api/workflow` | Graph nodes + edges |
| `fetchSkills()` | `GET /api/skills` | Required + available skill list |
| `executeWorkflowStream(instruction, cb, signal, opts?)` | `POST /api/workflow/execute/stream` | SSE streaming; `opts.start_from` = start from a specific node |
| `resumeWorkflowStream(sessionId, nodeId, cb, signal)` | `POST /api/workflow/resume` | Resume paused workflow from node |
| `stopWorkflow(sessionId)` | `POST /api/workflow/stop` | Graceful stop — pauses after current node |
| `submitWorkflowInput(sessionId, text)` | `POST /api/workflow/input` | Deliver browser voice/text to a waiting `check_identity` |
| `resetWorkflowStream(cb, signal)` | `POST /api/workflow/reset` | Robot returns to origin, state cleared |

### SSE events

```ts
// {event: "log", text, level: "info"|"warning"|"error"}
// {event: "node_start", node_id, executed_nodes, session_id}
// {event: "node_end",   node_id, executed_nodes, history, task_status, session_id}
// {event: "paused",     node_id, reason, session_id, …}
// {event: "await_input", session_id, prompt}
// {event: "done",       result}
// {event: "error",      error}
```

Log events now carry `level` — frontend colors deterministically (red=error, amber=warning, muted=info, emerald if the text contains `✓`).

## Voice input

`components/voice-input.tsx` uses `webkitSpeechRecognition` (Safari/iOS) with `lang="zh-TW"`. Hold-to-talk pattern: press and hold → recording, release → stop. Text input always available as fallback. Requires HTTPS (secure context) — see tunnel setup.

During a workflow's `check_identity` node in manual mode, the backend fires an `await_input` event; the Voice Input panel border turns sky-blue and "Awaiting" badge appears. Submitted text goes to `POST /api/workflow/input`, unblocks the workflow.

## Teleop

`/teleop` connects to `ws(s)://<api>/ws/teleop?robot=ws://<robot-ip>:8765` — the backend relays to the robot's WebSocket. Binary messages are `[1-byte camera_id][JPEG]`; the backend relay drops old frames per-camera when the browser can't keep up (see `backend/app/teleop_api.py`).

## Notes

- `next.config.mjs` has `typescript: { ignoreBuildErrors: true }` — TypeScript errors don't block builds. Still run `npx tsc --noEmit` before pushing.
- No test framework configured; verification is manual/integration.
- Workflow state lives in a Context mounted at `app/layout.tsx` so it survives navigation to `/teleop` and back.
