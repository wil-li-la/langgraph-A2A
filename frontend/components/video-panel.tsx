"use client"

import { useEffect, useState } from "react"
import { CameraView, type CameraDetectionOverlay } from "@/components/teleop/camera-view"
import { useRobotConnection } from "@/contexts/robot-connection"
import {
  fetchLatestDetections,
  subscribeDetections,
  type DetectionEvent,
} from "@/lib/api"

// Maps the backend camera key ("head" / "arm") to the dashboard's
// internal CameraName values used by useRobotConnection().
const BACKEND_CAMERA_TO_NAME: Record<string, "realsense" | "gripper"> = {
  head: "realsense",
  arm: "gripper",
  gripper: "gripper",
  wrist: "gripper",
}

// How long a detection overlay stays visible after it arrives. The VLM
// fires at most a few times per task so we let boxes linger; the operator
// can confirm them visually before they fade.
const OVERLAY_TTL_MS = 8000

export function VideoPanel() {
  const { cameras } = useRobotConnection()
  const [detectionByCamera, setDetectionByCamera] = useState<
    Record<string, CameraDetectionOverlay>
  >({})

  useEffect(() => {
    let cancelled = false
    const ttlTimers: Record<string, ReturnType<typeof setTimeout>> = {}

    const applyEvent = (evt: DetectionEvent) => {
      if (cancelled) return
      const cam = BACKEND_CAMERA_TO_NAME[evt.camera]
      if (!cam) return
      const overlay: CameraDetectionOverlay = {
        imageW: evt.image_w,
        imageH: evt.image_h,
        boxes: evt.detections,
        query: evt.query,
        ts: evt.ts,
      }
      setDetectionByCamera((prev) => ({ ...prev, [cam]: overlay }))

      if (ttlTimers[cam]) clearTimeout(ttlTimers[cam])
      ttlTimers[cam] = setTimeout(() => {
        if (cancelled) return
        setDetectionByCamera((prev) => {
          const next = { ...prev }
          delete next[cam]
          return next
        })
      }, OVERLAY_TTL_MS)
    }

    // Initial snapshot — paint whatever the backend has cached.
    fetchLatestDetections().then((latest) => {
      Object.values(latest).forEach(applyEvent)
    })

    // Live stream — every new VLM result.
    const unsubscribe = subscribeDetections(applyEvent)

    return () => {
      cancelled = true
      unsubscribe()
      Object.values(ttlTimers).forEach((t) => clearTimeout(t))
    }
  }, [])

  return (
    <div className="flex h-full flex-col gap-2">
      <div className="flex items-center justify-between shrink-0">
        <h2 className="font-mono text-lg font-medium tracking-wide text-foreground">
          VIDEO
        </h2>
      </div>

      <div className="grid flex-1 grid-cols-1 gap-2 min-h-0 sm:grid-cols-2">
        {/* Head view (realsense d435if) */}
        <div className="relative flex flex-col overflow-hidden rounded-md border border-border min-h-0">
          <div className="flex-1 min-h-0">
            <CameraView
              name="realsense"
              src={cameras.realsense}
              detections={detectionByCamera.realsense ?? null}
            />
          </div>
          <div className="flex items-center justify-between border-t border-border px-2 py-1 shrink-0">
            <span className="font-mono text-sm text-muted-foreground">Head</span>
            <div className="flex items-center gap-2">
              {detectionByCamera.realsense && (
                <span
                  className="font-mono text-xs text-cyan-400"
                  data-testid="overlay-tag-head"
                  title={detectionByCamera.realsense.query}
                >
                  {detectionByCamera.realsense.boxes.length} BOX
                </span>
              )}
              {cameras.realsense && (
                <div className="flex items-center gap-1">
                  <span className="relative flex h-1.5 w-1.5">
                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-foreground/50" />
                    <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-foreground" />
                  </span>
                  <span className="font-mono text-sm text-foreground">LIVE</span>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Gripper view (d405) */}
        <div className="relative flex flex-col overflow-hidden rounded-md border border-border min-h-0">
          <div className="flex-1 min-h-0">
            <CameraView
              name="gripper"
              src={cameras.gripper}
              detections={detectionByCamera.gripper ?? null}
            />
          </div>
          <div className="flex items-center justify-between border-t border-border px-2 py-1 shrink-0">
            <span className="font-mono text-sm text-muted-foreground">Gripper</span>
            <div className="flex items-center gap-2">
              {detectionByCamera.gripper && (
                <span
                  className="font-mono text-xs text-cyan-400"
                  data-testid="overlay-tag-gripper"
                  title={detectionByCamera.gripper.query}
                >
                  {detectionByCamera.gripper.boxes.length} BOX
                </span>
              )}
              {cameras.gripper && (
                <div className="flex items-center gap-1">
                  <span className="relative flex h-1.5 w-1.5">
                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-foreground/50" />
                    <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-foreground" />
                  </span>
                  <span className="font-mono text-sm text-foreground">LIVE</span>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
