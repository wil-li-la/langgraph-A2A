"use client"

import { useEffect, useState } from "react"
import type { ExecutionResult } from "@/lib/api"
import type { WorkflowState } from "@/hooks/use-workflow"

type Mode = "manual" | "auto"

interface WorkflowControlsProps {
  workflowState: WorkflowState
  pausedNodeId: string | null
  pauseReason: string | null
  activeNodeId: string | null
  onStart: (instruction: string) => Promise<ExecutionResult | null>
  onStop: () => Promise<void>
  onResume: (nodeId: string) => Promise<ExecutionResult | null>
  onInstructionChange: (text: string) => void
  instruction: string
}

export function WorkflowControls({
  workflowState,
  pausedNodeId,
  pauseReason,
  activeNodeId,
  onStart,
  onStop,
  onResume,
  onInstructionChange,
  instruction,
}: WorkflowControlsProps) {
  const [mode, setMode] = useState<Mode>("manual")
  const [stopPending, setStopPending] = useState(false)

  const handleStart = async () => {
    if (!instruction.trim() || workflowState !== "idle") return
    await onStart(instruction.trim())
  }

  const handleStop = async () => {
    if (workflowState !== "running") return
    setStopPending(true)
    try {
      await onStop()
    } finally {
      // stopPending clears when workflowState transitions to paused/idle
    }
  }

  const handleResume = async () => {
    if (workflowState !== "paused" || !pausedNodeId) return
    await onResume(pausedNodeId)
  }

  // Clear stopPending once state has actually transitioned away from running
  useEffect(() => {
    if (stopPending && workflowState !== "running") {
      setStopPending(false)
    }
  }, [stopPending, workflowState])

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h2 className="font-mono text-lg font-medium tracking-wide text-foreground">
          WORKFLOW
        </h2>

        {/* Mode toggle — disabled while not idle */}
        <div className="flex rounded-full border border-border bg-background/50 p-0.5">
          <button
            onClick={() => workflowState === "idle" && setMode("manual")}
            disabled={workflowState !== "idle"}
            className={`rounded-full px-3 py-1 font-mono text-sm font-medium transition-colors ${
              mode === "manual" ? "bg-foreground text-background" : "text-muted-foreground hover:text-foreground"
            } disabled:opacity-50 disabled:cursor-not-allowed`}
          >
            MANUAL
          </button>
          <button
            onClick={() => workflowState === "idle" && setMode("auto")}
            disabled={workflowState !== "idle"}
            className={`rounded-full px-3 py-1 font-mono text-sm font-medium transition-colors ${
              mode === "auto" ? "bg-foreground text-background" : "text-muted-foreground hover:text-foreground"
            } disabled:opacity-50 disabled:cursor-not-allowed`}
          >
            AUTO
          </button>
        </div>
      </div>

      {workflowState === "idle" && mode === "manual" && (
        <div className="flex flex-col gap-2">
          <div className="flex gap-2">
            <input
              type="text"
              value={instruction}
              onChange={(e) => onInstructionChange(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleStart()}
              placeholder="請將阿斯匹靈送給張小明 …"
              className="min-w-0 flex-1 rounded-md border border-border bg-background px-3 py-2 font-mono text-sm text-foreground placeholder:text-muted-foreground/40 focus:outline-none focus:ring-1 focus:ring-foreground/20"
            />
            <button
              onClick={handleStart}
              disabled={!instruction.trim()}
              className="shrink-0 rounded-md border border-border bg-foreground px-4 py-2 font-mono text-sm font-medium text-background transition-opacity hover:opacity-90 disabled:opacity-30"
            >
              ▶ START
            </button>
          </div>
          <p className="font-mono text-xs text-muted-foreground/60">
            or click any node below to start from there
          </p>
        </div>
      )}

      {workflowState === "idle" && mode === "auto" && (
        <div className="flex items-center gap-2 rounded-md border border-dashed border-border bg-background/50 px-3 py-3">
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-foreground/30" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-foreground/60" />
          </span>
          <span className="font-mono text-sm text-muted-foreground">
            Listening for A2A requests …
          </span>
        </div>
      )}

      {workflowState === "running" && (
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2 font-mono text-sm">
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-sky-400/50" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-sky-400" />
            </span>
            <span className="text-foreground">
              Running {activeNodeId ? `— ${activeNodeId}` : ""}
            </span>
          </div>
          <button
            onClick={handleStop}
            disabled={stopPending}
            className="rounded-md border border-red-500/50 bg-red-500/10 px-4 py-2 font-mono text-sm font-medium text-red-500 transition-colors hover:bg-red-500/20 disabled:cursor-wait disabled:opacity-60"
          >
            {stopPending ? "STOPPING — waiting for current step…" : "■ STOP"}
          </button>
        </div>
      )}

      {workflowState === "paused" && (
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2 font-mono text-sm">
            <span className="h-2 w-2 rounded-full bg-amber-400" />
            <span className="text-foreground">
              Paused{pausedNodeId ? ` at ${pausedNodeId}` : ""}
            </span>
          </div>
          {pauseReason && (
            <p className="font-mono text-xs text-muted-foreground">{pauseReason}</p>
          )}
          <button
            onClick={handleResume}
            disabled={!pausedNodeId}
            className="rounded-md border border-border bg-foreground px-4 py-2 font-mono text-sm font-medium text-background transition-opacity hover:opacity-90 disabled:opacity-30"
          >
            ▶ RESUME {pausedNodeId ? `from ${pausedNodeId}` : ""}
          </button>
          <p className="font-mono text-xs text-muted-foreground/60">
            or click any node to resume from there
          </p>
        </div>
      )}
    </div>
  )
}
