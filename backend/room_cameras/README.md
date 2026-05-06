# Room cameras (ROS2 → MJPEG bridge)

This directory hosts a **separate sidecar process** that subscribes to the
ED305 lab's overhead Basler cameras (published by
[`ED305_pylon_viewer`](https://github.com/chen1328/ED305_pylon_viewer) on the
`ros2_node` branch) and re-serves each topic as MJPEG over HTTP, plus a tiny
grid index page at `/`.

It is intentionally **not part of the langgraph-A2A backend**:

- ROS2 Humble ships its own Python 3.10 stack with `rclpy` C extensions
  built against that interpreter. The backend runs Python 3.12 — mixing
  them in one venv breaks `rclpy`.
- The bridge has no coupling to the backend code: separate process,
  separate port, separate Python. Stopping the bridge has zero impact on
  the dashboard / A2A / workflow code paths; the dashboard just shows a
  blank tile until it comes back.

## One-time setup

```bash
git clone https://github.com/chen1328/ED305_pylon_viewer.git /tmp/pylon
cd /tmp/pylon && git checkout ros2_node
bash scripts/install_ros2.sh   # adds ROS source + ROS_DOMAIN_ID=42 to ~/.bashrc
```

The launcher (`run_bridge.sh`) sources `/opt/ros/humble/setup.bash` itself,
so a fresh login is *not* required.

## Run

```bash
cd backend/room_cameras
./run_bridge.sh                                  # 0.0.0.0:9997, both sides, 8 cams/side
./run_bridge.sh --port 9997 --sides right        # right host only
./run_bridge.sh --sides right --cams-per-side 8
```

Open `http://<this-host>:9997/` for the 16-camera mosaic, or hit a single
stream:

```
http://<host>:9997/cam/right/0
http://<host>:9997/cam/left/3
```

`GET /healthz` returns `{"ok":true,"topics":[...]}` listing every topic
with at least one frame received — useful to confirm the publisher is
actually broadcasting.

## How the frontend picks it up

The Next.js dashboard reads `NEXT_PUBLIC_ROOM_CAMERAS_URL` at build time
and renders `<img>` tags pointing at `/cam/<side>/<idx>`. Set it to the
host:port the bridge is listening on (e.g. `http://10.0.0.5:9997`). When
unset, the `/cameras` page shows a "bridge URL not configured" banner but
the rest of the app is unaffected.

## QoS / network notes

- Publisher uses `BEST_EFFORT / VOLATILE / depth=1`. The bridge matches.
  If you change one side, change both — incompatible QoS = silent zero
  messages.
- `ROS_DOMAIN_ID` must match the publisher (default `42`). The launcher
  exports it; override with `ROS_DOMAIN_ID=N ./run_bridge.sh` if needed.
- Same subnet is recommended; many Wi-Fi APs block the multicast that
  Fast-DDS uses for discovery. Wired is the reliable path.

## Why MJPEG-over-HTTP and not WebRTC / WebSocket?

The publisher already encodes JPEG (`sensor_msgs/CompressedImage` with
`format="jpeg"`). MJPEG is a passthrough — the bridge never decodes a
frame, so OpenCV is unneeded and CPU stays cheap. The browser renders the
stream natively via `<img>` with zero JS. WebRTC would buy lower latency
and audio at the cost of ICE/SDP plumbing we do not need for situational
awareness panels.
