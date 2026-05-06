# Lab-Side User Guide: nvblox Robot-Driver ZMQ Surface

**Audience:** lab-side developers writing `sensors_bridge.py`, `cmdvel_bridge.py`, and `nav_service.py` — rclpy nodes that translate between the robot's ZMQ endpoints and the lab's local DDS network where Nav2 and nvblox run.

**Date:** 2026-05-06

---

## 1. Overview

The `stretch3-zmq` driver runs on the Stretch SE3 robot and exposes a thin ZMQ I/O surface for the lab GPU box's nvblox + Nav2 stack. The robot publishes four streams — depth frames, color frames, camera intrinsics, and wheel odometry — and subscribes to a single `cmd_vel` topic protected by a 200 ms wheel-stop watchdog. There is no rclpy on the robot, no on-board map, no goal handling. The robot is a dumb executor: it forwards sensor data, executes velocity commands, and halts if commands stop arriving. All planning, mapping, and goal logic live on the lab side.

---

## 2. Connection model

- **Robot binds; lab connects.** All five sockets are bound on the robot. Your bridge code calls `connect()` against the robot's hostname or IP.
- **Default robot host:** `stretch-se3-3099.local` (mDNS). Plain IPv4 addresses work equally well if mDNS is unreliable on your network.
- **Serialization:** every message on every new endpoint is a flat msgpack dict. Encode with `msgpack.packb(msg, use_bin_type=True)`, decode with `msgpack.unpackb(payload, raw=False)`. No multipart frames, no topic-prefix bytes, no compression layer above msgpack.
- **ZMQ socket options — robot PUB side** (depth / color / camera_info / odom_tf): `SNDHWM=2`, `CONFLATE=1`, `LINGER=0`. These are already set on the robot; you do not control them.
- **ZMQ socket options — your SUB side** (for depth / color / camera_info / odom_tf): set `RCVHWM=2`, `CONFLATE=1`, `LINGER=0`, `SUBSCRIBE=b""` on each socket you create.
- **ZMQ socket options — cmd_vel direction:** the lab is PUB, the robot is SUB. Your PUB socket: `SNDHWM=2`, `CONFLATE=1`, `LINGER=0`. Call `connect()`, not `bind()`.

---

## 3. Endpoint table

| Direction   | Topic         | Default port | Rate (Hz) | Payload (compact)                                             |
| ----------- | ------------- | :----------: | :-------: | ------------------------------------------------------------- |
| Robot → Lab | `depth`       |     6010     |    15     | `{ts_ns, h, w, encoding:"16UC1", data:bytes}` — raw uint16 mm |
| Robot → Lab | `color`       |     6011     |    15     | `{ts_ns, h, w, encoding:"rgb8", data:bytes}` — JPEG bytes     |
| Robot → Lab | `camera_info` |     6012     |     1     | `{ts_ns, K:list[9], D:list, distortion_model:str, h, w}`      |
| Robot → Lab | `odom_tf`     |     6013     |    50     | `{ts_ns, x:float, y:float, theta:float}` — odom→base_link     |
| Lab → Robot | `cmd_vel`     |     6014     |    20     | `{ts_ns, linear_x:float, angular_z:float}` — m/s, rad/s       |

All rates are configurable on the robot via `config.yaml` under `nvblox_nav.{depth,color,camera_info,odom_tf}_rate_hz`. The defaults above match `NvbloxNavConfig` in `driver/config.py`.

---

## 4. Schemas in detail

### 4.1 depth (port 6010)

```
{
  "ts_ns":    int,    # time.time_ns() on robot, CLOCK_REALTIME, nanoseconds
  "h":        int,    # frame height in pixels
  "w":        int,    # frame width in pixels
  "encoding": str,    # always "16UC1"
  "data":     bytes   # raw uint16 little-endian, length = h * w * 2, units: mm
}
```

Decode `data` to a numpy array:

```python
depth_mm = np.frombuffer(msg["data"], dtype="<u2").reshape(msg["h"], msg["w"])
```

Values are millimeters. Zero pixels represent invalid / no-return. Frame is in `depth_optical_frame` with native depth-stream intrinsics (not aligned to color).

### 4.2 color (port 6011)

```
{
  "ts_ns":    int,    # time.time_ns() on robot
  "h":        int,    # frame height in pixels
  "w":        int,    # frame width in pixels
  "encoding": str,    # "rgb8" — see note below
  "data":     bytes   # JPEG-encoded bytes, quality=80 (default)
}
```

> **Wire contract note:** the `encoding` field says `"rgb8"` per the spec, but `data` contains JPEG bytes, not raw RGB. This is a known artifact of the wire contract. Decode with OpenCV, which returns BGR:
>
> ```python
> bgr = cv2.imdecode(np.frombuffer(msg["data"], np.uint8), cv2.IMREAD_COLOR)
> ```
>
> If your pipeline needs RGB, convert: `rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)`.

Frame is in `color_optical_frame`. JPEG quality is configurable on the robot via `nvblox_nav.jpeg_quality` (default 80).

### 4.3 camera_info (port 6012)

```
{
  "ts_ns":             int,        # time.time_ns() on robot
  "K":                 list[float], # length-9, row-major 3x3 intrinsic matrix
  "D":                 list[float], # distortion coefficients
  "distortion_model":  str,         # "plumb_bob", "none", or another ROS-style string
  "h":                 int,         # image height
  "w":                 int          # image width
}
```

> **Depth intrinsics only.** This envelope carries depth-stream intrinsics. nvblox consumes depth + camera_info together, so depth intrinsics are the load-bearing values. Color intrinsics are **not** sent on the wire. Your `sensors_bridge` should hardcode color intrinsics from the one-time calibration stored in `nav_bridge/config/nvblox.yaml` and publish them as `/camera/color/camera_info` locally.

`K` layout: `[fx, 0, cx, 0, fy, cy, 0, 0, 1]` (standard ROS convention).

### 4.4 odom_tf (port 6013)

```
{
  "ts_ns": int,    # time.time_ns() on robot
  "x":     float, # meters, base_link origin in odom frame
  "y":     float, # meters
  "theta": float  # radians, yaw of base_link in odom frame (CCW positive)
}
```

> This is a 2D planar pose. Your bridge assembles a full 3D `TransformStamped` for tf2, assuming `z=0`, `roll=0`, `pitch=0`. Convert theta to a quaternion: `q = (0, 0, sin(theta/2), cos(theta/2))` in `(qx, qy, qz, qw)` order.

### 4.5 cmd_vel (port 6014)

```
{
  "ts_ns":     int,    # your time.time_ns(), informational only
  "linear_x":  float,  # m/s, positive = forward
  "angular_z": float   # rad/s, positive = counter-clockwise from above
}
```

Robot body axes: `+x` is forward, `+z` is up. `angular_z` follows right-hand rule around `+z`.

---

## 5. Sample code

### 5a. SUB receiver — general pattern

Any of the four PUB-consuming bridges can adapt this template.

```python
import threading
import queue
import zmq
import msgpack

ROBOT_HOST = "stretch-se3-3099.local"
DEPTH_PORT  = 6010  # change per topic

frame_queue: queue.Queue = queue.Queue(maxsize=2)


def recv_loop() -> None:
    """Drain the ZMQ socket in a daemon thread; hand frames to the queue."""
    ctx = zmq.Context.instance()
    sock = ctx.socket(zmq.SUB)
    sock.setsockopt(zmq.RCVHWM, 2)
    sock.setsockopt(zmq.CONFLATE, 1)
    sock.setsockopt(zmq.LINGER, 0)
    sock.setsockopt(zmq.SUBSCRIBE, b"")
    sock.connect(f"tcp://{ROBOT_HOST}:{DEPTH_PORT}")

    while True:
        payload = sock.recv()
        msg = msgpack.unpackb(payload, raw=False)
        # --- process the message here ---
        try:
            frame_queue.put_nowait(msg)
        except queue.Full:
            pass  # drop stale frame; CONFLATE already does this on the wire


t = threading.Thread(target=recv_loop, daemon=True)
t.start()
```

> Do not block ROS callbacks waiting on ZMQ. Run this loop in a daemon thread and hand frames to a queue or a shared latest-frame slot. The ROS publisher reads from that slot in its own timer callback.

### 5b. Decoding each stream

```python
import numpy as np
import cv2

# depth (port 6010)
depth_mm = np.frombuffer(msg["data"], dtype="<u2").reshape(msg["h"], msg["w"])

# color (port 6011) — data is JPEG bytes despite encoding field saying "rgb8"
bgr = cv2.imdecode(np.frombuffer(msg["data"], np.uint8), cv2.IMREAD_COLOR)

# camera_info (port 6012) — just print or build a CameraInfo message
print(msg["K"])            # [fx, 0, cx, 0, fy, cy, 0, 0, 1]
print(msg["D"])            # distortion coefficients
print(msg["distortion_model"])  # e.g. "plumb_bob"

# odom_tf (port 6013)
print(msg["x"], msg["y"], msg["theta"])  # meters, meters, radians
```

### 5c. cmd_vel publisher at 20 Hz

```python
import time
import zmq
import msgpack

ROBOT_HOST = "stretch-se3-3099.local"
CMD_VEL_PORT = 6014
RATE_HZ = 20

ctx = zmq.Context.instance()
sock = ctx.socket(zmq.PUB)
sock.setsockopt(zmq.SNDHWM, 2)
sock.setsockopt(zmq.CONFLATE, 1)
sock.setsockopt(zmq.LINGER, 0)
sock.connect(f"tcp://{ROBOT_HOST}:{CMD_VEL_PORT}")

# Allow ZMQ to establish the connection before sending.
time.sleep(0.3)

period_s = 1.0 / RATE_HZ
while True:
    t0 = time.monotonic()
    msg = {
        "ts_ns":     time.time_ns(),
        "linear_x":  0.0,   # m/s — set from Nav2 controller output
        "angular_z": 0.0,   # rad/s
    }
    sock.send(msgpack.packb(msg, use_bin_type=True))
    elapsed = time.monotonic() - t0
    if elapsed < period_s:
        time.sleep(period_s - elapsed)
```

> **Watchdog requirement:** the robot halts if no valid message arrives for 200 ms (default). Publish at 20 Hz to maintain smooth motion and stay well inside the watchdog window. Publishing slower than 5 Hz risks intermittent stops. The 20 Hz rate matches a typical Nav2 controller (`controller_frequency: 20.0`).

---

## 6. **Watchdog contract**

**This is a safety-critical requirement.**

The robot's `cmd_vel_endpoint` runs an internal watchdog thread (50 ms tick). If no well-formed msgpack `cmd_vel` message is received within `cmd_vel_watchdog_ms` (default: **200 ms**), the robot immediately sets wheel velocity to zero. The watchdog uses `time.monotonic()` and is immune to NTP clock jumps.

**The lab Nav2 controller MUST publish at >= 5 Hz.** 20 Hz is the recommended rate; it matches a typical Nav2 `controller_frequency` and leaves a comfortable margin before the 200 ms cutoff.

**Malformed messages do not satisfy the watchdog.** If the robot receives a message it cannot decode with msgpack, it logs and ignores the message. The watchdog timestamp is updated only after a successful decode. A flood of corrupt messages will trigger a wheel stop.

**Failure cases:**

- **Wi-Fi drop mid-traverse:** `cmd_vel` messages stop arriving. The robot halts within approximately `cmd_vel_watchdog_ms + 50 ms` (250 ms worst case). nvblox sees depth and odom timestamps frozen or absent and will eventually report the goal as OBSTRUCTED or TIMED_OUT. Your `nav_service` should treat a prolonged sensor blackout as a navigation failure.
- **Robot driver crash:** your `cmdvel_bridge`'s PUB socket will get `ECONNREFUSED` when it next tries to connect (or ZMQ will silently queue, then drop on `SNDHWM`). Map this to a `ROBOT_ERROR` state in your `nav_service` reply so the Mac-side caller gets a clean error rather than a silent hang.
- **Lab box absent at robot boot:** the robot's PUB sockets drop frames at `SNDHWM=2` with no error; the `cmd_vel` SUB receives nothing, so the watchdog keeps the wheels at zero. The driver is healthy and will start delivering data the moment your lab bridges connect.

---

## 7. Frame conventions

| Stream  | ROS frame ID (assign in bridge)    | Notes                                                                                      |
| ------- | ---------------------------------- | ------------------------------------------------------------------------------------------ |
| depth   | `depth_optical_frame`              | Native depth-stream intrinsics; NOT aligned to color. Values in mm, uint16.                |
| color   | `color_optical_frame`              | JPEG-encoded; decode to BGR via OpenCV.                                                    |
| odom_tf | parent: `odom`, child: `base_link` | 2D pose from wheel odometry; lab promotes to full 3D TransformStamped (z=0, roll=pitch=0). |

The robot driver does **not** embed ROS frame-ID strings in any envelope. Your bridge is responsible for assigning the correct frame IDs shown above when publishing to DDS.

**Timestamps** are `time.time_ns()` (CLOCK_REALTIME on Linux, nanosecond resolution). With NTP, robot and lab clocks should agree to within ~10 ms on a LAN. If tighter time synchronization is required for nvblox depth-color fusion, run PTP (`linuxptp` + `phc2sys`) on both machines.

---

## 8. Smoke-test commands

Run these from the lab box before wiring nvblox. Replace `<robot-host>` with `stretch-se3-3099.local` (or the robot's IP).

```bash
ROBOT=stretch-se3-3099.local

# --- 1. Subscribe to depth, color, camera_info: print 3 frames each ---
python3 - <<'EOF'
import zmq, msgpack, sys

ROBOT = "stretch-se3-3099.local"

for port, label in [(6010, "depth"), (6011, "color"), (6012, "camera_info")]:
    ctx = zmq.Context.instance()
    s = ctx.socket(zmq.SUB)
    s.setsockopt(zmq.RCVHWM, 2)
    s.setsockopt(zmq.CONFLATE, 1)
    s.setsockopt(zmq.LINGER, 0)
    s.setsockopt(zmq.SUBSCRIBE, b"")
    s.connect(f"tcp://{ROBOT}:{port}")
    print(f"\n[{label} port {port}]")
    for i in range(3):
        m = msgpack.unpackb(s.recv(), raw=False)
        data_len = len(m.get("data", m.get("K", [])))
        print(f"  frame {i}: ts_ns={m['ts_ns']} h={m.get('h')} w={m.get('w')} "
              f"encoding={m.get('encoding', '-')} data_len={data_len}")
    s.close()
print("\nPASS: received 3 frames from depth, color, camera_info")
EOF

# --- 2. Subscribe to odom_tf: print 50 messages, confirm ~50 Hz ---
python3 - <<'EOF'
import zmq, msgpack, time

ROBOT = "stretch-se3-3099.local"
s = zmq.Context.instance().socket(zmq.SUB)
s.setsockopt(zmq.RCVHWM, 2)
s.setsockopt(zmq.CONFLATE, 1)
s.setsockopt(zmq.LINGER, 0)
s.setsockopt(zmq.SUBSCRIBE, b"")
s.connect(f"tcp://{ROBOT}:6013")
t0 = time.monotonic()
for i in range(50):
    m = msgpack.unpackb(s.recv(), raw=False)
    print(f"  {i:2d}: x={m['x']:.4f}  y={m['y']:.4f}  theta={m['theta']:.4f}  ts_ns={m['ts_ns']}")
elapsed = time.monotonic() - t0
print(f"\n50 messages in {elapsed:.2f}s  (~{50/elapsed:.1f} Hz)  -- expect ~50 Hz")
EOF

# --- 3. Send ONE cmd_vel: robot should creep then halt on watchdog (~200 ms) ---
python3 - <<'EOF'
import zmq, msgpack, time

ROBOT = "stretch-se3-3099.local"
s = zmq.Context.instance().socket(zmq.PUB)
s.setsockopt(zmq.SNDHWM, 2)
s.setsockopt(zmq.CONFLATE, 1)
s.setsockopt(zmq.LINGER, 0)
s.connect(f"tcp://{ROBOT}:6014")
time.sleep(0.5)   # let ZMQ handshake complete
s.send(msgpack.packb({"ts_ns": time.time_ns(), "linear_x": 0.05, "angular_z": 0.0},
                     use_bin_type=True))
print("Sent one cmd_vel(linear_x=0.05). Robot should creep ~200 ms then halt.")
time.sleep(2)
EOF

# --- 4. Send cmd_vel at 20 Hz for 3 s, then stop: confirm halt within 250 ms ---
python3 - <<'EOF'
import zmq, msgpack, time

ROBOT = "stretch-se3-3099.local"
s = zmq.Context.instance().socket(zmq.PUB)
s.setsockopt(zmq.SNDHWM, 2)
s.setsockopt(zmq.CONFLATE, 1)
s.setsockopt(zmq.LINGER, 0)
s.connect(f"tcp://{ROBOT}:6014")
time.sleep(0.3)

end = time.monotonic() + 3.0
count = 0
while time.monotonic() < end:
    t0 = time.monotonic()
    s.send(msgpack.packb({"ts_ns": time.time_ns(), "linear_x": 0.05, "angular_z": 0.0},
                         use_bin_type=True))
    count += 1
    leftover = 0.05 - (time.monotonic() - t0)
    if leftover > 0:
        time.sleep(leftover)

print(f"Sent {count} messages over 3 s. Stopped publishing.")
print("Robot should halt within ~250 ms. Watch the base.")
time.sleep(2)
EOF
```

Expected outcomes:
- Test 1: 3 frames per port, `h`/`w` non-zero, `data_len` > 0.
- Test 2: 50 messages at approximately 50 Hz; `x`/`y` change if you nudge the base.
- Test 3: robot creeps briefly (< 300 ms), then stops.
- Test 4: robot moves continuously for ~3 s, then halts within ~250 ms after the publisher exits.

---

## 9. Cross-references

- `docs/nvblox/2026-05-05-nvblox-navigation-design.md` — system-wide design (lab + Mac + robot architecture, full phase plan).
- `docs/nvblox/2026-05-06-robot-driver-migration-design.md` — robot-driver design rationale, wire-format rationale, failure-mode analysis.
- `docs/nvblox/nvblox-integration-guide.md` — three-team how-to: lab-side Isaac ROS / nvblox / Nav2 installation, `sensors_bridge` / `cmdvel_bridge` / `nav_service` integration checklist, Phase 1 end-to-end verification steps.
