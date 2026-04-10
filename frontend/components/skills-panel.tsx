"use client"

import type { SkillsData } from "@/lib/api"

interface SkillsPanelProps {
  skillsData: SkillsData | null
}

export function SkillsPanel({ skillsData }: SkillsPanelProps) {
  return (
    <div className="flex h-full flex-col gap-3">
      <h2 className="font-mono text-lg font-medium tracking-wide text-foreground">
        REQUIRED SKILLS
      </h2>
      <div className="flex flex-col gap-2">
        {skillsData ? skillsData.required.map((skillId) => {
          const isLoaded = skillsData.available.includes(skillId)
          return (
            <div
              key={skillId}
              className={`flex items-center justify-between rounded-md border px-3 py-2 font-mono transition-colors border-foreground/20 bg-foreground/5`}
            >
              <div className="flex items-center gap-2">
                <div
                  className={`h-1.5 w-1.5 rounded-full ${
                    isLoaded ? "bg-foreground" : "bg-muted-foreground/30"
                  }`}
                />
                <span className="text-base text-foreground capitalize">
                  {skillId}
                </span>
              </div>
              <span className="text-base text-muted-foreground">
                {isLoaded ? "loaded" : "pending"}
              </span>
            </div>
          )
        }) : <span className="font-mono text-base text-muted-foreground/50">loading...</span>}
      </div>
    </div>
  )
}
