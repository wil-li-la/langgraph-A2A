"""A2A protocol integration layer for the LangGraph agent."""

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

from app.agent import ConversationalAgent


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ConversationalAgentExecutor(AgentExecutor):
    """A2A AgentExecutor implementation for the conversational agent."""
    
    def __init__(self):
        """Initialize the executor with the LangGraph agent."""
        self.agent = ConversationalAgent()
    
    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        """Execute the agent and stream responses via A2A protocol.
        
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
            # Stream agent responses
            async for item in self.agent.stream(query, task.context_id):
                is_task_complete = item['is_task_complete']
                require_user_input = item['require_user_input']
                
                # Agent is still working
                if not is_task_complete and not require_user_input:
                    await updater.update_status(
                        TaskState.working,
                        new_agent_text_message(
                            item['content'],
                            task.context_id,
                            task.id,
                        ),
                    )
                
                # Agent needs more input from user
                elif require_user_input:
                    await updater.update_status(
                        TaskState.input_required,
                        new_agent_text_message(
                            item['content'],
                            task.context_id,
                            task.id,
                        ),
                        final=True,
                    )
                    break
                
                # Task completed successfully
                else:
                    await updater.add_artifact(
                        [Part(root=TextPart(text=item['content']))],
                        name='agent_response',
                    )
                    await updater.complete()
                    break
        
        except Exception as e:
            logger.error(f'An error occurred while streaming the response: {e}')
            raise ServerError(error=InternalError()) from e
    
    def _validate_request(self, context: RequestContext) -> bool:
        """Validate the incoming request.
        
        Args:
            context: Request context to validate
            
        Returns:
            True if validation fails, False if request is valid
        """
        # Add custom validation logic here if needed
        # For now, accept all requests
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
