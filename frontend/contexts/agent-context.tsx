"use client"

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react"

import {
  executeAgentStream,
  fetchAgentInfo,
  type AgentEvent,
  type AgentExecutionResult,
  type AgentInfo,
  type AgentRobotState,
  type AgentToolCall,
} from "@/lib/agent-api"

import type { LogEntry } from "./workflow-context"

export type AgentEntryKind = "tool_call" | "tool_result" | "agent_message"

export interface AgentTimelineEntry {
  id: string
  kind: AgentEntryKind
  toolName?: string
  toolArgs?: Record<string, unknown>
  toolCallId?: string
  resultText?: string
  agentText?: string
  timestamp: number
}

export type AgentRunState = "idle" | "running" | "done" | "error"

interface AgentContextValue {
  info: AgentInfo | null
  infoError: string | null
  isInfoLoading: boolean
  refetchInfo: () => Promise<void>

  runState: AgentRunState
  task: string
  setTask: (text: string) => void
  budget: number
  setBudget: (n: number) => void

  timeline: AgentTimelineEntry[]
  log: LogEntry[]
  result: AgentExecutionResult | null
  errorText: string | null
  robotState: AgentRobotState | null

  startAgent: (task: string) => Promise<void>
  stopAgent: () => void
  clear: () => void
}

const AgentContext = createContext<AgentContextValue | null>(null)

let _entryCounter = 0
const nextEntryId = () => `e${Date.now()}-${++_entryCounter}`

export function AgentProvider({ children }: { children: ReactNode }) {
  const [info, setInfo] = useState<AgentInfo | null>(null)
  const [infoError, setInfoError] = useState<string | null>(null)
  const [isInfoLoading, setIsInfoLoading] = useState(true)

  const [task, setTask] = useState("")
  const [budget, setBudget] = useState(30)
  const [runState, setRunState] = useState<AgentRunState>("idle")
  const [timeline, setTimeline] = useState<AgentTimelineEntry[]>([])
  const [log, setLog] = useState<LogEntry[]>([])
  const [result, setResult] = useState<AgentExecutionResult | null>(null)
  const [errorText, setErrorText] = useState<string | null>(null)
  const [robotState, setRobotState] = useState<AgentRobotState | null>(null)

  const abortRef = useRef<AbortController | null>(null)

  const refetchInfo = useCallback(async () => {
    setIsInfoLoading(true)
    setInfoError(null)
    try {
      const data = await fetchAgentInfo()
      setInfo(data)
      // Default budget tracks the backend default unless user changed it.
      setBudget((cur) => (cur === 30 && data.default_budget ? data.default_budget : cur))
    } catch (e) {
      setInfo(null)
      setInfoError(e instanceof Error ? e.message : "Unknown error")
    } finally {
      setIsInfoLoading(false)
    }
  }, [])

  useEffect(() => {
    refetchInfo()
  }, [refetchInfo])

  const clear = useCallback(() => {
    setTimeline([])
    setLog([])
    setResult(null)
    setErrorText(null)
    setRobotState(null)
    setRunState("idle")
  }, [])

  const stopAgent = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
  }, [])

  const startAgent = useCallback(
    async (taskText: string) => {
      const t = taskText.trim()
      if (!t) return
      if (runState === "running") return
      if (info && !info.available) {
        setErrorText(info.reason ?? "Agent unavailable")
        setRunState("error")
        return
      }

      setRunState("running")
      setTimeline([])
      setLog([])
      setResult(null)
      setErrorText(null)
      setRobotState(null)

      const controller = new AbortController()
      abortRef.current = controller

      const onToolCall = (call: AgentToolCall) => {
        setTimeline((prev) => [
          ...prev,
          {
            id: nextEntryId(),
            kind: "tool_call",
            toolCallId: call.id,
            toolName: call.name,
            toolArgs: call.args,
            timestamp: Date.now(),
          },
        ])
      }

      const onToolResult = (id: string, name: string, resultText: string) => {
        setTimeline((prev) => [
          ...prev,
          {
            id: nextEntryId(),
            kind: "tool_result",
            toolCallId: id,
            toolName: name,
            resultText,
            timestamp: Date.now(),
          },
        ])
      }

      const onAgentMessage = (text: string, _toolCalls: AgentToolCall[]) => {
        // Tool calls are emitted as separate events; here we only render text.
        if (!text) return
        setTimeline((prev) => [
          ...prev,
          {
            id: nextEntryId(),
            kind: "agent_message",
            agentText: text,
            timestamp: Date.now(),
          },
        ])
      }

      const onLog = (text: string, level?: AgentEvent extends { event: "log" } ? never : "info" | "warning" | "error") => {
        setLog((prev) => [...prev, { text, level: (level as LogEntry["level"]) ?? "info" }])
      }

      try {
        const finalResult = await executeAgentStream(
          t,
          {
            onStarted: (_task, _budget) => {
              setLog((prev) => [...prev, { text: `▶ task: ${_task} (budget=${_budget})`, level: "info" }])
            },
            onToolCall,
            onToolResult,
            onAgentMessage,
            onLog,
            onDone: (r) => {
              setResult(r)
              setRobotState(r.robot_state)
              setRunState("done")
              setLog((prev) => [...prev, { text: `✓ done in ${r.elapsed_seconds}s — ${r.tool_calls.length} tool calls`, level: "info" }])
            },
            onError: (err) => {
              setErrorText(err)
              setRunState("error")
              setLog((prev) => [...prev, { text: `✗ ${err}`, level: "error" }])
            },
          },
          controller.signal,
          budget,
        )
        if (finalResult) {
          setResult(finalResult)
          setRobotState(finalResult.robot_state)
        }
      } catch (e) {
        if (e instanceof DOMException && e.name === "AbortError") {
          setLog((prev) => [...prev, { text: "⚠ stopped by user", level: "warning" }])
          setRunState("idle")
        } else {
          const msg = e instanceof Error ? e.message : "Unknown error"
          setErrorText(msg)
          setRunState("error")
          setLog((prev) => [...prev, { text: `✗ ${msg}`, level: "error" }])
        }
      } finally {
        abortRef.current = null
      }
    },
    [budget, info, runState],
  )

  return (
    <AgentContext.Provider
      value={{
        info,
        infoError,
        isInfoLoading,
        refetchInfo,
        runState,
        task,
        setTask,
        budget,
        setBudget,
        timeline,
        log,
        result,
        errorText,
        robotState,
        startAgent,
        stopAgent,
        clear,
      }}
    >
      {children}
    </AgentContext.Provider>
  )
}

export function useAgent(): AgentContextValue {
  const ctx = useContext(AgentContext)
  if (!ctx) throw new Error("useAgent must be used inside AgentProvider")
  return ctx
}
