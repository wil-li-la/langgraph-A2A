"use client";

import { useState } from "react";
import type { RobotCommand } from "@/lib/teleop-protocol";

interface TtsInputProps {
  sendCommand: (cmd: RobotCommand) => void;
  onSend?: (text: string) => void;
}

export function TtsInput({ sendCommand, onSend }: TtsInputProps) {
  const [text, setText] = useState("");

  const send = () => {
    if (text.trim()) {
      sendCommand({ type: "tts", text: text.trim() });
      onSend?.(text.trim());
      setText("");
    }
  };

  return (
    <div className="flex gap-2">
      <input
        placeholder="Text to speech..."
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && send()}
        className="flex-1 h-12 rounded-md border border-border bg-background px-3 font-mono text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
      />
      <button
        className="h-12 rounded-md border border-blue-400/25 bg-blue-400/5 px-5 font-mono text-sm font-medium transition-colors hover:bg-blue-400/15 active:bg-blue-400/25 disabled:opacity-30"
        onClick={send}
        disabled={!text.trim()}
      >
        SPEAK
      </button>
    </div>
  );
}
