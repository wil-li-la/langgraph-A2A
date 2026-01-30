# LangGraph A2A Agent

A conversational AI agent built with [LangGraph](https://github.com/langchain-ai/langgraph) that supports the [Agent-to-Agent (A2A) Protocol](https://github.com/a2aproject/a2a-samples).

## Overview

This project implements a general-purpose conversational agent that can:
- Answer questions and engage in helpful conversations
- Search for information (with tool integration)
- Perform mathematical calculations
- Maintain conversation context across multiple turns
- Communicate with other agents via the A2A protocol

## Features

- **LangGraph ReAct Agent**: Uses the ReAct (Reasoning + Acting) pattern for intelligent tool usage
- **A2A Protocol Compliance**: Full support for agent-to-agent communication
- **Streaming Responses**: Real-time task status updates
- **Memory Persistence**: Maintains conversation context using thread IDs
- **Flexible LLM Support**: Works with Google Gemini or OpenAI models
- **Extensible Tools**: Easy to add custom tools and capabilities

## Installation

### Prerequisites

- Python 3.12 or higher
- pip or uv package manager

### Setup

1. **Clone or navigate to the project directory**:
   ```bash
   cd /Users/willin/Gitub-local/pydantic-ai-test
   ```

2. **Install dependencies**:
   ```bash
   pip install -e .
   ```

3. **Configure environment variables**:
   ```bash
   cp .env.example .env
   ```
   
   Edit `.env` and add your API key:
   ```bash
   # For Google Gemini (default)
   model_source=google
   GOOGLE_API_KEY=your_api_key_here
   
   # OR for OpenAI
   # model_source=openai
   # OPENAI_API_KEY=your_api_key_here
   ```

## Docker Deployment

### Quick Start with Docker

1. **Configure environment variables**:
   ```bash
   cp .env.example .env
   # Edit .env and add your API key
   ```

2. **Build and run with Docker Compose**:
   ```bash
   docker-compose up -d
   ```

3. **View logs**:
   ```bash
   docker-compose logs -f
   ```

The agent will be available at `http://localhost:9999`

### Manual Docker Build

```bash
# Build the image
docker build -t langgraph-a2a-agent .

# Run the container
docker run -d \
  --name langgraph-agent \
  -p 9999:9999 \
  -e model_source=google \
  -e GOOGLE_API_KEY=your_api_key_here \
  langgraph-a2a-agent
```

### Docker Features

- ✅ Multi-stage build for optimized image size
- ✅ Non-root user for security
- ✅ Automatic health checks
- ✅ Environment variable configuration
- ✅ Production-ready setup

See the [Docker Deployment Guide](./DOCKER.md) for advanced configuration and production deployment.

## Usage

### Starting the Server

Run the agent server:

```bash
python -m app --host localhost --port 9999
```

The server will start and display:
```
INFO:     Starting LangGraph A2A agent server on localhost:9999
INFO:     Agent card available at: http://localhost:9999/agent-card
```

### Testing the Agent

1. **View the agent card** (metadata):
   ```bash
   curl http://localhost:9999/agent-card
   ```

2. **Send a message** (using A2A protocol):
   ```bash
   curl -X POST http://localhost:9999/tasks \
     -H "Content-Type: application/json" \
     -d '{
       "message": {
         "parts": [{"text": "Hello! What can you help me with?"}]
       }
     }'
   ```

3. **Use with A2A-compatible clients**: Connect this agent to other A2A agents or use A2A testing tools for full protocol testing.

### Calling This Agent from Your Application

If you want to integrate this agent into your own application or have another agent call this one, use the following function:

```python
import requests

def call_remote_agent(message: str) -> str:
    """Calls the LangGraph A2A agent to process a message/query."""
    url = "http://localhost:9999/tasks"
    payload = {
        "message": {
            "parts": [
                {
                    "kind": "text",
                    "text": message
                }
            ]
        }
    }
    
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        data = response.json()
        
        # Parse A2A response
        if "artifacts" in data:
            for artifact in data["artifacts"]:
                for part in artifact.get("parts", []):
                    if part.get("kind") == "text":
                        return part.get("text", "")
            return "Remote agent finished but returned no text."
        else:
            return f"Remote agent error: {data.get('error')}"
             
    except Exception as e:
        return f"Failed to communicate with remote agent: {str(e)}"
```

**Usage example:**
```python
# Simple query
result = call_remote_agent("What is 25 * 48?")
print(result)  # Output: "The result of 25 * 48 is 1200."
```

**For conversation context** (optional), add a `thread_id` to maintain context across multiple messages:
```python
import uuid

thread_id = str(uuid.uuid4())
payload = {
    "message": {"parts": [{"kind": "text", "text": message}]},
    "thread_id": thread_id  # Add this for conversation continuity
}
```

See [`simple_client.py`](./simple_client.py) for a minimal example or [`client_example.py`](./client_example.py) for a full-featured version with error handling and conversation support.

## Project Structure

```
pydantic-ai-test/
├── app/
│   ├── __init__.py          # Package initialization
│   ├── __main__.py          # Server entry point
│   ├── agent.py             # LangGraph agent implementation
│   └── agent_executor.py    # A2A protocol integration
├── pyproject.toml           # Dependencies and project config
├── .env.example             # Environment variables template
└── README.md                # This file
```

## Customization

### Adding New Tools

Edit `app/agent.py` and add your tool:

```python
from langchain_core.tools import tool

@tool
def my_custom_tool(param: str) -> str:
    """Description of what this tool does."""
    # Your implementation
    return result

# Add to the agent's tools list
self.tools = [search_web, calculate, my_custom_tool]
```

### Adding New Skills

Edit `app/__main__.py` to define new skills in the agent card:

```python
custom_skill = AgentSkill(
    id='my_skill',
    name='My Custom Skill',
    description='What this skill does',
    tags=['custom', 'skill'],
    examples=['Example query 1', 'Example query 2'],
)

# Add to agent card
skills=[general_conversation_skill, custom_skill]
```

### Changing the System Prompt

Edit the `SYSTEM_INSTRUCTION` in `app/agent.py`:

```python
SYSTEM_INSTRUCTION = (
    'Your custom system prompt here...'
)
```

## A2A Protocol

This agent implements the A2A protocol, which enables:
- **Standardized Communication**: Agents can discover and communicate with each other
- **Task Management**: Track task lifecycle (working, completed, input_required)
- **Streaming**: Real-time status updates during task execution
- **Push Notifications**: Notify clients of task updates

### Key A2A Endpoints

- `GET /agent-card` - Agent metadata and capabilities
- `POST /tasks` - Create and execute a new task
- `GET /tasks/{task_id}` - Get task status
- `POST /tasks/{task_id}/cancel` - Cancel a running task

## Development

### Running in Development Mode

```bash
# With auto-reload
uvicorn app.__main__:server --reload --host localhost --port 9999
```

### Testing

Test the agent's core functionality:

```bash
python -c "from app.agent import ConversationalAgent; agent = ConversationalAgent(); print('✓ Agent initialized successfully')"
```

## Troubleshooting

### Missing API Key Error

```
Configuration Error: GOOGLE_API_KEY environment variable not set
```

**Solution**: Make sure you've created a `.env` file with your API key.

### Import Errors

```
ModuleNotFoundError: No module named 'a2a'
```

**Solution**: Install dependencies with `pip install -e .`

### Port Already in Use

```
ERROR:    [Errno 48] Address already in use
```

**Solution**: Use a different port with `--port 10000` or stop the process using port 9999.

## Resources

- [LangGraph Documentation](https://langchain-ai.github.io/langgraph/)
- [A2A Protocol Samples](https://github.com/a2aproject/a2a-samples)
- [A2A SDK Documentation](https://github.com/a2aproject/a2a-sdk-python)

## License

This project is provided as-is for educational and development purposes.
