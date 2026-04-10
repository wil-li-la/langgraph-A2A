# Backend Reset with Return-to-Origin — Design Spec

**Date:** 2026-04-10
**Status:** Approved

## Overview

The RESET button triggers a backend endpoint that runs the `return_to_origin` node, navigating the robot back to the charging dock. It clears any paused session state. The frontend shows the execution in the workflow graph, then resets all UI state when done.

## Backend

### New Endpoint: `POST /api/workflow/reset`

**Behavior:**
1. Clear all entries from `_paused_sessions`
2. Run the workflow graph with `resume_from="return_to_origin"` using `stream_execute`
3. Return SSE stream with same event format as `/api/workflow/execute/stream` (node_start, node_end, log, done)
4. Uses a fresh `AgentState` with `resume_from="return_to_origin"`, patient/medication set to empty strings, mode="manual"

**Route:** Added to `workflow_routes` list in `workflow_api.py`

## Frontend

### Updated `resetWorkflow` in `use-workflow.ts`

**Current:** Clears local state only (activeNodeId, executedNodes, executionLog, pause state)

**New behavior:**
1. Set `isResetting = true` (new state field)
2. Call `POST /api/workflow/reset` via SSE stream
3. Process SSE events same as regular execution (show node_start/node_end in graph, log entries)
4. On `done` event: clear all state (nodes, log, pause, session)
5. Set `isResetting = false`

### Updated `UseWorkflowResult` interface

Add:
- `isResetting: boolean`

### Updated RESET button in `robot-dashboard.tsx`

- Disabled when `isExecuting` OR `isResetting`
- Shows "RESETTING..." text when `isResetting` is true
- Shows "↺ RESET" when idle

## What doesn't change

- Teleop page
- Workflow execute/resume endpoints
- `return_to_origin_node` function
- Backend WebSocket relay
