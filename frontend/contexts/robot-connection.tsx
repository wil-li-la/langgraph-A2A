"use client"

import { createContext, useContext, useState, useCallback, type ReactNode } from "react"
import { useTeleop } from "@/hooks/use-teleop"
import type { RobotStatus, CameraName } from "@/types/robot"
import type { RobotCommand, ConnectionErrorReason } from "@/lib/teleop-protocol"

interface RobotConnectionContextValue {
  robotHost: string
  setRobotHost: (host: string) => void
  isConnected: boolean
  connectionError: ConnectionErrorReason | null
  status: RobotStatus
  cameras: Record<CameraName, string | null>
  sendCommand: (cmd: RobotCommand) => void
  connect: (host: string) => void
  disconnect: () => void
  handleConnect: () => void
}

const RobotConnectionContext = createContext<RobotConnectionContextValue | null>(null)

const DEFAULT_ROBOT_HOST = process.env.NEXT_PUBLIC_ROBOT_HOST ?? "192.168.1.38"

export function RobotConnectionProvider({ children }: { children: ReactNode }) {
  const [robotHost, setRobotHost] = useState(DEFAULT_ROBOT_HOST)
  const { status, cameras, isConnected, connectionError, sendCommand, connect, disconnect } = useTeleop()

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
        connectionError,
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
