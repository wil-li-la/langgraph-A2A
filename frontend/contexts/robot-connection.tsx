"use client"

import { createContext, useContext, useState, useCallback, type ReactNode } from "react"
import { useTeleop } from "@/hooks/use-teleop"
import type { RobotStatus, CameraName } from "@/types/robot"
import type { RobotCommand } from "@/lib/teleop-protocol"

interface RobotConnectionContextValue {
  robotHost: string
  setRobotHost: (host: string) => void
  isConnected: boolean
  status: RobotStatus
  cameras: Record<CameraName, string | null>
  sendCommand: (cmd: RobotCommand) => void
  connect: (host: string) => void
  disconnect: () => void
  handleConnect: () => void
}

const RobotConnectionContext = createContext<RobotConnectionContextValue | null>(null)

export function RobotConnectionProvider({ children }: { children: ReactNode }) {
  const [robotHost, setRobotHost] = useState("")
  const { status, cameras, isConnected, sendCommand, connect, disconnect } = useTeleop()

  const handleConnect = useCallback(() => {
    if (robotHost.trim()) {
      const host = robotHost.trim()
      const url = host.includes("://") ? host : `ws://${host}:8765`
      connect(url)
    }
  }, [robotHost, connect])

  return (
    <RobotConnectionContext.Provider
      value={{
        robotHost,
        setRobotHost,
        isConnected,
        status,
        cameras,
        sendCommand,
        connect,
        disconnect,
        handleConnect,
      }}
    >
      {children}
    </RobotConnectionContext.Provider>
  )
}

export function useRobotConnection() {
  const ctx = useContext(RobotConnectionContext)
  if (!ctx) {
    throw new Error("useRobotConnection must be used within RobotConnectionProvider")
  }
  return ctx
}
