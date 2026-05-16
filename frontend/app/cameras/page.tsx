"use client"

import { NavBar } from "@/components/nav-bar"
import { PylonCamerasGrid } from "@/components/pylon-cameras-grid"
import { RoomCamerasGrid } from "@/components/room-cameras-grid"

const HAS_PYLON = Boolean(
  process.env.NEXT_PUBLIC_CAM_BRIDGE_URL ||
    process.env.NEXT_PUBLIC_PYLON_CAMS_RIGHT_URL ||
    process.env.NEXT_PUBLIC_PYLON_CAMS_LEFT_URL,
)

export default function Page() {
  return (
    <div className="flex min-h-dvh flex-col bg-background lg:h-dvh lg:overflow-hidden">
      <NavBar />
      <main className="flex flex-1 min-h-0 flex-col p-2 lg:p-3">
        <div className="flex flex-1 min-h-0 rounded-md border border-border bg-card">
          {HAS_PYLON ? <PylonCamerasGrid /> : <RoomCamerasGrid />}
        </div>
      </main>
    </div>
  )
}
