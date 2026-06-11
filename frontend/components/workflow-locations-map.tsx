"use client"

import { useEffect, useRef, useState } from "react"
import { useNavStatus } from "@/contexts/nav-status"
import { fetchNavMap, setNavPose, type NavMapMetadata, type SetNavPoseResult } from "@/lib/nav-api"
import { eventToWorld, worldToPx } from "@/lib/map-coords"
import { colorFor } from "@/lib/location-colors"
import type { Location } from "@/lib/workflow-locations-api"

interface Props {
  workflowId: string                       // threaded for future multi-workflow use
  locations: Record<string, Location>
  selectedName: string                     // which name a drag will write
  onAuthored: (
    name: string,
    pose: { x: number; y: number; theta: number },
  ) => Promise<void>
  disabled?: boolean
}

type DragKind = "author" | "seed"
interface DragState {
  kind: DragKind
  startWorld: { x: number; y: number }
  currentWorld: { x: number; y: number }
}

// World-frame sizes; converted to SVG pixels at render time via meta.resolution.
const ROBOT_RADIUS_M = 0.20
const LOCATION_RADIUS_M = 0.18
const HEADING_LEN_M = 0.4
// Drag distance (m) below which the heading is left at 0 instead of computed
// from atan2(dy, dx). Matches NavMap.
const DRAG_THETA_THRESHOLD_M = 0.05

/**
 * Embedded interactive map for the dashboard's Locations panel. Click-
 * and-drag on the map to author the currently-selected location's pose
 * (start = (x, y), drag direction = theta). The map also overlays each
 * saved location with a stable per-name color and shows the robot's
 * live pose read-only (writes go through the existing dashboard PUT
 * endpoint, not this component).
 */
export function WorkflowLocationsMap({
  workflowId: _workflowId,
  locations,
  selectedName,
  onAuthored,
  disabled = false,
}: Props) {
  const { pose, teleopActive, localization } = useNavStatus()
  const [meta, setMeta] = useState<NavMapMetadata | null>(null)
  const [metaError, setMetaError] = useState<string | null>(null)
  const [drag, setDrag] = useState<DragState | null>(null)
  const [seedStatus, setSeedStatus] = useState<{ kind: "ok" | "err"; text: string } | null>(null)
  const svgRef = useRef<SVGSVGElement | null>(null)

  useEffect(() => {
    fetchNavMap().then(setMeta).catch((e) => setMetaError(String(e)))
  }, [])

  // Seeding AMCL is independent of the location-author gate: operator
  // can re-seed even when no name is selected, and even while teleop is
  // active (teleop locks driving, not localization).
  const authorLocked = disabled || teleopActive || !selectedName

  const onPointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
    if (!meta) return
    const kind: DragKind = e.shiftKey ? "seed" : "author"
    if (kind === "author" && authorLocked) return
    const w = eventToWorld(svgRef.current, meta, e)
    if (!w) return
    e.currentTarget.setPointerCapture(e.pointerId)
    setDrag({ kind, startWorld: w, currentWorld: w })
  }

  const onPointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    if (!drag || !meta) return
    const w = eventToWorld(svgRef.current, meta, e)
    if (!w) return
    setDrag({ ...drag, currentWorld: w })
  }

  const onPointerUp = async (e: React.PointerEvent<SVGSVGElement>) => {
    if (!drag) return
    e.currentTarget.releasePointerCapture(e.pointerId)
    const { kind, startWorld, currentWorld } = drag
    setDrag(null)
    const dx = currentWorld.x - startWorld.x
    const dy = currentWorld.y - startWorld.y
    const dragLen = Math.hypot(dx, dy)
    const theta = dragLen > DRAG_THETA_THRESHOLD_M ? Math.atan2(dy, dx) : 0
    if (kind === "seed") {
      setSeedStatus({
        kind: "ok",
        text: `seeding (${startWorld.x.toFixed(2)}, ${startWorld.y.toFixed(2)}, ${(theta * 180 / Math.PI).toFixed(0)}°)…`,
      })
      try {
        const r: SetNavPoseResult = await setNavPose({ x: startWorld.x, y: startWorld.y, theta })
        setSeedStatus(
          !r.seed.forwarded
            ? { kind: "err", text: `forward failed: ${r.seed.error ?? "unknown"}` }
            : !r.seed.ok
              ? { kind: "err", text: `robot rejected: ${r.seed.reply ?? "unknown"}` }
              : { kind: "ok", text: `seeded → robot ${r.seed.reply ?? "ok"}` },
        )
      } catch (err) {
        setSeedStatus({ kind: "err", text: `error: ${(err as Error).message ?? err}` })
      }
      return
    }
    try {
      await onAuthored(selectedName, { x: startWorld.x, y: startWorld.y, theta })
    } catch (err) {
      console.error("author location failed", err)
    }
  }

  if (metaError) {
    return (
      <div className="rounded-md border border-border bg-card p-2 font-mono text-xs text-red-500">
        Map metadata error: {metaError}
      </div>
    )
  }
  if (!meta) {
    return (
      <div className="rounded-md border border-border bg-card p-2 font-mono text-xs text-muted-foreground">
        Loading map…
      </div>
    )
  }

  const robotPx = pose ? worldToPx(meta, pose.x, pose.y) : null
  const robotRadiusPx = ROBOT_RADIUS_M / meta.resolution
  const locationRadiusPx = LOCATION_RADIUS_M / meta.resolution
  const headingLenPx = HEADING_LEN_M / meta.resolution

  const dragPreview = drag ? {
    kind: drag.kind,
    start: worldToPx(meta, drag.startWorld.x, drag.startWorld.y),
    current: worldToPx(meta, drag.currentWorld.x, drag.currentWorld.y),
  } : null

  const SEED_COLOR = "#38bdf8"   // sky-400, distinct from any location color

  const locColor =
    localization?.state === "ok" ? "text-emerald-400"
    : localization?.state === "stale" ? "text-amber-400"
    : localization?.state === "unseeded" ? "text-red-400"
    : "text-muted-foreground/60"
  const locLabel =
    localization?.state === "ok" ? "AMCL: OK"
    : localization?.state === "stale" ? "AMCL: STALE"
    : localization?.state === "unseeded" ? "AMCL: UNSEEDED"
    : "AMCL: ?"

  return (
    <div className="overflow-hidden rounded-md border border-border bg-black/5">
      <div className="flex items-center gap-2 border-b border-border px-2 py-1">
        <span className={`font-mono text-xs ${locColor}`}>{locLabel}</span>
        <span className="ml-auto font-mono text-xs text-muted-foreground/60">
          drag: author · shift+drag: seed AMCL pose
        </span>
      </div>
      {seedStatus && (
        <div className={`px-2 py-1 font-mono text-xs border-b border-border ${
          seedStatus.kind === "ok" ? "bg-emerald-500/10 text-emerald-300" : "bg-red-500/10 text-red-300"
        }`}>
          {seedStatus.text}
        </div>
      )}
      <div
        className={authorLocked ? "opacity-60" : ""}
        title={authorLocked && !disabled && !teleopActive ? "Pick a location name to author (or use shift+drag to seed AMCL)" : undefined}
      >
      <svg
        ref={svgRef}
        viewBox={`0 0 ${meta.width_px} ${meta.height_px}`}
        className="block w-full touch-none select-none"
        style={{ aspectRatio: `${meta.width_px} / ${meta.height_px}` }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
      >
        <image
          href={meta.image}
          x={0}
          y={0}
          width={meta.width_px}
          height={meta.height_px}
          preserveAspectRatio="none"
        />

        {/* Saved location markers */}
        {Object.entries(locations).map(([name, loc]) => {
          const { px, py } = worldToPx(meta, loc.x, loc.y)
          const color = colorFor(name)
          const arrowX = px + Math.cos(loc.theta) * headingLenPx
          const arrowY = py - Math.sin(loc.theta) * headingLenPx
          return (
            <g key={name} pointerEvents="none">
              <circle
                cx={px}
                cy={py}
                r={locationRadiusPx}
                fill={color}
                fillOpacity={0.5}
                stroke={color}
                strokeWidth={3}
              />
              <line
                x1={px}
                y1={py}
                x2={arrowX}
                y2={arrowY}
                stroke={color}
                strokeWidth={3}
                strokeLinecap="round"
              />
              <text
                x={px + locationRadiusPx + 4}
                y={py + 4}
                fontSize={locationRadiusPx * 1.5}
                fontFamily="monospace"
                fill={color}
              >
                {name}
              </text>
            </g>
          )
        })}

        {/* Robot pose (read-only). Drawn LAST so it sits on top of any
            location marker that shares the same spot — important since
            AMCL often seeds at the same world coords as the "origin"
            location. The outer dashed ring + label make it visually
            distinct from the colored location markers. */}
        {robotPx && pose && (
          <g pointerEvents="none">
            <circle
              cx={robotPx.px}
              cy={robotPx.py}
              r={robotRadiusPx * 1.4}
              fill="none"
              stroke="#ef4444"
              strokeWidth={2}
              strokeDasharray="6 4"
              opacity={0.8}
            />
            <circle
              cx={robotPx.px}
              cy={robotPx.py}
              r={robotRadiusPx}
              fill="#ef4444"
              fillOpacity={0.6}
              stroke="#7f1d1d"
              strokeWidth={3}
            />
            <line
              x1={robotPx.px}
              y1={robotPx.py}
              x2={robotPx.px + Math.cos(pose.theta) * headingLenPx}
              y2={robotPx.py - Math.sin(pose.theta) * headingLenPx}
              stroke="#7f1d1d"
              strokeWidth={4}
              strokeLinecap="round"
            />
            <text
              x={robotPx.px + robotRadiusPx * 1.6}
              y={robotPx.py + 4}
              fontSize={robotRadiusPx * 1.5}
              fontFamily="monospace"
              fontWeight="bold"
              fill="#ef4444"
            >
              robot
            </text>
          </g>
        )}

        {/* Drag preview (in-flight author OR seed). Seed uses sky-blue
            to be visually distinct from any per-location color. */}
        {dragPreview && (dragPreview.kind === "seed" || !authorLocked) && (
          <g opacity={0.7} pointerEvents="none">
            <circle
              cx={dragPreview.start.px}
              cy={dragPreview.start.py}
              r={locationRadiusPx}
              fill={dragPreview.kind === "seed" ? SEED_COLOR : colorFor(selectedName)}
              fillOpacity={0.3}
              stroke={dragPreview.kind === "seed" ? SEED_COLOR : colorFor(selectedName)}
              strokeWidth={3}
            />
            <line
              x1={dragPreview.start.px}
              y1={dragPreview.start.py}
              x2={dragPreview.current.px}
              y2={dragPreview.current.py}
              stroke={dragPreview.kind === "seed" ? SEED_COLOR : colorFor(selectedName)}
              strokeWidth={3}
              strokeLinecap="round"
            />
          </g>
        )}
      </svg>
      </div>
    </div>
  )
}
