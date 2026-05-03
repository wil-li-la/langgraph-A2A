"use client"

import { createContext, useContext, useEffect, useState, type ReactNode } from "react"

export type UIMode = "scripted" | "agentic"

interface UIModeContextValue {
  mode: UIMode
  setMode: (mode: UIMode) => void
}

const UIModeContext = createContext<UIModeContextValue | null>(null)

const STORAGE_KEY = "robot-dashboard-ui-mode"

export function UIModeProvider({ children }: { children: ReactNode }) {
  // Default to scripted so first-time users see the existing dashboard unchanged.
  const [mode, setModeState] = useState<UIMode>("scripted")

  // Restore persisted choice on mount (guarded — can run server-side too).
  useEffect(() => {
    if (typeof window === "undefined") return
    const stored = window.localStorage.getItem(STORAGE_KEY)
    if (stored === "scripted" || stored === "agentic") {
      setModeState(stored)
    }
  }, [])

  const setMode = (next: UIMode) => {
    setModeState(next)
    if (typeof window !== "undefined") {
      window.localStorage.setItem(STORAGE_KEY, next)
    }
  }

  return (
    <UIModeContext.Provider value={{ mode, setMode }}>
      {children}
    </UIModeContext.Provider>
  )
}

export function useUIMode(): UIModeContextValue {
  const ctx = useContext(UIModeContext)
  if (!ctx) throw new Error("useUIMode must be used inside UIModeProvider")
  return ctx
}
