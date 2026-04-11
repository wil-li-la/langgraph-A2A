"use client";

import type { RobotCommand } from "@/lib/teleop-protocol";

interface HomeButtonProps {
  isHomed: boolean;
  sendCommand: (cmd: RobotCommand) => void;
}

export function HomeButton({ isHomed, sendCommand }: HomeButtonProps) {
  return (
    <button
      className="w-full h-[66px] rounded-md border border-blue-400/25 bg-blue-400/5 font-mono text-sm font-medium transition-colors hover:bg-blue-400/15 active:bg-blue-400/25"
      onClick={() => sendCommand({ type: "home" })}
    >
      {isHomed ? "RE-HOME ROBOT" : "HOME ROBOT"}
    </button>
  );
}
