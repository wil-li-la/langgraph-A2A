#!/usr/bin/env python3
"""YOLO-World open-vocabulary detection ROS 2 node.

Subscribes directly to the robot's camera ZMQ PUB (post-FUNMAP architecture
where ROS2 ports 6010-6013 are gone; only ZMQ 6000 arducam / 6002 d405
remain — see docs/steretch3_protocol/protocols.md). Frames decoded inline,
no sensors_bridge / ROS image hop. Publishes:

  - `/detections` (vision_msgs/Detection2DArray) — standard schema, so
    Foxglove/RViz/rosbag2/Nav2 obstacle layers consume it for free.
  - `/detections/annotated` (sensor_msgs/Image) — optional debug image
    with bboxes drawn (rqt_image_view friendly).

Class vocabulary is set per-launch via the `classes` parameter. Reset
at runtime by calling the `/yolo_world/set_classes` parameter service
(`ros2 param set /yolo_world classes '[...]'`) — the node reapplies
without restart.

Designed to run inside the lab `isaac_ros_dev` container alongside the
existing nav_service + nvblox stack:

    source /opt/ros/humble/setup.bash
    source /workspaces/isaac_ros-dev/install/setup.bash
    python3 backend/nav_bridge/yolo_world_node.py \\
        --zmq-addr tcp://192.168.1.38:6000 \\
        --frame-shape 720,1280,3 \\
        --classes "medicine bottle,patient,human,chair,door"

Dependencies (host or container): `ultralytics>=8.1` (brings torch),
`pyzmq`, `vision_msgs`. Optional: `blosc2` (only if the robot's
`cameras.arducam.compressed: true`). Falls back to YOLO-World v2 small
(`yolov8s-worldv2.pt`) — ~30 FPS on RTX 4080.
"""
from __future__ import annotations

import argparse
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import rclpy
import zmq
from rcl_interfaces.msg import ParameterDescriptor, SetParametersResult
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Image
from std_msgs.msg import Header
from vision_msgs.msg import (
    BoundingBox2D,
    Detection2D,
    Detection2DArray,
    ObjectHypothesisWithPose,
)


def _decode_zmq_payload(payload: bytes, shape: tuple[int, int, int]) -> np.ndarray:
    """Decode robot camera ZMQ payload into BGR uint8 ndarray.

    Tries raw first (default per protocols.md, `cameras.*.compressed=false`),
    falls back to blosc2+LZ4. Robot publishes BGR for arducam — see driver.
    Caller passes the expected (H, W, C) shape so a torn payload fails loud
    instead of reshaping into garbage.
    """
    expected = int(np.prod(shape))
    arr: np.ndarray | None = None
    if len(payload) == expected:
        arr = np.frombuffer(payload, dtype=np.uint8).reshape(shape)
    else:
        try:
            import blosc2

            raw = blosc2.decompress(payload)
        except Exception as e:
            raise ValueError(
                f"payload size {len(payload)} != raw {expected} and blosc2 decode failed: {e}"
            ) from e
        if len(raw) != expected:
            raise ValueError(
                f"decompressed payload {len(raw)} != expected {expected}"
            )
        arr = np.frombuffer(raw, dtype=np.uint8).reshape(shape)
    return arr


def _bgr_to_imgmsg(bgr: np.ndarray, header) -> Image:
    """Inverse of _imgmsg_to_bgr — publish a BGR frame as a ROS Image."""
    msg = Image()
    msg.header = header
    msg.height = int(bgr.shape[0])
    msg.width = int(bgr.shape[1])
    msg.encoding = "bgr8"
    msg.is_bigendian = 0
    msg.step = int(bgr.shape[1]) * 3
    msg.data = bgr.tobytes()
    return msg

logger = logging.getLogger("yolo_world_node")

# Sensor QoS matches RealSense / sensors_bridge color stream.
SENSOR_QOS = QoSProfile(
    reliability=ReliabilityPolicy.BEST_EFFORT,
    history=HistoryPolicy.KEEP_LAST,
    depth=2,
)


class YoloWorldNode(Node):
    def __init__(self, args: argparse.Namespace) -> None:
        super().__init__("yolo_world")

        # ---- Parameters (overridable at launch + at runtime) -------------
        self.declare_parameter(
            "zmq_addr",
            args.zmq_addr,
            ParameterDescriptor(description="Robot camera ZMQ PUB endpoint (tcp://host:port)."),
        )
        self.declare_parameter(
            "zmq_topic",
            args.zmq_topic,
            ParameterDescriptor(
                description="ZMQ topic filter (empty for arducam:6000; 'rgb' for d405:6002)."
            ),
        )
        self.declare_parameter(
            "frame_shape",
            args.frame_shape,
            ParameterDescriptor(description="Frame H,W,C — arducam=720,1280,3, d405=480,640,3."),
        )
        self.declare_parameter(
            "frame_id",
            args.frame_id,
            ParameterDescriptor(description="ROS header frame_id stamped on detections."),
        )
        self.declare_parameter(
            "rotate",
            args.rotate,
            ParameterDescriptor(description="Pre-rotate frame: none, cw, ccw, 180. Arducam on Stretch3 mounts 90° → use ccw."),
        )
        self.declare_parameter(
            "classes",
            args.classes,
            ParameterDescriptor(description="Comma-or-list-separated YOLO-World class prompts."),
        )
        self.declare_parameter(
            "model_path",
            args.model_path,
            ParameterDescriptor(description="Path to a YOLO-World .pt checkpoint."),
        )
        self.declare_parameter(
            "conf_threshold",
            args.conf,
            ParameterDescriptor(description="Min detection confidence in [0,1]."),
        )
        self.declare_parameter(
            "iou_threshold",
            args.iou,
            ParameterDescriptor(description="Per-class NMS IoU threshold."),
        )
        self.declare_parameter(
            "publish_annotated",
            args.publish_annotated,
            ParameterDescriptor(description="Publish /detections/annotated image."),
        )

        self._inference_lock = threading.Lock()
        self._latest_frame: Optional[tuple[np.ndarray, int]] = None
        self._image_lock = threading.Lock()
        self._fps_window: list[float] = []
        self._frame_shape = self._parse_frame_shape(self._param_frame_shape())

        # ---- Model ---------------------------------------------------------
        self._model = None
        self._load_model()
        self._apply_classes(self._param_classes())

        # ---- Pubs ---------------------------------------------------------
        self._det_pub = self.create_publisher(Detection2DArray, "/detections", 10)
        self._annot_pub = self.create_publisher(Image, "/detections/annotated", 1)

        # ---- ZMQ SUB thread -----------------------------------------------
        self._zmq_stop = threading.Event()
        self._zmq_thread = threading.Thread(
            target=self._zmq_loop, name="yolo-zmq-sub", daemon=True
        )
        self._zmq_thread.start()

        # Process at a steady cadence rather than per-callback so inference
        # can fall behind without queuing memory-grow.
        self._timer = self.create_timer(1.0 / 30.0, self._tick)

        # Hot-reload classes when the parameter is changed at runtime.
        self.add_on_set_parameters_callback(self._on_param_change)

        self.get_logger().info(
            f"yolo_world ready: zmq={self._param_zmq_addr()} "
            f"topic={self._param_zmq_topic()!r} shape={self._frame_shape} "
            f"classes={self._param_classes()} model={self._param_model_path()}"
        )

    # ----- Parameter helpers ----------------------------------------------

    def _param_zmq_addr(self) -> str:
        return str(self.get_parameter("zmq_addr").value)

    def _param_zmq_topic(self) -> str:
        return str(self.get_parameter("zmq_topic").value)

    def _param_frame_shape(self) -> str:
        return str(self.get_parameter("frame_shape").value)

    def _param_frame_id(self) -> str:
        return str(self.get_parameter("frame_id").value)

    def _param_rotate(self) -> str:
        return str(self.get_parameter("rotate").value).lower()

    @staticmethod
    def _rotate_frame(frame: np.ndarray, mode: str) -> np.ndarray:
        if mode == "cw":
            return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
        if mode == "ccw":
            return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
        if mode == "180":
            return cv2.rotate(frame, cv2.ROTATE_180)
        return frame

    @staticmethod
    def _parse_frame_shape(s: str) -> tuple[int, int, int]:
        parts = [int(x) for x in s.split(",") if x.strip()]
        if len(parts) != 3:
            raise ValueError(f"frame_shape must be H,W,C — got {s!r}")
        return parts[0], parts[1], parts[2]

    def _param_classes(self) -> list[str]:
        v = self.get_parameter("classes").value
        if isinstance(v, list):
            return [str(c).strip() for c in v if str(c).strip()]
        return [c.strip() for c in str(v).split(",") if c.strip()]

    def _param_model_path(self) -> str:
        return str(self.get_parameter("model_path").value)

    def _param_conf(self) -> float:
        return float(self.get_parameter("conf_threshold").value)

    def _param_iou(self) -> float:
        return float(self.get_parameter("iou_threshold").value)

    def _param_publish_annotated(self) -> bool:
        return bool(self.get_parameter("publish_annotated").value)

    # ----- Model lifecycle ------------------------------------------------

    def _load_model(self) -> None:
        from ultralytics import YOLOWorld

        path = self._param_model_path()
        if not Path(path).exists():
            self.get_logger().warning(
                f"Model {path!r} not found; ultralytics will download it on first call."
            )
        self._model = YOLOWorld(path)
        self.get_logger().info(f"Loaded YOLOWorld model from {path}")

    def _apply_classes(self, classes: list[str]) -> None:
        if not classes:
            self.get_logger().warning("Empty class list — detector will produce no output.")
            return
        # set_classes() bakes the prompt vocabulary into the model; subsequent
        # forward passes are no slower than vanilla YOLOv8s.
        with self._inference_lock:
            self._model.set_classes(classes)
        self.get_logger().info(f"Vocabulary set: {classes}")

    def _on_param_change(self, params) -> SetParametersResult:
        # Hot-reload only the parameters that don't need a subscriber rewire.
        for p in params:
            if p.name == "classes":
                new_classes = (
                    [str(c).strip() for c in p.value]
                    if isinstance(p.value, list)
                    else [c.strip() for c in str(p.value).split(",") if c.strip()]
                )
                self._apply_classes(new_classes)
            elif p.name in ("zmq_addr", "zmq_topic", "frame_shape", "frame_id"):
                self.get_logger().info(
                    f"{p.name} param change requires restart to take effect."
                )
        return SetParametersResult(successful=True)

    # ----- ZMQ ingest -----------------------------------------------------

    def _zmq_loop(self) -> None:
        addr = self._param_zmq_addr()
        topic = self._param_zmq_topic()
        ctx = zmq.Context.instance()
        sock = ctx.socket(zmq.SUB)
        # CONFLATE is unsafe with multipart payloads (libzmq fq.cpp asserts);
        # drop-oldest behaviour comes from RCVHWM=1 + we read in a tight loop.
        sock.setsockopt(zmq.RCVHWM, 1)
        sock.setsockopt(zmq.RCVTIMEO, 1000)
        sock.connect(addr)
        sock.setsockopt(zmq.SUBSCRIBE, topic.encode())
        self.get_logger().info(f"ZMQ SUB connected: {addr} topic={topic!r}")
        while not self._zmq_stop.is_set():
            try:
                parts = sock.recv_multipart()
            except zmq.Again:
                continue
            except Exception as e:
                self.get_logger().error(f"ZMQ recv failed: {e}")
                time.sleep(0.5)
                continue
            # arducam: [ts(8), payload]; d405: [topic, ts(8), payload]
            if len(parts) == 2:
                ts_bytes, payload = parts
            elif len(parts) == 3:
                _, ts_bytes, payload = parts
            else:
                self.get_logger().warning(f"unexpected multipart len={len(parts)}")
                continue
            try:
                frame = _decode_zmq_payload(payload, self._frame_shape)
            except Exception as e:
                self.get_logger().warning(f"frame decode failed: {e}")
                continue
            try:
                ts_ns = int.from_bytes(ts_bytes, "big") if len(ts_bytes) == 8 else time.time_ns()
            except Exception:
                ts_ns = time.time_ns()
            with self._image_lock:
                self._latest_frame = (frame, ts_ns)
        sock.close(0)

    def _make_header(self, ts_ns: int) -> Header:
        h = Header()
        h.frame_id = self._param_frame_id()
        h.stamp.sec = ts_ns // 1_000_000_000
        h.stamp.nanosec = ts_ns % 1_000_000_000
        return h

    # ----- Image pipeline -------------------------------------------------

    def _tick(self) -> None:
        with self._image_lock:
            item = self._latest_frame
            self._latest_frame = None
        if item is None:
            return
        frame, ts_ns = item
        frame = self._rotate_frame(frame, self._param_rotate())
        header = self._make_header(ts_ns)

        t0 = time.monotonic()
        with self._inference_lock:
            try:
                results = self._model.predict(
                    source=frame,
                    conf=self._param_conf(),
                    iou=self._param_iou(),
                    verbose=False,
                )
            except Exception as e:
                self.get_logger().error(f"YOLO predict raised: {e}")
                return
        dt = time.monotonic() - t0
        self._fps_window.append(dt)
        if len(self._fps_window) > 30:
            self._fps_window = self._fps_window[-30:]

        det_array = self._results_to_msg(results, header)
        self._det_pub.publish(det_array)

        if self._param_publish_annotated():
            annot = self._annotate(frame, results)
            self._annot_pub.publish(_bgr_to_imgmsg(annot, header))

        if len(self._fps_window) >= 10:
            avg = sum(self._fps_window) / len(self._fps_window)
            self.get_logger().debug(
                f"yolo {1.0 / max(avg, 1e-6):.1f} FPS ({avg * 1000:.1f}ms/frame)"
            )

    def _results_to_msg(self, results, header) -> Detection2DArray:
        out = Detection2DArray()
        out.header = header
        # Ultralytics returns a Results object per image. We feed one image
        # per call so results[0] is what we need.
        if not results:
            return out
        r = results[0]
        names = r.names  # int → str
        boxes = r.boxes
        if boxes is None or len(boxes) == 0:
            return out
        xyxy = boxes.xyxy.cpu().numpy()  # (N, 4)
        conf = boxes.conf.cpu().numpy()  # (N,)
        cls = boxes.cls.cpu().numpy().astype(int)  # (N,)
        for (x1, y1, x2, y2), c, k in zip(xyxy, conf, cls):
            d = Detection2D()
            d.header = header
            bb = BoundingBox2D()
            bb.center.position.x = float((x1 + x2) / 2.0)
            bb.center.position.y = float((y1 + y2) / 2.0)
            bb.center.theta = 0.0
            bb.size_x = float(x2 - x1)
            bb.size_y = float(y2 - y1)
            d.bbox = bb
            hyp = ObjectHypothesisWithPose()
            hyp.hypothesis.class_id = str(names.get(int(k), str(int(k))))
            hyp.hypothesis.score = float(c)
            d.results.append(hyp)
            out.detections.append(d)
        return out

    def _annotate(self, frame: np.ndarray, results) -> np.ndarray:
        if not results:
            return frame
        r = results[0]
        boxes = r.boxes
        if boxes is None or len(boxes) == 0:
            return frame
        names = r.names
        xyxy = boxes.xyxy.cpu().numpy()
        conf = boxes.conf.cpu().numpy()
        cls = boxes.cls.cpu().numpy().astype(int)
        out = frame.copy()
        for (x1, y1, x2, y2), c, k in zip(xyxy, conf, cls):
            label = f"{names.get(int(k), int(k))} {c:.2f}"
            cv2.rectangle(
                out,
                (int(x1), int(y1)),
                (int(x2), int(y2)),
                (255, 200, 50),
                2,
            )
            cv2.putText(
                out,
                label,
                (int(x1), max(0, int(y1) - 6)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (255, 200, 50),
                1,
                cv2.LINE_AA,
            )
        return out


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="YOLO-World ROS 2 detection node.")
    p.add_argument(
        "--zmq-addr",
        default=os.environ.get("ROBOT_CAM_ZMQ", "tcp://192.168.1.38:6000"),
        help="Robot camera ZMQ PUB (arducam=6000, d405=6002).",
    )
    p.add_argument("--zmq-topic", default="", help="Empty for arducam, 'rgb' for d405.")
    p.add_argument(
        "--frame-shape",
        default="720,1280,3",
        help="H,W,C — arducam OV9782=720,1280,3, d405=480,640,3.",
    )
    p.add_argument(
        "--frame-id",
        default="camera_color_optical_frame",
        help="ROS header frame_id stamped on detections.",
    )
    p.add_argument(
        "--rotate",
        default="ccw",
        choices=["none", "cw", "ccw", "180"],
        help="Pre-rotate frame to upright. Stretch3 arducam mounts 90° → ccw.",
    )
    p.add_argument(
        "--classes",
        default="medicine bottle,patient,human,chair,door,table",
    )
    p.add_argument("--model-path", default=os.environ.get("YOLO_WORLD_MODEL", "yolov8s-worldv2.pt"))
    p.add_argument("--conf", type=float, default=0.25)
    p.add_argument("--iou", type=float, default=0.45)
    p.add_argument("--no-annotated", dest="publish_annotated", action="store_false")
    p.set_defaults(publish_annotated=True)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    rclpy.init()
    node = YoloWorldNode(args)
    try:
        rclpy.spin(node)
    finally:
        node._zmq_stop.set()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
