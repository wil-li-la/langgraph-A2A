export type RobotId = "stretch3" | "kinova" | "franka"

export interface Robot {
  id: RobotId
  name: string
  model: string
  status: "connected" | "disconnected" | "connecting"
}

export interface WorkflowNode {
  id: string
  name: string
  label: string
  type: "start" | "process" | "decision" | "error" | "end"
  status: "completed" | "active" | "pending" | "error"
}

export interface WorkflowEdge {
  from: string
  to: string
}

export interface Skill {
  id: string
  name: string
  loaded: boolean
}

export interface RobotTaskData {
  connectedTo: string
  skills: Skill[]
  requiredSkills: string[]
  workflow: {
    nodes: WorkflowNode[]
    edges: WorkflowEdge[]
  }
  videoStreams: {
    gripper: { active: boolean }
    map: { active: boolean }
  }
}

export const robots: Robot[] = [
  { id: "stretch3", name: "Stretch 3", model: "Hello Robot Stretch 3", status: "connected" },
  { id: "kinova", name: "Kinova", model: "Kinova Gen3 7DoF", status: "disconnected" },
  { id: "franka", name: "Franka", model: "Franka Emika Panda", status: "connected" },
]

export const taskData: Record<RobotId, RobotTaskData> = {
  stretch3: {
    connectedTo: "stretch3-unit-01.local:5000",
    skills: [
      { id: "grasp", name: "Grasp", loaded: true },
      { id: "navigation", name: "Navigation", loaded: true },
      { id: "audio", name: "Audio", loaded: false },
    ],
    requiredSkills: ["grasp", "navigation", "audio"],
    workflow: {
      nodes: [
        { id: "start", name: "start", label: "Start", type: "start", status: "completed" },
        { id: "nlu", name: "nlu_parser", label: "nlu_parser_node", type: "process", status: "completed" },
        { id: "check1", name: "should_continue", label: "should_continue_check", type: "decision", status: "completed" },
        { id: "nav", name: "nav_to_pharmacy", label: "navigate_to_pharmacy_node", type: "process", status: "completed" },
        { id: "pickup", name: "pickup_med", label: "pickup_medication_node", type: "process", status: "active" },
        { id: "check2", name: "should_continue", label: "should_continue_check", type: "decision", status: "pending" },
        { id: "delivery", name: "delivery", label: "deliver_to_patient_node", type: "process", status: "pending" },
        { id: "error", name: "handle_error", label: "error_handler_node", type: "error", status: "pending" },
        { id: "end", name: "end", label: "End", type: "end", status: "pending" },
      ],
      edges: [
        { from: "start", to: "nlu" },
        { from: "nlu", to: "check1" },
        { from: "check1", to: "nav" },
        { from: "check1", to: "error" },
        { from: "nav", to: "pickup" },
        { from: "pickup", to: "check2" },
        { from: "check2", to: "delivery" },
        { from: "check2", to: "error" },
        { from: "delivery", to: "end" },
        { from: "error", to: "end" },
      ],
    },
    videoStreams: {
      gripper: { active: true },
      map: { active: true },
    },
  },
  kinova: {
    connectedTo: "",
    skills: [
      { id: "grasp", name: "Grasp", loaded: false },
      { id: "navigation", name: "Navigation", loaded: false },
      { id: "audio", name: "Audio", loaded: false },
    ],
    requiredSkills: [],
    workflow: {
      nodes: [],
      edges: [],
    },
    videoStreams: {
      gripper: { active: false },
      map: { active: false },
    },
  },
  franka: {
    connectedTo: "franka-desk.local:443",
    skills: [
      { id: "grasp", name: "Grasp", loaded: true },
      { id: "navigation", name: "Navigation", loaded: true },
      { id: "audio", name: "Audio", loaded: true },
    ],
    requiredSkills: ["grasp", "navigation"],
    workflow: {
      nodes: [
        { id: "start", name: "start", label: "Start", type: "start", status: "completed" },
        { id: "nlu", name: "nlu_parser", label: "nlu_parser_node", type: "process", status: "completed" },
        { id: "check1", name: "should_continue", label: "should_continue_check", type: "decision", status: "completed" },
        { id: "nav", name: "nav_to_pharmacy", label: "navigate_to_pharmacy_node", type: "process", status: "completed" },
        { id: "pickup", name: "pickup_med", label: "pickup_medication_node", type: "process", status: "completed" },
        { id: "check2", name: "should_continue", label: "should_continue_check", type: "decision", status: "completed" },
        { id: "delivery", name: "delivery", label: "deliver_to_patient_node", type: "process", status: "active" },
        { id: "error", name: "handle_error", label: "error_handler_node", type: "error", status: "pending" },
        { id: "end", name: "end", label: "End", type: "end", status: "pending" },
      ],
      edges: [
        { from: "start", to: "nlu" },
        { from: "nlu", to: "check1" },
        { from: "check1", to: "nav" },
        { from: "check1", to: "error" },
        { from: "nav", to: "pickup" },
        { from: "pickup", to: "check2" },
        { from: "check2", to: "delivery" },
        { from: "check2", to: "error" },
        { from: "delivery", to: "end" },
        { from: "error", to: "end" },
      ],
    },
    videoStreams: {
      gripper: { active: true },
      map: { active: false },
    },
  },
}
