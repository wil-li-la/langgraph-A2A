"""A2A protocol integration for the medication delivery agent.

Real A2A: callers send a Message containing a DataPart whose `data` is
`{"patient": str, "medicine": str}`. No free-form parsing, no fallbacks.
"""

import asyncio
import logging
from typing import Any

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import (
    DataPart,
    InternalError,
    InvalidParamsError,
    Part,
    TaskState,
    TextPart,
    UnsupportedOperationError,
)
from a2a.utils import new_agent_text_message, new_task
from a2a.utils.errors import ServerError

from app.workflows.medication_delivery import MedicationDeliveryAgent


logger = logging.getLogger(__name__)


class MedicationAgentExecutor(AgentExecutor):
    """A2A AgentExecutor for the medication delivery workflow."""

    def __init__(self) -> None:
        self.medication_agent = MedicationDeliveryAgent()

    @staticmethod
    def _extract_payload(context: RequestContext) -> dict[str, Any] | None:
        """Pull the first DataPart or JSON TextPart payload out of the inbound A2A message."""
        import json
        message = context.message
        if message is None or not message.parts:
            return None
        for part in message.parts:
            root = part.root
            if isinstance(root, DataPart) and isinstance(root.data, dict):
                return root.data
            elif isinstance(root, TextPart) and root.text:
                try:
                    text = root.text.strip()
                    # Strip markdown blocks just in case
                    if text.startswith("```json"):
                        text = text[7:]
                    elif text.startswith("```"):
                        text = text[3:]
                    if text.endswith("```"):
                        text = text[:-3]
                    text = text.strip()
                    logger.info(f"Attempting to parse JSON: {text}")
                    data = json.loads(text)
                    logger.info(f"Parsed JSON: {data}")
                    if isinstance(data, dict):
                        return data
                except Exception as e:
                    logger.error(f"Failed to parse text as JSON: {e}")
                    pass
        return None

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        payload = self._extract_payload(context)
        if payload is None:
            raise ServerError(error=InvalidParamsError(
                message="Expected a DataPart with {'patient': str, 'medicine': str}.",
            ))

        patient = str(payload.get("patient", "")).strip()
        medicine = str(payload.get("medicine", "")).strip()
        if not patient or not medicine:
            raise ServerError(error=InvalidParamsError(
                message="DataPart must contain non-empty 'patient' and 'medicine' fields.",
            ))

        task = context.current_task
        if not task:
            task = new_task(context.message)  # type: ignore[arg-type]
            await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue, task.id, task.context_id)

        try:
            logger.info(f"A2A medication delivery: patient={patient}, medicine={medicine}")

            await updater.update_status(
                TaskState.working,
                new_agent_text_message(
                    f"🤖 啟動給藥流程：{patient} / {medicine}",
                    task.context_id,
                    task.id,
                ),
            )

            # Offload the blocking LangGraph run to a thread so we don't pin
            # the event loop for the entire delivery (minutes). Without this,
            # the server cannot serve any other request — including the
            # dashboard introspection or a competing A2A call — until the
            # workflow finishes.
            result = await asyncio.to_thread(
                self.medication_agent.execute, patient, medicine, mode="auto"
            )

            structured = {
                "task_status": result.get("task_status"),
                "patient_name": result.get("patient_name"),
                "medication_name": result.get("medication_name"),
                "current_location": result.get("current_location"),
                "target_detected": result.get("target_detected"),
                "identity_verified": result.get("identity_verified"),
                "errors": result.get("errors", []),
                "history": result.get("history", []),
                "executed_nodes": result.get("executed_nodes", []),
            }

            if result.get("task_status") == "delivered":
                summary_lines = [
                    "✅ 給藥任務完成",
                    f"病患: {structured['patient_name']}",
                    f"藥物: {structured['medication_name']}",
                    f"位置: {structured['current_location']}",
                ]
                summary_lines.append("執行歷程:")
                summary_lines.extend(f"  {entry}" for entry in structured["history"])
                await updater.add_artifact(
                    [
                        Part(root=TextPart(text="\n".join(summary_lines))),
                        Part(root=DataPart(data=structured)),
                    ],
                    name="medication_delivery_result",
                )
                await updater.complete()
                return

            failure_lines = [
                "❌ 給藥任務失敗",
                f"狀態: {structured['task_status']}",
            ]
            if structured["errors"]:
                failure_lines.append("錯誤:")
                failure_lines.extend(f"  ✗ {err}" for err in structured["errors"])
            await updater.add_artifact(
                [
                    Part(root=TextPart(text="\n".join(failure_lines))),
                    Part(root=DataPart(data=structured)),
                ],
                name="medication_delivery_result",
            )
            await updater.update_status(TaskState.failed, final=True)

        except ServerError:
            raise
        except Exception as e:
            logger.error(f"Medication delivery execution failed: {e}", exc_info=True)
            raise ServerError(error=InternalError()) from e

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        raise ServerError(error=UnsupportedOperationError())
