"use client"

import { useState } from "react"
import type { ExecutionResult } from "@/lib/api"

type Mode = "manual" | "auto"

interface ModeToggleProps {
  isExecuting?: boolean
  onStreamRun?: (instruction: string) => Promise<ExecutionResult | null>
  onStreamStop?: () => void
  onExecutionResult?: (result: ExecutionResult) => void
}

export function ModeToggle({ isExecuting = false, onStreamRun, onStreamStop, onExecutionResult }: ModeToggleProps) {
  const [mode, setMode] = useState<Mode>("auto")
  const [instruction, setInstruction] = useState("")
  const [lastResult, setLastResult] = useState<string | null>(null)

  const handleRun = async () => {
    if (!instruction.trim() || isExecuting) return
    setLastResult(null)

    try {
      if (onStreamRun) {
        const result = await onStreamRun(instruction.trim())
        if (result) {
          setLastResult(result.task_status)
          onExecutionResult?.(result)
        } else {
          setLastResult("Error")
        }
      }
    } catch (err) {
      setLastResult(err instanceof Error ? err.message : "Error")
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h2 className="font-mono text-lg font-medium tracking-wide text-foreground">
          OPERATION MODE
        </h2>

        {/* Toggle pill */}
        <div className="flex rounded-full border border-border bg-background/50 p-0.5">
          <button
            onClick={() => setMode("manual")}
            className={`rounded-full px-3 py-1 font-mono text-base font-medium transition-colors ${
              mode === "manual"
                ? "bg-foreground text-background"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            MANUAL
          </button>
          <button
            onClick={() => setMode("auto")}
            className={`rounded-full px-3 py-1 font-mono text-base font-medium transition-colors ${
              mode === "auto"
                ? "bg-foreground text-background"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            AUTO
          </button>
        </div>
      </div>

      {mode === "manual" ? (
        /* Manual mode: instruction input + run button */
        <div className="flex flex-col gap-2">
          <div className="flex gap-2">
            <input
              type="text"
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleRun()}
              placeholder="請將阿斯匹靈送給張小明 …"
              className="flex-1 rounded-md border border-border bg-background px-3 py-2 font-mono text-base text-foreground placeholder:text-muted-foreground/40 focus:outline-none focus:ring-1 focus:ring-foreground/20"
              disabled={isExecuting}
            />
            {isExecuting ? (
              <button
                onClick={() => onStreamStop?.()}
                className="rounded-md border border-red-500/50 bg-red-500/10 px-4 py-2 font-mono text-base font-medium text-red-500 transition-colors hover:bg-red-500/20"
              >
                <span className="flex items-center gap-1.5">
                  <svg className="h-3 w-3 animate-spin" viewBox="0 0 24 24" fill="none">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                  STOP
                </span>
              </button>
            ) : (
              <button
                onClick={handleRun}
                disabled={!instruction.trim()}
                className="rounded-md border border-border bg-foreground px-4 py-2 font-mono text-base font-medium text-background transition-opacity hover:opacity-90 disabled:opacity-30"
              >
                ▶ RUN
              </button>
            )}
          </div>

          {/* Last result feedback */}
          {lastResult && (
            <div className="rounded-md border border-border bg-background/50 px-3 py-2">
              <span className="font-mono text-base text-muted-foreground">RESULT: </span>
              <span className="font-mono text-base text-foreground">{lastResult}</span>
            </div>
          )}
        </div>
      ) : (
        /* Auto mode: A2A listening indicator */
        <div className="flex items-center gap-2 rounded-md border border-dashed border-border bg-background/50 px-3 py-3">
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-foreground/30" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-foreground/60" />
          </span>
          <span className="font-mono text-base text-muted-foreground">
            Listening for A2A requests …
          </span>
        </div>
      )}
    </div>
  )
}
