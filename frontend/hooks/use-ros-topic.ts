"use client"

import { useEffect, useState } from "react"
import { getRosClient } from "@/lib/ros-client"

/**
 * Subscribe to a ROS2 topic via the lab's foxglove_bridge. Returns the
 * latest decoded message, or `null` until the first one arrives.
 *
 * The connection is shared across all callers — subscribe/unsubscribe
 * is reference-counted in ros-client.
 */
export function useRosTopic<T>(topic: string | null): T | null {
  const [msg, setMsg] = useState<T | null>(null)

  useEffect(() => {
    if (!topic) return
    const client = getRosClient()
    if (!client) return
    return client.subscribe<T>(topic, setMsg)
  }, [topic])

  return msg
}
