"use client";

import { useCallback, useState } from "react";
import { useTeleop } from "@/hooks/use-teleop";
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
  const [robotHost, setRobotHost] = useState("");
  const [speedScale, setSpeedScale] = useState(1.0);
  const [chatEntries, setChatEntries] = useState<ChatEntry[]>([]);

  const { status, cameras, isConnected, sendCommand, connect, disconnect } = useTeleop();

  const handleConnect = () => {
    if (robotHost.trim()) {
      const host = robotHost.trim();
      const url = host.includes("://") ? host : `ws://${host}:8765`;
      connect(url);
    }
  };

  const addChatEntry = useCallback((kind: "speech" | "listen", text: string) => {
    const time = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    setChatEntries((prev) => [...prev, { id: ++chatIdCounter, time, kind, text }]);
  }, []);

  return (
    <div className="flex flex-col h-dvh bg-background text-foreground overflow-hidden">
      <NavBar />

      {/* Connection header */}
      <div className="border-b border-border px-4 py-2 flex items-center gap-3">
        <div className="flex gap-1.5 flex-1 max-w-sm">
          <input
            placeholder="Robot IP or ws://host:port"
            value={robotHost}
            onChange={(e) => setRobotHost(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleConnect()}
            className="flex-1 rounded-md border border-border bg-background px-2 py-1 font-mono text-xs text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:ring-1 focus:ring-ring"
          />
          <button
            onClick={isConnected ? disconnect : handleConnect}
            className={`rounded-md border px-3 py-1 font-mono text-xs transition-colors ${
              isConnected
                ? "border-border text-muted-foreground hover:bg-foreground/5"
                : "border-foreground/20 bg-foreground/10 text-foreground hover:bg-foreground/15"
            }`}
          >
            {isConnected ? "Disconnect" : "Connect"}
          </button>
        </div>
        <div className="ml-auto">
          <StatusBar status={status} isConnected={isConnected} />
        </div>
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
