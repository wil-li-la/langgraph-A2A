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
from pathlib import Path
from typing import Any

import cv2
import yaml

from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

logger = logging.getLogger(__name__)

# ----- Configuration ------------------------------------------------------

NAV_HOST = os.getenv("NVBLOX_NAV_HOST", "localhost")
NAV_PORT = int(os.getenv("NVBLOX_NAV_PORT", "5560"))
NAV_INITIAL_POSE_PORT = int(os.getenv("NVBLOX_NAV_INITIAL_POSE_PORT", "5561"))
NAV_STATUS_PORT = int(os.getenv("NVBLOX_NAV_STATUS_PORT", "5562"))
DEFAULT_TIMEOUT_S = float(os.getenv("NVBLOX_NAV_TIMEOUT_S", "60"))
LOCALIZATION_POLL_INTERVAL_S = 1.0

# Persist the manually-set pose across backend restarts so the user
# doesn't have to re-drag every session. When the room-camera localizer
# eventually comes online and publishes live poses, this cache becomes
# a stale-but-harmless fallback (overwritten on next user drag or
# overridden by the localizer's live source).
POSE_CACHE_PATH = Path(
    os.getenv("NVBLOX_NAV_POSE_CACHE",
              str(Path.home() / ".cache" / "langgraph-A2A" / "nav-pose.json"))
)

def _load_map_metadata() -> dict[str, Any]:
    """Parse backend/maps/305/map.yaml + the referenced PGM to build the
    metadata blob the frontend's /nav page renders against. Fatal on
    error — we'd rather refuse to boot than serve a stale frame."""
    map_dir = Path(__file__).resolve().parents[2] / "maps" / "305"
    yaml_path = map_dir / "map.yaml"
    with yaml_path.open() as f:
        data = yaml.safe_load(f)
    pgm_path = map_dir / data["image"]
    pgm = cv2.imread(str(pgm_path), cv2.IMREAD_GRAYSCALE)
    if pgm is None:
        raise RuntimeError(f"cannot read map PGM at {pgm_path}")
    height_px, width_px = pgm.shape
    return {
        "image": "/maps/305_map.png",   # frontend asset, derived from pgm_path
        "resolution": float(data["resolution"]),
        "origin": [float(x) for x in data["origin"]],
        "width_px": int(width_px),
        "height_px": int(height_px),
        "frame_id": "map",
    }


MAP_METADATA = _load_map_metadata()
logger.info(
    "MAP_METADATA loaded: origin=%s resolution=%.4f size=%dx%d",
    MAP_METADATA["origin"],
    MAP_METADATA["resolution"],
    MAP_METADATA["width_px"],
    MAP_METADATA["height_px"],
)

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


def _load_cached_pose() -> Pose | None:
    """Read the manually-set pose from disk. Best-effort; failures are silent."""
    try:
        with POSE_CACHE_PATH.open() as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("nav-pose cache unreadable: %s", e)
        return None
    try:
        return Pose(
            x=float(data["x"]),
            y=float(data["y"]),
            theta=float(data["theta"]),
            source=str(data.get("source", "user")),
            ts_ms=int(data.get("ts_ms", time.time() * 1000)),
        )
    except (KeyError, TypeError, ValueError) as e:
        logger.warning("nav-pose cache malformed: %s", e)
        return None


def _save_cached_pose(pose: Pose) -> None:
    """Write the current pose to disk. Best-effort; never raises."""
    try:
        POSE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = POSE_CACHE_PATH.with_suffix(".tmp")
        with tmp.open("w") as f:
            json.dump(asdict(pose), f)
        os.replace(tmp, POSE_CACHE_PATH)
    except OSError as e:
        logger.warning("nav-pose cache write failed: %s", e)


_pose: Pose | None = _load_cached_pose()
if _pose is not None:
    logger.info("restored cached pose: (%.3f, %.3f, %.3f) source=%s age=%dms",
                _pose.x, _pose.y, _pose.theta, _pose.source,
                int(time.time() * 1000) - _pose.ts_ms)
_task: NavTask = NavTask(
    request_id="", target=(0.0, 0.0, 0.0), state="idle",
    status=None, reason="", started_ms=0, finished_ms=None,
    final_pose=None,
)
# Teleop is "active" while a browser holds an open /ws/teleop connection.
# The nav goto endpoint rejects goals while teleop is active, and the SSE
# stream surfaces this so the dashboard's /viz / /nav / /teleop pages can
# arbitrate UI state. See backend/app/api/teleop.py for the setter calls.
_teleop_active: bool = False
# Latest localization snapshot from nav_service status REP. Shape:
# {state: "ok|uncertain|dead-reckon|unseeded", cov_xy_m, cov_yaw_rad,
#  scan_age_s}. None until the first successful poll (or nav_service down).
_localization: dict | None = None
_state_event = asyncio.Event()


def set_teleop_active(active: bool) -> None:
    """Called by /ws/teleop on connect / disconnect."""
    global _teleop_active
    if _teleop_active == active:
        return
    _teleop_active = active
    logger.info("teleop_active → %s", active)
    _bump()


def is_teleop_active() -> bool:
    return _teleop_active


def is_nav_in_flight() -> bool:
    return _task.state in ("pending", "running")


def get_current_pose() -> Pose | None:
    """Return the latest known robot pose, or None if no pose has been set yet."""
    return _pose


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
    await _maybe_restore_cached_pose()
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
    _save_cached_pose(_pose)
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
    if _teleop_active:
        return JSONResponse(
            {"error": "teleop is driving the robot — nav locked",
             "control_owner": "teleop"},
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
    _ensure_localization_poller()
    return JSONResponse(asdict(_task))


def _snapshot() -> dict[str, Any]:
    return {
        "pose": asdict(_pose) if _pose else None,
        "task": asdict(_task),
        "teleop_active": _teleop_active,
        "localization": _localization,
    }


async def status_stream(request: Request):
    """SSE stream of (pose, task, teleop_active, localization) snapshots, one per change."""
    await _maybe_restore_cached_pose()
    _ensure_localization_poller()
    async def gen():
        yield _sse_event(_snapshot())
        while True:
            if await request.is_disconnected():
                return
            try:
                await asyncio.wait_for(_state_event.wait(), timeout=15.0)
            except asyncio.TimeoutError:
                # heartbeat — keeps proxies from closing the stream
                yield ": ping\n\n"
                continue
            yield _sse_event(_snapshot())
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


_restore_done = False
_localization_poller_task: asyncio.Task | None = None


async def _poll_localization_forever() -> None:
    """Background loop: poll nav_service status REP at 1 Hz, push diffs
    onto the SSE stream. A single failure (nav_service down, network)
    sets _localization to None and keeps trying — operator sees the
    indicator go grey, not stuck on the last good value."""
    global _localization
    while True:
        prev = _localization
        try:
            reply = await asyncio.to_thread(
                _zmq_request_blocking, {}, 1.0, NAV_STATUS_PORT
            )
            _localization = reply.get("localization")
        except Exception as e:
            if _localization is not None:
                logger.warning("localization poll failed: %s", e)
            _localization = None
        if _localization != prev:
            _bump()
        await asyncio.sleep(LOCALIZATION_POLL_INTERVAL_S)


def _ensure_localization_poller() -> None:
    """Start the poller on the first SSE/get_status call. asyncio.create_task
    needs a running event loop, so we defer it instead of starting at import."""
    global _localization_poller_task
    if _localization_poller_task is None or _localization_poller_task.done():
        _localization_poller_task = asyncio.create_task(_poll_localization_forever())


async def _maybe_restore_cached_pose() -> None:
    """Re-forward the cached user pose to nav_service exactly once.

    Called lazily from get_pose / status_stream — the dashboard hits
    one of these immediately on /nav page load, which gives us a live
    event loop to push from. Avoids needing a Starlette lifespan hook
    (a2a-sdk's app builder doesn't expose add_event_handler reliably).
    """
    global _restore_done
    if _restore_done:
        return
    _restore_done = True
    if _pose is None or _pose.source != "user":
        return
    logger.info("forwarding cached pose (%.3f, %.3f, %.3f) to nav_service",
                _pose.x, _pose.y, _pose.theta)
    await _forward_initial_pose((_pose.x, _pose.y, _pose.theta))
