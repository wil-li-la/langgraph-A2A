"use client"

import { useState } from "react"
import { useAgent } from "@/contexts/agent-context"

const EXAMPLE_TASKS = [
  "Deliver aspirin to 張小明",
  "Greet the patient and verify identity",
  "Fetch my water bottle from the pharmacy",
] as const

export function AgentControls() {
  const {
    info,
    isInfoLoading,
    runState,
    task,
    setTask,
    budget,
    setBudget,
    startAgent,
    stopAgent,
    clear,
  } = useAgent()

  const [showAdvanced, setShowAdvanced] = useState(false)

  const isRunning = runState === "running"
  const llmAvailable = !!info?.available

  const handleStart = () => {
    if (!task.trim() || isRunning || !llmAvailable) return
    void startAgent(task.trim())
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h2 className="font-mono text-lg font-medium tracking-wide text-foreground">
          AGENT
        </h2>
        <span className="font-mono text-xs text-muted-foreground">
          {isInfoLoading
            ? "checking…"
            : info?.available
              ? `${info.llm_provider}/${info.llm_model}`
              : "LLM unavailable"}
        </span>
      </div>

      {/* Availability hint */}
      {!isInfoLoading && info && !info.available && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 font-mono text-xs text-amber-300">
          {info.reason ?? "Agent not configured."}
        </div>
      )}

      {/* Task input */}
      <div className="flex flex-col gap-2">
        <textarea
          value={task}
          onChange={(e) => setTask(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleStart()
          }}
          placeholder='e.g. "deliver aspirin to 張小明" or "fetch my water bottle"'
          disabled={isRunning}
          rows={3}
          className="min-w-0 resize-y rounded-md border border-border bg-background px-3 py-2 font-mono text-sm text-foreground placeholder:text-muted-foreground/40 focus:outline-none focus:ring-1 focus:ring-foreground/20 disabled:opacity-60"
        />
        <p className="font-mono text-xs text-muted-foreground/60">
          Cmd/Ctrl+Enter to start
        </p>

        {/* Example chips */}
        <div className="flex flex-wrap gap-1.5">
          {EXAMPLE_TASKS.map((ex) => (
            <button
              key={ex}
              onClick={() => !isRunning && setTask(ex)}
              disabled={isRunning}
              className="rounded-full border border-border bg-background/50 px-2 py-0.5 font-mono text-xs text-muted-foreground transition-colors hover:bg-foreground/10 hover:text-foreground disabled:opacity-50"
            >
              {ex}
            </button>
          ))}
        </div>

        <div className="flex gap-2">
          {!isRunning ? (
            <button
              onClick={handleStart}
              disabled={!task.trim() || !llmAvailable}
              className="flex-1 rounded-md border border-border bg-foreground px-4 py-2 font-mono text-sm font-medium text-background transition-opacity hover:opacity-90 disabled:opacity-30"
            >
              ▶ START AGENT
            </button>
          ) : (
            <button
              onClick={stopAgent}
              className="flex-1 rounded-md border border-red-500/50 bg-red-500/10 px-4 py-2 font-mono text-sm font-medium text-red-500 transition-colors hover:bg-red-500/20"
            >
              ■ STOP
            </button>
          )}
          <button
            onClick={clear}
            disabled={isRunning}
            className="rounded-md border border-border px-3 py-2 font-mono text-sm text-muted-foreground transition-colors hover:bg-foreground/10 hover:text-foreground disabled:opacity-30"
          >
            CLEAR
          </button>
        </div>
      </div>

      {/* Advanced */}
      <button
        onClick={() => setShowAdvanced((s) => !s)}
        className="self-start font-mono text-xs text-muted-foreground/60 hover:text-foreground"
      >
        {showAdvanced ? "▾" : "▸"} advanced
      </button>
      {showAdvanced && (
        <div className="flex items-center gap-2 rounded-md border border-border bg-background/50 px-3 py-2">
          <label className="font-mono text-xs text-muted-foreground">budget</label>
          <input
            type="number"
            min={1}
            max={200}
            value={budget}
            onChange={(e) => setBudget(parseInt(e.target.value || "0", 10))}
            disabled={isRunning}
            className="w-20 rounded border border-border bg-background px-2 py-1 font-mono text-sm text-foreground disabled:opacity-60"
          />
          <span className="font-mono text-xs text-muted-foreground/60">
            tool calls before the agent is forced to stop
          </span>
        </div>
      )}
    </div>
  )
}
