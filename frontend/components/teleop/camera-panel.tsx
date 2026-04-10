"use client";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { CameraView } from "./camera-view";
import { NavMap } from "./nav-map";
import type { CameraName, NavState, RobotPose, NavPathPoint } from "@/types/robot";
import type { RobotCommand } from "@/lib/teleop-protocol";

interface CameraPanelProps {
  frames: Record<CameraName, string | null>;
  navState: NavState;
  robotPose: RobotPose | null;
  navPath: NavPathPoint[];
  sendCommand: (cmd: RobotCommand) => void;
}

export function CameraPanel({ frames, navState, robotPose, navPath, sendCommand }: CameraPanelProps) {
  return (
    <Tabs defaultValue="overhead" className="h-full flex flex-col">
      <TabsList className="grid w-full grid-cols-3 shrink-0 bg-muted/50">
        <TabsTrigger value="overhead" className="font-mono text-base">OVERHEAD</TabsTrigger>
        <TabsTrigger value="gripper" className="font-mono text-base">GRIPPER</TabsTrigger>
        <TabsTrigger value="map" className="font-mono text-base">MAP</TabsTrigger>
      </TabsList>
      <TabsContent value="overhead" className="mt-1 flex-1 min-h-0">
        <div className="grid grid-cols-2 gap-1 h-full">
          <CameraView name="overhead" src={frames.overhead} />
          <CameraView name="realsense" src={frames.realsense} />
        </div>
      </TabsContent>
      <TabsContent value="gripper" className="mt-1 flex-1 min-h-0">
        <CameraView name="gripper" src={frames.gripper} />
      </TabsContent>
      <TabsContent value="map" className="mt-1 flex-1 min-h-0">
        <NavMap
          navState={navState}
          robotPose={robotPose}
          navPath={navPath}
          sendCommand={sendCommand}
        />
      </TabsContent>
    </Tabs>
  );
}
