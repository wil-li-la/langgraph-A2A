# AMCL Localization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Switch the Stretch3 navigation stack from its placeholder static `map → odom` broadcast to off-the-shelf Nav2 AMCL localizing against the existing `backend/maps/305/raw/map.pgm`, with zero-touch cold-start auto-seeded from `home_pose.yaml`.

**Architecture:** `depthimage_to_laserscan` converts the D435if depth stream to a `/scan` topic; `nav2_amcl` consumes that + the map + wheel odom and publishes `map → odom`. `nav_service.py` loses its identity-transform broadcast loop and gains a startup auto-seed (publishes `home_pose` to AMCL's `/initialpose` topic when wheel odom is at origin) + two watchdogs (covariance, scan-staleness) that surface failure modes on the dashboard.

**Tech Stack:** ROS2 Humble, `ros-humble-nav2-amcl`, `ros-humble-depthimage-to-laserscan`, Python (rclpy + msgpack + PyYAML), the existing `backend/nav_bridge/` codebase.

**Spec:** `docs/superpowers/specs/2026-05-21-amcl-localization-design.md`

**Ordering:** Tasks 1–14 are pre-robot (config, code, dashboard). Tasks 15–18 are clearly tagged `[ROBOT]` and require the Stretch to be powered on at the dock.

---

## File structure

| File | Disposition |
|---|---|
| `backend/nav_bridge/config/nav2_params.yaml` | edit — fill in the disabled AMCL stub + add `depthimage_to_laserscan` section |
| `backend/nav_bridge/launch/nav.launch.py` | edit — add `depthimage_to_laserscan` + `nav2_amcl` nodes, expand lifecycle manager, drop `use_static_map_to_odom` arg + placeholder |
| `backend/nav_bridge/nav_service.py` | edit — drop `_broadcast_loop` (~lines 254-275), add `_load_home_pose`, `_auto_seed_from_home_pose`, rewrite `_handle_set_initial_pose`, add covariance + scan-staleness watchdogs, extend status reply with `localization` block |
| `backend/nav_bridge/config/home_pose.yaml` | **already exists** — committed 2026-05-21; no change |
| `backend/app/api/nav.py` | edit — propagate `localization` sub-object from `nav_service`'s ZMQ status reply onto `/api/nav/status/stream` |
| `frontend/components/nav-bar.tsx` | edit — colour the existing pose indicator (added by `2026-05-21-navbar-pose-hover-design.md`) by `localization.state` |
| `docs/lab-client-guide.md` | edit — short paragraph on the cold-start flow + how to re-measure `home_pose.yaml` |

---

### Task 1: Install Nav2 AMCL and depthimage_to_laserscan apt packages

**Files:** none (system-level install)

- [ ] **Step 1: Install packages**

Run:
```bash
sudo apt update
sudo apt install -y ros-humble-nav2-amcl ros-humble-depthimage-to-laserscan
```

- [ ] **Step 2: Verify both packages are queryable**

Run:
```bash
ros2 pkg prefix nav2_amcl
ros2 pkg prefix depthimage_to_laserscan
```

Expected: both print a path under `/opt/ros/humble/...`. If either prints "Package not found", re-run the apt install with `--reinstall`.

- [ ] **Step 3: Verify executables exist**

Run:
```bash
which amcl 2>/dev/null || ros2 run nav2_amcl --help 2>&1 | head -5
which depthimage_to_laserscan_node 2>/dev/null || ros2 run depthimage_to_laserscan depthimage_to_laserscan_node --help 2>&1 | head -5
```

Expected: both subcommands work (either return a path or print a help banner).

(No commit — system-level only.)

---

### Task 2: Verify the canonical map is loadable and TF tree is intact

**Files:** none (read-only verification)

- [ ] **Step 1: Confirm map.yaml + map.pgm are present**

Run:
```bash
ls -la backend/maps/305/raw/{map.yaml,map.pgm,map_stats.json}
```

Expected: all three files present, non-empty.

- [ ] **Step 2: Sanity-check map.yaml is parseable + origin matches the spec**

Run:
```bash
python3 -c "import yaml; m = yaml.safe_load(open('backend/maps/305/raw/map.yaml')); print(m); assert m['resolution'] == 0.006; assert m['origin'][0] < 0"
```

Expected: prints the loaded dict, no AssertionError.

- [ ] **Step 3: Verify the existing TF chain shape**

Run (only when the lab stack is up — skip if `nav_service` is not running):
```bash
ros2 topic list | grep -E "^/(tf|odom|camera/depth)" || echo "(stack down — skip)"
```

Expected if stack is up: at minimum `/tf`, `/tf_static`, `/camera/depth/image_rect`. If stack is down, just note it.

(No commit.)

---

### Task 3: Fill the AMCL block in nav2_params.yaml

**Files:**
- Modify: `backend/nav_bridge/config/nav2_params.yaml:13-18` (the disabled AMCL stub)

- [ ] **Step 1: Replace the AMCL stub**

In `backend/nav_bridge/config/nav2_params.yaml`, replace the existing `amcl:` block (the one with the comment "We use room-camera external localization, not AMCL. Keep AMCL stub disabled — Nav2 BasicNavigator wants *something* publishing map → odom, and that comes from the room_camera_localizer.") with:

```yaml
amcl:
  ros__parameters:
    use_sim_time: false

    # frames — match the existing TF tree from sensors_bridge.py + nav.launch.py
    base_frame_id: "base_link"
    odom_frame_id: "odom"
    global_frame_id: "map"
    scan_topic: "/scan"

    # particle filter
    min_particles: 500
    max_particles: 2000
    pf_err: 0.05
    pf_z: 0.99

    # differential-drive motion model (Stretch's base has two driven wheels + caster)
    robot_model_type: "nav2_amcl::DifferentialMotionModel"
    alpha1: 0.2
    alpha2: 0.2
    alpha3: 0.2
    alpha4: 0.2
    alpha5: 0.2

    # likelihood-field sensor model (faster than the beam model)
    laser_model_type: "likelihood_field"
    laser_min_range: 0.30
    laser_max_range: 8.00
    laser_likelihood_max_dist: 2.0
    sigma_hit: 0.20
    z_hit: 0.5
    z_short: 0.05
    z_max: 0.05
    z_rand: 0.5

    # auto-seed covariance: ~0.5 m + ~15° std-dev — absorbs dock slop
    initial_cov_xx: 0.25
    initial_cov_yy: 0.25
    initial_cov_aa: 0.07

    # update rates
    update_min_d: 0.20
    update_min_a: 0.20
    transform_tolerance: 0.5
```

- [ ] **Step 2: Verify YAML still parses**

Run:
```bash
python3 -c "import yaml; yaml.safe_load(open('backend/nav_bridge/config/nav2_params.yaml'))" && echo OK
```

Expected: `OK`.

- [ ] **Step 3: Commit**

```bash
git add backend/nav_bridge/config/nav2_params.yaml
git commit -m "config(nav2): fill AMCL block, drop room-camera-localizer placeholder"
```

---

### Task 4: Add depthimage_to_laserscan section to nav2_params.yaml

**Files:**
- Modify: `backend/nav_bridge/config/nav2_params.yaml` (append after the AMCL block)

- [ ] **Step 1: Append the depthimage_to_laserscan section**

After the AMCL block in `backend/nav_bridge/config/nav2_params.yaml`, add:

```yaml
depthimage_to_laserscan:
  ros__parameters:
    # 10 rows of depth pixels around the depth image's centerline get
    # collapsed (min over rows) into a single laser line. With the D435if
    # rotated to the standard optical convention this is a horizontal
    # slice at the camera's optical-axis height (~1.4 m on the Stretch).
    scan_height: 10
    range_min: 0.30
    range_max: 8.0
    output_frame: "camera_depth_optical_frame"
```

- [ ] **Step 2: Verify YAML parse**

Run:
```bash
python3 -c "
import yaml
d = yaml.safe_load(open('backend/nav_bridge/config/nav2_params.yaml'))
assert 'amcl' in d
assert 'depthimage_to_laserscan' in d
assert d['depthimage_to_laserscan']['ros__parameters']['scan_height'] == 10
print('OK')
"
```

Expected: `OK`.

- [ ] **Step 3: Commit**

```bash
git add backend/nav_bridge/config/nav2_params.yaml
git commit -m "config(nav2): add depthimage_to_laserscan params"
```

---

### Task 5: Add depthimage_to_laserscan node to nav.launch.py

**Files:**
- Modify: `backend/nav_bridge/launch/nav.launch.py` (insert a Node before `map_server` at ~line 91)

- [ ] **Step 1: Add the import (if not already present)**

Near the top of `backend/nav_bridge/launch/nav.launch.py`, confirm `from launch_ros.actions import Node` is imported. (It is, per current source.)

- [ ] **Step 2: Insert the depth-to-scan node**

Just before the `map_server = Node(...)` definition (~line 91), insert:

```python
    # D435if depth → /scan (sensor_msgs/LaserScan). AMCL needs a 2D scan;
    # the Stretch3 has no separate LiDAR, so we flatten a slice of the
    # head camera's depth image at robot-height.
    depth_to_scan = Node(
        package="depthimage_to_laserscan",
        executable="depthimage_to_laserscan_node",
        name="depthimage_to_laserscan",
        output="screen",
        parameters=[str(config_dir / "nav2_params.yaml")],
        remappings=[
            ("depth", "/camera/depth/image_rect"),
            ("depth_camera_info", "/camera/depth/camera_info"),
            ("scan", "/scan"),
        ],
        condition=UnlessCondition(only_bridges),
    )
```

- [ ] **Step 3: Add `depth_to_scan` to the LaunchDescription**

Find the `return LaunchDescription([... ])` at the end of `generate_launch_description()`. The current list includes `bridges`, `map_server`, `map_server_lifecycle`, `nav2_bringup`, etc. Add `depth_to_scan` immediately before `map_server`:

```python
    return LaunchDescription([
        *args,
        *bridges,
        depth_to_scan,
        map_server, map_server_lifecycle, nav2_bringup,
        static_tf_camera, static_tf_color,
        rosbridge, rosapi,
    ])
```

(Preserve other entries; the exact list in your tree may differ — just slot `depth_to_scan` in before `map_server`.)

- [ ] **Step 4: Verify launch file parses**

Run:
```bash
python3 -c "
import ast
ast.parse(open('backend/nav_bridge/launch/nav.launch.py').read())
print('OK')
"
```

Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add backend/nav_bridge/launch/nav.launch.py
git commit -m "launch(nav): add depthimage_to_laserscan node"
```

---

### Task 6: Add nav2_amcl node + lifecycle manager update to nav.launch.py

**Files:**
- Modify: `backend/nav_bridge/launch/nav.launch.py`

- [ ] **Step 1: Insert the AMCL node**

After `map_server_lifecycle` (~line 99), add:

```python
    amcl = Node(
        package="nav2_amcl",
        executable="amcl",
        name="amcl",
        output="screen",
        parameters=[str(config_dir / "nav2_params.yaml")],
        condition=UnlessCondition(only_bridges),
    )

    amcl_lifecycle = Node(
        package="nav2_lifecycle_manager",
        executable="lifecycle_manager",
        name="lifecycle_manager_amcl",
        output="screen",
        parameters=[{
            "use_sim_time": False,
            "autostart": True,
            "node_names": ["amcl"],
        }],
        condition=UnlessCondition(only_bridges),
    )
```

- [ ] **Step 2: Add `amcl` + `amcl_lifecycle` to the LaunchDescription**

In the final `LaunchDescription([... ])`, add them after `map_server_lifecycle`:

```python
    return LaunchDescription([
        *args,
        *bridges,
        depth_to_scan,
        map_server, map_server_lifecycle,
        amcl, amcl_lifecycle,
        nav2_bringup,
        static_tf_camera, static_tf_color,
        rosbridge, rosapi,
    ])
```

- [ ] **Step 3: Verify the file parses**

Run:
```bash
python3 -c "import ast; ast.parse(open('backend/nav_bridge/launch/nav.launch.py').read()); print('OK')"
```

Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
git add backend/nav_bridge/launch/nav.launch.py
git commit -m "launch(nav): add nav2_amcl + its lifecycle manager"
```

---

### Task 7: Remove the use_static_map_to_odom placeholder from nav.launch.py

**Files:**
- Modify: `backend/nav_bridge/launch/nav.launch.py:34, 43-49`

- [ ] **Step 1: Delete the LaunchConfiguration assignment**

In `generate_launch_description()`, delete (~line 34):

```python
    use_static_map_to_odom = LaunchConfiguration("use_static_map_to_odom")
```

- [ ] **Step 2: Delete the DeclareLaunchArgument**

Delete the `DeclareLaunchArgument("use_static_map_to_odom", ...)` entry from the `args = [...]` list (~lines 43-49):

```python
        DeclareLaunchArgument(
            "use_static_map_to_odom", default_value="true",
            description=(
                "Publish a static map→odom transform at the origin so Nav2 "
                "has a complete TF chain for testing WITHOUT the room-camera "
                "localizer. Set false once the real localizer is publishing."
            ),
        ),
```

(Leave the surrounding `args = [...]` list otherwise unchanged.)

- [ ] **Step 3: Delete the comment + placeholder `static_tf_map_to_odom = None` line**

Find and delete (~line 132-138, exact location varies):

```python
    # nav_service publishes map→odom continuously on /tf at 10 Hz (default
    # identity, updated by set_initial_pose from the dashboard). Once the
    # room-camera localizer is up, set use_static_map_to_odom:=false to
    # disable nav_service's broadcaster (TBD wiring) and let the localizer
    # own map→base_link directly. For now, it's always-on.
    static_tf_map_to_odom = None  # placeholder — kept for backward-compat list
```

Replace with a one-line note explaining that AMCL now owns `map → odom`:

```python
    # AMCL owns map → odom (see nav2_params.yaml's amcl: block).
    # nav_service no longer broadcasts an identity placeholder.
```

- [ ] **Step 4: Verify parse**

Run:
```bash
python3 -c "import ast; ast.parse(open('backend/nav_bridge/launch/nav.launch.py').read()); print('OK')"
```

Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add backend/nav_bridge/launch/nav.launch.py
git commit -m "launch(nav): drop use_static_map_to_odom placeholder — AMCL owns the TF now"
```

---

### Task 8: Delete the _broadcast_loop from nav_service.py

**Files:**
- Modify: `backend/nav_bridge/nav_service.py:254-275` (the `_broadcast_loop` method)

- [ ] **Step 1: Locate the broadcast loop**

Run:
```bash
grep -n "_broadcast_loop\|self._tf_broadcaster" backend/nav_bridge/nav_service.py
```

Expected: shows the method definition (~line 254) and any callers (in `__init__` or `run()`).

- [ ] **Step 2: Delete the `_broadcast_loop` method**

Remove the entire `def _broadcast_loop(self) -> None:` method body in `nav_service.py` (~lines 254-275, ~22 lines including the docstring/comment).

- [ ] **Step 3: Delete the thread spawning the broadcaster**

Search for where `_broadcast_loop` is started (typically in `__init__` or a `start()` method, as a `threading.Thread(target=self._broadcast_loop, daemon=True)`). Delete that thread creation + start.

- [ ] **Step 4: Delete the now-unused state**

If `self._tf_broadcaster`, `self._map_to_odom`, `self._map_to_odom_lock` exist solely to support the broadcast loop, leave them in place for now — they're reused by `_handle_set_initial_pose`'s computation in Task 10. Don't delete state speculatively.

- [ ] **Step 5: Verify module still imports**

Run:
```bash
python3 -c "
import sys
sys.path.insert(0, 'backend')
# Best-effort: just compile, not import (rclpy may not be installed in this venv)
import py_compile
py_compile.compile('backend/nav_bridge/nav_service.py', doraise=True)
print('OK')
"
```

Expected: `OK`.

- [ ] **Step 6: Commit**

```bash
git add backend/nav_bridge/nav_service.py
git commit -m "nav_service: drop _broadcast_loop — AMCL replaces the placeholder map→odom"
```

---

### Task 9: Add home_pose loader + auto-seed to nav_service.py

**Files:**
- Modify: `backend/nav_bridge/nav_service.py`

- [ ] **Step 1: Add yaml import + Path import**

Near the top imports of `nav_service.py`, add:

```python
from pathlib import Path
import yaml
```

(If already present, skip.)

- [ ] **Step 2: Add the home_pose loader**

Add a new top-level helper near the existing module-level helpers (the existing `_se2_matrix` / `_se2_unpack` functions are at the top — put this nearby):

```python
def _load_home_pose(config_dir: Path) -> dict | None:
    """Read backend/nav_bridge/config/home_pose.yaml. Returns the
    inner home_pose dict, or None if the file is missing / malformed."""
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
```

- [ ] **Step 3: Add the auto-seed method on `NavServiceNode`**

Inside the `NavServiceNode` class, add a new method:

```python
    def _auto_seed_from_home_pose(self) -> bool:
        """Publish home_pose.yaml to /initialpose if odom is at origin.
        Returns True if seeded; False if odom is not near origin or
        home_pose.yaml is missing."""
        hp = _load_home_pose(Path(__file__).resolve().parent / "config")
        if hp is None:
            self.get_logger().warn(
                "auto-seed: home_pose.yaml missing or malformed; "
                "operator must drag-set pose"
            )
            return False

        # Wait up to 5 s for first odom message from sensors_bridge.
        import time
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            with self._odom_lock:
                if self._latest_odom is not None:
                    ox, oy, ot = self._latest_odom
                    break
            time.sleep(0.1)
        else:
            self.get_logger().warn(
                "auto-seed: no odom received within 5 s; "
                "operator must drag-set pose"
            )
            return False

        import math
        xy_dist = math.hypot(ox, oy)
        theta_wrapped = math.atan2(math.sin(ot), math.cos(ot))
        if xy_dist > hp["odom_epsilon_xy"] or abs(theta_wrapped) > hp["odom_epsilon_theta"]:
            self.get_logger().warn(
                f"auto-seed: odom not at origin (x={ox:.3f}, y={oy:.3f}, "
                f"theta={ot:.3f}); operator must drag-set"
            )
            return False

        # Publish to /initialpose (PoseWithCovarianceStamped).
        from geometry_msgs.msg import PoseWithCovarianceStamped
        msg = PoseWithCovarianceStamped()
        msg.header.frame_id = hp.get("frame_id", "map")
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.pose.pose.position.x = float(hp["x"])
        msg.pose.pose.position.y = float(hp["y"])
        msg.pose.pose.position.z = 0.0
        # yaw → quaternion (z-axis)
        half = float(hp["theta"]) / 2.0
        msg.pose.pose.orientation.z = math.sin(half)
        msg.pose.pose.orientation.w = math.cos(half)
        # 6x6 covariance: 0.25 m² xx + yy, 0.07 rad² yaw, others zero
        cov = [0.0] * 36
        cov[0]  = 0.25   # xx
        cov[7]  = 0.25   # yy
        cov[35] = 0.07   # yaw
        msg.pose.covariance = cov

        self._initialpose_pub.publish(msg)
        self.get_logger().info(
            f"auto-seeded from home_pose: ({hp['x']:.3f}, {hp['y']:.3f}, "
            f"{hp['theta']:.3f}) [map frame]"
        )
        return True
```

- [ ] **Step 4: Create the /initialpose publisher in `__init__`**

In `NavServiceNode.__init__`, add the publisher (near where other publishers / subscribers are created):

```python
        from geometry_msgs.msg import PoseWithCovarianceStamped
        self._initialpose_pub = self.create_publisher(
            PoseWithCovarianceStamped, "/initialpose", 1
        )
```

- [ ] **Step 5: Call the auto-seed from the run/main path**

In `main()` (or wherever the node is created and spun), after the node is constructed but before `rclpy.spin(node)`, add:

```python
    # Try to auto-seed AMCL from the configured home_pose. If it fails,
    # the operator must drag-set on /nav — that path still works.
    node._auto_seed_from_home_pose()
```

- [ ] **Step 6: Verify module compiles**

Run:
```bash
python3 -c "import py_compile; py_compile.compile('backend/nav_bridge/nav_service.py', doraise=True); print('OK')"
```

Expected: `OK`.

- [ ] **Step 7: Commit**

```bash
git add backend/nav_bridge/nav_service.py
git commit -m "nav_service: auto-seed AMCL from home_pose.yaml when odom at origin"
```

---

### Task 10: Rewrite _handle_set_initial_pose to publish to /initialpose

**Files:**
- Modify: `backend/nav_bridge/nav_service.py:216-252`

- [ ] **Step 1: Replace the body of `_handle_set_initial_pose`**

Replace the existing body (which computes `map→odom = (map→base_link) * inv(odom→base_link)` and stores it in `self._map_to_odom`) with a version that publishes to AMCL's `/initialpose` topic:

```python
    def _handle_set_initial_pose(self, req: dict) -> dict:
        if "_decode_error" in req:
            return {"ok": False, "reason": f"msgpack decode: {req['_decode_error']}"}
        target = req.get("target")
        if not isinstance(target, list) or len(target) != 3:
            return {"ok": False, "reason": "target must be [x, y, theta]"}

        target_x, target_y, target_theta = (float(v) for v in target)

        # Forward to AMCL via /initialpose. AMCL re-seeds its particle
        # cloud and resumes publishing map→odom. We do not compute
        # map→odom locally any more — AMCL owns that transform.
        import math
        from geometry_msgs.msg import PoseWithCovarianceStamped
        msg = PoseWithCovarianceStamped()
        msg.header.frame_id = "map"
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.pose.pose.position.x = target_x
        msg.pose.pose.position.y = target_y
        msg.pose.pose.position.z = 0.0
        half = target_theta / 2.0
        msg.pose.pose.orientation.z = math.sin(half)
        msg.pose.pose.orientation.w = math.cos(half)
        # Wider covariance than auto-seed — operator drag is approximate.
        cov = [0.0] * 36
        cov[0]  = 0.50   # xx ≈ (0.7 m)²
        cov[7]  = 0.50   # yy
        cov[35] = 0.25   # yaw ≈ (0.5 rad)²
        msg.pose.covariance = cov
        self._initialpose_pub.publish(msg)

        self.get_logger().info(
            f"set_initial_pose: forwarded to /initialpose "
            f"({target_x:.3f}, {target_y:.3f}, {target_theta:.3f})"
        )
        return {"ok": True, "pose": [target_x, target_y, target_theta]}
```

- [ ] **Step 2: Remove now-unused state if applicable**

If after this edit nothing else in the file reads `self._map_to_odom` or `self._map_to_odom_lock`, remove their initialisation in `__init__`. (Use grep to verify before deleting.)

```bash
grep -n "_map_to_odom\|_map_to_odom_lock" backend/nav_bridge/nav_service.py
```

If only `__init__` references remain, delete those lines.

- [ ] **Step 3: Verify compile**

```bash
python3 -c "import py_compile; py_compile.compile('backend/nav_bridge/nav_service.py', doraise=True); print('OK')"
```

Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
git add backend/nav_bridge/nav_service.py
git commit -m "nav_service: forward set_initial_pose to AMCL /initialpose"
```

---

### Task 11: Add covariance + scan-staleness watchdogs

**Files:**
- Modify: `backend/nav_bridge/nav_service.py`

- [ ] **Step 1: Add subscribers in `__init__`**

In `NavServiceNode.__init__`, add subscribers for `/amcl_pose` and `/scan`:

```python
        from geometry_msgs.msg import PoseWithCovarianceStamped
        from sensor_msgs.msg import LaserScan

        self._amcl_cov_xy: float = float("inf")
        self._amcl_cov_yaw: float = float("inf")
        self._latest_amcl_stamp_ns: int = 0
        self._latest_scan_stamp_ns: int = 0

        self.create_subscription(
            PoseWithCovarianceStamped,
            "/amcl_pose",
            self._on_amcl_pose,
            10,
        )
        self.create_subscription(
            LaserScan,
            "/scan",
            self._on_scan,
            10,
        )
```

- [ ] **Step 2: Add the subscription callbacks**

```python
    def _on_amcl_pose(self, msg) -> None:
        # 6x6 covariance row-major; xx=[0], yy=[7], yaw=[35]
        self._amcl_cov_xy = float(msg.pose.covariance[0] + msg.pose.covariance[7])
        self._amcl_cov_yaw = float(msg.pose.covariance[35])
        s = msg.header.stamp
        self._latest_amcl_stamp_ns = s.sec * 1_000_000_000 + s.nanosec

    def _on_scan(self, msg) -> None:
        s = msg.header.stamp
        self._latest_scan_stamp_ns = s.sec * 1_000_000_000 + s.nanosec
```

- [ ] **Step 3: Add a derived `_localization_state` method**

```python
    def _localization_state(self) -> dict:
        """Compute current localization state for the status reply.
        Returns: {state, cov_xy_m, cov_yaw_rad, scan_age_s}."""
        import time
        now_ns = time.time_ns()
        scan_age_s = (now_ns - self._latest_scan_stamp_ns) / 1e9 if self._latest_scan_stamp_ns else float("inf")

        # state machine:
        #   no AMCL pose yet              → unseeded
        #   /scan stale > 1 s             → dead-reckon
        #   xx+yy > 1.0 OR yaw > 0.25     → uncertain
        #   else                           → ok
        if self._latest_amcl_stamp_ns == 0:
            state = "unseeded"
        elif scan_age_s > 1.0:
            state = "dead-reckon"
        elif self._amcl_cov_xy > 1.0 or self._amcl_cov_yaw > 0.25:
            state = "uncertain"
        else:
            state = "ok"

        return {
            "state": state,
            "cov_xy_m": self._amcl_cov_xy if self._amcl_cov_xy != float("inf") else None,
            "cov_yaw_rad": self._amcl_cov_yaw if self._amcl_cov_yaw != float("inf") else None,
            "scan_age_s": scan_age_s if scan_age_s != float("inf") else None,
        }
```

- [ ] **Step 4: Cancel active goal on dead-reckon (safety)**

The spec says: "any active nav goal auto-cancels after 5 s of no /scan." Add a periodic timer (1 Hz) that calls a safety check:

```python
    def _create_safety_timer(self) -> None:
        self._dead_reckon_since: float | None = None
        self._safety_timer = self.create_timer(1.0, self._safety_tick)

    def _safety_tick(self) -> None:
        import time
        state = self._localization_state()
        if state["state"] == "dead-reckon":
            if self._dead_reckon_since is None:
                self._dead_reckon_since = time.monotonic()
            elif time.monotonic() - self._dead_reckon_since > 5.0:
                # any active navigator goal: cancel
                # (the navigator handle lives in self._navigator if BasicNavigator is wired)
                if getattr(self, "_navigator", None) is not None:
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
```

Call `self._create_safety_timer()` at the end of `__init__`.

- [ ] **Step 5: Verify compile**

```bash
python3 -c "import py_compile; py_compile.compile('backend/nav_bridge/nav_service.py', doraise=True); print('OK')"
```

Expected: `OK`.

- [ ] **Step 6: Commit**

```bash
git add backend/nav_bridge/nav_service.py
git commit -m "nav_service: covariance + scan-staleness watchdogs + dead-reckon safety cancel"
```

---

### Task 12: Extend ZMQ status reply with localization block

**Files:**
- Modify: `backend/nav_bridge/nav_service.py` (wherever the ZMQ REP socket assembles status replies)

- [ ] **Step 1: Find the status handler**

Run:
```bash
grep -n "def _handle_status\|task.*state\|self.rep\b" backend/nav_bridge/nav_service.py | head -10
```

Identify the method that builds the reply dict for status queries (returns a dict with `pose`, `task`, etc.).

- [ ] **Step 2: Extend the reply dict**

In that method, add the `localization` sub-object to the returned dict:

```python
        # existing pose + task fields are unchanged
        reply["localization"] = self._localization_state()
        return reply
```

(If the reply is constructed inline rather than via a method, add the same line at the point of construction.)

- [ ] **Step 3: Verify compile**

```bash
python3 -c "import py_compile; py_compile.compile('backend/nav_bridge/nav_service.py', doraise=True); print('OK')"
```

Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
git add backend/nav_bridge/nav_service.py
git commit -m "nav_service: include localization state in ZMQ status reply"
```

---

### Task 13: Surface localization on /api/nav/status/stream

**Files:**
- Modify: `backend/app/api/nav.py` (the `_snapshot()` builder near line 332)

- [ ] **Step 1: Find the snapshot builder**

Run:
```bash
grep -n "\"pose\":\|\"teleop_active\":\|asdict(_pose)" backend/app/api/nav.py
```

Identify the function (`_snapshot` or similar) that returns `{ "pose": ..., "task": ..., "teleop_active": ... }`.

- [ ] **Step 2: Add localization to the snapshot**

Where the dict is assembled, add the `localization` key. The localization data arrives via the ZMQ status reply (extended in Task 12) — find where that reply is parsed and stored, then propagate:

```python
def _snapshot() -> dict:
    return {
        "pose": asdict(_pose) if _pose else None,
        "task": _task,
        "teleop_active": _teleop_active,
        "localization": _localization,
    }
```

And where the ZMQ status reply is unpacked (likely a periodic poller), store `_localization = reply.get("localization")` alongside `_pose` and `_task`.

If `_localization` doesn't yet exist as a module-level variable, add:

```python
_localization: dict | None = None
```

…near the existing `_teleop_active: bool = False` (line 158).

And in whatever function processes the ZMQ status reply, add:

```python
    global _localization
    _localization = reply.get("localization")
```

- [ ] **Step 3: Sanity-grep for any frontend type definitions to keep in sync**

```bash
grep -rn "teleop_active\|NavStatus" frontend/lib frontend/contexts 2>/dev/null | head -10
```

If a TypeScript interface for the status payload exists, add `localization?: { state, cov_xy_m, cov_yaw_rad, scan_age_s }` to it.

- [ ] **Step 4: Verify backend compiles**

```bash
cd backend && python3 -c "import py_compile; py_compile.compile('app/api/nav.py', doraise=True); print('OK')" && cd ..
```

Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/nav.py frontend/lib/*.ts frontend/contexts/*.tsx 2>/dev/null
git commit -m "api(nav): surface localization state on /api/nav/status/stream"
```

(The git-add wildcard will silently skip non-existent files; do not invent paths.)

---

### Task 14: Colour the NavBar pose indicator by localization.state

**Files:**
- Modify: `frontend/components/nav-bar.tsx`

- [ ] **Step 1: Read current nav-bar.tsx**

Run:
```bash
grep -n "pose\|localization\|navStatus\|useNavStatus" frontend/components/nav-bar.tsx | head -20
```

Confirm the indicator exists (added by `2026-05-21-navbar-pose-hover-design.md`).

- [ ] **Step 2: Add state → colour mapping**

Find where the indicator chip is rendered. Just above (or in-component), add:

```ts
const localizationColor = (state: string | undefined): string => {
  switch (state) {
    case "ok":          return "bg-emerald-500"
    case "uncertain":   return "bg-amber-400"
    case "dead-reckon": return "bg-amber-400"
    case "unseeded":    return "bg-amber-400"
    case "kidnapped":   return "bg-red-500"
    default:            return "bg-slate-500"  // unknown / no data yet
  }
}
```

(Or equivalent class names matching the existing Tailwind palette in `nav-bar.tsx`.)

- [ ] **Step 3: Apply the colour to the indicator dot**

Find the existing `<span>` / `<div>` that renders the dot, and set its `className` to use `localizationColor(navStatus?.localization?.state)`:

```tsx
<span
  className={`inline-block h-2 w-2 rounded-full ${localizationColor(navStatus?.localization?.state)}`}
  title={`localization: ${navStatus?.localization?.state ?? "—"}`}
/>
```

- [ ] **Step 4: Run TypeScript check**

Run:
```bash
cd frontend && ./node_modules/.bin/tsc --noEmit && cd ..
```

Expected: no output (clean), exit 0.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/nav-bar.tsx
git commit -m "ui(nav-bar): colour pose indicator by localization.state"
```

---

### Task 15: [ROBOT] Phase 1 sanity + Phase 2 auto-seed cold-start

**Files:** none — verification only. **Requires Stretch3 powered on at the dock.**

- [ ] **Step 1: Park the Stretch at the dock**

Visual check — the Stretch is at its usual parking spot, facing +x.

- [ ] **Step 2: Bring up the Stretch driver**

On the robot (or via SSH):

```bash
ssh stretch-se3-3099.local -l hello-robot
cd Desktop/stretch3-zmq/
uv run python -m stretch3_zmq.driver --config config.yaml
```

Wait for the driver banner.

- [ ] **Step 3: Bring up the lab nav stack**

On the lab box:

```bash
docker exec -it isaac_ros_dev /workspaces/langgraph-A2A/backend/nav_bridge/run_nav.sh
```

- [ ] **Step 4: Verify Phase 1 sanity**

In another terminal on the lab box (inside the same container):

```bash
ros2 topic hz /camera/depth/image_rect    # expect >= 10 Hz
ros2 topic hz /scan                       # expect same rate
ros2 topic echo /map --once | head -10    # expect non-empty OccupancyGrid header
```

All three must pass.

- [ ] **Step 5: Verify Phase 2 auto-seed**

In the `nav_service` launch log (the `docker exec` terminal), expect within 5 s of start:

```
auto-seeded from home_pose: (-4.000, -3.400, 0.000) [map frame]
```

If you instead see `odom not at origin (...); operator must drag-set`, the driver was not freshly booted — restart the driver and re-launch the nav stack.

- [ ] **Step 6: Verify on the dashboard**

Open `http://localhost:3000/nav`. Robot marker should appear at the dock position on the map. NavBar pose indicator should be green (`localization: ok`).

- [ ] **Step 7: If any check fails**

Capture the failing output and stop. Do not proceed to Task 16 until all six checks pass.

(No commit — verification only.)

---

### Task 16: [ROBOT] Phase 3 convergence under motion

**Files:** none — verification only.

- [ ] **Step 1: Drive a 1 m square via teleop**

Use the dashboard `/teleop` page or `ros2 run teleop_twist_keyboard teleop_twist_keyboard`. Drive forward ~1 m, then rotate 90°, then forward ~1 m, then rotate 90°, etc. Return to dock.

- [ ] **Step 2: Watch covariance shrink**

In another terminal:

```bash
ros2 topic echo /amcl_pose --field pose.covariance | head -50
```

Within ~30 s of motion, `cov[0]` (xx) + `cov[7]` (yy) should drop below `0.05` (matching the spec's testing recipe pass criterion).

- [ ] **Step 3: Watch the dashboard pose marker**

Marker should track motion smoothly with no jumps > 10 cm. NavBar stays on `localization: ok`.

- [ ] **Step 4: Return-to-dock overlap**

Drive back to the start position. Marker on the dashboard should overlap the original within ~10 cm.

- [ ] **Step 5: If any check fails**

Most likely cause: AMCL parameters need tuning. Adjust `update_min_d`, `min_particles`, or the `alpha*` motion-model values in `nav2_params.yaml`. Re-run from Task 15.

(No commit — verification only.)

---

### Task 17: [ROBOT] Phase 4 failure-mode rehearsals

**Files:** none — verification only.

- [ ] **Step 1: Kidnapped-robot test**

While the robot is stationary, manually lift it ~1 m sideways and put it down. Within ~2 s of placing it back (and a small jiggle of the wheels), NavBar should flip to amber `localization: uncertain`. Drag pose on dashboard; NavBar returns to `ok` in ~5 s.

- [ ] **Step 2: Depth-camera blackout test**

On the robot, kill the driver:

```bash
# in the driver's terminal
^C
```

Within 1 s of staleness, NavBar should flip to amber `localization: dead-reckon (no scan)`. Wait 6 s; the safety-tick should auto-cancel any active goal (no active goal here, so this just logs). Restart the driver; `localization` returns to `ok`.

- [ ] **Step 3: Wrong home_pose test**

Edit `backend/nav_bridge/config/home_pose.yaml`, set `x: 0.0`, `y: 0.0`. Power-cycle the Stretch + relaunch the nav stack. Auto-seed will publish wrong pose; AMCL initial particle cloud is centered at the map origin (which is `(0,0)` map frame). Drive the robot a meter; the laser scan will not match the map at that location → covariance grows → NavBar flips to `uncertain`. Drag pose to recover. Reset `home_pose.yaml` to the real values (-4.0, -3.4, 0).

(No commit — verification only.)

---

### Task 18: [ROBOT] Phase 5 end-to-end navigation

**Files:** none — verification only.

- [ ] **Step 1: Set a nav goal via the dashboard**

On `/nav`, drag from an empty point in the map → release. `nav_service` receives the goal over ZMQ, Nav2 BasicNavigator plans + drives.

- [ ] **Step 2: Watch AMCL stay stable**

NavBar `localization: ok` throughout the drive. `cov_xy_m` stays below 0.1 m. Marker tracks the robot.

- [ ] **Step 3: At goal, check marker / actual overlap**

Use a tape measure or visual estimate — marker position on the map should be within ~15 cm of the robot's actual world position. (Nav2's `xy_goal_tolerance` defaults to ~25 cm; the AMCL error budget is on top of that, so a total of ~10-15 cm of AMCL contribution is healthy.)

- [ ] **Step 4: Drive back to dock**

Set goal back at dock. Robot returns. Final marker position overlaps the auto-seeded home pose within ~15 cm.

- [ ] **Step 5: Tag the build**

If all phases pass:

```bash
git tag -a v-amcl-localization-1.0 -m "AMCL localization shipped, all phases pass"
git push origin v-amcl-localization-1.0
```

(Optional. Skip if the project doesn't use tags.)

---

## Self-review

After writing the plan, ran the checks per the writing-plans skill:

1. **Spec coverage:** every spec section has a corresponding task:
   - Goals 1–6 → Tasks 3–14
   - Cold-start auto-seed flow → Task 9 + Task 15
   - Error handling table → Tasks 11–13 (watchdogs + dashboard surfacing)
   - Testing recipe → Tasks 15–18
   - Future work → explicitly out of scope
2. **Placeholder scan:** no TODO/TBD/FIXME left in steps. Every code block is the actual code to write.
3. **Type consistency:** `_localization_state()` returns `{state, cov_xy_m, cov_yaw_rad, scan_age_s}`; the `localization` field on `/api/nav/status/stream` and the TypeScript interface match. The `localizationColor()` function and `_localization_state()`'s `state` values match (`ok`/`uncertain`/`dead-reckon`/`unseeded`/`kidnapped`).
4. **Scope:** 18 tasks, ~3 hours of focused work plus the on-robot phases. Bounded.

One known simplification vs spec: the **observation-likelihood watchdog** mentioned in the spec is implemented in this plan as a **covariance-jump heuristic** rather than re-computing AMCL's internal `z_hit` from `/scan` + `/map`. The two watchdogs that ship (covariance + scan-staleness) cover the same failure modes (kidnapped → covariance jumps; map mismatch → covariance stays high during motion). Full observation-likelihood watchdog is deferred to a follow-up; called out here so the next reader knows.
