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

interface ConnectPanelProps {
  selectedRobot: RobotId
  onSelectRobot: (id: RobotId) => void
  data: RobotTaskData
}

export function ConnectPanel({ selectedRobot, onSelectRobot, data }: ConnectPanelProps) {
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
          {data.skills.map((skill) => (
            <Button
              key={skill.id}
              variant={skill.loaded ? "secondary" : "outline"}
              size="sm"
              className={`font-mono text-xs ${
                skill.loaded
                  ? "border-foreground/20 bg-foreground/10 text-foreground"
                  : "border-border text-muted-foreground hover:text-foreground"
              }`}
            >
              {skill.name}
              {skill.loaded && (
                <span className="ml-1.5 inline-block h-1 w-1 rounded-full bg-foreground" />
              )}
            </Button>
          ))}
        </div>
      </div>
    </div>
  )
}
