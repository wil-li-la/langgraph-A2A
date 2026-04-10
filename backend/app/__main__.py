"""A2A server entry point for medication delivery agent."""

import logging
import os
import sys

import click
import httpx
import uvicorn
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Route, WebSocketRoute

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
from app.teleop_api import teleop_websocket
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

        # Public URL advertised in the AgentCard. PUBLIC_URL wins (for cloud
        # deployments behind a proxy); otherwise build it from host/port,
        # rewriting wildcard binds to localhost so the card stays callable.
        public_url = os.getenv('PUBLIC_URL', '').rstrip('/')
        if not public_url:
            advertised_host = 'localhost' if host in ('0.0.0.0', '::', '') else host
            public_url = f'http://{advertised_host}:{port}'

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
            description=(
                'Autonomous medication delivery workflow on a HelloRobot Stretch: '
                'confirm task → navigate to pharmacy → pick up medication → '
                'navigate to patient → voice-verify identity → hand over medication '
                '→ return to origin. '
                'Send an A2A message containing a DataPart with '
                '{"patient": str, "medicine": str}; the agent replies with a text '
                'summary plus a structured DataPart artifact carrying task_status, '
                'history, executed_nodes and errors.'
            ),
            tags=['healthcare', 'robotics', 'medication', 'delivery'],
            inputModes=['application/json'],
            outputModes=['application/json', 'text/plain'],
            examples=[
                '{"patient": "王大同", "medicine": "維他命C"}',
                '{"patient": "張小明", "medicine": "阿斯匹靈"}',
            ],
        )

        # Create agent card
        agent_card = AgentCard(
            name='Medication Delivery Robot',
            description=(
                'HelloRobot Stretch autonomous medication delivery agent, '
                'orchestrated as a LangGraph StateGraph and exposed over the '
                'Google A2A protocol. Accepts structured DataPart input '
                '{"patient": str, "medicine": str} via the standard '
                'message/send JSON-RPC method.'
            ),
            url=f'{public_url}/',
            version='2.0.0',
            defaultInputModes=['application/json'],
            defaultOutputModes=['application/json', 'text/plain'],
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

        # Mount teleop WebSocket relay
        starlette_app.routes.insert(0, WebSocketRoute("/ws/teleop", teleop_websocket))

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
        logger.info(f'Agent card: {public_url}/.well-known/agent-card.json')
        logger.info(f'A2A JSON-RPC endpoint: POST {public_url}/')
        logger.info(f'Dashboard workflow API: {public_url}/api/workflow')
        logger.info(f'Teleop WebSocket relay: {public_url}/ws/teleop')
        
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
