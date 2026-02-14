"""REST API endpoints for exposing LangGraph workflow to the dashboard."""

import json
import logging

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.healthcare.medication_delivery import (
    MedicationDeliveryAgent,
    create_medication_delivery_workflow,
)

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

        # Decision nodes are represented by conditional edges in LangGraph
        # We'll mark them separately below

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
        # Check if this is a conditional edge
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

        result = _agent.execute(instruction)

        return JSONResponse({
            "task_status": result.get("task_status"),
            "patient_name": result.get("patient_name"),
            "medication_name": result.get("medication_name"),
            "current_location": result.get("current_location"),
            "target_detected": result.get("target_detected"),
            "identity_verified": result.get("identity_verified"),
            "errors": result.get("errors", []),
            "history": result.get("history", []),
        })

    except Exception as e:
        logger.error(f"Workflow execution failed: {e}")
        return JSONResponse(
            {"error": str(e)},
            status_code=500,
        )


# Starlette route list to be mounted on the main app
workflow_routes = [
    Route("/api/workflow", get_workflow, methods=["GET"]),
    Route("/api/workflow/execute", execute_workflow, methods=["POST"]),
]
