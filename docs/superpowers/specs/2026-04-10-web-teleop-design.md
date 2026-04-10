# Web Teleop Integration — Design Spec

**Date:** 2026-04-10
**Status:** Approved

## Overview

Add a manual web teleoperation page (`/teleop`) to the existing Next.js frontend. The backend acts as a transparent WebSocket relay between the browser and the physical robot. The teleop feature is fully isolated from the existing workflow dashboard — no shared state, no shared hooks.

## Decisions

| # | Question | Decision |
|---|----------|----------|
| 1 | Where does teleop live? | New route `/teleop` |
| 2 | Which controls? | All (drive, head, joints, gripper, runstop, home, speed, nav map, TTS) |
| 3 | Camera handling? | Separate camera section, same as the template's own views |
| 4 | Connection model? | Backend WebSocket proxy (full relay) |
| 5 | Relationship to workflow? | Completely separate |
| 6 | Design system? | Redesign to match existing terminal monochrome style |
| 7 | Robot address config? | Frontend sends address on connect, backend uses it dynamically |
| 8 | Page navigation? | Shared nav bar with Dashboard/Teleop buttons |

## Frontend

### Navigation Bar

New shared component rendered in root `layout.tsx`:

```
┌──────────────────────────────────────────────────┐
│  Robot Task Dashboard           [Dashboard] [Teleop] │
└──────────────────────────────────────────────────┘
```

- **File:** `components/nav-bar.tsx`
- Two buttons: Dashboard (`/`) and Teleop (`/teleop`), active state highlighted
- Terminal monochrome style, uses Next.js `<Link>` for client-side navigation

### Teleop Page Layout

**Route:** `/teleop` (`app/teleop/page.tsx`)

```
┌─ Nav Bar ─────────────────────────────────────────────────┐
│  Robot Task Dashboard                [Dashboard] [Teleop] │
├─ Status Bar ──────────────────────────────────────────────┤
│ [Robot WS Address] [Connect]  ● Connected  🔋 12.8V  ⌂ Homed │
├───────────────────────────────────┬───────────────────────┤
│                                   │  RUNSTOP (large)      │
│  Camera Panel (tabs)              ├───────────────────────┤
│  ┌─────────────────────────────┐  │  Speed Scale          │
│  │  Overhead / Gripper / Map   │  │  [.25][.5][1][1.5][2] │
│  │                             │  ├───────────────────────┤
│  │                             │  │  Drive Pad  │ Head    │
│  │                             │  │  (WASD)     │ (↑←↓→)  │
│  └─────────────────────────────┘  ├───────────────────────┤
│                                   │  Joint Controls       │
│  TTS Input [text...] [Speak]      │  (Lift, Arm, Wrist)   │
│  Chat Log                         ├───────────────────────┤
│                                   │  Gripper  │ Home      │
└───────────────────────────────────┴───────────────────────┘
```

Left column (~60%): camera panel, TTS input, chat log.
Right column (~360px fixed): all control panels stacked vertically.

### New Files

**Components (`components/teleop/`):**

| File | Purpose |
|------|---------|
| `teleop-page.tsx` | Page orchestrator. Owns `robotHost`, `speedScale`, `chatEntries` state. Wires `useTeleop` hook to all child components. |
| `status-bar.tsx` | Robot WS address text input + Connect button. Displays connection, battery, homing, runstop status as badges. |
| `camera-panel.tsx` | Tabbed interface: Overhead, Gripper, Map. Uses shadcn Tabs. |
| `camera-view.tsx` | Canvas-based JPEG renderer. Accepts blob URL + rotation degrees. Shows "No signal" placeholder when null. |
| `nav-map.tsx` | 2D map with click-to-goal. Draws robot pose (circle + heading arrow), planned path (line), nav state badge. Cancel button during navigation. |
| `drive-pad.tsx` | 5-button grid (forward, left, stop, right, back). WASD + arrow keyboard support. Press-and-hold with 100ms repeat. Speed scale applied. |
| `head-controls.tsx` | 4-direction pan/tilt buttons + Forward/Gripper preset buttons. 200ms repeat. |
| `joint-controls.tsx` | +/- buttons for lift, arm extension, wrist yaw/pitch/roll. Real-time position readout (3 decimals). 200ms repeat. |
| `gripper-buttons.tsx` | Open / Close buttons. |
| `runstop-button.tsx` | Large toggle button. Red when active, muted when released. |
| `home-button.tsx` | "Home Robot" / "Re-Home Robot" based on `is_homed` status. |
| `speed-scale.tsx` | 5 preset buttons (0.25x, 0.5x, 1x, 1.5x, 2x). Active state highlighted. |
| `tts-input.tsx` | Text input + Speak button. Adds entry to chat log on send. |
| `chat-log.tsx` | Scrollable message history with timestamps and icons. |

**Hooks (`hooks/`):**

| File | Purpose |
|------|---------|
| `use-teleop.ts` | WebSocket connection to backend `/ws/teleop?robot=<address>`. Manages connection lifecycle with auto-reconnect (2s). Parses JSON status messages. Routes binary frames to camera state (overhead/realsense/gripper blob URLs with revocation). Exposes `{ status, cameras, isConnected, sendCommand, connect, disconnect }`. |

**Page (`app/teleop/`):**

| File | Purpose |
|------|---------|
| `page.tsx` | Client component. Renders `<TeleopPage />`. |

### Design System Adaptation

All teleop components follow the existing terminal monochrome style:

- Dark background, `border border-border`, `rounded-md`
- Monospace font for data readouts (joint positions, battery voltage)
- Opacity layers for visual hierarchy — no accent colors except red for runstop/errors
- No shadows, gradients, or transitions (pulse/ping for live indicators only)
- Button sizes: minimum `h-11` (44px) for touch targets, `h-14` (56px) for drive/head pads

### Keyboard Controls

- `W/A/S/D` or arrow keys for base driving
- Focus guard: keyboard drive disabled when any text input is focused (TTS input, robot address input)
- Press-and-hold pattern on all motion buttons (pointer down starts repeat interval, pointer up/leave stops)

## Backend

### New File: `teleop_api.py`

Single WebSocket endpoint that relays all messages between browser and robot transparently.

**Endpoint:** `GET /ws/teleop?robot=<ws-address>`

**Behavior:**
1. Browser connects to backend WebSocket, passing `robot` query param (e.g., `ws://stretch-se3-3099.local:8765`)
2. Backend opens a WebSocket to the specified robot address
3. Two async relay tasks run concurrently:
   - Browser → Robot: forward all messages (text and binary) as-is
   - Robot → Browser: forward all messages (text and binary) as-is
4. When either side disconnects, both connections are closed and tasks are cancelled

**No message transformation.** The backend is a dumb pipe. The protocol between browser and robot is unchanged from the template.

**Error handling:**
- If robot connection fails: send JSON `{ "type": "error", "message": "..." }` to browser, close
- If robot disconnects unexpectedly: close browser connection
- If browser disconnects: close robot connection

### Changes to `__main__.py`

Mount the WebSocket route on the Starlette app alongside existing REST routes:

```python
from app.teleop_api import teleop_websocket

# Add WebSocket route
routes.append(WebSocketRoute("/ws/teleop", teleop_websocket))
```

### Dependencies

Backend needs a WebSocket client library. Options:
- `websockets` — already commonly used, async-native, supports binary frames
- Add to `pyproject.toml` dependencies

## Protocol Reference

Unchanged from the template. Reproduced here for completeness.

### Commands (Browser → Robot)

```json
{ "type": "drive", "linear": float, "angular": float }
{ "type": "increment_joint", "name": string, "increment": float }
{ "type": "set_pose", "pose": { joint_name: float, ... } }
{ "type": "stop" }
{ "type": "set_runstop", "enabled": bool }
{ "type": "home" }
{ "type": "tts", "text": string }
{ "type": "look_at", "camera": "overhead"|"forward"|"gripper" }
{ "type": "nav_goal", "x": float, "y": float, "theta": float }
{ "type": "cancel_nav" }
```

### Status (Robot → Browser, 15Hz)

```json
{
  "type": "status",
  "joints": { "joint_lift": float, "wrist_extension": float, ... },
  "battery": { "voltage": float, "is_charging": bool, "is_low_voltage": bool },
  "runstop": bool,
  "is_homed": bool,
  "nav_state": "idle"|"navigating"|"succeeded"|"failed",
  "robot_pose": { "x": float, "y": float, "theta": float } | null,
  "nav_path": [{ "x": float, "y": float }, ...]
}
```

### Camera Frames (Robot → Browser, binary)

```
[1 byte: camera_id][JPEG payload]
camera_id: 0=overhead, 1=realsense, 2=gripper
```

## Isolation Guarantees

- Zero imports between teleop components and workflow dashboard components
- No shared hooks (workflow uses `use-workflow.ts`, teleop uses `use-teleop.ts`)
- No shared state or context providers
- Only shared resources: shadcn/ui component library, Tailwind classes, `lib/utils.ts`
- Nav bar is the only component rendered on both pages (via root layout)
- Can be extracted to a standalone app by copying `components/teleop/`, `hooks/use-teleop.ts`, `app/teleop/`, and the shared UI library

## Out of Scope

- Authentication / access control on the WebSocket endpoint
- Multi-user teleop (only one browser controls the robot at a time)
- Recording / playback of teleop sessions
- Integration between teleop commands and workflow execution
- Map image serving from backend (uses static `/public/maps/` for now)
