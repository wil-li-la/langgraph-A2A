"""REST + SSE adapter for the nvblox navigation stack.

Forwards requests from the dashboard to the lab `nav_service` (ZMQ REQ/REP at
$NVBLOX_NAV_HOST:$NVBLOX_NAV_PORT, default localhost:5560). Holds the latest
known robot pose in process memory so the UI has something to render even
before the lab service / room-camera localizer come online.

Endpoints:
  GET  /api/nav/pose              — latest robot pose in map frame, or null
  POST /api/nav/pose              — user override (drag-to-adjust on the map)
  POST /api/nav/goto              — submit a nav goal (non-blocking)
  GET  /api/nav/status            — current task state snapshot
  GET  /api/nav/status/stream     — SSE stream of state changes
  GET  /api/nav/map               — map metadata (resolution, origin, dims)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

logger = logging.getLogger(__name__)

# ----- Configuration ------------------------------------------------------

NAV_HOST = os.getenv("NVBLOX_NAV_HOST", "localhost")
NAV_PORT = int(os.getenv("NVBLOX_NAV_PORT", "5560"))
NAV_INITIAL_POSE_PORT = int(os.getenv("NVBLOX_NAV_INITIAL_POSE_PORT", "5561"))
DEFAULT_TIMEOUT_S = float(os.getenv("NVBLOX_NAV_TIMEOUT_S", "60"))

# Mirrors backend/maps/305/map.yaml — single source of truth for the dashboard
# coord conversion. Update both files together if the map regenerates.
MAP_METADATA = {
    "image": "/maps/305_map.png",
    "resolution": 0.006,
    "origin": [-6.048, -4.6439, 0.0],
    "width_px": 2059,
    "height_px": 1259,
    "frame_id": "map",
}

# ----- State --------------------------------------------------------------

@dataclass
class Pose:
    x: float
    y: float
    theta: float          # radians, map frame
    source: str = "user"  # "user" | "localizer" | "nav_result"
    ts_ms: int = field(default_factory=lambda: int(time.time() * 1000))


@dataclass
class NavTask:
    request_id: str
    target: tuple[float, float, float]
    state: str              # "idle" | "pending" | "running" | "done"
    status: str | None      # NavStatus enum string when done
    reason: str             # human-readable detail, "" if none
    started_ms: int
    finished_ms: int | None
    final_pose: tuple[float, float, float] | None


_pose: Pose | None = None
_task: NavTask = NavTask(
    request_id="", target=(0.0, 0.0, 0.0), state="idle",
    status=None, reason="", started_ms=0, finished_ms=None,
    final_pose=None,
)
_state_event = asyncio.Event()


def _bump():
    """Notify SSE subscribers that state changed."""
    _state_event.set()
    _state_event.clear()


# ----- Lab nav_service client (ZMQ REQ in a worker thread) ----------------

def _zmq_request_blocking(payload: dict, timeout_s: float, port: int = NAV_PORT) -> dict:
    """Synchronous ZMQ REQ to the lab nav_service. Runs in a worker thread."""
    import msgpack
    import zmq
    addr = f"tcp://{NAV_HOST}:{port}"
    ctx = zmq.Context.instance()
    sock = ctx.socket(zmq.REQ)
    sock.setsockopt(zmq.RCVTIMEO, int((timeout_s + 5) * 1000))
    sock.setsockopt(zmq.LINGER, 0)
    sock.connect(addr)
    try:
        sock.send(msgpack.packb(payload, use_bin_type=True))
        reply_bytes = sock.recv()
    finally:
        sock.close()
    return msgpack.unpackb(reply_bytes, raw=False)


async def _forward_initial_pose(target: tuple[float, float, float]) -> None:
    """Best-effort forward of user pose to lab nav_service so map→odom updates.

    Failures are logged but do not affect the dashboard's stored pose — the
    lab might be down and we still want the UI marker to move.
    """
    payload = {"target": list(target)}
    try:
        reply = await asyncio.to_thread(
            _zmq_request_blocking, payload, 2.0, NAV_INITIAL_POSE_PORT
        )
        if not reply.get("ok"):
            logger.warning("lab set_initial_pose rejected: %s", reply.get("reason"))
        else:
            logger.info("lab set_initial_pose OK: map_to_odom=%s",
                        reply.get("map_to_odom"))
    except Exception as e:
        logger.warning("lab set_initial_pose unreachable at %s:%s: %s",
                       NAV_HOST, NAV_INITIAL_POSE_PORT, e)


async def _run_nav_task(task: NavTask, timeout_s: float):
    """Background coroutine: drive the lab service, update _task, finalize."""
    global _task, _pose
    payload = {
        "target": list(task.target),
        "timeout_s": timeout_s,
        "request_id": task.request_id,
    }
    task.state = "running"
    _bump()
    try:
        reply = await asyncio.to_thread(_zmq_request_blocking, payload, timeout_s)
    except Exception as e:
        task.state = "done"
        task.status = "ROBOT_ERROR"
        task.reason = f"lab nav_service unreachable at {NAV_HOST}:{NAV_PORT}: {e}"
        task.finished_ms = int(time.time() * 1000)
        logger.warning("nav goto failed: %s", task.reason)
        _bump()
        return

    task.state = "done"
    task.status = reply.get("status", "UNKNOWN")
    task.reason = reply.get("reason", "")
    final = reply.get("final_pose")
    task.final_pose = tuple(final) if final else None
    task.finished_ms = int(time.time() * 1000)
    if task.final_pose:
        _pose = Pose(*task.final_pose, source="nav_result")
    _bump()


# ----- Endpoint handlers --------------------------------------------------

async def get_pose(_request: Request):
    return JSONResponse(asdict(_pose) if _pose else None)


async def post_pose(request: Request):
    """User override — drag-to-adjust on the dashboard.

    Stores locally AND forwards to the lab nav_service (best-effort) so its
    static map→odom transform reflects the user's chosen initial pose. Once
    the room-camera localizer is publishing, this manual override is no
    longer needed and the forward call simply gets ignored.
    """
    global _pose
    body = await request.json()
    try:
        target = (float(body["x"]), float(body["y"]), float(body["theta"]))
    except (KeyError, TypeError, ValueError) as e:
        return JSONResponse({"error": f"bad pose: {e}"}, status_code=400)

    _pose = Pose(*target, source="user")
    _bump()
    asyncio.create_task(_forward_initial_pose(target))
    return JSONResponse(asdict(_pose))


async def post_goto(request: Request):
    """Submit a nav goal. Returns the request_id immediately; status via SSE."""
    global _task
    body = await request.json()
    try:
        target = (float(body["x"]), float(body["y"]), float(body["theta"]))
        timeout_s = float(body.get("timeout_s", DEFAULT_TIMEOUT_S))
    except (KeyError, TypeError, ValueError) as e:
        return JSONResponse({"error": f"bad goal: {e}"}, status_code=400)

    if _task.state in ("pending", "running"):
        return JSONResponse(
            {"error": f"nav already {_task.state}", "request_id": _task.request_id},
            status_code=409,
        )

    rid = str(uuid.uuid4())
    _task = NavTask(
        request_id=rid, target=target, state="pending",
        status=None, reason="", started_ms=int(time.time() * 1000),
        finished_ms=None, final_pose=None,
    )
    _bump()
    asyncio.create_task(_run_nav_task(_task, timeout_s))
    return JSONResponse({"request_id": rid, "state": _task.state})


async def get_status(_request: Request):
    return JSONResponse(asdict(_task))


async def status_stream(request: Request):
    """SSE stream of (pose, task) snapshots, one event per state change."""
    async def gen():
        # Initial snapshot
        yield _sse_event({"pose": asdict(_pose) if _pose else None,
                          "task": asdict(_task)})
        while True:
            if await request.is_disconnected():
                return
            try:
                await asyncio.wait_for(_state_event.wait(), timeout=15.0)
            except asyncio.TimeoutError:
                # heartbeat — keeps proxies from closing the stream
                yield ": ping\n\n"
                continue
            yield _sse_event({"pose": asdict(_pose) if _pose else None,
                              "task": asdict(_task)})
    return StreamingResponse(gen(), media_type="text/event-stream")


async def get_map(_request: Request):
    return JSONResponse(MAP_METADATA)


def _sse_event(data: dict[str, Any]) -> str:
    return f"data: {json.dumps(data)}\n\n"


# ----- Route table --------------------------------------------------------

nav_routes = [
    Route("/api/nav/pose", get_pose, methods=["GET"]),
    Route("/api/nav/pose", post_pose, methods=["POST"]),
    Route("/api/nav/goto", post_goto, methods=["POST"]),
    Route("/api/nav/status", get_status, methods=["GET"]),
    Route("/api/nav/status/stream", status_stream, methods=["GET"]),
    Route("/api/nav/map", get_map, methods=["GET"]),
]
