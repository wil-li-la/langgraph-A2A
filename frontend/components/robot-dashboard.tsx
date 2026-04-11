"use client"

import { useEffect, useRef } from "react"
import { SkillsPanel } from "@/components/skills-panel"
import { WorkflowGraph } from "@/components/workflow-graph"
import { VideoPanel } from "@/components/video-panel"
import { ModeToggle } from "@/components/mode-toggle"
import { PauseGuide } from "@/components/pause-guide"
import { useWorkflow } from "@/hooks/use-workflow"
import { NavBar } from "@/components/nav-bar"

export function RobotDashboard() {
  const {
    nodes,
    edges,
    skillsData,
    isLoading,
    isLive,
    isExecuting,
    isResetting,
    activeNodeId,
    executionLog,
    progress,
    isPaused,
    pausedNodeId,
    pauseReason,
    resumeFromNode,
    resetWorkflow,
    startStreamExecution,
    stopStreamExecution,
  } = useWorkflow("stretch3")

  const logContainerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (logContainerRef.current) {
      logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight
    }
  }, [executionLog])

  return (
    <div className="flex h-dvh flex-col bg-background overflow-hidden">
      <NavBar />
      <div className="flex-1 grid grid-cols-[minmax(0,1fr)_300px] min-h-0 gap-3 p-3">
        {/* Left column: Graph + Video */}
        <div className="flex flex-col gap-3 min-h-0 overflow-hidden">
          {/* Task LangGraph */}
          <div className="flex-1 rounded-md border border-border bg-card p-3 flex flex-col min-h-0">
            <div className="mb-2 flex items-center justify-between shrink-0">
              <h2 className="font-mono text-lg font-medium tracking-wide text-foreground">
                TASK &mdash; LANGGRAPH
              </h2>
              <div className="flex items-center gap-2">
                <button
                  onClick={resetWorkflow}
                  disabled={isExecuting || isResetting}
                  className="rounded border border-border px-2 py-0.5 font-mono text-sm text-muted-foreground transition-colors hover:bg-foreground/10 hover:text-foreground disabled:opacity-30"
                  title="Reset workflow and return robot to origin"
                >
                  {isResetting ? "RESETTING..." : "↺ RESET"}
                </button>

                {isLoading ? (
                  <span className="font-mono text-sm text-muted-foreground">loading…</span>
                ) : isLive ? (
                  <>
                    <span className="relative flex h-1.5 w-1.5">
                      <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-foreground/50" />
                      <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-foreground" />
                    </span>
                    <span className="font-mono text-sm text-foreground">LIVE</span>
                  </>
                ) : (
                  <span className="font-mono text-sm text-muted-foreground/50">OFFLINE</span>
                )}
              </div>
            </div>

            {/* Progress bar */}
            {isExecuting && (
              <div className="mb-2 shrink-0">
                <div className="flex items-center justify-between mb-1">
                  <span className="font-mono text-sm text-muted-foreground">PROGRESS</span>
                  <span className="font-mono text-sm text-muted-foreground">{progress}%</span>
                </div>
                <div className="h-1 w-full rounded-full bg-border overflow-hidden">
                  <div
                    className="h-full rounded-full transition-all duration-700 ease-out"
                    style={{
                      width: `${progress}%`,
                      background: "linear-gradient(90deg, rgba(56,189,248,0.8), rgba(99,102,241,0.8))",
                    }}
                  />
                </div>
              </div>
            )}

            <div className="flex-1 min-h-0 overflow-auto rounded-md border border-border bg-background/50">
              <WorkflowGraph
                nodes={nodes}
                edges={edges}
                activeNodeId={activeNodeId}
                isPaused={isPaused}
                onNodeClick={resumeFromNode}
              />
            </div>
          </div>

          {/* Video & Map */}
          <div className="shrink-0 h-[370px] rounded-md border border-border bg-card p-3">
            <VideoPanel />
          </div>
        </div>

        {/* Right column: Skills + Mode + Log */}
        <div className="flex flex-col gap-3 min-h-0 overflow-y-auto">
          {/* Required Skills */}
          <div className="rounded-md border border-border bg-card p-3 shrink-0">
            <SkillsPanel skillsData={skillsData} />
          </div>

          {/* Operation Mode */}
          <div className="rounded-md border border-border bg-card p-3 shrink-0">
            <ModeToggle
              isExecuting={isExecuting}
              onStreamRun={startStreamExecution}
              onStreamStop={stopStreamExecution}
            />
          </div>

          {/* Pause guide */}
          {isPaused && pausedNodeId && pauseReason && (
            <div className="rounded-md border border-border bg-card p-3 shrink-0">
              <PauseGuide nodeId={pausedNodeId} reason={pauseReason} />
            </div>
          )}

          {/* Execution Log */}
          <div className="flex-1 min-h-0 rounded-md border border-border bg-card p-3 flex flex-col">
            <h2 className="mb-2 font-mono text-lg font-medium tracking-wide text-foreground shrink-0">
              EXECUTION LOG
            </h2>
            <div ref={logContainerRef} className="flex-1 min-h-0 overflow-auto rounded-md border border-border bg-background/50 p-2">
              {executionLog.length === 0 ? (
                <p className="font-mono text-sm text-muted-foreground/40">No execution history</p>
              ) : (
                <div className="flex flex-col gap-0.5">
                  {executionLog.map((entry, i) => (
                    <div
                      key={i}
                      className={`whitespace-pre-wrap font-mono text-sm leading-[1.5] ${
                        entry.includes("✗") || entry.includes("WARNING") || entry.includes("ERROR") || entry.includes("failed")
                          ? "text-red-400"
                          : entry.includes("✓") || entry.includes("▶") || entry.includes("✅")
                            ? "text-emerald-400"
                            : "text-muted-foreground"
                      }`}
                    >
                      {entry}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
