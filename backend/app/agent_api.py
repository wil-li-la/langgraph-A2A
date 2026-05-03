"""REST/SSE endpoints for the agentic execution path (/api/agent/*).

Lives alongside app.workflow_api which still serves the legacy scripted
workflow at /api/workflow/*. The two paths share nothing at runtime, so
errors here cannot affect the scripted workflow.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from typing import AsyncGenerator, Optional

from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from app.agents import DeliveryAgent, AGENT_AVAILABLE_REASON
from app.common.sse_log_bridge import bridge_logs_to_queue
from app.llm import get_llm
from app.tools import get_robot_tools, build_world_summary

logger = logging.getLogger(__name__)


# Lazy singleton — the LLM client / model spin-up is non-trivial.
_agent_lock = threading.Lock()
_agent: Optional[DeliveryAgent] = None
_agent_build_error: Optional[str] = None


def _get_or_build_agent(budget: int = 30) -> Optional[DeliveryAgent]:
    """Return the cached agent, attempting to build it if not yet present.

    Returns None when the LLM is not configured. Stores the build error in
    a module-level variable so /api/agent/info can surface it.
    """
    global _agent, _agent_build_error
    if _agent is not None and _agent.budget == budget:
        return _agent
    with _agent_lock:
        if _agent is not None and _agent.budget == budget:
            return _agent
        try:
            _agent = DeliveryAgent(budget=budget)
            _agent_build_error = None
            logger.info("DeliveryAgent ready (budget=%d)", budget)
            return _agent
        except Exception as e:
            _agent_build_error = str(e)
            logger.warning("DeliveryAgent unavailable: %s", e)
            return None


# ---------- /api/agent/info ----------------------------------------------


async def get_agent_info(request: Request) -> JSONResponse:
    """GET /api/agent/info — what the agent can do, whether it's available."""
    llm = get_llm()
    provider = os.getenv("LLM_PROVIDER", "none").lower()
    model = ""
    if provider == "ollama":
        model = os.getenv("OLLAMA_MODEL", "qwen3:4b")
    elif provider == "openai":
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    elif provider == "google":
        model = os.getenv("GOOGLE_MODEL", "gemini-2.5-flash")
    elif provider == "anthropic":
        model = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")

    available = llm is not None
    tool_summaries = [
        {"name": t.name, "description": (t.description or "").splitlines()[0]}
        for t in get_robot_tools()
    ]

    return JSONResponse({
        "available": available,
        "reason": None if available else AGENT_AVAILABLE_REASON,
        "build_error": _agent_build_error,
        "llm_provider": provider,
        "llm_model": model,
        "world_summary": build_world_summary(),
        "tools": tool_summaries,
        "default_budget": 30,
    })


# ---------- /api/agent/execute (one-shot) --------------------------------


async def execute_agent(request: Request) -> JSONResponse:
    """POST /api/agent/execute — Run the agent to completion.

    Body: {"task": str, "budget"?: int (default 30)}
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Body must be JSON."}, status_code=400)

    task = (body.get("task") or "").strip()
    if not task:
        return JSONResponse({"error": "Missing 'task' field."}, status_code=400)
    budget = int(body.get("budget") or 30)
    if budget < 1 or budget > 200:
        return JSONResponse({"error": "'budget' must be between 1 and 200."}, status_code=400)

    agent = _get_or_build_agent(budget=budget)
    if agent is None:
        return JSONResponse(
            {
                "error": "Agent not available.",
                "reason": AGENT_AVAILABLE_REASON,
                "build_error": _agent_build_error,
            },
            status_code=503,
        )

    try:
        result = await asyncio.to_thread(agent.execute, task)
        return JSONResponse(result)
    except Exception as e:
        logger.error("Agent execute failed: %s", e, exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


# ---------- /api/agent/execute/stream (SSE) ------------------------------


async def execute_agent_stream(request: Request) -> StreamingResponse:
    """POST /api/agent/execute/stream — Run the agent and stream events.

    Body: {"task": str, "budget"?: int}
    Events emitted (one per `data:` line):
      {"event":"started","task":...,"budget":...}
      {"event":"agent_message","text":...,"tool_calls":[...]}
      {"event":"tool_call","name":...,"args":{...},"id":...}
      {"event":"tool_result","name":...,"id":...,"result":...}
      {"event":"log","text":...,"level":...}
      {"event":"done","result":{...}}
      {"event":"error","error":...}
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Body must be JSON."}, status_code=400)

    task = (body.get("task") or "").strip()
    if not task:
        return JSONResponse({"error": "Missing 'task' field."}, status_code=400)
    budget = int(body.get("budget") or 30)

    agent = _get_or_build_agent(budget=budget)
    if agent is None:
        return JSONResponse(
            {
                "error": "Agent not available.",
                "reason": AGENT_AVAILABLE_REASON,
                "build_error": _agent_build_error,
            },
            status_code=503,
        )

    async def event_generator() -> AsyncGenerator[str, None]:
        loop = asyncio.get_event_loop()
        q: asyncio.Queue = asyncio.Queue()

        def _run() -> None:
            tid = threading.get_ident()
            with bridge_logs_to_queue(q, loop, tid):
                try:
                    for event_name, payload in agent.stream_execute(task):
                        try:
                            if not loop.is_closed():
                                loop.call_soon_threadsafe(
                                    q.put_nowait,
                                    {"type": "agent", "event": event_name, "payload": payload},
                                )
                        except RuntimeError:
                            break
                except Exception as e:
                    logger.error("Agent stream worker failed: %s", e, exc_info=True)
                    try:
                        if not loop.is_closed():
                            loop.call_soon_threadsafe(
                                q.put_nowait,
                                {"type": "agent", "event": "error", "payload": {"error": str(e)}},
                            )
                    except RuntimeError:
                        pass
                finally:
                    try:
                        if not loop.is_closed():
                            loop.call_soon_threadsafe(q.put_nowait, None)
                    except RuntimeError:
                        pass

        threading.Thread(target=_run, daemon=True).start()

        while True:
            item = await q.get()
            if item is None:
                break
            if item["type"] == "log":
                payload = json.dumps(
                    {"event": "log", "text": item["text"], "level": item["level"]},
                    ensure_ascii=False,
                )
                yield f"data: {payload}\n\n"
            elif item["type"] == "agent":
                event = item["event"]
                payload = json.dumps({"event": event, **item["payload"]}, ensure_ascii=False)
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


# Routes mounted by __main__.py alongside the existing workflow_routes.
agent_routes = [
    Route("/api/agent/info", get_agent_info, methods=["GET"]),
    Route("/api/agent/execute", execute_agent, methods=["POST", "OPTIONS"]),
    Route("/api/agent/execute/stream", execute_agent_stream, methods=["POST", "OPTIONS"]),
]
