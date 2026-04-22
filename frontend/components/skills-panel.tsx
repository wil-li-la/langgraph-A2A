"use client"

import type { SkillsData } from "@/lib/api"

interface SkillsPanelProps {
  skillsData: SkillsData | null
}

export function SkillsPanel({ skillsData }: SkillsPanelProps) {
  if (!skillsData) {
    return (
      <div className="flex items-center gap-2 font-mono text-sm text-muted-foreground/50">
        <span>SKILLS</span>
        <span>loading…</span>
      </div>
    )
  }

  return (
    <div className="flex items-center gap-2 flex-wrap">
      <span className="font-mono text-sm font-medium tracking-wide text-muted-foreground">
        SKILLS
      </span>
      {skillsData.required.map((skillId) => {
        const isLoaded = skillsData.available.includes(skillId)
        return (
          <div
            key={skillId}
            className={`flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 font-mono text-sm transition-colors ${
              isLoaded
                ? "border-foreground/20 bg-foreground/5 text-foreground"
                : "border-border bg-background/50 text-muted-foreground/50"
            }`}
            title={isLoaded ? "loaded" : "pending"}
          >
            <span
              className={`h-1.5 w-1.5 rounded-full ${
                isLoaded ? "bg-emerald-400" : "bg-muted-foreground/30"
              }`}
            />
            <span className="capitalize">{skillId}</span>
          </div>
        )
      })}
    </div>
  )
}
