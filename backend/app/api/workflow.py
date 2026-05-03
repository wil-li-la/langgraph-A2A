"""REST API endpoints for exposing LangGraph workflow to the dashboard."""

import asyncio
import json
import logging
import pkgutil
import threading
import uuid
from typing import AsyncGenerator

from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from app.api.camera import (
    stream_d405_rgb,
    stream_d405_depth,
    stream_d405_mix,
    stream_d435if_rgb,
    stream_d435if_depth,
    stream_d435if_mix
)

from app.common.sse_log_bridge import bridge_logs_to_queue
from app.workflows.medication_delivery import (
    MedicationDeliveryAgent,
    create_medication_delivery_workflow,
    _paused_sessions,
    _stop_requests,
    _VALID_RESUME_NODES,
)
from app.healthcare.mock_data import MockNLU
from app.skills.browser_input import (
    deliver_input,
    register_request_handler,
    unregister_request_handler,
)
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
        "check_identity": "check_identity_node",
        "hand_medicine": "hand_medicine_node",
        "handle_error": "error_handler_node",
    }

    # Internal nodes hidden from the dashboard graph
    _HIDDEN_NODES = {"resume_router"}

    # Extract nodes from the graph
    nodes = []
    raw_nodes = raw.get("nodes", [])
    for node_data in raw_nodes:
        node_id = node_data.get("id", "")
        if node_id in _HIDDEN_NODES:
            continue
        node_type = node_type_map.get(node_id, "process")

        nodes.append({
            "id": node_id,
            "name": node_id,
            "label": node_label_map.get(node_id, node_id),
            "type": node_type,
            "status": "pending",
        })

    # Extract edges, skipping those involving hidden nodes and
    # rewiring __start__ to point to confirm_task instead.
    edges = []
    raw_edges = raw.get("edges", [])
    for edge_data in raw_edges:
        source = edge_data.get("source", "")
        target = edge_data.get("target", "")
        is_conditional = edge_data.get("conditional", False)

        if source in _HIDDEN_NODES or target in _HIDDEN_NODES:
            continue

        edges.append({
            "from": source,
            "to": target,
            "conditional": is_conditional,
        })

    # Ensure __start__ connects to confirm_task (since resume_router is hidden)
    start_has_edge = any(e["from"] == "__start__" for e in edges)
    if not start_has_edge:
        edges.insert(0, {"from": "__start__", "to": "confirm_task", "conditional": False})

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
        session_id = str(uuid.uuid4())
        start_from = body.get("start_from", "")
        if start_from and start_from not in _VALID_RESUME_NODES:
            return JSONResponse(
                {"error": f"Invalid start_from node: {start_from}"},
                status_code=400,
            )

        async def event_generator() -> AsyncGenerator[str, None]:
            """Run stream_execute in a background thread, intercept stdout, and yield SSE lines."""
            loop = asyncio.get_event_loop()
            q = asyncio.Queue()

            # Bridge browser_input_skill → this SSE queue so the skill can
            # emit an await_input event from its worker thread.
            def _on_input_requested(prompt: str):
                try:
                    if not loop.is_closed():
                        loop.call_soon_threadsafe(q.put_nowait, {
                            "type": "await_input",
                            "prompt": prompt,
                        })
                except RuntimeError:
                    pass
            register_request_handler(session_id, _on_input_requested)

            def _run_stream():
                thread_id = threading.get_ident()
                with bridge_logs_to_queue(q, loop, thread_id):
                    try:
                        for event_type, node_id, data in _agent.stream_execute(
                            patient_name,
                            medication_name,
                            mode="manual",
                            session_id=session_id,
                            start_from=start_from,
                        ):
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
                        unregister_request_handler(session_id)
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

                if item["type"] == "log":
                    payload = json.dumps({"event": "log", "text": item["text"], "level": item["level"]}, ensure_ascii=False)
                    yield f"data: {payload}\n\n"

                elif item["type"] == "await_input":
                    payload = json.dumps({
                        "event": "await_input",
                        "session_id": session_id,
                        "prompt": item["prompt"],
                    }, ensure_ascii=False)
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
                        payload = json.dumps({"event": "done", "result": result, "session_id": session_id}, ensure_ascii=False)
                    elif event_type == "paused":
                        payload = json.dumps({
                            "event": "paused",
                            "node_id": node_id,
                            "session_id": session_id,
                            "reason": data.get("reason", ""),
                            "task_status": data.get("task_status", ""),
                            "executed_nodes": data.get("executed_nodes", []),
                        }, ensure_ascii=False)
                    else:
                        filtered_data = {k: v for k, v in data.items() if k != "session_id"}
                        payload = json.dumps({
                            "event": event_type,
                            "node_id": node_id,
                            "session_id": session_id,
                            **filtered_data,
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


async def resume_workflow_stream(request: Request) -> StreamingResponse:
    """POST /api/workflow/resume — Resume a paused workflow via SSE.

    Body: { "session_id": "...", "node_id": "..." }
    """
    try:
        body = await request.json()
        session_id = body.get("session_id", "")
        node_id = body.get("node_id", "")

        if not session_id or not node_id:
            return JSONResponse(
                {"error": "Missing 'session_id' or 'node_id' field"},
                status_code=400,
            )

        if session_id not in _paused_sessions:
            return JSONResponse(
                {"error": f"No paused session found for session_id={session_id}"},
                status_code=404,
            )

        saved_state = _paused_sessions[session_id]
        resume_state = dict(saved_state)
        resume_state["task_status"] = "resuming"
        resume_state["errors"] = []
        resume_state["resume_from"] = node_id

        async def event_generator() -> AsyncGenerator[str, None]:
            """Run stream_execute with resume_state in a background thread and yield SSE lines."""
            loop = asyncio.get_event_loop()
            q = asyncio.Queue()

            # Bridge browser_input_skill → this SSE queue (same as execute_workflow_stream)
            def _on_input_requested(prompt: str):
                try:
                    if not loop.is_closed():
                        loop.call_soon_threadsafe(q.put_nowait, {
                            "type": "await_input",
                            "prompt": prompt,
                        })
                except RuntimeError:
                    pass
            register_request_handler(session_id, _on_input_requested)

            def _run_stream():
                thread_id = threading.get_ident()
                with bridge_logs_to_queue(q, loop, thread_id):
                    try:
                        for event_type, ev_node_id, data in _agent.stream_execute(
                            resume_state["patient_name"],
                            resume_state["medication_name"],
                            mode="manual",
                            resume_state=resume_state,
                            session_id=session_id,
                        ):
                            try:
                                if not loop.is_closed():
                                    loop.call_soon_threadsafe(q.put_nowait, {
                                        "type": "langgraph",
                                        "event_type": event_type,
                                        "node_id": ev_node_id,
                                        "data": data,
                                    })
                            except RuntimeError:
                                break
                    except Exception as e:
                        logger.error(f"Error in resume stream thread: {e}", exc_info=True)
                        try:
                            if not loop.is_closed():
                                loop.call_soon_threadsafe(q.put_nowait, {"type": "error", "error": str(e)})
                        except RuntimeError:
                            pass
                    finally:
                        unregister_request_handler(session_id)
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

                if item["type"] == "log":
                    payload = json.dumps({"event": "log", "text": item["text"], "level": item["level"]}, ensure_ascii=False)
                    yield f"data: {payload}\n\n"

                elif item["type"] == "await_input":
                    payload = json.dumps({
                        "event": "await_input",
                        "session_id": session_id,
                        "prompt": item["prompt"],
                    }, ensure_ascii=False)
                    yield f"data: {payload}\n\n"

                elif item["type"] == "langgraph":
                    event_type = item["event_type"]
                    ev_node_id = item["node_id"]
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
                        payload = json.dumps({"event": "done", "result": result, "session_id": session_id}, ensure_ascii=False)
                    elif event_type == "paused":
                        payload = json.dumps({
                            "event": "paused",
                            "node_id": ev_node_id,
                            "session_id": session_id,
                            "reason": data.get("reason", ""),
                            "task_status": data.get("task_status", ""),
                            "executed_nodes": data.get("executed_nodes", []),
                        }, ensure_ascii=False)
                    else:
                        filtered_data = {k: v for k, v in data.items() if k != "session_id"}
                        payload = json.dumps({
                            "event": event_type,
                            "node_id": ev_node_id,
                            "session_id": session_id,
                            **filtered_data,
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
        logger.error(f"Resume stream execution failed: {e}")
        return JSONResponse(
            {"error": str(e)},
            status_code=500,
        )


async def submit_workflow_input(request: Request) -> JSONResponse:
    """POST /api/workflow/input — Deliver browser-captured text to a waiting skill.

    Body: { "session_id": "...", "text": "..." }
    """
    try:
        body = await request.json()
        session_id = body.get("session_id", "")
        text = body.get("text", "")

        if not session_id:
            return JSONResponse({"error": "Missing 'session_id'"}, status_code=400)

        delivered = deliver_input(session_id, text)
        if not delivered:
            return JSONResponse(
                {"error": f"No pending input request for session_id={session_id}"},
                status_code=404,
            )
        return JSONResponse({"status": "ok"})

    except Exception as e:
        logger.error(f"Submit input failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


async def submit_workflow_stop(request: Request) -> JSONResponse:
    """POST /api/workflow/stop — Request a graceful stop.

    Body: { "session_id": "..." }
    Adds the session_id to _stop_requests; stream_execute will pause after
    the current node finishes.
    """
    try:
        body = await request.json()
        session_id = body.get("session_id", "")
        if not session_id:
            return JSONResponse({"error": "Missing 'session_id'"}, status_code=400)

        _stop_requests.add(session_id)
        return JSONResponse({"status": "ok"})

    except Exception as e:
        logger.error(f"Stop request failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


async def reset_workflow_stream(request: Request) -> StreamingResponse:
    """POST /api/workflow/reset — Reset workflow: clear paused sessions and return robot to origin.

    Returns an SSE stream showing the return_to_origin node executing.
    """
    try:
        # Clear all paused sessions
        _paused_sessions.clear()

        # Build a minimal state that just runs return_to_origin
        reset_state = {
            "patient_name": "",
            "medication_name": "",
            "current_location": "unknown",
            "task_status": "resetting",
            "target_detected": False,
            "identity_verified": False,
            "identity_check_retries": 0,
            "mode": "manual",
            "errors": [],
            "history": [],
            "executed_nodes": [],
            "resume_from": "return_to_origin",
        }

        session_id = str(uuid.uuid4())

        async def event_generator() -> AsyncGenerator[str, None]:
            loop = asyncio.get_event_loop()
            q: asyncio.Queue = asyncio.Queue()

            def _run_stream():
                thread_id = threading.get_ident()
                with bridge_logs_to_queue(q, loop, thread_id):
                    try:
                        for event_type, node_id, data in _agent.stream_execute(
                            "", "", mode="manual", session_id=session_id, resume_state=reset_state,
                        ):
                            try:
                                if not loop.is_closed():
                                    loop.call_soon_threadsafe(q.put_nowait, {
                                        "type": "langgraph",
                                        "event_type": event_type,
                                        "node_id": node_id,
                                        "data": data
                                    })
                            except RuntimeError:
                                break
                    except Exception as e:
                        logger.error(f"Error in reset stream thread: {e}", exc_info=True)
                        try:
                            if not loop.is_closed():
                                loop.call_soon_threadsafe(q.put_nowait, {"type": "error", "error": str(e)})
                        except RuntimeError:
                            pass
                    finally:
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

                if item["type"] == "log":
                    payload = json.dumps({"event": "log", "text": item["text"], "level": item["level"]}, ensure_ascii=False)
                    yield f"data: {payload}\n\n"
                elif item["type"] == "langgraph":
                    event_type = item["event_type"]
                    ev_node_id = item["node_id"]
                    data = item["data"]

                    if event_type == "done":
                        result = {
                            "task_status": data.get("task_status"),
                            "executed_nodes": data.get("executed_nodes", []),
                            "history": data.get("history", []),
                        }
                        payload = json.dumps({"event": "done", "result": result, "session_id": session_id}, ensure_ascii=False)
                    else:
                        payload = json.dumps({
                            "event": event_type,
                            "node_id": ev_node_id,
                            "session_id": session_id,
                            **{k: v for k, v in data.items() if k != "session_id"},
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
        logger.error(f"Reset workflow failed: {e}")
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
    Route("/api/workflow/execute", execute_workflow, methods=["POST", "OPTIONS"]),
    Route("/api/workflow/execute/stream", execute_workflow_stream, methods=["POST", "OPTIONS"]),
    Route("/api/workflow/resume", resume_workflow_stream, methods=["POST", "OPTIONS"]),
    Route("/api/workflow/input", submit_workflow_input, methods=["POST", "OPTIONS"]),
    Route("/api/workflow/stop", submit_workflow_stop, methods=["POST", "OPTIONS"]),
    Route("/api/workflow/reset", reset_workflow_stream, methods=["POST", "OPTIONS"]),
    Route("/api/skills", get_skills, methods=["GET"]),
    Route("/api/stream/d405/rgb", stream_d405_rgb, methods=["GET"]),
    Route("/api/stream/d405/depth", stream_d405_depth, methods=["GET"]),
    Route("/api/stream/d405/mix", stream_d405_mix, methods=["GET"]),
    Route("/api/stream/d435if/rgb", stream_d435if_rgb, methods=["GET"]),
    Route("/api/stream/d435if/depth", stream_d435if_depth, methods=["GET"]),
    Route("/api/stream/d435if/mix", stream_d435if_mix, methods=["GET"]),
]
