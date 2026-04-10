import type { JointName, RobotStatus } from "@/types/robot";

// --- Commands (browser -> robot) ---

export type RobotCommand =
  | { type: "drive"; linear: number; angular: number }
  | { type: "increment_joint"; name: JointName; increment: number }
  | { type: "set_pose"; pose: Partial<Record<JointName, number>> }
  | { type: "stop" }
  | { type: "set_runstop"; enabled: boolean }
  | { type: "home" }
  | { type: "tts"; text: string }
  | { type: "look_at"; camera: string }
  | { type: "nav_goal"; x: number; y: number; theta: number }
  | { type: "cancel_nav" };

// --- Camera binary protocol ---
// Binary messages: [1-byte camera_id][JPEG bytes]
export const CAMERA_ID_OVERHEAD = 0;
export const CAMERA_ID_REALSENSE = 1;
export const CAMERA_ID_GRIPPER = 2;

export function parseStatusMessage(data: string): RobotStatus | null {
  try {
    const msg = JSON.parse(data);
    if (msg.type === "status") {
      return {
        joints: msg.joints,
        battery: msg.battery,
        runstop: msg.runstop,
        is_homed: msg.is_homed,
        nav_state: msg.nav_state ?? "idle",
        robot_pose: msg.robot_pose ?? null,
        nav_path: msg.nav_path ?? [],
      };
    }
  } catch {
    // ignore malformed messages
  }
  return null;
}

export function parseCameraFrame(data: ArrayBuffer): {
  cameraId: number;
  jpeg: Blob;
} | null {
  if (data.byteLength < 2) return null;
  const view = new Uint8Array(data);
  const cameraId = view[0];
  const jpeg = new Blob([view.slice(1)], { type: "image/jpeg" });
  return { cameraId, jpeg };
}
