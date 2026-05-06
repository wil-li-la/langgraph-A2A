"use client"

import { useEffect, useMemo, useState } from "react"

const ROOM_CAMERAS_URL = process.env.NEXT_PUBLIC_ROOM_CAMERAS_URL?.replace(/\/$/, "") || ""

const SIDES = ["left", "right"] as const
const CAMS_PER_SIDE = 8
const POLL_INTERVAL_MS = 400 // 2.5 fps per tile — plenty for situational awareness

type Side = (typeof SIDES)[number]

interface CamId {
  side: Side
  idx: number
}

const ALL_CAMERAS: CamId[] = SIDES.flatMap((side) =>
  Array.from({ length: CAMS_PER_SIDE }, (_, idx) => ({ side, idx })),
)

function snapUrl(cam: CamId, tick: number): string {
  return `${ROOM_CAMERAS_URL}/snap/${cam.side}/${cam.idx}?t=${tick}`
}

function streamUrl(cam: CamId, nonce: number): string {
  return `${ROOM_CAMERAS_URL}/cam/${cam.side}/${cam.idx}?n=${nonce}`
}

function camLabel(cam: CamId): string {
  return `${cam.side}_${cam.idx}`
}

// One tile: polls /snap on a timer. Short-lived requests release the
// connection slot quickly, so all 16 tiles can refresh concurrently
// without hitting the browser's 6-per-origin limit.
function GridTile({ cam, onFocus }: { cam: CamId; onFocus: () => void }) {
  const [tick, setTick] = useState(() => Date.now())
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    const id = setInterval(() => setTick(Date.now()), POLL_INTERVAL_MS)
    return () => clearInterval(id)
  }, [])

  return (
    <button
      onClick={onFocus}
      className="group relative flex flex-col overflow-hidden rounded-md border border-border bg-black/60 text-left transition-colors hover:border-foreground/40"
      title={`Click to focus ${camLabel(cam)}`}
    >
      <div className="relative aspect-video w-full">
        <img
          src={snapUrl(cam, tick)}
          alt={camLabel(cam)}
          onLoad={() => setLoaded(true)}
          onError={() => setLoaded(false)}
          className="h-full w-full object-cover"
        />
        {!loaded && (
          <div className="absolute inset-0 flex items-center justify-center font-mono text-xs text-muted-foreground">
            NO SIGNAL
          </div>
        )}
      </div>
      <div className="flex items-center justify-between border-t border-border bg-card/80 px-2 py-1">
        <span className="font-mono text-sm text-foreground">{camLabel(cam)}</span>
        <span className="font-mono text-xs text-muted-foreground group-hover:text-foreground">
          ⤢
        </span>
      </div>
    </button>
  )
}

export function RoomCamerasGrid() {
  const [focused, setFocused] = useState<CamId | null>(null)
  const [focusedNonce, setFocusedNonce] = useState(() => Date.now())

  const cameras = useMemo(() => ALL_CAMERAS, [])

  if (!ROOM_CAMERAS_URL) {
    return (
      <div className="flex h-full items-center justify-center p-6">
        <div className="max-w-xl rounded-md border border-amber-500/40 bg-amber-500/5 p-4 font-mono text-sm text-amber-200">
          <div className="mb-2 font-medium">Room-camera bridge URL not configured</div>
          <p className="text-amber-200/80">
            Set <code className="rounded bg-black/30 px-1">NEXT_PUBLIC_ROOM_CAMERAS_URL</code>{" "}
            in <code className="rounded bg-black/30 px-1">frontend/.env.local</code> (dev) or in
            the Cloudflare Pages project env vars (prod) to the host:port the bridge is
            listening on (e.g. <code>http://localhost:9997</code>). See{" "}
            <code className="rounded bg-black/30 px-1">backend/room_cameras/README.md</code>.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2 shrink-0">
        <div className="flex flex-col">
          <h2 className="font-mono text-lg font-medium tracking-wide text-foreground">
            ROOM CAMERAS
          </h2>
          <span className="font-mono text-xs text-muted-foreground">
            via {ROOM_CAMERAS_URL}
          </span>
        </div>
        {focused && (
          <button
            onClick={() => setFocusedNonce(Date.now())}
            className="rounded border border-border px-2 py-1 font-mono text-sm text-muted-foreground transition-colors hover:bg-foreground/10 hover:text-foreground"
            title="Force the focused stream to reconnect"
          >
            ↻ RECONNECT
          </button>
        )}
      </div>

      {focused ? (
        <div className="flex flex-1 min-h-0 flex-col gap-2">
          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={() => setFocused(null)}
              className="rounded border border-border px-2 py-1 font-mono text-sm text-muted-foreground hover:bg-foreground/10 hover:text-foreground"
            >
              ← BACK
            </button>
            <span className="font-mono text-sm text-foreground">{camLabel(focused)}</span>
            <span className="font-mono text-xs text-muted-foreground">— full-rate MJPEG</span>
          </div>
          <div className="flex-1 min-h-0 overflow-hidden rounded-md border border-border bg-black/60">
            <img
              src={streamUrl(focused, focusedNonce)}
              alt={camLabel(focused)}
              className="h-full w-full object-contain"
            />
          </div>
        </div>
      ) : (
        <div className="grid flex-1 min-h-0 grid-cols-2 gap-2 overflow-y-auto sm:grid-cols-3 lg:grid-cols-4">
          {cameras.map((cam) => (
            <GridTile
              key={camLabel(cam)}
              cam={cam}
              onFocus={() => {
                setFocused(cam)
                setFocusedNonce(Date.now())
              }}
            />
          ))}
        </div>
      )}
    </div>
  )
}
