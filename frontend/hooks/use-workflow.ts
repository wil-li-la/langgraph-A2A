"use client"

import { useState, useEffect, useCallback } from "react"
import type { RobotId, WorkflowNode, WorkflowEdge } from "@/lib/mock-data"
import { taskData } from "@/lib/mock-data"
import { fetchWorkflow, fetchSkills, executeWorkflowStream, resumeWorkflowStream, resetWorkflowStream, type WorkflowData, type SkillsData, type ExecutionResult } from "@/lib/api"

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
  isResetting: boolean
  refetch: () => void
  resetWorkflow: () => Promise<void>
  isPaused: boolean
  pausedNodeId: string | null
  pauseReason: string | null
  sessionId: string | null
  startStreamExecution: (instruction: string) => Promise<ExecutionResult | null>
  stopStreamExecution: () => void
  resumeFromNode: (nodeId: string) => Promise<ExecutionResult | null>
}

/**
 * Hook that fetches workflow data from the backend API.
 * Falls back to mock data if the backend is unavailable.
 * Supports streaming execution with real-time node highlights.
 */
export function useWorkflow(robotId: RobotId): UseWorkflowResult {
  const [data, setData] = useState<WorkflowData | null>(null)
  const [skillsData, setSkillsData] = useState<SkillsData | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [isLive, setIsLive] = useState(false)

  // Streaming execution state
  const [isExecuting, setIsExecuting] = useState(false)
  const [activeNodeId, setActiveNodeId] = useState<string | null>(null)
  const [executedNodes, setExecutedNodes] = useState<string[]>([])
  const [executionLog, setExecutionLog] = useState<string[]>([])
  const [abortController, setAbortController] = useState<AbortController | null>(null)
  const [isPaused, setIsPaused] = useState(false)
  const [pausedNodeId, setPausedNodeId] = useState<string | null>(null)
  const [pauseReason, setPauseReason] = useState<string | null>(null)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [isResetting, setIsResetting] = useState(false)

  const doFetch = useCallback(async () => {
    setIsLoading(true)
    setError(null)

    try {
      const [workflow, skills] = await Promise.all([
        fetchWorkflow(),
        fetchSkills()
      ])
      setData(workflow)
      setSkillsData(skills)
      setIsLive(true)
    } catch (err) {
      const message = err instanceof Error ? err.message : "Unknown error"
      setError(message)
      setIsLive(false)
      setData(null)
      setSkillsData(null)
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    doFetch()
  }, [doFetch])

  // Start a streaming execution
  const startStreamExecution = useCallback(async (instruction: string): Promise<ExecutionResult | null> => {
    setIsExecuting(true)
    setActiveNodeId(null)
    setExecutedNodes([])
    setExecutionLog([])
    
    // Create new abort controller for this run
    const controller = new AbortController()
    setAbortController(controller)

    try {
      const result = await executeWorkflowStream(instruction, {
        onNodeStart: (nodeId, executed) => {
          setActiveNodeId(nodeId)
          setExecutedNodes([...executed])
        },
        onNodeEnd: (nodeId, executed) => {
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
        onPaused: (nodeId, reason, sid) => {
          setIsPaused(true)
          setPausedNodeId(nodeId)
          setPauseReason(reason)
          setSessionId(sid)
          setExecutionLog((prev) => [...prev, `\n⚠ Workflow paused at ${nodeId}: ${reason}`])
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
  }, [])
  
  // Stop a streaming execution
  const stopStreamExecution = useCallback(() => {
    if (abortController) {
      abortController.abort()
      setAbortController(null)
    }
  }, [abortController])

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

  // Reset: run return_to_origin on backend, then clear all state
  const resetWorkflow = useCallback(async () => {
    setIsResetting(true)
    setIsPaused(false)
    setPausedNodeId(null)
    setPauseReason(null)
    setExecutionLog((prev) => [...prev, "\n↺ Resetting — returning to origin..."])

    try {
      await resetWorkflowStream({
        onNodeStart: (nodeId, executed) => {
          setActiveNodeId(nodeId)
          setExecutedNodes([...executed])
        },
        onNodeEnd: (nodeId, executed) => {
          setActiveNodeId(null)
          setExecutedNodes([...executed])
        },
        onLog: (text) => {
          setExecutionLog((prev) => [...prev, text])
        },
        onDone: () => {
          setExecutionLog((prev) => [...prev, "✓ Robot returned to origin"])
        },
        onError: (errMsg) => {
          setExecutionLog((prev) => [...prev, `✗ Reset error: ${errMsg}`])
        },
      })
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Unknown error"
      setExecutionLog((prev) => [...prev, `✗ Reset failed: ${msg}`])
    } finally {
      // Clear all state after reset completes
      setActiveNodeId(null)
      setExecutedNodes([])
      setSessionId(null)
      setIsResetting(false)
    }
  }, [])

  // Render live nodes only — do not fall back to mock nodes anymore!
  const rawNodes = data?.nodes ?? []
  const edges = data?.edges ?? []

  // Map raw nodes to apply execution status
  const nodes = rawNodes.map((n) => {
    if (n.id === activeNodeId) {
      return { ...n, status: "active" as WorkflowNode["status"] }
    }
    if (executedNodes.includes(n.id)) {
      if (n.type === "error") {
        return { ...n, status: "error" as WorkflowNode["status"] }
      }
      return { ...n, status: "completed" as WorkflowNode["status"] }
    }
    return n
  })

  // Calculate progress
  const totalExecutableNodes = rawNodes.filter((n) => n.type !== "start" && n.type !== "end").length
  const progress = totalExecutableNodes > 0
    ? Math.round((executedNodes.filter((id) => id !== "__start__" && id !== "__end__").length / totalExecutableNodes) * 100)
    : 0

  return {
    nodes,
    edges,
    skillsData,
    isLoading,
    error,
    isLive,
    isExecuting,
    isResetting,
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
}
