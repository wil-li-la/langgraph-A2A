"use client"

import type { RobotTaskData } from "@/lib/mock-data"

interface VideoPanelProps {
  data: RobotTaskData
}

export function VideoPanel({ data }: VideoPanelProps) {
  return (
    <div className="flex h-full flex-col gap-3">
      <h2 className="font-mono text-sm font-medium tracking-wide text-foreground">
        VIDEO STREAMING
      </h2>
      <div className="grid flex-1 grid-cols-2 gap-3">
        {/* Gripper view */}
        <div
          className={`relative flex flex-col overflow-hidden rounded-md border ${
            data.videoStreams.gripper.active
              ? "border-foreground/20"
              : "border-border"
          }`}
        >
          <div className="flex flex-1 items-center justify-center bg-muted/20 p-6">
            <div className="text-center">
              <svg
                className={`mx-auto h-8 w-8 ${
                  data.videoStreams.gripper.active
                    ? "text-foreground/60"
                    : "text-muted-foreground/30"
                }`}
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={1}
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M15.75 10.5l4.72-4.72a.75.75 0 011.28.53v11.38a.75.75 0 01-1.28.53l-4.72-4.72M4.5 18.75h9a2.25 2.25 0 002.25-2.25v-9a2.25 2.25 0 00-2.25-2.25h-9A2.25 2.25 0 002.25 7.5v9a2.25 2.25 0 002.25 2.25z"
                />
              </svg>
            </div>
          </div>
          <div className="flex items-center justify-between border-t border-border px-3 py-2">
            <span className="font-mono text-xs text-muted-foreground">Gripper</span>
            {data.videoStreams.gripper.active && (
              <div className="flex items-center gap-1.5">
                <span className="relative flex h-1.5 w-1.5">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-foreground/50" />
                  <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-foreground" />
                </span>
                <span className="font-mono text-[10px] text-foreground">LIVE</span>
              </div>
            )}
          </div>
        </div>

        {/* Map view */}
        <div
          className={`relative flex flex-col overflow-hidden rounded-md border ${
            data.videoStreams.map.active
              ? "border-foreground/20"
              : "border-border"
          }`}
        >
          <div className="flex flex-1 items-center justify-center bg-muted/20 p-6">
            <div className="text-center">
              <svg
                className={`mx-auto h-8 w-8 ${
                  data.videoStreams.map.active
                    ? "text-foreground/60"
                    : "text-muted-foreground/30"
                }`}
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={1}
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M9 6.75V15m6-6v8.25m.503 3.498l4.875-2.437c.381-.19.622-.58.622-1.006V4.82c0-.836-.88-1.38-1.628-1.006l-3.869 1.934c-.317.159-.69.159-1.006 0L9.503 3.252a1.125 1.125 0 00-1.006 0L3.622 5.689C3.24 5.88 3 6.27 3 6.695V19.18c0 .836.88 1.38 1.628 1.006l3.869-1.934c.317-.159.69-.159 1.006 0l4.994 2.497c.317.158.69.158 1.006 0z"
                />
              </svg>
            </div>
          </div>
          <div className="flex items-center justify-between border-t border-border px-3 py-2">
            <span className="font-mono text-xs text-muted-foreground">Map</span>
            {data.videoStreams.map.active && (
              <div className="flex items-center gap-1.5">
                <span className="relative flex h-1.5 w-1.5">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-foreground/50" />
                  <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-foreground" />
                </span>
                <span className="font-mono text-[10px] text-foreground">LIVE</span>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
