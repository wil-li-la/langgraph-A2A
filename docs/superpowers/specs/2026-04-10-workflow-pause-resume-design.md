# Workflow Pause & Resume via Teleop — Design Spec

**Date:** 2026-04-10
**Status:** Approved

## Overview

When a workflow node fails during manual (dashboard) execution, instead of routing to `error_handler → return_to_origin → END`, the workflow **pauses**. The dashboard shows a guide UI explaining the failure and directing the user to teleop for manual adjustment. The workflow graph nodes become clickable — the user clicks any node to resume execution from that point.

## Decisions

| # | Question | Decision |
|---|----------|----------|
| 1 | Resume behavior on "Continue"? | User clicks any node in the graph to resume from |
| 2 | Which nodes clickable? | All nodes — full freedom |
| 3 | Where does teleop happen? | Separate `/teleop` page (existing), with guide UI on dashboard |

## Backend

### New SSE Event: `paused`

When a node's `task_status` indicates failure during streaming execution, the stream emits:

```json
{ "event": "paused", "session_id": "abc123", "node_id": "pickup_med", "reason": "IK solver unreachable position", "task_status": "pickup_failed" }
```

Then the stream ends. The workflow does **not** route to `error_handler`.

### Session State Store

Simple in-memory dict:

```python
_paused_sessions: dict[str, dict] = {}
```

- Keyed by session ID (UUID generated when workflow starts)
- Stores: `AgentState` at pause time, plus `patient_name` and `medication_name`
- Set when workflow pauses
- Read and cleared when workflow resumes
- No persistence — server restart clears paused sessions
- Session ID returned in all SSE events (`node_start`, `node_end`, `paused`, `done`) so the frontend can reference it on resume

### Modifications to `stream_execute` (medication_delivery.py)

After each node completes in `manual` mode, check if `task_status` indicates failure (i.e., would have routed to `handle_error` under the current logic). If so:

1. Yield a `"paused"` event with the failed node ID, error reason (from `errors` list or `task_status`), and session ID
2. Save the current accumulated `AgentState` to `_paused_sessions[session_id]`
3. Stop iteration — do not continue to `error_handler`

In `auto` mode, behavior is unchanged (still routes to `error_handler → return_to_origin → END`).

### New Endpoint: `POST /api/workflow/resume`

**Request body:**
```json
{ "session_id": "abc123", "node_id": "pickup_med" }
```

**Behavior:**
1. Look up `_paused_sessions[session_id]` — 404 if not found
2. Retrieve the saved `AgentState`
3. Reset `task_status` to a neutral value (clear the failure status) and clear `errors`
4. Execute the graph starting from `node_id` using the saved state
5. Return an SSE stream with the same event format as `/api/workflow/execute/stream` (including `node_start`, `node_end`, `log`, `paused`, `done`)
6. The same session ID is reused, so if a second failure occurs it pauses again with the same flow
7. On successful completion (`done` event), remove the session from `_paused_sessions`

**Starting from an arbitrary node:**

LangGraph's `app.stream()` doesn't directly support "start from node X". The approach: add a `resume_from: str` field to `AgentState`. Add a `resume_router` node as the graph's entry point that checks this field. If `resume_from` is set, it returns the state unchanged and a conditional edge routes to the specified node. If not set (normal execution), it routes to `confirm_task` as before. When resuming, set `resume_from` in the saved state and invoke the graph normally — the router jumps to the right node.

### Modifications to SSE Streaming Endpoint (workflow_api.py)

The `/api/workflow/execute/stream` endpoint changes:
- Generate a `session_id` (UUID) at the start
- Include `session_id` in all emitted SSE events
- Handle the new `"paused"` event type from `stream_execute` — emit it as SSE and end the stream

The `/api/workflow/resume` endpoint:
- New SSE streaming endpoint, same format as execute/stream
- Reads session state, invokes graph from the requested node
- Same SSE event handling (node_start, node_end, log, paused, done)

### Failure Detection

A node's output is considered a failure if its `task_status` would cause the existing conditional edge to route to `handle_error`. The detection reuses the same routing functions (`_route_or_error` pattern). Specifically, after each node_end, if the router function for that node would return `"handle_error"`, we pause instead.

This means the pause detection is consistent with the graph's own routing logic — no duplicate failure definitions.

## Frontend

### New State in `use-workflow` Hook

```typescript
isPaused: boolean          // workflow is paused on failure
pausedNodeId: string | null    // which node failed
pauseReason: string | null     // failure description
sessionId: string | null       // for resume API call
```

### New SSE Event Handler

```typescript
case "paused":
  setIsPaused(true)
  setPausedNodeId(event.node_id)
  setPauseReason(event.reason)
  setSessionId(event.session_id)
  setIsExecuting(false)
  break
```

All events also store `session_id` from the stream.

### New Function: `resumeWorkflow(nodeId: string)`

Calls `POST /api/workflow/resume` with `{ session_id, node_id }`, then consumes the SSE response using the same streaming logic as `startStreamExecution`. Resets `isPaused` and resumes execution tracking.

### Guide UI Component

New component shown in the operation mode / execution log area when `isPaused` is true:

```
┌─────────────────────────────────────────────────────────┐
│  ⚠ WORKFLOW PAUSED                                      │
│  Node "pickup_med" failed: IK solver unreachable        │
│                                                          │
│  1. Switch to Teleop to adjust the robot                 │
│     [Open Teleop →]                                      │
│  2. Click any node in the graph to resume from there     │
└─────────────────────────────────────────────────────────┘
```

- Terminal monochrome style, red accent for warning
- "Open Teleop" links to `/teleop`
- Shows failed node name and reason

### Clickable Workflow Graph Nodes

When `isPaused` is true:
- All nodes get a click handler
- Visual: cursor pointer, subtle hover border highlight
- Clicking a node calls `resumeWorkflow(nodeId)`
- On resume: `isPaused` resets, normal execution tracking resumes
- The clicked node becomes `activeNodeId` immediately

When `isPaused` is false (normal execution or idle):
- Nodes are not clickable (existing behavior preserved)

### Workflow Graph Visual State During Pause

- Completed nodes: keep `completed` status (green check)
- Failed node: show `error` status (red)
- Nodes after the failure: remain `pending`
- All nodes show a clickable hover effect

## What Doesn't Change

- Teleop page and all teleop components — untouched
- WebSocket relay — untouched
- A2A endpoint (`POST /`) — uses `mode="auto"`, keeps current `error_handler` → END behavior
- `error_handler_node` and `return_to_origin` — still exist in graph, used in auto mode
- Graph structure (nodes and edges) — the nodes themselves are unchanged, only the streaming/routing behavior changes in manual mode

## Out of Scope

- Persisting paused sessions across server restarts
- Multiple concurrent paused sessions (one at a time is fine for single-robot use)
- Timeout on paused sessions
- Undo/rollback of completed nodes
