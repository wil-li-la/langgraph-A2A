"use client";

import type { RobotCommand } from "@/lib/teleop-protocol";

interface HomeButtonProps {
  isHomed: boolean;
  sendCommand: (cmd: RobotCommand) => void;
}

export function HomeButton({ isHomed, sendCommand }: HomeButtonProps) {
  return (
    <button
      className="w-full rounded-md border border-border py-3 font-mono text-sm font-medium transition-colors hover:bg-foreground/5 active:bg-foreground/10"
      onClick={() => sendCommand({ type: "home" })}
    >
      {isHomed ? "RE-HOME ROBOT" : "HOME ROBOT"}
    </button>
  );
}
