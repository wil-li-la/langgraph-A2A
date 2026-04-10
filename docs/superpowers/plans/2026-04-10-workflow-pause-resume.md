# Workflow Pause & Resume Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a workflow node fails in manual mode, pause execution, show a guide UI directing users to teleop, and let them click any graph node to resume from there.

**Architecture:** Backend `stream_execute` detects failures (by checking if the routing function would send to `handle_error`) and emits a `paused` SSE event instead. State is saved to an in-memory session store. A new `/api/workflow/resume` endpoint retrieves the saved state and re-runs the graph from the requested node using a `resume_router` entry point. Frontend adds pause state to the workflow hook, a guide UI panel, and clickable graph nodes during pause.

**Tech Stack:** Python (LangGraph StateGraph, Starlette), Next.js (React hooks, SSE), TypeScript

---

### Task 1: Backend — Session Store and AgentState Changes

**Files:**
- Modify: `backend/app/healthcare/medication_delivery.py`

- [ ] **Step 1: Add `resume_from` field to `AgentState`**

In `backend/app/healthcare/medication_delivery.py`, add `resume_from` to the `AgentState` TypedDict (after `executed_nodes` on line 46):

```python
class AgentState(TypedDict):
    """State definition for medication delivery workflow."""
    patient_name: str
    medication_name: str
    current_location: str
    task_status: str
    target_detected: bool
    identity_verified: bool
    identity_check_retries: int
    mode: str  # "manual" (dashboard) or "auto" (A2A from external agent)
    errors: Annotated[List[str], operator.add]
    history: Annotated[List[str], operator.add]
    executed_nodes: Annotated[List[str], operator.add]
    resume_from: str  # node ID to resume from (empty string = normal start)
```

- [ ] **Step 2: Add `resume_router` node function**

Add this function after the existing node functions (after `return_to_origin_node`, before the conditional edge functions section around line 347):

```python
def resume_router_node(state: AgentState) -> dict:
    """Entry point router — passes through state unchanged.
    
    The conditional edge after this node reads `resume_from` to decide
    which node to jump to. If `resume_from` is empty, routes to
    confirm_task (normal start).
    """
    _log_node_entry("resume_router", state)
    return {"executed_nodes": ["resume_router"]}
```

- [ ] **Step 3: Add routing function for `resume_router`**

Add this after the existing conditional edge functions (after `should_continue_after_identity`, around line 383):

```python
# All node IDs that are valid resume targets
_VALID_RESUME_NODES = {
    "confirm_task", "nav_to_pharmacy", "pickup_med",
    "nav_to_patient", "delivery", "check_patient_identity",
    "return_to_origin",
}


def route_from_resume_router(state: AgentState) -> str:
    """Route to resume_from node if set, otherwise normal start."""
    target = state.get("resume_from", "")
    if target and target in _VALID_RESUME_NODES:
        return target
    return "confirm_task"
```

- [ ] **Step 4: Update `create_medication_delivery_workflow` to use resume_router**

Replace the workflow construction function:

```python
def create_medication_delivery_workflow() -> StateGraph:
    """Create and compile the medication delivery workflow graph."""
    workflow = StateGraph(AgentState)

    # Nodes
    workflow.add_node("resume_router", resume_router_node)
    workflow.add_node("confirm_task", confirm_task_node)
    workflow.add_node("nav_to_pharmacy", navigate_to_pharmacy_node)
    workflow.add_node("pickup_med", pickup_medication_node)
    workflow.add_node("nav_to_patient", navigate_to_patient_node)
    workflow.add_node("delivery", deliver_to_patient_node)
    workflow.add_node("check_patient_identity", check_patient_identity_node)
    workflow.add_node("handle_error", error_handler_node)
    workflow.add_node("return_to_origin", return_to_origin_node)

    # Entry — always goes through resume_router
    workflow.set_entry_point("resume_router")

    # Resume router conditional edge — routes to resume_from target or confirm_task
    workflow.add_conditional_edges("resume_router", route_from_resume_router, {
        "confirm_task": "confirm_task",
        "nav_to_pharmacy": "nav_to_pharmacy",
        "pickup_med": "pickup_med",
        "nav_to_patient": "nav_to_patient",
        "delivery": "delivery",
        "check_patient_identity": "check_patient_identity",
        "return_to_origin": "return_to_origin",
    })

    # Edges (unchanged)
    workflow.add_conditional_edges("confirm_task", should_continue_after_confirm,
                                   {"nav_to_pharmacy": "nav_to_pharmacy", "handle_error": "handle_error"})
    workflow.add_edge("nav_to_pharmacy", "pickup_med")
    workflow.add_conditional_edges("pickup_med", should_continue_after_pickup,
                                   {"nav_to_patient": "nav_to_patient", "handle_error": "handle_error"})
    workflow.add_conditional_edges("nav_to_patient", should_continue_after_nav_to_patient,
                                   {"delivery": "delivery", "handle_error": "handle_error"})
    workflow.add_conditional_edges("delivery", should_continue_after_delivery,
                                   {"check_patient_identity": "check_patient_identity", "handle_error": "handle_error"})
    workflow.add_conditional_edges("check_patient_identity", should_continue_after_identity,
                                   {"return_to_origin": "return_to_origin", 
                                    "check_patient_identity": "check_patient_identity",
                                    "handle_error": "handle_error"})
    workflow.add_edge("handle_error", "return_to_origin")
    workflow.add_edge("return_to_origin", END)

    return workflow.compile()
```

- [ ] **Step 5: Update `_build_initial_state` to include `resume_from`**

In the `MedicationDeliveryAgent._build_initial_state` method, add the new field:

```python
    def _build_initial_state(
        self, patient_name: str, medication_name: str, *, mode: str = "auto"
    ) -> AgentState:
        """Build the initial AgentState dict."""
        return {
            "patient_name": patient_name,
            "medication_name": medication_name,
            "current_location": "charging_dock",
            "task_status": "initialized",
            "target_detected": False,
            "identity_verified": False,
            "identity_check_retries": 0,
            "mode": mode,
            "errors": [],
            "history": [],
            "executed_nodes": [],
            "resume_from": "",
        }
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/healthcare/medication_delivery.py
git commit -m "feat(backend): add resume_router and resume_from state field"
```

---

### Task 2: Backend — Pause Detection in stream_execute

**Files:**
- Modify: `backend/app/healthcare/medication_delivery.py`

- [ ] **Step 1: Add failure detection helper**

Add this helper function after `_VALID_RESUME_NODES` (around line 390, before `create_medication_delivery_workflow`):

```python
# Map each node to the router function that decides its next step.
# If the router returns "handle_error", the node has failed.
_NODE_ROUTERS = {
    "confirm_task": should_continue_after_confirm,
    "pickup_med": should_continue_after_pickup,
    "nav_to_patient": should_continue_after_nav_to_patient,
    "delivery": should_continue_after_delivery,
    "check_patient_identity": should_continue_after_identity,
}


def _should_pause(node_id: str, state: dict) -> bool:
    """Check if a node's output would route to handle_error."""
    router = _NODE_ROUTERS.get(node_id)
    if router is None:
        return False
    return router(state) == "handle_error"
```

- [ ] **Step 2: Add session store**

Add this module-level dict near the top of the file (after the `_rr_initialized` global, around line 31):

```python
# In-memory store for paused workflow sessions.
# Key: session_id (str), Value: dict with AgentState snapshot.
_paused_sessions: dict[str, dict] = {}
```

- [ ] **Step 3: Modify `stream_execute` to detect failures and pause**

Replace the `stream_execute` method with this version that adds pause detection for manual mode:

```python
    def stream_execute(
        self, patient_name: str, medication_name: str, *, mode: str = "auto",
        session_id: str = "", resume_state: dict | None = None,
    ) -> Generator[Tuple[str, str, dict], None, None]:
        """Execute workflow with per-node streaming.

        Yields (event_type, node_id, data) tuples:
          - ("node_start", node_id, {executed_nodes so far})
          - ("node_end",   node_id, {executed_nodes, history, task_status})
          - ("paused",     node_id, {session_id, reason, task_status, executed_nodes})
          - ("done",        "",      {full final state})

        In manual mode, if a node fails (its router would send to handle_error),
        the stream pauses instead. The state is saved to _paused_sessions so it
        can be resumed later via resume_state.
        """
        if not _rr_initialized:
            rr.init("medication_delivery", spawn=False)
        _setup_cure_loggers()

        start_time = time.time()
        logger.info(f"\n{'#'*60}\n# 給藥任務開始 (streaming, mode={mode})\n{'#'*60}")

        if resume_state is not None:
            initial_state = resume_state
        else:
            initial_state = self._build_initial_state(patient_name, medication_name, mode=mode)

        executed_nodes: list[str] = list(initial_state.get("executed_nodes", []))
        final_state = dict(initial_state)

        paused = False
        try:
            for chunk in self.app.stream(initial_state):
                for node_id, state_update in chunk.items():
                    # Skip the resume_router node in events — it's internal
                    if node_id == "resume_router":
                        final_state.update(state_update)
                        new_nodes = state_update.get("executed_nodes", [])
                        executed_nodes.extend(new_nodes)
                        final_state["executed_nodes"] = list(executed_nodes)
                        continue

                    # Emit node_start
                    yield ("node_start", node_id, {
                        "executed_nodes": list(executed_nodes),
                        "session_id": session_id,
                    })

                    # Merge state update
                    final_state.update(state_update)
                    new_nodes = state_update.get("executed_nodes", [])
                    executed_nodes.extend(new_nodes)
                    final_state["executed_nodes"] = list(executed_nodes)

                    # Emit node_end
                    yield ("node_end", node_id, {
                        "executed_nodes": list(executed_nodes),
                        "history": state_update.get("history", []),
                        "task_status": state_update.get("task_status", ""),
                        "session_id": session_id,
                    })

                    # In manual mode, check if this node failed
                    if mode == "manual" and _should_pause(node_id, final_state):
                        errors = final_state.get("errors", [])
                        reason = errors[-1] if errors else final_state.get("task_status", "unknown error")
                        # Save state for resume
                        _paused_sessions[session_id] = dict(final_state)
                        yield ("paused", node_id, {
                            "session_id": session_id,
                            "reason": reason,
                            "task_status": final_state.get("task_status", ""),
                            "executed_nodes": list(executed_nodes),
                        })
                        paused = True
                        break
                if paused:
                    break

        except KeyboardInterrupt:
            logger.warning("任務被使用者手動中斷 (KeyboardInterrupt)")
            final_state["task_status"] = "interrupted"
            final_state["history"].append("⚠️ 任務被手動中斷")

        if not paused:
            self._print_summary(final_state, time.time() - start_time)
            rrd_path = RERUN_LOG_DIR / f"medication_delivery_{patient_name}_{int(time.time())}.rrd"
            rr.save(str(rrd_path))
            logger.info(f"Rerun log saved to {rrd_path}")
            # Clean up session on successful completion
            _paused_sessions.pop(session_id, None)
            yield ("done", "", final_state)
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/healthcare/medication_delivery.py
git commit -m "feat(backend): add pause detection and session store to stream_execute"
```

---

### Task 3: Backend — SSE Streaming and Resume Endpoint

**Files:**
- Modify: `backend/app/workflow_api.py`

- [ ] **Step 1: Add uuid import and session import**

At the top of `backend/app/workflow_api.py`, add `uuid` to imports (line 1 area):

```python
import uuid
```

Also update the import from `medication_delivery` to include `_paused_sessions`:

```python
from app.healthcare.medication_delivery import (
    MedicationDeliveryAgent,
    create_medication_delivery_workflow,
    _paused_sessions,
)
```

- [ ] **Step 2: Update `execute_workflow_stream` to include session_id and handle paused events**

Replace the `event_generator` inner function and thread function in `execute_workflow_stream`. The key changes:
1. Generate a `session_id` at the start
2. Pass `session_id` to `stream_execute`
3. Handle the new `"paused"` event type

Replace the `event_generator` function (lines 184-281) with:

```python
        session_id = str(uuid.uuid4())

        async def event_generator() -> AsyncGenerator[str, None]:
            """Run stream_execute in a background thread, intercept stdout, and yield SSE lines."""
            loop = asyncio.get_event_loop()
            q: asyncio.Queue = asyncio.Queue()

            def _run_stream():
                thread_id = threading.get_ident()

                class QueueLogHandler(logging.Handler):
                    def emit(self, record):
                        if threading.get_ident() == thread_id:
                            text = f"{record.getMessage()}"
                            if text.strip():
                                try:
                                    if not loop.is_closed():
                                        loop.call_soon_threadsafe(q.put_nowait, {"type": "stdout", "text": text.rstrip("\n")})
                                except RuntimeError:
                                    pass

                queue_handler = QueueLogHandler()
                queue_handler.setLevel(logging.INFO)
                
                logging.getLogger().addHandler(queue_handler)
                logging.getLogger("app.healthcare.medication_delivery").addHandler(queue_handler)
                logging.getLogger("cure").addHandler(queue_handler)

                try:
                    for event_type, node_id, data in _agent.stream_execute(
                        patient_name, medication_name, mode="manual", session_id=session_id
                    ):
                        try:
                            if not loop.is_closed():
                                loop.call_soon_threadsafe(q.put_nowait, {
                                    "type": "langgraph", 
                                    "event_type": event_type, 
                                    "node_id": node_id, 
                                    "data": data
                                })
                        except RuntimeError:
                            break
                except Exception as e:
                    logger.error(f"Error in stream thread: {e}", exc_info=True)
                    try:
                        if not loop.is_closed():
                            loop.call_soon_threadsafe(q.put_nowait, {"type": "error", "error": str(e)})
                    except RuntimeError:
                        pass
                finally:
                    logging.getLogger().removeHandler(queue_handler)
                    logging.getLogger("app.healthcare.medication_delivery").removeHandler(queue_handler)
                    logging.getLogger("cure").removeHandler(queue_handler)
                    try:
                        if not loop.is_closed():
                            loop.call_soon_threadsafe(q.put_nowait, None)
                    except RuntimeError:
                        pass

            thread = threading.Thread(target=_run_stream)
            thread.start()

            while True:
                item = await q.get()
                if item is None:
                    break
                
                if item["type"] == "stdout":
                    payload = json.dumps({"event": "log", "text": item["text"]}, ensure_ascii=False)
                    yield f"data: {payload}\n\n"
                    
                elif item["type"] == "langgraph":
                    event_type = item["event_type"]
                    node_id = item["node_id"]
                    data = item["data"]
                    
                    if event_type == "done":
                        result = {
                            "task_status": data.get("task_status"),
                            "patient_name": data.get("patient_name"),
                            "medication_name": data.get("medication_name"),
                            "current_location": data.get("current_location"),
                            "target_detected": data.get("target_detected"),
                            "identity_verified": data.get("identity_verified"),
                            "errors": data.get("errors", []),
                            "history": data.get("history", []),
                            "executed_nodes": data.get("executed_nodes", []),
                        }
                        payload = json.dumps({"event": "done", "result": result, "session_id": session_id}, ensure_ascii=False)
                    elif event_type == "paused":
                        payload = json.dumps({
                            "event": "paused",
                            "node_id": node_id,
                            "session_id": data.get("session_id", session_id),
                            "reason": data.get("reason", ""),
                            "task_status": data.get("task_status", ""),
                            "executed_nodes": data.get("executed_nodes", []),
                        }, ensure_ascii=False)
                    else:
                        payload = json.dumps({
                            "event": event_type,
                            "node_id": node_id,
                            "session_id": session_id,
                            **{k: v for k, v in data.items() if k != "session_id"},
                        }, ensure_ascii=False)
                    yield f"data: {payload}\n\n"
                    
                elif item["type"] == "error":
                    payload = json.dumps({"event": "error", "error": item["error"]}, ensure_ascii=False)
                    yield f"data: {payload}\n\n"
```

- [ ] **Step 3: Add the resume endpoint**

Add this new endpoint function after `execute_workflow_stream` (before `get_skills`):

```python
async def resume_workflow_stream(request: Request) -> StreamingResponse:
    """POST /api/workflow/resume — Resume a paused workflow from a specific node.

    Body: { "session_id": "abc123", "node_id": "pickup_med" }

    Retrieves the saved state, resets failure status, and re-runs the graph
    starting from the requested node. Returns an SSE stream with the same
    event format as /api/workflow/execute/stream.
    """
    try:
        body = await request.json()
        session_id = body.get("session_id", "")
        node_id = body.get("node_id", "")

        if not session_id or not node_id:
            return JSONResponse(
                {"error": "Missing 'session_id' or 'node_id'"},
                status_code=400,
            )

        saved_state = _paused_sessions.get(session_id)
        if saved_state is None:
            return JSONResponse(
                {"error": f"No paused session found for {session_id}"},
                status_code=404,
            )

        # Prepare resume state: clear failure, set resume target
        resume_state = dict(saved_state)
        resume_state["task_status"] = "resuming"
        resume_state["errors"] = []
        resume_state["resume_from"] = node_id

        patient_name = resume_state.get("patient_name", "")
        medication_name = resume_state.get("medication_name", "")

        async def event_generator() -> AsyncGenerator[str, None]:
            loop = asyncio.get_event_loop()
            q: asyncio.Queue = asyncio.Queue()

            def _run_stream():
                thread_id = threading.get_ident()

                class QueueLogHandler(logging.Handler):
                    def emit(self, record):
                        if threading.get_ident() == thread_id:
                            text = f"{record.getMessage()}"
                            if text.strip():
                                try:
                                    if not loop.is_closed():
                                        loop.call_soon_threadsafe(q.put_nowait, {"type": "stdout", "text": text.rstrip("\n")})
                                except RuntimeError:
                                    pass

                queue_handler = QueueLogHandler()
                queue_handler.setLevel(logging.INFO)
                logging.getLogger().addHandler(queue_handler)
                logging.getLogger("app.healthcare.medication_delivery").addHandler(queue_handler)
                logging.getLogger("cure").addHandler(queue_handler)

                try:
                    for event_type, node_id_ev, data in _agent.stream_execute(
                        patient_name, medication_name, mode="manual",
                        session_id=session_id, resume_state=resume_state,
                    ):
                        try:
                            if not loop.is_closed():
                                loop.call_soon_threadsafe(q.put_nowait, {
                                    "type": "langgraph",
                                    "event_type": event_type,
                                    "node_id": node_id_ev,
                                    "data": data
                                })
                        except RuntimeError:
                            break
                except Exception as e:
                    logger.error(f"Error in resume stream thread: {e}", exc_info=True)
                    try:
                        if not loop.is_closed():
                            loop.call_soon_threadsafe(q.put_nowait, {"type": "error", "error": str(e)})
                    except RuntimeError:
                        pass
                finally:
                    logging.getLogger().removeHandler(queue_handler)
                    logging.getLogger("app.healthcare.medication_delivery").removeHandler(queue_handler)
                    logging.getLogger("cure").removeHandler(queue_handler)
                    try:
                        if not loop.is_closed():
                            loop.call_soon_threadsafe(q.put_nowait, None)
                    except RuntimeError:
                        pass

            thread = threading.Thread(target=_run_stream)
            thread.start()

            while True:
                item = await q.get()
                if item is None:
                    break

                if item["type"] == "stdout":
                    payload = json.dumps({"event": "log", "text": item["text"]}, ensure_ascii=False)
                    yield f"data: {payload}\n\n"
                elif item["type"] == "langgraph":
                    event_type = item["event_type"]
                    ev_node_id = item["node_id"]
                    data = item["data"]

                    if event_type == "done":
                        result = {
                            "task_status": data.get("task_status"),
                            "patient_name": data.get("patient_name"),
                            "medication_name": data.get("medication_name"),
                            "current_location": data.get("current_location"),
                            "target_detected": data.get("target_detected"),
                            "identity_verified": data.get("identity_verified"),
                            "errors": data.get("errors", []),
                            "history": data.get("history", []),
                            "executed_nodes": data.get("executed_nodes", []),
                        }
                        payload = json.dumps({"event": "done", "result": result, "session_id": session_id}, ensure_ascii=False)
                    elif event_type == "paused":
                        payload = json.dumps({
                            "event": "paused",
                            "node_id": ev_node_id,
                            "session_id": data.get("session_id", session_id),
                            "reason": data.get("reason", ""),
                            "task_status": data.get("task_status", ""),
                            "executed_nodes": data.get("executed_nodes", []),
                        }, ensure_ascii=False)
                    else:
                        payload = json.dumps({
                            "event": event_type,
                            "node_id": ev_node_id,
                            "session_id": session_id,
                            **{k: v for k, v in data.items() if k != "session_id"},
                        }, ensure_ascii=False)
                    yield f"data: {payload}\n\n"
                elif item["type"] == "error":
                    payload = json.dumps({"event": "error", "error": item["error"]}, ensure_ascii=False)
                    yield f"data: {payload}\n\n"

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    except Exception as e:
        logger.error(f"Resume execution failed: {e}")
        return JSONResponse(
            {"error": str(e)},
            status_code=500,
        )
```

- [ ] **Step 4: Add the resume route to the route list**

In the `workflow_routes` list at the bottom of the file, add:

```python
    Route("/api/workflow/resume", resume_workflow_stream, methods=["POST"]),
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/workflow_api.py
git commit -m "feat(backend): add session_id to SSE events and /api/workflow/resume endpoint"
```

---

### Task 4: Frontend — API and Hook Changes

**Files:**
- Modify: `frontend/lib/api.ts`
- Modify: `frontend/hooks/use-workflow.ts`

- [ ] **Step 1: Update `StreamEvent` and `StreamCallbacks` in `api.ts`**

In `frontend/lib/api.ts`, update the `StreamEvent` interface to include `paused`:

```typescript
/** SSE event types emitted by the streaming endpoint. */
export interface StreamEvent {
  event: "node_start" | "node_end" | "done" | "error" | "log" | "paused"
  node_id?: string
  executed_nodes?: string[]
  history?: string[]
  task_status?: string
  result?: ExecutionResult
  text?: string
  session_id?: string
  reason?: string
}
```

Update `StreamCallbacks` to include `onPaused`:

```typescript
export interface StreamCallbacks {
  onNodeStart?: (nodeId: string, executedNodes: string[]) => void
  onNodeEnd?: (nodeId: string, executedNodes: string[], history: string[]) => void
  onDone?: (result: ExecutionResult) => void
  onError?: (error: string) => void
  onLog?: (text: string) => void
  onPaused?: (nodeId: string, reason: string, sessionId: string) => void
}
```

- [ ] **Step 2: Add `paused` case to `executeWorkflowStream`**

In the `switch (event.event)` block inside `executeWorkflowStream`, add a case for `paused` (after the `error` case):

```typescript
          case "paused":
            callbacks.onPaused?.(
              event.node_id ?? "",
              event.reason ?? "",
              event.session_id ?? "",
            )
            break
```

Also update the final check — a paused stream should not throw. Change the end of the function:

```typescript
  if (!finalResult) {
    // Stream may have ended due to pause — that's OK
    return null as unknown as ExecutionResult
  }

  return finalResult
```

- [ ] **Step 3: Add `resumeWorkflowStream` function**

Add this new function after `executeWorkflowStream`:

```typescript
/**
 * Resume a paused workflow from a specific node via SSE streaming.
 */
export async function resumeWorkflowStream(
  sessionId: string,
  nodeId: string,
  callbacks: StreamCallbacks,
  signal?: AbortSignal,
): Promise<ExecutionResult | null> {
  const res = await fetch(`${API_BASE}/api/workflow/resume`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, node_id: nodeId }),
    signal,
  })

  if (!res.ok) {
    const body = await res.text()
    throw new Error(`Resume failed: ${res.status} ${body}`)
  }

  const reader = res.body?.getReader()
  if (!reader) throw new Error("No readable stream")

  const decoder = new TextDecoder()
  let buffer = ""
  let finalResult: ExecutionResult | null = null

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })

    const lines = buffer.split("\n\n")
    buffer = lines.pop() ?? ""

    for (const line of lines) {
      const trimmed = line.trim()
      if (!trimmed.startsWith("data: ")) continue

      try {
        const event: StreamEvent = JSON.parse(trimmed.slice(6))

        switch (event.event) {
          case "log":
            if (event.text) callbacks.onLog?.(event.text)
            break
          case "node_start":
            callbacks.onNodeStart?.(event.node_id ?? "", event.executed_nodes ?? [])
            break
          case "node_end":
            callbacks.onNodeEnd?.(
              event.node_id ?? "",
              event.executed_nodes ?? [],
              event.history ?? [],
            )
            break
          case "done":
            finalResult = event.result ?? null
            callbacks.onDone?.(finalResult!)
            break
          case "paused":
            callbacks.onPaused?.(
              event.node_id ?? "",
              event.reason ?? "",
              event.session_id ?? "",
            )
            break
          case "error":
            callbacks.onError?.(event.node_id ?? "Unknown error")
            break
        }
      } catch {
        // ignore malformed JSON
      }
    }
  }

  return finalResult
}
```

- [ ] **Step 4: Update `use-workflow.ts` — add pause state and resume function**

In `frontend/hooks/use-workflow.ts`, add the new imports:

```typescript
import { fetchWorkflow, fetchSkills, executeWorkflowStream, resumeWorkflowStream, type WorkflowData, type SkillsData, type ExecutionResult } from "@/lib/api"
```

Add new state fields to the `UseWorkflowResult` interface:

```typescript
interface UseWorkflowResult {
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
  skillsData: SkillsData | null
  isLoading: boolean
  error: string | null
  isLive: boolean
  isExecuting: boolean
  activeNodeId: string | null
  executedNodes: string[]
  executionLog: string[]
  progress: number
  isPaused: boolean
  pausedNodeId: string | null
  pauseReason: string | null
  sessionId: string | null
  refetch: () => void
  resetWorkflow: () => void
  startStreamExecution: (instruction: string) => Promise<ExecutionResult | null>
  stopStreamExecution: () => void
  resumeFromNode: (nodeId: string) => Promise<ExecutionResult | null>
}
```

Add new state declarations (after the existing state declarations around line 43):

```typescript
  const [isPaused, setIsPaused] = useState(false)
  const [pausedNodeId, setPausedNodeId] = useState<string | null>(null)
  const [pauseReason, setPauseReason] = useState<string | null>(null)
  const [sessionId, setSessionId] = useState<string | null>(null)
```

Add `onPaused` callback to `startStreamExecution`'s callbacks object (after `onError`):

```typescript
        onPaused: (nodeId, reason, sid) => {
          setIsPaused(true)
          setPausedNodeId(nodeId)
          setPauseReason(reason)
          setSessionId(sid)
          setExecutionLog((prev) => [...prev, `\n⚠ Workflow paused at ${nodeId}: ${reason}`])
        },
```

Add `resumeFromNode` function (after `stopStreamExecution`):

```typescript
  const resumeFromNode = useCallback(async (nodeId: string): Promise<ExecutionResult | null> => {
    if (!sessionId) return null

    setIsPaused(false)
    setPausedNodeId(null)
    setPauseReason(null)
    setIsExecuting(true)
    setActiveNodeId(null)

    const controller = new AbortController()
    setAbortController(controller)

    try {
      const result = await resumeWorkflowStream(sessionId, nodeId, {
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
          setSessionId(null)
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
      }, controller.signal)
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
    }
  }, [sessionId])
```

Update `resetWorkflow` to also clear pause state:

```typescript
  const resetWorkflow = useCallback(() => {
    setActiveNodeId(null)
    setExecutedNodes([])
    setExecutionLog([])
    setIsPaused(false)
    setPausedNodeId(null)
    setPauseReason(null)
    setSessionId(null)
  }, [])
```

Add the new fields to the return object:

```typescript
  return {
    nodes,
    edges,
    skillsData,
    isLoading,
    error,
    isLive,
    isExecuting,
    activeNodeId,
    executedNodes,
    executionLog,
    progress,
    isPaused,
    pausedNodeId,
    pauseReason,
    sessionId,
    refetch: doFetch,
    resetWorkflow,
    startStreamExecution,
    stopStreamExecution,
    resumeFromNode,
  }
```

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/api.ts frontend/hooks/use-workflow.ts
git commit -m "feat(frontend): add pause/resume state to workflow hook and API client"
```

---

### Task 5: Frontend — Pause Guide UI and Clickable Graph

**Files:**
- Create: `frontend/components/pause-guide.tsx`
- Modify: `frontend/components/workflow-graph.tsx`
- Modify: `frontend/components/robot-dashboard.tsx`

- [ ] **Step 1: Create `frontend/components/pause-guide.tsx`**

```tsx
"use client"

import Link from "next/link"

interface PauseGuideProps {
  nodeId: string
  reason: string
}

export function PauseGuide({ nodeId, reason }: PauseGuideProps) {
  return (
    <div className="rounded-md border border-red-400/30 bg-red-400/5 p-4">
      <div className="flex items-center gap-2 mb-2">
        <span className="font-mono text-sm font-bold text-red-400">
          WORKFLOW PAUSED
        </span>
      </div>
      <div className="font-mono text-xs text-muted-foreground mb-3">
        Node &quot;{nodeId}&quot; failed: {reason}
      </div>
      <div className="space-y-2 font-mono text-xs text-foreground/80">
        <div className="flex items-start gap-2">
          <span className="text-muted-foreground shrink-0">1.</span>
          <span>
            Switch to Teleop to adjust the robot{" "}
            <Link
              href="/teleop"
              className="rounded border border-border px-2 py-0.5 text-[10px] text-foreground transition-colors hover:bg-foreground/5"
            >
              Open Teleop
            </Link>
          </span>
        </div>
        <div className="flex items-start gap-2">
          <span className="text-muted-foreground shrink-0">2.</span>
          <span>Click any node in the graph to resume from there</span>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Update `WorkflowGraph` to support clickable nodes when paused**

In `frontend/components/workflow-graph.tsx`, update the component props interface:

```typescript
interface WorkflowGraphProps {
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
  activeNodeId?: string | null
  isPaused?: boolean
  onNodeClick?: (nodeId: string) => void
}
```

Update the function signature:

```typescript
export function WorkflowGraph({ nodes, edges, activeNodeId, isPaused = false, onNodeClick }: WorkflowGraphProps) {
```

In the `renderNodes` callback, update the `<g>` element's `onClick` and `style` to handle resume clicks. Find the `<g>` element (around line 256):

Replace:
```typescript
          onClick={() => setSelectedNode(selectedNode === node.id ? null : node.id)}
          style={{ cursor: "pointer" }}
```

With:
```typescript
          onClick={() => {
            if (isPaused && onNodeClick && node.type !== "start" && node.type !== "end") {
              onNodeClick(node.id)
            } else {
              setSelectedNode(selectedNode === node.id ? null : node.id)
            }
          }}
          style={{ cursor: isPaused && node.type !== "start" && node.type !== "end" ? "pointer" : "default" }}
```

Add a visual cue for clickable nodes when paused. After the main node rect (around line 296, after the `{/* Main node rect */}` block), add:

```typescript
          {/* Resume click highlight when paused */}
          {isPaused && node.type !== "start" && node.type !== "end" && isHovered && (
            <rect
              x={x - 2}
              y={y - 2}
              width={NODE_WIDTH + 4}
              height={NODE_HEIGHT + 4}
              rx={rx + 2}
              fill="none"
              stroke="rgba(56,189,248,0.5)"
              strokeWidth={1.5}
              strokeDasharray="4 2"
            />
          )}
```

Update the `renderNodes` dependency array to include `isPaused` and `onNodeClick`:

```typescript
  }, [nodes, layout.positions, hoveredNode, selectedNode, isPaused, onNodeClick])
```

- [ ] **Step 3: Wire up pause guide and clickable graph in `robot-dashboard.tsx`**

In `frontend/components/robot-dashboard.tsx`, add the import:

```typescript
import { PauseGuide } from "@/components/pause-guide"
```

Add the new destructured fields from `useWorkflow` (update the existing destructuring):

```typescript
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
    resetWorkflow,
    startStreamExecution,
    stopStreamExecution,
    resumeFromNode,
  } = useWorkflow(selectedRobot)
```

Update the `<WorkflowGraph>` component to pass the new props:

```tsx
              <WorkflowGraph
                nodes={nodes}
                edges={edges}
                activeNodeId={activeNodeId}
                isPaused={isPaused}
                onNodeClick={resumeFromNode}
              />
```

Add the pause guide in the left column of the bottom row (before or after the Operation Mode panel). Find the `{/* Operation Mode panel */}` section and add the pause guide right after it:

```tsx
            {/* Operation Mode panel */}
            <div className="rounded-md border border-border bg-card p-4">
              <ModeToggle
                isExecuting={isExecuting}
                onStreamRun={startStreamExecution}
                onStreamStop={stopStreamExecution}
              />
            </div>

            {/* Pause guide — shown when workflow is paused */}
            {isPaused && pausedNodeId && pauseReason && (
              <div className="rounded-md border border-border bg-card p-4">
                <PauseGuide nodeId={pausedNodeId} reason={pauseReason} />
              </div>
            )}
```

- [ ] **Step 4: Commit**

```bash
git add frontend/components/pause-guide.tsx frontend/components/workflow-graph.tsx frontend/components/robot-dashboard.tsx
git commit -m "feat(frontend): add pause guide UI and clickable graph nodes for resume"
```

---

### Task 6: Build Verification

**Files:** None (verification only)

- [ ] **Step 1: Run the frontend build**

Run: `cd frontend && pnpm build`

Expected: Build succeeds.

- [ ] **Step 2: Fix any issues**

If build fails, fix the errors and commit:

```bash
git add -u
git commit -m "fix: address build issues in pause/resume feature"
```
