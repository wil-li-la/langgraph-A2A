# LangGraph A2A Agent

A conversational AI agent built with [LangGraph](https://github.com/langchain-ai/langgraph) that supports the [Agent-to-Agent (A2A) Protocol](https://github.com/a2aproject/a2a-samples).

## Overview

This project implements AI agents using **LangGraph** for workflow orchestration and **MLflow** for experiment tracking. It includes:

1. **General Conversational Agent**: A ReAct-based agent for Q&A, search, and calculations
2. **Healthcare Medication Delivery Agent**: A specialized LangGraph workflow for robotic medication delivery

### What is LangGraph?

[LangGraph](https://langchain-ai.github.io/langgraph/) is a framework for building stateful, multi-actor applications with LLMs. It extends LangChain with the ability to create **cyclic graphs** for complex agent workflows.

**Key Concepts**:
- **State**: Shared data structure that flows through the graph
- **Nodes**: Functions that process and update the state
- **Edges**: Connections between nodes (conditional or direct)
- **Graphs**: The complete workflow definition

**Why LangGraph?**
- ✅ **Explicit Control Flow**: Define exactly how your agent should behave
- ✅ **State Management**: Maintain context across multiple steps
- ✅ **Conditional Logic**: Make decisions based on intermediate results
- ✅ **Error Handling**: Built-in support for error recovery and human-in-the-loop
- ✅ **Debugging**: Visualize and inspect workflow execution

### Healthcare Agent Example

The medication delivery agent (`app/healthcare/medication_delivery.py`) demonstrates a real-world LangGraph workflow:

```
┌─────────────┐
│  NLU Parser │ ──┐
└─────────────┘   │
                  ▼
            ┌──────────┐
            │ Navigate  │
            │ Pharmacy  │
            └──────────┘
                  │
                  ▼
            ┌──────────┐
            │  Pickup   │──── Error? ───┐
            │   Med     │               │
            └──────────┘               │
                  │                     │
                  ▼                     ▼
            ┌──────────┐         ┌──────────┐
            │ Deliver   │         │  Error   │
            │  Patient  │         │ Handler  │
            └──────────┘         └──────────┘
                  │                     │
                  ▼                     ▼
                [END]                 [END]
```

**Workflow Steps**:
1. **NLU Parsing**: Extract patient name and medication from voice command
2. **Navigation**: Guide robot to pharmacy
3. **Medication Pickup**: Use vision to locate and grasp medication
4. **Patient Delivery**: Navigate to patient room, verify identity, deliver medication
5. **Error Handling**: Handle failures at any step with human intervention

## Features

### Core Capabilities

- **LangGraph Workflows**: Build complex, stateful agent workflows with explicit control flow
- **A2A Protocol Compliance**: Full support for agent-to-agent communication
- **MLflow Experiment Tracking**: Automatic logging of parameters, metrics, and artifacts
- **Streaming Responses**: Real-time task status updates
- **Memory Persistence**: Maintains conversation context using thread IDs
- **Flexible LLM Support**: Works with Google Gemini or OpenAI models
- **Extensible Tools**: Easy to add custom tools and capabilities

### MLflow Integration

**MLflow** provides experiment tracking and model management for your agents. Every task execution automatically logs:

**Parameters**:
- Input instructions
- Patient/medication names
- Agent configuration
- LLM model settings

**Metrics**:
- Task success rate
- Execution time
- Error counts
- Workflow step counts
- Detection/verification accuracy

**Artifacts**:
- Complete execution state (JSON)
- Workflow history
- Error logs

**Benefits**:
- 📊 **Track Performance**: Monitor success rates and execution times over time
- 🔍 **Debug Issues**: Review failed runs with complete state information
- 📈 **Compare Experiments**: A/B test different prompts, models, or configurations
- 💰 **Cost Tracking**: Monitor LLM API usage and costs
- 🎯 **Optimize Workflows**: Identify bottlenecks in your agent workflows

**Access MLflow Dashboard**:
```bash
mlflow ui --port 5001
# Open http://localhost:5001 in your browser
```

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

### Healthcare Medication Delivery Agent

The healthcare agent demonstrates a complete LangGraph workflow for robotic medication delivery.

**Run the agent**:
```bash
python -m app.healthcare.medication_delivery
```

**With custom instruction**:
```bash
python -m app.healthcare.medication_delivery "請將阿斯匹靈送給張小明"
```

**Example Output**:
```
############################################################
# 給藥任務開始
############################################################

🎤 正在解析指令: 請將阿斯匹靈送給張小明
✓ 解析完成: 病患=張小明, 藥物=阿斯匹靈

🚶 導航中: 前往藥局領取 阿斯匹靈
✓ 已到達藥局

👁️ 視覺辨識中: 搜尋 阿斯匹靈
✓ 藥物已定位

🤖 機械臂操作中: 抓取藥物
✓ 藥物已抓取

🚶 導航中: 前往病房
✓ 已到達病房 301

👤 身份驗證中: 人臉辨識
✓ 身份驗證成功: 張小明

🤝 遞交藥物中...
✓ 藥物已遞交給病患

✅ 給藥任務完成！
📊 MLflow Run ID: d35b85069f384a00a5f2f98052f1d48d
```

**Available Test Commands**:
1. `請將阿斯匹靈送給張小明` (Chinese)
2. `Deliver Aspirin to John Smith` (English)
3. `請將普拿疼送給李美華`
4. `請將維他命C送給王大同`

### MLflow Monitoring

All agent executions are automatically tracked in MLflow.

**Start the MLflow UI**:
```bash
mlflow ui --port 5001
```

**Access the dashboard**: http://localhost:5001

**What you can do**:
- 📊 View all experiment runs and their metrics
- 🔍 Compare different agent configurations side-by-side
- 📈 Track success rates and execution times over time
- 💾 Download execution artifacts (state, logs, errors)
- 🎯 Filter runs by parameters, metrics, or tags

**Example Queries**:
```python
import mlflow

# Search for successful medication deliveries
runs = mlflow.search_runs(
    experiment_names=["medication_delivery_agent"],
    filter_string="metrics.task_success = 1"
)

# Get average execution time
avg_time = runs["metrics.execution_time_seconds"].mean()
print(f"Average execution time: {avg_time:.2f}s")

# Find failed runs
failed_runs = mlflow.search_runs(
    experiment_names=["medication_delivery_agent"],
    filter_string="metrics.task_success = 0"
)
```

## Project Structure

```
langgraph-a2a/
├── app/
│   ├── __init__.py              # Package initialization
│   ├── __main__.py              # Server entry point (A2A agent)
│   ├── agent_executor.py        # A2A protocol integration
│   └── healthcare/
│       ├── medication_delivery.py   # LangGraph medication delivery workflow
│       └── mock_data.py             # Mock database and robot actions
├── mlruns/                      # MLflow experiment tracking data
├── pyproject.toml               # Dependencies and project config
├── .env.example                 # Environment variables template
├── Dockerfile                   # Docker container configuration
├── docker-compose.yml           # Docker Compose setup
└── README.md                    # This file
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

### Creating Custom LangGraph Workflows

Build your own stateful workflows like the medication delivery agent:

```python
from typing import TypedDict
from langgraph.graph import StateGraph, END

# 1. Define your state
class MyAgentState(TypedDict):
    input: str
    output: str
    step_count: int

# 2. Create node functions
def process_node(state: MyAgentState) -> dict:
    # Your processing logic
    return {
        "output": f"Processed: {state['input']}",
        "step_count": state.get('step_count', 0) + 1
    }

# 3. Build the graph
workflow = StateGraph(MyAgentState)
workflow.add_node("process", process_node)
workflow.set_entry_point("process")
workflow.add_edge("process", END)

# 4. Compile and run
app = workflow.compile()
result = app.invoke({"input": "Hello", "step_count": 0})
```

**Key Patterns**:
- Use `TypedDict` for type-safe state management
- Return partial state updates from nodes (they merge automatically)
- Use `add_conditional_edges()` for branching logic
- Add error handling nodes for robustness
- Log state transitions with MLflow for debugging

### Integrating MLflow with Custom Workflows

Add tracking to your custom workflows:

```python
import mlflow

class MyAgent:
    def execute(self, input_data: str):
        with mlflow.start_run():
            # Log parameters
            mlflow.log_param("input", input_data)
            mlflow.set_tag("workflow_type", "custom")
            
            # Run workflow
            result = self.app.invoke({"input": input_data})
            
            # Log metrics
            mlflow.log_metrics({
                "success": 1 if result['output'] else 0,
                "steps": result['step_count']
            })
            
            return result
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

### LangGraph
- [LangGraph Documentation](https://langchain-ai.github.io/langgraph/)
- [LangGraph Tutorials](https://langchain-ai.github.io/langgraph/tutorials/)
- [LangGraph GitHub](https://github.com/langchain-ai/langgraph)
- [LangGraph Conceptual Guide](https://langchain-ai.github.io/langgraph/concepts/)

### MLflow
- [MLflow Documentation](https://mlflow.org/docs/latest/index.html)
- [MLflow Tracking Guide](https://mlflow.org/docs/latest/tracking.html)
- [MLflow Python API](https://mlflow.org/docs/latest/python_api/index.html)
- [MLflow LangChain Integration](https://mlflow.org/docs/latest/llms/langchain/index.html)

### A2A Protocol
- [A2A Protocol Samples](https://github.com/a2aproject/a2a-samples)
- [A2A SDK Documentation](https://github.com/a2aproject/a2a-sdk-python)

## License

This project is provided as-is for educational and development purposes.
