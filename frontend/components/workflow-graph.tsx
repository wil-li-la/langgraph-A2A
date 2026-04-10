"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import type { WorkflowNode, WorkflowEdge } from "@/lib/mock-data"

interface WorkflowGraphProps {
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
  activeNodeId?: string | null
  isPaused?: boolean
  onNodeClick?: (nodeId: string) => void
}

const NODE_WIDTH = 180
const NODE_HEIGHT = 56
const V_GAP = 24
const SVG_PADDING = 48

function getNodeColor(status: WorkflowNode["status"], type: WorkflowNode["type"]) {
  if (type === "error") {
    return {
      bg: status === "error" ? "rgba(239,68,68,0.15)" : "rgba(255,255,255,0.03)",
      border: status === "error" ? "rgba(239,68,68,0.6)" : "rgba(255,255,255,0.15)",
      text: status === "error" ? "rgba(239,68,68,0.9)" : "rgba(255,255,255,0.7)",
      glow: "rgba(239,68,68,0.4)",
    }
  }
  switch (status) {
    case "completed":
      return {
        bg: "rgba(52,211,153,0.08)",
        border: "rgba(52,211,153,0.40)",
        text: "rgba(255,255,255,0.9)",
        glow: "rgba(52,211,153,0.3)",
      }
    case "active":
      return {
        bg: "rgba(56,189,248,0.12)",
        border: "rgba(56,189,248,0.70)",
        text: "rgba(255,255,255,1)",
        glow: "rgba(56,189,248,0.5)",
      }
    case "pending":
      return {
        bg: "rgba(255,255,255,0.02)",
        border: "rgba(255,255,255,0.10)",
        text: "rgba(255,255,255,0.4)",
        glow: "transparent",
      }
    default:
      return {
        bg: "rgba(255,255,255,0.02)",
        border: "rgba(255,255,255,0.10)",
        text: "rgba(255,255,255,0.4)",
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

  // Layout nodes in a vertical flow with branching
  const layout = useMemo(() => {
    if (nodes.length === 0) return { positions: new Map(), width: 0, height: 0 }

    const positions = new Map<string, { x: number; y: number }>()

    const mainFlow = nodes.filter((n) => n.type !== "error")
    const errorNode = nodes.find((n) => n.type === "error")

    const centerX = SVG_PADDING + NODE_WIDTH / 2 + 60
    let y = SVG_PADDING

    mainFlow.forEach((node) => {
      positions.set(node.id, { x: centerX, y })
      y += NODE_HEIGHT + V_GAP
    })

    if (errorNode) {
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

  // Auto-scroll to active node
  useEffect(() => {
    if (!activeNodeId || !scrollContainerRef.current) return

    const pos = layout.positions.get(activeNodeId)
    if (!pos) return

    const container = scrollContainerRef.current
    const { offsetWidth, offsetHeight } = container

    // Target position is centered
    const targetX = pos.x - offsetWidth / 2
    const targetY = pos.y - offsetHeight / 2

    container.scrollTo({
      left: targetX,
      top: targetY,
      behavior: "smooth",
    })
  }, [activeNodeId, layout])

  // Determine which edges are "active" (leading to the currently executing node)
  const activeEdges = useMemo(() => {
    if (!activeNodeId) return new Set<number>()
    const set = new Set<number>()
    edges.forEach((edge, i) => {
      if (edge.to === activeNodeId) set.add(i)
    })
    return set
  }, [edges, activeNodeId])

  // Determine which edges are "completed" (both source and target are executed)
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

  const renderEdges = useCallback(() => {
    return edges.map((edge, i) => {
      const fromPos = layout.positions.get(edge.from)
      const toPos = layout.positions.get(edge.to)
      if (!fromPos || !toPos) return null

      const x1 = fromPos.x
      const y1 = fromPos.y + NODE_HEIGHT / 2
      const x2 = toPos.x
      const y2 = toPos.y - NODE_HEIGHT / 2

      const isActive = activeEdges.has(i)
      const isCompleted = completedEdgeSet.has(i)

      const edgeColor = isActive
        ? "rgba(56,189,248,0.5)"
        : isCompleted
          ? "rgba(52,211,153,0.3)"
          : "rgba(255,255,255,0.12)"

      const strokeWidth = isActive ? 2 : isCompleted ? 1.5 : 1
      const dashArray = isActive ? undefined : "6 3"

      const pathId = `edge-path-${i}`

      if (Math.abs(x1 - x2) < 5) {
        // Straight vertical
        return (
          <g key={i}>
            <line
              x1={x1} y1={y1} x2={x2} y2={y2}
              stroke={edgeColor}
              strokeWidth={strokeWidth}
              strokeDasharray={dashArray}
              className={isActive ? "edge-active" : undefined}
            />
            {/* Animated particle along active edge */}
            {isActive && (
              <>
                <path id={pathId} d={`M ${x1} ${y1} L ${x2} ${y2}`} fill="none" stroke="none" />
                <circle r="3" fill="rgba(56,189,248,0.9)">
                  <animateMotion dur="1.5s" repeatCount="indefinite">
                    <mpath xlinkHref={`#${pathId}`} />
                  </animateMotion>
                  <animate attributeName="opacity" values="1;0.3;1" dur="1.5s" repeatCount="indefinite" />
                </circle>
              </>
            )}
          </g>
        )
      }

      // Curved path for branches
      const midY = (y1 + y2) / 2
      const pathD = `M ${x1} ${y1} C ${x1} ${midY}, ${x2} ${midY}, ${x2} ${y2}`

      return (
        <g key={i}>
          <path
            id={pathId}
            d={pathD}
            fill="none"
            stroke={edgeColor}
            strokeWidth={strokeWidth}
            strokeDasharray={dashArray}
            className={isActive ? "edge-active" : undefined}
          />
          {isActive && (
            <circle r="3" fill="rgba(56,189,248,0.9)">
              <animateMotion dur="2s" repeatCount="indefinite">
                <mpath xlinkHref={`#${pathId}`} />
              </animateMotion>
              <animate attributeName="opacity" values="1;0.3;1" dur="2s" repeatCount="indefinite" />
            </circle>
          )}
        </g>
      )
    })
  }, [edges, layout.positions, activeEdges, completedEdgeSet])

  const renderNodes = useCallback(() => {
    return nodes.map((node) => {
      const pos = layout.positions.get(node.id)
      if (!pos) return null

      const colors = getNodeColor(node.status, node.type)
      const x = pos.x - NODE_WIDTH / 2
      const y = pos.y - NODE_HEIGHT / 2
      const rx = node.type === "start" || node.type === "end" ? NODE_HEIGHT / 2 : 6
      const isHovered = hoveredNode === node.id
      const isSelected = selectedNode === node.id
      const isActiveNode = node.status === "active"
      const isCompleted = node.status === "completed"

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
          {/* Outer glow for active node */}
          {isActiveNode && (
            <rect
              x={x - 4}
              y={y - 4}
              width={NODE_WIDTH + 8}
              height={NODE_HEIGHT + 8}
              rx={rx + 4}
              fill="none"
              stroke={colors.glow}
              strokeWidth={2}
              filter="url(#glow-filter)"
            >
              <animate
                attributeName="opacity"
                values="0.6;1;0.6"
                dur="1.5s"
                repeatCount="indefinite"
              />
            </rect>
          )}

          {/* Main node rect */}
          <rect
            x={x}
            y={y}
            width={NODE_WIDTH}
            height={NODE_HEIGHT}
            rx={rx}
            fill={colors.bg}
            stroke={isSelected ? "rgba(99,102,241,0.7)" : colors.border}
            strokeWidth={isActiveNode ? 2 : isSelected ? 1.5 : isCompleted ? 1 : 0.5}
            className={isActiveNode ? "node-active" : isCompleted ? "node-completed" : undefined}
          />

          {/* Resume click highlight when paused */}
          {isPaused && node.type !== "start" && node.type !== "end" && isHovered && (
            <rect
              x={x - 2}
              y={y - 2}
              width={NODE_WIDTH + 4}
              height={NODE_HEIGHT + 4}
              rx={rx + 2}
              fill="none"
              stroke="rgba(56,189,248,0.5)"
              strokeWidth={1.5}
              strokeDasharray="4 2"
            />
          )}

          {/* Inner pulse for active node */}
          {isActiveNode && (
            <rect
              x={x}
              y={y}
              width={NODE_WIDTH}
              height={NODE_HEIGHT}
              rx={rx}
              fill="none"
              stroke="rgba(56,189,248,0.4)"
              strokeWidth={1}
            >
              <animate
                attributeName="opacity"
                values="1;0.2;1"
                dur="2s"
                repeatCount="indefinite"
              />
            </rect>
          )}

          {/* Completed checkmark */}
          {isCompleted && (
            <g className="check-pop">
              <circle
                cx={x + NODE_WIDTH - 8}
                cy={y + 8}
                r={7}
                fill="rgba(52,211,153,0.9)"
              />
              <path
                d={`M ${x + NODE_WIDTH - 12} ${y + 8} l 3 3 l 5 -5`}
                fill="none"
                stroke="white"
                strokeWidth={1.5}
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </g>
          )}

          {/* Decision icon */}
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

          {/* Node name */}
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

          {/* Node label */}
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

          {/* Hover tooltip */}
          {isHovered && (
            <foreignObject
              x={pos.x + NODE_WIDTH / 2 + 8}
              y={pos.y - 30}
              width={160}
              height={60}
              style={{ pointerEvents: "none" }}
            >
              <div
                style={{
                  background: "rgba(15,15,20,0.95)",
                  border: "1px solid rgba(255,255,255,0.15)",
                  borderRadius: "6px",
                  padding: "6px 10px",
                  fontFamily: "monospace",
                  fontSize: "10px",
                  color: "rgba(255,255,255,0.85)",
                  backdropFilter: "blur(8px)",
                }}
              >
                <div style={{ fontWeight: 600, marginBottom: "2px" }}>{node.name}</div>
                <div style={{ color: "rgba(255,255,255,0.5)" }}>{node.label}</div>
                <div style={{
                  color: node.status === "active" ? "rgb(56,189,248)"
                    : node.status === "completed" ? "rgb(52,211,153)"
                    : node.status === "error" ? "rgb(239,68,68)"
                    : "rgba(255,255,255,0.4)",
                  fontWeight: 500,
                  marginTop: "2px",
                }}>
                  {STATUS_LABELS[node.status] || node.status}
                </div>
              </div>
            </foreignObject>
          )}
        </g>
      )
    })
  }, [nodes, layout.positions, hoveredNode, selectedNode, isPaused, onNodeClick])

  // Selected node detail
  const selectedNodeData = nodes.find((n) => n.id === selectedNode)

  if (nodes.length === 0) {
    return (
      <div className="flex h-full items-center justify-center">
        <p className="font-mono text-xs text-muted-foreground">No active workflow</p>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col">
      <div ref={scrollContainerRef} className="flex-1 overflow-auto">
        <svg
          width={layout.width}
          height={layout.height}
          viewBox={`0 0 ${layout.width} ${layout.height}`}
          className="mx-auto"
        >
          {/* SVG filters for glow effect */}
          <defs>
            <filter id="glow-filter" x="-50%" y="-50%" width="200%" height="200%">
              <feGaussianBlur stdDeviation="6" result="blur" />
              <feMerge>
                <feMergeNode in="blur" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
          </defs>
          {renderEdges()}
          {renderNodes()}
        </svg>
      </div>

      {/* Selected node detail bar */}
      {selectedNodeData && (
        <div className="border-t border-border bg-background/80 px-4 py-2 backdrop-blur-sm">
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2">
              <div
                className="h-2 w-2 rounded-full"
                style={{
                  background: selectedNodeData.status === "active" ? "rgb(56,189,248)"
                    : selectedNodeData.status === "completed" ? "rgb(52,211,153)"
                    : selectedNodeData.status === "error" ? "rgb(239,68,68)"
                    : "rgba(255,255,255,0.3)",
                }}
              />
              <span className="font-mono text-[11px] font-medium text-foreground">
                {selectedNodeData.name}
              </span>
            </div>
            <span className="font-mono text-[10px] text-muted-foreground">
              {selectedNodeData.label}
            </span>
            <span className="ml-auto font-mono text-[10px]" style={{
              color: selectedNodeData.status === "active" ? "rgb(56,189,248)"
                : selectedNodeData.status === "completed" ? "rgb(52,211,153)"
                : selectedNodeData.status === "error" ? "rgb(239,68,68)"
                : "rgba(255,255,255,0.4)",
            }}>
              {STATUS_LABELS[selectedNodeData.status] || selectedNodeData.status}
            </span>
          </div>
        </div>
      )}
    </div>
  )
}
