#!/usr/bin/env python3
"""ZMQ → ROS 2 sensor bridge for the lab nvblox/Nav2 stack.

Subscribes to the four PUB endpoints exposed by the stretch3-zmq driver
(depth, color, camera_info, odom_tf — see docs/lab-client-guide.md) and
republishes them on the lab box's local DDS as standard sensor_msgs +
a tf2 odom→base_link transform. Once running, nvblox and Nav2 see
ordinary ROS topics and have no idea ZMQ is in the loop.

Each ZMQ stream is drained in its own daemon thread to keep the rclpy
executor free; the latest frame is parked in a thread-safe slot and the
ROS publisher reads from there in a Timer callback. CONFLATE on both
ends means we never queue stale frames.

Run inside an environment with rclpy + cv2 + msgpack + pyzmq:
    source /opt/ros/humble/setup.bash
    python3 backend/nav_bridge/sensors_bridge.py --robot 192.168.1.38
"""
from __future__ import annotations

import argparse
import math
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Callable, Optional

import cv2
import msgpack
import numpy as np
import rclpy
import zmq
from geometry_msgs.msg import TransformStamped
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from sensor_msgs.msg import CameraInfo, Image
from std_msgs.msg import Header
from tf2_ros import TransformBroadcaster

DEFAULT_ROBOT_HOST = "192.168.1.38"
DEFAULT_PORTS = {
    "depth": 6010,
    "color": 6011,
    "camera_info": 6012,
    "odom_tf": 6013,
}

# Sensor publishers use BEST_EFFORT to match nvblox/RealSense convention.
SENSOR_QOS = QoSProfile(
    reliability=ReliabilityPolicy.BEST_EFFORT,
    history=HistoryPolicy.KEEP_LAST,
    depth=2,
)


@dataclass
class LatestFrame:
    """Thread-safe latest-frame slot. Producer overwrites; consumer drains."""

    _lock: threading.Lock = field(default_factory=threading.Lock)
    _msg: Optional[dict] = None

    def put(self, msg: dict) -> None:
        with self._lock:
            self._msg = msg

    def take(self) -> Optional[dict]:
        with self._lock:
            m, self._msg = self._msg, None
            return m


def _make_sub(ctx: zmq.Context, addr: str) -> zmq.Socket:
    s = ctx.socket(zmq.SUB)
    s.setsockopt(zmq.RCVHWM, 2)
    s.setsockopt(zmq.CONFLATE, 1)
    s.setsockopt(zmq.LINGER, 0)
    s.setsockopt(zmq.SUBSCRIBE, b"")
    s.connect(addr)
    return s


def _zmq_drain_loop(
    addr: str,
    slot: "LatestFrame",
    label: str,
    log_info: Callable[[str], None],
    log_err: Callable[[str], None],
) -> None:
    """Drain ZMQ → slot, swallowing exceptions so the thread can't die silently.

    Originally this only caught ZMQError, which meant any msgpack format error
    (or any bug in `slot.put`) would kill the daemon thread without a single
    log line — the rclpy timer would then spin forever on an always-empty
    slot, and the corresponding /camera/* topic would publish 0 Hz with no
    diagnostic to point at the cause. Now every iteration is wrapped, errors
    land on the rclpy logger (so they show up in /rosout and the launch
    terminal), and the loop continues.
    """
    ctx = zmq.Context.instance()
    sock = _make_sub(ctx, addr)
    log_info(f"{label}: drain thread connected to {addr}")
    count = 0
    window_start = time.monotonic()
    while True:
        try:
            payload = sock.recv()
            slot.put(msgpack.unpackb(payload, raw=False))
            count += 1
            now = time.monotonic()
            elapsed = now - window_start
            if elapsed >= 5.0:
                log_info(
                    f"{label}: drain={count} frames in {elapsed:.1f}s "
                    f"({count / elapsed:.2f} Hz)"
                )
                count = 0
                window_start = now
        except zmq.error.ZMQError as e:
            log_err(f"{label}: zmq error: {e!r}; continuing")
        except Exception as e:
            # The bug fix: previously this would kill the thread. Now the
            # error is logged with a stack trace and the loop survives.
            log_err(
                f"{label}: drain caught {type(e).__name__}: {e!r}; "
                f"continuing\n{traceback.format_exc()}"
            )


def _yaw_to_quat(theta: float) -> tuple[float, float, float, float]:
    """Yaw → quaternion (qx, qy, qz, qw). 2D pose, roll=pitch=0."""
    half = theta / 2.0
    return (0.0, 0.0, math.sin(half), math.cos(half))


class SensorsBridge(Node):
    def __init__(self, robot_host: str, ports: dict[str, int]) -> None:
        super().__init__("nvblox_sensors_bridge")

        self._slots = {k: LatestFrame() for k in ports}
        self._frame_id_depth = "camera_depth_optical_frame"
        self._frame_id_color = "camera_color_optical_frame"
        self._frame_id_base = "base_link"
        self._frame_id_odom = "odom"

        # Cached intrinsics — updated whenever a new camera_info arrives over
        # ZMQ. We re-publish camera_info ALONGSIDE every depth/color frame
        # using the image's timestamp, because nvblox uses image_exact_sync
        # (header.stamp must match exactly between depth and depth/camera_info,
        # likewise for color). Publishing the two streams on independent
        # timers leaves the sync filter without a single matching pair.
        self._intrinsics_lock = threading.Lock()
        self._intrinsics: Optional[dict] = None

        # Publishers
        self.pub_depth = self.create_publisher(
            Image, "/camera/depth/image_rect_raw", SENSOR_QOS
        )
        self.pub_color = self.create_publisher(
            Image, "/camera/color/image_raw", SENSOR_QOS
        )
        self.pub_depth_info = self.create_publisher(
            CameraInfo, "/camera/depth/camera_info", SENSOR_QOS
        )
        self.pub_color_info = self.create_publisher(
            CameraInfo, "/camera/color/camera_info", SENSOR_QOS
        )
        self.tf_bcast = TransformBroadcaster(self)

        # /odom is required by Nav2's controller_server (DWB needs current
        # velocity for trajectory rollout). The robot's odom_tf wire payload
        # only carries pose, so we differentiate consecutive samples to fill
        # in linear/angular velocity. Subscribers: controller_server, bt_navigator.
        self.pub_odom = self.create_publisher(Odometry, "/odom", SENSOR_QOS)
        self._prev_odom_sample: tuple[int, float, float, float] | None = None  # (ts_ns, x, y, theta)

        # Per-stream republish counters — paired with the drain-side counter
        # in _zmq_drain_loop. If drain is high but publish is low we know
        # the take/publish path is the issue; vice-versa points at ZMQ.
        # Note: `camera_info` is intentionally not in this dict because its
        # publishing rides along with depth and color (sister-publish using
        # the image's timestamp) — its rate equals depth_pub + color_pub
        # whenever intrinsics are cached. Standalone counter would be 0
        # forever and read as a false alarm.
        self._publish_counters: dict[str, int] = {
            k: 0 for k in ("depth", "color", "odom_tf")
        }
        self._publish_window_start = time.monotonic()
        self._publish_log_lock = threading.Lock()

        # Spawn one drain thread per ZMQ topic. The drain thread takes
        # rclpy log callables (info/err) so its messages land in /rosout
        # and the launch terminal — diagnosable from outside.
        logger = self.get_logger()
        for label, port in ports.items():
            addr = f"tcp://{robot_host}:{port}"
            t = threading.Thread(
                target=_zmq_drain_loop,
                args=(addr, self._slots[label], label,
                      lambda m, _l=logger: _l.info(m),
                      lambda m, _l=logger: _l.error(m)),
                name=f"zmq-{label}",
                daemon=True,
            )
            t.start()
            self.get_logger().info(f"subscribed {label} on {addr}")

        # Republish timers — generous rates so we don't bottleneck.
        # camera_info has no dedicated timer: it rides along with each
        # depth/color frame, sharing the image's timestamp (see comment
        # above on _intrinsics — exact-sync requirement inside nvblox).
        # We DO drain the camera_info ZMQ slot periodically to refresh
        # the cached intrinsics, but those updates only matter when
        # intrinsics change (they almost never do mid-session).
        self.create_timer(1.0 / 30.0, self._republish_depth)
        self.create_timer(1.0 / 30.0, self._republish_color)
        self.create_timer(1.0 / 2.0, self._refresh_intrinsics)
        self.create_timer(1.0 / 100.0, self._republish_odom_tf)

    # ---- Helpers ----

    def _stamp_from_ts_ns(self, ts_ns: int) -> Header:
        h = Header()
        h.stamp.sec = ts_ns // 1_000_000_000
        h.stamp.nanosec = ts_ns % 1_000_000_000
        return h

    def _bump_publish_counter(self, label: str) -> None:
        """Per-republish hit counter; logs aggregated rates every 5s."""
        with self._publish_log_lock:
            self._publish_counters[label] += 1
            now = time.monotonic()
            elapsed = now - self._publish_window_start
            if elapsed >= 5.0:
                summary = ", ".join(
                    f"{k}={v} ({v / elapsed:.2f} Hz)"
                    for k, v in self._publish_counters.items()
                )
                self.get_logger().info(f"publish: {summary}")
                for k in self._publish_counters:
                    self._publish_counters[k] = 0
                self._publish_window_start = now

    # ---- Republish callbacks ----

    def _refresh_intrinsics(self) -> None:
        m = self._slots["camera_info"].take()
        if not m:
            return
        with self._intrinsics_lock:
            self._intrinsics = m

    def _build_camera_info(self, ts_ns: int, frame_id: str) -> Optional[CameraInfo]:
        with self._intrinsics_lock:
            m = self._intrinsics
        if not m:
            return None
        info = CameraInfo()
        info.header = self._stamp_from_ts_ns(ts_ns)
        info.header.frame_id = frame_id
        info.height = m["h"]
        info.width = m["w"]
        info.distortion_model = m.get("distortion_model", "plumb_bob")
        info.d = list(m["D"])
        info.k = list(m["K"])
        info.r = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
        info.p = [m["K"][0], 0.0, m["K"][2], 0.0,
                  0.0, m["K"][4], m["K"][5], 0.0,
                  0.0, 0.0, 1.0, 0.0]
        return info

    def _republish_depth(self) -> None:
        m = self._slots["depth"].take()
        if not m:
            return
        ts_ns = int(m["ts_ns"])
        msg = Image()
        msg.header = self._stamp_from_ts_ns(ts_ns)
        msg.header.frame_id = self._frame_id_depth
        msg.height = m["h"]
        msg.width = m["w"]
        msg.encoding = "16UC1"
        msg.is_bigendian = 0
        msg.step = m["w"] * 2
        msg.data = m["data"]
        self.pub_depth.publish(msg)
        self._bump_publish_counter("depth")

        # Sister camera_info with the SAME timestamp — required for nvblox's
        # image_exact_sync. Issue a paired publish on every depth frame so
        # the sync filter inside nvblox always finds a match.
        info = self._build_camera_info(ts_ns, self._frame_id_depth)
        if info is not None:
            self.pub_depth_info.publish(info)

    def _republish_color(self) -> None:
        m = self._slots["color"].take()
        if not m:
            return
        # Wire contract: encoding="rgb8" but data is JPEG bytes (see lab-client-guide §4.2).
        bgr = cv2.imdecode(np.frombuffer(m["data"], np.uint8), cv2.IMREAD_COLOR)
        if bgr is None:
            self.get_logger().warning("color: jpeg decode failed", throttle_duration_sec=5.0)
            return
        # nvblox's color integrator only accepts "rgb8" — it rejects "bgr8"
        # with `Invalid color image encoding`. cv2.imdecode returns BGR, so
        # convert to RGB before publishing.
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        ts_ns = int(m["ts_ns"])
        msg = Image()
        msg.header = self._stamp_from_ts_ns(ts_ns)
        msg.header.frame_id = self._frame_id_color
        msg.height = rgb.shape[0]
        msg.width = rgb.shape[1]
        msg.encoding = "rgb8"
        msg.is_bigendian = 0
        msg.step = rgb.shape[1] * 3
        msg.data = rgb.tobytes()
        self.pub_color.publish(msg)
        self._bump_publish_counter("color")

        # Sister camera_info with the same timestamp (see _republish_depth).
        # Color intrinsics are placeholder-identical to depth until a one-time
        # calibration is run — refine then.
        info = self._build_camera_info(ts_ns, self._frame_id_color)
        if info is not None:
            self.pub_color_info.publish(info)

    def _republish_odom_tf(self) -> None:
        m = self._slots["odom_tf"].take()
        if not m:
            return
        ts_ns = int(m["ts_ns"])
        x = float(m["x"])
        y = float(m["y"])
        theta = float(m["theta"])

        # /tf — consumed by Nav2's lookup of robot pose
        t = TransformStamped()
        t.header = self._stamp_from_ts_ns(ts_ns)
        t.header.frame_id = self._frame_id_odom
        t.child_frame_id = self._frame_id_base
        t.transform.translation.x = x
        t.transform.translation.y = y
        t.transform.translation.z = 0.0
        qx, qy, qz, qw = _yaw_to_quat(theta)
        t.transform.rotation.x = qx
        t.transform.rotation.y = qy
        t.transform.rotation.z = qz
        t.transform.rotation.w = qw
        self.tf_bcast.sendTransform(t)

        # /odom — DWB controller subscribes here for current velocity. The
        # wire payload is pose-only; differentiate consecutive samples in the
        # robot's body frame to recover (vx, vy=0, wz). Skip the first sample
        # since there's nothing to differentiate against yet.
        odom = Odometry()
        odom.header = self._stamp_from_ts_ns(ts_ns)
        odom.header.frame_id = self._frame_id_odom
        odom.child_frame_id = self._frame_id_base
        odom.pose.pose.position.x = x
        odom.pose.pose.position.y = y
        odom.pose.pose.orientation.x = qx
        odom.pose.pose.orientation.y = qy
        odom.pose.pose.orientation.z = qz
        odom.pose.pose.orientation.w = qw

        prev = self._prev_odom_sample
        if prev is not None:
            ptn, px, py, ptheta = prev
            dt = (ts_ns - ptn) / 1e9
            if 1e-3 < dt < 0.5:
                # World-frame deltas → body-frame linear velocity
                dx = x - px
                dy = y - py
                # Use prev heading to project the delta onto base_link's x
                cprev = math.cos(ptheta)
                sprev = math.sin(ptheta)
                vx_body = (cprev * dx + sprev * dy) / dt
                # Normalize yaw delta to [-pi, pi]
                dtheta = (theta - ptheta + math.pi) % (2 * math.pi) - math.pi
                wz = dtheta / dt
                odom.twist.twist.linear.x = vx_body
                odom.twist.twist.angular.z = wz
        self._prev_odom_sample = (ts_ns, x, y, theta)
        self.pub_odom.publish(odom)
        self._bump_publish_counter("odom_tf")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--robot", default=DEFAULT_ROBOT_HOST,
                        help=f"robot hostname/IP (default: {DEFAULT_ROBOT_HOST})")
    args, ros_args = parser.parse_known_args()

    rclpy.init(args=ros_args)
    node = SensorsBridge(args.robot, DEFAULT_PORTS)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
