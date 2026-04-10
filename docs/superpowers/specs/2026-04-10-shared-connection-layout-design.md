# Shared Robot Connection + Dashboard Layout Refactor — Design Spec

**Date:** 2026-04-10
**Status:** Approved

## Overview

Move the robot IP input into the shared nav bar via a React context so the user only enters it once per session. Remove the Connect Robot panel from the dashboard. Redesign the dashboard as a two-column layout with the graph on the left and controls on the right. Robot type is hardcoded to Stretch 3.

## Decisions

| # | Question | Decision |
|---|----------|----------|
| 1 | Where does the robot IP input live? | Nav bar (shared across pages) |
| 2 | Dashboard layout? | Layout A — two-column, graph dominant |
| 3 | Robot type selector? | Hardcoded to Stretch 3 |

## New File: `contexts/robot-connection.tsx`

Client component providing `RobotConnectionContext` with:

- `robotHost: string` — user-entered IP/address
- `setRobotHost: (host: string) => void`
- `isConnected: boolean`
- `status: RobotStatus` — joint positions, battery, runstop, nav state
- `cameras: Record<CameraName, string | null>` — blob URLs for camera frames
- `sendCommand: (cmd: RobotCommand) => void`
- `connect: (host: string) => void`
- `disconnect: () => void`

The provider calls `useTeleop()` internally. Both pages consume the context instead of managing their own WebSocket connection.

## Modified: `components/nav-bar.tsx`

```
┌────────────────────────────────────────────────────────────────────────┐
│  Robot Task Dashboard   [robot-ip] [Connect] ● CONNECTED Stretch 3   [Dashboard] [Teleop] │
└────────────────────────────────────────────────────────────────────────┘
```

- Consumes `RobotConnectionContext` for `robotHost`, `isConnected`, `connect`, `disconnect`
- Compact input + connect/disconnect button
- Connection status dot + "Stretch 3" label shown when connected
- Terminal monochrome style

## Modified: `app/layout.tsx`

Wraps `{children}` with `<RobotConnectionProvider>`. The provider is a `"use client"` component imported into the server layout.

## Modified: `components/robot-dashboard.tsx`

New two-column layout:

```
┌─ Nav Bar ─────────────────────────────────────────────────────┐
├───────────────────────────────────┬───────────────────────────┤
│                                   │  Required Skills          │
│  TASK — LANGGRAPH                 │  (compact)                │
│  (graph, fills left column)       ├───────────────────────────┤
│                                   │  Operation Mode           │
│                                   │  [instruction] [RUN]      │
│                                   ├───────────────────────────┤
│                                   │  Execution Log            │
├───────────────────────────────────┤  (scrollable)             │
│  VIDEO STREAMING                  │                           │
│  (Gripper + Head side by side)    │                           │
└───────────────────────────────────┴───────────────────────────┘
```

- Remove `ConnectPanel` import and usage
- Remove `selectedRobot` state — hardcode `"stretch3"` for mock data
- Left column: graph (`flex-1`) + video (`shrink-0`)
- Right column: skills + mode toggle + pause guide + execution log (stacked, log fills remaining)
- `h-dvh overflow-hidden` — fits 11" iPad without scrolling
- No `max-w` constraint — use full viewport

## Modified: `components/teleop/teleop-page.tsx`

- Remove the connection header (robot IP input, connect/disconnect button, status bar)
- Consume `RobotConnectionContext` instead of calling `useTeleop()` directly
- The teleop status bar still renders inside the teleop main area, reading from context

## Deleted: `components/connect-panel.tsx`

No longer needed.

## Modified: `lib/mock-data.ts`

- `selectedRobot` is no longer user-selectable; dashboard hardcodes `"stretch3"`
- The `RobotId` type and `robots` map remain for potential future use

## What doesn't change

- `hooks/use-teleop.ts` — same hook, now called inside the context provider
- Backend — untouched
- All teleop control components — same props
- `hooks/use-workflow.ts` — unrelated to robot WS connection
- Workflow graph, pause/resume — untouched

## Out of Scope

- Robot type auto-detection from WebSocket status
- Multi-robot support
- Persisting robot IP across browser sessions (localStorage)
