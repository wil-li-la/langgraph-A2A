"""ZMQ subscriber: bridges the ROS-side YOLO-World detection stream into
the existing dashboard SSE broadcaster (`publish_detection`).

Run once at backend startup with `DETECT_ZMQ_HOST` set. Drops detection
events into the same SSE pipe the qwen2.5vl path uses, so the React
overlay is identical — bbox boxes show up on whichever camera tile
matches the published `camera` field.

Wire format mirrors what `nav_bridge/detection_bridge.py` publishes:
    msgpack {"ts_ns", "camera", "image_w", "image_h", "detections": [...]}

Failure mode: if the host is unreachable, the worker logs once and
sleeps; subscribers are not blocking the event loop.
"""
from __future__ import annotations

import logging
import os
import threading
import time

import msgpack
import zmq

from app.api.detect_stream import publish_detection

logger = logging.getLogger(__name__)

_started = False
_lock = threading.Lock()


def _worker(host: str, port: int) -> None:
    addr = f"tcp://{host}:{port}"
    ctx = zmq.Context.instance()
    sock = ctx.socket(zmq.SUB)
    sock.setsockopt(zmq.RCVHWM, 32)
    sock.setsockopt(zmq.SUBSCRIBE, b"")
    sock.setsockopt(zmq.RCVTIMEO, 2000)  # so a stalled feed doesn't block reconnect
    sock.connect(addr)
    logger.info("detect_zmq_consumer: connected SUB to %s", addr)

    while True:
        try:
            raw = sock.recv()
        except zmq.error.Again:
            continue
        except Exception as e:
            logger.warning("detect_zmq_consumer recv failed: %s; reconnecting", e)
            time.sleep(1.0)
            try:
                sock.disconnect(addr)
            except Exception:
                pass
            sock.connect(addr)
            continue

        try:
            event = msgpack.unpackb(raw, raw=False)
        except Exception as e:
            logger.warning("malformed detection payload: %s", e)
            continue

        try:
            publish_detection(
                camera=event.get("camera", "head"),
                query="yolo_world",
                location=event.get("location", "live"),
                image_w=int(event.get("image_w", 0) or 0),
                image_h=int(event.get("image_h", 0) or 0),
                image_path="",
                detections=event.get("detections", []),
                ts=str(event.get("ts_ns", time.time_ns())),
            )
        except Exception as e:
            logger.warning("publish_detection failed: %s", e)


def start_if_configured() -> None:
    """Kick off worker thread(s) once per process. Idempotent.

    Reads DETECT_ZMQ_HOST + DETECT_ZMQ_PORT from env. Default port 5562
    matches `nav_bridge/detection_bridge.py`. DETECT_ZMQ_PORT accepts a
    comma-separated list (e.g. "5570,5571") to fan-in multiple per-camera
    detect_bridge instances. When the host env is unset or "", does
    nothing — preserves the no-ROS development path.
    """
    global _started
    with _lock:
        if _started:
            return
        host = os.getenv("DETECT_ZMQ_HOST", "").strip()
        if not host:
            logger.info("DETECT_ZMQ_HOST not set; ROS detection consumer disabled")
            _started = True
            return
        port_spec = os.getenv("DETECT_ZMQ_PORT", "5562")
        ports = [int(p.strip()) for p in port_spec.split(",") if p.strip()]
        for port in ports:
            t = threading.Thread(
                target=_worker, args=(host, port), daemon=True,
                name=f"detect_zmq_consumer:{port}",
            )
            t.start()
        _started = True
