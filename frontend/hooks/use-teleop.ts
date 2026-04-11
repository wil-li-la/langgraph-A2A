"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { RobotStatus, CameraName } from "@/types/robot";
import {
  parseStatusMessage,
  parseCameraFrame,
  CAMERA_ID_OVERHEAD,
  CAMERA_ID_REALSENSE,
  CAMERA_ID_GRIPPER,
  type RobotCommand,
} from "@/lib/teleop-protocol";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:9999";

const DEFAULT_STATUS: RobotStatus = {
  joints: {
    joint_lift: 0,
    wrist_extension: 0,
    joint_head_pan: 0,
    joint_head_tilt: 0,
    joint_wrist_yaw: 0,
    joint_wrist_pitch: 0,
    joint_wrist_roll: 0,
    joint_gripper_finger_left: 0,
    translate_mobile_base: 0,
    rotate_mobile_base: 0,
  },
  battery: { voltage: 0, is_charging: false, is_low_voltage: false },
  runstop: false,
  is_homed: false,
  nav_state: "idle",
  robot_pose: null,
  nav_path: [],
};

type CameraFrames = Record<CameraName, string | null>;

export function useTeleop() {
  const [status, setStatus] = useState<RobotStatus>(DEFAULT_STATUS);
  const [isConnected, setIsConnected] = useState(false);
  const [cameras, setCameras] = useState<CameraFrames>({
    overhead: null,
    realsense: null,
    gripper: null,
  });
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined
  );
  const currentUrlRef = useRef<string | null>(null);

  const cleanup = useCallback(() => {
    if (reconnectTimer.current) {
      clearTimeout(reconnectTimer.current);
      reconnectTimer.current = undefined;
    }
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    setIsConnected(false);
  }, []);

  const connect = useCallback(
    (robotAddress: string) => {
      cleanup();

      // Build the backend relay URL
      const wsBase = API_BASE.replace(/^http/, "ws");
      const wsUrl = `${wsBase}/ws/teleop?robot=${encodeURIComponent(robotAddress)}`;
      currentUrlRef.current = robotAddress;

      const ws = new WebSocket(wsUrl);
      ws.binaryType = "arraybuffer";

      ws.onopen = () => {
        setIsConnected(true);
        if (reconnectTimer.current) {
          clearTimeout(reconnectTimer.current);
          reconnectTimer.current = undefined;
        }
      };

      ws.onmessage = (event) => {
        if (typeof event.data === "string") {
          const parsed = parseStatusMessage(event.data);
          if (parsed) setStatus(parsed);
        } else if (event.data instanceof ArrayBuffer) {
          const frame = parseCameraFrame(event.data);
          if (!frame) return;

          let name: CameraName;
          switch (frame.cameraId) {
            case CAMERA_ID_OVERHEAD:
              name = "overhead";
              break;
            case CAMERA_ID_REALSENSE:
              name = "realsense";
              break;
            case CAMERA_ID_GRIPPER:
              name = "gripper";
              break;
            default:
              return;
          }

          const url = URL.createObjectURL(frame.jpeg);
          setCameras((prev) => {
            const oldUrl = prev[name];
            if (oldUrl) URL.revokeObjectURL(oldUrl);
            return { ...prev, [name]: url };
          });
        }
      };

      ws.onclose = () => {
        setIsConnected(false);
        wsRef.current = null;
        // Auto-reconnect after 2s
        if (currentUrlRef.current) {
          reconnectTimer.current = setTimeout(
            () => connect(currentUrlRef.current!),
            2000
          );
        }
      };

      ws.onerror = () => {
        ws.close();
      };

      wsRef.current = ws;
    },
    [cleanup]
  );

  const disconnect = useCallback(() => {
    currentUrlRef.current = null;
    cleanup();
  }, [cleanup]);

  const sendCommand = useCallback((cmd: RobotCommand) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(cmd));
    }
  }, []);

  // Note: no cleanup on unmount — this hook lives in the root provider
  // and should persist across page navigation. The WebSocket connection
  // is only closed explicitly via disconnect().

  return { status, cameras, isConnected, sendCommand, connect, disconnect };
}
