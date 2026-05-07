#!/usr/bin/env python3
"""ZMQ REP nav_service — backend proxy ↔ Nav2 BasicNavigator.

Listens on tcp://*:5560, accepts msgpack `{target, timeout_s, request_id}`,
dispatches to nav2_simple_commander.BasicNavigator.goToPose(), and replies
with `{status, reason, final_pose, elapsed_s}` once the task terminates.

API contract: docs/superpowers/specs/2026-05-05-nvblox-navigation-design.md
                §"Backend ↔ nav_service (ZMQ REQ/REP, localhost)".

Phase 1 detail: when nav2_simple_commander is not yet installed (e.g.
because the user is bringing the stack up incrementally), the service
still starts and replies with status=BAD_TARGET so the backend proxy
surfaces a clean error rather than hanging.

Run:
    source /opt/ros/humble/setup.bash
    python3 backend/nav_bridge/nav_service.py
"""
from __future__ import annotations

import argparse
import math
import threading
import time

import msgpack
import numpy as np
import rclpy
import zmq
from geometry_msgs.msg import PoseStamped, TransformStamped
from rclpy.node import Node
from tf2_ros import TransformBroadcaster

DEFAULT_BIND_PORT = 5560
DEFAULT_INITIAL_POSE_PORT = 5561
DEFAULT_TIMEOUT_S = 60.0
DEFAULT_ROBOT_HOST = "192.168.1.38"
DEFAULT_ROBOT_ODOM_PORT = 6013

# Lifecycle nodes whose ACTIVE state means "Nav2 ready". We do NOT use AMCL —
# external (room-camera) localization publishes map→base_link directly. So we
# wait on the navigator + the map_server only.
NAV2_NAVIGATOR_NODE = "bt_navigator"
NAV2_LOCALIZER_NODE = "map_server"


def _yaw_to_pose(x: float, y: float, theta: float, frame_id: str, stamp) -> PoseStamped:
    p = PoseStamped()
    p.header.stamp = stamp
    p.header.frame_id = frame_id
    p.pose.position.x = float(x)
    p.pose.position.y = float(y)
    p.pose.position.z = 0.0
    half = float(theta) / 2.0
    p.pose.orientation.z = math.sin(half)
    p.pose.orientation.w = math.cos(half)
    return p


def _se2_matrix(x: float, y: float, theta: float) -> np.ndarray:
    c, s = math.cos(theta), math.sin(theta)
    return np.array([[c, -s, x], [s, c, y], [0.0, 0.0, 1.0]])


def _se2_unpack(M: np.ndarray) -> tuple[float, float, float]:
    return float(M[0, 2]), float(M[1, 2]), float(math.atan2(M[1, 0], M[0, 0]))


def _bind_or_die(sock, addr: str, label: str, logger) -> None:
    """Bind a ZMQ socket; on EADDRINUSE log a precise remediation and re-raise.

    Without this wrapper, `nav.launch.py`'s ExecuteProcess sees nav_service
    exit silently on a port conflict and the rest of the stack stays up,
    which is what allowed the orphan-publisher mess this is fixing. With
    this wrapper plus on_exit=Shutdown() in nav.launch.py, a port conflict
    is loud AND tears the whole launch down so the next run_nav.sh starts
    clean.
    """
    try:
        sock.bind(addr)
    except zmq.error.ZMQError as e:
        if e.errno == 98:  # EADDRINUSE
            logger.fatal(
                f"nav_service: cannot bind {label} on {addr}: address in use. "
                f"This means an orphan nav_service from a prior crashed launch "
                f"is still alive. Stop the current launch and re-run via "
                f"backend/nav_bridge/run_nav.sh — it cleans up before launching."
            )
        else:
            logger.fatal(f"nav_service: cannot bind {label} on {addr}: {e!r}")
        raise


class NavServiceNode(Node):
    def __init__(self, bind_port: int, initial_pose_port: int,
                 robot_host: str, robot_odom_port: int) -> None:
        super().__init__("nvblox_nav_service")

        self._navigator = None
        self._nav_ready = False
        try:
            from nav2_simple_commander.robot_navigator import BasicNavigator  # noqa
            self._navigator = BasicNavigator()
            # waitUntilNav2Active blocks; do it in a worker so the REP loop
            # can already accept (and reject with BAD_TARGET) requests.
            threading.Thread(
                target=self._wait_for_nav2, name="nav2-warmup", daemon=True
            ).start()
        except ImportError:
            self.get_logger().warning(
                "nav2_simple_commander not installed; service will reply BAD_TARGET. "
                "Install with: sudo apt install ros-humble-nav2-simple-commander"
            )

        # Track latest odom→base_link from robot's ZMQ. set_initial_pose
        # uses this to compute map→odom = map→base * inv(odom→base).
        self._latest_odom: tuple[float, float, float] = (0.0, 0.0, 0.0)
        self._odom_lock = threading.Lock()
        self._odom_addr = f"tcp://{robot_host}:{robot_odom_port}"
        threading.Thread(
            target=self._odom_drain_loop, name="zmq-odom-sub", daemon=True
        ).start()

        # Continuously publish map→odom on /tf at 10 Hz. Default to identity
        # until set_initial_pose updates _map_to_odom. Using a dynamic /tf
        # broadcaster (not /tf_static) means "latest wins" trivially — no
        # latched-message race with another publisher. Runs in a thread
        # rather than an rclpy Timer because we never spin this Node
        # (BasicNavigator spins its own internal node — see main()).
        self._tf_bcast = TransformBroadcaster(self)
        self._map_to_odom: tuple[float, float, float] = (0.0, 0.0, 0.0)
        self._map_to_odom_lock = threading.Lock()
        threading.Thread(
            target=self._broadcast_loop, name="map-to-odom-bcast", daemon=True
        ).start()

        ctx = zmq.Context.instance()
        self.rep = ctx.socket(zmq.REP)
        self.rep.setsockopt(zmq.LINGER, 0)
        bind_addr = f"tcp://*:{bind_port}"
        _bind_or_die(self.rep, bind_addr, "goto", self.get_logger())
        self.get_logger().info(f"nav_service goto bound on {bind_addr}")

        # Separate REP socket for set_initial_pose so a long-running goto
        # doesn't block pose updates from the dashboard.
        self.rep_pose = ctx.socket(zmq.REP)
        self.rep_pose.setsockopt(zmq.LINGER, 0)
        pose_bind_addr = f"tcp://*:{initial_pose_port}"
        _bind_or_die(self.rep_pose, pose_bind_addr, "initial_pose", self.get_logger())
        self.get_logger().info(f"nav_service initial_pose bound on {pose_bind_addr}")

        threading.Thread(target=self._serve_loop, name="zmq-rep-goto",
                         daemon=True).start()
        threading.Thread(target=self._serve_pose_loop, name="zmq-rep-pose",
                         daemon=True).start()

    def _odom_drain_loop(self) -> None:
        ctx = zmq.Context.instance()
        sock = ctx.socket(zmq.SUB)
        sock.setsockopt(zmq.RCVHWM, 2)
        sock.setsockopt(zmq.CONFLATE, 1)
        sock.setsockopt(zmq.LINGER, 0)
        sock.setsockopt(zmq.SUBSCRIBE, b"")
        sock.connect(self._odom_addr)
        while True:
            try:
                payload = sock.recv()
                m = msgpack.unpackb(payload, raw=False)
                with self._odom_lock:
                    self._latest_odom = (float(m["x"]), float(m["y"]),
                                         float(m["theta"]))
            except zmq.error.ZMQError as e:
                self.get_logger().warning(f"odom sub error: {e}")
                time.sleep(0.5)

    def _wait_for_nav2(self) -> None:
        # Default waitUntilNav2Active() polls amcl/get_state — but we use
        # external (room-camera) localization, no AMCL. Wait on map_server
        # instead, which IS a lifecycle node in our nav.launch.py stack.
        try:
            self._navigator.waitUntilNav2Active(
                navigator=NAV2_NAVIGATOR_NODE,
                localizer=NAV2_LOCALIZER_NODE,
            )
            self._nav_ready = True
            self.get_logger().info(
                f"Nav2 stack active (navigator={NAV2_NAVIGATOR_NODE}, "
                f"localizer={NAV2_LOCALIZER_NODE})"
            )
        except Exception as e:
            self.get_logger().error(f"waitUntilNav2Active failed: {e}")

    def _serve_loop(self) -> None:
        while True:
            try:
                payload = self.rep.recv()
            except zmq.error.ZMQError as e:
                self.get_logger().error(f"recv error: {e}")
                continue
            req = self._safe_unpack(payload)
            reply = self._handle(req)
            self.rep.send(msgpack.packb(reply, use_bin_type=True))

    def _serve_pose_loop(self) -> None:
        while True:
            try:
                payload = self.rep_pose.recv()
            except zmq.error.ZMQError as e:
                self.get_logger().error(f"pose recv error: {e}")
                continue
            req = self._safe_unpack(payload)
            reply = self._handle_set_initial_pose(req)
            self.rep_pose.send(msgpack.packb(reply, use_bin_type=True))

    def _handle_set_initial_pose(self, req: dict) -> dict:
        if "_decode_error" in req:
            return {"ok": False, "reason": f"msgpack decode: {req['_decode_error']}"}
        target = req.get("target")
        if not isinstance(target, list) or len(target) != 3:
            return {"ok": False, "reason": "target must be [x, y, theta]"}

        target_x, target_y, target_theta = (float(v) for v in target)
        with self._odom_lock:
            ox, oy, ot = self._latest_odom

        # map→odom = (map→base_link) * inv(odom→base_link).
        # When robot is at odom-origin (just-started driver), this reduces
        # to map→odom = (target_x, target_y, target_theta).
        m_t_b = _se2_matrix(target_x, target_y, target_theta)
        o_t_b = _se2_matrix(ox, oy, ot)
        m_t_o = m_t_b @ np.linalg.inv(o_t_b)
        mx, my, mt = _se2_unpack(m_t_o)

        with self._map_to_odom_lock:
            self._map_to_odom = (mx, my, mt)

        self.get_logger().info(
            f"set_initial_pose: target=({target_x:.3f}, {target_y:.3f}, "
            f"{target_theta:.3f}) odom→base=({ox:.3f}, {oy:.3f}, {ot:.3f}) "
            f"→ map→odom=({mx:.3f}, {my:.3f}, {mt:.3f})"
        )

        # Trigger a global costmap clear so old footprint inflations from
        # the previous (probably-incorrect) robot pose disappear.
        if self._navigator is not None and self._nav_ready:
            try:
                self._navigator.clearAllCostmaps()
            except Exception as e:
                self.get_logger().warning(f"costmap clear failed: {e}")

        return {"ok": True, "map_to_odom": [mx, my, mt]}

    def _broadcast_loop(self) -> None:
        """Thread loop — broadcast map→odom every 100ms from cached state."""
        while True:
            with self._map_to_odom_lock:
                mx, my, mt = self._map_to_odom
            ts = TransformStamped()
            ts.header.stamp = self.get_clock().now().to_msg()
            ts.header.frame_id = "map"
            ts.child_frame_id = "odom"
            ts.transform.translation.x = mx
            ts.transform.translation.y = my
            ts.transform.translation.z = 0.0
            half = mt / 2.0
            ts.transform.rotation.x = 0.0
            ts.transform.rotation.y = 0.0
            ts.transform.rotation.z = math.sin(half)
            ts.transform.rotation.w = math.cos(half)
            try:
                self._tf_bcast.sendTransform(ts)
            except Exception as e:
                self.get_logger().warning(f"map→odom send failed: {e}")
            time.sleep(0.1)

    @staticmethod
    def _safe_unpack(payload: bytes) -> dict:
        try:
            return msgpack.unpackb(payload, raw=False)
        except Exception as e:
            return {"_decode_error": str(e)}

    def _handle(self, req: dict) -> dict:
        t0 = time.monotonic()
        if "_decode_error" in req:
            return {"status": "BAD_TARGET", "reason": f"msgpack decode: {req['_decode_error']}",
                    "final_pose": None, "elapsed_s": 0.0}

        target = req.get("target")
        if not isinstance(target, list) or len(target) != 3:
            return {"status": "BAD_TARGET",
                    "reason": "target must be [x, y, theta]; named lookup is Phase 2",
                    "final_pose": None, "elapsed_s": 0.0}

        timeout_s = float(req.get("timeout_s", DEFAULT_TIMEOUT_S))
        request_id = req.get("request_id", "")

        if self._navigator is None:
            return {"status": "BAD_TARGET",
                    "reason": "nav2_simple_commander not installed on lab box",
                    "final_pose": None, "elapsed_s": 0.0}
        if not self._nav_ready:
            return {"status": "BAD_TARGET",
                    "reason": "Nav2 stack not yet active; bringup may still be loading",
                    "final_pose": None, "elapsed_s": 0.0}

        try:
            from nav2_simple_commander.robot_navigator import TaskResult
        except ImportError:
            return {"status": "ROBOT_ERROR", "reason": "nav2_simple_commander gone",
                    "final_pose": None, "elapsed_s": 0.0}

        x, y, theta = target
        goal = _yaw_to_pose(x, y, theta, "map", self._navigator.get_clock().now().to_msg())
        self.get_logger().info(
            f"[{request_id[:8]}] goToPose x={x:.3f} y={y:.3f} θ={theta:.3f} "
            f"timeout={timeout_s:.1f}s"
        )
        self._navigator.goToPose(goal)

        deadline = time.monotonic() + timeout_s
        while not self._navigator.isTaskComplete():
            if time.monotonic() > deadline:
                self._navigator.cancelTask()
                return {"status": "TIMEOUT",
                        "reason": f"exceeded {timeout_s:.1f}s",
                        "final_pose": None,
                        "elapsed_s": time.monotonic() - t0}
            time.sleep(0.1)

        result = self._navigator.getResult()
        status_map = {
            TaskResult.SUCCEEDED: "OK",
            TaskResult.CANCELED: "CANCELLED",
            TaskResult.FAILED: "OBSTRUCTED",
        }
        status = status_map.get(result, "ROBOT_ERROR")
        return {
            "status": status,
            "reason": f"Nav2 result: {result}",
            "final_pose": [x, y, theta],   # TODO: query from /tf in a follow-up
            "elapsed_s": time.monotonic() - t0,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_BIND_PORT,
                        help=f"goto REP port (default: {DEFAULT_BIND_PORT})")
    parser.add_argument("--initial-pose-port", type=int,
                        default=DEFAULT_INITIAL_POSE_PORT,
                        help=f"set_initial_pose REP port (default: {DEFAULT_INITIAL_POSE_PORT})")
    parser.add_argument("--robot", default=DEFAULT_ROBOT_HOST,
                        help="robot hostname/IP for odom_tf SUB")
    parser.add_argument("--robot-odom-port", type=int,
                        default=DEFAULT_ROBOT_ODOM_PORT)
    args, ros_args = parser.parse_known_args()

    rclpy.init(args=ros_args)
    node = NavServiceNode(args.port, args.initial_pose_port,
                          args.robot, args.robot_odom_port)
    # Don't rclpy.spin(node) — BasicNavigator spins its own internal node
    # on the same context and a second parallel spin races the executor
    # ("generator already executing"). The Node we created here is just a
    # logger holder + ZMQ REP container; the REP loop runs in its own
    # thread (see _serve_loop), so we just block until SIGINT.
    stop = threading.Event()
    try:
        stop.wait()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        try:
            rclpy.shutdown()
        except Exception:
            pass


if __name__ == "__main__":
    main()
