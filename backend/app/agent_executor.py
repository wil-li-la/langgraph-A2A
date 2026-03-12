"""A2A protocol integration for medication delivery agent."""

import logging

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
            
            # Parse instruction into patient_name + medication_name
            parsed = MockNLU.parse_instruction(query)
            if not parsed["success"]:
                await updater.update_status(
                    TaskState.input_required,
                    new_agent_text_message(
                        f"❌ {parsed['message']}",
                        task.context_id,
                        task.id,
                    ),
                    final=True,
                )
                return
            
            # Send initial status
            await updater.update_status(
                TaskState.working,
                new_agent_text_message(
                    "🤖 啟動給藥系統...",
                    task.context_id,
                    task.id,
                ),
            )
            
            # Run medication delivery agent (synchronous)
            result = self.medication_agent.execute(parsed["patient_name"], parsed["medication_name"])
            
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
