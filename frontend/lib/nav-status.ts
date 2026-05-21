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
