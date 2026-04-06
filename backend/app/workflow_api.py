"""REST API endpoints for exposing LangGraph workflow to the dashboard."""

import asyncio
import json
import logging
import pkgutil
import sys
import threading
from typing import AsyncGenerator

from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from app.camera_api import (
    stream_d405_rgb,
    stream_d405_depth,
    stream_d405_mix,
    stream_d435if_rgb,
    stream_d435if_depth,
    stream_d435if_mix
)

from app.healthcare.medication_delivery import (
    MedicationDeliveryAgent,
    create_medication_delivery_workflow,
)
from app.healthcare.mock_data import MockNLU
import cure.skills

logger = logging.getLogger(__name__)

# Singleton agent instance for executions
_agent = MedicationDeliveryAgent()


def _introspect_graph() -> dict:
    """Introspect the compiled LangGraph StateGraph to extract nodes and edges.

    Returns the graph structure in a format matching the frontend's
    WorkflowNode[] / WorkflowEdge[] types.
    """
    graph = create_medication_delivery_workflow()
    graph_dict = graph.get_graph().to_json()

    # Parse the Mermaid-style graph data from LangGraph
    raw = json.loads(json.dumps(graph_dict))

    # Map LangGraph node metadata to frontend node types
    node_type_map = {
        "__start__": "start",
        "__end__": "end",
        "handle_error": "error",
    }

    # Map node names to descriptive labels
    node_label_map = {
        "nlu_parser": "nlu_parser_node",
        "nav_to_pharmacy": "navigate_to_pharmacy_node",
        "pickup_med": "pickup_medication_node",
        "delivery": "deliver_to_patient_node",
        "check_patient_identity": "check_patient_identity_node",
        "handle_error": "error_handler_node",
    }

    # Extract nodes from the graph
    nodes = []
    raw_nodes = raw.get("nodes", [])
    for node_data in raw_nodes:
        node_id = node_data.get("id", "")
        node_type = node_type_map.get(node_id, "process")

        nodes.append({
            "id": node_id,
            "name": node_id,
            "label": node_label_map.get(node_id, node_id),
            "type": node_type,
            "status": "pending",
        })

    # Extract edges
    edges = []
    raw_edges = raw.get("edges", [])
    for edge_data in raw_edges:
        source = edge_data.get("source", "")
        target = edge_data.get("target", "")
        is_conditional = edge_data.get("conditional", False)

        edges.append({
            "from": source,
            "to": target,
            "conditional": is_conditional,
        })

    return {"nodes": nodes, "edges": edges}


async def get_workflow(request: Request) -> JSONResponse:
    """GET /api/workflow — Return the LangGraph graph structure."""
    try:
        graph_data = _introspect_graph()
        return JSONResponse(graph_data)
    except Exception as e:
        logger.error(f"Failed to introspect workflow graph: {e}")
        return JSONResponse(
            {"error": str(e)},
            status_code=500,
        )


async def execute_workflow(request: Request) -> JSONResponse:
    """POST /api/workflow/execute — Execute a medication delivery workflow.

    Body: { "instruction": "請將阿斯匹靈送給張小明" }
    """
    try:
        body = await request.json()
        instruction = body.get("instruction", "")

        if not instruction:
            return JSONResponse(
                {"error": "Missing 'instruction' field"},
                status_code=400,
            )

        parsed = MockNLU.parse_instruction(instruction)
        if not parsed["success"]:
            return JSONResponse(
                {"error": parsed["message"]},
                status_code=400,
            )

        result = _agent.execute(parsed["patient_name"], parsed["medication_name"], mode="manual")

        return JSONResponse({
            "task_status": result.get("task_status"),
            "patient_name": result.get("patient_name"),
            "medication_name": result.get("medication_name"),
            "current_location": result.get("current_location"),
            "target_detected": result.get("target_detected"),
            "identity_verified": result.get("identity_verified"),
            "errors": result.get("errors", []),
            "history": result.get("history", []),
            "executed_nodes": result.get("executed_nodes", []),
        })

    except Exception as e:
        logger.error(f"Workflow execution failed: {e}")
        return JSONResponse(
            {"error": str(e)},
            status_code=500,
        )


async def execute_workflow_stream(request: Request) -> StreamingResponse:
    """POST /api/workflow/execute/stream — SSE endpoint for real-time node events.

    Body: { "instruction": "請將阿斯匹靈送給張小明" }

    Emits SSE events:
      data: {"event":"node_start","node_id":"nav_to_pharmacy","executed_nodes":[...]}
      data: {"event":"node_end","node_id":"nav_to_pharmacy","executed_nodes":[...],"history":[...]}
      data: {"event":"done","result":{...final state...}}
    """
    try:
        body = await request.json()
        instruction = body.get("instruction", "")

        if not instruction:
            return JSONResponse(
                {"error": "Missing 'instruction' field"},
                status_code=400,
            )

        parsed = MockNLU.parse_instruction(instruction)
        if not parsed["success"]:
            return JSONResponse(
                {"error": parsed["message"]},
                status_code=400,
            )

        patient_name = parsed["patient_name"]
        medication_name = parsed["medication_name"]

        async def event_generator() -> AsyncGenerator[str, None]:
            """Run stream_execute in a background thread, intercept stdout, and yield SSE lines."""
            loop = asyncio.get_event_loop()
            q = asyncio.Queue()

            def _run_stream():
                thread_id = threading.get_ident()

                class QueueLogHandler(logging.Handler):
                    def emit(self, record):
                        if threading.get_ident() == thread_id:
                            # A simple text format for the UI log
                            text = f"{record.getMessage()}"
                            if text.strip():
                                try:
                                    if not loop.is_closed():
                                        loop.call_soon_threadsafe(q.put_nowait, {"type": "stdout", "text": text.rstrip("\n")})
                                except RuntimeError:
                                    pass

                queue_handler = QueueLogHandler()
                queue_handler.setLevel(logging.INFO)
                
                # Add to root to catch propagated logs, and explicitly to few known loggers just in case
                logging.getLogger().addHandler(queue_handler)
                logging.getLogger("app.healthcare.medication_delivery").addHandler(queue_handler)
                logging.getLogger("cure").addHandler(queue_handler)

                try:
                    for event_type, node_id, data in _agent.stream_execute(patient_name, medication_name, mode="manual"):
                        try:
                            if not loop.is_closed():
                                loop.call_soon_threadsafe(q.put_nowait, {
                                    "type": "langgraph", 
                                    "event_type": event_type, 
                                    "node_id": node_id, 
                                    "data": data
                                })
                        except RuntimeError:
                            break  # Loop closed, no point continuing to generate events for UI
                except Exception as e:
                    logger.error(f"Error in stream thread: {e}", exc_info=True)
                    try:
                        if not loop.is_closed():
                            loop.call_soon_threadsafe(q.put_nowait, {"type": "error", "error": str(e)})
                    except RuntimeError:
                        pass
                finally:
                    logging.getLogger().removeHandler(queue_handler)
                    logging.getLogger("app.healthcare.medication_delivery").removeHandler(queue_handler)
                    logging.getLogger("cure").removeHandler(queue_handler)
                    try:
                        if not loop.is_closed():
                            loop.call_soon_threadsafe(q.put_nowait, None)
                    except RuntimeError:
                        pass

            thread = threading.Thread(target=_run_stream)
            thread.start()

            while True:
                item = await q.get()
                if item is None:
                    break
                
                if item["type"] == "stdout":
                    payload = json.dumps({"event": "log", "text": item["text"]}, ensure_ascii=False)
                    yield f"data: {payload}\n\n"
                    
                elif item["type"] == "langgraph":
                    event_type = item["event_type"]
                    node_id = item["node_id"]
                    data = item["data"]
                    
                    if event_type == "done":
                        result = {
                            "task_status": data.get("task_status"),
                            "patient_name": data.get("patient_name"),
                            "medication_name": data.get("medication_name"),
                            "current_location": data.get("current_location"),
                            "target_detected": data.get("target_detected"),
                            "identity_verified": data.get("identity_verified"),
                            "errors": data.get("errors", []),
                            "history": data.get("history", []),
                            "executed_nodes": data.get("executed_nodes", []),
                        }
                        payload = json.dumps({"event": "done", "result": result}, ensure_ascii=False)
                    else:
                        payload = json.dumps({
                            "event": event_type,
                            "node_id": node_id,
                            **data,
                        }, ensure_ascii=False)
                    yield f"data: {payload}\n\n"
                    
                elif item["type"] == "error":
                    payload = json.dumps({"event": "error", "error": item["error"]}, ensure_ascii=False)
                    yield f"data: {payload}\n\n"

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    except Exception as e:
        logger.error(f"Stream execution failed: {e}")
        return JSONResponse(
            {"error": str(e)},
            status_code=500,
        )


async def get_skills(request: Request) -> JSONResponse:
    """Return available skills dynamically loaded from cure package and required task skills."""
    try:
        available = [name for _, name, _ in pkgutil.iter_modules(cure.skills.__path__)]
        required = ["grasp", "listen", "speak", "handover", "navigate"]
        
        return JSONResponse({
            "available": available,
            "required": required
        })
    except Exception as e:
        logger.error(f"Failed to fetch skills: {e}")
        return JSONResponse(
            {"error": str(e)},
            status_code=500,
        )


# Starlette route list to be mounted on the main app
workflow_routes = [
    Route("/api/workflow", get_workflow, methods=["GET"]),
    Route("/api/workflow/execute", execute_workflow, methods=["POST"]),
    Route("/api/workflow/execute/stream", execute_workflow_stream, methods=["POST"]),
    Route("/api/skills", get_skills, methods=["GET"]),
    Route("/api/stream/d405/rgb", stream_d405_rgb, methods=["GET"]),
    Route("/api/stream/d405/depth", stream_d405_depth, methods=["GET"]),
    Route("/api/stream/d405/mix", stream_d405_mix, methods=["GET"]),
    Route("/api/stream/d435if/rgb", stream_d435if_rgb, methods=["GET"]),
    Route("/api/stream/d435if/depth", stream_d435if_depth, methods=["GET"]),
    Route("/api/stream/d435if/mix", stream_d435if_mix, methods=["GET"]),
]
