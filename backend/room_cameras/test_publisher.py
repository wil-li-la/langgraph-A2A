"""Synthetic test publisher for the room-camera pipeline.

Useful when the real Pylon publisher (running on the Windows host in ED305)
is not reachable and you just want to verify the
bridge → MJPEG → frontend leg of the chain.

Publishes a generated JPEG (timestamp + frame counter on a colored
background) to one or more `/camera_<side>_<idx>/image/compressed` topics.

Run via the same launcher pattern as the bridge so ROS2 is sourced and
ROS_DOMAIN_ID matches:

    bash -lc 'source /opt/ros/humble/setup.bash \
        && export ROS_DOMAIN_ID=42 \
        && export RMW_IMPLEMENTATION=rmw_fastrtps_cpp \
        && python3 backend/room_cameras/test_publisher.py'

Defaults: publishes 30 fps to /camera_right_0/image/compressed only.
"""

import argparse
import time

import cv2
import numpy as np

import rclpy
from rclpy.node import Node
from rclpy.qos import (
    DurabilityPolicy,
    HistoryPolicy,
    QoSProfile,
    ReliabilityPolicy,
)
from sensor_msgs.msg import CompressedImage


class FakePylonPublisher(Node):
    def __init__(self, topics: list[str], width: int, height: int, fps: float):
        super().__init__("pylon_test_publisher")
        qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )
        self.publishers_by_topic = {
            t: self.create_publisher(CompressedImage, t, qos) for t in topics
        }
        self.width = width
        self.height = height
        self.frame = 0
        self.timer = self.create_timer(1.0 / fps, self._tick)
        self.get_logger().info(
            f"publishing on {len(topics)} topic(s) at {fps:g} Hz: {topics}"
        )

    def _tick(self) -> None:
        self.frame += 1
        for topic, pub in self.publishers_by_topic.items():
            jpeg = self._render(topic)
            msg = CompressedImage()
            msg.header.stamp = self.get_clock().now().to_msg()
            msg.header.frame_id = f"test|{topic}|{self.frame}"
            msg.format = "jpeg"
            msg.data = jpeg
            pub.publish(msg)

    def _render(self, topic: str) -> bytes:
        # Solid color that drifts over time so motion is obvious in the UI.
        hue = (self.frame * 2) % 180
        hsv = np.full((self.height, self.width, 3), (hue, 200, 200), np.uint8)
        bgr = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
        cv2.putText(
            bgr,
            time.strftime("%H:%M:%S"),
            (20, 60),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.6,
            (255, 255, 255),
            3,
        )
        cv2.putText(
            bgr,
            f"{topic}",
            (20, self.height - 60),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (255, 255, 255),
            2,
        )
        cv2.putText(
            bgr,
            f"frame {self.frame}",
            (20, self.height - 25),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (220, 220, 220),
            2,
        )
        ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return buf.tobytes() if ok else b""


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--topics",
        default="/camera_right_0/image/compressed",
        help=(
            "Comma-separated list of topics to publish. "
            "Default: /camera_right_0/image/compressed"
        ),
    )
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--height", type=int, default=480)
    p.add_argument("--fps", type=float, default=30.0)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    topics = [t.strip() for t in args.topics.split(",") if t.strip()]
    rclpy.init()
    node = FakePylonPublisher(topics, args.width, args.height, args.fps)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
