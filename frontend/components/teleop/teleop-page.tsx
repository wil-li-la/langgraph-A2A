"use client";

import { useCallback, useState } from "react";
import { useRobotConnection } from "@/contexts/robot-connection";
import { NavBar } from "@/components/nav-bar";
import { StatusBar } from "./status-bar";
import { CameraView } from "./camera-view";
import { NavMap } from "./nav-map";
import { ChatLog, type ChatEntry } from "./chat-log";
import { DrivePad } from "./drive-pad";
import { JointControls } from "./joint-controls";
import { HeadControls } from "./head-controls";
import { GripperButtons } from "./gripper-buttons";
import { SpeedScale } from "./speed-scale";
import { RunstopButton } from "./runstop-button";
import { HomeButton } from "./home-button";
import { TtsInput } from "./tts-input";

let chatIdCounter = 0;

export function TeleopPage() {
  const [speedScale, setSpeedScale] = useState(1.0);
  const [chatEntries, setChatEntries] = useState<ChatEntry[]>([]);

  const { status, cameras, isConnected, sendCommand } = useRobotConnection();

  const addChatEntry = useCallback((kind: "speech" | "listen", text: string) => {
    const time = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    setChatEntries((prev) => [...prev, { id: ++chatIdCounter, time, kind, text }]);
  }, []);

  return (
    <div className="flex flex-col h-dvh bg-background text-foreground overflow-hidden">
      <NavBar />

      {/* Status bar */}
      <div className="border-b border-border px-4 py-1 shrink-0">
        <StatusBar status={status} isConnected={isConnected} />
      </div>

      {/* Three-column layout: Drive | Cameras | Arm */}
      <div className="flex-1 grid grid-cols-[220px_1fr_220px] gap-2 p-2 min-h-0">

        {/* Left column: Mobility controls (left hand) */}
        <div className="flex flex-col gap-2 min-h-0 overflow-y-auto">
          <RunstopButton runstop={status.runstop} sendCommand={sendCommand} />
          <DrivePad sendCommand={sendCommand} speedScale={speedScale} disabled={status.nav_state === "navigating"} />
          <HeadControls sendCommand={sendCommand} speedScale={speedScale} />
          <SpeedScale scale={speedScale} onChange={setSpeedScale} />
        </div>

        {/* Center column: TTS + Chat on top, Cameras below */}
        <div className="flex flex-col gap-2 min-h-0">
          {/* TTS + Chat (top, above cameras so keyboard doesn't block) */}
          <div className="shrink-0">
            <TtsInput sendCommand={sendCommand} onSend={(text) => addChatEntry("speech", text)} />
          </div>
          <div className="shrink-0 h-[80px] overflow-hidden">
            <ChatLog entries={chatEntries} />
          </div>

          {/* 2×2 camera/map grid */}
          <div className="flex-1 grid grid-cols-2 grid-rows-2 gap-2 min-h-0">
            <div className="rounded-md border border-border overflow-hidden min-h-0">
              <CameraView name="overhead" src={cameras.overhead} />
            </div>
            <div className="rounded-md border border-border overflow-hidden min-h-0">
              <CameraView name="realsense" src={cameras.realsense} />
            </div>
            <div className="rounded-md border border-border overflow-hidden min-h-0">
              <CameraView name="gripper" src={cameras.gripper} />
            </div>
            <div className="rounded-md border border-border overflow-hidden min-h-0">
              <NavMap
                navState={status.nav_state}
                robotPose={status.robot_pose}
                navPath={status.nav_path}
                sendCommand={sendCommand}
              />
            </div>
          </div>
        </div>

        {/* Right column: Arm/manipulation controls (right hand) */}
        <div className="flex flex-col gap-2 min-h-0 overflow-y-auto">
          <JointControls joints={status.joints} sendCommand={sendCommand} speedScale={speedScale} />
          <GripperButtons joints={status.joints} sendCommand={sendCommand} speedScale={speedScale} />
          <HomeButton isHomed={status.is_homed} sendCommand={sendCommand} />
        </div>

      </div>
    </div>
  );
}
