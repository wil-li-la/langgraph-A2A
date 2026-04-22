"use client"

import type { RobotId } from "@/lib/mock-data"
import { useWorkflowContext, type WorkflowState } from "@/contexts/workflow-context"

export type { WorkflowState }

/**
 * Read the shared workflow state from context. The `robotId` parameter is
 * currently unused — kept for API compatibility.
 */
// eslint-disable-next-line @typescript-eslint/no-unused-vars
export function useWorkflow(_robotId: RobotId) {
  return useWorkflowContext()
}
