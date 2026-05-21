# Interactive Locations map in the workflow card

**Date:** 2026-05-21
**Status:** Design approved (no-clarifying-questions mode)

## Problem

The current `LocationsPanel` on `/` is text-only. To teach a location, the
operator must:

1. Switch to `/nav`, drag the robot to the desired spot to set `_pose`.
2. Switch back to `/`, pick the location name from a dropdown, click
   "Save current pose as …".

That two-step context switch is friction, and there is no visual
confirmation that the saved locations are where the operator intended.

## Goals

1. Embed a small interactive map directly inside the workflow card so the
   operator can author location poses without leaving `/`.
2. Click-and-drag on the embedded map (RViz-style: drag start = (x, y),
   drag direction = θ) writes the selected location's pose.
3. All saved locations render as colored markers on the embedded map,
   with stable per-name colors so the operator can verify position at a
   glance.

## Non-goals

- Replacing `/nav`'s interactive features. `/nav` keeps drag-to-set-pose,
  drag-to-set-goal, layer controls, hover readout, costmap overlays.
- Editing a location by clicking on its existing marker. Overwrite is
  done by selecting the name in the dropdown and dragging again.
- Showing nav goals, plans, or live costmaps on the embedded workflow
  map. It is purely a locations-authoring view.
- Multi-select / bulk delete.

## Design

### A) Shared coord helper

Extract the world↔pixel coordinate logic from `NavMap` into a tiny
module that both maps consume. No behavior change to `/nav`.

`frontend/lib/map-coords.ts`:

```ts
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

`NavMap` updates to call these instead of its inlined `useCallback`
versions. Behavior on `/nav` is identical.

### B) Location color palette

`frontend/lib/location-colors.ts`:

```ts
const CANONICAL: Record<string, string> = {
  medicine: "#f59e0b",   // amber-500
  patient:  "#3b82f6",   // blue-500
  origin:   "#10b981",   // emerald-500
}

const FALLBACK = ["#a855f7", "#ec4899", "#14b8a6", "#f97316"]

export function colorFor(name: string): string {
  if (CANONICAL[name]) return CANONICAL[name]
  // FNV-1a hash → fallback index (stable across reloads, deterministic).
  let h = 2166136261
  for (let i = 0; i < name.length; i++) {
    h ^= name.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return FALLBACK[Math.abs(h) % FALLBACK.length]
}
```

### C) `WorkflowLocationsMap` component

`frontend/components/workflow-locations-map.tsx` — purpose: render the
map + colored location markers + the click-and-drag authoring gesture.

Props:

```ts
interface Props {
  workflowId: string                       // unused today, threaded for future
  locations: Record<string, Location>      // saved locations to overlay
  selectedName: string                     // dropdown selection — name being authored
  onAuthored: (name: string,
               pose: { x: number; y: number; theta: number }) => Promise<void>
  disabled?: boolean                       // when true, drag is no-op
}
```

Internal state:
- `meta: NavMapMetadata | null` — fetched once via `fetchNavMap()`.
- `drag: { startWorld, currentWorld } | null` — like NavMap's drag state
  but only one mode (author location), no pose-drag-vs-goal-drag branch.

The map element is an SVG with `viewBox` = `meta.width_px × meta.height_px`.
Children:
1. The `<image href={meta.image}>` (renders `/maps/305_map.png`).
2. For each entry in `locations`: a `<g>` with a colored filled circle,
   heading-direction line, and a small text label with the name.
3. The robot pose from `useNavStatus()` as a small red dot — read-only,
   no interaction. Only rendered if `pose` is non-null.
4. The drag preview during an active drag (same look as NavMap's: a
   translucent circle at the start point with an arrow to the current
   pointer, colored to match `selectedName`).

On `pointerUp` (after a drag), compute (x, y, θ) and call
`onAuthored(selectedName, {x, y, theta})`. Theta = `atan2(dy, dx)` when
the drag length exceeds 0.05 m, else 0 (matching the existing NavMap
heuristic).

The component does NOT subscribe to its own SSE — it consumes
`useNavStatus()` for the live robot pose and reads `meta` from a
one-shot `fetchNavMap()` call (the metadata doesn't change at runtime).

### D) `LocationsPanel` integration

The existing `LocationsPanel` keeps its responsibilities:
- Owns `manifest`, `stored`, `selectedName`, `busy`, `error` state.
- Renders the required-status row, the dropdown, the save-current-pose
  button, the list of saved locations with `×` delete.

New additions:
- The component renders `<WorkflowLocationsMap …/>` between the
  dropdown/save row and the saved-locations list.
- `onAuthored(name, pose)` calls `setWorkflowLocation(workflowId, name, pose)`
  from the existing REST client (`PUT /api/workflows/<wf>/locations/<name>`),
  then refreshes the stored list.
- The dropdown default value becomes the *next* un-taught required name
  (or the first required name if all are taught) — small UX nudge so the
  operator's first click sets `medicine`, then `patient`, then `origin`.

### E) Layout

The workflow card lives in the right column of `RobotDashboard`
(`lg:w-[420px] xl:w-[500px] 2xl:w-[560px]`). The map's intrinsic aspect
ratio with the raw 305 map is 2059×1259 ≈ 1.635. At a card content
width of ~380 px, the map is ~232 px tall — a comfortable size that
doesn't dominate the right column.

The map element uses `aspect-ratio: meta.width_px / meta.height_px` and
fills the available width via `w-full max-h-[300px]`. On wider screens
the panel grows to a slightly larger map without pushing other content
out.

### F) Interaction details

- Map shows everything regardless of teleop state. Drag-to-author is
  disabled when `disabled={teleopActive}` is passed from
  `LocationsPanel` — same lockout policy as `/nav`.
- During a drag, the live preview circle uses `colorFor(selectedName)`
  so the operator sees the in-flight color of the location they are
  authoring.
- After a successful drag-and-release, the panel re-fetches via
  `listWorkflowLocations(workflowId)` and the new marker appears on the
  map in its final position.
- Clicking on an existing location's marker is a no-op. Overwrite is
  done by selecting the name in the dropdown and dragging.

## Files touched

**New:**
- `frontend/lib/map-coords.ts` — shared world↔pixel helpers.
- `frontend/lib/location-colors.ts` — stable per-name color map + hash
  fallback.
- `frontend/components/workflow-locations-map.tsx` — interactive
  embedded map.

**Edited:**
- `frontend/components/nav-map.tsx` — replaces its inlined `useCallback`
  versions of `worldToPx`/`pxToWorld`/`eventToWorld` with calls to the
  shared helpers. No behavioral change.
- `frontend/components/locations-panel.tsx` — renders the new map below
  the dropdown row; hoists `selectedName` default to the next-missing
  name; routes drag-authored poses through `setWorkflowLocation`.

**Not touched:** the existing nav SSE, the `/nav` page itself, the
backend, the workflow registry. Zero backend changes.

## Testing

Manual verification (repo has no test framework):

1. `pnpm exec tsc --noEmit` — clean (modulo the pre-existing 6 errors
   in `hooks/use-nvblox-mesh.ts`).
2. Open `/`. The Locations panel now includes a small map. The robot's
   cached pose `(-3.02, -3.10)` shows as a small red dot.
3. With dropdown set to `medicine`, click-and-drag on the map. The
   preview shows an amber circle + arrow. On release, a permanent
   amber marker appears at the dropped position, labeled "medicine".
   Required-status row updates: `medicine ✓`.
4. Switch dropdown to `patient`. Drag elsewhere. New blue marker.
   Required-status: `patient ✓`.
5. Click "Save current pose" with `origin` selected. Green marker at
   the cached pose. Required-status: `all taught`.
6. Click the `×` on `patient` in the list — its marker disappears
   from the map.
7. Re-select `patient`, drag again — marker reappears at the new
   location.
8. Visit `/nav`. The page behaves exactly as before (regression check
   on the coord-helper extraction).
9. `git diff main -- frontend/app/nav/ frontend/components/nav-bar.tsx
   frontend/contexts/nav-status.tsx backend/` shows zero changes. The
   feature is frontend-only.
