"use client"

import Link from "next/link"

interface PauseGuideProps {
  nodeId: string
  reason: string
}

export function PauseGuide({ nodeId, reason }: PauseGuideProps) {
  return (
    <div className="rounded-md border border-red-400/30 bg-red-400/5 p-4">
      <div className="flex items-center gap-2 mb-2">
        <span className="font-mono text-lg font-bold text-red-400">
          WORKFLOW PAUSED
        </span>
      </div>
      <div className="font-mono text-base text-muted-foreground mb-3">
        Node &quot;{nodeId}&quot; failed: {reason}
      </div>
      <div className="space-y-2 font-mono text-base text-foreground/80">
        <div className="flex items-start gap-2">
          <span className="text-muted-foreground shrink-0">1.</span>
          <span>
            Switch to Teleop to adjust the robot{" "}
            <Link
              href="/teleop"
              className="rounded border border-border px-3 py-1 text-base font-medium text-foreground transition-colors hover:bg-foreground/10"
            >
              Open Teleop
            </Link>
          </span>
        </div>
        <div className="flex items-start gap-2">
          <span className="text-muted-foreground shrink-0">2.</span>
          <span>Click any node in the graph to resume from there</span>
        </div>
      </div>
    </div>
  )
}
