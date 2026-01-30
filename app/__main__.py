"""A2A server entry point for the LangGraph conversational agent."""

import logging
import os
import sys

import click
import httpx
import uvicorn

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import (
    BasePushNotificationSender,
    InMemoryPushNotificationConfigStore,
    InMemoryTaskStore,
)
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
)

# Load .env file if it exists (for local development)
# This won't fail if .env doesn't exist (production environments)
try:
    from dotenv import load_dotenv
    load_dotenv()  # Only loads if .env exists, otherwise does nothing
except ImportError:
    pass  # python-dotenv not installed, that's fine in production

from app.agent import ConversationalAgent
from app.agent_executor import ConversationalAgentExecutor


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class MissingAPIKeyError(Exception):
    """Exception raised when required API key is missing."""


def validate_environment():
    """Validate required environment variables and log configuration."""
    model_source = os.getenv('model_source', 'google')
    port = os.getenv('PORT', '9999')
    
    # Log environment configuration for debugging
    logger.info(f"Environment Configuration:")
    logger.info(f"  - PORT: {port}")
    logger.info(f"  - model_source: {model_source}")
    logger.info(f"  - GOOGLE_API_KEY: {'[SET]' if os.getenv('GOOGLE_API_KEY') else '[NOT SET]'}")
    logger.info(f"  - OPENAI_API_KEY: {'[SET]' if os.getenv('OPENAI_API_KEY') else '[NOT SET]'}")
    
    # Validate API key based on model source
    if model_source == 'google':
        if not os.getenv('GOOGLE_API_KEY'):
            raise MissingAPIKeyError(
                'GOOGLE_API_KEY environment variable not set.\n'
                'For Railway/Zeabur: Set it in the dashboard under Variables.\n'
                'For local: Create a .env file with GOOGLE_API_KEY=your_key'
            )
    else:
        if not os.getenv('OPENAI_API_KEY'):
            raise MissingAPIKeyError(
                'OPENAI_API_KEY environment variable not set.\n'
                'For Railway/Zeabur: Set it in the dashboard under Variables.\n'
                'For local: Create a .env file with OPENAI_API_KEY=your_key'
            )
    
    return model_source


@click.command()
@click.option('--host', 'host', default='0.0.0.0', help='Server host address')
@click.option('--port', 'port', default=None, type=int, help='Server port number')
def main(host: str, port: int):
    """Start the LangGraph A2A agent server."""
    try:
        # Use PORT env var if --port not specified (for Railway/Zeabur/Cloud Run)
        if port is None:
            port = int(os.getenv('PORT', '9999'))
        
        # Validate environment and log configuration
        validate_environment()
        
        # Define agent capabilities
        capabilities = AgentCapabilities(
            streaming=True,
            push_notifications=True
        )
        
        # Define agent skills
        general_conversation_skill = AgentSkill(
            id='general_conversation',
            name='General Conversation',
            description='Engage in helpful conversations, answer questions, and assist with various tasks',
            tags=['conversation', 'qa', 'assistant'],
            examples=[
                'What is the capital of France?',
                'Can you help me understand quantum physics?',
                'Tell me about artificial intelligence',
            ],
        )
        
        search_skill = AgentSkill(
            id='web_search',
            name='Web Search',
            description='Search for information on the web',
            tags=['search', 'information', 'research'],
            examples=[
                'Search for the latest news about AI',
                'Find information about climate change',
            ],
        )
        
        calculation_skill = AgentSkill(
            id='calculation',
            name='Mathematical Calculations',
            description='Perform mathematical calculations and solve equations',
            tags=['math', 'calculation', 'arithmetic'],
            examples=[
                'Calculate 25 * 48',
                'What is 100 / 7?',
            ],
        )
        
        # Create agent card
        agent_card = AgentCard(
            name='LangGraph Conversational Agent',
            description='A helpful AI assistant powered by LangGraph with A2A protocol support',
            url=f'http://{host}:{port}/',
            version='1.0.0',
            default_input_modes=ConversationalAgent.SUPPORTED_CONTENT_TYPES,
            default_output_modes=ConversationalAgent.SUPPORTED_CONTENT_TYPES,
            capabilities=capabilities,
            skills=[general_conversation_skill, search_skill, calculation_skill],
        )
        
        # Set up push notifications
        httpx_client = httpx.AsyncClient()
        push_config_store = InMemoryPushNotificationConfigStore()
        push_sender = BasePushNotificationSender(
            httpx_client=httpx_client,
            config_store=push_config_store
        )
        
        # Create request handler
        request_handler = DefaultRequestHandler(
            agent_executor=ConversationalAgentExecutor(),
            task_store=InMemoryTaskStore(),
            push_config_store=push_config_store,
            push_sender=push_sender
        )
        
        # Create A2A server application
        server = A2AStarletteApplication(
            agent_card=agent_card,
            http_handler=request_handler
        )
        
        logger.info(f'Starting LangGraph A2A agent server on {host}:{port}')
        logger.info(f'Agent card available at: http://{host}:{port}/agent-card')
        
        # Run the server
        uvicorn.run(server.build(), host=host, port=port)
    
    except MissingAPIKeyError as e:
        logger.error(f'Configuration Error: {e}')
        sys.exit(1)
    except Exception as e:
        logger.error(f'An error occurred during server startup: {e}')
        sys.exit(1)


if __name__ == '__main__':
    main()
