"use client"

import { useUIMode } from "@/contexts/ui-mode-context"

interface ModeToggleProps {
  /** When true, the toggle is rendered in a disabled / locked state. */
  disabled?: boolean
  /** Tooltip explaining why disabled. */
  disabledReason?: string
}

export function ModeToggle({ disabled = false, disabledReason }: ModeToggleProps) {
  const { mode, setMode } = useUIMode()

  return (
    <div
      title={disabled ? disabledReason : undefined}
      className={`flex rounded-full border border-border bg-background/50 p-0.5 ${
        disabled ? "opacity-50" : ""
      }`}
    >
      <button
        onClick={() => !disabled && setMode("scripted")}
        disabled={disabled}
        className={`rounded-full px-3 py-1 font-mono text-sm font-medium transition-colors ${
          mode === "scripted"
            ? "bg-foreground text-background"
            : "text-muted-foreground hover:text-foreground"
        } disabled:cursor-not-allowed`}
      >
        SCRIPTED
      </button>
      <button
        onClick={() => !disabled && setMode("agentic")}
        disabled={disabled}
        className={`rounded-full px-3 py-1 font-mono text-sm font-medium transition-colors ${
          mode === "agentic"
            ? "bg-foreground text-background"
            : "text-muted-foreground hover:text-foreground"
        } disabled:cursor-not-allowed`}
      >
        AGENTIC
      </button>
    </div>
  )
}
