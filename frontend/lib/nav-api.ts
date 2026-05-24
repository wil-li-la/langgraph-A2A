/**
 * Backend client for the nvblox nav proxy. See backend/app/api/nav.py.
 *
 * The backend forwards /api/nav/goto to the lab nav_service over ZMQ. When
 * the lab service is down, /goto resolves with status="ROBOT_ERROR" rather
 * than throwing — UI should surface that instead of crashing.
 */

import { API_BASE } from "./api"

export interface NavPose {
  x: number
  y: number
  theta: number          // radians, map frame
  source: "user" | "localizer" | "nav_result"
  ts_ms: number
}

export type NavTaskState = "idle" | "pending" | "running" | "done"

export type NavStatus =
  | "OK"
  | "NO_PATH"
  | "TIMEOUT"
  | "OBSTRUCTED"
  | "CANCELLED"
  | "ROBOT_ERROR"
  | "BAD_TARGET"
  | "UNKNOWN"

export interface NavTask {
  request_id: string
  target: [number, number, number]
  state: NavTaskState
  status: NavStatus | null
  reason: string
  started_ms: number
  finished_ms: number | null
  final_pose: [number, number, number] | null
}

export interface NavMapMetadata {
  image: string
  resolution: number
  origin: [number, number, number]
  width_px: number
  height_px: number
  frame_id: string
}

export type NavLocalizationState =
  | "ok"
  | "uncertain"
  | "dead-reckon"
  | "unseeded"

export interface NavLocalization {
  state: NavLocalizationState
  cov_xy_m: number | null
  cov_yaw_rad: number | null
  scan_age_s: number | null
}

export interface NavSnapshot {
  pose: NavPose | null
  task: NavTask
  /**
   * True while a browser is holding an open `/ws/teleop` connection. While
   * true, the backend rejects /api/nav/goto with 409, and dashboard pages
   * should disable nav controls. Conversely, dashboard's teleop page
   * should disable drive controls when `task.state` is "pending"/"running".
   */
  teleop_active: boolean
  /**
   * AMCL localization health from nav_service. null when nav_service is
   * unreachable; the indicator should go grey in that case.
   */
  localization: NavLocalization | null
}

export async function fetchNavMap(): Promise<NavMapMetadata> {
  const r = await fetch(`${API_BASE}/api/nav/map`)
  if (!r.ok) throw new Error(`map metadata failed: ${r.status}`)
  return r.json()
}

export async function fetchNavPose(): Promise<NavPose | null> {
  const r = await fetch(`${API_BASE}/api/nav/pose`)
  if (!r.ok) throw new Error(`pose fetch failed: ${r.status}`)
  return r.json()
}

export async function setNavPose(p: { x: number; y: number; theta: number }): Promise<NavPose> {
  const r = await fetch(`${API_BASE}/api/nav/pose`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(p),
  })
  if (!r.ok) throw new Error(`pose set failed: ${r.status}`)
  return r.json()
}

export async function postNavGoto(
  goal: { x: number; y: number; theta: number; timeout_s?: number }
): Promise<{ request_id: string; state: NavTaskState } | { error: string; request_id?: string }> {
  const r = await fetch(`${API_BASE}/api/nav/goto`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(goal),
  })
  return r.json()
}

/**
 * Subscribe to the SSE stream of (pose, task) snapshots. Returns an
 * unsubscribe function. Auto-reconnects on transient disconnect.
 */
export function subscribeNavStatus(
  onSnapshot: (snap: NavSnapshot) => void,
  onError?: (e: Event) => void,
): () => void {
  const url = `${API_BASE}/api/nav/status/stream`
  const es = new EventSource(url)
  es.onmessage = (e) => {
    try {
      onSnapshot(JSON.parse(e.data) as NavSnapshot)
    } catch {
      // ignore malformed frame
    }
  }
  if (onError) es.onerror = onError
  return () => es.close()
}
