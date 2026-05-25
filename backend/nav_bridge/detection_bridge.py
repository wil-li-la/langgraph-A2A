#!/usr/bin/env python3
"""ROS 2 `/detections` → ZMQ PUB bridge.

Subscribes to `vision_msgs/Detection2DArray` on `/detections` (published
by `yolo_world_node.py`) and republishes each frame as msgpack on ZMQ
PUB port 5562. The backend's existing detect_stream broadcaster can
subscribe to this port and reuse the SSE pipe that already drives the
dashboard's bbox overlay — zero React changes needed.

Wire format (single ZMQ frame, msgpack):
    {
        "ts_ns":   int,                # ROS header.stamp converted to ns
        "frame":   str,                # header.frame_id (e.g. "camera_color")
        "camera":  str,                # logical camera, "head" or "arm"
        "image_w": int,                # not in the ROS msg; injected by backend
        "image_h": int,                #   ...or filled here from a /camera_info subscriber
        "detections": [
            {"label": str, "confidence": float,
             "bbox_2d": [x1, y1, x2, y2], "bbox_norm": [...]}
            ...
        ]
    }

Image dimensions: the ROS Detection2DArray itself doesn't carry image
W/H, so we co-subscribe to `/camera/color/camera_info` for that. (The
sensors_bridge already publishes that topic.)

Run pattern (inside isaac_ros_dev container, after sensors_bridge +
yolo_world_node are up):

    source /opt/ros/humble/setup.bash
    python3 backend/nav_bridge/detection_bridge.py \\
        --detections-topic /detections \\
        --camera-info-topic /camera/color/camera_info \\
        --camera head \\
        --zmq-port 5562
"""
from __future__ import annotations

import argparse
import logging
import threading
import time
from dataclasses import dataclass

import msgpack
import rclpy
import zmq
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import CameraInfo
from vision_msgs.msg import Detection2DArray

logger = logging.getLogger("detection_bridge")

SENSOR_QOS = QoSProfile(
    reliability=ReliabilityPolicy.BEST_EFFORT,
    history=HistoryPolicy.KEEP_LAST,
    depth=2,
)


@dataclass
class ImageDims:
    w: int = 0
    h: int = 0


class DetectionBridge(Node):
    def __init__(self, args: argparse.Namespace) -> None:
        super().__init__("detection_bridge")

        self._camera = args.camera
        # Seed dims from CLI — post-FUNMAP /camera/color/camera_info is gone
        # so the CameraInfo sub stays empty. Override is fine via CLI default.
        self._dims = ImageDims(w=int(args.image_w), h=int(args.image_h))
        self._lock = threading.Lock()

        # ZMQ PUB — bound, not connected, so multiple subscribers fan out.
        ctx = zmq.Context.instance()
        self._sock = ctx.socket(zmq.PUB)
        self._sock.setsockopt(zmq.SNDHWM, 16)
        addr = f"tcp://{args.zmq_bind}:{args.zmq_port}"
        self._sock.bind(addr)
        self.get_logger().info(f"ZMQ PUB bound to {addr}")

        self._det_sub = self.create_subscription(
            Detection2DArray,
            args.detections_topic,
            self._on_detections,
            10,
        )
        self._info_sub = self.create_subscription(
            CameraInfo,
            args.camera_info_topic,
            self._on_camera_info,
            SENSOR_QOS,
        )

        self.get_logger().info(
            f"Bridging {args.detections_topic} → ZMQ port {args.zmq_port} "
            f"(camera={self._camera})"
        )

    def _on_camera_info(self, msg: CameraInfo) -> None:
        with self._lock:
            self._dims.w = int(msg.width)
            self._dims.h = int(msg.height)

    def _on_detections(self, msg: Detection2DArray) -> None:
        with self._lock:
            w = self._dims.w
            h = self._dims.h
        if w == 0 or h == 0:
            # Without dims we can't normalize bboxes for the frontend.
            # RcutilsLogger in Humble has no warning_once — gate manually.
            if not getattr(self, "_warned_no_dims", False):
                self.get_logger().warning(
                    "camera_info not yet received — emitting bbox_norm=null"
                )
                self._warned_no_dims = True

        detections = []
        for d in msg.detections:
            if not d.results:
                continue
            best = max(d.results, key=lambda r: r.hypothesis.score)
            cx = float(d.bbox.center.position.x)
            cy = float(d.bbox.center.position.y)
            sx = float(d.bbox.size_x)
            sy = float(d.bbox.size_y)
            x1, y1, x2, y2 = cx - sx / 2, cy - sy / 2, cx + sx / 2, cy + sy / 2
            entry = {
                "label": str(best.hypothesis.class_id),
                "confidence": float(best.hypothesis.score),
                "bbox_2d": [x1, y1, x2, y2],
                "bbox_norm": (
                    [
                        max(0.0, min(1.0, x1 / w)),
                        max(0.0, min(1.0, y1 / h)),
                        max(0.0, min(1.0, x2 / w)),
                        max(0.0, min(1.0, y2 / h)),
                    ]
                    if (w and h)
                    else None
                ),
            }
            detections.append(entry)

        stamp = msg.header.stamp
        ts_ns = int(stamp.sec) * 1_000_000_000 + int(stamp.nanosec)
        payload = {
            "ts_ns": ts_ns or time.time_ns(),
            "frame": msg.header.frame_id,
            "camera": self._camera,
            "image_w": w,
            "image_h": h,
            "detections": detections,
        }
        try:
            self._sock.send(msgpack.packb(payload, use_bin_type=True))
        except zmq.ZMQError as e:
            self.get_logger().warning(f"ZMQ send failed: {e}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="ROS /detections → ZMQ bridge.")
    p.add_argument("--detections-topic", default="/detections")
    p.add_argument("--camera-info-topic", default="/camera/color/camera_info")
    p.add_argument(
        "--camera",
        default="head",
        choices=["head", "arm", "gripper", "wrist"],
        help="Logical camera label the dashboard uses to route the overlay.",
    )
    p.add_argument("--zmq-port", type=int, default=5562)
    p.add_argument("--zmq-bind", default="0.0.0.0")
    p.add_argument("--image-w", type=int, default=1280,
                   help="Frame width — used to normalize bboxes (arducam=1280, d405=640).")
    p.add_argument("--image-h", type=int, default=720,
                   help="Frame height (arducam=720, d405=480).")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    rclpy.init()
    node = DetectionBridge(args)
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
