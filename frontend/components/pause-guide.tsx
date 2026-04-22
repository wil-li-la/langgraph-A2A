"use client"

import Link from "next/link"

interface PauseGuideProps {
  nodeId: string
  reason: string
}

function isUserStop(reason: string): boolean {
  return reason.toLowerCase().includes("stopped by user")
}

export function PauseGuide({ nodeId, reason }: PauseGuideProps) {
  const userStopped = isUserStop(reason)

  return (
    <div className={`rounded-md border p-4 ${
      userStopped ? "border-amber-400/30 bg-amber-400/5" : "border-red-400/30 bg-red-400/5"
    }`}>
      <div className="flex items-center gap-2 mb-2">
        <span className={`font-mono text-lg font-bold ${userStopped ? "text-amber-400" : "text-red-400"}`}>
          {userStopped ? "WORKFLOW STOPPED" : "WORKFLOW PAUSED"}
        </span>
      </div>
      <div className="font-mono text-sm text-muted-foreground mb-3">
        {userStopped
          ? `Paused at \"${nodeId}\" — teleop the robot as needed, then resume.`
          : `Node \"${nodeId}\" failed: ${reason}`}
      </div>
      <div className="space-y-2 font-mono text-sm text-foreground/80">
        <div className="flex items-start gap-2">
          <span className="text-muted-foreground shrink-0">1.</span>
          <span>
            {userStopped
              ? "Use teleop to adjust the robot if needed "
              : "Switch to Teleop to adjust the robot "}
            <Link
              href="/teleop"
              className="rounded border border-border px-3 py-1 text-sm font-medium text-foreground transition-colors hover:bg-foreground/10"
            >
              Open Teleop
            </Link>
          </span>
        </div>
        <div className="flex items-start gap-2">
          <span className="text-muted-foreground shrink-0">2.</span>
          <span>Press RESUME in the workflow panel, or click any node in the graph to resume from there.</span>
        </div>
      </div>
    </div>
  )
}
