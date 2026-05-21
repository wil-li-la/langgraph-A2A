"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { useRobotConnection } from "@/contexts/robot-connection"
import { ModeToggle } from "@/components/mode-toggle"
import type { WorkflowState } from "@/hooks/use-workflow"
import { useNavStatus } from "@/contexts/nav-status"
import { navStatusColor } from "@/lib/nav-status"

const NAV_ITEMS = [
  { href: "/", label: "Dashboard" },
  { href: "/teleop", label: "Teleop" },
  { href: "/nav", label: "Map Nav" },
  { href: "/recon", label: "3D Recon" },
  { href: "/cameras", label: "Room Cameras" },
] as const;

interface NavBarProps {
  workflowState?: WorkflowState
}

export function NavBar({ workflowState = "idle" }: NavBarProps) {
  const pathname = usePathname();
  const { robotHost, setRobotHost, isConnected, connectionError, handleConnect, disconnect } = useRobotConnection();
  const { pose, task } = useNavStatus()
  const navState = task?.state ?? "idle"
  const navLabel =
    navState === "done" ? (task?.status ?? "?") : navState
  const navColor = navStatusColor(task?.status ?? null, navState)
  const teleopLocked = workflowState === "running"

  const errorLabel =
    connectionError === "public_unreachable"
      ? "Public IP unreachable"
      : connectionError === "robot_unreachable"
        ? "Robot offline"
        : null

  return (
    <header className="border-b border-border bg-background px-3 py-2 shrink-0 sm:px-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <h1 className="font-mono text-base font-medium tracking-tight text-foreground whitespace-nowrap sm:text-lg">
          Robot Task Dashboard
        </h1>

        {/* Robot connection */}
        <div className="flex flex-wrap items-center gap-2">
          <input
            placeholder="Robot IP"
            value={robotHost}
            onChange={(e) => setRobotHost(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleConnect()}
            className="w-36 rounded-md border border-border bg-background px-3 py-1.5 font-mono text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring sm:w-44"
          />
          <button
            onClick={isConnected ? disconnect : handleConnect}
            className={`rounded-md border px-3 py-1.5 font-mono text-sm font-medium transition-colors ${
              isConnected
                ? "border-border text-muted-foreground hover:bg-foreground/10"
                : "border-foreground/30 bg-foreground/10 text-foreground hover:bg-foreground/20"
            }`}
          >
            {isConnected ? "Disconnect" : "Connect"}
          </button>
          {isConnected && (
            <span className="flex items-center gap-1.5 font-mono text-sm">
              <span className="relative flex h-2 w-2">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-foreground/50" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-foreground" />
              </span>
              <span className="text-foreground font-medium">Stretch 3</span>
            </span>
          )}
          {!isConnected && errorLabel && (
            <span
              title={
                connectionError === "public_unreachable"
                  ? "Cannot reach the public IP — check network / port forwarding"
                  : "Public IP reachable, but the robot WS server isn't responding"
              }
              className={`flex items-center gap-1.5 font-mono text-sm ${
                connectionError === "public_unreachable"
                  ? "text-destructive"
                  : "text-amber-500 dark:text-amber-400"
              }`}
            >
              <span className="relative inline-flex h-2 w-2 rounded-full bg-current" />
              <span className="font-medium">{errorLabel}</span>
            </span>
          )}
        </div>

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

        {/* Mode toggle (Scripted | Agentic). Locked while a workflow is running so
            the active context isn't yanked out from under it. */}
        <div className="ml-auto flex items-center gap-3">
          <ModeToggle
            disabled={workflowState !== "idle"}
            disabledReason="Stop the workflow to switch modes"
          />
        </div>

        {/* Nav links */}
        <nav className="flex gap-1">
          {NAV_ITEMS.map((item) => {
            const isActive = pathname === item.href;
            const locked = item.href === "/teleop" && teleopLocked

            if (locked) {
              return (
                <span
                  key={item.href}
                  title="Stop workflow to access teleop"
                  className="cursor-not-allowed rounded-md px-4 py-1.5 font-mono text-sm font-medium text-muted-foreground/40"
                >
                  {item.label} 🔒
                </span>
              )
            }

            return (
              <Link
                key={item.href}
                href={item.href}
                className={`rounded-md px-4 py-1.5 font-mono text-sm font-medium transition-colors ${
                  isActive
                    ? "bg-foreground/15 text-foreground"
                    : "text-muted-foreground hover:bg-foreground/10 hover:text-foreground"
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
