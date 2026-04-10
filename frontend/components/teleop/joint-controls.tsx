"use client";

import { useRef, useCallback, useEffect } from "react";
import type { RobotCommand } from "@/lib/teleop-protocol";
import type { JointName, JointPositions } from "@/types/robot";
import { JOINT_INCREMENTS, JOINT_LIMITS, JOINT_LABELS } from "@/types/robot";

interface JointControlsProps {
  joints: JointPositions;
  sendCommand: (cmd: RobotCommand) => void;
  speedScale: number;
}

const CONTROLLED_JOINTS: JointName[] = [
  "joint_lift",
  "wrist_extension",
  "joint_wrist_yaw",
  "joint_wrist_pitch",
  "joint_wrist_roll",
];

const REPEAT_MS = 150;

export function JointControls({ joints, sendCommand, speedScale }: JointControlsProps) {
  const timerRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);
  const scaleRef = useRef(speedScale);
  const jointsRef = useRef(joints);
  useEffect(() => { scaleRef.current = speedScale; }, [speedScale]);
  useEffect(() => { jointsRef.current = joints; }, [joints]);

  const startRepeat = useCallback(
    (name: JointName, direction: 1 | -1) => {
      if (timerRef.current) clearInterval(timerRef.current);
      const send = () => {
        const inc = (JOINT_INCREMENTS[name] ?? 0.05) * direction * scaleRef.current;
        const current = jointsRef.current[name] ?? 0;
        const target = current + inc;

        // Clamp to joint limits if defined
        const limits = JOINT_LIMITS[name];
        if (limits && (target < limits[0] || target > limits[1])) return;

        sendCommand({ type: "increment_joint", name, increment: inc });
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

  const btn = "flex-1 aspect-square rounded-md border border-border font-mono text-xl font-medium transition-colors hover:bg-foreground/5 active:bg-foreground/10";

  return (
    <div className="rounded-md border border-border p-2">
      <div className="mb-2 font-mono text-lg font-medium text-muted-foreground tracking-wide">
        JOINTS
      </div>
      <div className="space-y-2">
        {CONTROLLED_JOINTS.map((name) => {
          const inv = name === "joint_wrist_yaw" ? -1 : 1;
          const value = joints[name] ?? 0;
          const limits = JOINT_LIMITS[name];
          return (
            <div key={name}>
              <div className="flex items-center justify-between mb-1">
                <span className="font-mono text-base text-muted-foreground">
                  {JOINT_LABELS[name] ?? name}
                </span>
                <span className="font-mono text-base font-medium text-foreground">
                  {value.toFixed(3)} rad
                  {limits && (
                    <span className="text-muted-foreground ml-1">
                      [{limits[0]}, {limits[1]}]
                    </span>
                  )}
                </span>
              </div>
              <div className="grid grid-cols-2 gap-1.5">
                <button className={btn}
                  onPointerDown={() => startRepeat(name, (-1 * inv) as 1 | -1)} onPointerUp={stopRepeat} onPointerLeave={stopRepeat}>
                  -
                </button>
                <button className={btn}
                  onPointerDown={() => startRepeat(name, (1 * inv) as 1 | -1)} onPointerUp={stopRepeat} onPointerLeave={stopRepeat}>
                  +
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
