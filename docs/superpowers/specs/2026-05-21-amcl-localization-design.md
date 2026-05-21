# AMCL localization for the Stretch3

**Date:** 2026-05-21
**Status:** Design — awaiting user review before implementation plan

## Problem

`backend/nav_bridge/launch/nav.launch.py` ships with a `use_static_map_to_odom`
launch arg (default `true`) and a comment that says
*"Set false once the real localizer is publishing."* The "real localizer"
is the missing piece. Today `nav_service.py:254–275` broadcasts an
identity `map → odom` transform continuously so Nav2's TF chain is
complete and the dashboard's drag-to-set flow can write an initial pose
into that static transform. Functional, but the robot doesn't actually
know where it is — it just trusts whatever the operator last dragged.

`backend/nav_bridge/config/nav2_params.yaml` reinforces the placeholder
with a stub block:
```yaml
amcl:
  ros__parameters:
    # We use room-camera external localization, not AMCL. Keep AMCL stub
    # disabled — Nav2 BasicNavigator wants *something* publishing
    # map → odom, and that comes from the room_camera_localizer.
    use_sim_time: false
```
…which was the original plan: the 13 overhead Basler cameras would
detect Stretch's factory ArUco markers (IDs 130/131 base, 134 shoulder,
`DICT_6X6_250`) and publish `map → base_link` directly.

That plan was measured and ruled out. With factory marker sizes
(47 mm base, 31.4 mm shoulder), ceiling cameras at ~3 m, vertical-face
mounting on the chassis, and H.264 compression at 2 Mbps, **zero
Stretch markers were detected across all 13 cameras in any ArUco
dictionary**. Foreshortened projection of the vertical markers
collapses to ~5–10 pixels in the best case; `DICT_6X6_250` needs ~15+.

This spec switches to off-the-shelf **Nav2 AMCL** as the localizer.
Room-camera localization stays as a deferred future augmentation,
viable only after larger horizontal markers get added to the top of the
Stretch's chassis (out of scope here).

## Goals

1. The Stretch can self-localize against `backend/maps/305/raw/map.pgm`
   anywhere in the lab without ceiling-camera assistance.
2. **Zero-touch cold start** in the common case: powering on the
   Stretch at its usual parking spot → AMCL auto-seeded from
   `home_pose.yaml` → Nav2 ready within ~5 s of `stretch3-zmq` driver
   boot, no operator drag required.
3. Failure modes — lost robot, kidnapped robot, depth-camera blackout,
   map–reality mismatch — are detected and surfaced on the dashboard
   via the **existing** `/api/nav/status/stream`, with a clear
   user-recoverable path for each.
4. No re-mapping. The existing `map.pgm` and `map.yaml` are reused as-is.
5. Implementation reuses off-the-shelf Nav2 packages
   (`nav2_amcl`, `depthimage_to_laserscan`). No custom Python localizer code.
6. Existing Nav2 BasicNavigator + dashboard `/nav` page + NavBar pose
   indicator (today's `2026-05-21-navbar-pose-hover-design.md`) keep
   working unchanged — they already consume `/tf map→…` via rosbridge.

## Non-goals

- **Live room-camera localization** (deferred — see "Future work").
- **EKF fusion of multiple pose sources** (`robot_localization`). AMCL
  alone for now; only revisit if cam augmentation comes back.
- **Auto-recovery driving** if AMCL gets lost. A confused robot in a
  lab full of people should stop, not drive itself.
- **Re-mapping the room.** The existing `305/raw/map.{pgm,yaml}` is
  authoritative.
- **Multi-floor / elevator / multi-robot.** Single-floor, single-robot,
  ED305 only.
- **AMCL parameter tuning beyond reasonable defaults.** The values in
  this spec are starting points; actual tuning is part of the testing
  recipe, not the design.

## Architecture

```
┌──────────────────────── Stretch3 (robot) ──────────────────────────────┐
│  stretch3-zmq driver:                                                  │
│    PUB depth + camera_info + odom_tf (D435if, 848×480 @ ~15 Hz)        │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │  ZMQ over Wi-Fi
                                   ▼
┌──────────────────────── lab box (ROS2 Humble) ─────────────────────────┐
│                                                                        │
│  sensors_bridge.py (existing — unchanged):                             │
│    /camera/depth/image_rect  +  /camera/depth/camera_info              │
│    /tf  odom → base_link                                               │
│                                                                        │
│  depthimage_to_laserscan (new — ros-humble-depthimage-to-laserscan):    │
│    /camera/depth/image_rect → /scan (sensor_msgs/LaserScan)            │
│    scan_height = 10 rows around centerline @ robot height              │
│                                                                        │
│  map_server (existing):                                                │
│    loads backend/maps/305/raw/map.yaml                                 │
│    publishes /map (latched)                                            │
│                                                                        │
│  nav2_amcl (new — ros-humble-nav2-amcl):                                │
│    in:  /scan + /map + /tf odom→base_link                              │
│    out: /tf  map → odom   (corrects odom drift continuously)            │
│         /amcl_pose (PoseWithCovarianceStamped)                         │
│    init: /initialpose (set at startup by nav_service auto-seed,         │
│           or by operator drag from dashboard)                          │
│                                                                        │
│  nav_service.py (trimmed):                                             │
│    - DROP _broadcast_loop (the placeholder identity map→odom)          │
│    - NEW startup hook: read home_pose.yaml; if first odom ≈ origin,    │
│        publish home_pose to /initialpose                               │
│    - KEEP _handle_set_initial_pose (now forwards to /initialpose       │
│        instead of setting internal static TF)                          │
│    - KEEP go_to + clear_costmaps                                       │
│    - NEW: watchdog tasks for covariance / scan staleness /             │
│        observation likelihood; surfaces state on /api/nav/status/stream│
│                                                                        │
│  Nav2 BasicNavigator + costmaps (existing — unchanged):                │
│    picks up /tf map→odom from AMCL automatically                       │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼  rosbridge_websocket → SSE
┌──────────────────────── dashboard ─────────────────────────────────────┐
│  /nav page             — drag-to-set + drag-to-goal (unchanged)        │
│  NavBar pose indicator — adds localization.state tag (one-line change) │
└────────────────────────────────────────────────────────────────────────┘
```

### Key choices

| Decision | Value | Reasoning |
|---|---|---|
| Localizer | `nav2_amcl` | Hello Robot's documented choice; lowest setup friction; existing `.pgm` map reused as-is. Tradeoff: under-uses D435if's RGB-D (single laser slice only). |
| Scan source | `depthimage_to_laserscan` on `/camera/depth/image_rect` | D435if is the only ranging sensor onboard; no separate LiDAR. |
| Map | `backend/maps/305/raw/map.yaml` (no re-baking) | Pre-built, already calibrated (6 mm/px, baked origin + rotation). |
| Initial pose | Auto-seed from `home_pose.yaml` when odom ≈ (0,0,0); operator-drag fallback otherwise | Common case is zero-touch; pathological case still recoverable. |
| `map → odom` ownership | `nav2_amcl` exclusively | Drops `nav_service._broadcast_loop`; eliminates dual-broadcaster TF conflict. |

### Alternative localizers considered

| Option | Outcome |
|---|---|
| `slam_toolbox` localization mode | Better robustness than AMCL with good odom; but requires re-mapping to produce `.posegraph` (existing `.pgm` map can't be reused). Deferred unless AMCL turns out to be inadequate after testing. |
| `RTAB-Map` localization mode | Uses D435if RGB-D natively — the only option that actually exploits the sensor. Best long-term answer if AMCL is too brittle in featureless / dynamic spaces. Deferred to a possible v2. |
| Room-camera ArUco fusion | The original plan. Killed by measurement: zero Stretch-marker detections from 13 ceiling cameras at 3 m with 47 mm vertical-face markers. Can return when larger horizontal markers are added on the chassis top. |
| `FUNMAP` (Hello Robot custom) | Not a localizer in the AMCL sense — manipulation/planning oriented. No `map → odom` published. Not appropriate. |
| `Cartographer` | Declining maintenance; `slam_toolbox` superseded it for new deployments. |

## Components & files

### System packages (one-time apt install)

```bash
sudo apt install -y \
  ros-humble-nav2-amcl \
  ros-humble-depthimage-to-laserscan
```

### File changes

| Path | Change | Purpose |
|---|---|---|
| `backend/nav_bridge/launch/nav.launch.py` | edit | Add `depthimage_to_laserscan` + `nav2_amcl` nodes; delete `use_static_map_to_odom` launch arg + the placeholder static-transform; update lifecycle-manager `node_names` to include `amcl`. |
| `backend/nav_bridge/nav_service.py` | trim | Drop `_broadcast_loop` (lines 254–275). Add `_auto_seed_from_home_pose` (reads `home_pose.yaml`, publishes to `/initialpose` if odom ≈ origin at launch). `_handle_set_initial_pose` now forwards to `/initialpose` topic instead of overwriting an internal static TF. Add three watchdog tasks: covariance, scan-staleness, observation-likelihood. |
| `backend/nav_bridge/config/nav2_params.yaml` | fill | Replace the disabled AMCL stub with a real AMCL config (values below). |
| `backend/nav_bridge/config/home_pose.yaml` | already created | Charging-dock pose: `x=-4.0, y=-3.4, θ=0` in map frame. |
| `backend/maps/305/raw/{map.yaml, map.pgm}` | no change | Existing map, AMCL-compatible. |
| `backend/app/api/nav.py` | small | Read `nav_service`'s ZMQ status reply, surface `localization: {state, cov_xy_m, cov_yaw_rad, scan_age_s}` on `/api/nav/status/stream` alongside the existing `{ pose, task, teleop_active }`. |
| `frontend/components/nav-bar.tsx` | small | Colour the NavBar pose indicator (added by `2026-05-21-navbar-pose-hover-design.md`, prerequisite) green / amber / red based on `localization.state`. |

### AMCL parameters (`nav2_params.yaml` shape)

```yaml
amcl:
  ros__parameters:
    use_sim_time: false

    # frames — match the existing TF tree
    base_frame_id: "base_link"
    odom_frame_id: "odom"
    global_frame_id: "map"
    scan_topic: "/scan"

    # particle filter
    min_particles: 500
    max_particles: 2000
    pf_err: 0.05
    pf_z: 0.99

    # differential-drive motion model — Stretch's base is differential
    robot_model_type: "nav2_amcl::DifferentialMotionModel"
    alpha1: 0.2
    alpha2: 0.2
    alpha3: 0.2
    alpha4: 0.2
    alpha5: 0.2

    # likelihood-field sensor model (faster than beam model)
    laser_model_type: "likelihood_field"
    laser_min_range: 0.30
    laser_max_range: 8.00
    laser_likelihood_max_dist: 2.0
    sigma_hit: 0.20
    z_hit: 0.5
    z_short: 0.05
    z_max: 0.05
    z_rand: 0.5

    # auto-seed cov: 0.5 m + ~15° std-dev — enough to absorb dock slop
    initial_cov_xx: 0.25
    initial_cov_yy: 0.25
    initial_cov_aa: 0.07

    # update rates
    update_min_d: 0.20
    update_min_a: 0.20
    transform_tolerance: 0.5
```

### depthimage_to_laserscan parameters

```yaml
depthimage_to_laserscan:
  ros__parameters:
    scan_height: 10
    range_min: 0.30
    range_max: 8.0
    output_frame: "camera_depth_optical_frame"
```

Subscribes to `/camera/depth/image_rect` + `/camera/depth/camera_info`
(both published by `sensors_bridge.py`); publishes `/scan`.

## Cold-start auto-seed flow

```python
# nav_service.py — startup hook

on_launch():
    home_pose = load_yaml(NAV_BRIDGE_CONFIG / "home_pose.yaml")["home_pose"]
    deadline = now() + 5.0
    while now() < deadline:
        if latest_odom is not None:
            ox, oy, ot = latest_odom
            xy_ok    = sqrt(ox**2 + oy**2)            < home_pose["odom_epsilon_xy"]
            theta_ok = abs(wrap_angle(ot))            < home_pose["odom_epsilon_theta"]
            if xy_ok and theta_ok:
                publish_pose_with_covariance_to(
                    topic="/initialpose",
                    pose=(home_pose["x"], home_pose["y"], home_pose["theta"]),
                    cov=diag([0.25, 0.25, 0.0, 0.0, 0.0, 0.07]),
                )
                log_info("auto-seeded from home_pose")
                state = "ok"
                return
        sleep(0.1)
    log_warn("odom not at origin; operator must drag-set")
    state = "unseeded"
```

In the common case (Stretch power-on at dock → driver boot → odom at
origin), AMCL has `/initialpose` within ~100 ms of `nav_service`
finishing startup. AMCL converges over the first few decimeters of
motion. Dashboard pose indicator: `localization: ok` from the start.

`home_pose.yaml` values for ED305 dock:
- `x = -4.0` m (map frame, relative to map origin at `[-6.0480, -4.6439, 0.0]`)
- `y = -3.4` m
- `θ = 0` rad (facing +x)

Re-measure if the map is re-baked or the dock physically moves; existing
in-file comment documents both procedures.

## Error handling

| Failure | Detection | Dashboard | Recovery |
|---|---|---|---|
| **Cold start — auto-seed succeeded** | First odom ≈ origin + `home_pose.yaml` parseable | `localization: ok` | none |
| **Cold start — auto-seed failed** | Odom not near origin OR yaml missing | `localization: unseeded` (amber) | operator drags on `/nav` |
| **AMCL diverged / lost** | `/amcl_pose.cov` xx+yy > 1.0 m² OR yaw > 0.25 rad² | `localization: uncertain` (amber, halo on robot marker) | (1) wait 2-3 s; (2) `/reinitialize_global_localization` service; (3) operator drags |
| **Kidnapped robot** | Observation-likelihood watchdog: median `z_hit` per scan falls below `obs_likelihood_min` (tunable, default `0.15` — refine during phase-3 testing) for > 1 s while odom reports stationary; OR `/amcl_pose` covariance jumps 10× while stationary | `localization: kidnapped — re-seed` (yellow) | auto-trigger `/reinitialize_global_localization`; possibly teleop ~1 m to disambiguate; operator drag after 30 s if still stuck |
| **Depth-camera blackout** | `/scan` timestamp stale > 1 s | `localization: dead-reckon (no scan)` (amber, growing halo) | any active goal auto-cancels after 5 s; operator restarts `stretch3-zmq` |
| **Map–reality mismatch** | Same observation-likelihood watchdog (`obs_likelihood_min`), but symptom persists > 10 s across at least ~2 m of cumulative motion (sustained, not a transient dip) | `localization: uncertain` | operator decides whether to re-map (separate workflow) |

### `localization` field added to `/api/nav/status/stream`

```jsonc
{
  pose: { x, y, theta },
  task: { state, reason },
  teleop_active: bool,
  localization: {
    state: "unseeded" | "ok" | "uncertain" | "kidnapped" | "dead-reckon",
    cov_xy_m: float,
    cov_yaw_rad: float,
    scan_age_s: float
  }
}
```

Frontend (NavBar pose indicator):
- `ok` — green dot, no banner
- `unseeded` / `dead-reckon` / `uncertain` — amber dot, one-line banner
- `kidnapped` — red dot, banner with "re-seed" CTA → opens `/nav`

## Testing recipe

Five phases, ~30 min first run, ~5 min regression. See the design
sections for the full per-phase pass criteria; summary:

1. **Sanity** — `/scan`, `/tf odom→base_link`, `/map` flowing at expected rates.
2. **Auto-seed cold-start** — robot powered on at dock; expect `nav_service: auto-seeded from home_pose` log + NavBar `ok` within 5 s of launch.
3. **Convergence under motion** — teleop a 1-m square; covariance drops below 0.05 m²; marker tracks without jumps > 10 cm; return-to-start overlap ≤ 10 cm.
4. **Failure-mode rehearsals** — kidnap (lift 1 m sideways); depth blackout (kill driver 10 s); deliberately-wrong home_pose. Each expected NavBar state transition lands within its stated detection latency.
5. **End-to-end nav** — drag a goal on dashboard; robot drives there; marker overlap ≤ 15 cm at goal; AMCL stays `ok` throughout.

Regression checklist for future map / param changes: phase 2 + 3 + cold-start row of phase 4.

## Future work / out of scope

- **Room-camera localization augmentation.** Re-becomes viable if larger
  horizontal markers (~15-20 cm `DICT_4X4_50`) are added on top of the
  Stretch's chassis. Then a small Python ROS2 node detects them on the
  13 overhead cameras, fuses (`aruco_ros` × 13 → `robot_localization`
  EKF → `PoseWithCovarianceStamped`), publishes to `/initialpose` as a
  re-seeding correction. AMCL accepts it as a regular reseed. Hardware
  prereq blocks this; no code work until then.
- **`slam_toolbox` localization mode** if AMCL particle filter turns
  out to be too unstable in the ED305 layout (e.g. too few wall
  features in the open-meeting-room corridor). Requires one-time
  re-mapping with `slam_toolbox` mapping mode to produce a `.posegraph`.
- **`RTAB-Map` localization mode** for D435if-native RGB-D loop closure.
  Best long-term answer if open / dynamic / featureless spaces become a
  pain point.
- **`home_pose` ↔ runtime-location-store integration.** When the
  workflow teaches its `origin` location for any workflow (see
  `2026-05-21-runtime-location-store-design.md`), side-effect-write
  the same coordinates to `home_pose.yaml`. Eliminates one manual step
  during dock changes. Two-line change in the location-store teach
  endpoint.

## Critical files / integration points

- `backend/nav_bridge/launch/nav.launch.py:42-49` — the
  `use_static_map_to_odom` arg + comment that flagged this as a
  placeholder. Both go away in this change.
- `backend/nav_bridge/nav_service.py:254-275` — the placeholder
  identity `map → odom` broadcast loop. Deleted.
- `backend/nav_bridge/nav_service.py:216-252` — `_handle_set_initial_pose`.
  Body rewritten to publish to `/initialpose` instead of computing a
  static TF; same ZMQ contract preserved.
- `backend/nav_bridge/config/nav2_params.yaml:13-17` — the disabled AMCL
  stub. Replaced with the full block above.
- `backend/maps/305/raw/map.yaml` — the canonical map. Source of truth
  for the frame; do not modify.
- `backend/maps/305/raw/map_stats.json` — width/height/resolution
  metadata that the testing recipe references.
- `backend/app/api/nav.py` — the SSE source for the dashboard's status
  stream. Adds the `localization` field.
- `frontend/components/nav-bar.tsx` — colour change in the pose
  indicator based on `localization.state`.

---

Hands off to writing-plans next: produce a step-by-step implementation
plan from this spec.
