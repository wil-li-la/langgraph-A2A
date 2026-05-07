"use client"

import { useState } from "react"
import { NavBar } from "@/components/nav-bar"

/**
 * Lichtblick (open-source Foxglove Studio fork) embed for nvblox 3D
 * inspection.
 *
 * Backend's rosbridge_websocket (port 9090 by default, spawned by
 * backend/nav_bridge/launch/nav.launch.py) exposes all ROS2 topics —
 * nvblox mesh + ESDF, /tf, costmaps, etc. This page embeds Lichtblick
 * in an iframe pointing at that WS via Lichtblick's "Rosbridge
 * (ROS 1 & 2)" data source.
 *
 * The dashboard's /nav page handles 2D layers (nvblox occupancy,
 * costmaps, planned path) natively — we only use Lichtblick here for
 * the 3D mesh / point cloud panels that would be expensive to roll
 * ourselves.
 *
 * Requires the env var NEXT_PUBLIC_ROSBRIDGE_WS_URL to be set, e.g.:
 *   NEXT_PUBLIC_ROSBRIDGE_WS_URL=wss://stretch-fg.your-domain.com   (Cloudflare Tunnel)
 *   NEXT_PUBLIC_ROSBRIDGE_WS_URL=ws://192.168.1.100:9090            (LAN only)
 *
 * The iframe target can be either:
 *   - https://app.lichtblick.io  (cloud, default — but X-Frame-Options
 *     blocks embedding of the public app, so see below)
 *   - a self-hosted Lichtblick at NEXT_PUBLIC_FOXGLOVE_STUDIO_URL,
 *     e.g. http://localhost:8000 from a docker container. This is the
 *     reliable LAN path:
 *         docker run -d -p 8000:8080 ghcr.io/lichtblick-suite/lichtblick:latest
 */

const WS_URL = process.env.NEXT_PUBLIC_ROSBRIDGE_WS_URL ?? ""
const SELF_HOSTED_STUDIO = process.env.NEXT_PUBLIC_FOXGLOVE_STUDIO_URL ?? ""

// Default to self-hosted whenever:
//   - the WS URL is insecure (ws://) — public Lichtblick is HTTPS and
//     blocks mixed-content, so the cloud app CAN'T connect, and
//   - a self-hosted URL is configured.
const WS_INSECURE = WS_URL.startsWith("ws://")
const DEFAULT_SELF_HOSTED = Boolean(SELF_HOSTED_STUDIO) && WS_INSECURE

function buildStudioUrl(useSelfHosted: boolean): string {
  const studio = useSelfHosted && SELF_HOSTED_STUDIO
    ? SELF_HOSTED_STUDIO
    : "https://app.lichtblick.io"
  // Lichtblick / Foxglove rosbridge data source key
  const params = new URLSearchParams({
    "ds": "rosbridge-websocket",
    "ds.url": WS_URL,
  })
  return `${studio}/?${params.toString()}`
}

export default function Page() {
  const [useSelfHosted, setUseSelfHosted] = useState(DEFAULT_SELF_HOSTED)
  const url = WS_URL ? buildStudioUrl(useSelfHosted) : ""

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
                title="Lichtblick"
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
          rosbridge WebSocket URL not configured
        </h2>
        <p className="mt-3 font-mono text-sm text-muted-foreground">
          Set <code className="rounded bg-foreground/10 px-1.5 py-0.5">NEXT_PUBLIC_ROSBRIDGE_WS_URL</code>{" "}
          to point at the lab box's <code className="rounded bg-foreground/10 px-1.5 py-0.5">rosbridge_websocket</code>{" "}
          (default port <code className="rounded bg-foreground/10 px-1.5 py-0.5">9090</code>).
        </p>
        <pre className="mt-4 whitespace-pre-wrap rounded bg-foreground/10 p-3 text-left text-xs">
{`# .env.local
NEXT_PUBLIC_ROSBRIDGE_WS_URL=wss://stretch-fg.your-domain.com   # via Cloudflare Tunnel
# or
NEXT_PUBLIC_ROSBRIDGE_WS_URL=ws://192.168.1.100:9090           # LAN only (no SSL)`}
        </pre>
        <p className="mt-4 text-xs text-muted-foreground">
          See <code>backend/nav_bridge/README.md</code> for the lab-side launch
          + tunnel setup.
        </p>
      </div>
    </div>
  )
}
