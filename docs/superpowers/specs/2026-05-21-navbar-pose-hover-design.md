# Global nav-status indicator + map hover readout

**Date:** 2026-05-21
**Status:** Design approved (no-clarifying-questions mode)

## Problem

Today the live nav state (`nav: idle / running / OK / ...`) and the robot's
`(x, y)` pose only appear on the `/nav` page, inside `NavMap`'s `StatusBar`.
An operator using `/`, `/teleop`, `/recon`, or `/cameras` has no way to see
where the robot thinks it is without context-switching to the map page.

Also, when planning a goal click on the map, there is no per-cursor coordinate
readout — the operator has to eyeball the position against the grid.

## Goals

1. Show `nav: <state>` and the robot's current `(x, y)` on the top `NavBar`,
   so it is visible on every page.
2. On the `/nav` map, show a small `(x, y)` label that follows the cursor
   when hovering, so click targets can be placed precisely.

## Non-goals

- No backend changes. `/api/nav/status/stream` already streams `{pose, task,
  teleop_active}` and is what the existing nav page subscribes to.
- No heading / source-badge / task-reason in the NavBar indicator — that
  detail stays on the `/nav` page's `StatusBar`. The NavBar version is a
  glanceable summary, not a replacement.
- No hover readout off the `/nav` page.

## Design

### A) Lift nav-status subscription into a global context

Today `NavMap` calls `subscribeNavStatus(...)` in a `useEffect`, opening one
EventSource per mount. If `NavBar` also subscribed independently, every page
would open a second SSE connection.

Add `frontend/contexts/nav-status.tsx`:

```ts
interface NavStatusContextValue {
  pose: NavPose | null
  task: NavTask | null
  teleopActive: boolean
}
```

The provider runs `subscribeNavStatus` once on mount and exposes the latest
snapshot through `useNavStatus()`. Mount it in `app/layout.tsx` inside the
existing provider stack (next to `RobotConnectionProvider`).

`NavMap` is updated to read from `useNavStatus()` instead of running its own
subscription. Behavior on `/nav` is unchanged — the data still arrives via
the same SSE endpoint, just sourced from the provider.

### B) NavBar indicator

In `frontend/components/nav-bar.tsx`, insert a new block between the robot
connection status group and the `ml-auto` mode-toggle group:

```
nav: idle  (1.23, 4.56)
```

- `nav: <state>` — `state` is `task.state` (`idle | pending | running | done`)
  except when `state === "done"` we render `task.status` (`OK | ROBOT_ERROR |
  …`) so a finished task shows its result, matching the StatusBar's existing
  display rule.
- The colored class comes from a shared `navStatusColor(status, state)` helper
  exported from `lib/nav-status.ts` (new file — same logic as the existing
  `statusColor` inside `nav-map.tsx`, hoisted so both consumers reuse it).
- `(x.xx, y.yy)` — `pose.x` / `pose.y` to 2 decimals. When `pose` is null,
  show `(—, —)` in `text-muted-foreground`.
- When `task` is null too (backend not reachable / no first frame yet),
  show `nav: idle` in `text-muted-foreground`.
- Styling: `font-mono text-xs`, single line, fits next to the existing
  connection chip. Hidden on very narrow screens (`hidden sm:flex`) since
  the NavBar already wraps aggressively on small widths.

### C) Map hover readout

In `frontend/components/nav-map.tsx`:

- Add `hover: { x: number; y: number; px: number; py: number } | null` state.
- `onPointerMove` updates it (call `eventToWorld` and `worldToPx` already
  available). The existing `onPointerMove` body that updates drag state
  stays — the hover state is updated unconditionally above it.
- Add `onPointerLeave` on the `<svg>` that clears the hover state.
- Render at the very end of the SVG (after the goal marker, so it paints
  on top):

  ```jsx
  {hover && !drag && !teleopActive && (
    <g pointerEvents="none">
      <rect x={hover.px + 10} y={hover.py - 22}
            width={...} height={16} rx={3}
            fill="rgba(0,0,0,0.7)" />
      <text x={hover.px + 14} y={hover.py - 10}
            fontSize="11" fontFamily="monospace" fill="white">
        ({hover.x.toFixed(2)}, {hover.y.toFixed(2)})
      </text>
    </g>
  )}
  ```

  Width can be a constant (~78px) since 2-decimal coords fit. The `+10/-22`
  offsets put the pill above-and-right of the cursor, the same pattern RViz
  uses.
- Hide the pill while dragging (drag preview already shows position) and
  while teleop is active (map is non-interactive then).

## Files touched

- **new** `frontend/lib/nav-status.ts` — exports `navStatusColor(status, state)`.
- **new** `frontend/contexts/nav-status.tsx` — `NavStatusProvider` + `useNavStatus`.
- **edit** `frontend/app/layout.tsx` — wrap with provider.
- **edit** `frontend/components/nav-bar.tsx` — consume context, render indicator.
- **edit** `frontend/components/nav-map.tsx` — consume context (drop local SSE),
  add hover state + readout, replace local `statusColor` with the shared helper.

## Testing

Manual verification (no test framework in this repo):

1. `pnpm dev`, open `/`, `/teleop`, `/recon`, `/cameras` — each shows
   `nav: <state>  (x, y)` in the top bar. Numbers update when the `/nav`
   page is open in another tab and the user drags the robot marker (state
   propagates via the shared SSE).
2. With backend down, NavBar shows `nav: idle  (—, —)` in muted color, no
   console errors.
3. On `/nav`, hover anywhere on the map — small dark pill follows the
   cursor and shows world coords matching the grid. Pill disappears on
   pointer-leave, during drag, and when teleop is active.
4. Drag-to-set-pose and drag-to-set-goal still work — hover state must
   not interfere with the existing pointer-event handlers.
5. Open DevTools Network tab → only one `/api/nav/status/stream` connection
   exists when on `/nav` (regression check: previously the NavMap opened
   one; with the lift to context, still exactly one across the whole app).
