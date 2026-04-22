/**
 * Backend API client for fetching LangGraph workflow data.
 */

import type { WorkflowNode, WorkflowEdge } from "./mock-data"

export const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:9999"

export interface WorkflowData {
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
}

export interface WorkflowData {
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
}

export interface SkillsData {
  available: string[]
  required: string[]
}

export interface ExecutionResult {
  task_status: string
  patient_name: string
  medication_name: string
  current_location: string
  target_detected: boolean
  identity_verified: boolean
  errors: string[]
  history: string[]
  executed_nodes: string[]
}

/** SSE event types emitted by the streaming endpoint. */
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

export interface StreamCallbacks {
  onNodeStart?: (nodeId: string, executedNodes: string[]) => void
  onNodeEnd?: (nodeId: string, executedNodes: string[], history: string[]) => void
  onDone?: (result: ExecutionResult) => void
  onError?: (error: string) => void
  onLog?: (text: string) => void
  onPaused?: (nodeId: string, reason: string, sessionId: string) => void
  onAwaitInput?: (sessionId: string, prompt: string) => void
  onSession?: (sessionId: string) => void
}

/**
 * Fetch the LangGraph workflow graph structure (nodes + edges).
 */
export async function fetchWorkflow(): Promise<WorkflowData> {
  const res = await fetch(`${API_BASE}/api/workflow`)
  if (!res.ok) {
    throw new Error(`Failed to fetch workflow: ${res.status} ${res.statusText}`)
  }
  const data = await res.json()

  // Map backend response to frontend WorkflowNode format
  const nodes: WorkflowNode[] = (data.nodes ?? []).map((n: Record<string, unknown>) => ({
    id: n.id as string,
    name: n.name as string,
    label: n.label as string,
    type: mapNodeType(n.type as string),
    status: (n.status as WorkflowNode["status"]) ?? "pending",
  }))

  const edges: WorkflowEdge[] = (data.edges ?? []).map((e: Record<string, unknown>) => ({
    from: e.from as string,
    to: e.to as string,
  }))

  return { nodes, edges }
}

/**
 * Fetch available and required skills from the backend.
 */
export async function fetchSkills(): Promise<SkillsData> {
  const res = await fetch(`${API_BASE}/api/skills`)
  if (!res.ok) {
    throw new Error(`Failed to fetch skills: ${res.status} ${res.statusText}`)
  }
  return res.json()
}

/**
 * Execute a medication delivery workflow via the backend (one-shot).
 */
export async function executeWorkflow(instruction: string): Promise<ExecutionResult> {
  const res = await fetch(`${API_BASE}/api/workflow/execute`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ instruction }),
  })
  if (!res.ok) {
    throw new Error(`Execution failed: ${res.status} ${res.statusText}`)
  }
  return res.json()
}

/**
 * Execute a medication delivery workflow with real-time SSE streaming.
 * Calls callbacks as each node starts/ends, returns final ExecutionResult.
 */
export async function executeWorkflowStream(
  instruction: string,
  callbacks: StreamCallbacks,
  signal?: AbortSignal,
  options?: { start_from?: string },
): Promise<ExecutionResult> {
  const res = await fetch(`${API_BASE}/api/workflow/execute/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ instruction, ...(options?.start_from ? { start_from: options.start_from } : {}) }),
    signal,
  })

  if (!res.ok) {
    const body = await res.text()
    throw new Error(`Stream failed: ${res.status} ${body}`)
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

    // Parse SSE lines: "data: {...}\n\n"
    const lines = buffer.split("\n\n")
    buffer = lines.pop() ?? "" // keep the incomplete chunk

    for (const line of lines) {
      const trimmed = line.trim()
      if (!trimmed.startsWith("data: ")) continue

      try {
        const event: StreamEvent = JSON.parse(trimmed.slice(6))

        if (event.session_id) {
          callbacks.onSession?.(event.session_id)
        }

        switch (event.event) {
          case "log":
            if (event.text) {
              callbacks.onLog?.(event.text)
            }
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
          case "await_input":
            callbacks.onAwaitInput?.(event.session_id ?? "", event.prompt ?? "")
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

  if (!finalResult) {
    return null as unknown as ExecutionResult
  }

  return finalResult
}

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

        if (event.session_id) {
          callbacks.onSession?.(event.session_id)
        }

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
          case "await_input":
            callbacks.onAwaitInput?.(event.session_id ?? "", event.prompt ?? "")
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

/**
 * Deliver browser-captured text to a workflow waiting on browser_input_skill.
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

/**
 * Reset workflow: return robot to origin and clear all state.
 */
export async function resetWorkflowStream(
  callbacks: StreamCallbacks,
  signal?: AbortSignal,
): Promise<ExecutionResult | null> {
  const res = await fetch(`${API_BASE}/api/workflow/reset`, {
    method: "POST",
    signal,
  })

  if (!res.ok) {
    const body = await res.text()
    throw new Error(`Reset failed: ${res.status} ${body}`)
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

function mapNodeType(type: string): WorkflowNode["type"] {
  switch (type) {
    case "start":
      return "start"
    case "end":
      return "end"
    case "error":
      return "error"
    case "decision":
      return "decision"
    default:
      return "process"
  }
}
