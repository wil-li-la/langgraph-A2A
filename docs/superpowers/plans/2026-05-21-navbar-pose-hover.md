# Global nav-status indicator + map hover readout — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface the live nav state and robot `(x, y)` on the top NavBar (visible on every page), and show a small cursor-following `(x, y)` readout when hovering over the `/nav` map.

**Architecture:** Lift the existing `subscribeNavStatus` SSE subscription out of `NavMap` into a new app-level `NavStatusProvider` so a single EventSource feeds both the NavBar indicator and the map. Add a hover-state to `NavMap` and render a small SVG pill at the cursor.

**Tech Stack:** Next.js 14 App Router, React 18, TypeScript, Tailwind. No new dependencies. No backend changes.

**Repo specifics:** No test framework is configured in this repo (per `CLAUDE.md`: "verification is manual/integration only"). Verification is done by running `pnpm lint` (which also runs the TypeScript build via Next.js) and `pnpm dev` and exercising the UI. There are no `*.test.*` files to write.

---

## File map

- **new** `frontend/lib/nav-status.ts` — shared `navStatusColor(status, state)` helper.
- **new** `frontend/contexts/nav-status.tsx` — `NavStatusProvider` + `useNavStatus()`. Owns one SSE.
- **edit** `frontend/app/layout.tsx` — mount the provider.
- **edit** `frontend/components/nav-bar.tsx` — consume `useNavStatus()`, render `nav: <state>  (x.xx, y.yy)`.
- **edit** `frontend/components/nav-map.tsx` — consume `useNavStatus()` (delete local subscription), hoist `statusColor` removal, add hover state + cursor pill.

---

### Task 1: Extract `navStatusColor` helper

**Files:**
- Create: `frontend/lib/nav-status.ts`

- [ ] **Step 1: Create the helper file**

```ts
// frontend/lib/nav-status.ts
import type { NavStatus, NavTask } from "@/lib/nav-api"

/**
 * Tailwind class for coloring a "nav: <state>" label. Mirrors the rule
 * the nav-map StatusBar has used since the page was introduced:
 *   - in-flight (pending/running) → blue
 *   - idle                        → muted
 *   - done + OK                   → green
 *   - done + anything else        → red
 */
export function navStatusColor(
  status: NavStatus | null,
  state: NavTask["state"],
): string {
  if (state === "running" || state === "pending") return "text-blue-500"
  if (state === "idle") return "text-muted-foreground"
  if (status === "OK") return "text-green-500"
  return "text-red-500"
}
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && pnpm lint`
Expected: PASS (no errors introduced).

- [ ] **Step 3: Commit**

```bash
git add frontend/lib/nav-status.ts
git commit -m "refactor(nav): extract navStatusColor helper for reuse"
```

---

### Task 2: Add `NavStatusProvider` context

**Files:**
- Create: `frontend/contexts/nav-status.tsx`

- [ ] **Step 1: Create the provider**

```tsx
// frontend/contexts/nav-status.tsx
"use client"

import { createContext, useContext, useEffect, useState, type ReactNode } from "react"
import {
  subscribeNavStatus,
  type NavPose,
  type NavTask,
} from "@/lib/nav-api"

interface NavStatusContextValue {
  pose: NavPose | null
  task: NavTask | null
  teleopActive: boolean
}

const NavStatusContext = createContext<NavStatusContextValue>({
  pose: null,
  task: null,
  teleopActive: false,
})

/**
 * Single SSE subscriber to /api/nav/status/stream. Mounted at app root so
 * every page (NavBar, NavMap, ...) reads from one stream. Without this,
 * each consuming component would open its own EventSource.
 */
export function NavStatusProvider({ children }: { children: ReactNode }) {
  const [pose, setPose] = useState<NavPose | null>(null)
  const [task, setTask] = useState<NavTask | null>(null)
  const [teleopActive, setTeleopActive] = useState(false)

  useEffect(() => {
    const off = subscribeNavStatus(
      (snap) => {
        setPose(snap.pose)
        setTask(snap.task)
        setTeleopActive(snap.teleop_active)
      },
      () => { /* EventSource auto-reconnects on backend flicker */ },
    )
    return off
  }, [])

  return (
    <NavStatusContext.Provider value={{ pose, task, teleopActive }}>
      {children}
    </NavStatusContext.Provider>
  )
}

export function useNavStatus(): NavStatusContextValue {
  return useContext(NavStatusContext)
}
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && pnpm lint`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend/contexts/nav-status.tsx
git commit -m "feat(nav): add NavStatusProvider, single SSE for app-wide pose"
```

---

### Task 3: Mount the provider in the root layout

**Files:**
- Modify: `frontend/app/layout.tsx`

- [ ] **Step 1: Add the import**

In `frontend/app/layout.tsx`, after the existing context imports (around line 6), add:

```tsx
import { NavStatusProvider } from '@/contexts/nav-status'
```

- [ ] **Step 2: Wrap the children**

Replace the existing provider stack body (currently lines 27-35) so it reads:

```tsx
<UIModeProvider>
  <RobotConnectionProvider>
    <NavStatusProvider>
      <WorkflowProvider>
        <AgentProvider>
          {children}
        </AgentProvider>
      </WorkflowProvider>
    </NavStatusProvider>
  </RobotConnectionProvider>
</UIModeProvider>
```

`NavStatusProvider` is placed inside `RobotConnectionProvider` for consistency (same "talks to backend" layer) but it does not depend on robot-connection state.

- [ ] **Step 3: Verify the app still boots**

Run: `cd frontend && pnpm dev` in one terminal. In a browser open `http://localhost:3000/`. Confirm the page renders with no console errors. Stop the dev server.

- [ ] **Step 4: Commit**

```bash
git add frontend/app/layout.tsx
git commit -m "feat(nav): mount NavStatusProvider in root layout"
```

---

### Task 4: NavBar — render `nav: <state>  (x, y)` indicator

**Files:**
- Modify: `frontend/components/nav-bar.tsx`

- [ ] **Step 1: Add imports**

In `frontend/components/nav-bar.tsx`, after the existing imports (top of file), add:

```tsx
import { useNavStatus } from "@/contexts/nav-status"
import { navStatusColor } from "@/lib/nav-status"
```

- [ ] **Step 2: Read from the context**

Inside `NavBar` (just after `const { robotHost, ... } = useRobotConnection();`), add:

```tsx
const { pose, task } = useNavStatus()
const navState = task?.state ?? "idle"
const navLabel =
  navState === "done" ? (task?.status ?? "?") : navState
const navColor = navStatusColor(task?.status ?? null, navState)
```

- [ ] **Step 3: Render the indicator**

Insert a new block immediately after the robot-connection group closes (after the existing `</div>` on what is currently line 85, before the `{/* Mode toggle ... */}` comment block). Add:

```tsx
{/* Live nav-status + pose indicator. Single SSE, shared across pages. */}
<div className="hidden items-center gap-2 font-mono text-xs sm:flex">
  <span className="text-muted-foreground">nav:</span>
  <span className={navColor}>{navLabel}</span>
  {pose ? (
    <span className="text-foreground">
      ({pose.x.toFixed(2)}, {pose.y.toFixed(2)})
    </span>
  ) : (
    <span className="text-muted-foreground">(—, —)</span>
  )}
</div>
```

- [ ] **Step 4: Type-check + visual verify**

Run: `cd frontend && pnpm lint`. Expected: PASS.

Then `pnpm dev`, open `http://localhost:3000/`, confirm the NavBar shows `nav: idle (—, —)` (muted) when the backend has no pose yet. Visit `/nav`, drag the robot marker — the NavBar indicator should update to show the new `(x.xx, y.yy)` live. Visit `/`, `/teleop`, `/recon`, `/cameras` — each shows the same indicator.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/nav-bar.tsx
git commit -m "feat(nav-bar): show live nav state + robot pose on every page"
```

---

### Task 5: NavMap — consume the shared context, drop local SSE

**Files:**
- Modify: `frontend/components/nav-map.tsx`

- [ ] **Step 1: Add the context import, drop the local helper**

In `frontend/components/nav-map.tsx`:

- Add to the imports at the top:

  ```tsx
  import { useNavStatus } from "@/contexts/nav-status"
  import { navStatusColor } from "@/lib/nav-status"
  ```

- Delete the local `statusColor` function (currently lines 52-57):

  ```ts
  function statusColor(status: NavStatus | null, state: NavTask["state"]): string {
    if (state === "running" || state === "pending") return "text-blue-500"
    if (state === "idle") return "text-muted-foreground"
    if (status === "OK") return "text-green-500"
    return "text-red-500"
  }
  ```

  (Now provided by `navStatusColor` from `@/lib/nav-status`.)

- [ ] **Step 2: Replace the local pose/task/teleop state and its SSE subscription**

Inside `NavMap`, currently lines 67-72 declare:

```tsx
const [meta, setMeta] = useState<NavMapMetadata | null>(null)
const [metaError, setMetaError] = useState<string | null>(null)
const [pose, setPose] = useState<NavPose | null>(null)
const [task, setTask] = useState<NavTask | null>(null)
const [teleopActive, setTeleopActive] = useState(false)
const [drag, setDrag] = useState<DragState | null>(null)
```

Remove the `pose`, `task`, `teleopActive` `useState` lines and read them from the context instead. The block should become:

```tsx
const [meta, setMeta] = useState<NavMapMetadata | null>(null)
const [metaError, setMetaError] = useState<string | null>(null)
const { pose, task, teleopActive } = useNavStatus()
const [drag, setDrag] = useState<DragState | null>(null)
```

Then in the bootstrap `useEffect` (currently lines 102-113), strip out the SSE subscription so only map-metadata fetch remains:

```tsx
useEffect(() => {
  fetchNavMap().then(setMeta).catch((e) => setMetaError(String(e)))
}, [])
```

- [ ] **Step 3: Fix the one remaining `statusColor` call site**

Currently line 485 reads:

```tsx
<span className={statusColor(task.status, task.state)}>
```

Change it to:

```tsx
<span className={navStatusColor(task.status, task.state)}>
```

- [ ] **Step 4: Drop the now-unused imports**

`subscribeNavStatus` is no longer used in this file. Remove it from the `@/lib/nav-api` import (currently lines 4-13). The remaining named imports are:

```tsx
import {
  fetchNavMap,
  postNavGoto,
  setNavPose,
  type NavMapMetadata,
  type NavPose,
  type NavStatus,
  type NavTask,
} from "@/lib/nav-api"
```

(`NavStatus` is kept — it's still used by other type references in the file. `NavPose`, `NavTask` likewise remain since they appear in the `PoseSourceBadge` and `StatusBar` prop types.)

- [ ] **Step 5: Type-check + visual verify**

Run: `cd frontend && pnpm lint`. Expected: PASS.

Then `pnpm dev`, open `/nav`. The page should look and behave exactly as before:
- pose badge (`MANUAL`/`LIVE`/`POST-NAV`) renders.
- dragging the robot marker still updates the pose via `setNavPose`.
- dragging empty space still submits a goal via `postNavGoto`.
- `nav: idle / running / OK / ...` color matches the previous behavior.

In DevTools → Network → EventStream, confirm exactly one `/api/nav/status/stream` connection is open while on `/nav` (and also when navigating to any other page — same single connection, lifted to the layout).

- [ ] **Step 6: Commit**

```bash
git add frontend/components/nav-map.tsx
git commit -m "refactor(nav-map): consume shared NavStatusProvider, drop local SSE"
```

---

### Task 6: NavMap — hover-coord readout

**Files:**
- Modify: `frontend/components/nav-map.tsx`

- [ ] **Step 1: Add hover state**

Below the existing `const [drag, setDrag] = useState<DragState | null>(null)` line, add:

```tsx
const [hover, setHover] = useState<{
  x: number
  y: number
  px: number
  py: number
} | null>(null)
```

- [ ] **Step 2: Update hover state in `onPointerMove`**

Replace the current `onPointerMove` body (currently lines 179-184):

```tsx
const onPointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
  if (!drag) return
  const w = eventToWorld(e)
  if (!w) return
  setDrag({ ...drag, currentWorld: w })
}
```

with:

```tsx
const onPointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
  const w = eventToWorld(e)
  if (!w) return
  if (!meta) return
  const { px, py } = worldToPx(w.x, w.y)
  setHover({ x: w.x, y: w.y, px, py })
  if (drag) {
    setDrag({ ...drag, currentWorld: w })
  }
}
```

- [ ] **Step 3: Add `onPointerLeave` to clear hover**

On the `<svg>` element (currently lines 282-291), add a new handler alongside the existing pointer handlers:

```tsx
onPointerLeave={() => setHover(null)}
```

- [ ] **Step 4: Render the cursor pill**

Inside the `<svg>`, immediately before the closing `</svg>` (after the goal-marker block, currently around line 390), add:

```tsx
{/* Cursor coordinate readout. Hidden while dragging (drag preview
    already shows position) and while teleop owns the map. */}
{hover && !drag && !teleopActive && (
  <g pointerEvents="none">
    <rect
      x={hover.px + 10}
      y={hover.py - 22}
      width={78}
      height={16}
      rx={3}
      fill="rgba(0, 0, 0, 0.7)"
    />
    <text
      x={hover.px + 14}
      y={hover.py - 10}
      fontSize="11"
      fontFamily="monospace"
      fill="white"
    >
      ({hover.x.toFixed(2)}, {hover.y.toFixed(2)})
    </text>
  </g>
)}
```

- [ ] **Step 5: Type-check + visual verify**

Run: `cd frontend && pnpm lint`. Expected: PASS.

Then `pnpm dev`, open `/nav`. Verification checklist:
- Hovering over the map → small dark pill appears next to the cursor, showing `(x.xx, y.yy)` matching the visible grid position.
- Moving cursor off the SVG → pill disappears.
- Holding the mouse down to drag the robot or set a goal → pill disappears for the duration of the drag, and the existing drag preview is unaffected.
- Existing drag-to-set-pose and drag-to-set-goal still work end-to-end (pose updates / nav goal POST fires).
- With teleop active (Connect button on stretch in the nav bar), the map is faded and the hover pill does not render.

- [ ] **Step 6: Commit**

```bash
git add frontend/components/nav-map.tsx
git commit -m "feat(nav-map): show world coords next to cursor on hover"
```

---

## Self-review

- **Spec coverage:**
  - Goal 1 (NavBar shows nav state + pose on every page) → Tasks 1-4.
  - Goal 2 (hover readout on map) → Task 6.
  - Non-goal "no backend changes" → respected (no files outside `frontend/` touched).
  - Single SSE invariant → Task 5 deletes the local subscription; Task 2 owns it.
- **Placeholder scan:** no TBDs, no "implement appropriately" — every step shows the exact code to write or delete.
- **Type consistency:** `useNavStatus()` returns `{ pose, task, teleopActive }` in Task 2 and is consumed with the same destructure in Tasks 4 and 5. `navStatusColor(status, state)` defined in Task 1 is called with the same arg order in Tasks 4 and 5. `hover` state shape `{x, y, px, py}` is defined in Task 6 Step 1 and used unchanged in Step 4.
- **Repo conventions:** No tests written — repo has no test framework (confirmed via `CLAUDE.md` and the absence of `*.test.*` / `*.spec.*` files). Verification is `pnpm lint` + manual exercise in `pnpm dev`, matching the existing workflow.
