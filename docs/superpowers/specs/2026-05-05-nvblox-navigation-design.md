# Isaac ROS nvblox Navigation Integration — Design

**Date:** 2026-05-05
**Status:** Draft, awaiting Phase 1 verification before workflow integration
**Owner:** helin

## Goal

Replace the current `cure.skills.navigate.navigate_skill` calls in
`backend/app/workflows/medication_delivery.py` with a Nav2 + Isaac ROS
nvblox stack hosted on the lab GPU box, so the medication-delivery
robot can avoid dynamic obstacles (people, IV stands, gurneys) instead
of relying on a precomputed velocity sequence to fixed waypoints.

## Non-goals

- **Room-camera global localization implementation.** This design assumes
  an external system publishes `map → base_link` at some rate. The
  publisher itself is a separate WIP and out of scope here.
- **Forking `cure`.** `cure.skills.navigate` stays untouched on disk;
  the replacement is a new module `app/skills/nav.py` (Phase 2).
- **Multi-robot coordination, charging-dock docking, elevator
  traversal, multi-floor maps.** Single-floor, single-robot, ED305 only.

## Phasing

| Phase | Outcome | Workflow touched? |
|---|---|---|
| **1** | Lab Nav2 + nvblox stack reachable over ZMQ. **Primary verification surface is the dashboard `/nav` page** — the user opens it, sees the auto-localized robot pose, drag-adjusts if needed, click-drags a point to send a nav goal, watches the robot drive there. The CLI `backend/test_nvblox_nav.py` remains as a low-level smoke test for the lab dev. | No |
| **2** | `medication_delivery.py` switches its three nav call-sites to the new client; failure paths route through `handle_error`. | Yes |

Phase 2 starts only after Phase 1 passes the verification checklist in
`docs/nvblox-integration-guide.md`.

### Phase 1 user-visible flow

1. User opens dashboard → **Map Nav** tab (`/nav`).
2. The page subscribes to `GET /api/nav/status/stream`. As soon as the
   room-camera localizer publishes a pose, the robot marker appears at
   the correct map-frame position with a heading arrow.
3. If the localizer's estimate is off, the user drags the robot marker
   to the right spot — drag direction sets the new heading. The drag
   commits to `POST /api/nav/pose` and the backend stores it as the
   working pose.
4. User drags from any empty point on the map — release commits a
   `POST /api/nav/goto` with `(x, y, θ)`. Drag direction is the goal
   heading.
5. The status bar shows `pending → running → OK | failure-reason` as
   the lab's `nav_service` reports back over ZMQ. On success, a dashed
   green ring marks the goal pose; on failure, the reason string is
   surfaced in red.

## Architecture

```
       Browser                          Backend (lab box, hcis-s28)
       ──────────────────               ─────────────────────────────────────
       /nav page                        backend/app/api/nav.py (Starlette)
       NavMap component  ──HTTP──→        GET  /api/nav/map     (metadata)
         drag robot      ──HTTP──→        GET/POST /api/nav/pose (in-mem)
         drag empty      ──HTTP──→        POST /api/nav/goto    ─────┐
         status panel  ←─SSE────          GET  /api/nav/status*      │
                                                                     │  ZMQ REQ
       backend/test_nvblox_nav.py  ─── direct ZMQ REQ (skips proxy) ─┤  msgpack
       (low-level smoke test, no UI)                                 │  {target,
                                                                     │   timeout_s}
                                          (untouched in Phase 1:     │
                                           medication_delivery.py)   │
                                                                     ▼
                                                       tcp://lab:5560
                                                                     ▼
┌────────────────────────────── Lab GPU box (hcis-s28) ────────────────────────────┐
│                                                                                  │
│   nav_service (ZMQ REP)  ── resolves name → pose via poses.yaml                  │
│       │  publishes /goal_pose                                                    │
│       ▼                                                                          │
│   ROS2 Nav2 ◄── nvblox_local_costmap ◄── /camera/{depth,color}/image_rect_raw    │
│       │              ▲                                                           │
│       │  /cmd_vel    └── sensors_bridge ── SUB ZMQ → PUB sensor_msgs + /tf       │
│       ▼                                              ▲                           │
│   cmdvel_bridge ── SUB /cmd_vel → PUB ZMQ            │                           │
│                                                      │ map → base_link           │
│                                            (room-camera localizer, WIP)          │
│                                                                                  │
└──────────────────────────────────────────┬───────────────────────────────────────┘
                                           │  ZMQ over Wi-Fi
                                           ▼
┌────────────────────────────── Stretch SE3 (robot) ───────────────────────────────┐
│                                                                                  │
│   stretch3-zmq driver (extended)                                                 │
│     PUB depth + color + camera_info + odom_tf  →  lab                            │
│     SUB cmd_vel (with stale-watchdog: stop after 200 ms silence)  →  wheels      │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

**Key choices:**
- nvblox + Nav2 run on the lab GPU box, not the robot. Stretch has no
  CUDA-capable GPU; running nvblox CPU-only is unviable.
- Closed loop is over Wi-Fi. The robot driver runs a 200 ms cmd_vel
  watchdog so a Wi-Fi drop halts wheels rather than producing runaways.
- Transport is ZMQ for every wire crossing the Wi-Fi link, matching the
  rest of `stretch3-zmq` and the `room_cameras` sidecar pattern. ROS2
  DDS is used **only** within the lab box's localhost.
- Localization comes from an external `map → base_link` publisher
  (room cameras WIP). Wheel odometry from the robot supplies
  `odom → base_link` for Nav2's local controller.
- Static `map.pgm` (room 305, staged at `backend/maps/305/`) is the
  global costmap; nvblox is the local costmap.
- D435if (head-mounted RealSense) is the depth source. D405 (gripper)
  stays untouched and continues to serve `cure.skills.grasp`.

## Components

| Where | What | New / extends | Lang |
|---|---|---|---|
| Browser | `frontend/app/nav/page.tsx` | new — page route | tsx |
| Browser | `frontend/components/nav-map.tsx` | new — SVG map + drag gestures + status panel | tsx |
| Browser | `frontend/lib/nav-api.ts` | new — typed client + SSE subscriber | ts |
| Browser | `frontend/components/nav-bar.tsx` | edit — adds "Map Nav" link | tsx |
| Backend | `backend/app/api/nav.py` | new — Starlette routes (`/api/nav/{map,pose,goto,status,status/stream}`); ZMQ REQ proxy to lab | py 3.12 |
| Backend | `backend/app/__main__.py` | edit — mounts `nav_routes` | py 3.12 |
| Backend | `backend/.env.example` | edit — adds `NVBLOX_NAV_HOST/PORT/TIMEOUT_S` | sh |
| Backend | `backend/test_nvblox_nav.py` | new — low-level smoke test (no UI dep) | py 3.12 |
| Backend | `backend/app/skills/nav.py` | new (Phase 2) | py 3.12 |
| Lab | `backend/nav_bridge/nav_service.py` | new — ZMQ REP, dispatches to Nav2 `BasicNavigator.goToPose` | py 3.10 |
| Lab | `backend/nav_bridge/sensors_bridge.py` | new — SUBs depth/color/odom_tf ZMQ, PUBs `sensor_msgs` + `/tf` | py 3.10 |
| Lab | `backend/nav_bridge/cmdvel_bridge.py` | new — SUBs `/cmd_vel`, PUBs ZMQ | py 3.10 |
| Lab | `backend/nav_bridge/launch/nav.launch.py` | new — Nav2 + nvblox + bridges + map server | py 3.10 |
| Lab | `backend/nav_bridge/config/nav2_params.yaml` | new | yaml |
| Lab | `backend/nav_bridge/config/nvblox.yaml` | new | yaml |
| Lab | `backend/nav_bridge/config/poses.yaml` | new | yaml |
| Lab | `backend/nav_bridge/README.md` | new — install + run, sibling of `room_cameras/` | md |
| Robot | `stretch3-zmq` driver extension | extends — depth/color/odom_tf PUB, cmd_vel SUB + watchdog | py |
| Repo | `backend/maps/305/{map.pgm,map.yaml}` | done | — |
| Repo | `medication_delivery.py` | edit 3 sites + 1 import (Phase 2) | py |

The `nav_bridge/` directory follows the same isolation rationale as
`room_cameras/`: ROS2 ships its own Python 3.10 stack with `rclpy` C
extensions; mixing it with the backend's Python 3.12 venv breaks
`rclpy`. Separate process, separate Python, separate ports.

## API contract

Two API surfaces, both terminating on the lab box: browser ↔ backend
(HTTP+SSE over Cloudflare Tunnel) and backend ↔ `nav_service` (ZMQ
REQ/REP, localhost since they share the box).

### Browser ↔ backend (HTTP / SSE)

All endpoints under `/api/nav/`. JSON in, JSON out (or `text/event-stream`).

| Method | Path | Purpose |
|---|---|---|
| GET  | `/api/nav/map`             | Map metadata: image URL, resolution, origin, dims, frame_id. Mirrors `backend/maps/305/map.yaml`. |
| GET  | `/api/nav/pose`            | Latest known robot pose in map frame, or `null`. Source = `user`/`localizer`/`nav_result`. |
| POST | `/api/nav/pose`            | User override (drag-to-adjust). Body `{x, y, theta}` (radians). |
| POST | `/api/nav/goto`            | Submit nav goal. Body `{x, y, theta, timeout_s?}`. Returns `{request_id, state: "pending"}` immediately; status comes via SSE. 409 if a task is already in flight. |
| GET  | `/api/nav/status`          | Current `NavTask` snapshot. |
| GET  | `/api/nav/status/stream`   | SSE stream of `{pose, task}` snapshots, one event per state change + 15 s heartbeats. |

Backend holds the pose and task in process memory. When the lab
`nav_service` is unreachable, `/api/nav/goto` still returns `200`
immediately, then the background task resolves to
`{state: "done", status: "ROBOT_ERROR", reason: "lab nav_service
unreachable at <host>:<port>: ..."}` — no exceptions surface to the
client.

Lab address comes from `NVBLOX_NAV_HOST` / `NVBLOX_NAV_PORT` env vars
(defaults `localhost:5560`).

### Backend ↔ `nav_service` (ZMQ REQ/REP, localhost)

`tcp://$NVBLOX_NAV_HOST:$NVBLOX_NAV_PORT`, msgpack-encoded.

**Request:**
```python
{
  "target": "medicine"                # str → poses.yaml lookup (Phase 2)
  # OR
  "target": [x, y, theta],            # explicit pose, map frame, radians
  "timeout_s": 60.0,                  # optional, defaults from server config
  "request_id": "uuid",               # for server-side logging / future cancel
}
```

**Reply** (REP blocks until Nav2 reports terminal state):
```python
{
  "status": "OK" | "NO_PATH" | "TIMEOUT" | "OBSTRUCTED"
          | "CANCELLED" | "ROBOT_ERROR" | "BAD_TARGET",
  "reason": "human-readable string",
  "final_pose": [x, y, theta],   # map frame, radians, robot pose at terminal
  "elapsed_s": 12.3,             # wall time spent inside nav_service
}
```

**Phase 2 backend client surface** (`app/skills/nav.py`, not in Phase 1):
```python
class NavStatus(Enum):
    OK = "OK"; NO_PATH = "NO_PATH"; TIMEOUT = "TIMEOUT"
    OBSTRUCTED = "OBSTRUCTED"; CANCELLED = "CANCELLED"
    ROBOT_ERROR = "ROBOT_ERROR"; BAD_TARGET = "BAD_TARGET"

@dataclass
class NavResult:
    status: NavStatus
    reason: str
    final_pose: tuple[float, float, float]

def navigate_to(
    target: str | tuple[float, float, float],
    timeout_s: float = 60.0,
) -> NavResult: ...
```

## Wire formats — robot ↔ lab

ZMQ PUB/SUB, msgpack. All ports configurable in robot's `config.yaml`.

| Direction | Topic | Default port | Rate | Payload |
|---|---|---|---|---|
| robot → lab | depth | `tcp://robot:6010` | 15 Hz | `{ts_ns, h, w, encoding="16UC1", data: bytes}` |
| robot → lab | color | `tcp://robot:6011` | 15 Hz | `{ts_ns, h, w, encoding="rgb8", data: bytes}` (jpeg-encoded) |
| robot → lab | camera_info | `tcp://robot:6012` | 1 Hz | `{K, D, distortion_model, h, w}` |
| robot → lab | odom_tf | `tcp://robot:6013` | 50 Hz | `{ts_ns, x, y, theta}` (odom→base_link) |
| lab → robot | cmd_vel | `tcp://robot:6014` | 20 Hz | `{ts_ns, linear_x, angular_z}` |
| (external) | map_pose | `tcp://localizer:6020` | room cams' rate | `{ts_ns, x, y, theta}` (map→base_link) |

**Watchdog:** robot driver halts wheels if no `cmd_vel` arrives within
200 ms. This is standard Nav2 behavior elsewhere — we just enforce it
on the ZMQ side instead of DDS.

**ZMQ socket options:**
- All PUB sockets: `SNDHWM=2`, `CONFLATE=1` for image streams (drop
  stale frames rather than queue).
- All SUB sockets: `RCVHWM=2`, `CONFLATE=1`.
- LINGER=0 everywhere so dirty disconnects don't hang processes.

## Localization

Lab-side TF tree:
```
map ──[room-camera localizer (external)]── base_link
 │
 └── odom ──[robot odom_tf bridge]── base_link
```

Both `map → base_link` and `odom → base_link` are published. Nav2's
local controller uses the `odom → base_link` chain for short-horizon
control; the global planner uses `map → base_link` for goal pursuit.
If the room-camera localizer goes silent for >1 s, `nav_service`
refuses new requests with `BAD_TARGET("localization stale")`. In-flight
requests continue but with a warning logged.

## Failure modes

| Failure | Detection | Response |
|---|---|---|
| Wi-Fi drop mid-traverse | robot watchdog (no cmd_vel ≤ 200 ms) | wheels stop; lab Nav2 sees no progress, eventually returns `OBSTRUCTED` |
| Localization stale (>1 s of no `map_pose`) | nav_service watchdog | new requests rejected `BAD_TARGET`; in-flight continues (logged) |
| nvblox falls behind (depth queue piling up) | ROS2 timer monitor | local costmap goes stale; controller stops; surfaces as `OBSTRUCTED` if no progress in `timeout_s` |
| Robot driver dead | cmdvel_bridge connection refused / ZMQ EAGAIN | `ROBOT_ERROR` |
| `nav_service` down | Backend ZMQ REQ recv timeout / connection refused | `/api/nav/goto` still returns `200`; background task resolves to `ROBOT_ERROR` with the underlying ZMQ error in `reason`. Dashboard surfaces it in red. (Phase 2: workflow node calls `_fail` from the same status.) |
| Goal in unreachable pose (inside obstacle, off-map) | Nav2 planner | `NO_PATH` immediately |
| Planner times out | nav_service caller timeout | `TIMEOUT` |

## Testing strategy

### Phase 1 — primary path (UI)

1. **Backend up** — restart `python -m app` so `/api/nav/*` routes are
   live. Hit `curl http://localhost:9999/api/nav/map` to confirm.
2. **Frontend up** — `pnpm dev` in `frontend/`. Open
   `http://localhost:3000/nav`. The status bar should show
   `pose: unknown — drag the area to set initial pose` (when no
   localizer is publishing yet).
3. **Pose seeding** — drag any point on the map to seed an initial
   pose. The red robot marker appears with a heading arrow. The
   backend logs the `POST /api/nav/pose`.
4. **Hardware E2E** — start the robot driver + lab nav_service +
   localizer per the integration guide. The robot marker should jump
   to the localizer's estimate (status bar shows `[localizer]` source).
5. **Click-to-nav** — drag from an empty point on the map; release.
   The status bar transitions `idle → pending → running → OK`. The
   robot drives to the goal in the real world. A dashed green ring
   marks the goal pose on the map.
6. **Drag-to-adjust** — if the localizer estimate is visibly off,
   drag the robot marker to the correct pose; subsequent goals plan
   from the corrected pose.
7. **Dynamic-obstacle test** — queue a goal, walk through the planned
   path. nvblox detects, Nav2 replans, robot still arrives.
8. **Wi-Fi-pull test** — mid-traverse, kill the lab→robot ZMQ link.
   Robot halts within 200 ms; status eventually shows `OBSTRUCTED`.

### Phase 1 — secondary (CLI smoke test)

`backend/test_nvblox_nav.py 1.5 0.0 90 --lab-host hcis-s28` is a
self-contained ZMQ REQ client that bypasses the backend proxy entirely
and talks directly to the lab `nav_service`. Useful for the lab dev
to verify their service in isolation, before the dashboard is in the
loop.

### Phase 2

- Update `--dry-run` mode in `medication_delivery.py` to stub
  `navigate_to` (currently stubs `navigate_skill`). Keep the stubbed
  CLI path working without hardware.
- Run a full delivery (`python -m app.workflows.medication_delivery
  張小明 阿斯匹靈`) end-to-end with hardware, confirm the failure path
  through `handle_error → return_to_origin → END` triggers correctly
  when nav reports anything other than `OK`.


## Open questions

- **Lab GPU model & CUDA capability.** Isaac ROS DP requires CUDA 12 +
  Compute Capability ≥ 7.5 (Turing). Need to confirm `hcis-s28` GPU
  meets this before the install step.
- **Wi-Fi reliability under depth load.** 15 Hz depth (640×480 at
  16-bit = ~9.4 MB/s raw) is on the edge of comfortable for 802.11ac;
  may need to drop to 10 Hz or downsample. Decide empirically in
  Phase 1.
- **Robot's existing `/scan`.** Stretch's planar LIDAR is currently
  not in the ZMQ surface. We are not using it for AMCL (room cameras
  do that), but Nav2's local costmap could still benefit from it as a
  cross-check. Defer until Phase 1 is green.
- **Server-side cancel.** API has `request_id`; cancel endpoint is not
  in Phase 1 scope. Add in Phase 2 if the dashboard's "stop" button
  needs to abort an in-flight nav.
