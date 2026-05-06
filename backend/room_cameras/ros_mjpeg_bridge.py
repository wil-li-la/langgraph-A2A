"""ROS2 → MJPEG-over-HTTP bridge for the ED305 room cameras.

Runs as a sidecar process under the system Python that ROS2 Humble ships
(/opt/ros/humble Python 3.10). Subscribes to every
/camera_{left,right}_{0..N}/image/compressed topic and re-serves each as
MJPEG on a small HTTP server, with a grid index page at /.

Use the run_bridge.sh launcher — it sources /opt/ros/humble/setup.bash and
sets ROS_DOMAIN_ID before exec'ing this script.
"""

import argparse
import logging
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import rclpy
from rclpy.node import Node
from rclpy.qos import (
    DurabilityPolicy,
    HistoryPolicy,
    QoSProfile,
    ReliabilityPolicy,
)
from sensor_msgs.msg import CompressedImage


_lock = threading.Lock()
_condition = threading.Condition(_lock)
_frames: dict[str, bytes] = {}


def topic_name(side: str, idx: int) -> str:
    return f"/camera_{side}_{idx}/image/compressed"


class CameraSubscriber(Node):
    def __init__(self, sides: list[str], cams_per_side: int):
        super().__init__("room_camera_mjpeg_bridge")
        qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )
        self._subs = []
        for side in sides:
            for i in range(cams_per_side):
                topic = topic_name(side, i)
                sub = self.create_subscription(
                    CompressedImage,
                    topic,
                    lambda msg, t=topic: self._on_image(t, msg),
                    qos,
                )
                self._subs.append(sub)
                self.get_logger().info(f"subscribed: {topic}")

    @staticmethod
    def _on_image(topic: str, msg: CompressedImage) -> None:
        # Publisher already encodes JPEG (msg.format == "jpeg"); pass through.
        data = bytes(msg.data)
        with _condition:
            _frames[topic] = data
            _condition.notify_all()


class Handler(BaseHTTPRequestHandler):
    server_version = "RoomCamMJPEG/1.0"

    sides: tuple[str, ...] = ("left", "right")
    cams_per_side: int = 8

    def log_message(self, fmt: str, *args) -> None:  # silence default access log
        return

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._index()
            return
        if path == "/healthz":
            self._json_ok()
            return
        if path.startswith("/cam/") or path.startswith("/snap/"):
            parts = path.split("/")
            if len(parts) == 4 and parts[2] in self.sides and parts[3].isdigit():
                topic = topic_name(parts[2], int(parts[3]))
                if parts[1] == "cam":
                    self._stream(topic)
                else:
                    self._snap(topic)
                return
        self.send_error(404, "not found")

    def _json_ok(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        with _lock:
            present = sorted(_frames.keys())
        body = (
            '{"ok":true,"topics":['
            + ",".join(f'"{t}"' for t in present)
            + "]}"
        )
        self.wfile.write(body.encode())

    def _index(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        tiles = []
        for side in self.sides:
            for i in range(self.cams_per_side):
                tiles.append(
                    f"<figure style='margin:0'>"
                    f"<img src='/cam/{side}/{i}' "
                    f"style='width:100%;display:block;background:#000' "
                    f"alt='{side}_{i}'/>"
                    f"<figcaption style='font:12px monospace;padding:2px 4px;"
                    f"background:#222;color:#bbb'>{side}_{i}</figcaption>"
                    f"</figure>"
                )
        html = (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<title>Room cameras</title></head>"
            "<body style='margin:0;background:#111;color:#eee;"
            "font-family:monospace'>"
            "<header style='padding:8px 12px;border-bottom:1px solid #333'>"
            "ED305 room cameras &mdash; ROS2 → MJPEG bridge</header>"
            "<main style='display:grid;grid-template-columns:repeat(4,1fr);"
            "gap:4px;padding:4px'>" + "".join(tiles) + "</main></body></html>"
        )
        self.wfile.write(html.encode())

    def _snap(self, topic: str) -> None:
        """Single-shot JPEG endpoint. Cheap to poll — used by the grid tiles
        to side-step the browser's per-origin connection limit (long-lived
        MJPEG would let only ~6 tiles connect)."""
        with _lock:
            data = _frames.get(topic)
        if data is None:
            self.send_response(503)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _stream(self, topic: str) -> None:
        self.send_response(200)
        self.send_header("Cache-Control", "no-cache, private")
        self.send_header("Pragma", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header(
            "Content-Type", "multipart/x-mixed-replace; boundary=frame"
        )
        self.end_headers()

        last = None
        try:
            while True:
                with _condition:
                    _condition.wait_for(
                        lambda: _frames.get(topic) is not None
                        and _frames.get(topic) is not last,
                        timeout=10.0,
                    )
                    data = _frames.get(topic)
                if data is None or data is last:
                    # No frame yet — keep the connection alive but do not spam.
                    continue
                last = data
                self.wfile.write(b"--frame\r\n")
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(
                    f"Content-Length: {len(data)}\r\n\r\n".encode()
                )
                self.wfile.write(data)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=9997)
    p.add_argument(
        "--sides",
        default="left,right",
        help="Comma-separated camera sides to subscribe (default: left,right).",
    )
    p.add_argument(
        "--cams-per-side",
        type=int,
        default=8,
        help="How many cameras per side to subscribe (default: 8).",
    )
    return p.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    args = parse_args()
    sides = tuple(s.strip() for s in args.sides.split(",") if s.strip())

    rclpy.init()
    node = CameraSubscriber(list(sides), args.cams_per_side)

    def _spin() -> None:
        try:
            rclpy.spin(node)
        except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
            pass  # cooperatively shut down with the main thread

    spin_thread = threading.Thread(
        target=_spin, daemon=True, name="rclpy-spin"
    )
    spin_thread.start()

    Handler.sides = sides
    Handler.cams_per_side = args.cams_per_side

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    print(
        f"[bridge] http://{args.host}:{args.port}/  "
        f"sides={sides} cams_per_side={args.cams_per_side}",
        flush=True,
    )
    print("[bridge] streams: /cam/<side>/<idx>", flush=True)

    # Land SIGTERM/SIGINT on a clean shutdown of serve_forever.
    def _stop(_signum, _frame) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    try:
        server.serve_forever()
    finally:
        server.server_close()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
