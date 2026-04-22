# Dashboard State Machine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the three-state dashboard (IDLE / RUNNING / PAUSED) per `docs/superpowers/specs/2026-04-22-dashboard-state-machine-design.md` — graceful STOP, unified start/resume-from-any-node, teleop gated while workflow runs.

**Architecture:** Backend adds a `_stop_requests: set[str]` checked in `stream_execute` after each `node_end`. A new `POST /api/workflow/stop` endpoint adds a session_id to that set; a modified `POST /api/workflow/execute/stream` accepts an optional `start_from` body field. Frontend derives a `workflowState` value from existing `isExecuting`/`isPaused` flags, routes UI affordances (START/STOP/RESUME buttons, node-click behavior, teleop lock) from that single value.

**Tech Stack:** Python (Starlette, LangGraph), TypeScript (React, Next.js App Router). **No test framework is configured** — verification is manual via `curl` and browser.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `backend/app/healthcare/medication_delivery.py` | modify | Add `_stop_requests` module-level set; check in `stream_execute`; clear on `done` / `paused` |
| `backend/app/workflow_api.py` | modify | Add `POST /api/workflow/stop` endpoint. Extend `POST /api/workflow/execute/stream` to accept optional `start_from` body field |
| `frontend/lib/api.ts` | modify | Add `stopWorkflow()`; extend `executeWorkflowStream()` to pass `start_from` |
| `frontend/hooks/use-workflow.ts` | modify | Derive `workflowState`; add `stopWorkflow`, `startFromNode` actions |
| `frontend/components/workflow-controls.tsx` | create | Three-variant (IDLE / RUNNING / PAUSED) workflow control panel. Replaces `mode-toggle.tsx` for this use |
| `frontend/components/mode-toggle.tsx` | delete | Superseded by `workflow-controls.tsx` |
| `frontend/components/workflow-graph.tsx` | modify | Enable `onNodeClick` in IDLE state; extend hover highlight; disallow clicking start/end nodes |
| `frontend/components/nav-bar.tsx` | modify | Accept `workflowState` prop; disable Teleop link when `"running"` |
| `frontend/components/pause-guide.tsx` | modify | Copy varies by reason (user-stopped vs error) |
| `frontend/components/robot-dashboard.tsx` | modify | Pass `workflowState` to NavBar; import and render `WorkflowControls` instead of `ModeToggle` |

---

### Task 1: Backend — graceful stop flag

**Files:**
- Modify: `backend/app/healthcare/medication_delivery.py`

- [ ] **Step 1: Add `_stop_requests` set next to `_paused_sessions`**

Locate the existing `_paused_sessions` declaration (near line 34 after the imports). Add right below it:

```python
# Session IDs that have requested a graceful stop. Consumed by stream_execute
# after each node_end — when present, the workflow pauses via the existing
# _paused_sessions mechanism instead of continuing.
_stop_requests: set[str] = set()
```

- [ ] **Step 2: Check the flag in `stream_execute` after each node_end**

In `stream_execute` (around line 600 in the `for chunk in self.app.stream(...):` loop), the code currently yields `("node_end", node_id, ...)` then checks `_should_pause` for manual-mode errors. Extend the pause check to also respect `_stop_requests`.

Find this block (exact location may shift; search for `_should_pause`):

```python
                    # In manual mode, check if this node failed
                    if mode == "manual" and _should_pause(node_id, final_state):
                        errors = final_state.get("errors", [])
                        reason = errors[-1] if errors else final_state.get("task_status", "unknown error")
                        _paused_sessions[session_id] = dict(final_state)
                        yield ("paused", node_id, {
                            "session_id": session_id,
                            "reason": reason,
                            "task_status": final_state.get("task_status", ""),
                            "executed_nodes": list(executed_nodes),
                        })
                        paused = True
                        break
```

Replace it with:

```python
                    # In manual mode, check if this node failed OR user requested stop
                    user_stop = mode == "manual" and session_id in _stop_requests
                    node_failed = mode == "manual" and _should_pause(node_id, final_state)
                    if user_stop or node_failed:
                        if user_stop:
                            reason = "Stopped by user"
                            _stop_requests.discard(session_id)
                        else:
                            errors = final_state.get("errors", [])
                            reason = errors[-1] if errors else final_state.get("task_status", "unknown error")
                        _paused_sessions[session_id] = dict(final_state)
                        yield ("paused", node_id, {
                            "session_id": session_id,
                            "reason": reason,
                            "task_status": final_state.get("task_status", ""),
                            "executed_nodes": list(executed_nodes),
                        })
                        paused = True
                        break
```

- [ ] **Step 3: Clear the flag on normal completion**

In the same function, find the block at the end:

```python
        if not paused:
            self._print_summary(final_state, time.time() - start_time)
            rrd_path = RERUN_LOG_DIR / f"medication_delivery_{patient_name}_{int(time.time())}.rrd"
            rr.save(str(rrd_path))
            logger.info(f"Rerun log saved to {rrd_path}")
            _paused_sessions.pop(session_id, None)
            yield ("done", "", final_state)
```

Add `_stop_requests.discard(session_id)` next to the `_paused_sessions.pop`:

```python
        if not paused:
            self._print_summary(final_state, time.time() - start_time)
            rrd_path = RERUN_LOG_DIR / f"medication_delivery_{patient_name}_{int(time.time())}.rrd"
            rr.save(str(rrd_path))
            logger.info(f"Rerun log saved to {rrd_path}")
            _paused_sessions.pop(session_id, None)
            _stop_requests.discard(session_id)
            yield ("done", "", final_state)
```

- [ ] **Step 4: Verify backend imports cleanly**

Run:

```bash
cd "backend" && source .venv/bin/activate && python -c "
from app.healthcare.medication_delivery import _stop_requests, _paused_sessions, MedicationDeliveryAgent
print('stop_requests type:', type(_stop_requests).__name__)
print('agent init:', MedicationDeliveryAgent() is not None)
"
```

Expected output:
```
stop_requests type: set
agent init: True
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/healthcare/medication_delivery.py
git commit -m "feat(backend): add _stop_requests set for graceful workflow stop"
```

---

### Task 2: Backend — /api/workflow/stop endpoint and start_from param

**Files:**
- Modify: `backend/app/workflow_api.py`

- [ ] **Step 1: Import `_stop_requests`**

Near the top of `workflow_api.py`, extend the import of medication_delivery symbols:

```python
from app.healthcare.medication_delivery import (
    MedicationDeliveryAgent,
    create_medication_delivery_workflow,
    _paused_sessions,
    _stop_requests,
)
```

- [ ] **Step 2: Accept `start_from` in `execute_workflow_stream`**

In `execute_workflow_stream` (around line 180), after parsing `instruction` and `MockNLU.parse_instruction`, add:

```python
        start_from = body.get("start_from", "")
```

Then inside the `event_generator` async function, after `initial_state` is built (which happens inside `stream_execute`), we need to inject `resume_from`. The cleanest way: build a resume_state locally if `start_from` is set, and pass it to `stream_execute`.

Locate the call to `_agent.stream_execute(patient_name, medication_name, mode="manual", session_id=session_id)` inside `_run_stream`. Replace with:

```python
                try:
                    stream_kwargs = {
                        "mode": "manual",
                        "session_id": session_id,
                    }
                    if start_from:
                        stream_kwargs["resume_state"] = _agent._build_initial_state(
                            patient_name, medication_name, mode="manual"
                        )
                        stream_kwargs["resume_state"]["resume_from"] = start_from
                    for event_type, node_id, data in _agent.stream_execute(patient_name, medication_name, **stream_kwargs):
```

- [ ] **Step 3: Add `submit_workflow_stop` endpoint**

After the existing `submit_workflow_input` function (search for `async def submit_workflow_input`), add:

```python
async def submit_workflow_stop(request: Request) -> JSONResponse:
    """POST /api/workflow/stop — Request a graceful stop.

    Body: { "session_id": "..." }
    Adds the session_id to _stop_requests; stream_execute will pause after
    the current node finishes.
    """
    try:
        body = await request.json()
        session_id = body.get("session_id", "")
        if not session_id:
            return JSONResponse({"error": "Missing 'session_id'"}, status_code=400)

        _stop_requests.add(session_id)
        return JSONResponse({"status": "ok"})

    except Exception as e:
        logger.error(f"Stop request failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)
```

- [ ] **Step 4: Register the route**

In the `workflow_routes` list at the bottom of the file, add next to the other workflow routes:

```python
    Route("/api/workflow/stop", submit_workflow_stop, methods=["POST", "OPTIONS"]),
```

- [ ] **Step 5: Restart the backend and verify the endpoint exists**

```bash
pkill -f "python -m app" 2>/dev/null; sleep 2
cd "backend" && source .venv/bin/activate && python -m app --host localhost --port 9999 &
sleep 4
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:9999/api/workflow/stop -H "Content-Type: application/json" -d '{"session_id":"test"}'
```

Expected output:
```
200
```

(200 because adding to a set is idempotent and does not error when the session does not exist.)

- [ ] **Step 6: Commit**

```bash
git add backend/app/workflow_api.py
git commit -m "feat(backend): add /api/workflow/stop and start_from param"
```

---

### Task 3: Frontend API client — stopWorkflow + start_from

**Files:**
- Modify: `frontend/lib/api.ts`

- [ ] **Step 1: Extend `executeWorkflowStream` signature**

Find the existing function declaration:

```typescript
export async function executeWorkflowStream(
  instruction: string,
  callbacks: StreamCallbacks,
  signal?: AbortSignal,
): Promise<ExecutionResult> {
```

Replace with:

```typescript
export async function executeWorkflowStream(
  instruction: string,
  callbacks: StreamCallbacks,
  signal?: AbortSignal,
  options?: { start_from?: string },
): Promise<ExecutionResult> {
```

Then find the `body: JSON.stringify({ instruction })` line in that function and replace with:

```typescript
    body: JSON.stringify({ instruction, ...(options?.start_from ? { start_from: options.start_from } : {}) }),
```

- [ ] **Step 2: Add `stopWorkflow` function**

Directly after `submitWorkflowInput` function (near line 290), add:

```typescript
/**
 * Request a graceful stop. The backend pauses after the current node completes.
 */
export async function stopWorkflow(sessionId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/workflow/stop`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId }),
  })
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`Stop failed: ${res.status} ${body}`)
  }
}
```

- [ ] **Step 3: Type-check**

```bash
cd frontend && npx tsc --noEmit --pretty 2>&1 | head -20
```

Expected: no output (silent success).

- [ ] **Step 4: Commit**

```bash
git add frontend/lib/api.ts
git commit -m "feat(frontend): add stopWorkflow API and start_from param"
```

---

### Task 4: Frontend hook — workflowState derivation + new actions

**Files:**
- Modify: `frontend/hooks/use-workflow.ts`

- [ ] **Step 1: Add `WorkflowState` type + import `stopWorkflow`**

Update the import line:

```typescript
import { fetchWorkflow, fetchSkills, executeWorkflowStream, resumeWorkflowStream, resetWorkflowStream, submitWorkflowInput, stopWorkflow, type WorkflowData, type SkillsData, type ExecutionResult } from "@/lib/api"
```

Add right after the interface declaration `UseWorkflowResult`:

```typescript
export type WorkflowState = "idle" | "running" | "paused"
```

- [ ] **Step 2: Add fields to `UseWorkflowResult` interface**

Locate the interface and add these three fields (below `submitInput` or in a consistent location):

```typescript
  workflowState: WorkflowState
  stop: () => Promise<void>
  startFromNode: (nodeId: string, instruction: string) => Promise<ExecutionResult | null>
```

- [ ] **Step 3: Derive `workflowState` inside the hook body**

Locate this block near the bottom of the hook, just before the return:

```typescript
  const appendLog = useCallback((text: string) => {
    setExecutionLog((prev) => [...prev, text])
  }, [])
```

Add right after it:

```typescript
  const workflowState: WorkflowState = isPaused ? "paused" : isExecuting ? "running" : "idle"
```

- [ ] **Step 4: Add `stop` action**

After the `submitInput` useCallback, add:

```typescript
  const stop = useCallback(async () => {
    if (!sessionId) return
    setExecutionLog((prev) => [...prev, "⏸ Stop requested — waiting for current node to finish…"])
    try {
      await stopWorkflow(sessionId)
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Unknown error"
      setExecutionLog((prev) => [...prev, `✗ Stop request failed: ${msg}`])
    }
  }, [sessionId])
```

- [ ] **Step 5: Add `startFromNode` action**

Add directly after `stop`:

```typescript
  const startFromNode = useCallback(async (nodeId: string, instruction: string): Promise<ExecutionResult | null> => {
    if (!instruction.trim()) return null

    setIsExecuting(true)
    setActiveNodeId(null)
    setExecutedNodes([])
    setExecutionLog([])

    const controller = new AbortController()
    setAbortController(controller)

    try {
      const result = await executeWorkflowStream(instruction, {
        onNodeStart: (nid, executed) => {
          setActiveNodeId(nid)
          setExecutedNodes([...executed])
        },
        onNodeEnd: (nid, executed) => {
          setActiveNodeId(null)
          setExecutedNodes([...executed])
        },
        onLog: (text) => {
          setExecutionLog((prev) => [...prev, text])
        },
        onDone: (result) => {
          setExecutionLog((prev) => [...prev, `\n✓ Workflow completed: ${result.task_status}`])
        },
        onError: (errMsg) => {
          setExecutionLog((prev) => [...prev, `\n✗ Workflow error: ${errMsg}`])
        },
        onPaused: (nid, reason, sid) => {
          setIsPaused(true)
          setPausedNodeId(nid)
          setPauseReason(reason)
          setSessionId(sid)
          setExecutionLog((prev) => [...prev, `\n⚠ Workflow paused at ${nid}: ${reason}`])
        },
        onAwaitInput: (sid, prompt) => {
          setSessionId(sid)
          setInputPrompt(prompt)
          setAwaitingInput(true)
          setExecutionLog((prev) => [...prev, `\n🎤 Waiting for input: 「${prompt}」`])
        },
      }, controller.signal, { start_from: nodeId })
      return result
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        setExecutionLog((prev) => [...prev, `\n⚠️ Workflow execution stopped by user`])
        return null
      }
      const msg = err instanceof Error ? err.message : "Unknown error"
      setExecutionLog((prev) => [...prev, `✗ Error: ${msg}`])
      return null
    } finally {
      setIsExecuting(false)
      setActiveNodeId(null)
      setAbortController(null)
      setAwaitingInput(false)
      setInputPrompt("")
    }
  }, [])
```

- [ ] **Step 6: Expose the new fields in the return**

Locate the return statement at the bottom of the hook. Add three new fields alongside the existing ones:

```typescript
    workflowState,
    stop,
    startFromNode,
```

- [ ] **Step 7: Type-check**

```bash
cd frontend && npx tsc --noEmit --pretty 2>&1 | head -20
```

Expected: no output.

- [ ] **Step 8: Commit**

```bash
git add frontend/hooks/use-workflow.ts
git commit -m "feat(frontend): derive workflowState, add stop and startFromNode actions"
```

---

### Task 5: Frontend — WorkflowControls component (replaces ModeToggle)

**Files:**
- Create: `frontend/components/workflow-controls.tsx`

- [ ] **Step 1: Create the new component**

Write `frontend/components/workflow-controls.tsx`:

```tsx
"use client"

import { useEffect, useState } from "react"
import type { ExecutionResult } from "@/lib/api"
import type { WorkflowState } from "@/hooks/use-workflow"

type Mode = "manual" | "auto"

interface WorkflowControlsProps {
  workflowState: WorkflowState
  pausedNodeId: string | null
  pauseReason: string | null
  activeNodeId: string | null
  onStart: (instruction: string) => Promise<ExecutionResult | null>
  onStop: () => Promise<void>
  onResume: (nodeId: string) => Promise<ExecutionResult | null>
  onInstructionChange: (text: string) => void
  instruction: string
}

export function WorkflowControls({
  workflowState,
  pausedNodeId,
  pauseReason,
  activeNodeId,
  onStart,
  onStop,
  onResume,
  onInstructionChange,
  instruction,
}: WorkflowControlsProps) {
  const [mode, setMode] = useState<Mode>("manual")
  const [stopPending, setStopPending] = useState(false)

  const handleStart = async () => {
    if (!instruction.trim() || workflowState !== "idle") return
    await onStart(instruction.trim())
  }

  const handleStop = async () => {
    if (workflowState !== "running") return
    setStopPending(true)
    try {
      await onStop()
    } finally {
      // stopPending clears when workflowState transitions to paused/idle
    }
  }

  const handleResume = async () => {
    if (workflowState !== "paused" || !pausedNodeId) return
    await onResume(pausedNodeId)
  }

  // Clear stopPending once state has actually transitioned away from running
  useEffect(() => {
    if (stopPending && workflowState !== "running") {
      setStopPending(false)
    }
  }, [stopPending, workflowState])

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h2 className="font-mono text-lg font-medium tracking-wide text-foreground">
          WORKFLOW
        </h2>

        {/* Mode toggle — disabled while not idle */}
        <div className="flex rounded-full border border-border bg-background/50 p-0.5">
          <button
            onClick={() => workflowState === "idle" && setMode("manual")}
            disabled={workflowState !== "idle"}
            className={`rounded-full px-3 py-1 font-mono text-sm font-medium transition-colors ${
              mode === "manual" ? "bg-foreground text-background" : "text-muted-foreground hover:text-foreground"
            } disabled:opacity-50 disabled:cursor-not-allowed`}
          >
            MANUAL
          </button>
          <button
            onClick={() => workflowState === "idle" && setMode("auto")}
            disabled={workflowState !== "idle"}
            className={`rounded-full px-3 py-1 font-mono text-sm font-medium transition-colors ${
              mode === "auto" ? "bg-foreground text-background" : "text-muted-foreground hover:text-foreground"
            } disabled:opacity-50 disabled:cursor-not-allowed`}
          >
            AUTO
          </button>
        </div>
      </div>

      {workflowState === "idle" && mode === "manual" && (
        <div className="flex flex-col gap-2">
          <div className="flex gap-2">
            <input
              type="text"
              value={instruction}
              onChange={(e) => onInstructionChange(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleStart()}
              placeholder="請將阿斯匹靈送給張小明 …"
              className="min-w-0 flex-1 rounded-md border border-border bg-background px-3 py-2 font-mono text-sm text-foreground placeholder:text-muted-foreground/40 focus:outline-none focus:ring-1 focus:ring-foreground/20"
            />
            <button
              onClick={handleStart}
              disabled={!instruction.trim()}
              className="shrink-0 rounded-md border border-border bg-foreground px-4 py-2 font-mono text-sm font-medium text-background transition-opacity hover:opacity-90 disabled:opacity-30"
            >
              ▶ START
            </button>
          </div>
          <p className="font-mono text-xs text-muted-foreground/60">
            or click any node below to start from there
          </p>
        </div>
      )}

      {workflowState === "idle" && mode === "auto" && (
        <div className="flex items-center gap-2 rounded-md border border-dashed border-border bg-background/50 px-3 py-3">
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-foreground/30" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-foreground/60" />
          </span>
          <span className="font-mono text-sm text-muted-foreground">
            Listening for A2A requests …
          </span>
        </div>
      )}

      {workflowState === "running" && (
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2 font-mono text-sm">
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-sky-400/50" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-sky-400" />
            </span>
            <span className="text-foreground">
              Running {activeNodeId ? `— ${activeNodeId}` : ""}
            </span>
          </div>
          <button
            onClick={handleStop}
            disabled={stopPending}
            className="rounded-md border border-red-500/50 bg-red-500/10 px-4 py-2 font-mono text-sm font-medium text-red-500 transition-colors hover:bg-red-500/20 disabled:cursor-wait disabled:opacity-60"
          >
            {stopPending ? "STOPPING — waiting for current step…" : "■ STOP"}
          </button>
        </div>
      )}

      {workflowState === "paused" && (
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2 font-mono text-sm">
            <span className="h-2 w-2 rounded-full bg-amber-400" />
            <span className="text-foreground">
              Paused{pausedNodeId ? ` at ${pausedNodeId}` : ""}
            </span>
          </div>
          {pauseReason && (
            <p className="font-mono text-xs text-muted-foreground">{pauseReason}</p>
          )}
          <button
            onClick={handleResume}
            disabled={!pausedNodeId}
            className="rounded-md border border-border bg-foreground px-4 py-2 font-mono text-sm font-medium text-background transition-opacity hover:opacity-90 disabled:opacity-30"
          >
            ▶ RESUME {pausedNodeId ? `from ${pausedNodeId}` : ""}
          </button>
          <p className="font-mono text-xs text-muted-foreground/60">
            or click any node to resume from there
          </p>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Delete `mode-toggle.tsx`**

```bash
rm "frontend/components/mode-toggle.tsx"
```

- [ ] **Step 3: Commit**

```bash
git add frontend/components/workflow-controls.tsx frontend/components/mode-toggle.tsx
git commit -m "feat(frontend): add WorkflowControls with IDLE/RUNNING/PAUSED variants"
```

---

### Task 6: Frontend — graph node click in IDLE

**Files:**
- Modify: `frontend/components/workflow-graph.tsx`

- [ ] **Step 1: Update component props to accept `workflowState` instead of `isPaused`**

Near the top of the file, find the props interface:

```typescript
interface WorkflowGraphProps {
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
  activeNodeId?: string | null
  isPaused?: boolean
  onNodeClick?: (nodeId: string) => void
}
```

Replace with:

```typescript
interface WorkflowGraphProps {
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
  activeNodeId?: string | null
  workflowState?: "idle" | "running" | "paused"
  onNodeClick?: (nodeId: string) => void
}
```

- [ ] **Step 2: Update function signature**

Find:

```typescript
export function WorkflowGraph({ nodes, edges, activeNodeId, isPaused = false, onNodeClick }: WorkflowGraphProps) {
```

Replace with:

```typescript
export function WorkflowGraph({ nodes, edges, activeNodeId, workflowState = "idle", onNodeClick }: WorkflowGraphProps) {
  const clickable = workflowState === "idle" || workflowState === "paused"
```

- [ ] **Step 3: Update the click handler and hover outline**

Find the `<g>` element inside `renderNodes`:

```typescript
        <g
          key={node.id}
          onMouseEnter={() => setHoveredNode(node.id)}
          onMouseLeave={() => setHoveredNode(null)}
          onClick={() => {
            if (isPaused && onNodeClick && node.type !== "start" && node.type !== "end") {
              onNodeClick(node.id)
            } else {
              setSelectedNode(selectedNode === node.id ? null : node.id)
            }
          }}
          style={{ cursor: isPaused && node.type !== "start" && node.type !== "end" ? "pointer" : "default" }}
        >
```

Replace with:

```typescript
        <g
          key={node.id}
          onMouseEnter={() => setHoveredNode(node.id)}
          onMouseLeave={() => setHoveredNode(null)}
          onClick={() => {
            const canClick = clickable && node.type !== "start" && node.type !== "end"
            if (canClick && onNodeClick) {
              onNodeClick(node.id)
            } else {
              setSelectedNode(selectedNode === node.id ? null : node.id)
            }
          }}
          style={{ cursor: clickable && node.type !== "start" && node.type !== "end" ? "pointer" : "default" }}
        >
```

- [ ] **Step 4: Update the hover highlight condition**

Find the "Pause hover highlight" comment and its block:

```typescript
          {/* Pause hover highlight */}
          {isPaused && node.type !== "start" && node.type !== "end" && isHovered && (
            <rect
              x={x - 2} y={y - 2}
              width={NODE_W + 4} height={NODE_H + 4}
              rx={rx + 2} fill="none"
              stroke="rgba(56,189,248,0.5)" strokeWidth={1.5} strokeDasharray="4 2"
            />
          )}
```

Replace `isPaused` with `clickable`, and update the comment:

```typescript
          {/* Clickable hover highlight (IDLE or PAUSED) */}
          {clickable && node.type !== "start" && node.type !== "end" && isHovered && (
            <rect
              x={x - 2} y={y - 2}
              width={NODE_W + 4} height={NODE_H + 4}
              rx={rx + 2} fill="none"
              stroke="rgba(56,189,248,0.5)" strokeWidth={1.5} strokeDasharray="4 2"
            />
          )}
```

- [ ] **Step 5: Update the `useCallback` dependency array for `renderNodes`**

Find the end of the `renderNodes` useCallback:

```typescript
  }, [nodes, edges, layout.positions, hoveredNode, selectedNode, isPaused, onNodeClick])
```

Replace `isPaused` with `clickable`:

```typescript
  }, [nodes, edges, layout.positions, hoveredNode, selectedNode, clickable, onNodeClick])
```

- [ ] **Step 6: Type-check**

```bash
cd frontend && npx tsc --noEmit --pretty 2>&1 | head -20
```

Expected: a couple of errors in `robot-dashboard.tsx` because it still passes `isPaused` — fixed in Task 9. No errors inside workflow-graph.tsx itself.

- [ ] **Step 7: Commit**

```bash
git add frontend/components/workflow-graph.tsx
git commit -m "feat(frontend): enable node click in IDLE (via workflowState prop)"
```

---

### Task 7: Frontend — NavBar teleop link lock

**Files:**
- Modify: `frontend/components/nav-bar.tsx`

- [ ] **Step 1: Accept `workflowState` prop**

Replace the component declaration:

```typescript
export function NavBar() {
  const pathname = usePathname();
  const { robotHost, setRobotHost, isConnected, handleConnect, disconnect } = useRobotConnection();
```

With:

```typescript
interface NavBarProps {
  workflowState?: "idle" | "running" | "paused"
}

export function NavBar({ workflowState = "idle" }: NavBarProps) {
  const pathname = usePathname();
  const { robotHost, setRobotHost, isConnected, handleConnect, disconnect } = useRobotConnection();
  const teleopLocked = workflowState === "running"
```

- [ ] **Step 2: Gate the Teleop `<Link>`**

Find the nav rendering block:

```tsx
        {NAV_ITEMS.map((item) => {
            const isActive = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`rounded-md px-4 py-1.5 font-mono text-sm font-medium transition-colors ${
                  isActive
                    ? "bg-foreground/15 text-foreground"
                    : "text-muted-foreground hover:bg-foreground/10 hover:text-foreground"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
```

Replace with:

```tsx
        {NAV_ITEMS.map((item) => {
            const isActive = pathname === item.href;
            const locked = item.href === "/teleop" && teleopLocked

            if (locked) {
              return (
                <span
                  key={item.href}
                  title="Stop workflow to access teleop"
                  className="cursor-not-allowed rounded-md px-4 py-1.5 font-mono text-sm font-medium text-muted-foreground/40"
                >
                  {item.label} 🔒
                </span>
              )
            }

            return (
              <Link
                key={item.href}
                href={item.href}
                className={`rounded-md px-4 py-1.5 font-mono text-sm font-medium transition-colors ${
                  isActive
                    ? "bg-foreground/15 text-foreground"
                    : "text-muted-foreground hover:bg-foreground/10 hover:text-foreground"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/components/nav-bar.tsx
git commit -m "feat(frontend): disable teleop nav link while workflow is running"
```

---

### Task 8: Frontend — PauseGuide copy variants

**Files:**
- Modify: `frontend/components/pause-guide.tsx`

- [ ] **Step 1: Update to handle user-stopped vs error cases**

Replace the entire file contents:

```tsx
"use client"

import Link from "next/link"

interface PauseGuideProps {
  nodeId: string
  reason: string
}

function isUserStop(reason: string): boolean {
  return reason.toLowerCase().startsWith("stopped by user") || reason.toLowerCase().includes("stopped by user")
}

export function PauseGuide({ nodeId, reason }: PauseGuideProps) {
  const userStopped = isUserStop(reason)

  return (
    <div className={`rounded-md border p-4 ${
      userStopped ? "border-amber-400/30 bg-amber-400/5" : "border-red-400/30 bg-red-400/5"
    }`}>
      <div className="flex items-center gap-2 mb-2">
        <span className={`font-mono text-lg font-bold ${userStopped ? "text-amber-400" : "text-red-400"}`}>
          {userStopped ? "WORKFLOW STOPPED" : "WORKFLOW PAUSED"}
        </span>
      </div>
      <div className="font-mono text-sm text-muted-foreground mb-3">
        {userStopped
          ? `Paused at \"${nodeId}\" — teleop the robot as needed, then resume.`
          : `Node \"${nodeId}\" failed: ${reason}`}
      </div>
      <div className="space-y-2 font-mono text-sm text-foreground/80">
        <div className="flex items-start gap-2">
          <span className="text-muted-foreground shrink-0">1.</span>
          <span>
            {userStopped
              ? "Use teleop to adjust the robot if needed "
              : "Switch to Teleop to adjust the robot "}
            <Link
              href="/teleop"
              className="rounded border border-border px-3 py-1 text-sm font-medium text-foreground transition-colors hover:bg-foreground/10"
            >
              Open Teleop
            </Link>
          </span>
        </div>
        <div className="flex items-start gap-2">
          <span className="text-muted-foreground shrink-0">2.</span>
          <span>Press RESUME in the workflow panel, or click any node in the graph to resume from there.</span>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/components/pause-guide.tsx
git commit -m "feat(frontend): differentiate user-stopped vs error in PauseGuide"
```

---

### Task 9: Wire it all together in RobotDashboard

**Files:**
- Modify: `frontend/components/robot-dashboard.tsx`

- [ ] **Step 1: Update imports**

Find near the top:

```tsx
import { ModeToggle } from "@/components/mode-toggle"
```

Replace with:

```tsx
import { WorkflowControls } from "@/components/workflow-controls"
```

- [ ] **Step 2: Hold `instruction` state in the dashboard and destructure new hook fields**

Right below `"use client"` imports, the dashboard function destructures the hook. Find:

```tsx
  const {
    nodes,
    edges,
    skillsData,
    isLoading,
    isLive,
    isExecuting,
    activeNodeId,
    executionLog,
    progress,
    isPaused,
    pausedNodeId,
    pauseReason,
    resumeFromNode,
    resetWorkflow,
    startStreamExecution,
    stopStreamExecution,
    appendLog,
    awaitingInput,
    inputPrompt,
    submitInput,
  } = useWorkflow("stretch3")
```

Replace with (note: `isPaused` stays for the VoiceInput/paused-specific pieces, `stopStreamExecution` is removed since STOP now goes through the backend flag, and `workflowState` / `stop` / `startFromNode` are new):

```tsx
  const {
    nodes,
    edges,
    skillsData,
    isLoading,
    isLive,
    isExecuting,
    activeNodeId,
    executionLog,
    progress,
    isPaused,
    pausedNodeId,
    pauseReason,
    resumeFromNode,
    resetWorkflow,
    startStreamExecution,
    appendLog,
    awaitingInput,
    inputPrompt,
    submitInput,
    workflowState,
    stop,
    startFromNode,
  } = useWorkflow("stretch3")

  const [instruction, setInstruction] = useState("")
```

Add `useState` to the imports at the top (if not already present):

```tsx
import { useEffect, useRef, useState } from "react"
```

- [ ] **Step 3: Replace `<ModeToggle>` usage**

Find (roughly around line 125):

```tsx
          {/* Mode */}
          <div className="rounded-md border border-border bg-card p-3 shrink-0">
            <ModeToggle
              isExecuting={isExecuting}
              onStreamRun={startStreamExecution}
              onStreamStop={stopStreamExecution}
            />
          </div>
```

Replace with:

```tsx
          {/* Workflow controls */}
          <div className="rounded-md border border-border bg-card p-3 shrink-0">
            <WorkflowControls
              workflowState={workflowState}
              pausedNodeId={pausedNodeId}
              pauseReason={pauseReason}
              activeNodeId={activeNodeId}
              onStart={startStreamExecution}
              onStop={stop}
              onResume={resumeFromNode}
              onInstructionChange={setInstruction}
              instruction={instruction}
            />
          </div>
```

- [ ] **Step 4: Update `<WorkflowGraph>` usage**

Find:

```tsx
              <WorkflowGraph
                nodes={nodes}
                edges={edges}
                activeNodeId={activeNodeId}
                isPaused={isPaused}
                onNodeClick={resumeFromNode}
              />
```

Replace with:

```tsx
              <WorkflowGraph
                nodes={nodes}
                edges={edges}
                activeNodeId={activeNodeId}
                workflowState={workflowState}
                onNodeClick={(nodeId) => {
                  if (workflowState === "paused") {
                    resumeFromNode(nodeId)
                  } else if (workflowState === "idle" && instruction.trim()) {
                    startFromNode(nodeId, instruction.trim())
                  }
                }}
              />
```

- [ ] **Step 5: Pass `workflowState` to NavBar**

Find the NavBar render:

```tsx
      <NavBar />
```

Replace with:

```tsx
      <NavBar workflowState={workflowState} />
```

- [ ] **Step 6: Type-check**

```bash
cd frontend && npx tsc --noEmit --pretty 2>&1 | head -20
```

Expected: no output.

- [ ] **Step 7: Commit**

```bash
git add frontend/components/robot-dashboard.tsx
git commit -m "feat(frontend): wire WorkflowControls, node-click-in-idle, teleop lock"
```

---

### Task 10: Integration verification

**Files:** none changed, manual testing only.

- [ ] **Step 1: Restart both services**

```bash
pkill -f "python -m app" 2>/dev/null
pkill -f "next dev" 2>/dev/null
sleep 2
cd "backend" && source .venv/bin/activate && python -m app --host localhost --port 9999 &
cd frontend && pnpm dev &
sleep 5
```

Verify both services are up:

```bash
curl -s -o /dev/null -w "backend: %{http_code}\n" http://localhost:9999/api/workflow
curl -s -o /dev/null -w "frontend: %{http_code}\n" http://localhost:3000
```

Expected:
```
backend: 200
frontend: 200
```

- [ ] **Step 2: IDLE → START → normal completion → IDLE**

In the browser (localhost:3000 or the cloudflare dashboard URL):

1. Confirm the WORKFLOW panel shows "MANUAL/AUTO" toggle + instruction input + START button.
2. Confirm the Teleop nav link is enabled (not locked).
3. Enter `請將阿斯匹靈送給張小明` and press START.
4. Confirm the WORKFLOW panel transitions to "Running — <node>" + STOP button.
5. Confirm the Teleop nav link shows 🔒 and is grey/unclickable.
6. Let the workflow complete naturally (or pass identity via VoiceInput).
7. Confirm panel returns to IDLE (START button shown), Teleop link re-enables, completed nodes stay green.

- [ ] **Step 3: IDLE → click-node → start-from-node**

1. In an IDLE dashboard with an instruction filled in (e.g., `請將阿斯匹靈送給張小明`), hover any intermediate node (e.g., `pickup_med`) — it should show the sky-blue dashed highlight indicating clickable.
2. Click `pickup_med`.
3. Confirm the workflow starts and the first event is `node_start` for `pickup_med` (visible in the Execution Log).
4. Let it proceed; should continue from there.

- [ ] **Step 4: RUNNING → STOP → PAUSED**

1. Start a fresh workflow with `請將阿斯匹靈送給張小明`.
2. When the robot is mid-`nav_to_pharmacy` (or any running node), press STOP.
3. Confirm the button text changes to "STOPPING — waiting for current step…" and is disabled.
4. Confirm when the current node finishes, the panel transitions to "Paused at <node>" with a RESUME button.
5. Confirm the Teleop nav link re-enables.
6. Confirm PauseGuide shows amber "WORKFLOW STOPPED" styling (not red "WORKFLOW PAUSED").

- [ ] **Step 5: PAUSED → RESUME**

1. From the paused state in Step 4, press RESUME.
2. Confirm the workflow continues from the paused node.

- [ ] **Step 6: PAUSED → click-different-node**

1. Stop another workflow mid-run.
2. Instead of RESUME, click a different node in the graph.
3. Confirm the workflow resumes from that clicked node.

- [ ] **Step 7: Error-pause still works with red styling**

1. Start a workflow with an unknown patient, e.g., `請將阿斯匹靈送給不存在的病患`.
2. The `confirm_task` node should fail (patient_not_found).
3. Confirm the PauseGuide shows **red** "WORKFLOW PAUSED" styling (not amber) and displays the error reason.

- [ ] **Step 8: RESET from PAUSED returns to IDLE**

1. From any paused state, press RESET.
2. Confirm the robot runs return_to_origin and the panel returns to IDLE with cleared state.

- [ ] **Step 9: Commit verification log (optional)**

If you made any doc or minor fix along the way:

```bash
git add -A && git commit -m "docs: integration verification log for dashboard state machine"
```

Otherwise no commit required.
