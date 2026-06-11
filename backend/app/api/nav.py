"""REST + SSE adapter for the on-robot FUNMAP nav stack.

Subscribes to the robot's ZMQ surface at $STRETCH_ROBOT_HOST
(default 192.168.1.38) — map_pub (5562), amcl_pose (5563) — and forwards
nav goals to goto (5557). Exposes the dashboard's REST/SSE contract on top.

History: pre-2026-05-25 this talked to a lab-side `nav_service` in the
isaac_ros_dev container at localhost:5560/5561/5562. The FUNMAP migration
moved AMCL + static map onto the robot itself; that whole lab stack is
gone. See docs/steretch3_protocol/protocols.md for the wire contract.

Endpoints:
  GET  /api/nav/pose              — latest robot pose (live AMCL or cached)
  POST /api/nav/pose              — user override (local-only; AMCL overrides on next tick)
  POST /api/nav/goto              — submit a nav goal (REQ to robot 5557, non-blocking)
  GET  /api/nav/status            — current task state snapshot
  GET  /api/nav/status/stream     — SSE stream of state changes
  GET  /api/nav/map               — map metadata (live from 5562 if available, else disk)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import cv2
import msgpack
import yaml
import zmq

from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

logger = logging.getLogger(__name__)

# ----- Configuration ------------------------------------------------------

ROBOT_HOST = os.getenv("STRETCH_ROBOT_HOST", "192.168.1.38")
GOTO_PORT = int(os.getenv("STRETCH_GOTO_PORT", "5557"))
SCAN_PORT = int(os.getenv("STRETCH_SCAN_PORT", "5561"))
MAP_PORT = int(os.getenv("STRETCH_MAP_PORT", "5562"))
AMCL_POSE_PORT = int(os.getenv("STRETCH_AMCL_POSE_PORT", "5563"))
AMCL_SEED_PORT = int(os.getenv("STRETCH_AMCL_SEED_PORT", "5564"))
DEFAULT_TIMEOUT_S = float(os.getenv("STRETCH_GOTO_TIMEOUT_S", "60"))
AMCL_SEED_TIMEOUT_S = float(os.getenv("STRETCH_AMCL_SEED_TIMEOUT_S", "2.0"))

# Robot publishes amcl_pose at 5 Hz; 2 s absorbs WiFi jitter without flapping
# the localization status indicator.
AMCL_STALE_S = float(os.getenv("STRETCH_AMCL_STALE_S", "2.0"))

POSE_CACHE_PATH = Path(
    os.getenv("NAV_POSE_CACHE",
              str(Path.home() / ".cache" / "langgraph-A2A" / "nav-pose.json"))
)


def _load_map_metadata_from_disk() -> dict[str, Any]:
    """Static map metadata from the repo's checked-in map.yaml. Used as
    fallback before the first live frame arrives on 5562, and as the
    source of the frontend's PNG asset URL (the OccupancyGrid bytes from
    5562 have no stable URL to point an <img> at)."""
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
        "image": "/maps/305_map.png",
        "resolution": float(data["resolution"]),
        "origin": [float(x) for x in data["origin"]],
        "width_px": int(width_px),
        "height_px": int(height_px),
        "frame_id": "map",
        "source": "disk",
    }


DISK_MAP_METADATA = _load_map_metadata_from_disk()
logger.info(
    "DISK_MAP_METADATA loaded: origin=%s resolution=%.4f size=%dx%d",
    DISK_MAP_METADATA["origin"],
    DISK_MAP_METADATA["resolution"],
    DISK_MAP_METADATA["width_px"],
    DISK_MAP_METADATA["height_px"],
)

# ----- State --------------------------------------------------------------

@dataclass
class Pose:
    x: float
    y: float
    theta: float          # radians, map frame
    source: str = "user"  # "user" | "amcl" | "nav_result"
    ts_ms: int = field(default_factory=lambda: int(time.time() * 1000))


@dataclass
class NavTask:
    request_id: str
    target: tuple[float, float, float]
    state: str              # "idle" | "pending" | "running" | "done"
    status: str | None      # "SUCCESS" | "FAILED" | "ROBOT_ERROR"
    reason: str             # raw robot reply string, "" if none
    started_ms: int
    finished_ms: int | None
    final_pose: tuple[float, float, float] | None


def _load_cached_pose() -> Pose | None:
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
_teleop_active: bool = False
# Live map cache from 5562 (XPUB last-value-cached on robot side, so first
# subscribe gives us the cached map immediately). None until first frame.
_live_map: dict | None = None
_last_amcl_ms: int = 0
_state_event = asyncio.Event()
_state_lock = threading.Lock()
_loop: asyncio.AbstractEventLoop | None = None


def set_teleop_active(active: bool) -> None:
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
    return _pose


def _bump() -> None:
    """Wake SSE subscribers. Called from the event loop thread."""
    _state_event.set()
    _state_event.clear()


def _bump_threadsafe() -> None:
    """Same as _bump but safe to invoke from non-loop threads (subscriber
    threads). asyncio.Event isn't documented thread-safe, so we schedule
    the set/clear on the captured loop."""
    if _loop is not None and _loop.is_running():
        try:
            _loop.call_soon_threadsafe(_bump)
        except RuntimeError:
            pass  # loop closing


def _localization_status() -> str:
    """Coarse localization indicator derived from amcl_pose freshness."""
    if _last_amcl_ms == 0:
        return "unseeded"
    age_s = (int(time.time() * 1000) - _last_amcl_ms) / 1000.0
    if age_s > AMCL_STALE_S:
        return "stale"
    return "ok"


# ----- Robot ZMQ — goto REQ (5557) ----------------------------------------

_GOTO_OK = "ok"


def _goto_blocking(target: tuple[float, float, float], timeout_s: float) -> dict:
    """Synchronous ZMQ REQ to robot goto:5557. Runs in a worker thread.

    Wire shape (protocols.md):
      send: msgpack({"x": float, "y": float, "theta": float})
      recv: utf-8 string — "ok" | "no_map: scan first" | "timeout: …" |
            "obstructed: …" | "invalid_goal: …" | "robot_error: …"
    Any non-"ok" reply is failure; the full string is preserved so the
    operator can read the actual cause in the dashboard.
    """
    addr = f"tcp://{ROBOT_HOST}:{GOTO_PORT}"
    ctx = zmq.Context.instance()
    sock = ctx.socket(zmq.REQ)
    sock.setsockopt(zmq.RCVTIMEO, int((timeout_s + 5) * 1000))
    sock.setsockopt(zmq.LINGER, 0)
    sock.connect(addr)
    try:
        sock.send(msgpack.packb({"x": float(target[0]),
                                 "y": float(target[1]),
                                 "theta": float(target[2])}))
        reply = sock.recv().decode("utf-8", errors="replace")
    finally:
        sock.close()
    return {"ok": reply == _GOTO_OK, "reply": reply}


def _seed_amcl_blocking(target: tuple[float, float, float],
                        timeout_s: float = AMCL_SEED_TIMEOUT_S) -> dict:
    """REQ to robot's AMCL seed bridge on $STRETCH_AMCL_SEED_PORT (5564).

    Replaces RViz "2D Pose Estimate" for the headless workflow. Robot-side
    bridge republishes the seed to ROS2 /amcl/initialpose. Until the robot
    implements this endpoint, calls will REQ-timeout — handled by caller.
    """
    addr = f"tcp://{ROBOT_HOST}:{AMCL_SEED_PORT}"
    ctx = zmq.Context.instance()
    sock = ctx.socket(zmq.REQ)
    sock.setsockopt(zmq.RCVTIMEO, int(timeout_s * 1000))
    sock.setsockopt(zmq.SNDTIMEO, int(timeout_s * 1000))
    sock.setsockopt(zmq.LINGER, 0)
    sock.connect(addr)
    try:
        sock.send(msgpack.packb({"x": float(target[0]),
                                 "y": float(target[1]),
                                 "theta": float(target[2])}))
        reply = sock.recv().decode("utf-8", errors="replace")
    finally:
        sock.close()
    return {"ok": reply == _GOTO_OK, "reply": reply}


# ----- Robot ZMQ — amcl_pose (5563) + map_pub (5562) subscribers ----------

def _amcl_pose_subscriber_thread() -> None:
    """Drain amcl_pose at 5 Hz, push into _pose with source='amcl'."""
    global _pose, _last_amcl_ms
    ctx = zmq.Context.instance()
    sock = ctx.socket(zmq.SUB)
    sock.setsockopt(zmq.SUBSCRIBE, b"")
    sock.setsockopt(zmq.CONFLATE, 1)
    sock.setsockopt(zmq.LINGER, 0)
    addr = f"tcp://{ROBOT_HOST}:{AMCL_POSE_PORT}"
    sock.connect(addr)
    logger.info("amcl_pose subscriber connected: %s", addr)
    try:
        while True:
            raw = sock.recv()
            try:
                msg = msgpack.unpackb(raw, raw=False)
                x = float(msg["x"])
                y = float(msg["y"])
                theta = float(msg["theta"])
            except (msgpack.UnpackException, KeyError, TypeError, ValueError) as e:
                logger.warning("malformed amcl_pose frame (%d bytes): %s", len(raw), e)
                continue
            with _state_lock:
                _pose = Pose(x=x, y=y, theta=theta, source="amcl")
                _last_amcl_ms = int(time.time() * 1000)
            _bump_threadsafe()
    except zmq.ContextTerminated:
        return
    except Exception:
        logger.exception("amcl_pose subscriber died")


def _map_subscriber_thread() -> None:
    """Drain map_pub. XPUB on the robot resends the cached last map on
    every new subscribe, so we get the map within ms of connecting and
    then nothing until map.yaml changes.

    We cache metadata only — the bulky `data` bytes blob is dropped.
    The frontend renders the disk PGM asset; this subscriber exists
    primarily to detect drift between the robot's live static map and
    the checked-in PNG."""
    global _live_map
    ctx = zmq.Context.instance()
    sock = ctx.socket(zmq.SUB)
    sock.setsockopt(zmq.SUBSCRIBE, b"")
    sock.setsockopt(zmq.LINGER, 0)
    addr = f"tcp://{ROBOT_HOST}:{MAP_PORT}"
    sock.connect(addr)
    logger.info("map_pub subscriber connected: %s", addr)
    try:
        while True:
            raw = sock.recv()
            try:
                msg = msgpack.unpackb(raw, raw=False)
                meta = {
                    "image": DISK_MAP_METADATA["image"],
                    "resolution": float(msg["resolution"]),
                    "origin": [float(msg["origin"]["x"]),
                               float(msg["origin"]["y"]),
                               float(msg["origin"]["theta"])],
                    "width_px": int(msg["width"]),
                    "height_px": int(msg["height"]),
                    "frame_id": str(msg.get("frame_id", "map")),
                    "source": "robot",
                }
            except (msgpack.UnpackException, KeyError, TypeError, ValueError) as e:
                logger.warning("malformed map frame (%d bytes): %s", len(raw), e)
                continue
            with _state_lock:
                _live_map = meta
            if (abs(meta["resolution"] - DISK_MAP_METADATA["resolution"]) > 1e-6
                    or meta["origin"] != DISK_MAP_METADATA["origin"]
                    or meta["width_px"] != DISK_MAP_METADATA["width_px"]
                    or meta["height_px"] != DISK_MAP_METADATA["height_px"]):
                logger.warning(
                    "live map metadata differs from disk PNG asset; "
                    "frontend overlay may be misaligned. live=%s disk=%s",
                    meta, DISK_MAP_METADATA,
                )
            logger.info("live map cached: %dx%d res=%.4f origin=%s",
                        meta["width_px"], meta["height_px"],
                        meta["resolution"], meta["origin"])
            _bump_threadsafe()
    except zmq.ContextTerminated:
        return
    except Exception:
        logger.exception("map_pub subscriber died")


_subscribers_started = False


def _ensure_subscribers_started() -> None:
    """Spawn the amcl_pose + map_pub subscriber threads on first endpoint
    hit. Daemon threads so they don't block shutdown. Captures the
    running loop for cross-thread SSE wakeups."""
    global _subscribers_started, _loop
    if _subscribers_started:
        return
    _subscribers_started = True
    try:
        _loop = asyncio.get_running_loop()
    except RuntimeError:
        _loop = None
    threading.Thread(target=_amcl_pose_subscriber_thread,
                     name="nav-amcl-sub", daemon=True).start()
    threading.Thread(target=_map_subscriber_thread,
                     name="nav-map-sub", daemon=True).start()


# ----- Goto task driver ---------------------------------------------------

async def _run_nav_task(task: NavTask, timeout_s: float):
    """Background coroutine: REQ to robot 5557, update _task, finalize.

    The robot's reply schema is a single string and does NOT echo the
    achieved pose. On success we copy the *target* into final_pose; the
    live amcl_pose stream is the source of truth for where the robot
    actually ended up.
    """
    global _task
    task.state = "running"
    _bump()
    try:
        result = await asyncio.to_thread(_goto_blocking, task.target, timeout_s)
    except Exception as e:
        task.state = "done"
        task.status = "ROBOT_ERROR"
        task.reason = f"robot goto unreachable at {ROBOT_HOST}:{GOTO_PORT}: {e}"
        task.finished_ms = int(time.time() * 1000)
        logger.warning("nav goto failed: %s", task.reason)
        _bump()
        return

    task.state = "done"
    if result["ok"]:
        task.status = "SUCCESS"
        task.reason = ""
        task.final_pose = task.target
    else:
        task.status = "FAILED"
        task.reason = result["reply"]
    task.finished_ms = int(time.time() * 1000)
    _bump()


# ----- Endpoint handlers --------------------------------------------------

async def get_pose(_request: Request):
    _ensure_subscribers_started()
    return JSONResponse(asdict(_pose) if _pose else None)


async def post_pose(request: Request):
    """Seed AMCL with the operator's clicked pose — replaces RViz
    "2D Pose Estimate" for the headless workflow.

    Forwards {x, y, theta} to the robot's AMCL seed bridge
    (`STRETCH_AMCL_SEED_PORT`, default 5564), which republishes onto
    ROS2 /amcl/initialpose. AMCL refines from there as the robot moves.

    Best-effort forward: if the robot bridge isn't reachable (e.g.
    endpoint not yet implemented), the local _pose cache still gets
    updated with source="user" so the dashboard dot reflects the
    operator's intent. The live amcl_pose stream will overwrite within
    one tick once seeding succeeds.
    """
    global _pose
    body = await request.json()
    try:
        target = (float(body["x"]), float(body["y"]), float(body["theta"]))
    except (KeyError, TypeError, ValueError) as e:
        return JSONResponse({"error": f"bad pose: {e}"}, status_code=400)

    with _state_lock:
        _pose = Pose(*target, source="user")
    _save_cached_pose(_pose)
    _bump()

    seed_result: dict[str, Any] = {"forwarded": False}
    try:
        result = await asyncio.to_thread(_seed_amcl_blocking, target)
        seed_result = {"forwarded": True, "ok": result["ok"], "reply": result["reply"]}
        if not result["ok"]:
            logger.warning("AMCL seed rejected by robot: %s", result["reply"])
    except Exception as e:
        logger.warning("AMCL seed unreachable at %s:%s: %s",
                       ROBOT_HOST, AMCL_SEED_PORT, e)
        seed_result = {"forwarded": False, "error": str(e)}

    return JSONResponse({"pose": asdict(_pose), "seed": seed_result})


async def post_goto(request: Request):
    """Submit a nav goal. Returns request_id immediately; status via SSE."""
    global _task
    _ensure_subscribers_started()
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
    _ensure_subscribers_started()
    return JSONResponse(asdict(_task))


def _snapshot() -> dict[str, Any]:
    return {
        "pose": asdict(_pose) if _pose else None,
        "task": asdict(_task),
        "teleop_active": _teleop_active,
        "localization": {
            "state": _localization_status(),
            "last_amcl_ms": _last_amcl_ms,
        },
    }


async def status_stream(request: Request):
    _ensure_subscribers_started()

    async def gen():
        yield _sse_event(_snapshot())
        while True:
            if await request.is_disconnected():
                return
            try:
                await asyncio.wait_for(_state_event.wait(), timeout=15.0)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            yield _sse_event(_snapshot())

    return StreamingResponse(gen(), media_type="text/event-stream")


async def get_map(_request: Request):
    _ensure_subscribers_started()
    return JSONResponse(_live_map or DISK_MAP_METADATA)


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
