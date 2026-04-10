"use client";

import { useEffect, useRef } from "react";

export interface ChatEntry {
  id: number;
  time: string;
  kind: "speech" | "listen";
  text: string;
}

interface ChatLogProps {
  entries: ChatEntry[];
}

export function ChatLog({ entries }: ChatLogProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [entries.length]);

  return (
    <div className="flex-1 min-h-0 overflow-y-auto rounded-md border border-border bg-background/50 p-2 font-mono text-[11px] space-y-0.5">
      {entries.length === 0 && (
        <div className="text-muted-foreground/40 text-center py-4">No messages</div>
      )}
      {entries.map((e) => (
        <div key={e.id} className="flex gap-1.5">
          <span className="text-muted-foreground shrink-0">{e.time}</span>
          <span className="text-muted-foreground shrink-0">{e.kind === "speech" ? "TTS" : "ASR"}</span>
          <span className="text-foreground break-words">{e.text}</span>
        </div>
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
