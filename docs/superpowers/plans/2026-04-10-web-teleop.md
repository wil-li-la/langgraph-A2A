# Web Teleop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/teleop` page to the Next.js frontend with full robot teleoperation controls, plus a backend WebSocket relay endpoint that proxies commands between the browser and the robot.

**Architecture:** The frontend gets a new isolated page with drive/head/joint/gripper controls, camera views, and nav map — all communicating via a single WebSocket through the backend. The backend adds a transparent WebSocket relay (`/ws/teleop`) that connects to the robot's WebSocket server. A shared nav bar is added to both pages.

**Tech Stack:** Next.js 16 (App Router), React 19, Tailwind CSS, shadcn/ui, Starlette WebSocket, `websockets` Python library

---

### Task 1: Backend WebSocket Relay

**Files:**
- Create: `backend/app/teleop_api.py`
- Modify: `backend/app/__main__.py:148-160`
- Modify: `backend/pyproject.toml:7-19`

- [ ] **Step 1: Add `websockets` to pyproject.toml**

In `backend/pyproject.toml`, add `websockets` to the dependencies list:

```toml
dependencies = [
    "click>=8.1.8",
    "httpx>=0.28.1",
    "langchain-google-genai>=2.0.10",
    "langgraph>=0.3.18",
    "langchain-openai>=0.1.0",
    "pydantic>=2.10.6",
    "python-dotenv>=1.1.0",
    "uvicorn>=0.34.2",
    "sse-starlette>=2.3.6",
    "starlette>=0.46.2",
    "a2a-sdk>=0.3.0",
    "websockets>=12.0",
]
```

- [ ] **Step 2: Create `backend/app/teleop_api.py`**

```python
"""WebSocket relay for teleop: browser <-> backend <-> robot."""

import asyncio
import logging

import websockets
from starlette.websockets import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


async def teleop_websocket(ws: WebSocket):
    """Transparent WebSocket relay between browser and robot.

    The browser connects to /ws/teleop?robot=ws://robot-ip:8765.
    This endpoint opens a WebSocket to the robot and relays all
    messages (text + binary) bidirectionally.
    """
    robot_url = ws.query_params.get("robot")
    if not robot_url:
        await ws.close(code=1008, reason="Missing ?robot= query parameter")
        return

    await ws.accept()
    logger.info(f"Teleop: browser connected, relaying to {robot_url}")

    try:
        async with websockets.connect(robot_url) as robot_ws:
            async def browser_to_robot():
                try:
                    while True:
                        msg = await ws.receive()
                        if "text" in msg:
                            await robot_ws.send(msg["text"])
                        elif "bytes" in msg:
                            await robot_ws.send(msg["bytes"])
                except WebSocketDisconnect:
                    pass

            async def robot_to_browser():
                try:
                    async for msg in robot_ws:
                        if isinstance(msg, str):
                            await ws.send_text(msg)
                        elif isinstance(msg, bytes):
                            await ws.send_bytes(msg)
                except websockets.exceptions.ConnectionClosed:
                    pass

            # Run both relay directions concurrently; when either
            # side disconnects the other task gets cancelled.
            done, pending = await asyncio.wait(
                [
                    asyncio.create_task(browser_to_robot()),
                    asyncio.create_task(robot_to_browser()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()

    except Exception as exc:
        logger.warning(f"Teleop: failed to connect to robot at {robot_url}: {exc}")
        try:
            await ws.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass
    finally:
        try:
            await ws.close()
        except Exception:
            pass
        logger.info("Teleop: session closed")
```

- [ ] **Step 3: Mount the WebSocket route in `__main__.py`**

Add the import at the top (after the `workflow_api` import around line 36):

```python
from app.teleop_api import teleop_websocket
```

Add the WebSocket route mount. After line 158 (`for route in workflow_routes:`/ `starlette_app.routes.insert(0, route)`), add:

```python
        # Mount teleop WebSocket relay
        from starlette.routing import WebSocketRoute
        starlette_app.routes.insert(0, WebSocketRoute("/ws/teleop", teleop_websocket))
```

Add a log line after the existing log lines (after line 177):

```python
        logger.info(f'Teleop WebSocket relay: {public_url}/ws/teleop')
```

- [ ] **Step 4: Verify backend starts**

Run: `cd backend && source .venv/bin/activate && python -m app --host localhost --port 9999`

Expected: Server starts, logs show `Teleop WebSocket relay: http://localhost:9999/ws/teleop`

Stop the server after verifying.

- [ ] **Step 5: Commit**

```bash
git add backend/app/teleop_api.py backend/app/__main__.py backend/pyproject.toml
git commit -m "feat(backend): add WebSocket teleop relay endpoint"
```

---

### Task 2: Frontend Types and Protocol

**Files:**
- Create: `frontend/types/robot.ts`
- Create: `frontend/lib/teleop-protocol.ts`

- [ ] **Step 1: Create `frontend/types/robot.ts`**

```typescript
export interface JointPositions {
  joint_lift: number;
  wrist_extension: number;
  joint_head_pan: number;
  joint_head_tilt: number;
  joint_wrist_yaw: number;
  joint_wrist_pitch: number;
  joint_wrist_roll: number;
  joint_gripper_finger_left: number;
  translate_mobile_base: number;
  rotate_mobile_base: number;
}

export interface BatteryState {
  voltage: number;
  is_charging: boolean;
  is_low_voltage: boolean;
}

export interface RobotStatus {
  joints: JointPositions;
  battery: BatteryState;
  runstop: boolean;
  is_homed: boolean;
  nav_state: NavState;
  robot_pose: RobotPose | null;
  nav_path: NavPathPoint[];
}

export type JointName = keyof JointPositions;

export const JOINT_INCREMENTS: Partial<Record<JointName, number>> = {
  joint_lift: 0.05,
  wrist_extension: 0.05,
  joint_head_pan: 0.1,
  joint_head_tilt: 0.1,
  joint_wrist_yaw: 0.2,
  joint_wrist_pitch: 0.2,
  joint_wrist_roll: 0.2,
  joint_gripper_finger_left: 3,
  translate_mobile_base: 0.1,
  rotate_mobile_base: 0.2,
};

export const JOINT_LABELS: Partial<Record<JointName, string>> = {
  joint_lift: "Lift",
  wrist_extension: "Arm",
  joint_head_pan: "Head Pan",
  joint_head_tilt: "Head Tilt",
  joint_wrist_yaw: "Wrist Yaw",
  joint_wrist_pitch: "Wrist Pitch",
  joint_wrist_roll: "Wrist Roll",
  joint_gripper_finger_left: "Gripper",
};

export const CAMERA_NAMES = ["overhead", "realsense", "gripper"] as const;
export type CameraName = (typeof CAMERA_NAMES)[number];

export type NavState = "idle" | "navigating" | "succeeded" | "failed";

export interface RobotPose {
  x: number;
  y: number;
  theta: number;
}

export interface NavPathPoint {
  x: number;
  y: number;
}

export const MAP_CONFIG = {
  imageUrl: "/maps/305_map.png",
  resolution: 0.05,
  originX: -7.99,
  originY: -3.23,
  width: 250,
  height: 183,
} as const;
```

- [ ] **Step 2: Create `frontend/lib/teleop-protocol.ts`**

```typescript
import type { JointName, RobotStatus } from "@/types/robot";

// --- Commands (browser -> robot) ---

export type RobotCommand =
  | { type: "drive"; linear: number; angular: number }
  | { type: "increment_joint"; name: JointName; increment: number }
  | { type: "set_pose"; pose: Partial<Record<JointName, number>> }
  | { type: "stop" }
  | { type: "set_runstop"; enabled: boolean }
  | { type: "home" }
  | { type: "tts"; text: string }
  | { type: "look_at"; camera: string }
  | { type: "nav_goal"; x: number; y: number; theta: number }
  | { type: "cancel_nav" };

// --- Camera binary protocol ---
// Binary messages: [1-byte camera_id][JPEG bytes]
export const CAMERA_ID_OVERHEAD = 0;
export const CAMERA_ID_REALSENSE = 1;
export const CAMERA_ID_GRIPPER = 2;

export function parseStatusMessage(data: string): RobotStatus | null {
  try {
    const msg = JSON.parse(data);
    if (msg.type === "status") {
      return {
        joints: msg.joints,
        battery: msg.battery,
        runstop: msg.runstop,
        is_homed: msg.is_homed,
        nav_state: msg.nav_state ?? "idle",
        robot_pose: msg.robot_pose ?? null,
        nav_path: msg.nav_path ?? [],
      };
    }
  } catch {
    // ignore malformed messages
  }
  return null;
}

export function parseCameraFrame(data: ArrayBuffer): {
  cameraId: number;
  jpeg: Blob;
} | null {
  if (data.byteLength < 2) return null;
  const view = new Uint8Array(data);
  const cameraId = view[0];
  const jpeg = new Blob([view.slice(1)], { type: "image/jpeg" });
  return { cameraId, jpeg };
}
```

- [ ] **Step 3: Copy map image to frontend public dir**

```bash
mkdir -p frontend/public/maps
cp /tmp/stretch-teleop-ui/public/maps/305_map.png frontend/public/maps/
```

- [ ] **Step 4: Commit**

```bash
git add frontend/types/robot.ts frontend/lib/teleop-protocol.ts frontend/public/maps/305_map.png
git commit -m "feat(frontend): add teleop types, protocol, and map asset"
```

---

### Task 3: Teleop WebSocket Hook

**Files:**
- Create: `frontend/hooks/use-teleop.ts`

- [ ] **Step 1: Create `frontend/hooks/use-teleop.ts`**

This hook manages the WebSocket connection to the backend relay, parses status messages, and routes binary camera frames to blob URLs.

```typescript
"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { RobotStatus, CameraName } from "@/types/robot";
import {
  parseStatusMessage,
  parseCameraFrame,
  CAMERA_ID_OVERHEAD,
  CAMERA_ID_REALSENSE,
  CAMERA_ID_GRIPPER,
  type RobotCommand,
} from "@/lib/teleop-protocol";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:9999";

const DEFAULT_STATUS: RobotStatus = {
  joints: {
    joint_lift: 0,
    wrist_extension: 0,
    joint_head_pan: 0,
    joint_head_tilt: 0,
    joint_wrist_yaw: 0,
    joint_wrist_pitch: 0,
    joint_wrist_roll: 0,
    joint_gripper_finger_left: 0,
    translate_mobile_base: 0,
    rotate_mobile_base: 0,
  },
  battery: { voltage: 0, is_charging: false, is_low_voltage: false },
  runstop: false,
  is_homed: false,
  nav_state: "idle",
  robot_pose: null,
  nav_path: [],
};

type CameraFrames = Record<CameraName, string | null>;

export function useTeleop() {
  const [status, setStatus] = useState<RobotStatus>(DEFAULT_STATUS);
  const [isConnected, setIsConnected] = useState(false);
  const [cameras, setCameras] = useState<CameraFrames>({
    overhead: null,
    realsense: null,
    gripper: null,
  });
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined
  );
  const currentUrlRef = useRef<string | null>(null);

  const cleanup = useCallback(() => {
    if (reconnectTimer.current) {
      clearTimeout(reconnectTimer.current);
      reconnectTimer.current = undefined;
    }
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    setIsConnected(false);
  }, []);

  const connect = useCallback(
    (robotAddress: string) => {
      cleanup();

      // Build the backend relay URL
      const wsBase = API_BASE.replace(/^http/, "ws");
      const wsUrl = `${wsBase}/ws/teleop?robot=${encodeURIComponent(robotAddress)}`;
      currentUrlRef.current = robotAddress;

      const ws = new WebSocket(wsUrl);
      ws.binaryType = "arraybuffer";

      ws.onopen = () => {
        setIsConnected(true);
        if (reconnectTimer.current) {
          clearTimeout(reconnectTimer.current);
          reconnectTimer.current = undefined;
        }
      };

      ws.onmessage = (event) => {
        if (typeof event.data === "string") {
          const parsed = parseStatusMessage(event.data);
          if (parsed) setStatus(parsed);
        } else if (event.data instanceof ArrayBuffer) {
          const frame = parseCameraFrame(event.data);
          if (!frame) return;

          let name: CameraName;
          switch (frame.cameraId) {
            case CAMERA_ID_OVERHEAD:
              name = "overhead";
              break;
            case CAMERA_ID_REALSENSE:
              name = "realsense";
              break;
            case CAMERA_ID_GRIPPER:
              name = "gripper";
              break;
            default:
              return;
          }

          const url = URL.createObjectURL(frame.jpeg);
          setCameras((prev) => {
            const oldUrl = prev[name];
            if (oldUrl) URL.revokeObjectURL(oldUrl);
            return { ...prev, [name]: url };
          });
        }
      };

      ws.onclose = () => {
        setIsConnected(false);
        wsRef.current = null;
        // Auto-reconnect after 2s
        if (currentUrlRef.current) {
          reconnectTimer.current = setTimeout(
            () => connect(currentUrlRef.current!),
            2000
          );
        }
      };

      ws.onerror = () => {
        ws.close();
      };

      wsRef.current = ws;
    },
    [cleanup]
  );

  const disconnect = useCallback(() => {
    currentUrlRef.current = null;
    cleanup();
  }, [cleanup]);

  const sendCommand = useCallback((cmd: RobotCommand) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(cmd));
    }
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      currentUrlRef.current = null;
      cleanup();
    };
  }, [cleanup]);

  return { status, cameras, isConnected, sendCommand, connect, disconnect };
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/hooks/use-teleop.ts
git commit -m "feat(frontend): add useTeleop WebSocket hook"
```

---

### Task 4: Navigation Bar

**Files:**
- Create: `frontend/components/nav-bar.tsx`
- Modify: `frontend/app/layout.tsx`
- Modify: `frontend/components/robot-dashboard.tsx`

- [ ] **Step 1: Create `frontend/components/nav-bar.tsx`**

```tsx
"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"

const NAV_ITEMS = [
  { href: "/", label: "Dashboard" },
  { href: "/teleop", label: "Teleop" },
] as const;

export function NavBar() {
  const pathname = usePathname();

  return (
    <header className="border-b border-border bg-background px-4 py-2">
      <div className="mx-auto flex max-w-[1400px] items-center justify-between">
        <h1 className="font-mono text-sm font-medium tracking-tight text-foreground">
          Robot Task Dashboard
        </h1>
        <nav className="flex gap-1">
          {NAV_ITEMS.map((item) => {
            const isActive = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`rounded-md px-3 py-1 font-mono text-xs transition-colors ${
                  isActive
                    ? "bg-foreground/10 text-foreground"
                    : "text-muted-foreground hover:bg-foreground/5 hover:text-foreground"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
      </div>
    </header>
  );
}
```

- [ ] **Step 2: Update `frontend/app/layout.tsx` to include NavBar**

Replace the entire layout file:

```tsx
import type { Metadata } from 'next'
import { Geist, Geist_Mono } from 'next/font/google'

import './globals.css'

const _geist = Geist({ subsets: ['latin'] })
const _geistMono = Geist_Mono({ subsets: ['latin'] })

export const metadata: Metadata = {
  title: 'Robot Task Dashboard',
  description: 'Monitor and manage robotic arm tasks',
  generator: 'v0.app',
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="en" className="dark">
      <body className="font-sans antialiased">
        {children}
      </body>
    </html>
  )
}
```

Note: The NavBar uses `usePathname()` which requires `"use client"`. Since the root layout is a server component, the NavBar must be rendered inside each page's client component. Instead, we add it to `robot-dashboard.tsx` and the new `teleop-page.tsx`.

- [ ] **Step 3: Add NavBar to `robot-dashboard.tsx`**

Replace the existing `<header>` block in `robot-dashboard.tsx`. Change the outer return to:

```tsx
  return (
    <div className="flex min-h-screen flex-col bg-background">
      <NavBar />
      <div className="flex flex-1 flex-col p-4 lg:p-6">
        {/* Main grid matching wireframe layout */}
        <div className="mx-auto flex w-full max-w-[1400px] flex-1 flex-col gap-4">
```

Add the import at the top:

```tsx
import { NavBar } from "@/components/nav-bar"
```

Remove the old `<header>` block (the one with "Robot Task Dashboard" h1 and the divider).

Close the extra `<div>` — the structure becomes:

```tsx
  return (
    <div className="flex min-h-screen flex-col bg-background">
      <NavBar />
      <div className="flex flex-1 flex-col p-4 lg:p-6">
        <div className="mx-auto flex w-full max-w-[1400px] flex-1 flex-col gap-4">
          {/* Top row ... */}
          {/* Bottom row ... */}
        </div>
      </div>
    </div>
  )
```

- [ ] **Step 4: Verify dashboard renders**

Run: `cd frontend && pnpm dev`

Open http://localhost:3000 — should see the nav bar at top with "Dashboard" active, and the existing dashboard below.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/nav-bar.tsx frontend/app/layout.tsx frontend/components/robot-dashboard.tsx
git commit -m "feat(frontend): add shared navigation bar"
```

---

### Task 5: Teleop Control Components

**Files:**
- Create: `frontend/components/teleop/status-bar.tsx`
- Create: `frontend/components/teleop/runstop-button.tsx`
- Create: `frontend/components/teleop/speed-scale.tsx`
- Create: `frontend/components/teleop/drive-pad.tsx`
- Create: `frontend/components/teleop/head-controls.tsx`
- Create: `frontend/components/teleop/joint-controls.tsx`
- Create: `frontend/components/teleop/gripper-buttons.tsx`
- Create: `frontend/components/teleop/home-button.tsx`
- Create: `frontend/components/teleop/tts-input.tsx`
- Create: `frontend/components/teleop/chat-log.tsx`

All components follow the terminal monochrome design system: `border border-border`, `rounded-md`, `font-mono` for data, no accent colors except red for runstop.

- [ ] **Step 1: Create `frontend/components/teleop/status-bar.tsx`**

```tsx
"use client";

import type { RobotStatus } from "@/types/robot";

interface StatusBarProps {
  status: RobotStatus;
  isConnected: boolean;
}

export function StatusBar({ status, isConnected }: StatusBarProps) {
  return (
    <div className="flex items-center gap-2 flex-wrap font-mono text-[10px]">
      <span className="flex items-center gap-1.5">
        <span
          className={`inline-block h-1.5 w-1.5 rounded-full ${
            isConnected ? "bg-foreground" : "bg-muted-foreground/30"
          }`}
        />
        <span className={isConnected ? "text-foreground" : "text-muted-foreground/50"}>
          {isConnected ? "CONNECTED" : "DISCONNECTED"}
        </span>
      </span>
      <span className="text-muted-foreground">
        {status.battery.voltage.toFixed(1)}V
        {status.battery.is_charging && " CHG"}
      </span>
      <span className={status.is_homed ? "text-foreground" : "text-muted-foreground/50"}>
        {status.is_homed ? "HOMED" : "NOT HOMED"}
      </span>
      {status.runstop && (
        <span className="text-red-400">RUNSTOP</span>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Create `frontend/components/teleop/runstop-button.tsx`**

```tsx
"use client";

import type { RobotCommand } from "@/lib/teleop-protocol";

interface RunstopButtonProps {
  runstop: boolean;
  sendCommand: (cmd: RobotCommand) => void;
}

export function RunstopButton({ runstop, sendCommand }: RunstopButtonProps) {
  return (
    <button
      className={`w-full h-14 rounded-md border font-mono text-sm font-bold tracking-wide transition-colors ${
        runstop
          ? "border-border bg-background text-foreground hover:bg-foreground/5"
          : "border-red-400/30 bg-red-400/10 text-red-400 hover:bg-red-400/20"
      }`}
      onClick={() => sendCommand({ type: "set_runstop", enabled: !runstop })}
    >
      {runstop ? "RELEASE RUNSTOP" : "RUNSTOP"}
    </button>
  );
}
```

- [ ] **Step 3: Create `frontend/components/teleop/speed-scale.tsx`**

```tsx
"use client";

const LEVELS = [
  { label: "0.25x", value: 0.25 },
  { label: "0.5x", value: 0.5 },
  { label: "1x", value: 1.0 },
  { label: "1.5x", value: 1.5 },
  { label: "2x", value: 2.0 },
] as const;

interface SpeedScaleProps {
  scale: number;
  onChange: (scale: number) => void;
}

export function SpeedScale({ scale, onChange }: SpeedScaleProps) {
  return (
    <div className="rounded-md border border-border p-2">
      <div className="mb-1.5 font-mono text-[10px] text-muted-foreground tracking-wide">
        SPEED
      </div>
      <div className="flex gap-1">
        {LEVELS.map((lvl) => (
          <button
            key={lvl.value}
            className={`flex-1 rounded-md border py-1.5 font-mono text-xs transition-colors ${
              scale === lvl.value
                ? "border-foreground/30 bg-foreground/10 text-foreground"
                : "border-border text-muted-foreground hover:bg-foreground/5 hover:text-foreground"
            }`}
            onClick={() => onChange(lvl.value)}
          >
            {lvl.label}
          </button>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Create `frontend/components/teleop/drive-pad.tsx`**

```tsx
"use client";

import { useCallback, useEffect, useRef } from "react";
import type { RobotCommand } from "@/lib/teleop-protocol";

interface DrivePadProps {
  sendCommand: (cmd: RobotCommand) => void;
  speedScale: number;
  disabled?: boolean;
}

const BASE_LIN = 0.15;
const BASE_ANG = 0.4;
const REPEAT_MS = 100;

export function DrivePad({ sendCommand, speedScale, disabled = false }: DrivePadProps) {
  const activeKeys = useRef(new Set<string>());
  const intervalRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);
  const btnTimerRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);
  const scaleRef = useRef(speedScale);
  useEffect(() => { scaleRef.current = speedScale; }, [speedScale]);

  const computeAndSend = useCallback(() => {
    const keys = activeKeys.current;
    const s = scaleRef.current;
    let linear = 0;
    let angular = 0;

    if (keys.has("w") || keys.has("arrowup")) linear += BASE_LIN * s;
    if (keys.has("s") || keys.has("arrowdown")) linear -= BASE_LIN * s;
    if (keys.has("a") || keys.has("arrowleft")) angular += BASE_ANG * s;
    if (keys.has("d") || keys.has("arrowright")) angular -= BASE_ANG * s;

    sendCommand({ type: "drive", linear, angular });
  }, [sendCommand]);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (!e.key) return;
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (disabled) return;
      const key = e.key.toLowerCase();
      if (["w", "a", "s", "d", "arrowup", "arrowdown", "arrowleft", "arrowright"].includes(key)) {
        e.preventDefault();
        if (!activeKeys.current.has(key)) {
          activeKeys.current.add(key);
          computeAndSend();
          if (!intervalRef.current) {
            intervalRef.current = setInterval(computeAndSend, REPEAT_MS);
          }
        }
      }
    };

    const onKeyUp = (e: KeyboardEvent) => {
      if (!e.key) return;
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      const key = e.key.toLowerCase();
      activeKeys.current.delete(key);
      if (activeKeys.current.size === 0) {
        if (intervalRef.current) {
          clearInterval(intervalRef.current);
          intervalRef.current = undefined;
        }
        sendCommand({ type: "drive", linear: 0, angular: 0 });
      } else {
        computeAndSend();
      }
    };

    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [sendCommand, computeAndSend, disabled]);

  const startDrive = useCallback(
    (linear: number, angular: number) => {
      const send = () =>
        sendCommand({ type: "drive", linear: linear * scaleRef.current, angular: angular * scaleRef.current });
      send();
      btnTimerRef.current = setInterval(send, REPEAT_MS);
    },
    [sendCommand],
  );

  const stopDrive = useCallback(() => {
    if (btnTimerRef.current) {
      clearInterval(btnTimerRef.current);
      btnTimerRef.current = undefined;
    }
    sendCommand({ type: "drive", linear: 0, angular: 0 });
  }, [sendCommand]);

  const btn = "h-14 w-14 rounded-md border border-border font-mono text-xs transition-colors hover:bg-foreground/5 active:bg-foreground/10 disabled:opacity-30";

  return (
    <div className="rounded-md border border-border p-2">
      <div className="mb-1.5 font-mono text-[10px] text-muted-foreground tracking-wide">
        DRIVE (WASD)
      </div>
      <div className="grid grid-cols-3 gap-1 w-fit mx-auto">
        <div />
        <button className={btn} disabled={disabled}
          onPointerDown={() => !disabled && startDrive(BASE_LIN, 0)} onPointerUp={stopDrive} onPointerLeave={stopDrive}>
          W
        </button>
        <div />
        <button className={btn} disabled={disabled}
          onPointerDown={() => !disabled && startDrive(0, BASE_ANG)} onPointerUp={stopDrive} onPointerLeave={stopDrive}>
          A
        </button>
        <button className={btn} disabled={disabled}
          onPointerDown={() => !disabled && startDrive(-BASE_LIN, 0)} onPointerUp={stopDrive} onPointerLeave={stopDrive}>
          S
        </button>
        <button className={btn} disabled={disabled}
          onPointerDown={() => !disabled && startDrive(0, -BASE_ANG)} onPointerUp={stopDrive} onPointerLeave={stopDrive}>
          D
        </button>
      </div>
      <button
        className="mt-1.5 w-full rounded-md border border-red-400/30 bg-red-400/10 py-2 font-mono text-xs text-red-400 hover:bg-red-400/20 disabled:opacity-30"
        disabled={disabled}
        onClick={() => sendCommand({ type: "stop" })}
      >
        {disabled ? "NAVIGATING..." : "STOP"}
      </button>
    </div>
  );
}
```

- [ ] **Step 5: Create `frontend/components/teleop/head-controls.tsx`**

```tsx
"use client";

import { useRef, useCallback, useEffect } from "react";
import type { RobotCommand } from "@/lib/teleop-protocol";

interface HeadControlsProps {
  sendCommand: (cmd: RobotCommand) => void;
  speedScale: number;
}

const BASE_PAN = 0.15;
const BASE_TILT = 0.15;
const REPEAT_MS = 200;

export function HeadControls({ sendCommand, speedScale }: HeadControlsProps) {
  const timerRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);
  const scaleRef = useRef(speedScale);
  useEffect(() => { scaleRef.current = speedScale; }, [speedScale]);

  const startRepeat = useCallback(
    (name: "joint_head_pan" | "joint_head_tilt", base: number, direction: 1 | -1) => {
      if (timerRef.current) clearInterval(timerRef.current);
      const send = () =>
        sendCommand({ type: "increment_joint", name, increment: base * direction * scaleRef.current });
      send();
      timerRef.current = setInterval(send, REPEAT_MS);
    },
    [sendCommand],
  );

  const stopRepeat = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = undefined;
    }
  }, []);

  const btn = "h-14 w-14 rounded-md border border-border font-mono text-xs transition-colors hover:bg-foreground/5 active:bg-foreground/10";
  const preset = "rounded-md border border-border px-3 py-1.5 font-mono text-[10px] text-muted-foreground transition-colors hover:bg-foreground/5 hover:text-foreground";

  return (
    <div className="rounded-md border border-border p-2">
      <div className="mb-1.5 font-mono text-[10px] text-muted-foreground tracking-wide">
        HEAD
      </div>
      <div className="grid grid-cols-3 gap-1 w-fit mx-auto">
        <div />
        <button className={btn}
          onPointerDown={() => startRepeat("joint_head_tilt", BASE_TILT, 1)} onPointerUp={stopRepeat} onPointerLeave={stopRepeat}>
          Up
        </button>
        <div />
        <button className={btn}
          onPointerDown={() => startRepeat("joint_head_pan", BASE_PAN, 1)} onPointerUp={stopRepeat} onPointerLeave={stopRepeat}>
          L
        </button>
        <button className={btn}
          onPointerDown={() => startRepeat("joint_head_tilt", BASE_TILT, -1)} onPointerUp={stopRepeat} onPointerLeave={stopRepeat}>
          Dn
        </button>
        <button className={btn}
          onPointerDown={() => startRepeat("joint_head_pan", BASE_PAN, -1)} onPointerUp={stopRepeat} onPointerLeave={stopRepeat}>
          R
        </button>
      </div>
      <div className="mt-1.5 flex gap-1 justify-center">
        <button className={preset} onClick={() => sendCommand({ type: "look_at", camera: "forward" })}>
          Forward
        </button>
        <button className={preset} onClick={() => sendCommand({ type: "look_at", camera: "gripper" })}>
          Gripper
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 6: Create `frontend/components/teleop/joint-controls.tsx`**

```tsx
"use client";

import { useRef, useCallback, useEffect } from "react";
import type { RobotCommand } from "@/lib/teleop-protocol";
import type { JointName, JointPositions } from "@/types/robot";
import { JOINT_INCREMENTS, JOINT_LABELS } from "@/types/robot";

interface JointControlsProps {
  joints: JointPositions;
  sendCommand: (cmd: RobotCommand) => void;
  speedScale: number;
}

const CONTROLLED_JOINTS: JointName[] = [
  "joint_lift",
  "wrist_extension",
  "joint_wrist_yaw",
  "joint_wrist_pitch",
  "joint_wrist_roll",
  "joint_gripper_finger_left",
];

const REPEAT_MS = 200;

export function JointControls({ joints, sendCommand, speedScale }: JointControlsProps) {
  const timerRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);
  const scaleRef = useRef(speedScale);
  useEffect(() => { scaleRef.current = speedScale; }, [speedScale]);

  const startRepeat = useCallback(
    (name: JointName, direction: 1 | -1) => {
      if (timerRef.current) clearInterval(timerRef.current);
      const send = () => {
        const inc = (JOINT_INCREMENTS[name] ?? 0.05) * direction * scaleRef.current;
        sendCommand({ type: "increment_joint", name, increment: inc });
      };
      send();
      timerRef.current = setInterval(send, REPEAT_MS);
    },
    [sendCommand],
  );

  const stopRepeat = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = undefined;
    }
  }, []);

  const btn = "h-11 w-11 shrink-0 rounded-md border border-border font-mono text-base transition-colors hover:bg-foreground/5 active:bg-foreground/10";

  return (
    <div className="rounded-md border border-border p-2">
      <div className="mb-1.5 font-mono text-[10px] text-muted-foreground tracking-wide">
        JOINTS
      </div>
      <div className="space-y-1">
        {CONTROLLED_JOINTS.map((name) => (
          <div key={name} className="flex items-center gap-2">
            <button className={btn}
              onPointerDown={() => startRepeat(name, -1)} onPointerUp={stopRepeat} onPointerLeave={stopRepeat}>
              -
            </button>
            <div className="flex-1 min-w-0">
              <div className="font-mono text-[10px] text-muted-foreground truncate">
                {JOINT_LABELS[name] ?? name}
              </div>
              <div className="font-mono text-xs text-foreground">
                {joints[name]?.toFixed(3) ?? "0.000"}
              </div>
            </div>
            <button className={btn}
              onPointerDown={() => startRepeat(name, 1)} onPointerUp={stopRepeat} onPointerLeave={stopRepeat}>
              +
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 7: Create `frontend/components/teleop/gripper-buttons.tsx`**

```tsx
"use client";

import { useRef, useCallback, useEffect } from "react";
import type { RobotCommand } from "@/lib/teleop-protocol";

interface GripperButtonsProps {
  sendCommand: (cmd: RobotCommand) => void;
  speedScale: number;
}

const BASE_INCREMENT = 3;
const REPEAT_MS = 200;

export function GripperButtons({ sendCommand, speedScale }: GripperButtonsProps) {
  const timerRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);
  const scaleRef = useRef(speedScale);
  useEffect(() => { scaleRef.current = speedScale; }, [speedScale]);

  const startRepeat = useCallback(
    (direction: 1 | -1) => {
      if (timerRef.current) clearInterval(timerRef.current);
      const send = () =>
        sendCommand({
          type: "increment_joint",
          name: "joint_gripper_finger_left",
          increment: BASE_INCREMENT * direction * scaleRef.current,
        });
      send();
      timerRef.current = setInterval(send, REPEAT_MS);
    },
    [sendCommand],
  );

  const stopRepeat = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = undefined;
    }
  }, []);

  const btn = "flex-1 rounded-md border border-border py-2 font-mono text-xs transition-colors hover:bg-foreground/5 active:bg-foreground/10";

  return (
    <div className="flex gap-1">
      <button className={btn}
        onPointerDown={() => startRepeat(1)} onPointerUp={stopRepeat} onPointerLeave={stopRepeat}>
        OPEN
      </button>
      <button className={btn}
        onPointerDown={() => startRepeat(-1)} onPointerUp={stopRepeat} onPointerLeave={stopRepeat}>
        CLOSE
      </button>
    </div>
  );
}
```

- [ ] **Step 8: Create `frontend/components/teleop/home-button.tsx`**

```tsx
"use client";

import type { RobotCommand } from "@/lib/teleop-protocol";

interface HomeButtonProps {
  isHomed: boolean;
  sendCommand: (cmd: RobotCommand) => void;
}

export function HomeButton({ isHomed, sendCommand }: HomeButtonProps) {
  return (
    <button
      className="w-full rounded-md border border-border py-2 font-mono text-xs transition-colors hover:bg-foreground/5 active:bg-foreground/10"
      onClick={() => sendCommand({ type: "home" })}
    >
      {isHomed ? "RE-HOME ROBOT" : "HOME ROBOT"}
    </button>
  );
}
```

- [ ] **Step 9: Create `frontend/components/teleop/tts-input.tsx`**

```tsx
"use client";

import { useState } from "react";
import type { RobotCommand } from "@/lib/teleop-protocol";

interface TtsInputProps {
  sendCommand: (cmd: RobotCommand) => void;
  onSend?: (text: string) => void;
}

export function TtsInput({ sendCommand, onSend }: TtsInputProps) {
  const [text, setText] = useState("");

  const send = () => {
    if (text.trim()) {
      sendCommand({ type: "tts", text: text.trim() });
      onSend?.(text.trim());
      setText("");
    }
  };

  return (
    <div className="flex gap-1.5">
      <input
        placeholder="Text to speech..."
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && send()}
        className="flex-1 rounded-md border border-border bg-background px-2 py-1.5 font-mono text-xs text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:ring-1 focus:ring-ring"
      />
      <button
        className="rounded-md border border-border px-3 py-1.5 font-mono text-xs transition-colors hover:bg-foreground/5 disabled:opacity-30"
        onClick={send}
        disabled={!text.trim()}
      >
        SPEAK
      </button>
    </div>
  );
}
```

- [ ] **Step 10: Create `frontend/components/teleop/chat-log.tsx`**

```tsx
"use client";

import { useEffect, useRef } from "react";

export interface ChatEntry {
  id: number;
  time: string;
  kind: "speech" | "listen";
  text: string;
}

interface ChatLogProps {
  entries: ChatEntry[];
}

export function ChatLog({ entries }: ChatLogProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [entries.length]);

  return (
    <div className="flex-1 min-h-0 overflow-y-auto rounded-md border border-border bg-background/50 p-2 font-mono text-[11px] space-y-0.5">
      {entries.length === 0 && (
        <div className="text-muted-foreground/40 text-center py-4">No messages</div>
      )}
      {entries.map((e) => (
        <div key={e.id} className="flex gap-1.5">
          <span className="text-muted-foreground shrink-0">{e.time}</span>
          <span className="text-muted-foreground shrink-0">{e.kind === "speech" ? "TTS" : "ASR"}</span>
          <span className="text-foreground break-words">{e.text}</span>
        </div>
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
```

- [ ] **Step 11: Commit**

```bash
git add frontend/components/teleop/status-bar.tsx frontend/components/teleop/runstop-button.tsx frontend/components/teleop/speed-scale.tsx frontend/components/teleop/drive-pad.tsx frontend/components/teleop/head-controls.tsx frontend/components/teleop/joint-controls.tsx frontend/components/teleop/gripper-buttons.tsx frontend/components/teleop/home-button.tsx frontend/components/teleop/tts-input.tsx frontend/components/teleop/chat-log.tsx
git commit -m "feat(frontend): add teleop control components"
```

---

### Task 6: Camera Components

**Files:**
- Create: `frontend/components/teleop/camera-view.tsx`
- Create: `frontend/components/teleop/nav-map.tsx`
- Create: `frontend/components/teleop/camera-panel.tsx`

- [ ] **Step 1: Create `frontend/components/teleop/camera-view.tsx`**

```tsx
"use client";

import { useEffect, useRef } from "react";
import type { CameraName } from "@/types/robot";

const CAMERA_ROTATION: Record<CameraName, number> = {
  overhead: -90,
  realsense: 90,
  gripper: 0,
};

interface CameraViewProps {
  name: CameraName;
  src: string | null;
}

export function CameraView({ name, src }: CameraViewProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);

  useEffect(() => {
    if (!imgRef.current) {
      imgRef.current = new Image();
    }
    const img = imgRef.current;
    const canvas = canvasRef.current;
    if (!canvas || !src) return;

    img.onload = () => {
      const ctx = canvas.getContext("2d");
      if (!ctx) return;

      const rot = CAMERA_ROTATION[name] ?? 0;

      if (rot === 90 || rot === -90) {
        canvas.width = img.naturalHeight;
        canvas.height = img.naturalWidth;
        ctx.save();
        ctx.translate(canvas.width / 2, canvas.height / 2);
        ctx.rotate((rot * Math.PI) / 180);
        ctx.drawImage(img, -img.naturalWidth / 2, -img.naturalHeight / 2);
        ctx.restore();
      } else {
        canvas.width = img.naturalWidth;
        canvas.height = img.naturalHeight;
        ctx.drawImage(img, 0, 0);
      }
    };
    img.src = src;
  }, [src, name]);

  return (
    <div className="relative w-full h-full rounded-md border border-border bg-background/50 overflow-hidden flex items-center justify-center">
      <canvas
        ref={canvasRef}
        className="max-w-full max-h-full object-contain"
      />
      {!src && (
        <div className="absolute inset-0 flex items-center justify-center font-mono text-[10px] text-muted-foreground/40">
          NO SIGNAL &mdash; {name.toUpperCase()}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Create `frontend/components/teleop/nav-map.tsx`**

```tsx
"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { RobotCommand } from "@/lib/teleop-protocol";
import { MAP_CONFIG, type NavState, type RobotPose, type NavPathPoint } from "@/types/robot";

interface NavMapProps {
  navState: NavState;
  robotPose: RobotPose | null;
  navPath: NavPathPoint[];
  sendCommand: (cmd: RobotCommand) => void;
}

function mapToPixel(mx: number, my: number): [number, number] {
  const px = (mx - MAP_CONFIG.originX) / MAP_CONFIG.resolution;
  const py = MAP_CONFIG.height - (my - MAP_CONFIG.originY) / MAP_CONFIG.resolution;
  return [px, py];
}

function pixelToMap(px: number, py: number): [number, number] {
  const mx = MAP_CONFIG.originX + px * MAP_CONFIG.resolution;
  const my = MAP_CONFIG.originY + (MAP_CONFIG.height - py) * MAP_CONFIG.resolution;
  return [mx, my];
}

export function NavMap({ navState, robotPose, navPath, sendCommand }: NavMapProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const mapImgRef = useRef<HTMLImageElement | null>(null);
  const [mapLoaded, setMapLoaded] = useState(false);

  useEffect(() => {
    const img = new Image();
    img.onload = () => {
      mapImgRef.current = img;
      setMapLoaded(true);
    };
    img.src = MAP_CONFIG.imageUrl;
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    const img = mapImgRef.current;
    if (!canvas || !img || !mapLoaded) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    canvas.width = MAP_CONFIG.width;
    canvas.height = MAP_CONFIG.height;

    ctx.drawImage(img, 0, 0);

    if (navPath.length > 1) {
      ctx.strokeStyle = "rgba(255,255,255,0.5)";
      ctx.lineWidth = 2;
      ctx.beginPath();
      const [sx, sy] = mapToPixel(navPath[0].x, navPath[0].y);
      ctx.moveTo(sx, sy);
      for (let i = 1; i < navPath.length; i++) {
        const [px, py] = mapToPixel(navPath[i].x, navPath[i].y);
        ctx.lineTo(px, py);
      }
      ctx.stroke();
    }

    if (robotPose) {
      const [rx, ry] = mapToPixel(robotPose.x, robotPose.y);

      const arrowLen = 8;
      const ax = rx + arrowLen * Math.cos(-robotPose.theta);
      const ay = ry + arrowLen * Math.sin(-robotPose.theta);
      ctx.strokeStyle = "rgba(255,255,255,0.8)";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(rx, ry);
      ctx.lineTo(ax, ay);
      ctx.stroke();

      ctx.fillStyle = "rgba(255,255,255,0.9)";
      ctx.beginPath();
      ctx.arc(rx, ry, 5, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = "rgba(0,0,0,0.5)";
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }
  }, [mapLoaded, robotPose, navPath]);

  const handleClick = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      if (navState === "navigating") return;

      const canvas = canvasRef.current;
      if (!canvas) return;

      const rect = canvas.getBoundingClientRect();
      const scaleX = MAP_CONFIG.width / rect.width;
      const scaleY = MAP_CONFIG.height / rect.height;
      const px = (e.clientX - rect.left) * scaleX;
      const py = (e.clientY - rect.top) * scaleY;
      const [mx, my] = pixelToMap(px, py);

      sendCommand({ type: "nav_goal", x: mx, y: my, theta: 0.0 });
    },
    [navState, sendCommand],
  );

  const stateLabel: Record<NavState, string> = {
    idle: "IDLE",
    navigating: "NAVIGATING",
    succeeded: "SUCCEEDED",
    failed: "FAILED",
  };

  return (
    <div className="relative w-full h-full rounded-md border border-border bg-background/50 overflow-hidden flex flex-col">
      <div className="flex items-center justify-between px-2 py-1 shrink-0">
        <span className={`font-mono text-[10px] ${
          navState === "failed" ? "text-red-400" : "text-muted-foreground"
        }`}>
          NAV: {stateLabel[navState]}
        </span>
        {navState === "navigating" && (
          <button
            className="rounded-md border border-red-400/30 bg-red-400/10 px-2 py-0.5 font-mono text-[10px] text-red-400 hover:bg-red-400/20"
            onClick={() => sendCommand({ type: "cancel_nav" })}
          >
            CANCEL
          </button>
        )}
      </div>
      <div className="flex-1 min-h-0 flex items-center justify-center p-1">
        <canvas
          ref={canvasRef}
          className="cursor-crosshair"
          style={{
            maxWidth: "100%",
            maxHeight: "100%",
            width: "auto",
            height: "100%",
            aspectRatio: `${MAP_CONFIG.width} / ${MAP_CONFIG.height}`,
            imageRendering: "pixelated",
          }}
          onClick={handleClick}
        />
        {!mapLoaded && (
          <div className="absolute inset-0 flex items-center justify-center font-mono text-[10px] text-muted-foreground/40">
            LOADING MAP...
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Create `frontend/components/teleop/camera-panel.tsx`**

```tsx
"use client";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { CameraView } from "./camera-view";
import { NavMap } from "./nav-map";
import type { CameraName, NavState, RobotPose, NavPathPoint } from "@/types/robot";
import type { RobotCommand } from "@/lib/teleop-protocol";

interface CameraPanelProps {
  frames: Record<CameraName, string | null>;
  navState: NavState;
  robotPose: RobotPose | null;
  navPath: NavPathPoint[];
  sendCommand: (cmd: RobotCommand) => void;
}

export function CameraPanel({ frames, navState, robotPose, navPath, sendCommand }: CameraPanelProps) {
  return (
    <Tabs defaultValue="overhead" className="h-full flex flex-col">
      <TabsList className="grid w-full grid-cols-3 shrink-0 bg-muted/50">
        <TabsTrigger value="overhead" className="font-mono text-[10px]">OVERHEAD</TabsTrigger>
        <TabsTrigger value="gripper" className="font-mono text-[10px]">GRIPPER</TabsTrigger>
        <TabsTrigger value="map" className="font-mono text-[10px]">MAP</TabsTrigger>
      </TabsList>
      <TabsContent value="overhead" className="mt-1 flex-1 min-h-0">
        <div className="grid grid-cols-2 gap-1 h-full">
          <CameraView name="overhead" src={frames.overhead} />
          <CameraView name="realsense" src={frames.realsense} />
        </div>
      </TabsContent>
      <TabsContent value="gripper" className="mt-1 flex-1 min-h-0">
        <CameraView name="gripper" src={frames.gripper} />
      </TabsContent>
      <TabsContent value="map" className="mt-1 flex-1 min-h-0">
        <NavMap
          navState={navState}
          robotPose={robotPose}
          navPath={navPath}
          sendCommand={sendCommand}
        />
      </TabsContent>
    </Tabs>
  );
}
```

- [ ] **Step 4: Commit**

```bash
git add frontend/components/teleop/camera-view.tsx frontend/components/teleop/nav-map.tsx frontend/components/teleop/camera-panel.tsx
git commit -m "feat(frontend): add teleop camera panel with nav map"
```

---

### Task 7: Teleop Page

**Files:**
- Create: `frontend/components/teleop/teleop-page.tsx`
- Create: `frontend/app/teleop/page.tsx`

- [ ] **Step 1: Create `frontend/components/teleop/teleop-page.tsx`**

```tsx
"use client";

import { useCallback, useState } from "react";
import { useTeleop } from "@/hooks/use-teleop";
import { NavBar } from "@/components/nav-bar";
import { StatusBar } from "./status-bar";
import { CameraPanel } from "./camera-panel";
import { ChatLog, type ChatEntry } from "./chat-log";
import { DrivePad } from "./drive-pad";
import { JointControls } from "./joint-controls";
import { HeadControls } from "./head-controls";
import { GripperButtons } from "./gripper-buttons";
import { SpeedScale } from "./speed-scale";
import { RunstopButton } from "./runstop-button";
import { HomeButton } from "./home-button";
import { TtsInput } from "./tts-input";

let chatIdCounter = 0;

export function TeleopPage() {
  const [robotHost, setRobotHost] = useState("");
  const [speedScale, setSpeedScale] = useState(1.0);
  const [chatEntries, setChatEntries] = useState<ChatEntry[]>([]);

  const { status, cameras, isConnected, sendCommand, connect, disconnect } = useTeleop();

  const handleConnect = () => {
    if (robotHost.trim()) {
      const host = robotHost.trim();
      const url = host.includes("://") ? host : `ws://${host}:8765`;
      connect(url);
    }
  };

  const addChatEntry = useCallback((kind: "speech" | "listen", text: string) => {
    const time = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    setChatEntries((prev) => [...prev, { id: ++chatIdCounter, time, kind, text }]);
  }, []);

  return (
    <div className="flex flex-col h-dvh bg-background text-foreground overflow-hidden">
      <NavBar />

      {/* Connection header */}
      <div className="border-b border-border px-4 py-2 flex items-center gap-3">
        <div className="flex gap-1.5 flex-1 max-w-sm">
          <input
            placeholder="Robot IP or ws://host:port"
            value={robotHost}
            onChange={(e) => setRobotHost(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleConnect()}
            className="flex-1 rounded-md border border-border bg-background px-2 py-1 font-mono text-xs text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:ring-1 focus:ring-ring"
          />
          <button
            onClick={isConnected ? disconnect : handleConnect}
            className={`rounded-md border px-3 py-1 font-mono text-xs transition-colors ${
              isConnected
                ? "border-border text-muted-foreground hover:bg-foreground/5"
                : "border-foreground/20 bg-foreground/10 text-foreground hover:bg-foreground/15"
            }`}
          >
            {isConnected ? "Disconnect" : "Connect"}
          </button>
        </div>
        <div className="ml-auto">
          <StatusBar status={status} isConnected={isConnected} />
        </div>
      </div>

      {/* Main content */}
      <div className="flex-1 grid grid-cols-[1fr_360px] gap-3 p-3 min-h-0">
        {/* Left: cameras + TTS + chat */}
        <div className="flex flex-col gap-2 min-h-0">
          <div className="h-[60%] shrink-0 min-h-0">
            <CameraPanel
              frames={cameras}
              navState={status.nav_state}
              robotPose={status.robot_pose}
              navPath={status.nav_path}
              sendCommand={sendCommand}
            />
          </div>
          <TtsInput sendCommand={sendCommand} onSend={(text) => addChatEntry("speech", text)} />
          <ChatLog entries={chatEntries} />
        </div>

        {/* Right: controls */}
        <div className="flex flex-col gap-2 min-h-0 overflow-y-auto">
          <RunstopButton runstop={status.runstop} sendCommand={sendCommand} />
          <SpeedScale scale={speedScale} onChange={setSpeedScale} />
          <div className="grid grid-cols-2 gap-2">
            <DrivePad sendCommand={sendCommand} speedScale={speedScale} disabled={status.nav_state === "navigating"} />
            <HeadControls sendCommand={sendCommand} speedScale={speedScale} />
          </div>
          <JointControls joints={status.joints} sendCommand={sendCommand} speedScale={speedScale} />
          <GripperButtons sendCommand={sendCommand} speedScale={speedScale} />
          <HomeButton isHomed={status.is_homed} sendCommand={sendCommand} />
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Create `frontend/app/teleop/page.tsx`**

```tsx
"use client"

import { TeleopPage } from "@/components/teleop/teleop-page"

export default function Page() {
  return <TeleopPage />
}
```

- [ ] **Step 3: Verify the teleop page renders**

Run: `cd frontend && pnpm dev`

Open http://localhost:3000/teleop — should see the nav bar with "Teleop" active, the connection input, and all control panels laid out. Clicking "Dashboard" in the nav bar should go back to `/`.

- [ ] **Step 4: Verify the dashboard still works**

Open http://localhost:3000 — should see the nav bar with "Dashboard" active and the existing workflow dashboard below it.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/teleop/teleop-page.tsx frontend/app/teleop/page.tsx
git commit -m "feat(frontend): add teleop page with full control layout"
```

---

### Task 8: Build Verification

- [ ] **Step 1: Run the frontend build**

Run: `cd frontend && pnpm build`

Expected: Build succeeds with no errors. TypeScript warnings are acceptable (project has `ignoreBuildErrors: true`).

- [ ] **Step 2: Run lint**

Run: `cd frontend && pnpm lint`

Expected: No new errors introduced. Fix any lint issues in teleop files if found.

- [ ] **Step 3: Final commit (if any fixes)**

If lint or build required fixes:

```bash
git add -u
git commit -m "fix(frontend): address lint/build issues in teleop components"
```
