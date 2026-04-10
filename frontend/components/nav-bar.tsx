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
    <header className="border-b border-border bg-background px-4 py-2 shrink-0">
      <div className="flex items-center gap-3">
        <h1 className="font-mono text-sm font-medium tracking-tight text-foreground whitespace-nowrap">
          Robot Task Dashboard
        </h1>

        {/* Robot connection */}
        <div className="flex items-center gap-2">
          <input
            placeholder="Robot IP"
            value={robotHost}
            onChange={(e) => setRobotHost(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleConnect()}
            className="w-44 rounded-md border border-border bg-background px-3 py-1.5 font-mono text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
          />
          <button
            onClick={isConnected ? disconnect : handleConnect}
            className={`rounded-md border px-3 py-1.5 font-mono text-xs font-medium transition-colors ${
              isConnected
                ? "border-border text-muted-foreground hover:bg-foreground/10"
                : "border-foreground/30 bg-foreground/10 text-foreground hover:bg-foreground/20"
            }`}
          >
            {isConnected ? "Disconnect" : "Connect"}
          </button>
          {isConnected && (
            <span className="flex items-center gap-1.5 font-mono text-xs">
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
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`rounded-md px-4 py-1.5 font-mono text-xs font-medium transition-colors ${
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
