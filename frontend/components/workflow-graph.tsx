"use client"

import { useCallback, useMemo, useRef, useState } from "react"
import type { WorkflowNode, WorkflowEdge } from "@/lib/mock-data"

interface WorkflowGraphProps {
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
  activeNodeId?: string | null
  isPaused?: boolean
  onNodeClick?: (nodeId: string) => void
}

// Node dimensions
const NODE_W = 120
const NODE_H = 52
const NODE_GAP = 12 // gap between nodes within a phase
const PHASE_PAD = 10 // padding inside phase box
const PHASE_GAP = 14 // gap between phase boxes
const PHASE_LABEL_H = 16 // height for phase label
const SVG_PAD = 16
const TOOLTIP_CLEARANCE = 52 // room below bottom nodes for tooltips (44px tooltip + 4px gap + buffer)

// Define phases: each phase has a label and ordered node IDs
const PHASES = [
  { label: "PREPARE", nodes: ["__start__", "confirm_task"] },
  { label: "TRANSPORT", nodes: ["nav_to_pharmacy", "pickup_med"] },
  { label: "DELIVER", nodes: ["nav_to_patient", "delivery", "check_identity", "hand_medicine"] },
  { label: "RETURN", nodes: ["return_to_origin", "__end__"] },
] as const

function getNodeColor(status: WorkflowNode["status"], type: WorkflowNode["type"]) {
  if (type === "error") {
    return {
      bg: status === "error" ? "rgba(239,68,68,0.25)" : "rgba(255,255,255,0.06)",
      border: status === "error" ? "rgba(239,68,68,0.7)" : "rgba(255,255,255,0.25)",
      text: status === "error" ? "rgba(239,68,68,0.95)" : "rgba(255,255,255,0.8)",
      glow: "rgba(239,68,68,0.5)",
    }
  }
  switch (status) {
    case "completed":
      return {
        bg: "rgba(52,211,153,0.15)",
        border: "rgba(52,211,153,0.55)",
        text: "rgba(255,255,255,0.95)",
        glow: "rgba(52,211,153,0.4)",
      }
    case "active":
      return {
        bg: "rgba(56,189,248,0.20)",
        border: "rgba(56,189,248,0.80)",
        text: "rgba(255,255,255,1)",
        glow: "rgba(56,189,248,0.6)",
      }
    case "pending":
    default:
      return {
        bg: "rgba(255,255,255,0.06)",
        border: "rgba(255,255,255,0.25)",
        text: "rgba(255,255,255,0.7)",
        glow: "transparent",
      }
  }
}

const STATUS_LABELS: Record<string, string> = {
  completed: "完成",
  active: "執行中",
  pending: "等待中",
  error: "錯誤",
}

export function WorkflowGraph({ nodes, edges, activeNodeId, isPaused = false, onNodeClick }: WorkflowGraphProps) {
  const [hoveredNode, setHoveredNode] = useState<string | null>(null)
  const [selectedNode, setSelectedNode] = useState<string | null>(null)
  const scrollContainerRef = useRef<HTMLDivElement>(null)

  // Build a lookup from node ID to WorkflowNode
  const nodeMap = useMemo(() => {
    const m = new Map<string, WorkflowNode>()
    nodes.forEach((n) => m.set(n.id, n))
    return m
  }, [nodes])

  // Compute layout: phase boxes with nodes inside, plus error node below
  const layout = useMemo(() => {
    if (nodes.length === 0) return { positions: new Map(), phases: [] as { label: string; x: number; y: number; w: number; h: number }[], width: 0, height: 0, errorPos: null as { x: number; y: number } | null }

    const positions = new Map<string, { x: number; y: number }>()
    const phaseBounds: { label: string; x: number; y: number; w: number; h: number }[] = []

    let curX = SVG_PAD
    const nodesY = SVG_PAD + PHASE_LABEL_H + PHASE_PAD + NODE_H / 2

    for (const phase of PHASES) {
      const phaseNodeCount = phase.nodes.length
      const phaseInnerW = phaseNodeCount * NODE_W + (phaseNodeCount - 1) * NODE_GAP
      const phaseW = phaseInnerW + PHASE_PAD * 2
      const phaseH = NODE_H + PHASE_PAD * 2 + PHASE_LABEL_H

      // Position nodes within this phase
      let nodeX = curX + PHASE_PAD + NODE_W / 2
      for (const nodeId of phase.nodes) {
        positions.set(nodeId, { x: nodeX, y: nodesY })
        nodeX += NODE_W + NODE_GAP
      }

      phaseBounds.push({
        label: phase.label,
        x: curX,
        y: SVG_PAD,
        w: phaseW,
        h: phaseH,
      })

      curX += phaseW + PHASE_GAP
    }

    // Error node: centered below all phases
    const errorNode = nodes.find((n) => n.type === "error")
    let errorPos: { x: number; y: number } | null = null
    if (errorNode) {
      const totalW = curX - PHASE_GAP
      const errY = nodesY + NODE_H / 2 + 36
      errorPos = { x: totalW / 2, y: errY }
      positions.set(errorNode.id, errorPos)
    }

    const totalWidth = curX - PHASE_GAP + SVG_PAD
    const bottomY = errorPos ? errorPos.y + NODE_H / 2 : nodesY + NODE_H / 2
    const maxY = bottomY + TOOLTIP_CLEARANCE

    return { positions, phases: phaseBounds, width: totalWidth, height: maxY, errorPos }
  }, [nodes])

  const activeEdges = useMemo(() => {
    if (!activeNodeId) return new Set<number>()
    const set = new Set<number>()
    edges.forEach((edge, i) => {
      if (edge.to === activeNodeId) set.add(i)
    })
    return set
  }, [edges, activeNodeId])

  const completedEdgeSet = useMemo(() => {
    const executedSet = new Set(nodes.filter((n) => n.status === "completed").map((n) => n.id))
    const set = new Set<number>()
    edges.forEach((edge, i) => {
      if (executedSet.has(edge.from) && (executedSet.has(edge.to) || edge.to === activeNodeId)) {
        set.add(i)
      }
    })
    return set
  }, [edges, nodes, activeNodeId])

  // Render phase group boxes
  const renderPhases = useCallback(() => {
    return layout.phases.map((phase, i) => (
      <g key={`phase-${i}`}>
        {/* Phase box */}
        <rect
          x={phase.x}
          y={phase.y}
          width={phase.w}
          height={phase.h}
          rx={8}
          fill="rgba(255,255,255,0.04)"
          stroke="rgba(255,255,255,0.15)"
          strokeWidth={1}
        />
        {/* Phase label */}
        <text
          x={phase.x + phase.w / 2}
          y={phase.y + 12}
          textAnchor="middle"
          fontSize="12"
          fontFamily="monospace"
          fontWeight="600"
          letterSpacing="1"
          fill="rgba(255,255,255,0.45)"
        >
          {phase.label}
        </text>
      </g>
    ))
  }, [layout.phases])

  // Render edges
  const renderEdges = useCallback(() => {
    return edges.map((edge, i) => {
      const fromPos = layout.positions.get(edge.from)
      const toPos = layout.positions.get(edge.to)
      if (!fromPos || !toPos) return null

      // Skip self-loops (check_identity retry) — we'll render a special icon instead
      if (edge.from === edge.to) return null

      const isActive = activeEdges.has(i)
      const isCompleted = completedEdgeSet.has(i)

      const edgeColor = isActive
        ? "rgba(56,189,248,0.5)"
        : isCompleted
          ? "rgba(52,211,153,0.3)"
          : "rgba(255,255,255,0.20)"

      const strokeWidth = isActive ? 2 : isCompleted ? 1.5 : 1
      const dashArray = isActive ? undefined : "4 3"
      const pathId = `edge-path-${i}`

      const toIsError = nodeMap.get(edge.to)?.type === "error"
      const sameRow = Math.abs(fromPos.y - toPos.y) < 5

      let pathD: string

      if (sameRow) {
        // Horizontal: right side of from → left side of to
        const x1 = fromPos.x + NODE_W / 2
        const y1 = fromPos.y
        const x2 = toPos.x - NODE_W / 2
        const y2 = toPos.y
        pathD = `M ${x1} ${y1} L ${x2} ${y2}`
      } else if (toIsError) {
        // Down to error node: exit bottom, enter top
        const x1 = fromPos.x
        const y1 = fromPos.y + NODE_H / 2
        const x2 = toPos.x
        const y2 = toPos.y - NODE_H / 2
        const midY = (y1 + y2) / 2
        pathD = `M ${x1} ${y1} C ${x1} ${midY}, ${x2} ${midY}, ${x2} ${y2}`
      } else {
        // General curve
        const x1 = fromPos.x + NODE_W / 2
        const y1 = fromPos.y
        const x2 = toPos.x - NODE_W / 2
        const y2 = toPos.y
        const midX = (x1 + x2) / 2
        pathD = `M ${x1} ${y1} C ${midX} ${y1}, ${midX} ${y2}, ${x2} ${y2}`
      }

      return (
        <g key={i}>
          <path
            id={pathId}
            d={pathD}
            fill="none"
            stroke={edgeColor}
            strokeWidth={strokeWidth}
            strokeDasharray={dashArray}
          />
          {isActive && (
            <circle r="2.5" fill="rgba(56,189,248,0.9)">
              <animateMotion dur="1s" repeatCount="indefinite">
                <mpath xlinkHref={`#${pathId}`} />
              </animateMotion>
              <animate attributeName="opacity" values="1;0.3;1" dur="1s" repeatCount="indefinite" />
            </circle>
          )}
        </g>
      )
    })
  }, [edges, layout.positions, activeEdges, completedEdgeSet, nodeMap])

  // Render nodes
  const renderNodes = useCallback(() => {
    return nodes.map((node) => {
      const pos = layout.positions.get(node.id)
      if (!pos) return null

      const colors = getNodeColor(node.status, node.type)
      const x = pos.x - NODE_W / 2
      const y = pos.y - NODE_H / 2
      const rx = node.type === "start" || node.type === "end" ? NODE_H / 2 : 5
      const isHovered = hoveredNode === node.id
      const isActiveNode = node.status === "active"
      const isCompleted = node.status === "completed"
      const hasRetry = edges.some((e) => e.from === node.id && e.to === node.id)

      return (
        <g
          key={node.id}
          onMouseEnter={() => setHoveredNode(node.id)}
          onMouseLeave={() => setHoveredNode(null)}
          onClick={() => {
            if (isPaused && onNodeClick && node.type !== "start" && node.type !== "end") {
              onNodeClick(node.id)
            } else {
              setSelectedNode(selectedNode === node.id ? null : node.id)
            }
          }}
          style={{ cursor: isPaused && node.type !== "start" && node.type !== "end" ? "pointer" : "default" }}
        >
          {/* Active glow */}
          {isActiveNode && (
            <rect
              x={x - 3} y={y - 3}
              width={NODE_W + 6} height={NODE_H + 6}
              rx={rx + 3} fill="none"
              stroke={colors.glow} strokeWidth={2}
              filter="url(#glow-filter)"
            >
              <animate attributeName="opacity" values="0.6;1;0.6" dur="1.5s" repeatCount="indefinite" />
            </rect>
          )}

          {/* Node rect */}
          <rect
            x={x} y={y}
            width={NODE_W} height={NODE_H}
            rx={rx}
            fill={colors.bg}
            stroke={colors.border}
            strokeWidth={isActiveNode ? 2 : isCompleted ? 1 : 0.5}
            className={isActiveNode ? "node-active" : isCompleted ? "node-completed" : undefined}
          />

          {/* Pause hover highlight */}
          {isPaused && node.type !== "start" && node.type !== "end" && isHovered && (
            <rect
              x={x - 2} y={y - 2}
              width={NODE_W + 4} height={NODE_H + 4}
              rx={rx + 2} fill="none"
              stroke="rgba(56,189,248,0.5)" strokeWidth={1.5} strokeDasharray="4 2"
            />
          )}

          {/* Active pulse */}
          {isActiveNode && (
            <rect x={x} y={y} width={NODE_W} height={NODE_H} rx={rx}
              fill="none" stroke="rgba(56,189,248,0.4)" strokeWidth={1}>
              <animate attributeName="opacity" values="1;0.2;1" dur="2s" repeatCount="indefinite" />
            </rect>
          )}

          {/* Completed check */}
          {isCompleted && (
            <g className="check-pop">
              <circle cx={x + NODE_W - 7} cy={y + 7} r={5} fill="rgba(52,211,153,0.9)" />
              <path d={`M ${x + NODE_W - 10} ${y + 7} l 2 2 l 3 -3`}
                fill="none" stroke="white" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" />
            </g>
          )}

          {/* Retry icon */}
          {hasRetry && (
            <text
              x={x + 8} y={y + 10}
              fontSize="14" fontFamily="monospace"
              fill="rgba(255,255,255,0.3)"
            >↺</text>
          )}

          {/* Node name */}
          <text
            x={pos.x} y={pos.y + 1}
            textAnchor="middle" dominantBaseline="middle"
            fontSize="12" fontFamily="monospace" fontWeight="500"
            fill={colors.text}
          >
            {node.type === "start" ? "START" : node.type === "end" ? "END" : node.name}
          </text>

          {/* Tooltip */}
          {isHovered && node.type !== "start" && node.type !== "end" && (
            <foreignObject
              x={pos.x - 75} y={pos.y + NODE_H / 2 + 4}
              width={150} height={44}
              style={{ pointerEvents: "none" }}
            >
              <div style={{
                background: "rgba(15,15,20,0.95)",
                border: "1px solid rgba(255,255,255,0.30)",
                borderRadius: "5px",
                padding: "3px 6px",
                fontFamily: "monospace",
                fontSize: "12px",
                color: "rgba(255,255,255,0.85)",
                backdropFilter: "blur(8px)",
                textAlign: "center",
              }}>
                <div style={{ fontWeight: 600 }}>{node.label}</div>
                <div style={{
                  color: node.status === "active" ? "rgb(56,189,248)"
                    : node.status === "completed" ? "rgb(52,211,153)"
                    : node.status === "error" ? "rgb(239,68,68)"
                    : "rgba(255,255,255,0.4)",
                  fontWeight: 500, marginTop: "1px",
                }}>
                  {STATUS_LABELS[node.status] || node.status}
                </div>
              </div>
            </foreignObject>
          )}
        </g>
      )
    })
  }, [nodes, edges, layout.positions, hoveredNode, selectedNode, isPaused, onNodeClick])

  if (nodes.length === 0) {
    return (
      <div className="flex h-full items-center justify-center">
        <p className="font-mono text-sm text-muted-foreground">No active workflow</p>
      </div>
    )
  }

  return (
    <div ref={scrollContainerRef} className="h-full overflow-auto">
      <div className="flex h-full w-fit min-w-full items-center justify-center">
        <svg
          width={layout.width}
          height={layout.height}
          viewBox={`0 0 ${layout.width} ${layout.height}`}
        >
          <defs>
            <filter id="glow-filter" x="-50%" y="-50%" width="200%" height="200%">
              <feGaussianBlur stdDeviation="6" result="blur" />
              <feMerge>
                <feMergeNode in="blur" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
          </defs>
          {renderPhases()}
          {renderEdges()}
          {renderNodes()}
        </svg>
      </div>
    </div>
  )
}
