"use client"

import { useEffect, useRef, useState } from "react"
import { SkillsPanel } from "@/components/skills-panel"
import { WorkflowGraph } from "@/components/workflow-graph"
import { VideoPanel } from "@/components/video-panel"
import { WorkflowControls } from "@/components/workflow-controls"
import { PauseGuide } from "@/components/pause-guide"
import { useWorkflow } from "@/hooks/use-workflow"
import { NavBar } from "@/components/nav-bar"
import { VoiceInput } from "@/components/voice-input"
import { AgentControls } from "@/components/agent/agent-controls"
import { AgentTimeline } from "@/components/agent/agent-timeline"
import { AgentInfo } from "@/components/agent/agent-info"
import { useAgent } from "@/contexts/agent-context"
import { useUIMode } from "@/contexts/ui-mode-context"
import { LocationsPanel } from "@/components/locations-panel"

export function RobotDashboard() {
  const { mode } = useUIMode()
  const {
    nodes,
    edges,
    skillsData,
    isLoading,
    isLive,
    isExecuting,
    activeNodeId,
    executionLog,
    progress,
    isPaused,
    pausedNodeId,
    pauseReason,
    resumeFromNode,
    resetWorkflow,
    startStreamExecution,
    appendLog,
    awaitingInput,
    inputPrompt,
    submitInput,
    workflowState,
    stop,
    startFromNode,
  } = useWorkflow("stretch3")

  // Agent context (only consumed when mode === "agentic"). Reading it here
  // keeps the hook order stable across renders.
  const agentCtx = useAgent()

  const [instruction, setInstruction] = useState("")
  const [highlightInstructionTick, setHighlightInstructionTick] = useState(0)

  const logContainerRef = useRef<HTMLDivElement>(null)
  const voiceInputRef = useRef<HTMLDivElement>(null)

  // Active log: scripted mode reads workflow log, agentic mode reads agent log.
  const activeLog = mode === "agentic" ? agentCtx.log : executionLog

  useEffect(() => {
    if (logContainerRef.current) {
      logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight
    }
  }, [activeLog])

  // When the workflow blocks on operator input, the Voice Input panel
  // is often off-screen (bottom of right column). Scroll it into view
  // and pulse the dashboard so the operator notices.
  useEffect(() => {
    if (!awaitingInput) return
    voiceInputRef.current?.scrollIntoView({ behavior: "smooth", block: "center" })
  }, [awaitingInput])

  return (
    <div className="flex min-h-dvh flex-col bg-background lg:h-dvh lg:overflow-hidden">
      <NavBar workflowState={workflowState} />
      {awaitingInput && (
        <div className="shrink-0 border-y border-sky-500/40 bg-sky-500/10 px-4 py-2 flex items-center gap-3 animate-pulse">
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-sky-400/60" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-sky-400" />
          </span>
          <span className="font-mono text-sm font-medium text-sky-300">
            🎤 Workflow awaiting your response:
          </span>
          <span className="font-mono text-sm text-foreground truncate">
            「{inputPrompt}」
          </span>
          <span className="ml-auto font-mono text-xs text-sky-300/80">
            → VOICE INPUT panel below
          </span>
        </div>
      )}
      <div className="flex flex-1 min-h-0 flex-col gap-2 p-2 lg:flex-row lg:gap-3 lg:p-3">
        {/* Left column: main visualization (scripted graph OR agent timeline) + Video */}
        <div className="flex flex-1 flex-col gap-3 min-h-0 min-w-0">
          {/* Main panel — content depends on mode. */}
          <div className="flex-1 min-h-[360px] min-w-0 rounded-md border border-border bg-card p-3 flex flex-col">
            {mode === "scripted" ? (
              <>
                <div className="mb-2 flex items-center justify-between shrink-0">
                  <h2 className="font-mono text-lg font-medium tracking-wide text-foreground">
                    TASK &mdash; LANGGRAPH
                  </h2>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={resetWorkflow}
                      disabled={isExecuting}
                      className="rounded border border-border px-2 py-0.5 font-mono text-sm text-muted-foreground transition-colors hover:bg-foreground/10 hover:text-foreground disabled:opacity-30"
                      title="Reset graph state"
                    >
                      ↺ RESET
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

                {/* Skills strip */}
                <div className="mb-2 shrink-0">
                  <SkillsPanel skillsData={skillsData} />
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

                <div className="flex-1 min-h-0 overflow-hidden rounded-md border border-border bg-background/50">
                  <WorkflowGraph
                    nodes={nodes}
                    edges={edges}
                    activeNodeId={activeNodeId}
                    workflowState={workflowState}
                    onNodeClick={(nodeId) => {
                      if (workflowState === "paused") {
                        resumeFromNode(nodeId)
                      } else if (workflowState === "idle") {
                        if (nodeId === "handle_error") {
                          appendLog(`⚠ Cannot start from "${nodeId}" — error node is not a valid start target`, "warning")
                          return
                        }
                        if (!instruction.trim()) {
                          appendLog("⚠ Enter an instruction first, then click a node to start from there", "warning")
                          setHighlightInstructionTick((t) => t + 1)
                          return
                        }
                        startFromNode(nodeId, instruction.trim())
                      }
                    }}
                  />
                </div>
              </>
            ) : (
              <AgentTimeline />
            )}
          </div>

          {/* Video (shared by both modes) */}
          <div className="shrink-0 h-[320px] sm:h-[400px] lg:h-[420px] xl:h-[480px] 2xl:h-[540px] rounded-md border border-border bg-card p-3">
            <VideoPanel />
          </div>
        </div>

        {/* Right column: controls + log */}
        <div className="flex-shrink-0 flex flex-col gap-3 min-h-0 w-full lg:w-[420px] xl:w-[500px] 2xl:w-[560px] lg:overflow-y-auto">
          {mode === "scripted" ? (
            <>
              {/* Workflow controls */}
              <div className="rounded-md border border-border bg-card p-3 shrink-0">
                <WorkflowControls
                  workflowState={workflowState}
                  pausedNodeId={pausedNodeId}
                  pauseReason={pauseReason}
                  activeNodeId={activeNodeId}
                  onStart={startStreamExecution}
                  onStop={stop}
                  onResume={resumeFromNode}
                  onInstructionChange={setInstruction}
                  instruction={instruction}
                  highlightInstructionTick={highlightInstructionTick}
                />
              </div>

              {/* Per-workflow teach-and-save Locations panel */}
              <LocationsPanel workflowId="medication_delivery" />

              {/* Pause guide */}
              {isPaused && pausedNodeId && pauseReason && (
                <div className="rounded-md border border-border bg-card p-3 shrink-0">
                  <PauseGuide nodeId={pausedNodeId} reason={pauseReason} />
                </div>
              )}

              {/* Voice Input */}
              <div
                ref={voiceInputRef}
                className={`rounded-md border bg-card p-3 shrink-0 transition-colors ${
                  awaitingInput ? "border-sky-500/50 ring-2 ring-sky-500/30" : "border-border"
                }`}
              >
                <VoiceInput
                  onResult={(text) => {
                    if (awaitingInput) {
                      submitInput(text)
                    } else {
                      appendLog(`🗣️ Voice input: 「${text}」`)
                    }
                  }}
                  prompt={awaitingInput ? inputPrompt : null}
                  awaiting={awaitingInput}
                />
              </div>
            </>
          ) : (
            <>
              {/* Agent controls */}
              <div className="rounded-md border border-border bg-card p-3 shrink-0">
                <AgentControls />
              </div>

              {/* Agent info */}
              <div className="rounded-md border border-border bg-card p-3 shrink-0">
                <AgentInfo />
              </div>
            </>
          )}

          {/* Execution Log (shared shell, content depends on mode) */}
          <div className="min-h-[200px] lg:min-h-0 flex-1 rounded-md border border-border bg-card p-3 flex flex-col">
            <h2 className="mb-2 font-mono text-lg font-medium tracking-wide text-foreground shrink-0">
              {mode === "agentic" ? "AGENT LOG" : "EXECUTION LOG"}
            </h2>
            <div ref={logContainerRef} className="flex-1 min-h-0 overflow-auto rounded-md border border-border bg-background/50 p-2">
              {activeLog.length === 0 ? (
                <p className="font-mono text-sm text-muted-foreground/40">No execution history</p>
              ) : (
                <div className="flex flex-col gap-0.5">
                  {activeLog.map((entry, i) => (
                    <div
                      key={i}
                      className={`whitespace-pre-wrap font-mono text-sm leading-[1.5] ${
                        entry.level === "error"
                          ? "text-red-400"
                          : entry.level === "warning"
                            ? "text-amber-400"
                            : entry.text.includes("✓") || entry.text.includes("✅")
                              ? "text-emerald-400"
                              : "text-muted-foreground"
                      }`}
                    >
                      {entry.text}
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
