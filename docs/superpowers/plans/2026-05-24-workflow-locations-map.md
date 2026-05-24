# Interactive Locations map in the workflow card — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Embed an interactive map inside the dashboard's Locations card so the operator can author per-workflow location poses by clicking and dragging on it, with all saved locations rendered as colored markers.

**Architecture:** Extract the world↔pixel coordinate helpers from `NavMap` into a shared `map-coords.ts` module so both the existing `/nav` page and the new dashboard widget consume one implementation. Add a small color helper, a new `WorkflowLocationsMap` SVG component, and mount it inside the existing `LocationsPanel`. Drag-release writes via the existing `PUT /api/workflows/<wf>/locations/<name>` endpoint. No backend changes.

**Tech Stack:** Next.js 14 App Router, React 18, TypeScript, Tailwind. No new dependencies. No new endpoints.

**Repo specifics:** No test framework configured (per `CLAUDE.md`: "verification is manual/integration only"). Verification = `cd frontend && pnpm exec tsc --noEmit` + visual UI walkthrough. Pre-existing 6 TS errors in `hooks/use-nvblox-mesh.ts` (untracked file) are out of scope — accept them. `pnpm lint` is broken in this repo (Next.js 16 dropped `next lint`); do NOT use it for verification.

**Branching:** Direct commits on `main`, per repo workflow.

---

## File map

**New:**
- `frontend/lib/map-coords.ts` — `worldToPx`, `pxToWorld`, `eventToWorld` (pure functions taking `NavMapMetadata` as a param).
- `frontend/lib/location-colors.ts` — `colorFor(name)` returning a stable hex string.
- `frontend/components/workflow-locations-map.tsx` — interactive SVG widget.

**Edited:**
- `frontend/components/nav-map.tsx` — drop the 3 inlined `useCallback` helpers, call the shared ones instead. No behavior change.
- `frontend/components/locations-panel.tsx` — mount the new map; route drag-authored poses through `setWorkflowLocation`; default dropdown to the next un-taught required name.

**Not touched:** `/nav` page, backend, SSE, `nav-bar`, `nav-status` context.

---

### Task 1: Extract shared world↔pixel coordinate helpers

**Files:**
- Create: `frontend/lib/map-coords.ts`

- [ ] **Step 1: Create the helper module**

```ts
// frontend/lib/map-coords.ts
//
// Pure world↔pixel coord helpers for any SVG map rendering the
// /api/nav/map metadata. Consumed by both the /nav page's NavMap and
// the dashboard's WorkflowLocationsMap so both speak the same frame.
//
// World y is flipped relative to SVG y: world origin is the bottom-left
// corner of the map, SVG origin is top-left. The conversions account
// for that and for the resolution (metres per pixel).

import type { NavMapMetadata } from "@/lib/nav-api"

export function worldToPx(
  meta: NavMapMetadata | null, x: number, y: number,
): { px: number; py: number } {
  if (!meta) return { px: 0, py: 0 }
  return {
    px: (x - meta.origin[0]) / meta.resolution,
    py: meta.height_px - (y - meta.origin[1]) / meta.resolution,
  }
}

export function pxToWorld(
  meta: NavMapMetadata | null, px: number, py: number,
): { x: number; y: number } {
  if (!meta) return { x: 0, y: 0 }
  return {
    x: px * meta.resolution + meta.origin[0],
    y: (meta.height_px - py) * meta.resolution + meta.origin[1],
  }
}

export function eventToWorld(
  svg: SVGSVGElement | null,
  meta: NavMapMetadata | null,
  e: { clientX: number; clientY: number },
): { x: number; y: number } | null {
  if (!svg || !meta) return null
  const pt = svg.createSVGPoint()
  pt.x = e.clientX
  pt.y = e.clientY
  const ctm = svg.getScreenCTM()
  if (!ctm) return null
  const local = pt.matrixTransform(ctm.inverse())
  return pxToWorld(meta, local.x, local.y)
}
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && pnpm exec tsc --noEmit`
Expected: clean (the 6 pre-existing errors in `hooks/use-nvblox-mesh.ts` are unchanged and out of scope).

- [ ] **Step 3: Commit**

```bash
git add frontend/lib/map-coords.ts
git commit -m "refactor(map): extract world↔pixel coord helpers"
```

---

### Task 2: Migrate `NavMap` to consume the shared helpers

**Files:**
- Modify: `frontend/components/nav-map.tsx`

Pure refactor — no behavior change to `/nav`. The three inlined `useCallback` helpers are replaced by calls to the shared module, threading `meta` through as a parameter.

- [ ] **Step 1: Add the import**

In `frontend/components/nav-map.tsx`, alongside the existing imports near the top of the file, add:

```tsx
import { eventToWorld as eventToWorldShared, pxToWorld as pxToWorldShared, worldToPx as worldToPxShared } from "@/lib/map-coords"
```

The "as …Shared" aliasing keeps the existing local names (`worldToPx`, `pxToWorld`, `eventToWorld`) inside the component intact — every call site stays unchanged after Step 2.

- [ ] **Step 2: Replace the three inlined `useCallback` helpers**

Locate the block (currently lines 103–142 of `nav-map.tsx`):

```tsx
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
```

and replace it with thin wrappers that delegate to the shared functions:

```tsx
  // ---- Coord conversions: world (metres, map frame) ↔ SVG pixel space.
  // Backed by lib/map-coords.ts so the dashboard's workflow map uses the
  // same implementation. SVG viewBox uses the map's pixel dimensions;
  // world origin is the bottom-left, so y is flipped.

  const worldToPx = useCallback(
    (x: number, y: number) => worldToPxShared(meta, x, y),
    [meta],
  )

  const pxToWorld = useCallback(
    (px: number, py: number) => pxToWorldShared(meta, px, py),
    [meta],
  )

  const eventToWorld = useCallback(
    (e: React.PointerEvent<SVGSVGElement>) => eventToWorldShared(svgRef.current, meta, e),
    [meta],
  )
```

Every call to `worldToPx(...)`, `pxToWorld(...)`, and `eventToWorld(e)` inside the component continues to work unchanged.

- [ ] **Step 3: Check `useCallback` is still imported**

The new code still uses `useCallback`. `nav-map.tsx` already imports it (`import { useCallback, useEffect, useRef, useState } from "react"`). No import change needed.

- [ ] **Step 4: Type-check + visual regression**

Run: `cd frontend && pnpm exec tsc --noEmit`
Expected: clean (modulo the 6 pre-existing `use-nvblox-mesh.ts` errors).

Then visually exercise `/nav` in the browser (the dev server is hot-reloading): the map should render, the hover-coord pill should still follow the cursor, drag-to-set-pose and drag-to-set-goal should still work, the costmap layers should still overlay. No behavior change is expected.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/nav-map.tsx
git commit -m "refactor(nav-map): consume shared map-coords helpers"
```

---

### Task 3: Stable location-color helper

**Files:**
- Create: `frontend/lib/location-colors.ts`

- [ ] **Step 1: Create the helper**

```ts
// frontend/lib/location-colors.ts
//
// Stable per-name color for a saved workflow location. The three canonical
// names that the medication_delivery workflow declares as required get
// hand-picked hues that read as "medical / patient / home". Free-text
// names fall back to an FNV-1a-hashed palette index so a given name always
// gets the same color across reloads without colliding with the canonical
// three.

const CANONICAL: Record<string, string> = {
  medicine: "#f59e0b",   // amber-500
  patient:  "#3b82f6",   // blue-500
  origin:   "#10b981",   // emerald-500
}

const FALLBACK = ["#a855f7", "#ec4899", "#14b8a6", "#f97316"]

export function colorFor(name: string): string {
  const canonical = CANONICAL[name]
  if (canonical) return canonical
  let h = 2166136261
  for (let i = 0; i < name.length; i++) {
    h ^= name.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return FALLBACK[Math.abs(h) % FALLBACK.length]
}
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && pnpm exec tsc --noEmit`
Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add frontend/lib/location-colors.ts
git commit -m "feat(locations): add stable per-name color helper"
```

---

### Task 4: `WorkflowLocationsMap` component

**Files:**
- Create: `frontend/components/workflow-locations-map.tsx`

Interactive SVG map for the dashboard's Locations card. Fetches map metadata once. Renders the map image, saved location markers (colored by `colorFor`), the live robot pose (read-only), and the drag preview. Drag-release calls `onAuthored(name, pose)`.

- [ ] **Step 1: Create the component**

```tsx
// frontend/components/workflow-locations-map.tsx
"use client"

import { useEffect, useRef, useState } from "react"
import { useNavStatus } from "@/contexts/nav-status"
import { fetchNavMap, type NavMapMetadata } from "@/lib/nav-api"
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

interface DragState {
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
  const { pose, teleopActive } = useNavStatus()
  const [meta, setMeta] = useState<NavMapMetadata | null>(null)
  const [metaError, setMetaError] = useState<string | null>(null)
  const [drag, setDrag] = useState<DragState | null>(null)
  const svgRef = useRef<SVGSVGElement | null>(null)

  useEffect(() => {
    fetchNavMap().then(setMeta).catch((e) => setMetaError(String(e)))
  }, [])

  const locked = disabled || teleopActive || !selectedName

  const onPointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
    if (!meta || locked) return
    const w = eventToWorld(svgRef.current, meta, e)
    if (!w) return
    e.currentTarget.setPointerCapture(e.pointerId)
    setDrag({ startWorld: w, currentWorld: w })
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
    const { startWorld, currentWorld } = drag
    setDrag(null)
    const dx = currentWorld.x - startWorld.x
    const dy = currentWorld.y - startWorld.y
    const dragLen = Math.hypot(dx, dy)
    const theta = dragLen > DRAG_THETA_THRESHOLD_M ? Math.atan2(dy, dx) : 0
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
    start: worldToPx(meta, drag.startWorld.x, drag.startWorld.y),
    current: worldToPx(meta, drag.currentWorld.x, drag.currentWorld.y),
  } : null

  return (
    <div
      className={`overflow-hidden rounded-md border border-border bg-black/5 ${locked ? "opacity-60" : ""}`}
      title={locked && !disabled && !teleopActive ? "Pick a location name to author" : undefined}
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
                fontSize="14"
                fontFamily="monospace"
                fill={color}
              >
                {name}
              </text>
            </g>
          )
        })}

        {/* Robot pose (read-only) */}
        {robotPx && pose && (
          <g pointerEvents="none">
            <circle
              cx={robotPx.px}
              cy={robotPx.py}
              r={robotRadiusPx}
              fill="#ef4444"
              fillOpacity={0.5}
              stroke="#7f1d1d"
              strokeWidth={2}
            />
            <line
              x1={robotPx.px}
              y1={robotPx.py}
              x2={robotPx.px + Math.cos(pose.theta) * headingLenPx}
              y2={robotPx.py - Math.sin(pose.theta) * headingLenPx}
              stroke="#7f1d1d"
              strokeWidth={3}
              strokeLinecap="round"
            />
          </g>
        )}

        {/* Drag preview (in-flight author) */}
        {dragPreview && !locked && (
          <g opacity={0.6} pointerEvents="none">
            <circle
              cx={dragPreview.start.px}
              cy={dragPreview.start.py}
              r={locationRadiusPx}
              fill={colorFor(selectedName)}
              fillOpacity={0.3}
              stroke={colorFor(selectedName)}
              strokeWidth={3}
            />
            <line
              x1={dragPreview.start.px}
              y1={dragPreview.start.py}
              x2={dragPreview.current.px}
              y2={dragPreview.current.py}
              stroke={colorFor(selectedName)}
              strokeWidth={3}
              strokeLinecap="round"
            />
          </g>
        )}
      </svg>
    </div>
  )
}
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && pnpm exec tsc --noEmit`
Expected: clean. (If you see "type only import used as value" errors for `Location`, double-check the import line — `import type { Location } from "@/lib/workflow-locations-api"` is correct because `Location` is only used as a TypeScript type, not constructed.)

- [ ] **Step 3: Commit**

```bash
git add frontend/components/workflow-locations-map.tsx
git commit -m "feat(locations): add interactive map widget for workflow card"
```

---

### Task 5: Mount the map in `LocationsPanel`

**Files:**
- Modify: `frontend/components/locations-panel.tsx`

Wires the new map into the existing panel. Adds a `setWorkflowLocation` REST call (already exported from the Task 9 client) and a `handleAuthored` callback. Tweaks the dropdown default to the next un-taught required name.

- [ ] **Step 1: Add new imports**

In `frontend/components/locations-panel.tsx`, extend the existing import block to include `setWorkflowLocation`, and add a second import for the new map component. The new top of the file reads:

```tsx
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
```

- [ ] **Step 2: Default the dropdown to the next un-taught required name**

Replace the initial-fetch `useEffect` (currently lines 33–48 of `locations-panel.tsx`). Find this block:

```tsx
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
        setSelectedName(m.required_locations[0])
      }
    }).catch((e) => !cancelled && setError(String(e)))
    return () => { cancelled = true }
  }, [workflowId])
```

and replace with the next-missing default:

```tsx
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
```

After the initial set, the dropdown is fully user-controlled — no further effect overrides their selection.

- [ ] **Step 3: Add the `handleAuthored` callback**

Below the existing `handleDelete` function (currently around lines 72–82), add:

```tsx
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
```

- [ ] **Step 4: Mount the map between the dropdown row and the saved-locations list**

Locate the closing `</div>` of the "Save current pose as" row inside the `return (...)` (currently around lines 134–149 of `locations-panel.tsx` — the `<div className="flex items-center gap-2 border-t border-border pt-2">` block). Immediately after that closing `</div>` and before the `{Object.keys(stored).length > 0 && (` conditional, insert:

```tsx
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
```

- [ ] **Step 5: Type-check + visual verify**

Run: `cd frontend && pnpm exec tsc --noEmit`
Expected: clean.

Then visually exercise the dashboard at `http://localhost:3000/`:
- The Locations panel now contains a small map between the "Save current pose as" row and the saved-locations list.
- On a fresh backend (`rm -rf ~/.cache/langgraph-A2A/locations/`), the dropdown defaults to `medicine` (first un-taught).
- Dragging on the map writes the new pose: an amber marker (medicine's color) appears at the dropped position with a heading arrow.
- The dropdown still updates user-controllably; selecting `patient` and dragging makes a blue marker appear.
- The "Save current pose" button still works as before.
- The `×` buttons in the saved-locations list still delete entries; the corresponding marker disappears from the map.
- Switching to agentic mode hides the entire panel (same as today).
- Visit `/nav` and confirm the existing map/drag/hover behavior is unchanged (regression check on Task 2's refactor).

- [ ] **Step 6: Commit**

```bash
git add frontend/components/locations-panel.tsx
git commit -m "feat(dashboard): mount interactive Locations map; default to next un-taught"
```

---

## Self-review

**Spec coverage:**

- Spec section A (shared coord helper) → Task 1 (create) + Task 2 (migrate NavMap).
- Spec section B (location color palette) → Task 3.
- Spec section C (`WorkflowLocationsMap` component) → Task 4. Renders the map image, saved-location markers with `colorFor`, the read-only robot pose, and the drag preview using `colorFor(selectedName)`. Uses `useNavStatus()` for the robot pose. Calls `onAuthored` on drag-release. Drag threshold = 0.05 m matches NavMap.
- Spec section D (`LocationsPanel` integration) → Task 5. Mounts the map, routes via `setWorkflowLocation`, defaults to next-missing.
- Spec section E (layout) → Task 4 step 1 — `aspect-ratio: meta.width_px / meta.height_px` with `w-full` matches the spec's "fills the available width" requirement; the parent's existing `lg:w-[420px]…2xl:w-[560px]` provides the width budget. No `max-h` needed since aspect ratio governs height.
- Spec section F (interaction details) → Task 4 (drag preview color, teleop lockout, single-source `_workflowId` thread-through) + Task 5 (refresh after authoring).
- Spec "not touched": no backend / SSE / `/nav` page / `nav-bar` / `nav-status` changes in any task. Confirmed.

**Placeholder scan:** no TBDs. Every step has the exact code or command needed. The line-number references in Task 5 are anchored by quoting the surrounding code (the engineer matches by content, not line number, if the file has drifted).

**Type / name consistency:**
- `Location` type from `@/lib/workflow-locations-api` is the same one consumed in Task 4 (component) and Task 5 (panel passes `stored: Record<string, Location>` as `locations`). Matches the existing store shape (`{x, y, theta, ts_ms}`).
- `worldToPx(meta, x, y)`, `pxToWorld(meta, px, py)`, `eventToWorld(svg, meta, e)` — signatures in Task 1 match call sites in Task 2 (via aliased wrappers) and Task 4 (direct calls).
- `colorFor(name)` returns `string` (hex) — consumed identically in Task 4's marker render, drag preview, and label.
- `onAuthored(name, pose)` — signature defined in Task 4 (Props), implemented in Task 5 (`handleAuthored`). Pose shape `{x, y, theta}` matches `setWorkflowLocation`'s body (already part of the existing REST client).
- `WORKFLOW_ID = "medication_delivery"` is not duplicated in this plan — Task 5 passes `workflowId={workflowId}` through from the existing panel prop, which is already wired in `RobotDashboard` to `"medication_delivery"`.

**Verification adjustments:**
- `pnpm lint` is broken (Next.js 16 dropped `next lint`); every "type-check" step uses `pnpm exec tsc --noEmit`.
- No test framework; verification is `tsc --noEmit` + manual UI walkthrough on the running dev server.
- 6 pre-existing TS errors in `hooks/use-nvblox-mesh.ts` are unrelated; ignore.
