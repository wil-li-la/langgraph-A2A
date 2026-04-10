"use client";

import type { RobotCommand } from "@/lib/teleop-protocol";

interface RunstopButtonProps {
  runstop: boolean;
  sendCommand: (cmd: RobotCommand) => void;
}

export function RunstopButton({ runstop, sendCommand }: RunstopButtonProps) {
  return (
    <button
      className={`w-full h-[66px] rounded-md border font-mono text-lg font-bold tracking-wide transition-colors ${
        runstop
          ? "border-border bg-background text-foreground hover:bg-foreground/5"
          : "border-red-400/30 bg-red-400/10 text-red-400 hover:bg-red-400/20"
      }`}
      onClick={() => sendCommand({ type: "set_runstop", enabled: !runstop })}
    >
      {runstop ? "RELEASE RUNSTOP" : "RUNSTOP"}
    </button>
  );
}
