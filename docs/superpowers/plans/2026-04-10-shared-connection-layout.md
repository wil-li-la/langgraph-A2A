# Shared Robot Connection + Dashboard Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move robot IP input to the shared nav bar via React context so the user connects once per session, remove the ConnectPanel, and redesign the dashboard as a two-column layout (graph left, controls right).

**Architecture:** A new `RobotConnectionProvider` wraps the app in root layout, calling `useTeleop()` internally and exposing connection state via context. The nav bar consumes the context for the IP input and status. Both dashboard and teleop pages read from the same context. The dashboard drops the ConnectPanel and adopts a two-column layout.

**Tech Stack:** Next.js 16 (App Router), React 19 (Context API), TypeScript, Tailwind CSS

---

### Task 1: Robot Connection Context

**Files:**
- Create: `frontend/contexts/robot-connection.tsx`

- [ ] **Step 1: Create `frontend/contexts/robot-connection.tsx`**

```tsx
"use client"

import { createContext, useContext, useState, useCallback, type ReactNode } from "react"
import { useTeleop } from "@/hooks/use-teleop"
import type { RobotStatus, CameraName } from "@/types/robot"
import type { RobotCommand } from "@/lib/teleop-protocol"

interface RobotConnectionContextValue {
  robotHost: string
  setRobotHost: (host: string) => void
  isConnected: boolean
  status: RobotStatus
  cameras: Record<CameraName, string | null>
  sendCommand: (cmd: RobotCommand) => void
  connect: (host: string) => void
  disconnect: () => void
  handleConnect: () => void
}

const RobotConnectionContext = createContext<RobotConnectionContextValue | null>(null)

export function RobotConnectionProvider({ children }: { children: ReactNode }) {
  const [robotHost, setRobotHost] = useState("")
  const { status, cameras, isConnected, sendCommand, connect, disconnect } = useTeleop()

  const handleConnect = useCallback(() => {
    if (robotHost.trim()) {
      const host = robotHost.trim()
      const url = host.includes("://") ? host : `ws://${host}:8765`
      connect(url)
    }
  }, [robotHost, connect])

  return (
    <RobotConnectionContext.Provider
      value={{
        robotHost,
        setRobotHost,
        isConnected,
        status,
        cameras,
        sendCommand,
        connect,
        disconnect,
        handleConnect,
      }}
    >
      {children}
    </RobotConnectionContext.Provider>
  )
}

export function useRobotConnection() {
  const ctx = useContext(RobotConnectionContext)
  if (!ctx) {
    throw new Error("useRobotConnection must be used within RobotConnectionProvider")
  }
  return ctx
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/contexts/robot-connection.tsx
git commit -m "feat(frontend): add RobotConnectionProvider context"
```

---

### Task 2: Wire Context in Layout + Update Nav Bar

**Files:**
- Modify: `frontend/app/layout.tsx`
- Modify: `frontend/components/nav-bar.tsx`

- [ ] **Step 1: Update `frontend/app/layout.tsx` to wrap with provider**

Replace the entire file:

```tsx
import type { Metadata } from 'next'
import { Geist, Geist_Mono } from 'next/font/google'
import { RobotConnectionProvider } from '@/contexts/robot-connection'

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
        <RobotConnectionProvider>
          {children}
        </RobotConnectionProvider>
      </body>
    </html>
  )
}
```

- [ ] **Step 2: Update `frontend/components/nav-bar.tsx` to include connection UI**

Replace the entire file:

```tsx
"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { useRobotConnection } from "@/contexts/robot-connection"

const NAV_ITEMS = [
  { href: "/", label: "Dashboard" },
  { href: "/teleop", label: "Teleop" },
] as const;

export function NavBar() {
  const pathname = usePathname();
  const { robotHost, setRobotHost, isConnected, handleConnect, disconnect } = useRobotConnection();

  return (
    <header className="border-b border-border bg-background px-4 py-1.5 shrink-0">
      <div className="flex items-center gap-3">
        <h1 className="font-mono text-sm font-medium tracking-tight text-foreground whitespace-nowrap">
          Robot Task Dashboard
        </h1>

        {/* Robot connection */}
        <div className="flex items-center gap-1.5">
          <input
            placeholder="Robot IP"
            value={robotHost}
            onChange={(e) => setRobotHost(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleConnect()}
            className="w-40 rounded-md border border-border bg-background px-2 py-0.5 font-mono text-[10px] text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:ring-1 focus:ring-ring"
          />
          <button
            onClick={isConnected ? disconnect : handleConnect}
            className={`rounded-md border px-2 py-0.5 font-mono text-[10px] transition-colors ${
              isConnected
                ? "border-border text-muted-foreground hover:bg-foreground/5"
                : "border-foreground/20 bg-foreground/10 text-foreground hover:bg-foreground/15"
            }`}
          >
            {isConnected ? "Disconnect" : "Connect"}
          </button>
          {isConnected && (
            <span className="flex items-center gap-1.5 font-mono text-[10px]">
              <span className="relative flex h-1.5 w-1.5">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-foreground/50" />
                <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-foreground" />
              </span>
              <span className="text-foreground">Stretch 3</span>
            </span>
          )}
        </div>

        {/* Nav links */}
        <nav className="ml-auto flex gap-1">
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

- [ ] **Step 3: Commit**

```bash
git add frontend/app/layout.tsx frontend/components/nav-bar.tsx
git commit -m "feat(frontend): wire connection context in layout and nav bar"
```

---

### Task 3: Refactor Dashboard Layout

**Files:**
- Modify: `frontend/components/robot-dashboard.tsx`

- [ ] **Step 1: Replace `frontend/components/robot-dashboard.tsx`**

Replace the entire file. Key changes:
- Remove `ConnectPanel` import, `selectedRobot` state, robot selector
- Hardcode `taskData["stretch3"]`
- Two-column layout: left (graph + video), right (skills + mode + log)
- Remove `NavBar` import (already rendered by each page or will be in layout)
- Actually, the NavBar is still rendered here — keep it

```tsx
"use client"

import { useEffect, useRef } from "react"
import { taskData } from "@/lib/mock-data"
import { SkillsPanel } from "@/components/skills-panel"
import { WorkflowGraph } from "@/components/workflow-graph"
import { VideoPanel } from "@/components/video-panel"
import { ModeToggle } from "@/components/mode-toggle"
import { PauseGuide } from "@/components/pause-guide"
import { useWorkflow } from "@/hooks/use-workflow"
import { NavBar } from "@/components/nav-bar"

export function RobotDashboard() {
  const data = taskData["stretch3"]
  const {
    nodes,
    edges,
    skillsData,
    isLoading,
    isLive,
    isExecuting,
    activeNodeId,
    executionLog,
    progress,
    isPaused,
    pausedNodeId,
    pauseReason,
    resumeFromNode,
    resetWorkflow,
    startStreamExecution,
    stopStreamExecution,
  } = useWorkflow("stretch3")

  const logContainerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (logContainerRef.current) {
      logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight
    }
  }, [executionLog])

  return (
    <div className="flex h-dvh flex-col bg-background overflow-hidden">
      <NavBar />
      <div className="flex flex-1 min-h-0 gap-3 p-3">
        {/* Left column: Graph + Video */}
        <div className="flex flex-1 flex-col gap-3 min-h-0">
          {/* Task LangGraph */}
          <div className="flex-1 rounded-md border border-border bg-card p-3 flex flex-col min-h-0">
            <div className="mb-2 flex items-center justify-between shrink-0">
              <h2 className="font-mono text-sm font-medium tracking-wide text-foreground">
                TASK &mdash; LANGGRAPH
              </h2>
              <div className="flex items-center gap-2">
                <button
                  onClick={resetWorkflow}
                  disabled={isExecuting}
                  className="rounded border border-border px-2 py-0.5 font-mono text-[9px] text-muted-foreground transition-colors hover:bg-foreground/10 hover:text-foreground disabled:opacity-30"
                  title="Reset graph state"
                >
                  ↺ RESET
                </button>

                {isLoading ? (
                  <span className="font-mono text-[10px] text-muted-foreground">loading…</span>
                ) : isLive ? (
                  <>
                    <span className="relative flex h-1.5 w-1.5">
                      <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-foreground/50" />
                      <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-foreground" />
                    </span>
                    <span className="font-mono text-[10px] text-foreground">LIVE</span>
                  </>
                ) : (
                  <span className="font-mono text-[10px] text-muted-foreground/50">OFFLINE</span>
                )}
              </div>
            </div>

            {/* Progress bar */}
            {isExecuting && (
              <div className="mb-2 shrink-0">
                <div className="flex items-center justify-between mb-1">
                  <span className="font-mono text-[9px] text-muted-foreground">PROGRESS</span>
                  <span className="font-mono text-[9px] text-muted-foreground">{progress}%</span>
                </div>
                <div className="h-1 w-full rounded-full bg-border overflow-hidden">
                  <div
                    className="h-full rounded-full transition-all duration-700 ease-out"
                    style={{
                      width: `${progress}%`,
                      background: "linear-gradient(90deg, rgba(56,189,248,0.8), rgba(99,102,241,0.8))",
                    }}
                  />
                </div>
              </div>
            )}

            <div className="flex-1 min-h-0 overflow-auto rounded-md border border-border bg-background/50">
              <WorkflowGraph
                nodes={nodes}
                edges={edges}
                activeNodeId={activeNodeId}
                isPaused={isPaused}
                onNodeClick={resumeFromNode}
              />
            </div>
          </div>

          {/* Video streaming */}
          <div className="shrink-0 h-[200px] rounded-md border border-border bg-card p-3">
            <VideoPanel data={data} />
          </div>
        </div>

        {/* Right column: Skills + Mode + Log */}
        <div className="w-[340px] shrink-0 flex flex-col gap-3 min-h-0">
          {/* Required Skills */}
          <div className="rounded-md border border-border bg-card p-3 shrink-0">
            <SkillsPanel skillsData={skillsData} />
          </div>

          {/* Operation Mode */}
          <div className="rounded-md border border-border bg-card p-3 shrink-0">
            <ModeToggle
              isExecuting={isExecuting}
              onStreamRun={startStreamExecution}
              onStreamStop={stopStreamExecution}
            />
          </div>

          {/* Pause guide */}
          {isPaused && pausedNodeId && pauseReason && (
            <div className="rounded-md border border-border bg-card p-3 shrink-0">
              <PauseGuide nodeId={pausedNodeId} reason={pauseReason} />
            </div>
          )}

          {/* Execution Log */}
          <div className="flex-1 min-h-0 rounded-md border border-border bg-card p-3 flex flex-col">
            <h2 className="mb-2 font-mono text-sm font-medium tracking-wide text-foreground shrink-0">
              EXECUTION LOG
            </h2>
            <div ref={logContainerRef} className="flex-1 min-h-0 overflow-auto rounded-md border border-border bg-background/50 p-2">
              {executionLog.length === 0 ? (
                <p className="font-mono text-xs text-muted-foreground/40">No execution history</p>
              ) : (
                <div className="flex flex-col gap-0.5">
                  {executionLog.map((entry, i) => (
                    <div
                      key={i}
                      className={`whitespace-pre-wrap font-mono text-[11px] leading-[1.5] ${
                        entry.includes("✗") || entry.includes("WARNING") || entry.includes("ERROR") || entry.includes("failed")
                          ? "text-red-400"
                          : entry.includes("✓") || entry.includes("▶") || entry.includes("✅")
                            ? "text-emerald-400"
                            : "text-muted-foreground"
                      }`}
                    >
                      {entry}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/components/robot-dashboard.tsx
git commit -m "feat(frontend): refactor dashboard to two-column layout, remove ConnectPanel"
```

---

### Task 4: Refactor Teleop Page to Use Context

**Files:**
- Modify: `frontend/components/teleop/teleop-page.tsx`

- [ ] **Step 1: Replace `frontend/components/teleop/teleop-page.tsx`**

Remove the connection header, use context instead of direct `useTeleop()` call:

```tsx
"use client";

import { useCallback, useState } from "react";
import { useRobotConnection } from "@/contexts/robot-connection";
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
  const [speedScale, setSpeedScale] = useState(1.0);
  const [chatEntries, setChatEntries] = useState<ChatEntry[]>([]);

  const { status, cameras, isConnected, sendCommand } = useRobotConnection();

  const addChatEntry = useCallback((kind: "speech" | "listen", text: string) => {
    const time = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    setChatEntries((prev) => [...prev, { id: ++chatIdCounter, time, kind, text }]);
  }, []);

  return (
    <div className="flex flex-col h-dvh bg-background text-foreground overflow-hidden">
      <NavBar />

      {/* Status bar */}
      <div className="border-b border-border px-4 py-1.5 shrink-0">
        <StatusBar status={status} isConnected={isConnected} />
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

- [ ] **Step 2: Commit**

```bash
git add frontend/components/teleop/teleop-page.tsx
git commit -m "feat(frontend): refactor teleop page to use shared connection context"
```

---

### Task 5: Delete ConnectPanel + Build Verification

**Files:**
- Delete: `frontend/components/connect-panel.tsx`

- [ ] **Step 1: Delete the ConnectPanel file**

```bash
rm frontend/components/connect-panel.tsx
```

- [ ] **Step 2: Verify no remaining imports of ConnectPanel**

Search for any leftover imports:

```bash
grep -r "connect-panel" frontend/
```

Expected: No results (the import was removed from robot-dashboard.tsx in Task 3).

- [ ] **Step 3: Run the frontend build**

```bash
cd frontend && pnpm build
```

Expected: Build succeeds.

- [ ] **Step 4: Commit**

```bash
git add -u
git commit -m "refactor(frontend): remove unused ConnectPanel component"
```

- [ ] **Step 5: Fix any build issues if needed**

If build fails, fix and commit:

```bash
git add -u
git commit -m "fix(frontend): address build issues from layout refactor"
```
