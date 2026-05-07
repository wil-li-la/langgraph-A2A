/**
 * Browser ↔ rosbridge_websocket client.
 *
 * Connects to the lab's `rosbridge_websocket` node (spawned by
 * backend/nav_bridge/launch/nav.launch.py on port 9090) over the URL
 * NEXT_PUBLIC_ROSBRIDGE_WS_URL. Surfaces a subscribe-decode-callback
 * API for ROS2 topics. Reuses the connection across all subscribers
 * — roslib's `Ros` instance is a singleton.
 *
 * Why rosbridge instead of foxglove_bridge: Foxglove went commercial
 * and the new ros-humble-foxglove-bridge 3.x speaks "foxglove.sdk.v1",
 * which the open-source @foxglove/ws-protocol cannot. rosbridge_suite
 * has been the boring-default for a decade. See backend/nav_bridge/README.md.
 */

import { Ros, Topic } from "roslib"

type Listener<T> = (msg: T) => void

interface TopicEntry {
  topic: Topic
  listeners: Set<Listener<unknown>>
  // ROS2 type strings like "nav_msgs/msg/OccupancyGrid". We discover
  // this lazily from rosbridge's getTopicType service, so the dashboard
  // doesn't have to know it ahead of time.
  messageType: string
}

class RosClient {
  private url: string
  private ros: Ros
  private topics = new Map<string, TopicEntry>()
  private connected = false

  constructor(url: string) {
    this.url = url
    this.ros = new Ros({ url })

    this.ros.on("connection", () => {
      this.connected = true
      // On reconnect, re-subscribe everything that was active.
      for (const entry of this.topics.values()) {
        this.attachTopic(entry)
      }
    })
    this.ros.on("close", () => {
      this.connected = false
      // roslib auto-reconnects on subsequent connect() calls; trigger
      // one with backoff. The default Ros() does NOT auto-retry.
      setTimeout(() => this.ros.connect(this.url), 2000)
    })
    this.ros.on("error", (err: unknown) => {
      console.warn("rosbridge connection error:", err)
    })
  }

  private async resolveTopicType(topic: string): Promise<string | null> {
    return new Promise((resolve) => {
      this.ros.getTopicType(
        topic,
        (type: string) => resolve(type || null),
        () => resolve(null),
      )
    })
  }

  private attachTopic(entry: TopicEntry) {
    entry.topic.subscribe((msg: unknown) => {
      for (const l of entry.listeners) {
        try { l(msg) } catch (e) { console.warn("ros-client listener threw:", e) }
      }
    })
  }

  subscribe<T>(topic: string, listener: Listener<T>): () => void {
    const typedListener = listener as Listener<unknown>
    const existing = this.topics.get(topic)
    if (existing) {
      existing.listeners.add(typedListener)
      return () => {
        existing.listeners.delete(typedListener)
        if (existing.listeners.size === 0) {
          existing.topic.unsubscribe()
          this.topics.delete(topic)
        }
      }
    }

    // Race-tolerant init: stash the listener in a temp Set, resolve
    // type async, then create+attach the Topic. If unsubscribe runs
    // before the type lookup returns, just bail.
    const listeners = new Set<Listener<unknown>>([typedListener])
    let cancelled = false
    const unsubscribe = () => {
      cancelled = true
      const e = this.topics.get(topic)
      if (e) {
        e.listeners.delete(typedListener)
        if (e.listeners.size === 0) {
          e.topic.unsubscribe()
          this.topics.delete(topic)
        }
      }
    }

    void this.resolveTopicType(topic).then((messageType) => {
      if (cancelled || !messageType) return
      const rosTopic = new Topic({
        ros: this.ros,
        name: topic,
        messageType,
      })
      const entry: TopicEntry = { topic: rosTopic, listeners, messageType }
      this.topics.set(topic, entry)
      this.attachTopic(entry)
    })

    return unsubscribe
  }
}

let _instance: RosClient | null = null

export function getRosClient(): RosClient | null {
  if (typeof window === "undefined") return null
  if (_instance) return _instance
  const url = process.env.NEXT_PUBLIC_ROSBRIDGE_WS_URL
  if (!url) return null
  _instance = new RosClient(url)
  return _instance
}

// Common ROS2 message shapes we care about (subset — we only decode
// fields we render). rosbridge JSON encoding gives us the same field
// names as the .msg definitions, so these match the ROS2 schemas.

export interface OccupancyGrid {
  header: { stamp: { sec: number; nanosec: number }; frame_id: string }
  info: {
    map_load_time: { sec: number; nanosec: number }
    resolution: number
    width: number
    height: number
    origin: {
      position: { x: number; y: number; z: number }
      orientation: { x: number; y: number; z: number; w: number }
    }
  }
  // rosbridge encodes int8[] as a regular JS number[]; consumer code
  // (occupancy-grid.ts) just indexes into it with [i].
  data: number[] | Int8Array
}

export interface PoseStamped {
  header: { stamp: { sec: number; nanosec: number }; frame_id: string }
  pose: {
    position: { x: number; y: number; z: number }
    orientation: { x: number; y: number; z: number; w: number }
  }
}

export interface Path {
  header: { stamp: { sec: number; nanosec: number }; frame_id: string }
  poses: PoseStamped[]
}
