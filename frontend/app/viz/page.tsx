"use client"

import { useState } from "react"
import { NavBar } from "@/components/nav-bar"

/**
 * Foxglove Studio embed for nvblox visualization.
 *
 * Backend's foxglove_bridge (port 8765 by default) exposes all ROS2 topics
 * over WebSocket — including nvblox's mesh, ESDF, color/depth pointclouds,
 * costmaps, /tf, and the static map. This page embeds Foxglove Studio in
 * an iframe and points it at that WS.
 *
 * Requires the env var NEXT_PUBLIC_FOXGLOVE_WS_URL to be set, e.g.:
 *   NEXT_PUBLIC_FOXGLOVE_WS_URL=wss://stretch-fg.your-domain.com   (Cloudflare Tunnel)
 *   NEXT_PUBLIC_FOXGLOVE_WS_URL=ws://192.168.1.100:8765            (LAN only)
 *
 * Foxglove Studio source can be either:
 *   - app.foxglove.dev (cloud) — default, no install needed
 *   - a self-hosted Foxglove Studio at NEXT_PUBLIC_FOXGLOVE_STUDIO_URL — fallback
 *     for when app.foxglove.dev refuses iframe embedding (X-Frame-Options).
 */

const WS_URL = process.env.NEXT_PUBLIC_FOXGLOVE_WS_URL ?? ""
const SELF_HOSTED_STUDIO = process.env.NEXT_PUBLIC_FOXGLOVE_STUDIO_URL ?? ""

function buildFoxgloveUrl(useSelfHosted: boolean): string {
  const studio = useSelfHosted && SELF_HOSTED_STUDIO
    ? SELF_HOSTED_STUDIO
    : "https://app.foxglove.dev"
  const params = new URLSearchParams({
    "ds": "foxglove-websocket",
    "ds.url": WS_URL,
  })
  return `${studio}/?${params.toString()}`
}

export default function Page() {
  const [useSelfHosted, setUseSelfHosted] = useState(false)
  const url = WS_URL ? buildFoxgloveUrl(useSelfHosted) : ""

  return (
    <div className="flex min-h-dvh flex-col bg-background lg:h-dvh lg:overflow-hidden">
      <NavBar />
      <main className="flex flex-1 min-h-0 flex-col p-2 lg:p-3">
        <div className="flex flex-1 min-h-0 flex-col rounded-md border border-border bg-card overflow-hidden">
          {!WS_URL ? (
            <ConfigBanner />
          ) : (
            <>
              <div className="flex items-center justify-between border-b border-border px-3 py-2 font-mono text-xs">
                <div>
                  <span className="text-muted-foreground">WebSocket:</span>{" "}
                  {WS_URL}
                </div>
                <div className="flex items-center gap-2">
                  {SELF_HOSTED_STUDIO && (
                    <label className="flex items-center gap-1.5 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={useSelfHosted}
                        onChange={(e) => setUseSelfHosted(e.target.checked)}
                        className="cursor-pointer"
                      />
                      self-host
                    </label>
                  )}
                  <a
                    href={url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-muted-foreground underline hover:text-foreground"
                  >
                    open in new tab
                  </a>
                </div>
              </div>
              <iframe
                src={url}
                className="flex-1 w-full border-0"
                title="Foxglove Studio"
                allow="clipboard-read; clipboard-write"
              />
            </>
          )}
        </div>
      </main>
    </div>
  )
}

function ConfigBanner() {
  return (
    <div className="flex h-full items-center justify-center p-6 text-center">
      <div className="max-w-xl">
        <h2 className="font-mono text-lg font-medium text-foreground">
          Foxglove WebSocket URL not configured
        </h2>
        <p className="mt-3 font-mono text-sm text-muted-foreground">
          Set <code className="rounded bg-foreground/10 px-1.5 py-0.5">NEXT_PUBLIC_FOXGLOVE_WS_URL</code>{" "}
          to point at the lab box's <code className="rounded bg-foreground/10 px-1.5 py-0.5">foxglove_bridge</code>{" "}
          (default port <code className="rounded bg-foreground/10 px-1.5 py-0.5">8765</code>).
        </p>
        <pre className="mt-4 whitespace-pre-wrap rounded bg-foreground/10 p-3 text-left text-xs">
{`# .env.local
NEXT_PUBLIC_FOXGLOVE_WS_URL=wss://stretch-fg.your-domain.com   # via Cloudflare Tunnel
# or
NEXT_PUBLIC_FOXGLOVE_WS_URL=ws://192.168.1.100:8765           # LAN only (no SSL)`}
        </pre>
        <p className="mt-4 text-xs text-muted-foreground">
          See <code>backend/nav_bridge/README.md</code> for the lab-side launch
          + tunnel setup.
        </p>
      </div>
    </div>
  )
}
