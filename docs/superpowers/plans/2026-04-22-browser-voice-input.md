# Browser Voice Input for Identity Check

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace robot-side `listen_skill()` with browser-side voice/text input during `check_identity` in manual (dashboard) mode, so the patient can speak into the iPad/iPhone mic or type their name.

**Architecture:** When the workflow reaches `check_identity_node` in manual mode, instead of calling `listen_skill()` (ZMQ to robot ASR), the backend emits an `await_input` SSE event and blocks the workflow thread on a `threading.Event`. The frontend shows a dual-mode input (Web Speech API mic + text field). On submit, the frontend POSTs the transcript to `POST /api/workflow/input`, which unblocks the thread. The workflow continues with the submitted text as if `listen_skill()` had returned it. Auto mode (A2A) still uses `listen_skill()` unchanged.

**Tech Stack:** Python threading primitives (Event, dict), Starlette REST, Web Speech API (`webkitSpeechRecognition`), Next.js React components, SSE streaming.

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/app/healthcare/medication_delivery.py` | Modify | Add `_await_browser_input()` helper; use it in `check_identity_node` when `mode=="manual"` |
| `backend/app/workflow_api.py` | Modify | Add `POST /api/workflow/input` endpoint; forward `await_input` events in SSE generators |
| `frontend/lib/api.ts` | Modify | Add `await_input` SSE event type; add `submitWorkflowInput()` API call; add `onAwaitInput` callback |
| `frontend/hooks/use-workflow.ts` | Modify | Handle `onAwaitInput` callback; expose `awaitingInput` / `inputPrompt` state + `submitInput()` action |
| `frontend/components/voice-input.tsx` | Create | Dual-mode component: Web Speech API mic button + text input field |
| `frontend/components/robot-dashboard.tsx` | Modify | Render `<VoiceInput>` when `awaitingInput` is true |

---

### Task 1: Backend — blocking input bridge

**Files:**
- Modify: `backend/app/healthcare/medication_delivery.py:34,249-283`

- [ ] **Step 1: Add the pending-input store alongside `_paused_sessions`**

At the top of the file (after `_paused_sessions` on line 34), add:

```python
import threading

# Pending browser-input requests: session_id -> {"event": Event, "result": str}
_pending_inputs: dict[str, dict] = {}
```

Note: `threading` is already available in `workflow_api.py` but not imported here yet.

- [ ] **Step 2: Add `_await_browser_input()` helper**

After the `_speak_and_wait` helper (line 128), add:

```python
def _await_browser_input(session_id: str, prompt: str, *, timeout_s: float = 120.0) -> str:
    """Block until the frontend submits text via POST /api/workflow/input.

    Puts an entry in _pending_inputs with a threading.Event, then yields an
    'await_input' event to the SSE stream by logging a special marker that
    the SSE generator intercepts.
    """
    evt = threading.Event()
    _pending_inputs[session_id] = {"event": evt, "result": "", "prompt": prompt}
    logger.info(f"__await_input__::{session_id}::{prompt}")
    if not evt.wait(timeout=timeout_s):
        _pending_inputs.pop(session_id, None)
        return ""
    result = _pending_inputs.pop(session_id, {}).get("result", "")
    return result
```

- [ ] **Step 3: Modify `check_identity_node` to branch on mode**

Replace lines 257-265 of `check_identity_node` (the speak + listen block):

```python
    # Voice confirmation — identity
    q1 = f"您好，我是給藥機器人，請問你叫什麼名字"
    _log("🔊", f"語音播放: 「{q1}」")
    _speak_and_wait(q1)

    resp1 = listen_skill() or ""
    _log("🗣️", f"病患回覆: 「{resp1}」")
```

With:

```python
    # Voice confirmation — identity
    q1 = "您好，我是給藥機器人，請問你叫什麼名字"
    _log("🔊", f"語音播放: 「{q1}」")
    _speak_and_wait(q1)

    mode = state.get("mode", "auto")
    session_id = state.get("session_id", "")
    if mode == "manual" and session_id:
        resp1 = _await_browser_input(session_id, q1)
    else:
        resp1 = listen_skill() or ""
    _log("🗣️", f"病患回覆: 「{resp1}」")
```

- [ ] **Step 4: Add `session_id` to `AgentState`**

In the `AgentState` TypedDict (line 37), add `session_id`:

```python
class AgentState(TypedDict):
    patient_name: str
    medication_name: str
    current_location: str
    task_status: str
    target_detected: bool
    identity_verified: bool
    identity_check_retries: int
    mode: str
    session_id: str  # SSE session ID for browser input bridge
    errors: Annotated[List[str], operator.add]
    history: Annotated[List[str], operator.add]
    executed_nodes: Annotated[List[str], operator.add]
    resume_from: str
```

Also update `_build_initial_state` (line 496) to include `"session_id": ""`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/healthcare/medication_delivery.py
git commit -m "feat(backend): add browser input bridge for check_identity in manual mode"
```

---

### Task 2: Backend — wire session_id through stream_execute and add input endpoint

**Files:**
- Modify: `backend/app/workflow_api.py:171-327,644-657`
- Modify: `backend/app/healthcare/medication_delivery.py:571-654`

- [ ] **Step 1: Pass session_id into initial state in `stream_execute`**

In `medication_delivery.py`, update `stream_execute` (line 571). After building `initial_state` on line 593, inject the session_id:

```python
        if resume_state is not None:
            initial_state = resume_state
        else:
            initial_state = self._build_initial_state(patient_name, medication_name, mode=mode)
        initial_state["session_id"] = session_id
```

- [ ] **Step 2: Intercept `__await_input__` log markers in `workflow_api.py`**

In `execute_workflow_stream` (line 202), inside `_run_stream`'s `QueueLogHandler.emit()`, intercept the marker before queuing it as a normal log:

Replace the emit method body in `execute_workflow_stream`'s `_run_stream` (lines 213-219):

```python
                    def emit(self, record):
                        if threading.get_ident() == thread_id:
                            text = f"{record.getMessage()}"
                            if text.strip():
                                # Intercept await_input markers
                                if text.startswith("__await_input__::"):
                                    parts = text.split("::", 2)
                                    sid = parts[1] if len(parts) > 1 else ""
                                    prompt = parts[2] if len(parts) > 2 else ""
                                    try:
                                        if not loop.is_closed():
                                            loop.call_soon_threadsafe(q.put_nowait, {
                                                "type": "await_input",
                                                "session_id": sid,
                                                "prompt": prompt,
                                            })
                                    except RuntimeError:
                                        pass
                                    return
                                try:
                                    if not loop.is_closed():
                                        loop.call_soon_threadsafe(q.put_nowait, {"type": "stdout", "text": text.rstrip("\n")})
                                except RuntimeError:
                                    pass
```

Do the same replacement in `resume_workflow_stream`'s `_run_stream` handler (lines 368-377).

- [ ] **Step 3: Emit `await_input` SSE events in the event generators**

In `execute_workflow_stream`'s `event_generator` while-loop (after the `elif item["type"] == "error"` block around line 308), add:

```python
                elif item["type"] == "await_input":
                    payload = json.dumps({
                        "event": "await_input",
                        "session_id": item["session_id"],
                        "prompt": item["prompt"],
                    }, ensure_ascii=False)
                    yield f"data: {payload}\n\n"
```

Do the same in `resume_workflow_stream`'s event generator (after its error block around line 468).

- [ ] **Step 4: Add `POST /api/workflow/input` endpoint**

After `resume_workflow_stream` (after line 487), add:

```python
async def submit_workflow_input(request: Request) -> JSONResponse:
    """POST /api/workflow/input — Submit browser-captured text to a waiting workflow node.

    Body: { "session_id": "...", "text": "..." }
    """
    from app.healthcare.medication_delivery import _pending_inputs

    try:
        body = await request.json()
        session_id = body.get("session_id", "")
        text = body.get("text", "")

        if not session_id:
            return JSONResponse({"error": "Missing 'session_id'"}, status_code=400)

        pending = _pending_inputs.get(session_id)
        if not pending:
            return JSONResponse(
                {"error": f"No pending input request for session_id={session_id}"},
                status_code=404,
            )

        pending["result"] = text
        pending["event"].set()
        return JSONResponse({"status": "ok"})

    except Exception as e:
        logger.error(f"Submit input failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)
```

- [ ] **Step 5: Register the route**

In the `workflow_routes` list (line 644), add:

```python
    Route("/api/workflow/input", submit_workflow_input, methods=["POST", "OPTIONS"]),
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/workflow_api.py backend/app/healthcare/medication_delivery.py
git commit -m "feat(backend): add /api/workflow/input endpoint and await_input SSE event"
```

---

### Task 3: Frontend — API client and hook changes

**Files:**
- Modify: `frontend/lib/api.ts:36-56`
- Modify: `frontend/hooks/use-workflow.ts:7-30,84-135,145-199`

- [ ] **Step 1: Add `await_input` to the SSE event types in `api.ts`**

Update the `StreamEvent` interface (line 37):

```typescript
export interface StreamEvent {
  event: "node_start" | "node_end" | "done" | "error" | "log" | "paused" | "await_input"
  node_id?: string
  executed_nodes?: string[]
  history?: string[]
  task_status?: string
  result?: ExecutionResult
  text?: string
  session_id?: string
  reason?: string
  prompt?: string
}
```

Add `onAwaitInput` to `StreamCallbacks` (line 49):

```typescript
export interface StreamCallbacks {
  onNodeStart?: (nodeId: string, executedNodes: string[]) => void
  onNodeEnd?: (nodeId: string, executedNodes: string[], history: string[]) => void
  onDone?: (result: ExecutionResult) => void
  onError?: (error: string) => void
  onLog?: (text: string) => void
  onPaused?: (nodeId: string, reason: string, sessionId: string) => void
  onAwaitInput?: (sessionId: string, prompt: string) => void
}
```

- [ ] **Step 2: Handle `await_input` in both SSE consumer functions**

In `executeWorkflowStream`, inside the switch statement (after the `case "paused"` block, around line 183), add:

```typescript
          case "await_input":
            callbacks.onAwaitInput?.(event.session_id ?? "", event.prompt ?? "")
            break
```

Do the same in `resumeWorkflowStream` (after its `case "paused"` block, around line 272).

- [ ] **Step 3: Add `submitWorkflowInput()` API function**

After `resetWorkflowStream` (end of file), add:

```typescript
/**
 * Submit browser-captured text input to a waiting workflow node.
 */
export async function submitWorkflowInput(sessionId: string, text: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/workflow/input`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, text }),
  })
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`Submit input failed: ${res.status} ${body}`)
  }
}
```

- [ ] **Step 4: Add `await_input` state and `submitInput` action to `use-workflow.ts`**

Add imports at the top (line 6):

```typescript
import { fetchWorkflow, fetchSkills, executeWorkflowStream, resumeWorkflowStream, resetWorkflowStream, submitWorkflowInput, type WorkflowData, type SkillsData, type ExecutionResult } from "@/lib/api"
```

Add to the `UseWorkflowResult` interface (after `sessionId`, line 26):

```typescript
  awaitingInput: boolean
  inputPrompt: string
  submitInput: (text: string) => Promise<void>
```

Add state variables (after `isResetting` state, line 54):

```typescript
  const [awaitingInput, setAwaitingInput] = useState(false)
  const [inputPrompt, setInputPrompt] = useState("")
```

Add the `onAwaitInput` callback to both `startStreamExecution` and `resumeFromNode` callbacks objects (next to `onPaused`):

```typescript
        onAwaitInput: (sid, prompt) => {
          setAwaitingInput(true)
          setInputPrompt(prompt)
          setSessionId(sid)
          setExecutionLog((prev) => [...prev, `\n🎤 Waiting for voice/text input...`])
        },
```

Add the `submitInput` action (after `resumeFromNode`):

```typescript
  const submitInput = useCallback(async (text: string) => {
    if (!sessionId) return
    setAwaitingInput(false)
    setInputPrompt("")
    setExecutionLog((prev) => [...prev, `🗣️ Input submitted: 「${text}」`])
    await submitWorkflowInput(sessionId, text)
  }, [sessionId])
```

Add to the return object:

```typescript
    awaitingInput,
    inputPrompt,
    submitInput,
```

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/api.ts frontend/hooks/use-workflow.ts
git commit -m "feat(frontend): handle await_input SSE events and submitInput action"
```

---

### Task 4: Frontend — VoiceInput component

**Files:**
- Create: `frontend/components/voice-input.tsx`

- [ ] **Step 1: Create the dual-mode voice/text input component**

```tsx
"use client"

import { useState, useRef, useEffect, useCallback } from "react"

interface VoiceInputProps {
  prompt: string
  onSubmit: (text: string) => void
}

export function VoiceInput({ prompt, onSubmit }: VoiceInputProps) {
  const [text, setText] = useState("")
  const [isListening, setIsListening] = useState(false)
  const [speechSupported, setSpeechSupported] = useState(false)
  const recognitionRef = useRef<SpeechRecognition | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition
    setSpeechSupported(!!SR)
  }, [])

  const startListening = useCallback(() => {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition
    if (!SR) return

    const recognition = new SR()
    recognition.lang = "zh-TW"
    recognition.continuous = false
    recognition.interimResults = false

    recognition.onresult = (event: SpeechRecognitionEvent) => {
      const transcript = event.results[0]?.[0]?.transcript ?? ""
      setText(transcript)
      setIsListening(false)
    }

    recognition.onerror = () => {
      setIsListening(false)
    }

    recognition.onend = () => {
      setIsListening(false)
    }

    recognitionRef.current = recognition
    recognition.start()
    setIsListening(true)
  }, [])

  const stopListening = useCallback(() => {
    recognitionRef.current?.stop()
    setIsListening(false)
  }, [])

  const handleSubmit = () => {
    const trimmed = text.trim()
    if (!trimmed) return
    onSubmit(trimmed)
    setText("")
  }

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <span className="relative flex h-2 w-2">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-sky-400/50" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-sky-400" />
        </span>
        <h2 className="font-mono text-lg font-medium tracking-wide text-foreground">
          VOICE INPUT
        </h2>
      </div>

      <div className="font-mono text-sm text-muted-foreground">
        {prompt}
      </div>

      <div className="flex gap-2">
        {speechSupported && (
          <button
            onClick={isListening ? stopListening : startListening}
            className={`shrink-0 rounded-md border px-3 py-2 font-mono text-sm font-medium transition-colors ${
              isListening
                ? "border-red-500/50 bg-red-500/10 text-red-500 hover:bg-red-500/20"
                : "border-sky-500/50 bg-sky-500/10 text-sky-400 hover:bg-sky-500/20"
            }`}
          >
            {isListening ? "■ STOP" : "● REC"}
          </button>
        )}

        <input
          ref={inputRef}
          type="text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleSubmit()}
          placeholder="或在此輸入姓名 …"
          className="flex-1 rounded-md border border-border bg-background px-3 py-2 font-mono text-sm text-foreground placeholder:text-muted-foreground/40 focus:outline-none focus:ring-1 focus:ring-sky-500/30"
        />

        <button
          onClick={handleSubmit}
          disabled={!text.trim()}
          className="rounded-md border border-border bg-foreground px-4 py-2 font-mono text-sm font-medium text-background transition-opacity hover:opacity-90 disabled:opacity-30"
        >
          SEND
        </button>
      </div>

      {isListening && (
        <div className="flex items-center gap-2 rounded-md border border-dashed border-sky-500/30 bg-sky-500/5 px-3 py-2">
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-red-400/50" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-red-400" />
          </span>
          <span className="font-mono text-sm text-muted-foreground">Listening …</span>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Add Web Speech API type declarations**

Create or check if `frontend/types/speech.d.ts` is needed. Safari exposes `webkitSpeechRecognition` which TypeScript doesn't know about. Add to `frontend/types/speech.d.ts`:

```typescript
interface Window {
  SpeechRecognition: typeof SpeechRecognition
  webkitSpeechRecognition: typeof SpeechRecognition
}
```

Note: If the project already has `@types/dom-speech-recognition` or the types exist in `lib.dom.d.ts`, this file is unnecessary — check first and skip if so.

- [ ] **Step 3: Commit**

```bash
git add frontend/components/voice-input.tsx frontend/types/speech.d.ts
git commit -m "feat(frontend): add VoiceInput dual-mode component (mic + text)"
```

---

### Task 5: Frontend — wire VoiceInput into dashboard

**Files:**
- Modify: `frontend/components/robot-dashboard.tsx:3,12-30,130-135`

- [ ] **Step 1: Import VoiceInput and destructure new hook fields**

Add import (line 3 area):

```typescript
import { VoiceInput } from "@/components/voice-input"
```

Add `awaitingInput`, `inputPrompt`, `submitInput` to the destructured hook return (line 12):

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
    awaitingInput,
    inputPrompt,
    submitInput,
    resumeFromNode,
    resetWorkflow,
    startStreamExecution,
    stopStreamExecution,
  } = useWorkflow("stretch3")
```

- [ ] **Step 2: Render VoiceInput in the right column when awaiting input**

After the pause guide block (line 131-135), add:

```tsx
          {/* Voice/text input prompt */}
          {awaitingInput && (
            <div className="rounded-md border border-sky-500/30 bg-card p-3 shrink-0">
              <VoiceInput prompt={inputPrompt} onSubmit={submitInput} />
            </div>
          )}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/components/robot-dashboard.tsx
git commit -m "feat(frontend): show VoiceInput panel when workflow awaits browser input"
```

---

### Task 6: Verify end-to-end with dry-run

- [ ] **Step 1: Start backend in dry-run**

```bash
cd backend
source .venv/bin/activate
python -m app --host localhost --port 9999
```

- [ ] **Step 2: Start frontend**

```bash
cd frontend
pnpm dev
```

- [ ] **Step 3: Test the flow**

1. Open `http://localhost:3000` in Safari (or a device with microphone)
2. Switch to MANUAL mode
3. Enter instruction: `請將阿斯匹靈送給張小明`
4. Watch the workflow stream through nodes
5. When it reaches `check_identity`, verify the **VOICE INPUT** panel appears
6. Test typing `張小明` in the text field and clicking SEND — workflow should continue and pass identity check
7. If on a device with mic, test the REC button — verify it captures speech and populates the text field

- [ ] **Step 4: Test timeout behavior**

1. Run the workflow again to `check_identity`
2. Wait without submitting — after 120s the `_await_browser_input` should return empty string, causing identity check to fail and the workflow to pause on error (normal pause behavior)

- [ ] **Step 5: Commit any fixes**

```bash
git add -A
git commit -m "fix: address issues found during e2e voice input testing"
```
