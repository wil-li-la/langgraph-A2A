import { NavMap } from "@/components/teleop/nav-map"
import { CameraView } from "@/components/teleop/camera-view"
import { useRobotConnection } from "@/contexts/robot-connection"

export function VideoPanel() {
  const { status, cameras, sendCommand } = useRobotConnection()

  return (
    <div className="flex h-full flex-col gap-2">
      <div className="flex items-center justify-between shrink-0">
        <h2 className="font-mono text-lg font-medium tracking-wide text-foreground">
          VIDEO &amp; MAP
        </h2>
      </div>

      <div className="grid flex-1 grid-cols-1 gap-2 min-h-0 sm:grid-cols-3">
        {/* Head view (realsense d435if) */}
        <div className="relative flex flex-col overflow-hidden rounded-md border border-border min-h-0">
          <div className="flex-1 min-h-0">
            <CameraView name="realsense" src={cameras.realsense} />
          </div>
          <div className="flex items-center justify-between border-t border-border px-2 py-1 shrink-0">
            <span className="font-mono text-sm text-muted-foreground">Head</span>
            {cameras.realsense && (
              <div className="flex items-center gap-1">
                <span className="relative flex h-1.5 w-1.5">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-foreground/50" />
                  <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-foreground" />
                </span>
                <span className="font-mono text-sm text-foreground">LIVE</span>
              </div>
            )}
          </div>
        </div>

        {/* Gripper view (d405) */}
        <div className="relative flex flex-col overflow-hidden rounded-md border border-border min-h-0">
          <div className="flex-1 min-h-0">
            <CameraView name="gripper" src={cameras.gripper} />
          </div>
          <div className="flex items-center justify-between border-t border-border px-2 py-1 shrink-0">
            <span className="font-mono text-sm text-muted-foreground">Gripper</span>
            {cameras.gripper && (
              <div className="flex items-center gap-1">
                <span className="relative flex h-1.5 w-1.5">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-foreground/50" />
                  <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-foreground" />
                </span>
                <span className="font-mono text-sm text-foreground">LIVE</span>
              </div>
            )}
          </div>
        </div>

        {/* Nav Map */}
        <div className="relative flex flex-col overflow-hidden rounded-md border border-border min-h-0">
          <div className="flex-1 min-h-0">
            <NavMap
              navState={status.nav_state}
              robotPose={status.robot_pose}
              navPath={status.nav_path}
              sendCommand={sendCommand}
            />
          </div>
          <div className="flex items-center border-t border-border px-2 py-1 shrink-0">
            <span className="font-mono text-sm text-muted-foreground">Map</span>
          </div>
        </div>
      </div>
    </div>
  )
}
