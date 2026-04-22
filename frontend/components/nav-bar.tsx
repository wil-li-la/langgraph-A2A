"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { useRobotConnection } from "@/contexts/robot-connection"

const NAV_ITEMS = [
  { href: "/", label: "Dashboard" },
  { href: "/teleop", label: "Teleop" },
] as const;

interface NavBarProps {
  workflowState?: "idle" | "running" | "paused"
}

export function NavBar({ workflowState = "idle" }: NavBarProps) {
  const pathname = usePathname();
  const { robotHost, setRobotHost, isConnected, handleConnect, disconnect } = useRobotConnection();
  const teleopLocked = workflowState === "running"

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
        </div>

        {/* Nav links */}
        <nav className="ml-auto flex gap-1">
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
