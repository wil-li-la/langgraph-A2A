"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import {
  fetchNavMap,
  postNavGoto,
  setNavPose,
  subscribeNavStatus,
  type NavMapMetadata,
  type NavPose,
  type NavStatus,
  type NavTask,
} from "@/lib/nav-api"
import { useRosTopic } from "@/hooks/use-ros-topic"
import type { OccupancyGrid, Path } from "@/lib/ros-client"
import {
  renderOccupancyGrid,
  STYLE_GLOBAL_COSTMAP,
  STYLE_LOCAL_COSTMAP,
  STYLE_NVBLOX_2D,
} from "@/lib/occupancy-grid"

/**
 * Interactive room-305 map. Renders the static .pgm preview, overlays the
 * robot's current pose (from the backend's in-memory store, which is fed
 * by the room-camera localizer once that comes online), and supports two
 * RViz-style click-and-drag gestures:
 *
 *   - Drag from the robot marker → adjusts the robot pose (drag direction
 *     = new heading). Sent to POST /api/nav/pose so the backend's working
 *     pose stays in sync with what the user sees.
 *   - Drag on empty space → submits a navigation goal (drag direction =
 *     goal heading). Sent to POST /api/nav/goto.
 *
 * If the lab nav_service is down, /goto returns ROBOT_ERROR and the
 * status panel shows the failure reason. The page does not crash.
 */

const ROBOT_RADIUS_M = 0.20    // visual size of the robot marker, world metres
const HIT_RADIUS_PX = 28       // pointer-down within this many SVG units of the
                               // robot counts as "drag the robot" not "set goal"
const HEADING_LEN_M = 0.5      // length of the drag-direction arrow

type DragMode = "none" | "adjust_pose" | "set_goal"

interface DragState {
  mode: DragMode
  startWorld: { x: number; y: number }
  currentWorld: { x: number; y: number }
}

function statusColor(status: NavStatus | null, state: NavTask["state"]): string {
  if (state === "running" || state === "pending") return "text-blue-500"
  if (state === "idle") return "text-muted-foreground"
  if (status === "OK") return "text-green-500"
  return "text-red-500"
}

interface LayerState {
  nvblox: boolean
  localCostmap: boolean
  globalCostmap: boolean
  path: boolean
}

export function NavMap() {
  const [meta, setMeta] = useState<NavMapMetadata | null>(null)
  const [metaError, setMetaError] = useState<string | null>(null)
  const [pose, setPose] = useState<NavPose | null>(null)
  const [task, setTask] = useState<NavTask | null>(null)
  const [teleopActive, setTeleopActive] = useState(false)
  const [drag, setDrag] = useState<DragState | null>(null)
  const [layers, setLayers] = useState<LayerState>({
    nvblox: true,
    localCostmap: true,
    globalCostmap: false,
    path: true,
  })
  const svgRef = useRef<SVGSVGElement | null>(null)

  // Live ROS topics — null when foxglove_bridge isn't reachable. Each
  // returns latest decoded message; subscribers reuse one shared WS.
  const nvbloxSlice = useRosTopic<OccupancyGrid>(
    layers.nvblox ? "/nvblox_node/static_map_slice" : null,
  )
  const localCostmap = useRosTopic<OccupancyGrid>(
    layers.localCostmap ? "/local_costmap/costmap" : null,
  )
  const globalCostmap = useRosTopic<OccupancyGrid>(
    layers.globalCostmap ? "/global_costmap/costmap" : null,
  )
  const planMsg = useRosTopic<Path>(layers.path ? "/plan" : null)

  // ---- Bootstrap: fetch map metadata, subscribe to SSE pose+task stream

  useEffect(() => {
    fetchNavMap().then(setMeta).catch((e) => setMetaError(String(e)))
    const off = subscribeNavStatus(
      (snap) => {
        setPose(snap.pose)
        setTask(snap.task)
        setTeleopActive(snap.teleop_active)
      },
      () => { /* SSE may flicker on backend restart; EventSource auto-reconnects */ },
    )
    return off
  }, [])

  // ---- Coord conversions: world (metres, map frame) ↔ SVG pixel space.
  // SVG viewBox uses the map's pixel dimensions; world origin is the
  // bottom-left, so y is flipped.

  const worldToPx = useCallback(
    (x: number, y: number) => {
      if (!meta) return { px: 0, py: 0 }
      return {
        px: (x - meta.origin[0]) / meta.resolution,
        py: meta.height_px - (y - meta.origin[1]) / meta.resolution,
      }
    },
    [meta],
  )

  const pxToWorld = useCallback(
    (px: number, py: number) => {
      if (!meta) return { x: 0, y: 0 }
      return {
        x: px * meta.resolution + meta.origin[0],
        y: (meta.height_px - py) * meta.resolution + meta.origin[1],
      }
    },
    [meta],
  )

  const eventToWorld = useCallback(
    (e: React.PointerEvent<SVGSVGElement>) => {
      const svg = svgRef.current
      if (!svg) return null
      const pt = svg.createSVGPoint()
      pt.x = e.clientX
      pt.y = e.clientY
      const ctm = svg.getScreenCTM()
      if (!ctm) return null
      const local = pt.matrixTransform(ctm.inverse())
      return pxToWorld(local.x, local.y)
    },
    [pxToWorld],
  )

  // ---- Drag interaction

  const onPointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
    if (!meta) return
    if (teleopActive) return  // teleop is driving — drag-to-nav locked
    const w = eventToWorld(e)
    if (!w) return
    e.currentTarget.setPointerCapture(e.pointerId)

    // No pose yet → first drag must set the initial pose (matches the
    // status-bar prompt). Once pose exists, drag near the robot adjusts
    // it; drag elsewhere is a nav goal.
    let mode: DragMode = "adjust_pose"
    if (pose) {
      const robotPx = worldToPx(pose.x, pose.y)
      const clickPx = worldToPx(w.x, w.y)
      const dx = clickPx.px - robotPx.px
      const dy = clickPx.py - robotPx.py
      mode = Math.hypot(dx, dy) <= HIT_RADIUS_PX ? "adjust_pose" : "set_goal"
    }
    setDrag({ mode, startWorld: w, currentWorld: w })
  }

  const onPointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    if (!drag) return
    const w = eventToWorld(e)
    if (!w) return
    setDrag({ ...drag, currentWorld: w })
  }

  const onPointerUp = async (e: React.PointerEvent<SVGSVGElement>) => {
    if (!drag) return
    e.currentTarget.releasePointerCapture(e.pointerId)
    const { mode, startWorld, currentWorld } = drag
    setDrag(null)

    const dx = currentWorld.x - startWorld.x
    const dy = currentWorld.y - startWorld.y
    // If the user barely moved, keep the existing heading (or 0 for goals).
    const dragLen = Math.hypot(dx, dy)
    const fallbackTheta = mode === "adjust_pose" && pose ? pose.theta : 0
    const theta = dragLen > 0.05 ? Math.atan2(dy, dx) : fallbackTheta

    if (mode === "adjust_pose") {
      try {
        await setNavPose({ x: startWorld.x, y: startWorld.y, theta })
      } catch (err) {
        console.error("set pose failed", err)
      }
    } else {
      try {
        await postNavGoto({ x: startWorld.x, y: startWorld.y, theta })
      } catch (err) {
        console.error("nav goto failed", err)
      }
    }
  }

  // ---- Render

  if (metaError) {
    return (
      <div className="flex h-full items-center justify-center p-6 text-center text-sm text-red-500">
        Failed to load map metadata: {metaError}
      </div>
    )
  }
  if (!meta) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        Loading map…
      </div>
    )
  }

  const robotPx = pose ? worldToPx(pose.x, pose.y) : null
  const robotRadiusPx = ROBOT_RADIUS_M / meta.resolution

  // Preview overlay during drag — shows the would-be pose
  let previewPx: { px: number; py: number } | null = null
  let previewArrow: { fromPx: { px: number; py: number }; toPx: { px: number; py: number } } | null = null
  if (drag) {
    const startPx = worldToPx(drag.startWorld.x, drag.startWorld.y)
    const currentPx = worldToPx(drag.currentWorld.x, drag.currentWorld.y)
    previewPx = startPx
    previewArrow = { fromPx: startPx, toPx: currentPx }
  }

  // Compute SVG layout for an OccupancyGrid given its info.origin (map
  // frame, lower-left corner of the grid) + cell resolution. The grid's
  // pixel resolution differs from the static map's (e.g. nvblox is
  // 0.05 m/cell vs static 0.006), so we scale to the static-map's
  // pixel space.
  const gridImageProps = (grid: OccupancyGrid | null) => {
    if (!grid || !meta) return null
    const cell = grid.info.resolution
    const widthPxOnMap = (grid.info.width * cell) / meta.resolution
    const heightPxOnMap = (grid.info.height * cell) / meta.resolution
    const tlWorld = {
      x: grid.info.origin.position.x,
      y: grid.info.origin.position.y + grid.info.height * cell,
    }
    const tlPx = worldToPx(tlWorld.x, tlWorld.y)
    return { x: tlPx.px, y: tlPx.py, width: widthPxOnMap, height: heightPxOnMap }
  }

  // renderOccupancyGrid is WeakMap-cached on the message object — no
  // useMemo wrapper needed (and useMemo here would violate Rules of
  // Hooks since it sits below the early returns above).
  const nvbloxRender = nvbloxSlice ? renderOccupancyGrid(nvbloxSlice, STYLE_NVBLOX_2D) : null
  const localCostmapRender = localCostmap ? renderOccupancyGrid(localCostmap, STYLE_LOCAL_COSTMAP) : null
  const globalCostmapRender = globalCostmap ? renderOccupancyGrid(globalCostmap, STYLE_GLOBAL_COSTMAP) : null

  const nvbloxBox = gridImageProps(nvbloxSlice)
  const localBox = gridImageProps(localCostmap)
  const globalBox = gridImageProps(globalCostmap)

  return (
    <div className="flex h-full w-full flex-col gap-3 p-3">
      <StatusBar pose={pose} task={task} />
      {teleopActive && (
        <div className="rounded-md border border-amber-500/50 bg-amber-500/10 px-3 py-2 font-mono text-xs text-amber-700 dark:text-amber-300">
          Teleop is driving the robot — nav goals locked. Click <span className="font-semibold">Disconnect</span> in the top nav to release.
        </div>
      )}
      <div className={`flex flex-1 min-h-0 items-center justify-center bg-black/5 ${teleopActive ? "pointer-events-none opacity-60" : ""}`}>
        <svg
          ref={svgRef}
          viewBox={`0 0 ${meta.width_px} ${meta.height_px}`}
          className="h-full max-h-full max-w-full touch-none select-none"
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

          {/* Live ROS layers, painted under the robot/goal markers */}
          {globalCostmapRender && globalBox && (
            <image href={globalCostmapRender.dataUrl}
                   x={globalBox.x} y={globalBox.y}
                   width={globalBox.width} height={globalBox.height}
                   preserveAspectRatio="none"
                   style={{ imageRendering: "pixelated" }} />
          )}
          {nvbloxRender && nvbloxBox && (
            <image href={nvbloxRender.dataUrl}
                   x={nvbloxBox.x} y={nvbloxBox.y}
                   width={nvbloxBox.width} height={nvbloxBox.height}
                   preserveAspectRatio="none"
                   style={{ imageRendering: "pixelated" }} />
          )}
          {localCostmapRender && localBox && (
            <image href={localCostmapRender.dataUrl}
                   x={localBox.x} y={localBox.y}
                   width={localBox.width} height={localBox.height}
                   preserveAspectRatio="none"
                   style={{ imageRendering: "pixelated" }} />
          )}
          {/* Live planned path (nav_msgs/Path), if Nav2 is computing one */}
          {layers.path && planMsg && planMsg.poses.length > 1 && (() => {
            const pts = planMsg.poses.map((p) => worldToPx(p.pose.position.x, p.pose.position.y))
            const d = pts.map((pt, i) => `${i === 0 ? "M" : "L"} ${pt.px} ${pt.py}`).join(" ")
            return (
              <path d={d} fill="none" stroke="#10b981" strokeWidth={4}
                    strokeLinecap="round" strokeLinejoin="round" opacity={0.8} />
            )
          })()}

          {/* Drag preview */}
          {previewPx && previewArrow && (
            <g opacity={0.6}>
              <circle
                cx={previewPx.px}
                cy={previewPx.py}
                r={robotRadiusPx}
                fill={drag?.mode === "adjust_pose" ? "#3b82f6" : "#10b981"}
                fillOpacity={0.3}
                stroke={drag?.mode === "adjust_pose" ? "#3b82f6" : "#10b981"}
                strokeWidth={3}
              />
              <line
                x1={previewArrow.fromPx.px}
                y1={previewArrow.fromPx.py}
                x2={previewArrow.toPx.px}
                y2={previewArrow.toPx.py}
                stroke={drag?.mode === "adjust_pose" ? "#3b82f6" : "#10b981"}
                strokeWidth={4}
                strokeLinecap="round"
              />
            </g>
          )}

          {/* Robot marker */}
          {robotPx && pose && (
            <g>
              <circle
                cx={robotPx.px}
                cy={robotPx.py}
                r={robotRadiusPx}
                fill="#ef4444"
                fillOpacity={0.5}
                stroke="#7f1d1d"
                strokeWidth={3}
              />
              <line
                x1={robotPx.px}
                y1={robotPx.py}
                x2={robotPx.px + Math.cos(pose.theta) * (HEADING_LEN_M / meta.resolution)}
                y2={robotPx.py - Math.sin(pose.theta) * (HEADING_LEN_M / meta.resolution)}
                stroke="#7f1d1d"
                strokeWidth={4}
                strokeLinecap="round"
              />
            </g>
          )}

          {/* Goal marker (last completed task target) */}
          {task && task.state === "done" && task.status === "OK" && (() => {
            const g = worldToPx(task.target[0], task.target[1])
            return (
              <g opacity={0.6}>
                <circle cx={g.px} cy={g.py} r={robotRadiusPx * 0.6}
                        fill="none" stroke="#10b981" strokeWidth={3} strokeDasharray="6 4" />
              </g>
            )
          })()}
        </svg>
      </div>
      <LayerControls layers={layers} setLayers={setLayers} />
      <Legend pose={pose} />
    </div>
  )
}

function LayerControls({
  layers, setLayers,
}: { layers: LayerState; setLayers: (l: LayerState) => void }) {
  const items: Array<{
    key: keyof LayerState
    label: string
    swatch: string
  }> = [
    { key: "nvblox", label: "nvblox 2D slice", swatch: "#ef4444" },
    { key: "localCostmap", label: "local costmap (Nav2 + nvblox)", swatch: "#3b82f6" },
    { key: "globalCostmap", label: "global costmap (static map)", swatch: "#6366f1" },
    { key: "path", label: "planned path", swatch: "#10b981" },
  ]
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-md border border-border bg-card px-3 py-2 font-mono text-xs">
      <span className="text-muted-foreground">layers:</span>
      {items.map((item) => (
        <label key={item.key} className="flex cursor-pointer items-center gap-1.5">
          <input
            type="checkbox"
            checked={layers[item.key]}
            onChange={(e) => setLayers({ ...layers, [item.key]: e.target.checked })}
            className="cursor-pointer"
          />
          <span className="inline-block h-2.5 w-2.5 rounded-sm"
                style={{ backgroundColor: item.swatch }} />
          {item.label}
        </label>
      ))}
    </div>
  )
}

function PoseSourceBadge({ pose }: { pose: NavPose }) {
  // Three states the badge reflects:
  //   - "user"        → MANUAL (you set this by dragging; will drift on
  //                     wheel odom alone until re-set)
  //   - "localizer"   → LIVE   (the room-camera localizer is publishing;
  //                     pose tracks the robot in real time)
  //   - "nav_result"  → POST-NAV (snapshot from the last completed goal)
  //
  // Localizer support is the future state. Today, expect "user" — the
  // badge color makes that obvious so an operator doesn't mistake a
  // stale manual pose for live data.
  const ageMs = Date.now() - pose.ts_ms
  const ageMin = Math.floor(ageMs / 60000)
  const ageStr = ageMin === 0 ? "just now" : ageMin === 1 ? "1 min ago" : `${ageMin} min ago`
  switch (pose.source) {
    case "localizer":
      return (
        <span className="ml-2 rounded-sm bg-emerald-500/15 px-1.5 py-0.5 text-emerald-600 dark:text-emerald-400">
          LIVE
        </span>
      )
    case "nav_result":
      return (
        <span className="ml-2 rounded-sm bg-blue-500/15 px-1.5 py-0.5 text-blue-600 dark:text-blue-400">
          POST-NAV · {ageStr}
        </span>
      )
    default: // "user"
      return (
        <span className="ml-2 rounded-sm bg-amber-500/15 px-1.5 py-0.5 text-amber-700 dark:text-amber-300">
          MANUAL · {ageStr}
        </span>
      )
  }
}

function StatusBar({ pose, task }: { pose: NavPose | null; task: NavTask | null }) {
  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-1 rounded-md border border-border bg-card px-3 py-2 font-mono text-xs">
      <div>
        <span className="text-muted-foreground">pose:</span>{" "}
        {pose ? (
          <span>
            ({pose.x.toFixed(2)}, {pose.y.toFixed(2)}, {((pose.theta * 180) / Math.PI).toFixed(1)}°)
            <PoseSourceBadge pose={pose} />
          </span>
        ) : (
          <span className="text-muted-foreground">unknown — drag the map to set initial pose</span>
        )}
      </div>
      {task && (
        <div>
          <span className="text-muted-foreground">nav:</span>{" "}
          <span className={statusColor(task.status, task.state)}>
            {task.state === "done" ? task.status ?? "?" : task.state}
          </span>
          {task.reason && (
            <span className="ml-2 text-muted-foreground">— {task.reason}</span>
          )}
        </div>
      )}
    </div>
  )
}

function Legend({ pose }: { pose: NavPose | null }) {
  // Surface a one-time hint about the manual workflow when no real
  // localizer is publishing. Once the room cameras come online and a
  // /api/nav/pose POST returns source="localizer", the warning hides.
  const showManualHint = !pose || pose.source === "user"
  return (
    <div className="rounded-md border border-border bg-card px-3 py-2 text-xs text-muted-foreground space-y-1">
      <div>
        <span className="text-foreground">drag the red robot</span> to adjust its
        pose if localization is off; <span className="text-foreground">drag empty space</span>{" "}
        to send a nav goal (drag direction sets heading).
      </div>
      {showManualHint && (
        <div className="text-amber-700 dark:text-amber-300">
          ⚠ manual-pose mode — robot pose is tracked via wheel odometry only
          and drifts over long sessions. Re-drag the marker if the robot
          appears to be in the wrong spot. Cached pose persists across
          backend restarts.
        </div>
      )}
    </div>
  )
}
