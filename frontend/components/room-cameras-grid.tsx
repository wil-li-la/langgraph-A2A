"use client"

import { useEffect, useRef, useState } from "react"
import { connectWhep, disconnectWhep, type WhepSession } from "@/lib/whep-client"
import Hls from "hls.js"
import { attachHls, detachHls, type HlsSession } from "@/lib/hls-player"

// Room camera grid — 16 fixed Basler ceiling cams in lab ED305, served by
// MediaMTX (see backend/mediamtx/). Two transports, picked by env precedence:
//
// 1. NEXT_PUBLIC_MEDIAMTX_HLS_URL — preferred over a Cloudflare Tunnel (pure
//    HTTP, works through any HTTP-only proxy). ~200-400 ms with HLS-LL.
// 2. NEXT_PUBLIC_MEDIAMTX_WEBRTC_URL — best for LAN (sub-100 ms via WHEP), but
//    WebRTC needs UDP which doesn't traverse Cloudflare Tunnel without TURN.
//
// The legacy fallbacks (cam_bridge h264, direct MJPEG, the :9997 ROS bridge)
// were removed 2026-06-01 — MediaMTX is the sole transport. See ADR-0001.
//
// Whitespace tolerated because Cloudflare Pages' env-var UI doesn't trim,
// and a leading/trailing space silently breaks `${URL}/cam_3/index.m3u8`.
const cleanUrl = (v: string | undefined): string =>
  (v ?? "").trim().replace(/\/$/, "")

const MEDIAMTX_HLS_URL = cleanUrl(process.env.NEXT_PUBLIC_MEDIAMTX_HLS_URL)
const MEDIAMTX_WEBRTC_URL = cleanUrl(process.env.NEXT_PUBLIC_MEDIAMTX_WEBRTC_URL)

// Per-side camera IDs — must match the path names in backend/mediamtx/mediamtx.yml
// (`${side}_cam_<N>`). Right has 8; left has 5 (1, 2, 4, 5, 7) — left's
// cam_3/6/8 aren't wired on its host.
const CAM_IDS_BY_SIDE = {
  right: [3, 6, 8, 10, 12, 13, 15, 16],
  left: [1, 2, 4, 5, 7],
} as const

const STREAM_RETRY_MS = 5000

type Side = "right" | "left"
type Mode = "hls" | "webrtc"

const MODE: Mode = MEDIAMTX_HLS_URL ? "hls" : "webrtc"

interface Cam {
  side: Side
  id: number
  label: string
  // Exactly one of these is populated, depending on MODE.
  hlsUrl?: string
  whepUrl?: string
}

function buildCams(): Cam[] {
  const out: Cam[] = []
  for (const side of ["right", "left"] as Side[]) {
    for (const id of CAM_IDS_BY_SIDE[side]) {
      // MediaMTX paths are namespaced `${side}_cam_<N>` so left + right
      // don't collide when they share IDs.
      const cam: Cam = { side, id, label: `${side}_cam_${id}` }
      if (MODE === "hls") {
        cam.hlsUrl = `${MEDIAMTX_HLS_URL}/${side}_cam_${id}/index.m3u8`
      } else {
        cam.whepUrl = `${MEDIAMTX_WEBRTC_URL}/${side}_cam_${id}/whep`
      }
      out.push(cam)
    }
  }
  return out
}

function camKey(cam: Cam): string {
  return `${cam.side}:${cam.id}`
}

function WhepTile({ cam }: { cam: Cam }) {
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const sessionRef = useRef<WhepSession | null>(null)
  const [errored, setErrored] = useState(false)
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    if (!cam.whepUrl) return
    let cancelled = false
    const ctrl = new AbortController()
    setErrored(false)
    connectWhep(cam.whepUrl, videoRef.current!, ctrl.signal)
      .then((session) => {
        if (cancelled) {
          disconnectWhep(session)
          return
        }
        sessionRef.current = session
        session.pc.addEventListener("connectionstatechange", () => {
          if (
            session.pc.connectionState === "failed" ||
            session.pc.connectionState === "disconnected" ||
            session.pc.connectionState === "closed"
          ) {
            setErrored(true)
            if (retryRef.current) clearTimeout(retryRef.current)
            retryRef.current = setTimeout(() => {
              setErrored(false)
              setAttempt((n) => n + 1)
            }, STREAM_RETRY_MS)
          }
        })
      })
      .catch((err) => {
        if (cancelled || err?.name === "AbortError") return
        setErrored(true)
        if (retryRef.current) clearTimeout(retryRef.current)
        retryRef.current = setTimeout(() => {
          setErrored(false)
          setAttempt((n) => n + 1)
        }, STREAM_RETRY_MS)
      })
    return () => {
      cancelled = true
      ctrl.abort()
      if (retryRef.current) clearTimeout(retryRef.current)
      const session = sessionRef.current
      sessionRef.current = null
      if (session) void disconnectWhep(session)
    }
  }, [cam.whepUrl, attempt])

  return (
    <>
      <video
        ref={videoRef}
        autoPlay
        muted
        playsInline
        className="h-full w-full object-cover"
      />
      {errored && (
        <div className="absolute inset-0 flex items-center justify-center bg-black/60 font-mono text-xs text-muted-foreground">
          NO SIGNAL
        </div>
      )}
    </>
  )
}

function HlsTile({ cam }: { cam: Cam }) {
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const [errored, setErrored] = useState(false)
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    if (!cam.hlsUrl || !videoRef.current) return
    const video = videoRef.current
    let session: HlsSession | null = null
    setErrored(false)
    try {
      session = attachHls(cam.hlsUrl, video)
      if (session.hls) {
        session.hls.on(Hls.Events.ERROR, (_event, data) => {
          // Log every error (including non-fatal) so silent black-screen
          // failures are diagnosable from the browser console.
          // eslint-disable-next-line no-console
          console.warn(
            `[hls ${cam.label}] ${data.type}/${data.details}${data.fatal ? " FATAL" : ""}`,
            data,
          )
          if (data.fatal) {
            setErrored(true)
            if (retryRef.current) clearTimeout(retryRef.current)
            retryRef.current = setTimeout(() => {
              setErrored(false)
              setAttempt((n) => n + 1)
            }, STREAM_RETRY_MS)
          }
        })
        // Stage-by-stage confirmation. If manifest_parsed fires but
        // frag_loaded never does, segment fetches are broken. If
        // frag_loaded fires but buffer_appended never does, MediaSource
        // is rejecting the bytes. Only log each event once per tile to
        // keep the console readable across 8 cams.
        let fragLoggedOnce = false
        let bufferLoggedOnce = false
        session.hls.on(Hls.Events.MANIFEST_PARSED, (_e, data) => {
          // eslint-disable-next-line no-console
          console.log(`[hls ${cam.label}] manifest parsed, ${data.levels.length} level(s)`)
        })
        session.hls.on(Hls.Events.FRAG_LOADED, () => {
          if (fragLoggedOnce) return
          fragLoggedOnce = true
          // eslint-disable-next-line no-console
          console.log(`[hls ${cam.label}] first fragment loaded`)
        })
        session.hls.on(Hls.Events.BUFFER_APPENDED, () => {
          if (bufferLoggedOnce) return
          bufferLoggedOnce = true
          // eslint-disable-next-line no-console
          console.log(`[hls ${cam.label}] first buffer appended → should be playing`)
        })
      }
    } catch {
      setErrored(true)
    }
    return () => {
      if (retryRef.current) clearTimeout(retryRef.current)
      if (session) detachHls(session, video)
    }
  }, [cam.hlsUrl, attempt])

  return (
    <>
      <video
        ref={videoRef}
        autoPlay
        muted
        playsInline
        className="h-full w-full object-cover"
      />
      {errored && (
        <div className="absolute inset-0 flex items-center justify-center bg-black/60 font-mono text-xs text-muted-foreground">
          NO SIGNAL
        </div>
      )}
    </>
  )
}

function StreamTile({ cam, onFocus }: { cam: Cam; onFocus: () => void }) {
  return (
    <button
      onClick={onFocus}
      className="group relative flex flex-col overflow-hidden rounded-md border border-border bg-black/60 text-left transition-colors hover:border-foreground/40"
      title={`Click to focus ${cam.label}`}
    >
      <div className="relative aspect-video w-full">
        {cam.hlsUrl ? <HlsTile cam={cam} /> : <WhepTile cam={cam} />}
      </div>
      <div className="flex items-center justify-between border-t border-border bg-card/80 px-2 py-1">
        <span className="font-mono text-sm text-foreground">{cam.label}</span>
        <span className="font-mono text-xs text-muted-foreground group-hover:text-foreground">
          ⤢
        </span>
      </div>
    </button>
  )
}

function FocusView({
  cam,
  nonce,
  onBack,
}: {
  cam: Cam
  nonce: number
  onBack: () => void
}) {
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const whepRef = useRef<WhepSession | null>(null)
  const hlsRef = useRef<HlsSession | null>(null)

  useEffect(() => {
    const video = videoRef.current
    if (!video) return
    let cancelled = false
    const ctrl = new AbortController()

    if (cam.hlsUrl) {
      try {
        hlsRef.current = attachHls(cam.hlsUrl, video)
      } catch {
        // ignored — focus view shows blank on error
      }
    } else if (cam.whepUrl) {
      connectWhep(cam.whepUrl, video, ctrl.signal)
        .then((session) => {
          if (cancelled) {
            disconnectWhep(session)
            return
          }
          whepRef.current = session
        })
        .catch(() => {
          /* ignore */
        })
    }
    return () => {
      cancelled = true
      ctrl.abort()
      if (hlsRef.current) {
        detachHls(hlsRef.current, video)
        hlsRef.current = null
      }
      if (whepRef.current) {
        void disconnectWhep(whepRef.current)
        whepRef.current = null
      }
    }
  }, [cam.hlsUrl, cam.whepUrl, nonce])

  const formatLabel = cam.hlsUrl ? "HLS-LL" : "WebRTC"

  return (
    <div className="flex flex-1 min-h-0 flex-col gap-2">
      <div className="flex items-center gap-2 shrink-0">
        <button
          onClick={onBack}
          className="rounded border border-border px-2 py-1 font-mono text-sm text-muted-foreground hover:bg-foreground/10 hover:text-foreground"
        >
          ← BACK
        </button>
        <span className="font-mono text-sm text-foreground">{cam.label}</span>
        <span className="font-mono text-xs text-muted-foreground">
          — full-rate {formatLabel}
        </span>
      </div>
      <div className="flex-1 min-h-0 overflow-hidden rounded-md border border-border bg-black/60">
        <video
          ref={videoRef}
          autoPlay
          muted
          playsInline
          controls
          className="h-full w-full object-contain"
        />
      </div>
    </div>
  )
}

export function RoomCamerasGrid() {
  const [focused, setFocused] = useState<Cam | null>(null)
  const [focusedNonce, setFocusedNonce] = useState(() => Date.now())

  // No MediaMTX endpoint configured → show setup help instead of dead tiles.
  if (!MEDIAMTX_HLS_URL && !MEDIAMTX_WEBRTC_URL) {
    return (
      <div className="flex h-full items-center justify-center p-6">
        <div className="max-w-xl rounded-md border border-amber-500/40 bg-amber-500/5 p-4 font-mono text-sm text-amber-200">
          <div className="mb-2 font-medium">No MediaMTX endpoint configured</div>
          <p className="text-amber-200/80">
            Set{" "}
            <code className="rounded bg-black/30 px-1">
              NEXT_PUBLIC_MEDIAMTX_WEBRTC_URL
            </code>{" "}
            (LAN) or{" "}
            <code className="rounded bg-black/30 px-1">
              NEXT_PUBLIC_MEDIAMTX_HLS_URL
            </code>{" "}
            (tunnel) in{" "}
            <code className="rounded bg-black/30 px-1">frontend/.env.local</code>.
            See <code className="rounded bg-black/30 px-1">backend/mediamtx/</code>.
          </p>
        </div>
      </div>
    )
  }

  const cams = buildCams()
  const sourceSummary = MODE === "hls"
    ? `hls: ${MEDIAMTX_HLS_URL}`
    : `webrtc: ${MEDIAMTX_WEBRTC_URL}`

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2 shrink-0">
        <div className="flex flex-col">
          <h2 className="font-mono text-lg font-medium tracking-wide text-foreground">
            ROOM CAMERAS
          </h2>
          <span className="font-mono text-xs text-muted-foreground">
            {MODE} · {sourceSummary}
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
        <FocusView
          cam={focused}
          nonce={focusedNonce}
          onBack={() => setFocused(null)}
        />
      ) : (
        <div className="grid flex-1 min-h-0 grid-cols-2 gap-2 overflow-y-auto sm:grid-cols-3 lg:grid-cols-4">
          {cams.map((cam) => (
            <StreamTile
              key={camKey(cam)}
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
