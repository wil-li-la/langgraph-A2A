export interface JointPositions {
  joint_lift: number;
  wrist_extension: number;
  joint_head_pan: number;
  joint_head_tilt: number;
  joint_wrist_yaw: number;
  joint_wrist_pitch: number;
  joint_wrist_roll: number;
  joint_gripper_finger_left: number;
  translate_mobile_base: number;
  rotate_mobile_base: number;
}

export interface BatteryState {
  voltage: number;
  is_charging: boolean;
  is_low_voltage: boolean;
}

export interface RobotStatus {
  joints: JointPositions;
  battery: BatteryState;
  runstop: boolean;
  is_homed: boolean;
  nav_state: NavState;
  robot_pose: RobotPose | null;
  nav_path: NavPathPoint[];
}

export type JointName = keyof JointPositions;

export const JOINT_INCREMENTS: Partial<Record<JointName, number>> = {
  joint_lift: 0.05,
  wrist_extension: 0.05,
  joint_head_pan: 0.1,
  joint_head_tilt: 0.1,
  joint_wrist_yaw: 0.2,
  joint_wrist_pitch: 0.2,
  joint_wrist_roll: 0.2,
  joint_gripper_finger_left: 3,
  translate_mobile_base: 0.1,
  rotate_mobile_base: 0.2,
};

export const JOINT_LABELS: Partial<Record<JointName, string>> = {
  joint_lift: "Lift",
  wrist_extension: "Arm",
  joint_head_pan: "Head Pan",
  joint_head_tilt: "Head Tilt",
  joint_wrist_yaw: "Wrist Yaw",
  joint_wrist_pitch: "Wrist Pitch",
  joint_wrist_roll: "Wrist Roll",
  joint_gripper_finger_left: "Gripper",
};

export const CAMERA_NAMES = ["overhead", "realsense", "gripper"] as const;
export type CameraName = (typeof CAMERA_NAMES)[number];

export type NavState = "idle" | "navigating" | "succeeded" | "failed";

export interface RobotPose {
  x: number;
  y: number;
  theta: number;
}

export interface NavPathPoint {
  x: number;
  y: number;
}

export const MAP_CONFIG = {
  imageUrl: "/maps/305_map.png",
  resolution: 0.05,
  originX: -7.99,
  originY: -3.23,
  width: 250,
  height: 183,
} as const;
