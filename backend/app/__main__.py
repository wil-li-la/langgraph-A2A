"""A2A server entry point for medication delivery agent."""

import logging
import os
import sys

import click
import httpx
import uvicorn
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Route

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
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed, that's fine in production

from app.agent_executor import MedicationAgentExecutor
from app.workflow_api import workflow_routes


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
    
    # Note: API keys are optional for medication delivery demo with mock data
    # Validate only if you plan to use LLM for NLU parsing
    
    return model_source


@click.command()
@click.option('--host', 'host', default='0.0.0.0', help='Server host address')
@click.option('--port', 'port', default=None, type=int, help='Server port number')
def main(host: str, port: int):
    """Start the medication delivery A2A agent server."""
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
        
        # Define medication delivery skill
        medication_delivery_skill = AgentSkill(
            id='medication_delivery',
            name='Medication Delivery (給藥服務)',
            description='Execute end-to-end medication delivery tasks using HelloRobot Stretch',
            tags=['healthcare', 'robotics', 'medication', 'delivery'],
            examples=[
                '請將阿斯匹靈送給張小明',
                'Deliver Aspirin to John Smith',
                '請將普拿疼送給李美華',
                'Deliver Vitamin C to Mary Johnson',
                '請將維他命C送給王大同',
            ],
        )
        
        # Create agent card
        agent_card = AgentCard(
            name='Medication Delivery Robot',
            description='HelloRobot Stretch autonomous medication delivery system powered by LangGraph',
            url=f'http://{host}:{port}/',
            version='1.0.0',
            defaultInputModes=['text', 'text/plain'],
            defaultOutputModes=['text', 'text/plain'],
            capabilities=capabilities,
            skills=[medication_delivery_skill],
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
            agent_executor=MedicationAgentExecutor(),
            task_store=InMemoryTaskStore(),
            push_config_store=push_config_store,
            push_sender=push_sender
        )
        
        # Create A2A server application
        server = A2AStarletteApplication(
            agent_card=agent_card,
            http_handler=request_handler
        )
        
        # Build the base Starlette app and add workflow API routes + CORS
        starlette_app = server.build()
        
        # Mount workflow API routes
        for route in workflow_routes:
            starlette_app.routes.insert(0, route)
        
        # Add CORS middleware for frontend dev server
        starlette_app.add_middleware(
            CORSMiddleware,
            allow_origins=[
                "http://localhost:3000",
                "http://127.0.0.1:3000",
                os.getenv("FRONTEND_URL", ""),
            ],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        
        logger.info(f'Starting Medication Delivery A2A agent server on {host}:{port}')
        logger.info(f'Agent card available at: http://{host}:{port}/agent-card')
        logger.info(f'Workflow API available at: http://{host}:{port}/api/workflow')
        
        # Run the server
        uvicorn.run(starlette_app, host=host, port=port)
    
    except MissingAPIKeyError as e:
        logger.error(f'Configuration Error: {e}')
        sys.exit(1)
    except Exception as e:
        logger.error(f'An error occurred during server startup: {e}')
        sys.exit(1)


if __name__ == '__main__':
    main()
