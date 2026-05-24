"use client"

import { useCallback, useEffect, useState } from "react"
import { useNavStatus } from "@/contexts/nav-status"
import {
  deleteWorkflowLocation,
  fetchWorkflowManifest,
  listWorkflowLocations,
  setWorkflowLocation,
  teachWorkflowLocation,
  type Location,
  type WorkflowManifest,
} from "@/lib/workflow-locations-api"
import { WorkflowLocationsMap } from "@/components/workflow-locations-map"

interface Props {
  workflowId: string
}

/**
 * Per-workflow teach-and-save UI for named (x, y, theta) poses. The
 * "current pose" used by Save is whatever the backend has cached as
 * _pose (set by drag-to-set-pose on /nav, teleop, or a future
 * localizer). The panel does not own the map.
 */
export function LocationsPanel({ workflowId }: Props) {
  const { pose } = useNavStatus()
  const [manifest, setManifest] = useState<WorkflowManifest | null>(null)
  const [stored, setStored] = useState<Record<string, Location>>({})
  const [selectedName, setSelectedName] = useState<string>("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Initial fetch
  useEffect(() => {
    let cancelled = false
    Promise.all([
      fetchWorkflowManifest(),
      listWorkflowLocations(workflowId),
    ]).then(([allWorkflows, locs]) => {
      if (cancelled) return
      const m = allWorkflows.find((w) => w.id === workflowId) ?? null
      setManifest(m)
      setStored(locs)
      if (m && m.required_locations.length > 0) {
        // Default to the first required name that has not been taught yet,
        // so the operator's first drag/save targets it. Falls back to the
        // first required name once everything is taught (re-author flow).
        const nextMissing = m.required_locations.find((n) => !(n in locs))
        setSelectedName(nextMissing ?? m.required_locations[0])
      }
    }).catch((e) => !cancelled && setError(String(e)))
    return () => { cancelled = true }
  }, [workflowId])

  const refresh = useCallback(async () => {
    try {
      const locs = await listWorkflowLocations(workflowId)
      setStored(locs)
    } catch (e) {
      setError(String(e))
    }
  }, [workflowId])

  const handleSave = async () => {
    if (!selectedName) return
    setBusy(true); setError(null)
    try {
      await teachWorkflowLocation(workflowId, selectedName)
      await refresh()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  const handleDelete = async (name: string) => {
    setBusy(true); setError(null)
    try {
      await deleteWorkflowLocation(workflowId, name)
      await refresh()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  const handleAuthored = useCallback(
    async (name: string, pose: { x: number; y: number; theta: number }) => {
      setBusy(true); setError(null)
      try {
        await setWorkflowLocation(workflowId, name, pose)
        await refresh()
      } catch (e) {
        setError(String(e))
      } finally {
        setBusy(false)
      }
    },
    [workflowId, refresh],
  )

  if (!manifest) {
    return (
      <div className="rounded-md border border-border bg-card p-3 font-mono text-xs text-muted-foreground">
        Locations: loading…
        {error && <div className="mt-1 text-red-500">{error}</div>}
      </div>
    )
  }

  const required = manifest.required_locations
  const missing = required.filter((n) => !(n in stored))
  const allTaught = missing.length === 0

  return (
    <div className="rounded-md border border-border bg-card p-3 font-mono text-xs space-y-2">
      <div className="flex items-center justify-between">
        <span className="font-medium text-foreground">Locations</span>
        <span className={allTaught ? "text-green-500" : "text-amber-500"}>
          {allTaught ? "all taught" : `${missing.length} missing`}
        </span>
      </div>

      <div className="flex flex-wrap gap-x-3 gap-y-1">
        {required.map((name) => {
          const taught = name in stored
          return (
            <span key={name} className={taught ? "text-green-500" : "text-red-500"}>
              {taught ? "✓" : "✗"} {name}
            </span>
          )
        })}
      </div>

      <div className="flex items-center gap-2 border-t border-border pt-2">
        <span className="text-muted-foreground">Save current pose as:</span>
        <select
          value={selectedName}
          onChange={(e) => setSelectedName(e.target.value)}
          className="rounded border border-border bg-background px-2 py-0.5 font-mono"
        >
          {required.map((n) => (
            <option key={n} value={n}>{n}</option>
          ))}
        </select>
        <button
          onClick={handleSave}
          disabled={busy || !pose || !selectedName}
          className="rounded border border-foreground/30 bg-foreground/10 px-2 py-0.5 text-foreground hover:bg-foreground/20 disabled:cursor-not-allowed disabled:opacity-40"
          title={!pose ? "Drive the robot to a pose first (drag on /nav or teleop)" : "Save"}
        >
          {busy ? "saving…" : "Save"}
        </button>
      </div>

      <div className="border-t border-border pt-2">
        <div className="mb-1 text-muted-foreground">
          Drag on the map to author <span className="text-foreground">{selectedName}</span>:
        </div>
        <WorkflowLocationsMap
          workflowId={workflowId}
          locations={stored}
          selectedName={selectedName}
          onAuthored={handleAuthored}
          disabled={busy}
        />
      </div>

      {Object.keys(stored).length > 0 && (
        <div className="space-y-0.5 border-t border-border pt-2">
          {Object.entries(stored).map(([name, loc]) => {
            const headingDeg = (loc.theta * 180) / Math.PI
            return (
              <div key={name} className="flex items-center justify-between">
                <span>
                  <span className="text-foreground">{name}</span>{" "}
                  <span className="text-muted-foreground">
                    ({loc.x.toFixed(2)}, {loc.y.toFixed(2)}) {headingDeg.toFixed(0)}°
                  </span>
                </span>
                <button
                  onClick={() => handleDelete(name)}
                  disabled={busy}
                  className="text-muted-foreground hover:text-red-500 disabled:opacity-40"
                  aria-label={`delete ${name}`}
                  title={`delete ${name}`}
                >
                  ×
                </button>
              </div>
            )
          })}
        </div>
      )}

      {error && (
        <div className="border-t border-border pt-2 text-red-500">{error}</div>
      )}
    </div>
  )
}
