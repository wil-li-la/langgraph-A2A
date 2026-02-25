"use client"

import { useState, useEffect, useCallback } from "react"
import type { RobotId, WorkflowNode, WorkflowEdge } from "@/lib/mock-data"
import { taskData } from "@/lib/mock-data"
import { fetchWorkflow, type WorkflowData } from "@/lib/api"

interface UseWorkflowResult {
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
  isLoading: boolean
  error: string | null
  isLive: boolean
  refetch: () => void
}

/**
 * Hook that fetches workflow data from the backend API.
 * Falls back to mock data if the backend is unavailable.
 */
export function useWorkflow(robotId: RobotId, executedNodes: string[] = []): UseWorkflowResult {
  const [data, setData] = useState<WorkflowData | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [isLive, setIsLive] = useState(false)

  const doFetch = useCallback(async () => {
    setIsLoading(true)
    setError(null)

    try {
      const workflow = await fetchWorkflow()
      setData(workflow)
      setIsLive(true)
    } catch (err) {
      const message = err instanceof Error ? err.message : "Unknown error"
      setError(message)
      setIsLive(false)
      // Fall back to mock data
      setData(null)
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    doFetch()
  }, [doFetch])

  // Use live data if available, otherwise fall back to mock data for this robot
  const fallback = taskData[robotId]?.workflow ?? { nodes: [], edges: [] }
  const rawNodes = data?.nodes ?? fallback.nodes
  const edges = data?.edges ?? fallback.edges

  // Map raw nodes to apply execution status
  const nodes = rawNodes.map((n) => {
    // Determine status based on execution history
    if (executedNodes.includes(n.id)) {
      if (n.type === "error") {
        return { ...n, status: "error" as WorkflowNode["status"] }
      }
      return { ...n, status: "completed" as WorkflowNode["status"] }
    }
    return n
  })

  return {
    nodes,
    edges,
    isLoading,
    error,
    isLive,
    refetch: doFetch,
  }
}
