"use client";

import { useCallback, useEffect, useState } from "react";
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
import { subscribeNavStatus, type NavTaskState } from "@/lib/nav-api";
import { useNavStatus } from "@/contexts/nav-status";

let chatIdCounter = 0;

export function TeleopPage() {
  const [speedScale, setSpeedScale] = useState(1.0);
  const [chatEntries, setChatEntries] = useState<ChatEntry[]>([]);
  // Backend's autonomous-nav state. While "pending"/"running", the dashboard's
  // drive controls are disabled — both ends would otherwise publish cmd_vel
  // and the robot's 200ms watchdog would resolve to whichever arrived last.
  const [navState, setNavState] = useState<NavTaskState>("idle");

  useEffect(() => subscribeNavStatus((snap) => setNavState(snap.task.state)), []);

  const navInFlight = navState === "pending" || navState === "running";

  const { status, cameras, isConnected, sendCommand } = useRobotConnection();
  // AMCL-localized pose in the `amcl_map` frame (from backend /api/nav SSE).
  // The teleop WS `status.robot_pose` is raw wheel odom and drifts — don't
  // use it for the static-map overlay.
  const { pose: amclPose } = useNavStatus();

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
      <div className="flex-1 grid grid-cols-[220px_1fr_260px] gap-2 p-2 min-h-0">

        {/* Left column: Mobility controls (left hand) */}
        <div className="flex flex-col gap-2 min-h-0 overflow-y-auto">
          <RunstopButton runstop={status.runstop} sendCommand={sendCommand} />
          {navInFlight && (
            <div className="rounded-md border border-amber-500/50 bg-amber-500/10 px-2 py-1.5 text-center font-mono text-xs text-amber-700 dark:text-amber-300">
              Backend nav running — drive locked
            </div>
          )}
          <DrivePad sendCommand={sendCommand} speedScale={speedScale} disabled={status.nav_state === "navigating" || navInFlight} />
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
            <div className="flex flex-col rounded-md border border-border overflow-hidden min-h-0">
              <div className="flex-1 min-h-0">
                <CameraView name="overhead" src={cameras.overhead} />
              </div>
              <div className="flex items-center justify-between border-t border-border px-2 py-1 shrink-0">
                <span className="font-mono text-sm text-muted-foreground">Overhead</span>
                {cameras.overhead && (
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
            <div className="flex flex-col rounded-md border border-border overflow-hidden min-h-0">
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
            <div className="flex flex-col rounded-md border border-border overflow-hidden min-h-0">
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
            <div className="flex flex-col rounded-md border border-border overflow-hidden min-h-0">
              <div className="flex-1 min-h-0">
                <NavMap
                  navState={status.nav_state}
                  robotPose={amclPose}
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
