"use client";

import type { RobotCommand } from "@/lib/teleop-protocol";

interface HomeButtonProps {
  isHomed: boolean;
  sendCommand: (cmd: RobotCommand) => void;
}

export function HomeButton({ isHomed, sendCommand }: HomeButtonProps) {
  return (
    <button
      className="w-full h-[66px] rounded-md border border-border font-mono text-base font-medium transition-colors hover:bg-foreground/5 active:bg-foreground/10"
      onClick={() => sendCommand({ type: "home" })}
    >
      {isHomed ? "RE-HOME ROBOT" : "HOME ROBOT"}
    </button>
  );
}
