"""A2A protocol integration for medication delivery agent."""

import logging
import re

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import (
    InternalError,
    InvalidParamsError,
    Part,
    TaskState,
    TextPart,
    UnsupportedOperationError,
)
from a2a.utils import (
    new_agent_text_message,
    new_task,
)
from a2a.utils.errors import ServerError

from app.healthcare import MedicationDeliveryAgent
from app.healthcare.mock_data import MockNLU


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class MedicationAgentExecutor(AgentExecutor):
    """A2A AgentExecutor implementation for medication delivery agent."""
    
    def __init__(self):
        """Initialize the executor with medication delivery agent."""
        self.medication_agent = MedicationDeliveryAgent()

    @staticmethod
    def _parse_a2a_instruction(query: str) -> dict:
        """Extract patient_name and medication_name from free-form A2A instructions.

        Supports patterns like:
          - 請機器人送 drug a 給 張雅安
          - 送 阿斯匹靈 給 張小明
          - deliver Aspirin to John Smith
        """
        # Chinese: 送 <med> 給 <patient>
        m = re.search(r"送\s*(.+?)\s*給\s*(.+?)(?:\s*$)", query)
        if m:
            return {
                "success": True,
                "medication_name": m.group(1).strip(),
                "patient_name": m.group(2).strip(),
                "message": "A2A 指令解析成功",
            }

        # English: deliver <med> to <patient>
        m = re.search(r"deliver\s+(.+?)\s+to\s+(.+?)(?:\s*$)", query, re.IGNORECASE)
        if m:
            return {
                "success": True,
                "medication_name": m.group(1).strip(),
                "patient_name": m.group(2).strip(),
                "message": "A2A instruction parsed",
            }

        return {
            "success": False,
            "patient_name": None,
            "medication_name": None,
            "message": "無法解析指令，請使用「送 <藥物> 給 <病患>」或「deliver <med> to <patient>」格式",
        }

    @staticmethod
    def _is_capabilities_query(query: str) -> bool:
        """Detect lightweight intro/capability questions."""
        normalized = query.strip().lower()
        if not normalized:
            return False

        # Keep this intentionally simple and conservative.
        patterns = (
            r"\bwhat do you do\b",
            r"\bwhat can you do\b",
            r"\bcapabilit(?:y|ies)\b",
            r"\bintroduce yourself\b",
            r"你會做什麼",
            r"你可以做什麼",
            r"你能做什麼",
            r"你是誰",
            r"介紹一下",
            r"功能",
        )
        return any(re.search(pattern, normalized) for pattern in patterns)

    @staticmethod
    def _capabilities_response() -> str:
        """Return a concise capability summary and usage examples."""
        return (
            "🤖 我是給藥服務代理（Medication Delivery Agent）。\n\n"
            "自主給藥流程：\n"
            "1) 導航至藥局取藥\n"
            "2) 導航至病患位置\n"
            "3) 語音確認病患身份\n"
            "4) 遞交藥物並回報結果\n\n"
            '請使用 POST /api/a2a/execute {"patient": "王大同", "medicine": "維他命C"} 呼叫。'
        )
    
    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        """Execute medication delivery task via A2A protocol.
        
        Args:
            context: Request context containing user input and task info
            event_queue: Queue for sending A2A events back to the client
            
        Raises:
            ServerError: If validation fails or execution encounters an error
        """
        # Validate the request
        error = self._validate_request(context)
        if error:
            raise ServerError(error=InvalidParamsError())
        
        # Extract user input
        query = context.get_user_input()
        task = context.current_task
        
        # Create a new task if one doesn't exist
        if not task:
            task = new_task(context.message)  # type: ignore
            await event_queue.enqueue_event(task)
        
        # Task updater for managing task lifecycle
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        
        try:
            logger.info(f"Executing medication delivery: {query}")

            # Friendly response for intro/capability questions.
            if self._is_capabilities_query(query):
                response = self._capabilities_response()
                await updater.add_artifact(
                    [Part(root=TextPart(text=response))],
                    name='capabilities_intro',
                )
                await updater.complete()
                return
            
            # Parse instruction into patient_name + medication_name.
            # Try MockNLU first (matches known DB entries); fall back to
            # regex extraction so A2A commands with arbitrary names still work.
            # If all parsing fails, use hardcoded defaults (temporary).
            parsed = MockNLU.parse_instruction(query)
            if not parsed["success"]:
                parsed = self._parse_a2a_instruction(query)
            if not parsed["success"]:
                # TEMPORARY: always run workflow regardless of command
                logger.warning(f"Could not parse instruction, using defaults: {query}")
                parsed = {
                    "success": True,
                    "patient_name": "張小明",
                    "medication_name": "阿斯匹靈",
                    "message": "使用預設值執行",
                }
            
            # Send initial status
            await updater.update_status(
                TaskState.working,
                new_agent_text_message(
                    "🤖 啟動給藥系統...",
                    task.context_id,
                    task.id,
                ),
            )
            
            # Run medication delivery agent (synchronous) — auto mode skips mock DB validation
            result = self.medication_agent.execute(
                parsed["patient_name"], parsed["medication_name"], mode="auto"
            )
            
            # Determine final status based on result
            if result['task_status'] == 'delivered':
                response = f"✅ 給藥任務完成！\n\n"
                response += f"病患: {result['patient_name']}\n"
                response += f"藥物: {result['medication_name']}\n"
                response += f"位置: {result['current_location']}\n\n"
                response += "執行歷程:\n"
                for entry in result.get('history', []):
                    response += f"  {entry}\n"
                
                await updater.add_artifact(
                    [Part(root=TextPart(text=response))],
                    name='medication_delivery_result',
                )
                await updater.complete()
            else:
                response = f"❌ 給藥任務失敗\n\n"
                response += f"狀態: {result['task_status']}\n\n"
                if result.get('errors'):
                    response += "錯誤:\n"
                    for error in result['errors']:
                        response += f"  ✗ {error}\n"
                
                await updater.update_status(
                    TaskState.input_required,
                    new_agent_text_message(
                        response,
                        task.context_id,
                        task.id,
                    ),
                    final=True,
                )
        
        except Exception as e:
            logger.error(f'An error occurred during medication delivery: {e}')
            raise ServerError(error=InternalError()) from e
    
    def _validate_request(self, context: RequestContext) -> bool:
        """Validate the incoming request.
        
        Args:
            context: Request context to validate
            
        Returns:
            True if validation fails, False if request is valid
        """
        # Accept all requests for medication delivery
        return False
    
    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        """Handle task cancellation.
        
        Args:
            context: Request context
            event_queue: Event queue for sending cancellation events
            
        Raises:
            ServerError: Cancellation is not currently supported
        """
        raise ServerError(error=UnsupportedOperationError())
