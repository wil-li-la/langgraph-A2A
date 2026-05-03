"use client"

import { useAgent } from "@/contexts/agent-context"

export function AgentInfo() {
  const { info, isInfoLoading, infoError, refetchInfo } = useAgent()

  if (isInfoLoading) {
    return (
      <p className="font-mono text-xs text-muted-foreground/60">loading agent info…</p>
    )
  }

  if (infoError || !info) {
    return (
      <div className="flex items-start gap-2 rounded-md border border-red-500/40 bg-red-500/10 p-2 font-mono text-xs text-red-300">
        <span>✗ could not reach /api/agent/info: {infoError ?? "unknown"}</span>
        <button
          onClick={() => void refetchInfo()}
          className="ml-auto rounded border border-red-400/40 px-2 py-0.5 hover:bg-red-500/20"
        >
          retry
        </button>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-2 font-mono text-xs">
      <div className="flex items-center justify-between">
        <span className="text-muted-foreground">
          {info.available ? "✓ available" : "✗ unavailable"}
        </span>
        <span className="text-muted-foreground/60">
          {info.llm_provider}/{info.llm_model || "—"}
        </span>
      </div>
      <details className="rounded-md border border-border bg-background/50 px-2 py-1.5">
        <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
          tools ({info.tools.length})
        </summary>
        <ul className="mt-2 flex flex-col gap-1 text-muted-foreground/80">
          {info.tools.map((t) => (
            <li key={t.name}>
              <span className="text-foreground">{t.name}</span>
              <span className="text-muted-foreground/60"> — {t.description}</span>
            </li>
          ))}
        </ul>
      </details>
      <details className="rounded-md border border-border bg-background/50 px-2 py-1.5">
        <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
          world model
        </summary>
        <pre className="mt-2 whitespace-pre-wrap text-muted-foreground/80">
          {info.world_summary}
        </pre>
      </details>
    </div>
  )
}
