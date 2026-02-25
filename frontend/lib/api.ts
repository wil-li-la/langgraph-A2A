/**
 * Backend API client for fetching LangGraph workflow data.
 */

import type { WorkflowNode, WorkflowEdge } from "./mock-data"

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:9999"

export interface WorkflowData {
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
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
 * Execute a medication delivery workflow via the backend.
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
