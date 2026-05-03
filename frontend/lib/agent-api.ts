/**
 * Backend API client for the agentic execution path (/api/agent/*).
 *
 * Lives alongside lib/api.ts which still serves the legacy scripted
 * workflow at /api/workflow/*. The two paths share nothing at runtime.
 */

import { API_BASE } from "./api"

export interface AgentInfo {
  available: boolean
  reason: string | null
  build_error: string | null
  llm_provider: string
  llm_model: string
  world_summary: string
  tools: { name: string; description: string }[]
  default_budget: number
}

export interface AgentToolCall {
  id: string
  name: string
  args: Record<string, unknown>
}

export interface AgentRobotState {
  location: string
  holding: string | null
  calls_made: number
  budget: number
  elapsed_seconds: number
}

export interface AgentExecutionResult {
  task: string
  summary: string
  tool_calls: AgentToolCall[]
  robot_state: AgentRobotState
  elapsed_seconds: number
  message_count: number
}

export type AgentEvent =
  | { event: "started"; task: string; budget: number }
  | { event: "agent_message"; text: string; tool_calls: AgentToolCall[] }
  | { event: "tool_call"; id: string; name: string; args: Record<string, unknown> }
  | { event: "tool_result"; id: string; name: string; result: string }
  | { event: "log"; text: string; level?: "info" | "warning" | "error" }
  | { event: "done"; result?: AgentExecutionResult } & Partial<AgentExecutionResult>
  | { event: "error"; error: string }

export interface AgentStreamCallbacks {
  onStarted?: (task: string, budget: number) => void
  onAgentMessage?: (text: string, toolCalls: AgentToolCall[]) => void
  onToolCall?: (call: AgentToolCall) => void
  onToolResult?: (id: string, name: string, result: string) => void
  onLog?: (text: string, level?: "info" | "warning" | "error") => void
  onDone?: (result: AgentExecutionResult) => void
  onError?: (error: string) => void
}

export async function fetchAgentInfo(): Promise<AgentInfo> {
  const res = await fetch(`${API_BASE}/api/agent/info`)
  if (!res.ok) {
    throw new Error(`Failed to fetch agent info: ${res.status} ${res.statusText}`)
  }
  return res.json()
}

export async function executeAgent(
  task: string,
  budget = 30,
): Promise<AgentExecutionResult> {
  const res = await fetch(`${API_BASE}/api/agent/execute`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ task, budget }),
  })
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`Agent execute failed: ${res.status} ${body}`)
  }
  return res.json()
}

export async function executeAgentStream(
  task: string,
  callbacks: AgentStreamCallbacks,
  signal?: AbortSignal,
  budget = 30,
): Promise<AgentExecutionResult | null> {
  const res = await fetch(`${API_BASE}/api/agent/execute/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ task, budget }),
    signal,
  })

  if (!res.ok) {
    const body = await res.text()
    throw new Error(`Agent stream failed: ${res.status} ${body}`)
  }

  const reader = res.body?.getReader()
  if (!reader) throw new Error("No readable stream")

  const decoder = new TextDecoder()
  let buffer = ""
  let finalResult: AgentExecutionResult | null = null

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
        const ev = JSON.parse(trimmed.slice(6)) as AgentEvent
        switch (ev.event) {
          case "started":
            callbacks.onStarted?.(ev.task, ev.budget)
            break
          case "agent_message":
            callbacks.onAgentMessage?.(ev.text, ev.tool_calls ?? [])
            break
          case "tool_call":
            callbacks.onToolCall?.({ id: ev.id, name: ev.name, args: ev.args })
            break
          case "tool_result":
            callbacks.onToolResult?.(ev.id, ev.name, ev.result)
            break
          case "log":
            callbacks.onLog?.(ev.text, ev.level)
            break
          case "done": {
            // The "done" event payload shape: top-level fields ARE the result fields
            // (the backend spreads payload into the SSE envelope), not nested under .result.
            const raw = ev as unknown as Record<string, unknown>
            const result: AgentExecutionResult = {
              task: (raw.task as string) ?? "",
              summary: (raw.summary as string) ?? "",
              tool_calls: (raw.tool_calls as AgentToolCall[]) ?? [],
              robot_state: (raw.robot_state as AgentRobotState) ?? {
                location: "unknown",
                holding: null,
                calls_made: 0,
                budget: 0,
                elapsed_seconds: 0,
              },
              elapsed_seconds: (raw.elapsed_seconds as number) ?? 0,
              message_count: (raw.message_count as number) ?? 0,
            }
            finalResult = result
            callbacks.onDone?.(result)
            break
          }
          case "error":
            callbacks.onError?.(ev.error)
            break
        }
      } catch {
        // ignore malformed JSON
      }
    }
  }

  return finalResult
}
