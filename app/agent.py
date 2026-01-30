"""LangGraph agent implementation with structured output for A2A protocol."""

import os
from collections.abc import AsyncIterable
from typing import Any, Literal

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel


# Initialize memory for conversation persistence
memory = MemorySaver()


# Example tools - customize these for your use case
@tool
def search_web(query: str) -> str:
    """Search the web for information.
    
    Args:
        query: The search query to look up.
        
    Returns:
        Search results or information about the query.
    """
    # This is a placeholder - integrate with actual search API if needed
    return f"Search results for '{query}': This is a demo agent. Integrate with a real search API for actual results."


@tool
def calculate(expression: str) -> str:
    """Perform mathematical calculations.
    
    Args:
        expression: A mathematical expression to evaluate (e.g., "2 + 2" or "10 * 5").
        
    Returns:
        The result of the calculation.
    """
    try:
        # Safe evaluation of simple math expressions
        result = eval(expression, {"__builtins__": {}}, {})
        return f"The result is: {result}"
    except Exception as e:
        return f"Error calculating '{expression}': {str(e)}"


class ResponseFormat(BaseModel):
    """Structured response format for A2A protocol compliance."""
    
    status: Literal['input_required', 'completed', 'error'] = 'input_required'
    message: str


class ConversationalAgent:
    """A general-purpose conversational agent with A2A protocol support."""
    
    SYSTEM_INSTRUCTION = (
        'You are a helpful AI assistant that can answer questions, search for information, '
        'and perform calculations. Be concise and friendly in your responses. '
        'If you need more information from the user to complete a request, ask for it. '
        'Use the available tools when appropriate to provide accurate information.'
    )
    
    FORMAT_INSTRUCTION = (
        'Set response status to "input_required" if the user needs to provide more information to complete the request. '
        'Set response status to "error" if there is an error while processing the request. '
        'Set response status to "completed" if the request is complete and you have provided a full answer.'
    )
    
    SUPPORTED_CONTENT_TYPES = ['text', 'text/plain']
    
    def __init__(self):
        """Initialize the conversational agent with LLM and tools."""
        model_source = os.getenv('model_source', 'google')
        
        if model_source == 'google':
            self.model = ChatGoogleGenerativeAI(model='gemini-2.0-flash-exp')
        else:
            self.model = ChatOpenAI(
                model=os.getenv('TOOL_LLM_NAME', 'gpt-4'),
                openai_api_key=os.getenv('OPENAI_API_KEY', 'EMPTY'),
                openai_api_base=os.getenv('TOOL_LLM_URL'),
                temperature=0.7,
            )
        
        # Define available tools
        self.tools = [search_web, calculate]
        
        # Create ReAct agent with memory
        self.graph = create_react_agent(
            self.model,
            tools=self.tools,
            checkpointer=memory,
            prompt=self.SYSTEM_INSTRUCTION,
            response_format=(self.FORMAT_INSTRUCTION, ResponseFormat),
        )
    
    async def stream(self, query: str, context_id: str) -> AsyncIterable[dict[str, Any]]:
        """Stream agent responses with status updates.
        
        Args:
            query: User's input query
            context_id: Conversation context ID for memory persistence
            
        Yields:
            Dictionary with task completion status and content
        """
        inputs = {'messages': [('user', query)]}
        config = {'configurable': {'thread_id': context_id}}
        
        # Stream through the agent's reasoning process
        for item in self.graph.stream(inputs, config, stream_mode='values'):
            message = item['messages'][-1]
            
            # Agent is calling a tool
            if (
                isinstance(message, AIMessage)
                and message.tool_calls
                and len(message.tool_calls) > 0
            ):
                tool_name = message.tool_calls[0].get('name', 'a tool')
                yield {
                    'is_task_complete': False,
                    'require_user_input': False,
                    'content': f'Using {tool_name} to help answer your question...',
                }
            
            # Tool has returned results
            elif isinstance(message, ToolMessage):
                yield {
                    'is_task_complete': False,
                    'require_user_input': False,
                    'content': 'Processing the information...',
                }
        
        # Get final structured response
        yield self.get_agent_response(config)
    
    def get_agent_response(self, config: dict) -> dict[str, Any]:
        """Extract and format the agent's final response.
        
        Args:
            config: LangGraph configuration with thread_id
            
        Returns:
            Dictionary with task status and agent's message
        """
        current_state = self.graph.get_state(config)
        structured_response = current_state.values.get('structured_response')
        
        if structured_response and isinstance(structured_response, ResponseFormat):
            if structured_response.status == 'input_required':
                return {
                    'is_task_complete': False,
                    'require_user_input': True,
                    'content': structured_response.message,
                }
            
            if structured_response.status == 'error':
                return {
                    'is_task_complete': False,
                    'require_user_input': True,
                    'content': structured_response.message,
                }
            
            if structured_response.status == 'completed':
                return {
                    'is_task_complete': True,
                    'require_user_input': False,
                    'content': structured_response.message,
                }
        
        # Fallback if no structured response
        return {
            'is_task_complete': False,
            'require_user_input': True,
            'content': (
                'I encountered an issue processing your request. '
                'Please try rephrasing your question.'
            ),
        }
