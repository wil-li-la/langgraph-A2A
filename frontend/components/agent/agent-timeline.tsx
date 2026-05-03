"use client"

import { useEffect, useRef } from "react"
import { useAgent, type AgentTimelineEntry } from "@/contexts/agent-context"

export function AgentTimeline() {
  const { timeline, runState, errorText, robotState, info } = useAgent()
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [timeline.length])

  return (
    <div className="flex h-full flex-col gap-3">
      {/* Header */}
      <div className="flex shrink-0 items-center justify-between">
        <h2 className="font-mono text-lg font-medium tracking-wide text-foreground">
          AGENT TIMELINE
        </h2>
        <RunBadge state={runState} />
      </div>

      {/* Robot state strip */}
      {(robotState || info) && (
        <div className="grid shrink-0 grid-cols-2 gap-2 rounded-md border border-border bg-background/50 p-2 sm:grid-cols-4">
          <Stat label="location" value={robotState?.location ?? "—"} />
          <Stat label="holding" value={robotState?.holding ?? "—"} />
          <Stat
            label="calls"
            value={
              robotState
                ? `${robotState.calls_made}/${robotState.budget}`
                : `0/${info?.default_budget ?? 30}`
            }
          />
          <Stat
            label="elapsed"
            value={robotState ? `${robotState.elapsed_seconds.toFixed(1)}s` : "—"}
          />
        </div>
      )}

      {/* Timeline */}
      <div
        ref={scrollRef}
        className="flex-1 min-h-0 overflow-auto rounded-md border border-border bg-background/50 p-2"
      >
        {timeline.length === 0 ? (
          <p className="font-mono text-sm text-muted-foreground/40">
            {runState === "running"
              ? "agent is thinking…"
              : "no activity yet — start the agent to see tool calls and reasoning"}
          </p>
        ) : (
          <ol className="flex flex-col gap-1.5">
            {timeline.map((entry, i) => (
              <TimelineEntry key={entry.id} entry={entry} index={i + 1} />
            ))}
            {runState === "running" && (
              <li className="flex items-center gap-2 px-1 py-1 font-mono text-xs text-muted-foreground">
                <span className="relative flex h-1.5 w-1.5">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-sky-400/60" />
                  <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-sky-400" />
                </span>
                LLM thinking…
              </li>
            )}
          </ol>
        )}
      </div>

      {errorText && (
        <div className="shrink-0 rounded-md border border-red-500/40 bg-red-500/10 p-2 font-mono text-xs text-red-300">
          {errorText}
        </div>
      )}
    </div>
  )
}

function RunBadge({ state }: { state: ReturnType<typeof useAgent>["runState"] }) {
  if (state === "running") {
    return (
      <span className="flex items-center gap-1.5 font-mono text-sm">
        <span className="relative flex h-2 w-2">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-sky-400/60" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-sky-400" />
        </span>
        <span className="text-foreground">RUNNING</span>
      </span>
    )
  }
  if (state === "done") {
    return <span className="font-mono text-sm text-emerald-400">DONE</span>
  }
  if (state === "error") {
    return <span className="font-mono text-sm text-red-400">ERROR</span>
  }
  return <span className="font-mono text-sm text-muted-foreground/60">IDLE</span>
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col">
      <span className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground/60">
        {label}
      </span>
      <span className="truncate font-mono text-sm text-foreground">{value}</span>
    </div>
  )
}

function TimelineEntry({ entry, index }: { entry: AgentTimelineEntry; index: number }) {
  if (entry.kind === "tool_call") {
    return (
      <li className="rounded-md border border-sky-500/30 bg-sky-500/5 px-2 py-1.5">
        <div className="flex items-center gap-2 font-mono text-xs">
          <span className="text-muted-foreground/50">#{index}</span>
          <span className="rounded bg-sky-500/20 px-1.5 py-0.5 text-sky-300">CALL</span>
          <span className="font-medium text-foreground">{entry.toolName}</span>
        </div>
        {entry.toolArgs && Object.keys(entry.toolArgs).length > 0 && (
          <pre className="mt-1 overflow-x-auto whitespace-pre-wrap font-mono text-xs text-muted-foreground">
            {JSON.stringify(entry.toolArgs, null, 2)}
          </pre>
        )}
      </li>
    )
  }
  if (entry.kind === "tool_result") {
    const text = entry.resultText ?? ""
    const isFailure =
      text.startsWith("BLOCKED:") ||
      text.startsWith("FAILED:") ||
      text.startsWith("UNKNOWN_LOCATION:")
    return (
      <li
        className={`rounded-md border px-2 py-1.5 ${
          isFailure
            ? "border-red-500/30 bg-red-500/5"
            : "border-emerald-500/30 bg-emerald-500/5"
        }`}
      >
        <div className="flex items-center gap-2 font-mono text-xs">
          <span className="text-muted-foreground/50">#{index}</span>
          <span
            className={`rounded px-1.5 py-0.5 ${
              isFailure ? "bg-red-500/20 text-red-300" : "bg-emerald-500/20 text-emerald-300"
            }`}
          >
            RESULT
          </span>
          <span className="text-muted-foreground">from {entry.toolName}</span>
        </div>
        <p className="mt-1 whitespace-pre-wrap break-words font-mono text-xs text-foreground">
          {text}
        </p>
      </li>
    )
  }
  // agent_message
  return (
    <li className="rounded-md border border-border bg-background/30 px-2 py-1.5">
      <div className="flex items-center gap-2 font-mono text-xs">
        <span className="text-muted-foreground/50">#{index}</span>
        <span className="rounded bg-foreground/15 px-1.5 py-0.5 text-foreground">SAID</span>
      </div>
      <p className="mt-1 whitespace-pre-wrap break-words font-mono text-sm text-foreground">
        {entry.agentText}
      </p>
    </li>
  )
}
