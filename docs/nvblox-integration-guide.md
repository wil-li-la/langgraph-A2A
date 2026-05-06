# nvblox Navigation Integration Guide

**Audience:** robot-side dev (extending `stretch3-zmq`), lab-side dev
(installing Isaac ROS + nvblox + bringing up the bridges + the LangGraph
backend — all on the lab GPU box `hcis-s28`). Frontend is on Cloudflare
Pages and reaches the backend via the existing Cloudflare Tunnel; no
local "Mac" host is involved anymore.

**Goal of Phase 1:** the **dashboard `/nav` page** drives the robot.
A user opens it, sees the auto-localized robot pose on the room-305
map, drag-adjusts if the localizer is off, then clicks-and-drags any
point on the map to send a navigation goal. The robot drives there.
The CLI `backend/test_nvblox_nav.py 1.5 0.0 90 --lab-host hcis-s28`
remains as a low-level smoke test that talks straight to `nav_service`
over ZMQ (skips the backend proxy) — useful for verifying `nav_service`
in isolation.

**The medication-delivery workflow is not modified in Phase 1.**
Workflow integration is Phase 2 and only starts after Phase 1's
verification checklist passes (see end of this doc).

The full design rationale lives in
`docs/superpowers/specs/2026-05-05-nvblox-navigation-design.md`.
This guide is a how-to for the three teams that have to build it.

---

## 0 · Architecture in one diagram

```
   Browser (Cloudflare Pages)   Lab GPU box (hcis-s28) — backend   Stretch SE3 (robot)
   ──────────────────────────   ─────────────────────────────────   ─────────────────────
                                ┌─ Starlette /api/nav/* (port 9999) ┌─ stretch3-zmq driver
   /nav page ──HTTPS via       │     ↓ ZMQ REQ to localhost:5560   │
   Cloudflare Tunnel ──────→   ├─ nav_service (ZMQ REP)            │
                                │     ↓ /goal_pose                  │
   test_nvblox_nav.py ─REQ──→   │ (skips proxy, hits :5560 direct)  │
                                │  Nav2 + nvblox                    │   PUB depth/color/
                                │     ↑ depth/color/tf              │       camera_info/
                                │  sensors_bridge ←─ZMQ─────────────┤       odom_tf
                                │  cmdvel_bridge   ─ZMQ────────────→│   SUB cmd_vel
                                │     ↑ map→base_link               │       (200ms watchdog)
                                │  external room-cam localizer       │
                                └────────────────────────────────────└────────────
```

Three responsibilities, three teams:

| Team | Repo | What they do |
|---|---|---|
| Robot | `stretch3-zmq` | Add the new PUB/SUB sockets, the watchdog |
| Lab | `langgraph-A2A` `backend/nav_bridge/` (new dir) | Install Isaac ROS + nvblox; write the bridges + nav_service |
| Backend | `langgraph-A2A` (this repo, root, on lab box) | Run `python -m app`; the dashboard talks to `/api/nav/*` which forwards to `nav_service` over local ZMQ. Phase 2: wire `app/skills/nav.py` into `medication_delivery.py` |

---

## Part A · Robot side (`stretch3-zmq`)

The robot must learn five new ZMQ sockets and one watchdog.

### A.1 Add ports to `config.yaml`

```yaml
ports:
  # ... existing entries (status, navigate, listen, speak, ...) ...
  depth: 6010          # PUB
  color: 6011          # PUB
  camera_info: 6012    # PUB
  odom_tf: 6013        # PUB
  cmd_vel: 6014        # SUB

cmd_vel_watchdog_ms: 200
```

### A.2 Driver service: `driver/services/depth.py`

```python
# Pattern: follow driver/services/<existing_camera_service>.py
# Pull frames from the head RealSense (D435if), msgpack-encode, PUB.

import time, zmq, msgpack
from driver.config import get_config

def run(ctx: zmq.Context, head_camera):
    cfg = get_config()
    sock = ctx.socket(zmq.PUB)
    sock.setsockopt(zmq.SNDHWM, 2)
    sock.setsockopt(zmq.CONFLATE, 1)
    sock.setsockopt(zmq.LINGER, 0)
    sock.bind(f"tcp://*:{cfg.ports.depth}")

    while True:
        frame = head_camera.get_depth_frame()        # uint16 mm
        msg = {
            "ts_ns": time.time_ns(),
            "h": frame.shape[0],
            "w": frame.shape[1],
            "encoding": "16UC1",
            "data": frame.tobytes(),
        }
        sock.send(msgpack.packb(msg, use_bin_type=True))
        time.sleep(1/15)   # 15 Hz
```

Mirror this for `color` (encoding `"rgb8"`, jpeg-compressed bytes
from `cv2.imencode`) and `camera_info` (1 Hz, payload
`{K, D, distortion_model, h, w}`).

### A.3 Driver service: `driver/services/odom_tf.py`

50 Hz PUB of `{ts_ns, x, y, theta}` from the existing
status/odometry stream. Same pattern as A.2 but smaller payload.

### A.4 Driver service: `driver/services/cmd_vel.py`

```python
import time, zmq, msgpack, threading
from driver.config import get_config

def run(ctx: zmq.Context, mobile_base):
    cfg = get_config()
    sock = ctx.socket(zmq.SUB)
    sock.setsockopt(zmq.SUBSCRIBE, b"")
    sock.setsockopt(zmq.RCVHWM, 2)
    sock.setsockopt(zmq.CONFLATE, 1)
    sock.setsockopt(zmq.LINGER, 0)
    sock.bind(f"tcp://*:{cfg.ports.cmd_vel}")

    last_msg_ts = [0.0]
    watchdog_s = cfg.cmd_vel_watchdog_ms / 1000.0

    def watchdog():
        while True:
            if time.monotonic() - last_msg_ts[0] > watchdog_s:
                mobile_base.set_velocity(0.0, 0.0)
            time.sleep(0.05)
    threading.Thread(target=watchdog, daemon=True).start()

    while True:
        try:
            payload = sock.recv(flags=zmq.NOBLOCK)
        except zmq.error.Again:
            time.sleep(0.005)
            continue
        msg = msgpack.unpackb(payload, raw=False)
        last_msg_ts[0] = time.monotonic()
        mobile_base.set_velocity(msg["linear_x"], msg["angular_z"])
```

The watchdog is **not** optional — without it, a Wi-Fi drop leaves
the robot driving on the last cmd_vel until something physical
intervenes.

### A.5 Wire the new services into `driver/__main__.py`

Same pattern as the existing services: launch each as a
`threading.Thread`.

> **Supersedes the `goto.py` TODO in `langgraph-A2A/CLAUDE.md`.** That
> note describes a robot-side `goto` REQ/REP service on port 5557 that
> would run Nav2 *on the robot*. This design moves Nav2 to the lab box
> and uses `cmd_vel` PUB/SUB on port 6014 instead. **Do not implement
> `goto.py` — implement the services in this section.** The CLAUDE.md
> note will be removed when Phase 2 lands.

### A.6 Robot-side smoke test

From the lab box, after services are running on the robot:

```bash
# subscribe to depth and confirm frames arrive
python -c "
import zmq, msgpack
ctx = zmq.Context.instance()
s = ctx.socket(zmq.SUB); s.subscribe(b'')
s.connect('tcp://stretch-se3-3099.local:6010')
for _ in range(5):
    m = msgpack.unpackb(s.recv(), raw=False)
    print(m['ts_ns'], m['h'], m['w'], m['encoding'], len(m['data']))
"
```

Then send a low-speed cmd_vel and watch the robot creep forward 200 ms,
then halt:

```bash
python -c "
import zmq, msgpack, time
ctx = zmq.Context.instance()
s = ctx.socket(zmq.PUB)
s.connect('tcp://stretch-se3-3099.local:6014')
time.sleep(0.5)  # let SUB attach
s.send(msgpack.packb({'ts_ns': time.time_ns(), 'linear_x': 0.05, 'angular_z': 0.0}))
print('sent — robot should move briefly then stop on watchdog')
time.sleep(2)
"
```

If the robot drove for ~200 ms then stopped, the watchdog works.

---

## Part B · Lab side (Isaac ROS + nvblox + bridges)

### B.1 Hardware / OS prerequisites

Confirm before starting:

- [ ] Ubuntu 22.04 (Isaac ROS DP supports 22.04; 24.04 is unsupported as of
      Isaac ROS 3.1)
- [ ] x86_64
- [ ] NVIDIA GPU, Compute Capability ≥ 7.5 (Turing or newer — RTX 20-series
      and up)
- [ ] CUDA 12.x driver
- [ ] ≥ 30 GB free on the partition where Docker images live
- [ ] Docker + NVIDIA Container Toolkit (`docker run --gpus all` works)
- [ ] ROS 2 Humble — Isaac ROS DP runs inside a container with Humble
      preinstalled; you do not need Humble on the host

### B.2 Install Isaac ROS Dev environment

The official path is the **Isaac ROS Dev container**, which bundles all
the build tooling, ROS 2 Humble, and the GPU-accelerated packages.

Follow the canonical quickstart — do not improvise:
**https://nvidia-isaac-ros.github.io/getting_started/index.html**

Specifically:
1. Set up the **Developer Environment** page (clone
   `isaac_ros_common`, set `ISAAC_ROS_WS`, run `run_dev.sh`).
2. Run the **Hardware Setup** smoke test (e.g. RealSense quickstart) so
   you know your camera + GPU + driver path is healthy *before* layering
   nvblox on top. If the RealSense quickstart doesn't work, nvblox won't.

### B.3 Install nvblox

Repo: **https://github.com/NVIDIA-ISAAC-ROS/isaac_ros_nvblox**

Follow the **Quickstart** in that repo's README. The simplest verified
path is:

1. Inside the Isaac ROS Dev container, clone `isaac_ros_nvblox` into
   `${ISAAC_ROS_WS}/src/`.
2. `rosdep install --from-paths src --ignore-src -r -y`
3. `colcon build --symlink-install --packages-up-to nvblox_examples_bringup`
4. Run the offline tutorial bag (Carter dataset) — confirms nvblox
   itself is functional before you point it at live data.

If anything from this section fails, escalate before continuing —
nvblox install issues compound.

### B.4 Install Nav2

Inside the same dev container:
```bash
sudo apt update
sudo apt install -y ros-humble-nav2-bringup ros-humble-nav2-map-server
```

You will *not* be using `nav2_amcl` (we localize via room cameras),
but `nav2-bringup` may pull it transitively — that's fine.

### B.5 Build the bridges (this repo)

In the **host shell** (not inside the Isaac ROS container — these
bridges run as plain ROS2 Humble nodes alongside the container, in the
same network namespace):

```bash
cd /path/to/langgraph-A2A
mkdir -p backend/nav_bridge/{config,launch}
```

Sketches of the four files (full implementations TBD by lab dev):

#### `backend/nav_bridge/sensors_bridge.py`

Subscribes to robot's ZMQ `depth`, `color`, `camera_info`, `odom_tf`
topics; republishes them as ROS 2 `sensor_msgs/Image`,
`sensor_msgs/CameraInfo`, and a `tf2` `odom→base_link` transform on the
lab's local DDS. nvblox + Nav2 see only standard ROS topics.

```python
# rclpy node skeleton
# - 4 zmq.SUB sockets (set CONFLATE=1, RCVHWM=2)
# - matching ROS2 publishers on /camera/depth/image_rect_raw,
#   /camera/color/image_raw, /camera/color/camera_info, /tf
# - one Timer per stream, drains the SUB and republishes
# - frame_ids: depth_optical_frame, color_optical_frame, base_link, odom
```

#### `backend/nav_bridge/cmdvel_bridge.py`

Subscribes to `/cmd_vel` (ROS 2), publishes msgpack to robot's
`tcp://robot:6014`. `LINGER=0`, no batching.

#### `backend/nav_bridge/nav_service.py`

Main entry point for Phase 1. Outline:

```python
import zmq, msgpack
from nav2_simple_commander.robot_navigator import BasicNavigator, TaskResult

ctx = zmq.Context.instance()
rep = ctx.socket(zmq.REP); rep.bind("tcp://*:5560")
nav = BasicNavigator(); nav.waitUntilNav2Active()

while True:
    req = msgpack.unpackb(rep.recv(), raw=False)
    target = req["target"]
    if isinstance(target, str):
        target = poses_yaml[target]   # phase 2
    x, y, theta = target

    goal = build_pose_stamped(x, y, theta, nav.get_clock().now())
    nav.goToPose(goal)
    while not nav.isTaskComplete():
        feedback = nav.getFeedback()
        # optional: log progress to rerun

    result = nav.getResult()
    status = {
        TaskResult.SUCCEEDED: "OK",
        TaskResult.CANCELED:  "CANCELLED",
        TaskResult.FAILED:    "OBSTRUCTED",
    }.get(result, "ROBOT_ERROR")

    final = current_robot_pose_in_map(nav)
    rep.send(msgpack.packb({
        "status": status,
        "reason": "",
        "final_pose": final,
        "elapsed_s": elapsed,
    }, use_bin_type=True))
```

#### `backend/nav_bridge/launch/nav.launch.py`

Brings up: map_server (loads `backend/maps/305/map.yaml`),
nvblox_node, nav2_bringup (lifecycle + planner + controller),
sensors_bridge, cmdvel_bridge, nav_service. Single `ros2 launch`
command for the whole stack.

#### Config files

- `config/nav2_params.yaml` — start from
  `nav2_bringup/params/nav2_params.yaml`, edit:
  - `controller_server.FollowPath.max_vel_x: 0.7` (Stretch comfortable)
  - `bt_navigator.transform_tolerance: 0.5` (slack for ZMQ jitter)
  - `local_costmap.plugins: ["nvblox_layer", "inflation_layer"]`
- `config/nvblox.yaml` — depth camera intrinsics (filled from
  `camera_info`), `voxel_size: 0.05`, `esdf_slice_height: 0.3`
  (above floor, below most obstacles).
- `config/poses.yaml` — Phase 2 only:
  ```yaml
  medicine: [x, y, theta]   # fill from a manual drive-and-record run
  patient:  [x, y, theta]
  origin:   [x, y, theta]
  ```

### B.6 Lab-side smoke test (without robot)

Before plugging the robot in, validate the lab stack in isolation
using a recorded ROS bag from the Isaac ROS quickstart:

```bash
ros2 bag play <isaac_ros_test_bag> --loop &
ros2 launch backend/nav_bridge/launch/nav.launch.py
# In another shell:
ros2 topic echo /nvblox_node/static_map_slice
ros2 topic echo /global_costmap/costmap
```

If those topics produce data, the Nav2+nvblox graph is alive.

---

## Part C · Backend (lab box) + frontend (Cloudflare Pages)

The lab box runs both the LangGraph backend (Starlette on `:9999`) and
the nav stack from Part B. The backend ships a `/api/nav/*` proxy that
forwards goals to `nav_service` over local ZMQ. The frontend is a
static export hosted on Cloudflare Pages; it reaches the backend
through the existing Cloudflare Tunnel at `stretch-api.<domain>`. The
`/nav` page is the primary verification surface.

### C.1 Install dependencies

```bash
cd backend && source ../.venv/bin/activate
pip install pyzmq msgpack
```

(Both are likely already in the venv via `cure`. Verify with
`pip show pyzmq msgpack`.)

### C.2 Configure the lab address

Set in `backend/.env` (or export before running):

```
NVBLOX_NAV_HOST=hcis-s28      # lab GPU box
NVBLOX_NAV_PORT=5560
NVBLOX_NAV_TIMEOUT_S=60
```

If unset, defaults to `localhost:5560`.

### C.3 Phase 1 verification — dashboard flow (primary)

1. **Restart the backend** to pick up the nav routes:
   ```bash
   python -m app --host 0.0.0.0 --port 9999
   ```
   Smoke test: `curl http://localhost:9999/api/nav/map` should return
   the room-305 map metadata.

2. **Start the lab stack** (Part B):
   ```bash
   ros2 launch backend/nav_bridge/launch/nav.launch.py
   ```

3. **Start the robot driver** with the new services (Part A).

4. **Confirm room-camera localization** is publishing `map_pose` on
   its ZMQ topic and being consumed by the lab.

5. **Open the dashboard** at `http://localhost:3000/nav`.
   - On load, the status bar shows
     `pose: unknown — drag the area to set initial pose` until the
     localizer publishes its first estimate.
   - Once the localizer fires, the red robot marker appears at the
     correct map-frame position with a heading arrow. Status bar
     shows `[localizer]` as the source.

6. **Drag-to-adjust** (optional) — if the localizer estimate is off,
   click the red marker and drag it to the right pose. Drag direction
   sets the new heading. Release commits.

7. **Click-to-navigate** — click any empty point on the map and drag
   to set the goal heading. Release commits. Status bar transitions
   `idle → pending → running → OK`. Robot drives in the real world. A
   dashed green ring marks the achieved goal.

8. **Dynamic-obstacle test** — repeat #7 with someone walking through
   the planned path. nvblox should detect, Nav2 replans, robot still
   arrives. Status: `OK`.

9. **Wi-Fi-pull test** — mid-traverse, kill the lab→robot ZMQ link.
   Robot should halt within 200 ms. Status: `OBSTRUCTED` once the
   server-side timeout hits. UI does not crash.

### C.4 Phase 1 verification — CLI smoke test (secondary)

The script `backend/test_nvblox_nav.py` is a self-contained ZMQ REQ
client. It does **not** import from `app/` and **bypasses the backend
proxy entirely** — it talks straight to `nav_service`. Useful for
validating `nav_service` before the dashboard
is in the loop.

```bash
python backend/test_nvblox_nav.py <x> <y> <theta_deg> \
    --lab-host hcis-s28 --timeout 60
```

Examples:
```bash
python backend/test_nvblox_nav.py 0.5 0.0 0       # 0.5 m forward
python backend/test_nvblox_nav.py 1.5 1.0 90      # non-trivial pose
```

### C.3 Phase 2 (deferred — do not start until Phase 1 passes)

Only after the verification checklist above:

1. Add `backend/app/skills/nav.py` exposing `navigate_to(target,
   timeout_s) -> NavResult` and the `NavStatus` enum (see design
   spec for the exact shape).
2. Add `backend/nav_bridge/config/poses.yaml` with measured poses for
   `medicine`, `patient`, `origin`.
3. Modify `backend/app/workflows/medication_delivery.py`:
   - Replace `from cure.skills.navigate import navigate_skill` with
     `from app.skills.nav import navigate_to, NavStatus`.
   - In the three nav nodes (`navigate_to_pharmacy_node`,
     `navigate_to_patient_node`, `return_to_origin_node`):
     ```python
     result = navigate_to("medicine")
     if result.status != NavStatus.OK:
         return _fail("nav_to_pharmacy", "nav_failed",
                      f"導航至藥局失敗: {result.reason}",
                      f"✗ 導航至藥局失敗: {result.reason}")
     ```
4. Update `--dry-run` in `medication_delivery.py` to stub
   `navigate_to` (currently stubs `navigate_skill`).
5. Run a full delivery end-to-end with hardware.

---

## Verification checklist (gate from Phase 1 → Phase 2)

Run these in order. **Every box must be green before Phase 2 starts.**

### Robot side
- [ ] `config.yaml` has the five new ports + watchdog setting
- [ ] depth/color/camera_info/odom_tf services all PUB at expected
      rates (verify with the `ros2 topic hz` style check from B.6 or
      the smoke test in A.6)
- [ ] cmd_vel watchdog confirmed: 200 ms after PUB stops, wheels stop
- [ ] Robot driver survives a Wi-Fi drop without crashing or driving away

### Lab side
- [ ] Isaac ROS Dev container builds and the RealSense quickstart works
- [ ] nvblox builds and the offline tutorial bag runs
- [ ] `sensors_bridge` republishes ZMQ → `sensor_msgs/Image` at expected rate
- [ ] `cmdvel_bridge` republishes `/cmd_vel` → ZMQ at 20 Hz
- [ ] `nav.launch.py` brings everything up with no `[ERROR]` lines
- [ ] `ros2 topic echo /nvblox_node/static_map_slice` shows live data
- [ ] `ros2 topic echo /global_costmap/costmap` shows the static `map.pgm`
- [ ] Room-camera localizer is publishing `map → base_link` (external)

### Backend
- [ ] `curl http://localhost:9999/api/nav/map` returns map metadata
- [ ] `curl http://localhost:9999/api/nav/pose` returns `null` (or last
      stored pose)
- [ ] `POST /api/nav/pose` round-trips correctly
- [ ] `POST /api/nav/goto` with lab unreachable resolves to
      `{state:"done",status:"ROBOT_ERROR"}` — no exception, no 500

### End-to-end via dashboard (primary)
- [ ] `/nav` page loads, NavBar shows "Map Nav" highlighted
- [ ] Localizer's pose appears as the red robot marker
- [ ] Drag-to-adjust commits a new pose (status bar shows `[user]`)
- [ ] Click-and-drag goal → robot drives there → status `OK` + green
      goal ring drawn
- [ ] Same with a moving person in the path → status still `OK`
- [ ] Wi-Fi drop mid-traverse → robot halts within 200 ms; status
      shows `OBSTRUCTED` after server timeout

### End-to-end via CLI (secondary smoke test)
- [ ] `python backend/test_nvblox_nav.py 0.5 0.0 0` → `OK`
- [ ] `python backend/test_nvblox_nav.py 1.5 1.0 90` → `OK`

Once all green: open Phase 2.

---

## Troubleshooting

| Symptom | Likely cause | Where to look |
|---|---|---|
| Lab box receives ZMQ frames but `sensor_msgs` are missing | bridge node not republishing — check encoding decode (jpeg → BGR8) | `sensors_bridge.py` logs |
| nvblox node alive but no `static_map_slice` | depth camera intrinsics in `nvblox.yaml` don't match `camera_info` | compare `K` matrix |
| Nav2 won't plan — `BT navigator: Goal failed` | TF chain broken (no `map → base_link`) | `ros2 run tf2_tools view_frames` |
| Robot drives wrong direction | `linear_x`/`angular_z` axes flipped vs robot's body frame | check sign convention in `cmd_vel.py` driver service |
| Watchdog killing legitimate motion | cmd_vel publish rate < 5 Hz | check Nav2 controller loop frequency, raise `controller_frequency` |
| ZMQ HWM filling, depth frames stale | SUB consumer too slow; CONFLATE not set | confirm `setsockopt(zmq.CONFLATE, 1)` on **both** ends |
| `map → base_link` updates lag | room-camera localizer issue (out of scope here) | escalate to localizer team |
| Phase 1 script returns `TIMEOUT` immediately | nav_service not bound, or wrong host | check `ss -lntp \| grep 5560` on lab |

---

## References

- Isaac ROS getting-started:
  https://nvidia-isaac-ros.github.io/getting_started/index.html
- isaac_ros_nvblox repo:
  https://github.com/NVIDIA-ISAAC-ROS/isaac_ros_nvblox
- Nav2 docs: https://docs.nav2.org/
- Nav2 simple commander API:
  https://docs.nav2.org/commander_api/index.html
- `stretch3-zmq` repo (private): https://github.com/lnfu/stretch3-zmq
- Design spec:
  `docs/superpowers/specs/2026-05-05-nvblox-navigation-design.md`
