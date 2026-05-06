#!/usr/bin/env python3
"""ROS 2 → ZMQ cmd_vel bridge.

Subscribes to /cmd_vel (geometry_msgs/Twist) on the lab DDS and forwards
each message as a msgpack ZMQ frame to the robot's cmd_vel SUB endpoint
(default port 6014). The robot driver enforces a 200 ms watchdog — see
docs/lab-client-guide.md §6 — so this bridge MUST keep republishing at
the controller rate. Nav2's controller_frequency: 20.0 satisfies that.

Run:
    source /opt/ros/humble/setup.bash
    python3 backend/nav_bridge/cmdvel_bridge.py --robot 192.168.1.38
"""
from __future__ import annotations

import argparse
import time

import msgpack
import rclpy
import zmq
from geometry_msgs.msg import Twist
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

DEFAULT_ROBOT_HOST = "192.168.1.38"
DEFAULT_CMD_VEL_PORT = 6014

CMD_VEL_QOS = QoSProfile(
    reliability=ReliabilityPolicy.RELIABLE,
    history=HistoryPolicy.KEEP_LAST,
    depth=10,
)


class CmdVelBridge(Node):
    def __init__(self, robot_host: str, port: int) -> None:
        super().__init__("nvblox_cmdvel_bridge")
        ctx = zmq.Context.instance()
        self.sock = ctx.socket(zmq.PUB)
        self.sock.setsockopt(zmq.SNDHWM, 2)
        self.sock.setsockopt(zmq.CONFLATE, 1)
        self.sock.setsockopt(zmq.LINGER, 0)
        addr = f"tcp://{robot_host}:{port}"
        self.sock.connect(addr)
        time.sleep(0.3)  # let ZMQ handshake complete before first send

        self.create_subscription(Twist, "/cmd_vel", self._on_twist, CMD_VEL_QOS)
        self.get_logger().info(
            f"cmd_vel bridge: /cmd_vel (ROS) → {addr} (ZMQ PUB), watchdog=200ms"
        )

    def _on_twist(self, msg: Twist) -> None:
        payload = {
            "ts_ns": time.time_ns(),
            "linear_x": float(msg.linear.x),
            "angular_z": float(msg.angular.z),
        }
        try:
            self.sock.send(msgpack.packb(payload, use_bin_type=True))
        except zmq.error.ZMQError as e:
            self.get_logger().error(f"cmd_vel send failed: {e}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--robot", default=DEFAULT_ROBOT_HOST,
                        help=f"robot hostname/IP (default: {DEFAULT_ROBOT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_CMD_VEL_PORT)
    args, ros_args = parser.parse_known_args()

    rclpy.init(args=ros_args)
    node = CmdVelBridge(args.robot, args.port)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
