"use client"

import { useCallback, useMemo } from "react"
import type { WorkflowNode, WorkflowEdge } from "@/lib/mock-data"

interface WorkflowGraphProps {
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
}

const NODE_WIDTH = 180
const NODE_HEIGHT = 56
const V_GAP = 24
const SVG_PADDING = 24

function getNodeColor(status: WorkflowNode["status"], type: WorkflowNode["type"]) {
  if (type === "error") {
    return {
      bg: status === "error" ? "rgba(255,255,255,0.08)" : "rgba(255,255,255,0.03)",
      border: "rgba(255,255,255,0.15)",
      text: "rgba(255,255,255,0.7)",
    }
  }
  switch (status) {
    case "completed":
      return {
        bg: "rgba(255,255,255,0.08)",
        border: "rgba(255,255,255,0.30)",
        text: "rgba(255,255,255,0.9)",
      }
    case "active":
      return {
        bg: "rgba(255,255,255,0.12)",
        border: "rgba(255,255,255,0.60)",
        text: "rgba(255,255,255,1)",
      }
    case "pending":
      return {
        bg: "rgba(255,255,255,0.02)",
        border: "rgba(255,255,255,0.10)",
        text: "rgba(255,255,255,0.4)",
      }
    default:
      return {
        bg: "rgba(255,255,255,0.02)",
        border: "rgba(255,255,255,0.10)",
        text: "rgba(255,255,255,0.4)",
      }
  }
}

function getNodeShape(type: WorkflowNode["type"]) {
  switch (type) {
    case "start":
    case "end":
      return "rounded-full"
    case "decision":
      return "diamond"
    default:
      return "rect"
  }
}

export function WorkflowGraph({ nodes, edges }: WorkflowGraphProps) {
  // Layout nodes in a vertical flow with branching
  const layout = useMemo(() => {
    if (nodes.length === 0) return { positions: new Map(), width: 0, height: 0 }

    const positions = new Map<string, { x: number; y: number }>()

    // Main flow: start -> nlu -> check1 -> nav -> pickup -> check2 -> delivery -> end
    // Branch: check1 -> error, check2 -> error
    // error -> end

    const mainFlow = nodes.filter((n) => n.type !== "error")
    const errorNode = nodes.find((n) => n.type === "error")

    const centerX = SVG_PADDING + NODE_WIDTH / 2 + 60
    let y = SVG_PADDING

    mainFlow.forEach((node) => {
      positions.set(node.id, { x: centerX, y })
      y += NODE_HEIGHT + V_GAP
    })

    if (errorNode) {
      // Place error node to the right, vertically between the last two nodes if possible
      const nodeIds = Array.from(positions.keys())
      const checkPos = positions.get("check_patient_identity") || positions.get("check2") || positions.get(nodeIds[nodeIds.length - 2])
      const deliveryPos = positions.get("delivery") || positions.get(nodeIds[nodeIds.length - 1])
      if (checkPos && deliveryPos) {
        positions.set(errorNode.id, {
          x: centerX + NODE_WIDTH + 40,
          y: (checkPos.y + deliveryPos.y) / 2,
        })
      } else {
        positions.set(errorNode.id, {
          x: centerX + NODE_WIDTH + 40,
          y,
        })
      }
    }

    let maxX = 0
    let maxY = 0
    positions.forEach((pos) => {
      maxX = Math.max(maxX, pos.x + NODE_WIDTH / 2)
      maxY = Math.max(maxY, pos.y + NODE_HEIGHT)
    })

    return {
      positions,
      width: maxX + SVG_PADDING + 20,
      height: maxY + SVG_PADDING + 10,
    }
  }, [nodes])

  const renderEdges = useCallback(() => {
    return edges.map((edge, i) => {
      const fromPos = layout.positions.get(edge.from)
      const toPos = layout.positions.get(edge.to)
      if (!fromPos || !toPos) return null

      const x1 = fromPos.x
      const y1 = fromPos.y + NODE_HEIGHT / 2
      const x2 = toPos.x
      const y2 = toPos.y - NODE_HEIGHT / 2

      // Determine if this is a straight or curved edge
      if (Math.abs(x1 - x2) < 5) {
        // Straight vertical
        return (
          <line
            key={i}
            x1={x1}
            y1={y1}
            x2={x2}
            y2={y2}
            stroke="rgba(255,255,255,0.15)"
            strokeWidth={1}
            strokeDasharray="6 3"
          />
        )
      }

      // Curved path for branches
      const midY = (y1 + y2) / 2
      return (
        <path
          key={i}
          d={`M ${x1} ${y1} C ${x1} ${midY}, ${x2} ${midY}, ${x2} ${y2}`}
          fill="none"
          stroke="rgba(255,255,255,0.10)"
          strokeWidth={1}
          strokeDasharray="6 3"
        />
      )
    })
  }, [edges, layout.positions])

  const renderNodes = useCallback(() => {
    return nodes.map((node) => {
      const pos = layout.positions.get(node.id)
      if (!pos) return null

      const colors = getNodeColor(node.status, node.type)
      const x = pos.x - NODE_WIDTH / 2
      const y = pos.y - NODE_HEIGHT / 2

      return (
        <g key={node.id}>
          <rect
            x={x}
            y={y}
            width={NODE_WIDTH}
            height={NODE_HEIGHT}
            rx={node.type === "start" || node.type === "end" ? NODE_HEIGHT / 2 : 6}
            fill={colors.bg}
            stroke={colors.border}
            strokeWidth={node.status === "active" ? 1.5 : 0.5}
          />
          {node.status === "active" && (
            <rect
              x={x}
              y={y}
              width={NODE_WIDTH}
              height={NODE_HEIGHT}
              rx={node.type === "start" || node.type === "end" ? NODE_HEIGHT / 2 : 6}
              fill="none"
              stroke="rgba(255,255,255,0.3)"
              strokeWidth={0.5}
            >
              <animate
                attributeName="opacity"
                values="1;0.3;1"
                dur="2s"
                repeatCount="indefinite"
              />
            </rect>
          )}
          {node.type === "decision" && (
            <text
              x={pos.x - NODE_WIDTH / 2 + 12}
              y={pos.y - 2}
              fontSize="14"
              fill={colors.text}
            >
              {"?"}
            </text>
          )}
          <text
            x={pos.x + (node.type === "decision" ? 4 : 0)}
            y={pos.y - 4}
            textAnchor="middle"
            fontSize="12"
            fontFamily="monospace"
            fontWeight="500"
            fill={colors.text}
          >
            {node.name}
          </text>
          <text
            x={pos.x + (node.type === "decision" ? 4 : 0)}
            y={pos.y + 12}
            textAnchor="middle"
            fontSize="9"
            fontFamily="monospace"
            fill="rgba(255,255,255,0.35)"
          >
            {node.label}
          </text>
        </g>
      )
    })
  }, [nodes, layout.positions])

  if (nodes.length === 0) {
    return (
      <div className="flex h-full items-center justify-center">
        <p className="font-mono text-xs text-muted-foreground">No active workflow</p>
      </div>
    )
  }

  return (
    <div className="h-full w-full overflow-auto">
      <svg
        width={layout.width}
        height={layout.height}
        viewBox={`0 0 ${layout.width} ${layout.height}`}
        className="mx-auto"
      >
        {renderEdges()}
        {renderNodes()}
      </svg>
    </div>
  )
}
