"use client"

import type { RobotTaskData } from "@/lib/mock-data"

interface SkillsPanelProps {
  data: RobotTaskData
}

const allSkills = [
  { id: "grasp", name: "Grasp", description: "Pick and place objects" },
  { id: "navigation", name: "Navigation", description: "Autonomous movement" },
  { id: "audio", name: "Audio", description: "Speech & sound processing" },
]

export function SkillsPanel({ data }: SkillsPanelProps) {
  return (
    <div className="flex h-full flex-col gap-3">
      <h2 className="font-mono text-sm font-medium tracking-wide text-foreground">
        REQUIRED SKILLS
      </h2>
      <div className="flex flex-col gap-2">
        {allSkills.map((skill) => {
          const isRequired = data.requiredSkills.includes(skill.id)
          const isLoaded = data.skills.find((s) => s.id === skill.id)?.loaded ?? false
          return (
            <div
              key={skill.id}
              className={`flex items-center justify-between rounded-md border px-3 py-2 font-mono transition-colors ${
                isRequired
                  ? "border-foreground/20 bg-foreground/5"
                  : "border-border bg-transparent"
              }`}
            >
              <div className="flex items-center gap-2">
                <div
                  className={`h-1.5 w-1.5 rounded-full ${
                    isRequired && isLoaded
                      ? "bg-foreground"
                      : isRequired
                        ? "bg-muted-foreground"
                        : "bg-muted-foreground/30"
                  }`}
                />
                <span
                  className={`text-xs ${
                    isRequired ? "text-foreground" : "text-muted-foreground/50"
                  }`}
                >
                  {skill.name}
                </span>
              </div>
              {isRequired && (
                <span className="text-[10px] text-muted-foreground">
                  {isLoaded ? "loaded" : "pending"}
                </span>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
