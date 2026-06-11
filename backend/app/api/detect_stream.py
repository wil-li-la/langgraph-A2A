"""Real-time detection event broadcaster + SSE endpoint.

`detect_impl()` publishes a dict here after every successful VLM call.
The frontend subscribes via `/api/detect/stream` and overlays bboxes on
the dashboard's Head / Gripper camera tiles.

Coordinate convention emitted to the frontend:
  - `bbox_norm`: list of `[x1, y1, x2, y2]` in **normalized [0,1]
    coordinates of the upright frame** (same orientation the VLM saw and
    the same orientation the dashboard canvas renders after applying
    CAMERA_ROTATION). The frontend can therefore stretch each bbox to
    its canvas's bounding box with no further math.

The broadcaster is in-process — multiple SSE subscribers each get their
own bounded queue. No persistence; clients that connect after a
detection only see future events. A separate `GET /api/detect/latest`
returns the most-recent event per camera so a freshly opened dashboard
can paint immediately.
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
import time
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

logger = logging.getLogger(__name__)


# ---------- Broadcaster ----------------------------------------------------

_subscribers_lock = threading.Lock()
_subscribers: set["queue.Queue[dict[str, Any]]"] = set()

_latest_lock = threading.Lock()
_latest: dict[str, dict[str, Any]] = {}  # keyed by camera ("head" / "arm")


def publish_detection(
    *,
    camera: str,
    query: str,
    location: str,
    image_w: int,
    image_h: int,
    image_path: str,
    detections: list[dict],
    ts: str | None = None,
) -> None:
    """Broadcast a detection event to all SSE subscribers.

    Thread-safe; safe to call from worker threads. `detections` items must
    each have `label`, `bbox_2d` (pixel coords in the upright frame),
    `confidence`, `description`. The function attaches a `bbox_norm` to
    each detection so the frontend doesn't need to know image dims.
    """
    norm_dets: list[dict] = []
    for d in detections:
        bbox = d.get("bbox_2d")
        if not (isinstance(bbox, list) and len(bbox) == 4):
            continue
        try:
            x1, y1, x2, y2 = [float(v) for v in bbox]
        except Exception:
            continue
        w = max(1.0, float(image_w))
        h = max(1.0, float(image_h))
        norm_dets.append({
            "label": d.get("label", ""),
            "confidence": float(d.get("confidence", 0.0) or 0.0),
            "description": d.get("description", ""),
            "bbox_2d": [x1, y1, x2, y2],
            "bbox_norm": [
                max(0.0, min(1.0, x1 / w)),
                max(0.0, min(1.0, y1 / h)),
                max(0.0, min(1.0, x2 / w)),
                max(0.0, min(1.0, y2 / h)),
            ],
        })

    event = {
        "ts": ts or str(time.time_ns()),
        "camera": camera,
        "query": query,
        "location": location,
        "image_w": int(image_w),
        "image_h": int(image_h),
        "image_path": image_path,
        "detections": norm_dets,
    }

    with _latest_lock:
        _latest[camera] = event

    with _subscribers_lock:
        dead: list[queue.Queue] = []
        for q in _subscribers:
            try:
                q.put_nowait(event)
            except queue.Full:
                # Slow consumer — drop oldest by draining one slot. Detection
                # events are at most a few per second so this is acceptable.
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except Exception:
                    dead.append(q)
        for q in dead:
            _subscribers.discard(q)


def _subscribe() -> "queue.Queue[dict[str, Any]]":
    q: "queue.Queue[dict[str, Any]]" = queue.Queue(maxsize=64)
    with _subscribers_lock:
        _subscribers.add(q)
    return q


def _unsubscribe(q: "queue.Queue[dict[str, Any]]") -> None:
    with _subscribers_lock:
        _subscribers.discard(q)


# ---------- Routes ---------------------------------------------------------


async def get_latest(_request: Request) -> JSONResponse:
    """Most-recent detection per camera. Used to paint on dashboard load."""
    with _latest_lock:
        snapshot = {k: v for k, v in _latest.items()}
    return JSONResponse({"latest": snapshot})


async def stream_detections(request: Request) -> StreamingResponse:
    """SSE stream of every detection event from now on."""
    q = _subscribe()

    async def event_gen():
        try:
            # Replay the most-recent snapshot so a fresh subscriber sees
            # something immediately rather than waiting on the next VLM call.
            with _latest_lock:
                replay = list(_latest.values())
            for e in replay:
                yield f"data: {json.dumps(e, ensure_ascii=False)}\n\n"

            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.to_thread(q.get, True, 15.0)
                except queue.Empty:
                    # Heartbeat so reverse-proxies don't close the idle conn.
                    yield ": keepalive\n\n"
                    continue
                if event is None:
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            _unsubscribe(q)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def run_detection(request: Request) -> JSONResponse:
    """Run a real VLM detection on the current robot camera and broadcast.

    Body: `{ query: str, camera?: "head"|"arm", location?: str }`.
    Runs `detect_impl` (which captures a frame + calls the configured VLM
    + persists to scene memory + publishes to the SSE stream). Returned
    JSON includes the text summary and the parsed detections list, so a
    caller can confirm success without subscribing to SSE.

    Offloaded to a worker thread so the event loop stays free while the
    VLM call (~1–8s) is in flight.
    """
    try:
        body = await request.json()
    except Exception as e:
        return JSONResponse({"error": f"bad JSON: {e}"}, status_code=400)

    query = (body.get("query") or "").strip()
    if not query:
        return JSONResponse({"error": "query required"}, status_code=400)
    camera = (body.get("camera") or "head").strip()
    location_override = body.get("location")

    # Lazy import keeps detect_tools out of the import graph until first use.
    from app.tools.detect_tools import detect_impl

    text, record, detections = await asyncio.to_thread(
        detect_impl,
        query=query,
        camera=camera,
        location_override=location_override,
        check_budget=False,
    )
    ok = text.startswith("OK")
    return JSONResponse({
        "ok": ok,
        "text": text,
        "record": record,
        "detections": detections,
    })


async def inject_detection(request: Request) -> JSONResponse:
    """Inject a synthetic detection event into the broadcaster.

    Local-development utility — lets Playwright/manual testers exercise
    the dashboard overlay without a real camera or VLM. Body schema:
      { camera: "head"|"arm", query: str, location: str, image_w, image_h,
        detections: [{label, bbox_2d, confidence, description?}] }
    All fields except `camera` and `detections` are optional.
    """
    try:
        body = await request.json()
    except Exception as e:
        return JSONResponse({"error": f"bad JSON: {e}"}, status_code=400)

    camera = body.get("camera") or "head"
    detections = body.get("detections") or []
    if not isinstance(detections, list):
        return JSONResponse({"error": "detections must be a list"}, status_code=400)

    publish_detection(
        camera=camera,
        query=body.get("query", "test"),
        location=body.get("location", "unknown"),
        image_w=int(body.get("image_w", 1280)),
        image_h=int(body.get("image_h", 720)),
        image_path=body.get("image_path", "/tmp/inject.jpg"),
        detections=detections,
    )
    return JSONResponse({"ok": True, "broadcast_to_camera": camera, "count": len(detections)})


detect_stream_routes = [
    Route("/api/detect/stream", stream_detections, methods=["GET"]),
    Route("/api/detect/latest", get_latest, methods=["GET"]),
    Route("/api/detect/inject", inject_detection, methods=["POST"]),
    Route("/api/detect/run", run_detection, methods=["POST"]),
]
