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
from pathlib import Path

import msgpack
import rclpy
import yaml
import zmq
from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from sensor_msgs.msg import LaserScan

DEFAULT_BIND_PORT = 5560
DEFAULT_INITIAL_POSE_PORT = 5561
DEFAULT_STATUS_PORT = 5562
DEFAULT_TIMEOUT_S = 60.0
DEFAULT_ROBOT_HOST = "192.168.1.38"
DEFAULT_ROBOT_ODOM_PORT = 6013

# Lifecycle nodes whose ACTIVE state means "Nav2 ready". AMCL owns map → odom;
# wait on the navigator + amcl together so we don't accept goals before the
# transform chain is complete.
NAV2_NAVIGATOR_NODE = "bt_navigator"
NAV2_LOCALIZER_NODE = "amcl"


def _quat_to_yaw(q) -> float:
    """Extract yaw (rotation about z) from a geometry_msgs/Quaternion.
    Assumes roll/pitch are zero, which holds for a differential-drive
    base in the map frame."""
    siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
    cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny_cosp, cosy_cosp)


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


def _load_home_pose(config_dir: Path) -> dict | None:
    """Read backend/nav_bridge/config/home_pose.yaml. Returns the inner
    home_pose dict (with x, y, theta, odom_epsilon_xy, odom_epsilon_theta,
    frame_id) or None if the file is missing or malformed."""
    p = config_dir / "home_pose.yaml"
    if not p.exists():
        return None
    try:
        data = yaml.safe_load(p.read_text())
    except yaml.YAMLError:
        return None
    hp = (data or {}).get("home_pose")
    if not isinstance(hp, dict):
        return None
    required = ("x", "y", "theta", "odom_epsilon_xy", "odom_epsilon_theta")
    if not all(k in hp for k in required):
        return None
    return hp


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
                 status_port: int, robot_host: str,
                 robot_odom_port: int) -> None:
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

        # Track latest odom→base_link from robot's ZMQ. Read by the
        # home-pose auto-seed (to confirm odom ≈ origin at startup) and
        # available for future watchdogs.
        self._latest_odom: tuple[float, float, float] | None = None
        self._odom_lock = threading.Lock()
        self._odom_addr = f"tcp://{robot_host}:{robot_odom_port}"
        threading.Thread(
            target=self._odom_drain_loop, name="zmq-odom-sub", daemon=True
        ).start()

        # map → odom is now published by AMCL. nav_service no longer
        # broadcasts a placeholder identity transform; set_initial_pose
        # forwards to AMCL's /initialpose (see _handle_set_initial_pose).
        self._initialpose_pub = self.create_publisher(
            PoseWithCovarianceStamped, "/initialpose", 1
        )

        # Localization watchdogs. AMCL publishes /amcl_pose with covariance;
        # /scan staleness reveals D435 / depthimage_to_laserscan dropouts.
        # _safety_tick cancels active nav goals if /scan stays stale > 5 s.
        self._amcl_cov_xy: float = float("inf")
        self._amcl_cov_yaw: float = float("inf")
        self._latest_amcl_stamp_ns: int = 0
        self._latest_scan_stamp_ns: int = 0
        self._latest_amcl_pose: tuple[float, float, float] | None = None
        self.create_subscription(
            PoseWithCovarianceStamped, "/amcl_pose", self._on_amcl_pose, 10,
        )
        self.create_subscription(LaserScan, "/scan", self._on_scan, 10)
        self._dead_reckon_since: float | None = None
        self._safety_timer = self.create_timer(1.0, self._safety_tick)

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

        # Status REP socket — backend polls this at ~1 Hz to learn localization
        # health (AMCL covariance + scan staleness) without depending on the
        # goto socket, which is often blocked inside a multi-second goToPose().
        self.rep_status = ctx.socket(zmq.REP)
        self.rep_status.setsockopt(zmq.LINGER, 0)
        status_bind_addr = f"tcp://*:{status_port}"
        _bind_or_die(self.rep_status, status_bind_addr, "status", self.get_logger())
        self.get_logger().info(f"nav_service status bound on {status_bind_addr}")

        threading.Thread(target=self._serve_loop, name="zmq-rep-goto",
                         daemon=True).start()
        threading.Thread(target=self._serve_pose_loop, name="zmq-rep-pose",
                         daemon=True).start()
        threading.Thread(target=self._serve_status_loop, name="zmq-rep-status",
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

    def _auto_seed_from_home_pose(self) -> bool:
        """At launch, if the wheel odometry is at (0, 0, 0) ± epsilon
        (i.e. stretch3-zmq just booted), publish home_pose.yaml to
        AMCL's /initialpose so the operator doesn't have to drag-set.
        Returns True if seeded; False otherwise."""
        hp = _load_home_pose(Path(__file__).resolve().parent / "config")
        if hp is None:
            self.get_logger().warn(
                "auto-seed: home_pose.yaml missing or malformed; "
                "operator must drag-set pose"
            )
            return False

        # Wait up to 5 s for first odom message from sensors_bridge.
        deadline = time.monotonic() + 5.0
        ox = oy = ot = None
        while time.monotonic() < deadline:
            with self._odom_lock:
                if self._latest_odom is not None:
                    ox, oy, ot = self._latest_odom
                    break
            time.sleep(0.1)
        if ox is None:
            self.get_logger().warn(
                "auto-seed: no odom received within 5 s; "
                "operator must drag-set pose"
            )
            return False

        xy_dist = math.hypot(ox, oy)
        theta_wrapped = math.atan2(math.sin(ot), math.cos(ot))
        if (xy_dist > hp["odom_epsilon_xy"]
                or abs(theta_wrapped) > hp["odom_epsilon_theta"]):
            self.get_logger().warn(
                f"auto-seed: odom not at origin (x={ox:.3f}, y={oy:.3f}, "
                f"theta={ot:.3f}); operator must drag-set"
            )
            return False

        msg = PoseWithCovarianceStamped()
        msg.header.frame_id = hp.get("frame_id", "map")
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.pose.pose.position.x = float(hp["x"])
        msg.pose.pose.position.y = float(hp["y"])
        msg.pose.pose.position.z = 0.0
        half = float(hp["theta"]) / 2.0
        msg.pose.pose.orientation.z = math.sin(half)
        msg.pose.pose.orientation.w = math.cos(half)
        cov = [0.0] * 36
        cov[0]  = 0.25   # xx ≈ (0.5 m)²
        cov[7]  = 0.25   # yy
        cov[35] = 0.07   # yaw ≈ (15°)²
        msg.pose.covariance = cov
        self._initialpose_pub.publish(msg)
        self.get_logger().info(
            f"auto-seeded from home_pose: ({hp['x']:.3f}, {hp['y']:.3f}, "
            f"{hp['theta']:.3f}) [map frame]"
        )
        return True

    def _on_amcl_pose(self, msg: PoseWithCovarianceStamped) -> None:
        # 6x6 covariance row-major: xx=[0], yy=[7], yaw=[35]. cov_xy is the
        # trace of the position block; cov_yaw is the angular variance.
        self._amcl_cov_xy = float(msg.pose.covariance[0] + msg.pose.covariance[7])
        self._amcl_cov_yaw = float(msg.pose.covariance[35])
        s = msg.header.stamp
        self._latest_amcl_stamp_ns = s.sec * 1_000_000_000 + s.nanosec
        p = msg.pose.pose
        self._latest_amcl_pose = (
            float(p.position.x),
            float(p.position.y),
            _quat_to_yaw(p.orientation),
        )

    def _on_scan(self, msg: LaserScan) -> None:
        s = msg.header.stamp
        self._latest_scan_stamp_ns = s.sec * 1_000_000_000 + s.nanosec

    def _localization_state(self) -> dict:
        """Pose health classification for status replies. States:
          unseeded    — no AMCL pose received yet (just-booted, pre-seed)
          dead-reckon — /scan stale > 1 s; AMCL is open-loop on odom
          uncertain   — covariance over loose thresholds; cloud not converged
          ok          — AMCL has fresh scan + tight covariance
        """
        now_ns = time.time_ns()
        scan_age_s = ((now_ns - self._latest_scan_stamp_ns) / 1e9
                      if self._latest_scan_stamp_ns else float("inf"))

        if self._latest_amcl_stamp_ns == 0:
            state = "unseeded"
        elif scan_age_s > 1.0:
            state = "dead-reckon"
        elif self._amcl_cov_xy > 1.0 or self._amcl_cov_yaw > 0.25:
            state = "uncertain"
        else:
            state = "ok"

        def _finite(v: float) -> float | None:
            return v if v != float("inf") else None

        return {
            "state": state,
            "cov_xy_m": _finite(self._amcl_cov_xy),
            "cov_yaw_rad": _finite(self._amcl_cov_yaw),
            "scan_age_s": _finite(scan_age_s),
        }

    def _safety_tick(self) -> None:
        """1 Hz: if /scan stays stale > 5 s, cancel any active nav goal.
        AMCL keeps integrating odom under dead-reckon, but the planner has
        no obstacle truth, so continuing to drive is unsafe."""
        state = self._localization_state()
        if state["state"] == "dead-reckon":
            if self._dead_reckon_since is None:
                self._dead_reckon_since = time.monotonic()
            elif time.monotonic() - self._dead_reckon_since > 5.0:
                if self._navigator is not None and self._nav_ready:
                    try:
                        self._navigator.cancelTask()
                        self.get_logger().warn(
                            "safety: /scan stale > 5 s — cancelled active nav goal"
                        )
                    except Exception as e:
                        self.get_logger().warn(f"safety: cancelTask failed: {e}")
                self._dead_reckon_since = None  # one-shot
        else:
            self._dead_reckon_since = None

    def _wait_for_nav2(self) -> None:
        # Block until bt_navigator + amcl are both ACTIVE. Once AMCL is up
        # it'll accept /initialpose, so this is the right point to auto-seed
        # from home_pose.yaml.
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
            self._auto_seed_from_home_pose()
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

    def _serve_status_loop(self) -> None:
        while True:
            try:
                # We don't even look at the request body — any message is a
                # status poll. Keeps the protocol trivial; backend just sends b"".
                self.rep_status.recv()
            except zmq.error.ZMQError as e:
                self.get_logger().error(f"status recv error: {e}")
                continue
            reply = {
                "nav_ready": self._nav_ready,
                "localization": self._localization_state(),
                "pose": (
                    list(self._latest_amcl_pose)
                    if self._latest_amcl_pose is not None
                    else None
                ),
            }
            self.rep_status.send(msgpack.packb(reply, use_bin_type=True))

    def _handle_set_initial_pose(self, req: dict) -> dict:
        if "_decode_error" in req:
            return {"ok": False, "reason": f"msgpack decode: {req['_decode_error']}"}
        target = req.get("target")
        if not isinstance(target, list) or len(target) != 3:
            return {"ok": False, "reason": "target must be [x, y, theta]"}

        target_x, target_y, target_theta = (float(v) for v in target)

        msg = PoseWithCovarianceStamped()
        msg.header.frame_id = "map"
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.pose.pose.position.x = target_x
        msg.pose.pose.position.y = target_y
        msg.pose.pose.position.z = 0.0
        half = target_theta / 2.0
        msg.pose.pose.orientation.z = math.sin(half)
        msg.pose.pose.orientation.w = math.cos(half)
        # Same covariance shape as auto-seed: ~0.5 m / ~15° std-dev so AMCL
        # spreads its particle cloud wide enough to converge after a manual
        # drag-set. Tighter values trap AMCL near a bad guess; looser values
        # waste cycles. AMCL re-computes map→odom internally.
        cov = [0.0] * 36
        cov[0]  = 0.25
        cov[7]  = 0.25
        cov[35] = 0.07
        msg.pose.covariance = cov
        self._initialpose_pub.publish(msg)

        self.get_logger().info(
            f"set_initial_pose → AMCL /initialpose: "
            f"({target_x:.3f}, {target_y:.3f}, {target_theta:.3f}) [map]"
        )

        # Trigger a global costmap clear so old footprint inflations from
        # the previous (probably-incorrect) robot pose disappear.
        if self._navigator is not None and self._nav_ready:
            try:
                self._navigator.clearAllCostmaps()
            except Exception as e:
                self.get_logger().warning(f"costmap clear failed: {e}")

        return {"ok": True}

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
    parser.add_argument("--status-port", type=int, default=DEFAULT_STATUS_PORT,
                        help=f"status REP port (default: {DEFAULT_STATUS_PORT})")
    parser.add_argument("--robot", default=DEFAULT_ROBOT_HOST,
                        help="robot hostname/IP for odom_tf SUB")
    parser.add_argument("--robot-odom-port", type=int,
                        default=DEFAULT_ROBOT_ODOM_PORT)
    args, ros_args = parser.parse_known_args()

    rclpy.init(args=ros_args)
    node = NavServiceNode(args.port, args.initial_pose_port, args.status_port,
                          args.robot, args.robot_odom_port)

    # Give THIS node its own SingleThreadedExecutor on a dedicated thread.
    # We can't rclpy.spin(node) on the main thread because BasicNavigator
    # spins its own internal node on the default context and a second spin
    # of the same node races the executor ("generator already executing").
    # Spinning a *different* node on a *different* executor is safe and
    # also necessary — without it, our /amcl_pose + /scan subscriptions
    # and the 1 Hz safety timer would never deliver callbacks.
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    threading.Thread(
        target=executor.spin, name="nav-service-spin", daemon=True
    ).start()

    stop = threading.Event()
    try:
        stop.wait()
    except KeyboardInterrupt:
        pass
    finally:
        executor.shutdown()
        node.destroy_node()
        try:
            rclpy.shutdown()
        except Exception:
            pass


if __name__ == "__main__":
    main()
