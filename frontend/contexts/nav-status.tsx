"use client"

import { createContext, useContext, useEffect, useState, type ReactNode } from "react"
import {
  subscribeNavStatus,
  type NavPose,
  type NavTask,
} from "@/lib/nav-api"

interface NavStatusContextValue {
  pose: NavPose | null
  task: NavTask | null
  teleopActive: boolean
}

const NavStatusContext = createContext<NavStatusContextValue>({
  pose: null,
  task: null,
  teleopActive: false,
})

/**
 * Single SSE subscriber to /api/nav/status/stream. Mounted at app root so
 * every page (NavBar, NavMap, ...) reads from one stream. Without this,
 * each consuming component would open its own EventSource.
 */
export function NavStatusProvider({ children }: { children: ReactNode }) {
  const [pose, setPose] = useState<NavPose | null>(null)
  const [task, setTask] = useState<NavTask | null>(null)
  const [teleopActive, setTeleopActive] = useState(false)

  useEffect(() => {
    const off = subscribeNavStatus(
      (snap) => {
        setPose(snap.pose)
        setTask(snap.task)
        setTeleopActive(snap.teleop_active)
      },
      () => { /* EventSource auto-reconnects on backend flicker */ },
    )
    return off
  }, [])

  return (
    <NavStatusContext.Provider value={{ pose, task, teleopActive }}>
      {children}
    </NavStatusContext.Provider>
  )
}

export function useNavStatus(): NavStatusContextValue {
  return useContext(NavStatusContext)
}
