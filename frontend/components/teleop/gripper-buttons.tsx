"use client";

import { useRef, useCallback, useEffect } from "react";
import type { RobotCommand } from "@/lib/teleop-protocol";

interface GripperButtonsProps {
  sendCommand: (cmd: RobotCommand) => void;
  speedScale: number;
}

const BASE_INCREMENT = 3;
const REPEAT_MS = 200;

export function GripperButtons({ sendCommand, speedScale }: GripperButtonsProps) {
  const timerRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);
  const scaleRef = useRef(speedScale);
  useEffect(() => { scaleRef.current = speedScale; }, [speedScale]);

  const startRepeat = useCallback(
    (direction: 1 | -1) => {
      if (timerRef.current) clearInterval(timerRef.current);
      const send = () =>
        sendCommand({
          type: "increment_joint",
          name: "joint_gripper_finger_left",
          increment: BASE_INCREMENT * direction * scaleRef.current,
        });
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

  const btn = "flex-1 rounded-md border border-border py-3 font-mono text-sm font-medium transition-colors hover:bg-foreground/5 active:bg-foreground/10";

  return (
    <div className="flex gap-1">
      <button className={btn}
        onPointerDown={() => startRepeat(1)} onPointerUp={stopRepeat} onPointerLeave={stopRepeat}>
        OPEN
      </button>
      <button className={btn}
        onPointerDown={() => startRepeat(-1)} onPointerUp={stopRepeat} onPointerLeave={stopRepeat}>
        CLOSE
      </button>
    </div>
  );
}
