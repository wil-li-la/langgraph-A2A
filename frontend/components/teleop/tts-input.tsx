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
    <div className="flex gap-1.5">
      <input
        placeholder="Text to speech..."
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && send()}
        className="flex-1 rounded-md border border-border bg-background px-2 py-1.5 font-mono text-xs text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:ring-1 focus:ring-ring"
      />
      <button
        className="rounded-md border border-border px-3 py-1.5 font-mono text-xs transition-colors hover:bg-foreground/5 disabled:opacity-30"
        onClick={send}
        disabled={!text.trim()}
      >
        SPEAK
      </button>
    </div>
  );
}
