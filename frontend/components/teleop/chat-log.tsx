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
    <div className="h-full overflow-y-auto rounded-md border border-border bg-background/50 p-2 font-mono text-sm space-y-1">
      {entries.length === 0 && (
        <div className="text-muted-foreground text-center py-4">No messages</div>
      )}
      {entries.map((e) => (
        <div key={e.id} className="flex gap-2">
          <span className="text-muted-foreground shrink-0">{e.time}</span>
          <span className="text-muted-foreground shrink-0">{e.kind === "speech" ? "TTS" : "ASR"}</span>
          <span className="text-foreground break-words">{e.text}</span>
        </div>
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
