"use client";

import type { RobotStatus } from "@/types/robot";

interface StatusBarProps {
  status: RobotStatus;
  isConnected: boolean;
}

export function StatusBar({ status, isConnected }: StatusBarProps) {
  return (
    <div className="flex items-center gap-2 flex-wrap font-mono text-[10px]">
      <span className="flex items-center gap-1.5">
        <span
          className={`inline-block h-1.5 w-1.5 rounded-full ${
            isConnected ? "bg-foreground" : "bg-muted-foreground/30"
          }`}
        />
        <span className={isConnected ? "text-foreground" : "text-muted-foreground/50"}>
          {isConnected ? "CONNECTED" : "DISCONNECTED"}
        </span>
      </span>
      <span className="text-muted-foreground">
        {status.battery.voltage.toFixed(1)}V
        {status.battery.is_charging && " CHG"}
      </span>
      <span className={status.is_homed ? "text-foreground" : "text-muted-foreground/50"}>
        {status.is_homed ? "HOMED" : "NOT HOMED"}
      </span>
      {status.runstop && (
        <span className="text-red-400">RUNSTOP</span>
      )}
    </div>
  );
}
