"use client";

const LEVELS = [
  { label: "0.25x", value: 0.25 },
  { label: "0.5x", value: 0.5 },
  { label: "1x", value: 1.0 },
  { label: "1.5x", value: 1.5 },
  { label: "2x", value: 2.0 },
] as const;

interface SpeedScaleProps {
  scale: number;
  onChange: (scale: number) => void;
}

export function SpeedScale({ scale, onChange }: SpeedScaleProps) {
  return (
    <div className="rounded-md border border-border p-2">
      <div className="mb-1.5 font-mono text-[10px] text-muted-foreground tracking-wide">
        SPEED
      </div>
      <div className="flex gap-1">
        {LEVELS.map((lvl) => (
          <button
            key={lvl.value}
            className={`flex-1 rounded-md border py-1.5 font-mono text-xs transition-colors ${
              scale === lvl.value
                ? "border-foreground/30 bg-foreground/10 text-foreground"
                : "border-border text-muted-foreground hover:bg-foreground/5 hover:text-foreground"
            }`}
            onClick={() => onChange(lvl.value)}
          >
            {lvl.label}
          </button>
        ))}
      </div>
    </div>
  );
}
