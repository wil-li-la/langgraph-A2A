# Dashboard State Machine — Robot Control Structure

**Date:** 2026-04-22
**Goal:** Define a single top-level state machine for the dashboard that cleanly gates teleop access, distinguishes user-initiated stop from automatic error-pause, and unifies the resume / start-from-node behavior.

## Motivation

The dashboard currently mixes concerns:

- Teleop is always nominally available (separate `/teleop` page) even while the workflow is driving the robot, so two drivers can fight over the robot.
- The STOP button only aborts the SSE stream, not the backend workflow — the robot keeps moving after the user thinks they stopped it.
- Resume only works through clicking a node; there is no dedicated "continue from where we paused" button.
- There is no way to start the workflow from a non-initial node except by resuming after a pause.

This spec replaces ad-hoc control flow with one top-level state machine and derives all UI affordances from it.

## State Machine

Three top-level states, plus one transient sub-state:

```
┌─────────┐   START / click-node   ┌──────────┐   completes   ┌─────────┐
│  IDLE   │ ──────────────────────→│ RUNNING  │ ─────────────→│  IDLE   │
└─────────┘                        └──────────┘               └─────────┘
     ▲                                  │                          
     │                                  │ STOP (user)              
     │                                  │ error (auto)             
     │                                  ▼                          
     │                              ┌──────────┐                   
     └────────── RESET ──────────── │  PAUSED  │                   
                                    └──────────┘                   
                                         │ ▲                       
                                         └─┘                       
                                    RESUME / click-node            
```

`await_input` is **not** a separate state. It is a sub-state of RUNNING where the currently executing node is blocked on browser input. Teleop stays locked.

### Per-state affordances

| State | START | STOP | RESUME | Click node | Teleop nav link | Notes |
|---|---|---|---|---|---|---|
| **IDLE** | ✅ `START` | — | — | ✅ "Start from here" | ✅ Enabled | Instruction required in Mode panel |
| **RUNNING** | — | ✅ `STOP` (graceful) | — | ❌ No-op | 🔒 Locked | Shows "STOPPING…" while stop is pending |
| **PAUSED** | — | — | ✅ `RESUME from <node>` | ✅ "Resume from here" | ✅ Enabled | User may teleop freely before resuming |

### Transitions

| From | Event | To | Backend action |
|---|---|---|---|
| IDLE | press START | RUNNING | `POST /api/workflow/execute/stream`, `resume_from = ""` |
| IDLE | click node | RUNNING | `POST /api/workflow/execute/stream`, `resume_from = <node_id>` |
| RUNNING | press STOP | PAUSED (after current node) | `POST /api/workflow/stop` adds session_id to `_stop_requests`; `stream_execute` checks flag after each `node_end` and pauses via existing mechanism |
| RUNNING | workflow completes | IDLE | SSE emits `done`; frontend clears `isExecuting` |
| RUNNING | node routes to handle_error | PAUSED | Existing behavior unchanged |
| RUNNING | await_input (mid-node) | RUNNING (await_input sub-state) | No state change; VoiceInput panel activates (existing behavior) |
| PAUSED | press RESUME | RUNNING | `POST /api/workflow/resume`, `node_id = <paused_node>` |
| PAUSED | click node | RUNNING | `POST /api/workflow/resume`, `node_id = <clicked_node>` |
| PAUSED | press RESET | IDLE (after robot reaches origin) | `POST /api/workflow/reset` (existing) |
| IDLE | press RESET | IDLE | `POST /api/workflow/reset` (existing) — goes through return_to_origin |

### Completion semantics

When the workflow completes normally:

- Backend emits `done` SSE event.
- Frontend sets `isExecuting = false`, `activeNodeId = null`.
- `executedNodes` stays populated — the graph continues to show nodes as "completed" (green) for user reference.
- The UI transitions to IDLE: START is enabled, teleop link unlocks.
- `executedNodes` clears on the **next** START / node-click (not on completion), so the graph resets right before the next run begins.

## Data Model

### Backend (`backend/app/healthcare/medication_delivery.py`)

Add one module-level variable:

```python
# Session IDs that have requested a graceful stop. Consumed by stream_execute
# after each node_end — when the session is in this set, the workflow pauses
# via the existing _paused_sessions mechanism instead of continuing.
_stop_requests: set[str] = set()
```

No change to `AgentState`.

### Backend (`backend/app/workflow_api.py`)

Two endpoint changes:

1. **`POST /api/workflow/stop`** (new):
   - Body: `{ "session_id": str }`
   - Adds `session_id` to `_stop_requests`.
   - Returns `{ "status": "ok" }` immediately — does not wait for the workflow to actually pause.

2. **`POST /api/workflow/execute/stream`** (modified):
   - Accept new optional `start_from` in the request body. If present and valid, injects it as `resume_from` in the initial state, letting the `resume_router` route execution to the named node instead of `confirm_task`.

### Frontend hook (`frontend/hooks/use-workflow.ts`)

Derived workflow state:

```typescript
type WorkflowState = "idle" | "running" | "paused"

const workflowState: WorkflowState =
  isPaused ? "paused" :
  isExecuting ? "running" :
  "idle"
```

Two new actions:

```typescript
// From IDLE: starts workflow from a specific node with the current instruction.
// Falls through to startStreamExecution with a start_from param.
startFromNode: (nodeId: string, instruction: string) => Promise<ExecutionResult | null>

// From RUNNING: requests a graceful stop. Does not abort SSE; waits for backend
// to emit 'paused' event.
stopWorkflow: () => Promise<void>
```

`resumeFromNode` is unchanged. `stopStreamExecution` (SSE abort) stays as a hard-cancel escape hatch but is no longer wired to any button.

### Frontend API client (`frontend/lib/api.ts`)

Two new exports:

```typescript
stopWorkflow(sessionId: string): Promise<void>
// POSTs { session_id } to /api/workflow/stop.

executeWorkflowStream(..., options?: { start_from?: string })
// Extend existing function to pass start_from through to the backend.
```

## Component Changes

### Mode panel (`frontend/components/mode-toggle.tsx` → renamed `workflow-controls.tsx`)

The panel renders one of three variants based on `workflowState`:

**IDLE:**
```
 MANUAL / AUTO toggle
 [ instruction input ........ ] [ START ▶ ]
 hint: "or click any node below to start from there"
```

**RUNNING:**
```
 ⏺ Running — <current_node_name>
 [ STOP ■ ]   (label: "STOPPING…" after click, until paused event arrives)
```

**PAUSED:**
```
 ⏸ Paused at <paused_node_name>
 <reason if error, or "Stopped by user">
 [ RESUME ▶ from <paused_node_name> ]
 hint: "or click any node to resume from there"
```

### Workflow graph (`frontend/components/workflow-graph.tsx`)

`onNodeClick` becomes active in both IDLE and PAUSED. Currently it only fires in PAUSED. The hover highlight (sky-blue dashed outline on clickable nodes) extends to IDLE too.

Start and end pseudo-nodes remain non-clickable in all states.

When a node is clicked:
- If IDLE: calls `startFromNode(nodeId, instruction)`. If instruction is empty, show a tooltip "Enter instruction first" and do not start.
- If PAUSED: calls `resumeFromNode(nodeId)` (unchanged).

### NavBar (`frontend/components/nav-bar.tsx`)

The `/teleop` link checks `workflowState`:

- If `workflowState === "running"` (including `await_input` sub-state): link renders as disabled (muted color, `cursor: not-allowed`, tooltip "Stop workflow to access teleop"). Click is a no-op.
- Otherwise: link behaves normally.

`useWorkflow` is already called at the top of RobotDashboard; NavBar needs access to `workflowState`. Simplest approach: lift the derived `workflowState` to a React context, or pass as a prop from layout. Since NavBar is rendered inside RobotDashboard, prop-passing works.

Trade-off: teleop page (`/teleop`) doesn't currently know about workflow state because it's a separate route. Defense-in-depth would have the page also check and redirect — skipped for MVP.

### Pause guide (`frontend/components/pause-guide.tsx`)

Rendered when `workflowState === "paused"`. Copy varies by cause:

- **Stopped by user** (reason is empty or starts with "stopped"): "Workflow paused. Use teleop to adjust the robot, then press RESUME or click any node."
- **Error** (reason is an error string): existing copy — "Node X failed: <reason>."

Both show:
- "Open Teleop" button (active link)
- "or click any node in the graph to resume from there"

## Error Handling

| Situation | Handling |
|---|---|
| STOP pressed while `await_input` is active | `_stop_requests` flag is set but not acted on; `stream_execute` only checks the flag after `node_end`, so the workflow waits for the browser input to complete (or time out), then pauses before the next node. UI shows "STOPPING — waiting for input…". |
| STOP pressed but workflow finishes in the same tick | Stop flag is left in `_stop_requests` until session ends; next workflow with same session_id would inherit it. Mitigation: `_stop_requests.discard(session_id)` on every `done` and `paused` event in `stream_execute`. |
| User teleops during PAUSED, robot position drifts, then RESUME | RESUME re-enters the paused node. Each node's logic (navigate, grasp) operates from wherever the robot currently is. No special handling needed — nodes are already idempotent in this regard. |
| Network blip kills SSE mid-run | SSE drops; frontend state falls back to IDLE; but backend workflow keeps running. This is a pre-existing issue (outside this spec's scope) and is not made worse by these changes. |
| RESET pressed during RUNNING | RESET button is disabled in RUNNING state (existing behavior preserved). User must STOP first. |

## Success Criteria

1. IDLE state: pressing START begins the workflow from the first node. Clicking `pickup_med` in IDLE starts the workflow at `pickup_med`. Both require a valid instruction in the Mode panel.
2. RUNNING state: the Teleop nav link is visibly disabled and clicking it does nothing. Pressing STOP transitions to PAUSED within one node boundary (≤30s typical).
3. PAUSED state: pressing RESUME continues execution from the paused node. Clicking `nav_to_patient` resumes from `nav_to_patient` instead. Teleop link is enabled.
4. When the workflow completes normally, the graph stays showing completed nodes, but the Mode panel shows START (not STOP) and the Teleop link is enabled — the user can teleop without pressing RESET first.
5. Pressing STOP during an `await_input` sub-state waits for the input to be submitted (or to time out), then pauses before the next node — the user is told "STOPPING — waiting for input…".
6. RESET from PAUSED sends the robot to origin and transitions to a clean IDLE.

## Out of Scope

- Interrupting in-flight cure skills (would require rewriting each skill to be cancellable).
- Teleop page detecting workflow state and redirecting (nav-bar gate is sufficient for MVP).
- Persisting IDLE/PAUSED state across page refresh (paused session store is already in-memory; a refresh loses it).
- Multi-user coordination (e.g., two browsers sharing a session) — single-user assumption holds.
