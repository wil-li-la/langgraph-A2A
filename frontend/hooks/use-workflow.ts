"use client"

import { useState, useEffect, useCallback } from "react"
import type { RobotId, WorkflowNode, WorkflowEdge } from "@/lib/mock-data"
import { taskData } from "@/lib/mock-data"
import { fetchWorkflow, fetchSkills, executeWorkflowStream, type WorkflowData, type SkillsData, type ExecutionResult } from "@/lib/api"

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
  refetch: () => void
  resetWorkflow: () => void
  startStreamExecution: (instruction: string) => Promise<ExecutionResult | null>
  stopStreamExecution: () => void
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

  // Reset all execution state
  const resetWorkflow = useCallback(() => {
    setActiveNodeId(null)
    setExecutedNodes([])
    setExecutionLog([])
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
    activeNodeId,
    executedNodes,
    executionLog,
    progress,
    refetch: doFetch,
    resetWorkflow,
    startStreamExecution,
    stopStreamExecution,
  }
}
