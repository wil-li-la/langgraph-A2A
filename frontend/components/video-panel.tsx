import { useState } from "react"
import type { RobotTaskData } from "@/lib/mock-data"
import { API_BASE } from "@/lib/api"

interface VideoPanelProps {
  data: RobotTaskData
}

export function VideoPanel({ data }: VideoPanelProps) {
  const [streamMode, setStreamMode] = useState<"rgb" | "depth" | "mix">("rgb")

  return (
    <div className="flex h-full flex-col gap-3">
      <div className="flex items-center justify-between">
        <h2 className="font-mono text-sm font-medium tracking-wide text-foreground">
          VIDEO STREAMING
        </h2>
        <div className="flex rounded-md border border-border p-0.5">
          <button
            onClick={() => setStreamMode("rgb")}
            className={`px-2 py-0.5 font-mono text-[10px] rounded-sm transition-colors ${
              streamMode === "rgb"
                ? "bg-foreground text-background"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            RGB
          </button>
          <button
            onClick={() => setStreamMode("depth")}
            className={`px-2 py-0.5 font-mono text-[10px] rounded-sm transition-colors ${
              streamMode === "depth"
                ? "bg-foreground text-background"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            DEPTH
          </button>
          <button
            onClick={() => setStreamMode("mix")}
            className={`px-2 py-0.5 font-mono text-[10px] rounded-sm transition-colors ${
              streamMode === "mix"
                ? "bg-foreground text-background"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            MIX
          </button>
        </div>
      </div>
      
      <div className="grid flex-1 grid-cols-2 gap-3">
        {/* Gripper view */}
        <div
          className={`relative flex flex-col overflow-hidden rounded-md border ${
            data.videoStreams.gripper.active
              ? "border-foreground/20"
              : "border-border"
          }`}
        >
          <div className="flex flex-1 items-center justify-center bg-muted/20 overflow-hidden">
            {data.videoStreams.gripper.active ? (
              <img
                src={`${API_BASE}/api/stream/d405/${streamMode}`}
                alt="Gripper Camera Stream"
                className="h-full w-full object-cover"
                key={`d405-${streamMode}`}
                onError={(e) => {
                  (e.target as HTMLImageElement).style.display = 'none';
                }}
              />
            ) : (
              <div className="text-center">
                <svg
                  className="mx-auto h-8 w-8 text-muted-foreground/30"
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
            )}
          </div>
          <div className="flex items-center justify-between border-t border-border px-3 py-2">
            <span className="font-mono text-xs text-muted-foreground">Gripper (d405)</span>
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

        {/* Head view (d435if) */}
        <div
          className={`relative flex flex-col overflow-hidden rounded-md border ${
            data.videoStreams?.map?.active
              ? "border-foreground/20"
              : "border-border"
          }`}
        >
          <div className="flex flex-1 items-center justify-center bg-muted/20 overflow-hidden">
            {data.videoStreams?.map?.active ? (
              <img
                src={`${API_BASE}/api/stream/d435if/${streamMode}`}
                alt="Head Camera Stream"
                className="h-full w-full object-cover"
                key={`d435if-${streamMode}`}
                onError={(e) => {
                  (e.target as HTMLImageElement).style.display = 'none';
                }}
              />
            ) : (
              <div className="text-center">
                <svg
                  className="mx-auto h-8 w-8 text-muted-foreground/30"
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
            )}
          </div>
          <div className="flex items-center justify-between border-t border-border px-3 py-2">
            <span className="font-mono text-xs text-muted-foreground">Head (d435if)</span>
            {data.videoStreams?.map?.active && (
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
