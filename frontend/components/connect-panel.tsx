"use client"

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Button } from "@/components/ui/button"
import { robots, type RobotId, type RobotTaskData } from "@/lib/mock-data"
import type { SkillsData } from "@/lib/api"

interface ConnectPanelProps {
  selectedRobot: RobotId
  onSelectRobot: (id: RobotId) => void
  data: RobotTaskData
  skillsData: SkillsData | null
}

export function ConnectPanel({ selectedRobot, onSelectRobot, data, skillsData }: ConnectPanelProps) {
  const robot = robots.find((r) => r.id === selectedRobot)
  const isConnected = robot?.status === "connected"

  return (
    <div className="flex h-full flex-col gap-4">
      {/* Header with dropdown */}
      <div className="flex items-center justify-between">
        <h2 className="font-mono text-sm font-medium tracking-wide text-foreground">
          CONNECT ROBOT
        </h2>
        <Select value={selectedRobot} onValueChange={(v) => onSelectRobot(v as RobotId)}>
          <SelectTrigger className="w-[200px] border-border bg-background font-mono text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {robots.map((r) => (
              <SelectItem key={r.id} value={r.id} className="font-mono text-xs">
                <div className="flex items-center gap-2">
                  <div
                    className={`h-1.5 w-1.5 rounded-full ${
                      r.status === "connected" ? "bg-foreground" : "bg-muted-foreground/40"
                    }`}
                  />
                  {r.name}
                </div>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* Connection status */}
      <div className="rounded-md border border-border bg-muted/30 px-4 py-3">
        <p className="font-mono text-xs text-muted-foreground">
          {isConnected ? "Connected to" : "Not connected"}
        </p>
        {isConnected && (
          <p className="mt-1 font-mono text-sm text-foreground">{data.connectedTo}</p>
        )}
        {robot && (
          <p className="mt-1 font-mono text-[10px] text-muted-foreground/60">{robot.model}</p>
        )}
      </div>

      {/* Load skill */}
      <div>
        <p className="mb-2 font-mono text-xs text-muted-foreground">Load Skill</p>
        <div className="flex flex-wrap gap-2">
          {skillsData ? skillsData.available.map((skillId) => (
            <Button
              key={skillId}
              variant="secondary"
              size="sm"
              className="font-mono text-xs border-foreground/20 bg-foreground/10 text-foreground capitalize"
            >
              {skillId}
              <span className="ml-1.5 inline-block h-1 w-1 rounded-full bg-foreground" />
            </Button>
          )) : <span className="font-mono text-[10px] text-muted-foreground/50">loading...</span>}
        </div>
      </div>
    </div>
  )
}
