# Lab-side nav bridges (nvblox / Nav2 ↔ stretch3-zmq)

Three rclpy nodes that translate between the robot's ZMQ surface
(`docs/lab-client-guide.md`) and the lab box's local DDS, where Nav2
and nvblox actually run. **Sibling to `room_cameras/`**: separate
process group, separate ROS_DOMAIN_ID, doesn't pollute the backend
Python 3.12 venv.

Lives at `backend/nav_bridge/` so it's discoverable next to the rest of
the backend, but it runs under the system ROS2 Humble Python 3.10 — not
under `backend/.venv`. Mixing rclpy C extensions with the backend's
3.12 venv breaks `import rclpy`.

## What's in here

```
nav_bridge/
├── sensors_bridge.py     ZMQ → ROS  (depth, color, camera_info, odom_tf, /tf)
├── cmdvel_bridge.py      ROS → ZMQ  (/cmd_vel → robot:6014, 200ms watchdog)
├── nav_service.py        ZMQ REP    (backend /api/nav/goto → Nav2 BasicNavigator)
├── run_bridges.sh        launcher (sources ROS2 itself)
├── launch/nav.launch.py  full stack (bridges + map_server + Nav2)
└── config/
    ├── nav2_params.yaml  Nav2 controller / planner / costmaps
    ├── nvblox.yaml       nvblox voxel size, depth topics (loaded once nvblox is built)
    └── poses.yaml        Phase-2 named poses (medicine / patient / origin)
```

## One-time prereqs (lab box `hcis-s28`)

Ubuntu 22.04 + ROS2 Humble + NVIDIA GPU (Compute ≥ 7.5) are already in
place. Install the Nav2 packages we need:

```bash
sudo apt update
sudo apt install -y \
  ros-humble-nav2-bringup \
  ros-humble-nav2-map-server \
  ros-humble-nav2-simple-commander \
  ros-humble-nav2-lifecycle-manager
```

Python deps for the bridges:
```bash
pip3 install --user pyzmq msgpack opencv-python numpy
```

For nvblox itself, follow `docs/nvblox-integration-guide.md` Part B —
that's a Docker-based Isaac ROS Dev container install and is a
larger one-time job. **Phase 1a (this README) does not need nvblox**;
the bridges + Nav2 + static map work on their own.

## Phase 1a — bridges only (no Nav2, no nvblox)

Verifies the ZMQ ↔ ROS plumbing.

```bash
# 1. Robot driver up (on the robot)
#    ssh stretch-se3-3099.local -l hello-robot
#    cd Desktop/stretch3-zmq && ./start.sh

# 2. Bridges up (lab box, this directory)
./run_bridges.sh
```

Verify in another shell:
```bash
source /opt/ros/humble/setup.bash
export ROS_DOMAIN_ID=37   # match run_bridges.sh

ros2 topic list                         # should include /camera/depth/image_rect_raw etc.
ros2 topic hz /camera/depth/image_rect_raw  # ~15 Hz
ros2 topic hz /camera/color/image_raw       # ~15 Hz
ros2 topic echo /tf --once              # odom→base_link transform

# Send a fake cmd_vel via ROS to verify the bridge to the robot:
ros2 topic pub /cmd_vel geometry_msgs/Twist \
  '{linear: {x: 0.05}, angular: {z: 0.0}}' --rate 20
# Robot should creep forward; Ctrl-C to stop, robot halts within 200ms.
```

## Phase 1b — full stack (Nav2 + map server, no nvblox yet)

```bash
ros2 launch backend/nav_bridge/launch/nav.launch.py
```

This brings up: 3 bridges + 2 static TFs + map_server (loading
`backend/maps/305/map.yaml`) + nav2_bringup. nvblox is **not** added
to the local costmap yet — the only obstacle source is the static map.

Confirm the static map shows up:
```bash
ros2 topic echo /map --once | head -20
```

Then test from the dashboard:
1. Restart the backend (`python -m app`); it forwards to `localhost:5560`
2. Open `http://localhost:3000/nav`
3. Drag-to-set the initial pose (room-camera localizer not in the loop yet)
4. Click-and-drag a goal — Nav2 should plan a path against the static
   map and send `/cmd_vel` to drive the robot

## Phase 1c — add nvblox

After Part B of `docs/nvblox-integration-guide.md`:

1. Start the nvblox node from the Isaac ROS container, sharing the
   host network so it sees the same ROS topics as the bridges
2. Uncomment the `nvblox_layer` line in `config/nav2_params.yaml`
   under `local_costmap.plugins`
3. Restart `nav.launch.py`

Now the local costmap reflects live obstacle reconstruction.

## Configuration

- `ROS_DOMAIN_ID`: defaults to **37** (room_cameras uses 42; backend
  agent uses none). Override with `ROS_DOMAIN_ID=N ./run_bridges.sh`.
- `--robot HOST`: each bridge takes `--robot 192.168.1.38` (default).
  Override per shell, or pass `./run_bridges.sh --robot HOST`.
- `nav_service` binds `tcp://*:5560`. Override port with `--port`.

## Live ROS data for the dashboard (rosbridge)

The launch file spawns a `rosbridge_websocket` node on port **9090** by
default (rosbridge's canonical port; override with `rosbridge_port:=N`).
It exposes every ROS2 topic over JSON-over-WebSocket — `/tf`, `/map`,
`/local_costmap/costmap`, the nvblox mesh + ESDF, `/cmd_vel_nav`, etc.
Two consumers:

1. **Dashboard `/nav` page** subscribes via `roslib` (the canonical JS
   client) and renders live overlays (nvblox 2D slice, costmaps, /plan
   polyline) on top of the room-305 map.
2. **Lichtblick on `/viz`** uses the same WS — configure the
   "Rosbridge (ROS 1 & 2)" data source pointed at this URL — for 3D
   mesh inspection.

**Why rosbridge and not foxglove_bridge:** the `ros-humble-foxglove-bridge`
3.x apt package was rewritten on top of Foxglove's commercial Rust SDK
and only negotiates subprotocol `foxglove.sdk.v1`. The open-source
`@foxglove/ws-protocol` JS client (last released v0.8.0 before Foxglove
sunset it) only knows `foxglove.websocket.v1` and gets HTTP 400'd at the
handshake. rosbridge has been the boring-default ROS↔WS bridge for
~10 years, FOSS, and Lichtblick speaks it natively.

**Local LAN access (quickest):** in `frontend/.env.local`,
```
NEXT_PUBLIC_ROSBRIDGE_WS_URL=ws://192.168.1.100:9090
```

**Production (Cloudflare-tunneled, preferred):** add a route to your
`cloudflared` config:
```yaml
ingress:
  - hostname: stretch-fg.<your-domain>
    service: ws://localhost:9090
```
Then in `frontend/.env.local` (or the Cloudflare Pages env vars):
```
NEXT_PUBLIC_ROSBRIDGE_WS_URL=wss://stretch-fg.<your-domain>
```

**Disable rosbridge:** comment out the `rosbridge` Node in
`nav.launch.py` (or set `rosbridge_port:=0` and let the bind fail
loudly).

**Bandwidth note:** OccupancyGrids can run a few MB per update. roslib
only requests topics the dashboard actually subscribes to (default
layer set: nvblox 2D slice + local costmap + path), so the ambient
load is small. To clamp further, configure `topics_glob` in
rosbridge's params.

## Cross-references

- `docs/lab-client-guide.md` — robot-side ZMQ wire formats (the spec
  these bridges consume)
- `docs/nvblox-integration-guide.md` — three-team how-to + nvblox install
- `docs/superpowers/specs/2026-05-05-nvblox-navigation-design.md` — design
- `backend/app/api/nav.py` — the backend proxy that forwards
  `/api/nav/goto` to `nav_service` here
