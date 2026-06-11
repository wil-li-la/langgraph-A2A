#!/usr/bin/env python3
"""Container-side YOLO-World worker — direct ZMQ → backend SSE injection.

Bypasses both `sensors_bridge` (which had stale ZMQ sockets) and the
ROS `/detections` topic for a fast verification path. Captures color
frames directly from the robot's head camera ZMQ stream (`head_color`
port 6011, single-part msgpack JPEG), runs YOLO-World on the local
GPU at a configurable cadence, and POSTs every result to the backend's
`/api/detect/inject` endpoint — which broadcasts via the existing SSE
pipe to the React overlay.

Why this is a worker not a node:
  - The ROS `yolo_world_node.py` is the right industrial-pattern
    long-run answer; this worker exists to verify the perception loop
    end-to-end while the lab `sensors_bridge` is being repaired.
  - Runs inside `isaac_ros_dev` because that container has the only
    Python environment on this machine with a working `torch + CUDA +
    ultralytics + numpy<2 / cv_bridge`-compatible stack.

Run:
    docker exec -it isaac_ros_dev bash -lc '
      python3 /workspaces/langgraph-A2A/backend/nav_bridge/yolo_worker.py \\
        --robot 192.168.1.38 \\
        --backend http://localhost:9999 \\
        --classes "chair,person,monitor,door,table,bottle,backpack" \\
        --hz 5
    '
"""
from __future__ import annotations

import argparse
import json
import logging
import time
import urllib.error
import urllib.request
from threading import Lock

import cv2
import msgpack
import numpy as np
import zmq

logger = logging.getLogger("yolo_worker")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def capture_head_frame(sock: zmq.Socket, timeout_s: float = 5.0) -> tuple[np.ndarray, int]:
    """Receive one color frame from the head camera ZMQ publisher.

    Wire format: single-part msgpack {ts_ns, h, w, encoding, data} where
    `data` is JPEG bytes. Frame is rotated 90° CW to match the dashboard.
    """
    sock.setsockopt(zmq.RCVTIMEO, int(timeout_s * 1000))
    raw = sock.recv()
    msg = msgpack.unpackb(raw, raw=False)
    data = msg.get("data")
    if not isinstance(data, (bytes, bytearray)):
        raise RuntimeError(f"head frame missing 'data'; keys={list(msg.keys())}")
    frame = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise RuntimeError(f"cv2 could not decode head buffer ({len(data)} bytes)")
    frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    ts_ns = int(msg.get("ts_ns", time.time_ns()))
    return frame, ts_ns


def capture_arm_frame(sock: zmq.Socket, timeout_s: float = 5.0) -> tuple[np.ndarray, int]:
    """Receive one color frame from the d405 wrist camera (port 6002).

    Wire format: multi-part `[topic="rgb", ts, payload]` where payload is
    blosc2-compressed raw BGR (640x480x3 or 1280x720x3) or JPEG fallback.
    No rotation — d405 is mounted upright.
    """
    sock.setsockopt(zmq.RCVTIMEO, int(timeout_s * 1000))
    parts = sock.recv_multipart()
    if not parts or parts[0] != b"rgb":
        raise RuntimeError(f"expected rgb topic, got {parts[0] if parts else 'empty'!r}")

    payload = parts[-1]
    # The middle part is a timestamp blob; not strictly needed for inference.
    try:
        import blosc2  # type: ignore
        raw = bytes(blosc2.decompress(payload))
    except Exception:
        raw = payload

    if len(raw) == 640 * 480 * 3:
        frame = np.frombuffer(raw, dtype=np.uint8).reshape(480, 640, 3)
    elif len(raw) == 1280 * 720 * 3:
        frame = np.frombuffer(raw, dtype=np.uint8).reshape(720, 1280, 3)
    else:
        frame = cv2.imdecode(np.frombuffer(payload, np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise RuntimeError(f"failed to decode d405 frame (raw_len={len(raw)})")
    return frame, time.time_ns()


def post_inject(backend: str, payload: dict, timeout_s: float = 3.0) -> bool:
    req = urllib.request.Request(
        f"{backend}/api/detect/inject",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            r.read()
        return True
    except urllib.error.URLError as e:
        logger.warning("inject POST failed: %s", e)
        return False


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--robot", default="192.168.1.38")
    p.add_argument("--port", type=int, default=0, help="Override ZMQ port (0=default per camera)")
    p.add_argument("--backend", default="http://localhost:9999")
    p.add_argument("--camera", default="head", choices=["head", "arm"])
    p.add_argument("--classes", default="chair,person,monitor,door,table,bottle,backpack")
    p.add_argument("--model", default="yolov8s-worldv2.pt")
    p.add_argument("--conf", type=float, default=0.25)
    p.add_argument("--iou", type=float, default=0.45)
    p.add_argument("--hz", type=float, default=5.0, help="Inference cadence (Hz)")
    p.add_argument("--device", default="cuda:0")
    p.add_argument("--max-iters", type=int, default=0, help="Stop after N inferences (0 = forever)")
    args = p.parse_args()

    classes = [c.strip() for c in args.classes.split(",") if c.strip()]

    logger.info("loading YOLOWorld model=%s classes=%s", args.model, classes)
    from ultralytics import YOLOWorld
    model = YOLOWorld(args.model)
    model.set_classes(classes)
    # First call after set_classes warms the cuda graph.
    _ = model.predict(np.zeros((480, 640, 3), dtype=np.uint8), verbose=False)

    # Port defaults differ per camera: head=6011 single-part msgpack JPEG,
    # arm=6002 multi-part with blosc2-compressed raw frames.
    if args.camera == "head":
        port = args.port if args.port else 6011
        capture = capture_head_frame
        subscribe = b""
    else:  # arm
        port = args.port if args.port else 6002
        capture = capture_arm_frame
        subscribe = b"rgb"

    ctx = zmq.Context.instance()
    sock = ctx.socket(zmq.SUB)
    sock.setsockopt(zmq.RCVHWM, 1)
    sock.setsockopt(zmq.SUBSCRIBE, subscribe)
    addr = f"tcp://{args.robot}:{port}"
    sock.connect(addr)
    logger.info("ZMQ SUB connected to %s (camera=%s)", addr, args.camera)

    period = 1.0 / max(args.hz, 0.1)
    next_t = time.monotonic()
    fps_window: list[float] = []
    iters = 0

    while True:
        # Steady cadence — sleep until next slot.
        now = time.monotonic()
        if now < next_t:
            time.sleep(next_t - now)
        next_t += period

        try:
            frame, ts_ns = capture(sock, timeout_s=2.0)
        except zmq.error.Again:
            logger.warning("no frame within 2s; robot driver may be down")
            continue
        except Exception as e:
            logger.warning("capture failed: %s", e)
            continue

        h, w = frame.shape[:2]
        t0 = time.monotonic()
        try:
            results = model.predict(
                source=frame, conf=args.conf, iou=args.iou,
                verbose=False, device=args.device,
            )
        except Exception as e:
            logger.error("YOLO predict failed: %s", e)
            continue
        dt = time.monotonic() - t0
        fps_window.append(dt)
        if len(fps_window) > 30:
            fps_window = fps_window[-30:]

        # Pack detections.
        r = results[0]
        boxes = r.boxes
        names = r.names
        dets: list[dict] = []
        if boxes is not None and len(boxes) > 0:
            xyxy = boxes.xyxy.cpu().numpy()
            conf = boxes.conf.cpu().numpy()
            cls = boxes.cls.cpu().numpy().astype(int)
            for (x1, y1, x2, y2), c, k in zip(xyxy, conf, cls):
                dets.append({
                    "label": str(names.get(int(k), str(int(k)))),
                    "bbox_2d": [float(x1), float(y1), float(x2), float(y2)],
                    "confidence": float(c),
                    "description": "",
                })

        payload = {
            "camera": args.camera,
            "query": f"yolo_world: {', '.join(classes)}",
            "location": "live",
            "image_w": int(w),
            "image_h": int(h),
            "image_path": "",
            "detections": dets,
        }
        post_inject(args.backend, payload)

        avg_ms = (sum(fps_window) / len(fps_window)) * 1000
        if len(fps_window) % 10 == 0:
            logger.info(
                "frame %dx%d -> %d dets, %.0fms inference (avg %.0fms, %.1f infer-Hz)",
                w, h, len(dets), dt * 1000, avg_ms, 1000.0 / max(avg_ms, 1e-3),
            )

        iters += 1
        if args.max_iters and iters >= args.max_iters:
            logger.info("max iters reached, exiting")
            return


if __name__ == "__main__":
    main()
