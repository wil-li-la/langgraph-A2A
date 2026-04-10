"use client";

import { useRef, useCallback, useEffect } from "react";
import type { RobotCommand } from "@/lib/teleop-protocol";
import type { JointPositions } from "@/types/robot";
import { JOINT_LIMITS } from "@/types/robot";

interface GripperButtonsProps {
  joints: JointPositions;
  sendCommand: (cmd: RobotCommand) => void;
  speedScale: number;
}

// Radians per step, matching stretch_web_teleop official (0.075 rad)
const BASE_INCREMENT = 0.075;
const REPEAT_MS = 150; // slightly faster than 200ms, matching official feel

const GRIPPER_LIMITS = JOINT_LIMITS.joint_gripper_finger_left ?? [-0.37, 0.17];

export function GripperButtons({ joints, sendCommand, speedScale }: GripperButtonsProps) {
  const timerRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);
  const scaleRef = useRef(speedScale);
  const jointsRef = useRef(joints);
  useEffect(() => { scaleRef.current = speedScale; }, [speedScale]);
  useEffect(() => { jointsRef.current = joints; }, [joints]);

  const startRepeat = useCallback(
    (direction: 1 | -1) => {
      if (timerRef.current) clearInterval(timerRef.current);
      const send = () => {
        const current = jointsRef.current.joint_gripper_finger_left ?? 0;
        const increment = BASE_INCREMENT * direction * scaleRef.current;
        const target = current + increment;

        // Clamp to joint limits
        if (target < GRIPPER_LIMITS[0] || target > GRIPPER_LIMITS[1]) return;

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

  const btn = "flex-1 aspect-square rounded-md border border-border font-mono text-base font-medium transition-colors hover:bg-foreground/5 active:bg-foreground/10";

  return (
    <div className="rounded-md border border-border p-2">
      <div className="flex items-center justify-between mb-1">
        <span className="font-mono text-base text-muted-foreground">Gripper</span>
        <span className="font-mono text-base font-medium text-foreground">
          {current.toFixed(3)} rad ({pct}%)
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
