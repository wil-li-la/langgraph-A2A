"use client";

import { useRef, useCallback, useEffect } from "react";
import type { RobotCommand } from "@/lib/teleop-protocol";
import type { JointName, JointPositions } from "@/types/robot";
import { JOINT_INCREMENTS, JOINT_LABELS } from "@/types/robot";

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
  "joint_gripper_finger_left",
];

const REPEAT_MS = 200;

export function JointControls({ joints, sendCommand, speedScale }: JointControlsProps) {
  const timerRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);
  const scaleRef = useRef(speedScale);
  useEffect(() => { scaleRef.current = speedScale; }, [speedScale]);

  const startRepeat = useCallback(
    (name: JointName, direction: 1 | -1) => {
      if (timerRef.current) clearInterval(timerRef.current);
      const send = () => {
        const inc = (JOINT_INCREMENTS[name] ?? 0.05) * direction * scaleRef.current;
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

  const btn = "h-11 w-11 shrink-0 rounded-md border border-border font-mono text-base transition-colors hover:bg-foreground/5 active:bg-foreground/10";

  return (
    <div className="rounded-md border border-border p-2">
      <div className="mb-1.5 font-mono text-[10px] text-muted-foreground tracking-wide">
        JOINTS
      </div>
      <div className="space-y-1">
        {CONTROLLED_JOINTS.map((name) => (
          <div key={name} className="flex items-center gap-2">
            <button className={btn}
              onPointerDown={() => startRepeat(name, -1)} onPointerUp={stopRepeat} onPointerLeave={stopRepeat}>
              -
            </button>
            <div className="flex-1 min-w-0">
              <div className="font-mono text-[10px] text-muted-foreground truncate">
                {JOINT_LABELS[name] ?? name}
              </div>
              <div className="font-mono text-xs text-foreground">
                {joints[name]?.toFixed(3) ?? "0.000"}
              </div>
            </div>
            <button className={btn}
              onPointerDown={() => startRepeat(name, 1)} onPointerUp={stopRepeat} onPointerLeave={stopRepeat}>
              +
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
