# Frontend — Robot Task Dashboard

Next.js dashboard for visualizing and controlling the medication delivery robot workflow in real time.

## Stack

- **Next.js 16** (App Router, Turbopack)
- **React 19**
- **Tailwind CSS v4** + **shadcn/ui**
- **pnpm**

## Development

```bash
cp .env.example .env.local    # set NEXT_PUBLIC_API_URL
pnpm install
pnpm dev                      # http://localhost:3000
pnpm build
pnpm lint                     # ESLint + TypeScript
```

`NEXT_PUBLIC_API_URL` is read at dev-server start (not at runtime) — restart `pnpm dev` after editing `.env.local`. See [`.env.example`](./.env.example) for the three usage modes (localhost / LAN / Cloudflare tunnel).

For iPad / mobile access with a working microphone, the frontend must be served over HTTPS — see the root [README.md](../README.md#remote-access-via-cloudflare-tunnel) for the Cloudflare Tunnel setup. If your tunnel is already provisioned, just set `NEXT_PUBLIC_API_URL` to the tunneled backend hostname and `pnpm dev`.

## State machine

The dashboard has three top-level states derived from the workflow hook:

| State | Teleop link | Mode panel shows | Click node |
|---|---|---|---|
| `idle` | enabled | instruction input + START | "start from here" (requires instruction) |
| `running` | 🔒 locked | STOP (graceful) | no-op |
| `paused` | enabled | RESUME from `<node>` | "resume from here" |

Design spec: [`docs/superpowers/specs/2026-04-22-dashboard-state-machine-design.md`](../docs/superpowers/specs/2026-04-22-dashboard-state-machine-design.md).

## Structure

```
app/
├── layout.tsx               # wraps children in RobotConnectionProvider + WorkflowProvider
├── page.tsx                 # main route → RobotDashboard
├── teleop/page.tsx          # /teleop route (full-screen robot control)
└── globals.css

components/
├── robot-dashboard.tsx      # top-level layout; wires NavBar, WorkflowGraph, VideoPanel, WorkflowControls, VoiceInput, PauseGuide, ExecutionLog
├── nav-bar.tsx              # robot IP input + connect + nav links (teleop link gates on workflowState)
├── workflow-graph.tsx       # SVG LangGraph; auto-scrolls to active node; clickable in idle/paused
├── workflow-controls.tsx    # three-variant panel (IDLE/RUNNING/PAUSED)
├── voice-input.tsx          # hold-to-talk (Web Speech API) + text fallback; used in `check_identity` prompts
├── pause-guide.tsx          # amber (user-stopped) or red (error) guide when paused
├── skills-panel.tsx         # horizontal strip of required cure skills with load status
├── video-panel.tsx          # 3-pane layout: head camera, gripper camera, nav map
└── teleop/                  # components for /teleop route (joint sliders, camera views, etc.)

contexts/
├── robot-connection.tsx     # WebSocket to /ws/teleop — survives page navigation
└── workflow-context.tsx     # All workflow state + actions — survives page navigation
                             # (see hooks/use-workflow.ts — thin re-export)

hooks/
├── use-workflow.ts          # thin re-export of useWorkflowContext
├── use-teleop.ts            # WebSocket client + camera frame decoder
├── use-mobile.tsx
└── use-toast.ts

lib/
├── api.ts                   # SSE/REST client + stream event types
├── mock-data.ts             # fallback WorkflowNode[] / WorkflowEdge[] when backend unavailable
└── teleop-protocol.ts       # robot WS message types + camera frame parser

types/
├── robot.ts                 # RobotStatus, CameraName, JointName
└── speech.d.ts              # SpeechRecognition API types (Safari-specific)
```

## Workflow context

All workflow state lives in one React Context mounted at the root layout, so it survives navigation between `/` and `/teleop`:

```tsx
const { workflowState, startStreamExecution, stop, resumeFromNode, executionLog, ... } = useWorkflow()
```

`executionLog` is `LogEntry[]` (`{text, level: "info"|"warning"|"error"}`) — color-coded in the dashboard by level.

## API client (`lib/api.ts`)

| Function | Endpoint | Description |
|---|---|---|
| `fetchWorkflow()` | `GET /api/workflow` | Load graph structure |
| `fetchSkills()` | `GET /api/skills` | Required + available cure skills |
| `executeWorkflow()` | `POST /api/workflow/execute` | One-shot blocking execution |
| `executeWorkflowStream()` | `POST /api/workflow/execute/stream` | SSE streaming; accepts `start_from` param |
| `resumeWorkflowStream()` | `POST /api/workflow/resume` | Resume paused workflow from node |
| `stopWorkflow()` | `POST /api/workflow/stop` | Request graceful stop |
| `submitWorkflowInput()` | `POST /api/workflow/input` | Submit browser-captured voice/text to a waiting `check_identity` |
| `resetWorkflowStream()` | `POST /api/workflow/reset` | Return robot to origin, clear paused sessions |

SSE event types: `node_start`, `node_end`, `log`, `done`, `error`, `paused`, `await_input`.

If the backend is unreachable, `fetchWorkflow()` falls back to mock data from `lib/mock-data.ts`.

## Voice input (iPad / phone)

The `check_identity` node pauses the workflow and requests browser input. `VoiceInput` uses Safari's `webkitSpeechRecognition` (zh-TW) for hold-to-talk, with a text field as fallback. The `getUserMedia` and `SpeechRecognition` APIs require a **secure context** — on iOS Safari that means HTTPS (or `localhost`). See root README's Cloudflare Tunnel section.

## Notes

- `next.config.mjs` has `typescript: { ignoreBuildErrors: true }` — TypeScript errors don't block builds (but `pnpm lint` surfaces them).
- No test framework; verification is manual via browser + `tsc --noEmit`.
- Camera frames arrive as binary WebSocket messages (`[1-byte camera_id][jpeg]`), decoded client-side in `use-teleop.ts` and rendered via canvas.
