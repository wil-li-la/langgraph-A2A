# AMCL → dashboard pose bridge

**Date:** 2026-05-24
**Status:** Design approved (goal-driven, /goal: "wire robot's current position to dashboard")

## Problem

The dashboard's robot pose indicator (the NavBar `(x, y)` and the
`WorkflowLocationsMap` red dot) reads from `backend/app/api/nav.py`'s
module-level `_pose` global. Today that global is set in only three
places:

1. Loaded once at backend import from
   `~/.cache/langgraph-A2A/nav-pose.json`.
2. Overwritten by user drag-to-set-pose on `/nav` (`POST /api/nav/pose`).
3. Overwritten after a nav goal completes with the reported `final_pose`.

There is no subscription to AMCL's `/amcl_pose`, so once the user
drags the robot to its initial spot and AMCL takes over, the
dashboard dot stays frozen at the user's manual seed. The
`PoseSourceBadge` was built to flip to "LIVE" (`source: "localizer"`)
once a localizer is publishing, but nothing ever sets that source.

This spec wires AMCL → dashboard so the dot tracks the robot's
actual map-frame pose.

## Goals

1. The dashboard's robot pose dot (`NavBar` indicator + workflow
   map) updates at ~5 Hz from AMCL whenever AMCL is publishing in
   the `"OK"` state.
2. `_pose.source` becomes `"localizer"` whenever AMCL is the
   authority. The existing `PoseSourceBadge` ("LIVE" branch)
   already renders this correctly.
3. AMCL pose + a small localization-state summary
   (`{state, cov_xy_m, cov_yaw_rad, scan_age_s}`) ride on the
   existing `/api/nav/status/stream` SSE — no new transport,
   no new endpoint.
4. User drag-to-set-pose still works as the AMCL seed. After
   the drag, AMCL converges on the seeded pose and starts driving
   the dot.
5. If AMCL is not publishing (lab nav stack down, container
   restarting, scan stale, lost robot), the dashboard's `_pose`
   keeps its last value — no flickering to `null`, no clearing
   the user's manual seed.

## Non-goals

- Coloring the NavBar pose indicator by `localization.state`
  (green / amber / red) — surfacing the field on SSE is enough
  for this PR; visual rendering is a one-line follow-up.
- Watchdog UI affordances (lost-robot recovery banner,
  scan-staleness warning, kidnapped-robot dialog). The state
  is surfaced; UX for it is deferred.
- Replacing or rewriting the `2026-05-21-amcl-localization-design.md`
  spec. This is the narrow first slice of that larger plan: the
  pose+state-on-SSE bullet from its "File changes" table, nothing
  more.
- EKF fusion, multi-pose-source arbitration, or any change to
  AMCL's parameters. AMCL is already running per the prior commits;
  this spec just plumbs its output.

## Design

### A) `nav_service.py` — remember the latest AMCL pose; add a `status` RPC

`nav_service.py` already subscribes to `/amcl_pose` (line 156) and
keeps the latest covariance in `self._amcl_cov_xy` /
`self._amcl_cov_yaw`. Extend `_on_amcl_pose` to also store the
latest pose:

```python
def _on_amcl_pose(self, msg: PoseWithCovarianceStamped) -> None:
    # ... existing covariance update ...
    p = msg.pose.pose
    yaw = _quat_to_yaw(p.orientation)
    self._amcl_pose_xytheta = (float(p.position.x), float(p.position.y), yaw)
    self._amcl_pose_ts_ns = msg.header.stamp.sec * 1_000_000_000 + msg.header.stamp.nanosec
```

Add a `_quat_to_yaw` helper (4 lines: standard atan2 formula on
the z/w components — Stretch's base is differential so roll/pitch
are zero in map frame).

Add a new ZMQ action handler `"status"` to the existing
REQ/REP loop. The reply payload:

```json
{
  "pose": [x, y, theta] | null,
  "cov_xy_m": float | null,
  "cov_yaw_rad": float | null,
  "scan_age_s": float | null,
  "state": "OK" | "INITIALIZING" | "UNLOCALIZED" | "STALE_SCAN" | "ERROR",
  "ts_ms": int
}
```

State derivation:
- `OK`: `pose` non-null, `cov_xy_m < 0.5`, `scan_age_s < 1.0`.
- `INITIALIZING`: `pose` non-null but `cov_xy_m >= 0.5`
  (AMCL still converging from seed).
- `UNLOCALIZED`: `pose` null (no AMCL message yet, or AMCL node
  not running).
- `STALE_SCAN`: `scan_age_s >= 1.0` (laserscan input stopped,
  AMCL can't update — robot might still know roughly where it
  is from the last good fix, so we keep `pose` populated).
- `ERROR`: catch-all for unexpected internal exceptions in the
  status handler.

`scan_age_s` comes from the existing `/scan` subscription that
`nav_service` already uses for its watchdog. If that subscription
hasn't been added yet (per the AMCL spec), this field can be
`null` and the state derivation degrades gracefully to
`OK | INITIALIZING | UNLOCALIZED`.

### B) `backend/app/api/nav.py` — add a poller task

Add a module-level `_localization: dict | None = None` to hold
the latest state snapshot.

At server startup (the existing Starlette `on_startup` hook, or
`__main__.py`'s setup), kick off:

```python
async def _localizer_poller():
    """Poll nav_service for the latest AMCL pose at ~5 Hz.

    When AMCL is in state OK or INITIALIZING and reports a pose,
    overwrite _pose with source="localizer" so the dashboard's
    robot dot tracks live. When AMCL is down or unlocalized,
    leave _pose alone so the user's manual seed / nav_result
    pose persists as a fallback display.
    """
    global _pose, _localization
    while True:
        await asyncio.sleep(0.2)
        try:
            reply = await asyncio.to_thread(
                _zmq_request_blocking, {"action": "status"}, 0.5,
            )
        except Exception as e:
            # nav_service unreachable or timed out — leave state alone.
            # No retry storm: the next iteration tries again in 200 ms.
            logger.debug("localizer poll failed: %s", e)
            continue

        new_loc = {
            "state": reply.get("state", "ERROR"),
            "cov_xy_m": reply.get("cov_xy_m"),
            "cov_yaw_rad": reply.get("cov_yaw_rad"),
            "scan_age_s": reply.get("scan_age_s"),
        }
        changed = new_loc != _localization
        _localization = new_loc

        pose_xyt = reply.get("pose")
        if pose_xyt and new_loc["state"] in ("OK", "INITIALIZING"):
            x, y, theta = pose_xyt
            new_pose = Pose(x=float(x), y=float(y), theta=float(theta),
                            source="localizer")
            # Only bump if numerically different (avoid SSE spam at standstill).
            if (_pose is None
                    or abs(_pose.x - new_pose.x) > 1e-4
                    or abs(_pose.y - new_pose.y) > 1e-4
                    or abs(_pose.theta - new_pose.theta) > 1e-4
                    or _pose.source != "localizer"):
                _pose = new_pose
                changed = True
        if changed:
            _bump()
```

Two thresholds to keep SSE quiet:
- Pose change threshold: 0.1 mm / 0.0001 rad (effectively any
  real motion; numerical noise won't fire).
- `_localization` is compared by equality — only state/cov flips
  send an event.

The poller runs forever; if `nav_service` is down its REQ times
out per call and the loop continues. No exponential backoff
because the polling interval IS the backoff.

### C) `_snapshot()` — surface the new field

```python
def _snapshot() -> dict[str, Any]:
    return {
        "pose": asdict(_pose) if _pose else None,
        "task": asdict(_task),
        "teleop_active": _teleop_active,
        "localization": _localization,   # may be None at startup
    }
```

### D) Frontend types

`frontend/lib/nav-api.ts` `NavSnapshot` interface gains a
`localization` field. No component change is required: the dot
just starts moving. The `WorkflowLocationsMap` and `NavBar`
already read `pose` from the same SSE-fed context.

### E) Failure modes

| Scenario | Behavior |
|---|---|
| `nav_service` not running (lab container down) | Poller's REQ times out every 200 ms. `_pose` keeps last value. `_localization` keeps last value (likely `state: "UNLOCALIZED"` from the previous run, or `null` if backend just started). Backend logs at DEBUG, not WARNING, to avoid noise. |
| AMCL not yet publishing | `nav_service` returns `pose: null, state: "UNLOCALIZED"`. `_pose` left alone — the user's cached seed shows. |
| AMCL converging from seed | `pose: [x, y, theta], state: "INITIALIZING"`. `_pose` updates with `source="localizer"`. PoseSourceBadge shows "LIVE" (treating INITIALIZING as live; better than freezing at the seed). |
| Scan dies | `pose: [...], state: "STALE_SCAN"`. `_pose` NOT updated (state is not OK/INITIALIZING). The dashboard dot stays at the last live position; SSE event fires with the new state so a future watchdog UI can warn. |
| Operator drag-to-set-pose | `POST /api/nav/pose` still works: it overwrites `_pose` to `source="user"` and forwards to `/initialpose`. AMCL reconverges; within ~1 s the poller flips `_pose` back to `source="localizer"`. |

## Files touched

**Backend (in-container, lab nav stack):**
- `backend/nav_bridge/nav_service.py` — extend `_on_amcl_pose`
  to remember the latest pose; add `_quat_to_yaw` helper; add
  the `"status"` action handler to the existing REQ/REP loop.

**Backend (dashboard, host venv):**
- `backend/app/api/nav.py` — add `_localization` global, the
  `_localizer_poller` coroutine, registration of the poller
  task on app startup, and the `localization` field in
  `_snapshot()`.
- `backend/app/__main__.py` — start the `_localizer_poller`
  task on Starlette's `on_startup` hook (or wherever existing
  background tasks are started today).

**Frontend (types only):**
- `frontend/lib/nav-api.ts` — extend `NavSnapshot` with the
  `localization` field (typed as
  `{ state: string; cov_xy_m: number | null; cov_yaw_rad: number | null; scan_age_s: number | null } | null`).

**Not touched:**
- AMCL config (`nav2_params.yaml`).
- Any frontend component file.
- The location store, workflow API, or any unrelated endpoint.
- `~/.cache/langgraph-A2A/nav-pose.json` — the manual-seed
  cache stays as the cold-start fallback when AMCL is silent.

## Testing

Repo has no test framework; verification is manual.

1. **Backend imports clean:**
   `cd backend && source .venv/bin/activate && python -c "from app.api import nav; print(nav._localization)"` — expect `None`.

2. **nav_service status RPC reply shape** (with the container running):
   ```bash
   python -c "
   import zmq, msgpack
   ctx = zmq.Context(); s = ctx.socket(zmq.REQ); s.connect('tcp://localhost:5560')
   s.send(msgpack.packb({'action': 'status'}, use_bin_type=True))
   print(msgpack.unpackb(s.recv(), raw=False))
   "
   ```
   Expect a dict with the 6 keys; `state` is one of the 5 enum values.

3. **SSE includes localization field:**
   ```bash
   curl -N http://localhost:9999/api/nav/status/stream | head -2
   ```
   First event's JSON contains a `localization` field (may be `null`
   if no poll has succeeded yet).

4. **Live tracking with AMCL up:**
   - Start the lab nav stack.
   - On the dashboard, drag the robot to the charging dock.
     Pose source flips: `MANUAL` → `LIVE` within ~1 s.
   - Drive the robot via teleop. The dashboard dot moves in
     real time at ~5 Hz, tracking AMCL.
   - Stop the robot. Dot stops moving; covariance values in
     SSE go down (AMCL refining).

5. **Graceful degradation:**
   - Kill the nav container (or stop nav_service). Poller logs
     DEBUG-level timeouts. `_pose` keeps last live value
     indefinitely. Dashboard dot stops moving but does NOT
     vanish.
   - Restart the container. Poller's next call succeeds; dot
     resumes tracking.

6. **Frame check:**
   AMCL publishes in the `map` frame, which is identical to the
   dashboard's `MAP_METADATA["origin"]` since both load
   `backend/maps/305/raw/map.yaml`. The dot's `(x, y)` matches
   `ros2 topic echo /amcl_pose` to within numerical noise.
