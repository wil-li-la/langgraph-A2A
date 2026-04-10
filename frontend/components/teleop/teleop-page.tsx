"use client";

import { useCallback, useState } from "react";
import { useRobotConnection } from "@/contexts/robot-connection";
import { NavBar } from "@/components/nav-bar";
import { StatusBar } from "./status-bar";
import { CameraPanel } from "./camera-panel";
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
      <div className="border-b border-border px-4 py-1.5 shrink-0">
        <StatusBar status={status} isConnected={isConnected} />
      </div>

      {/* Main content */}
      <div className="flex-1 grid grid-cols-[1fr_360px] gap-3 p-3 min-h-0">
        {/* Left: cameras + TTS + chat */}
        <div className="flex flex-col gap-2 min-h-0">
          <div className="h-[60%] shrink-0 min-h-0">
            <CameraPanel
              frames={cameras}
              navState={status.nav_state}
              robotPose={status.robot_pose}
              navPath={status.nav_path}
              sendCommand={sendCommand}
            />
          </div>
          <TtsInput sendCommand={sendCommand} onSend={(text) => addChatEntry("speech", text)} />
          <ChatLog entries={chatEntries} />
        </div>

        {/* Right: controls */}
        <div className="flex flex-col gap-2 min-h-0 overflow-y-auto">
          <RunstopButton runstop={status.runstop} sendCommand={sendCommand} />
          <SpeedScale scale={speedScale} onChange={setSpeedScale} />
          <div className="grid grid-cols-2 gap-2">
            <DrivePad sendCommand={sendCommand} speedScale={speedScale} disabled={status.nav_state === "navigating"} />
            <HeadControls sendCommand={sendCommand} speedScale={speedScale} />
          </div>
          <JointControls joints={status.joints} sendCommand={sendCommand} speedScale={speedScale} />
          <GripperButtons sendCommand={sendCommand} speedScale={speedScale} />
          <HomeButton isHomed={status.is_homed} sendCommand={sendCommand} />
        </div>
      </div>
    </div>
  );
}
