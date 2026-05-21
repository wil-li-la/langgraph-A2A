// frontend/lib/workflow-locations-api.ts
//
// REST client for /api/workflows + /api/workflows/<wf>/locations.
// Mirrors the structure of nav-api.ts.

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:9999"

export interface Location {
  x: number
  y: number
  theta: number
  ts_ms: number
}

export interface WorkflowManifest {
  id: string
  required_locations: string[]
}

export async function fetchWorkflowManifest(): Promise<WorkflowManifest[]> {
  const r = await fetch(`${API_BASE}/api/workflows`)
  if (!r.ok) throw new Error(`fetchWorkflowManifest: ${r.status}`)
  return r.json()
}

export async function listWorkflowLocations(
  workflowId: string,
): Promise<Record<string, Location>> {
  const r = await fetch(
    `${API_BASE}/api/workflows/${workflowId}/locations`,
  )
  if (!r.ok) throw new Error(`listWorkflowLocations: ${r.status}`)
  return r.json()
}

export async function teachWorkflowLocation(
  workflowId: string, name: string,
): Promise<Location> {
  const r = await fetch(
    `${API_BASE}/api/workflows/${workflowId}/locations/${name}/teach`,
    { method: "POST" },
  )
  if (!r.ok) {
    const detail = await r.json().catch(() => ({}))
    throw new Error(detail.error ?? `teachWorkflowLocation: ${r.status}`)
  }
  return r.json()
}

export async function setWorkflowLocation(
  workflowId: string, name: string,
  p: { x: number; y: number; theta: number },
): Promise<Location> {
  const r = await fetch(
    `${API_BASE}/api/workflows/${workflowId}/locations/${name}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(p),
    },
  )
  if (!r.ok) {
    const detail = await r.json().catch(() => ({}))
    throw new Error(detail.error ?? `setWorkflowLocation: ${r.status}`)
  }
  return r.json()
}

export async function deleteWorkflowLocation(
  workflowId: string, name: string,
): Promise<void> {
  const r = await fetch(
    `${API_BASE}/api/workflows/${workflowId}/locations/${name}`,
    { method: "DELETE" },
  )
  if (!r.ok && r.status !== 404) {
    throw new Error(`deleteWorkflowLocation: ${r.status}`)
  }
}
