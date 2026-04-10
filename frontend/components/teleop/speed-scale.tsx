"use client";

interface SpeedScaleProps {
  scale: number;
  onChange: (scale: number) => void;
}

const MIN = 0.25;
const MAX = 2.0;
const STEP = 0.25;

export function SpeedScale({ scale, onChange }: SpeedScaleProps) {
  return (
    <div className="rounded-md border border-border p-3">
      <div className="flex items-center justify-between mb-3">
        <span className="font-mono text-sm font-medium text-muted-foreground tracking-wide">
          SPEED
        </span>
        <span className="font-mono text-sm font-bold text-foreground">
          {scale.toFixed(2)}x
        </span>
      </div>
      <input
        type="range"
        min={MIN}
        max={MAX}
        step={STEP}
        value={scale}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        className="w-full h-10 cursor-pointer accent-foreground"
        style={{
          WebkitAppearance: "none",
          appearance: "none",
          background: "transparent",
        }}
      />
      <div className="flex justify-between mt-1 font-mono text-sm text-muted-foreground">
        <span>{MIN}x</span>
        <span>1x</span>
        <span>{MAX}x</span>
      </div>
    </div>
  );
}
