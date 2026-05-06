"use client"

import { NavBar } from "@/components/nav-bar"
import { RoomCamerasGrid } from "@/components/room-cameras-grid"

export default function Page() {
  return (
    <div className="flex min-h-dvh flex-col bg-background lg:h-dvh lg:overflow-hidden">
      <NavBar />
      <main className="flex flex-1 min-h-0 flex-col p-2 lg:p-3">
        <div className="flex flex-1 min-h-0 rounded-md border border-border bg-card">
          <RoomCamerasGrid />
        </div>
      </main>
    </div>
  )
}
