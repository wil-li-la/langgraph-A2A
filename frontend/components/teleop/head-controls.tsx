"use client";

import { useRef, useCallback, useEffect } from "react";
import type { RobotCommand } from "@/lib/teleop-protocol";

interface HeadControlsProps {
  sendCommand: (cmd: RobotCommand) => void;
  speedScale: number;
}

const BASE_PAN = 0.15;
const BASE_TILT = 0.15;
const REPEAT_MS = 200;

export function HeadControls({ sendCommand, speedScale }: HeadControlsProps) {
  const timerRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);
  const scaleRef = useRef(speedScale);
  useEffect(() => { scaleRef.current = speedScale; }, [speedScale]);

  const startRepeat = useCallback(
    (name: "joint_head_pan" | "joint_head_tilt", base: number, direction: 1 | -1) => {
      if (timerRef.current) clearInterval(timerRef.current);
      const send = () =>
        sendCommand({ type: "increment_joint", name, increment: base * direction * scaleRef.current });
      send();
      timerRef.current = setInterval(send, REPEAT_MS);
    },
    [sendCommand],
  );

  const stopRepeat = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = undefined;
    }
  }, []);

  const btn = "aspect-square rounded-md border border-border font-mono text-base font-medium transition-colors hover:bg-foreground/5 active:bg-foreground/10";
  const preset = "flex-1 h-[66px] rounded-md border border-border font-mono text-base font-medium text-muted-foreground transition-colors hover:bg-foreground/5 hover:text-foreground text-center";

  return (
    <div className="rounded-md border border-border p-2">
      <div className="mb-2 font-mono text-lg font-medium text-muted-foreground tracking-wide">
        HEAD
      </div>
      <div className="grid grid-cols-3 gap-1.5">
        <div />
        <button className={btn}
          onPointerDown={() => startRepeat("joint_head_tilt", BASE_TILT, 1)} onPointerUp={stopRepeat} onPointerLeave={stopRepeat}>
          Up
        </button>
        <div />
        <button className={btn}
          onPointerDown={() => startRepeat("joint_head_pan", BASE_PAN, 1)} onPointerUp={stopRepeat} onPointerLeave={stopRepeat}>
          L
        </button>
        <button className={btn}
          onPointerDown={() => startRepeat("joint_head_tilt", BASE_TILT, -1)} onPointerUp={stopRepeat} onPointerLeave={stopRepeat}>
          Dn
        </button>
        <button className={btn}
          onPointerDown={() => startRepeat("joint_head_pan", BASE_PAN, -1)} onPointerUp={stopRepeat} onPointerLeave={stopRepeat}>
          R
        </button>
      </div>
      <div className="mt-1.5 flex gap-1 justify-center">
        <button className={preset} onClick={() => sendCommand({ type: "look_at", camera: "forward" })}>
          Forward
        </button>
        <button className={preset} onClick={() => sendCommand({ type: "look_at", camera: "gripper" })}>
          Gripper
        </button>
      </div>
    </div>
  );
}
