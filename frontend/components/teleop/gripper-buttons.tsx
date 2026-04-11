"use client";

import { useRef, useCallback, useEffect } from "react";
import type { RobotCommand } from "@/lib/teleop-protocol";
import type { JointPositions } from "@/types/robot";
import { JOINT_LIMITS, JOINT_INCREMENTS } from "@/types/robot";

interface GripperButtonsProps {
  joints: JointPositions;
  sendCommand: (cmd: RobotCommand) => void;
  speedScale: number;
}

const BASE_INCREMENT = JOINT_INCREMENTS.joint_gripper_finger_left ?? 3;
const REPEAT_MS = 150;

const GRIPPER_LIMITS = JOINT_LIMITS.joint_gripper_finger_left ?? [-0.37, 0.17];

export function GripperButtons({ joints, sendCommand, speedScale }: GripperButtonsProps) {
  const timerRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);
  const scaleRef = useRef(speedScale);
  useEffect(() => { scaleRef.current = speedScale; }, [speedScale]);

  const startRepeat = useCallback(
    (direction: 1 | -1) => {
      if (timerRef.current) clearInterval(timerRef.current);
      const send = () => {
        const increment = BASE_INCREMENT * direction * scaleRef.current;
        sendCommand({
          type: "increment_joint",
          name: "joint_gripper_finger_left",
          increment,
        });
      };
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

  const current = joints.joint_gripper_finger_left ?? 0;
  const range = GRIPPER_LIMITS[1] - GRIPPER_LIMITS[0];
  const pct = Math.round(((current - GRIPPER_LIMITS[0]) / range) * 100);

  const btn = "flex-1 aspect-square rounded-md border border-blue-400/25 bg-blue-400/5 font-mono text-base font-medium transition-colors hover:bg-blue-400/15 active:bg-blue-400/25";

  return (
    <div className="rounded-md border border-border p-2">
      <div className="flex items-center justify-between mb-1">
        <span className="font-mono text-sm text-muted-foreground">Gripper</span>
        <span className="font-mono text-sm font-medium text-foreground text-right">
          <div>{current.toFixed(3)} rad</div>
          <div className="text-muted-foreground">
            [{GRIPPER_LIMITS[0]}, {GRIPPER_LIMITS[1]}]
          </div>
        </span>
      </div>
      <div className="grid grid-cols-2 gap-1.5">
        <button className={btn}
          onPointerDown={() => startRepeat(-1)} onPointerUp={stopRepeat} onPointerLeave={stopRepeat}>
          CLOSE
        </button>
        <button className={btn}
          onPointerDown={() => startRepeat(1)} onPointerUp={stopRepeat} onPointerLeave={stopRepeat}>
          OPEN
        </button>
      </div>
    </div>
  );
}
