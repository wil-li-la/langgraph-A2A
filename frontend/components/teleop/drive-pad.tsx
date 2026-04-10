"use client";

import { useCallback, useEffect, useRef } from "react";
import type { RobotCommand } from "@/lib/teleop-protocol";

interface DrivePadProps {
  sendCommand: (cmd: RobotCommand) => void;
  speedScale: number;
  disabled?: boolean;
}

const BASE_LIN = 0.15;
const BASE_ANG = 0.4;
const REPEAT_MS = 100;

export function DrivePad({ sendCommand, speedScale, disabled = false }: DrivePadProps) {
  const activeKeys = useRef(new Set<string>());
  const intervalRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);
  const btnTimerRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);
  const scaleRef = useRef(speedScale);
  useEffect(() => { scaleRef.current = speedScale; }, [speedScale]);

  const computeAndSend = useCallback(() => {
    const keys = activeKeys.current;
    const s = scaleRef.current;
    let linear = 0;
    let angular = 0;

    if (keys.has("w") || keys.has("arrowup")) linear += BASE_LIN * s;
    if (keys.has("s") || keys.has("arrowdown")) linear -= BASE_LIN * s;
    if (keys.has("a") || keys.has("arrowleft")) angular += BASE_ANG * s;
    if (keys.has("d") || keys.has("arrowright")) angular -= BASE_ANG * s;

    sendCommand({ type: "drive", linear, angular });
  }, [sendCommand]);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (!e.key) return;
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (disabled) return;
      const key = e.key.toLowerCase();
      if (["w", "a", "s", "d", "arrowup", "arrowdown", "arrowleft", "arrowright"].includes(key)) {
        e.preventDefault();
        if (!activeKeys.current.has(key)) {
          activeKeys.current.add(key);
          computeAndSend();
          if (!intervalRef.current) {
            intervalRef.current = setInterval(computeAndSend, REPEAT_MS);
          }
        }
      }
    };

    const onKeyUp = (e: KeyboardEvent) => {
      if (!e.key) return;
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      const key = e.key.toLowerCase();
      activeKeys.current.delete(key);
      if (activeKeys.current.size === 0) {
        if (intervalRef.current) {
          clearInterval(intervalRef.current);
          intervalRef.current = undefined;
        }
        sendCommand({ type: "drive", linear: 0, angular: 0 });
      } else {
        computeAndSend();
      }
    };

    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [sendCommand, computeAndSend, disabled]);

  const startDrive = useCallback(
    (linear: number, angular: number) => {
      const send = () =>
        sendCommand({ type: "drive", linear: linear * scaleRef.current, angular: angular * scaleRef.current });
      send();
      btnTimerRef.current = setInterval(send, REPEAT_MS);
    },
    [sendCommand],
  );

  const stopDrive = useCallback(() => {
    if (btnTimerRef.current) {
      clearInterval(btnTimerRef.current);
      btnTimerRef.current = undefined;
    }
    sendCommand({ type: "drive", linear: 0, angular: 0 });
  }, [sendCommand]);

  const btn = "h-14 w-14 rounded-md border border-border font-mono text-xs transition-colors hover:bg-foreground/5 active:bg-foreground/10 disabled:opacity-30";

  return (
    <div className="rounded-md border border-border p-2">
      <div className="mb-1.5 font-mono text-[10px] text-muted-foreground tracking-wide">
        DRIVE (WASD)
      </div>
      <div className="grid grid-cols-3 gap-1 w-fit mx-auto">
        <div />
        <button className={btn} disabled={disabled}
          onPointerDown={() => !disabled && startDrive(BASE_LIN, 0)} onPointerUp={stopDrive} onPointerLeave={stopDrive}>
          W
        </button>
        <div />
        <button className={btn} disabled={disabled}
          onPointerDown={() => !disabled && startDrive(0, BASE_ANG)} onPointerUp={stopDrive} onPointerLeave={stopDrive}>
          A
        </button>
        <button className={btn} disabled={disabled}
          onPointerDown={() => !disabled && startDrive(-BASE_LIN, 0)} onPointerUp={stopDrive} onPointerLeave={stopDrive}>
          S
        </button>
        <button className={btn} disabled={disabled}
          onPointerDown={() => !disabled && startDrive(0, -BASE_ANG)} onPointerUp={stopDrive} onPointerLeave={stopDrive}>
          D
        </button>
      </div>
      <button
        className="mt-1.5 w-full rounded-md border border-red-400/30 bg-red-400/10 py-2 font-mono text-xs text-red-400 hover:bg-red-400/20 disabled:opacity-30"
        disabled={disabled}
        onClick={() => sendCommand({ type: "stop" })}
      >
        {disabled ? "NAVIGATING..." : "STOP"}
      </button>
    </div>
  );
}
