"use client"

import { useState } from "react"
import { type RobotId, taskData } from "@/lib/mock-data"
import { ConnectPanel } from "@/components/connect-panel"
import { SkillsPanel } from "@/components/skills-panel"
import { WorkflowGraph } from "@/components/workflow-graph"
import { VideoPanel } from "@/components/video-panel"
import { ModeToggle } from "@/components/mode-toggle"
import { useWorkflow } from "@/hooks/use-workflow"

export function RobotDashboard() {
  const [selectedRobot, setSelectedRobot] = useState<RobotId>("stretch3")
  const [executedNodes, setExecutedNodes] = useState<string[]>([])
  const data = taskData[selectedRobot]
  const { nodes, edges, isLoading, isLive } = useWorkflow(selectedRobot, executedNodes)

  return (
    <div className="flex min-h-screen flex-col bg-background p-4 lg:p-6">
      {/* Title */}
      <header className="mb-6">
        <h1 className="font-mono text-lg font-medium tracking-tight text-foreground">
          Robot Task Dashboard
        </h1>
        <div className="mt-1 h-px bg-border" />
      </header>

      {/* Main grid matching wireframe layout */}
      <div className="mx-auto flex w-full max-w-[1400px] flex-1 flex-col gap-4">
        {/* Top row: Connect Robot | Task LangGraph | Required Skills */}
        <div className="grid gap-4 lg:grid-cols-[1fr_1.2fr_0.6fr]">
          {/* Connect Robot panel */}
          <div className="rounded-md border border-border bg-card p-4">
            <ConnectPanel
              selectedRobot={selectedRobot}
              onSelectRobot={setSelectedRobot}
              data={data}
            />
          </div>

          {/* Task LangGraph */}
          <div className="rounded-md border border-border bg-card p-4">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="font-mono text-sm font-medium tracking-wide text-foreground">
                TASK &mdash; LANGGRAPH
              </h2>
              <div className="flex items-center gap-1.5">
                {isLoading ? (
                  <span className="font-mono text-[10px] text-muted-foreground">loading…</span>
                ) : isLive ? (
                  <>
                    <span className="relative flex h-1.5 w-1.5">
                      <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-foreground/50" />
                      <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-foreground" />
                    </span>
                    <span className="font-mono text-[10px] text-foreground">LIVE</span>
                  </>
                ) : (
                  <span className="font-mono text-[10px] text-muted-foreground/50">OFFLINE</span>
                )}
              </div>
            </div>
            <div className="h-[420px] overflow-auto rounded-md border border-border bg-background/50">
              <WorkflowGraph
                nodes={nodes}
                edges={edges}
              />
            </div>
          </div>

          {/* Required Skills */}
          <div className="rounded-md border border-border bg-card p-4">
            <SkillsPanel data={data} />
          </div>
        </div>

        {/* Bottom row: Operation Mode + 3D View | Video streaming */}
        <div className="grid gap-4 lg:grid-cols-[1fr_1fr]">
          {/* Left: Operation Mode + Main View */}
          <div className="flex flex-col gap-4">
            {/* Operation Mode panel */}
            <div className="rounded-md border border-border bg-card p-4">
              <ModeToggle onExecutionResult={(res) => setExecutedNodes(res.executed_nodes)} />
            </div>

            {/* 3D view / main camera */}
            <div className="rounded-md border border-border bg-card p-4">
              <h2 className="mb-3 font-mono text-sm font-medium tracking-wide text-foreground">
                MAIN VIEW
              </h2>
              <div className="flex aspect-video items-center justify-center rounded-md border border-dashed border-border bg-muted/10">
                <div className="text-center">
                  <svg
                    className="mx-auto h-10 w-10 text-muted-foreground/30"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                    strokeWidth={0.75}
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="M21 7.5l-2.25-1.313M21 7.5v2.25m0-2.25l-2.25 1.313M3 7.5l2.25-1.313M3 7.5l2.25 1.313M3 7.5v2.25m9 3l2.25-1.313M12 12.75l-2.25-1.313M12 12.75V15m0 6.75l2.25-1.313M12 21.75V19.5m0 2.25l-2.25-1.313m0-16.875L12 2.25l2.25 1.313M21 14.25v2.25l-2.25 1.313m-13.5 0L3 16.5v-2.25"
                    />
                  </svg>
                  <p className="mt-2 font-mono text-xs text-muted-foreground/40">
                    3D View / Camera Feed
                  </p>
                </div>
              </div>
            </div>
          </div>

          {/* Video streaming panel */}
          <div className="rounded-md border border-border bg-card p-4">
            <VideoPanel data={data} />
          </div>
        </div>
      </div>
    </div>
  )
}
