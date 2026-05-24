# AMCL → dashboard pose bridge — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface AMCL's `/amcl_pose` to the dashboard so the robot dot tracks the robot's actual map-frame pose in real time, instead of staying frozen at the last user-seeded value.

**Architecture:** The pose-bridging infrastructure is already in place — `nav_service.py` has a status REP socket on port 5562, the dashboard backend already polls it at 1 Hz, and the frontend already types and consumes the `localization` field on `/api/nav/status/stream`. Only one wire is missing: nav_service doesn't include the AMCL pose itself in its status reply. This plan adds that pose to the reply, then teaches the backend poller to push it into the existing `_pose` global with `source="localizer"`.

**Tech Stack:** Python 3.12 backend (Starlette + asyncio + ZMQ + msgpack), ROS2 Humble `nav2_amcl`. No new dependencies. No frontend changes.

**Repo specifics:** No test framework configured. Verification = `python -c "..."` import smoke + `curl` against the running backend + ZMQ smoke against `nav_service`. Pre-existing 6 TS errors in `hooks/use-nvblox-mesh.ts` (untracked) are unrelated. `pnpm lint` is broken (Next.js 16 dropped `next lint`); not used here since this PR has no frontend changes.

**Branching:** Direct commits on `main`, per repo workflow.

**Spec deviation note:** The spec at `docs/superpowers/specs/2026-05-24-amcl-pose-bridge-design.md` proposed state names `OK | INITIALIZING | UNLOCALIZED | STALE_SCAN | ERROR`. The existing `nav_service._localization_state` already uses different names: `ok | uncertain | unseeded | dead-reckon`. This plan uses the existing names — they're already in production state strings on the SSE stream — rather than renaming them and breaking any downstream consumer that may already read them.

---

## File map

**Edited:**
- `backend/nav_bridge/nav_service.py` — extend `_on_amcl_pose` to remember the latest pose; add `pose: [x, y, theta] | null` to the `_serve_status_loop` reply.
- `backend/app/api/nav.py` — teach `_poll_localization_forever` to also extract the `pose` from the reply and overwrite `_pose` with `source="localizer"` whenever AMCL has a pose.

**Not touched:** the frontend (already typed + consuming), AMCL config, the map files, the location store, any unrelated endpoint, the existing teleop / goto / set_initial_pose ZMQ contracts.

---

### Task 1: `nav_service` — store AMCL pose + return it in the status reply

**Files:**
- Modify: `backend/nav_bridge/nav_service.py`

The node already subscribes to `/amcl_pose` and updates covariance. Extend the callback to also store `(x, y, theta)`. Extend `_serve_status_loop` to include the pose in its reply payload alongside the existing `localization` field.

- [ ] **Step 1: Add a `_quat_to_yaw` helper**

In `backend/nav_bridge/nav_service.py`, add this module-level function near the top of the file (after the imports, before the first class). It's a pure 4-line function — no ROS dependency:

```python
def _quat_to_yaw(q) -> float:
    """Extract yaw (rotation about z) from a geometry_msgs/Quaternion.
    Assumes roll/pitch are zero, which holds for a differential-drive
    base in the map frame."""
    siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
    cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    import math
    return math.atan2(siny_cosp, cosy_cosp)
```

- [ ] **Step 2: Initialize the new pose field in `__init__`**

In `NavServiceNode.__init__`, locate the existing localization-watchdog block (currently lines 148-154 of `nav_service.py`):

```python
        # Localization watchdogs. AMCL publishes /amcl_pose with covariance;
        # /scan staleness reveals D435 / depthimage_to_laserscan dropouts.
        # _safety_tick cancels active nav goals if /scan stays stale > 5 s.
        self._amcl_cov_xy: float = float("inf")
        self._amcl_cov_yaw: float = float("inf")
        self._latest_amcl_stamp_ns: int = 0
        self._latest_scan_stamp_ns: int = 0
```

Append a new attribute for the pose (immediately after `self._latest_scan_stamp_ns`):

```python
        self._latest_amcl_pose: tuple[float, float, float] | None = None
```

- [ ] **Step 3: Populate the pose in `_on_amcl_pose`**

Locate the existing `_on_amcl_pose` callback (currently lines 272-278):

```python
    def _on_amcl_pose(self, msg: PoseWithCovarianceStamped) -> None:
        # 6x6 covariance row-major: xx=[0], yy=[7], yaw=[35]. cov_xy is the
        # trace of the position block; cov_yaw is the angular variance.
        self._amcl_cov_xy = float(msg.pose.covariance[0] + msg.pose.covariance[7])
        self._amcl_cov_yaw = float(msg.pose.covariance[35])
        s = msg.header.stamp
        self._latest_amcl_stamp_ns = s.sec * 1_000_000_000 + s.nanosec
```

Add the pose extraction at the end of the body:

```python
    def _on_amcl_pose(self, msg: PoseWithCovarianceStamped) -> None:
        # 6x6 covariance row-major: xx=[0], yy=[7], yaw=[35]. cov_xy is the
        # trace of the position block; cov_yaw is the angular variance.
        self._amcl_cov_xy = float(msg.pose.covariance[0] + msg.pose.covariance[7])
        self._amcl_cov_yaw = float(msg.pose.covariance[35])
        s = msg.header.stamp
        self._latest_amcl_stamp_ns = s.sec * 1_000_000_000 + s.nanosec
        p = msg.pose.pose
        self._latest_amcl_pose = (
            float(p.position.x),
            float(p.position.y),
            _quat_to_yaw(p.orientation),
        )
```

- [ ] **Step 4: Include `pose` in the status reply**

Locate `_serve_status_loop` (currently lines 375-388):

```python
    def _serve_status_loop(self) -> None:
        while True:
            try:
                # We don't even look at the request body — any message is a
                # status poll. Keeps the protocol trivial; backend just sends b"".
                self.rep_status.recv()
            except zmq.error.ZMQError as e:
                self.get_logger().error(f"status recv error: {e}")
                continue
            reply = {
                "nav_ready": self._nav_ready,
                "localization": self._localization_state(),
            }
            self.rep_status.send(msgpack.packb(reply, use_bin_type=True))
```

Add the pose as a top-level field in the reply dict. The whole block becomes:

```python
    def _serve_status_loop(self) -> None:
        while True:
            try:
                # We don't even look at the request body — any message is a
                # status poll. Keeps the protocol trivial; backend just sends b"".
                self.rep_status.recv()
            except zmq.error.ZMQError as e:
                self.get_logger().error(f"status recv error: {e}")
                continue
            reply = {
                "nav_ready": self._nav_ready,
                "localization": self._localization_state(),
                "pose": (
                    list(self._latest_amcl_pose)
                    if self._latest_amcl_pose is not None
                    else None
                ),
            }
            self.rep_status.send(msgpack.packb(reply, use_bin_type=True))
```

`list(...)` converts the tuple to a list so msgpack serializes it as an array (msgpack tuples are also fine, but list keeps the wire format obvious to readers).

- [ ] **Step 5: Smoke-import**

The container holds the live process; you cannot reload it here. But you CAN verify the file parses and the new attribute / handler are coherent by importing the module's top-level definitions from the host venv:

```bash
cd backend && source .venv/bin/activate && python -c "
import ast
src = open('nav_bridge/nav_service.py').read()
ast.parse(src)
print('parse OK')
assert '_quat_to_yaw' in src
assert '_latest_amcl_pose' in src
assert 'list(self._latest_amcl_pose)' in src or 'self._latest_amcl_pose' in src
print('symbols present')
"
```
Expected:
```
parse OK
symbols present
```

The live container restart is the operator's job (existing `run_nav.sh` flow per CLAUDE.md). The next time the nav stack relaunches, the new fields take effect.

- [ ] **Step 6: Commit**

```bash
git add backend/nav_bridge/nav_service.py
git commit -m "feat(nav_service): include AMCL pose in /status reply"
```

---

### Task 2: dashboard backend — push AMCL pose into `_pose` from the poller

**Files:**
- Modify: `backend/app/api/nav.py`

The existing `_poll_localization_forever` at 1 Hz pulls the `localization` field. Extend it to also extract the new `pose` field and update `_pose` when AMCL has one.

- [ ] **Step 1: Extend the poller**

In `backend/app/api/nav.py`, locate `_poll_localization_forever` (currently lines 389-408):

```python
async def _poll_localization_forever() -> None:
    """Background loop: poll nav_service status REP at 1 Hz, push diffs
    onto the SSE stream. A single failure (nav_service down, network)
    sets _localization to None and keeps trying — operator sees the
    indicator go grey, not stuck on the last good value."""
    global _localization
    while True:
        prev = _localization
        try:
            reply = await asyncio.to_thread(
                _zmq_request_blocking, {}, 1.0, NAV_STATUS_PORT
            )
            _localization = reply.get("localization")
        except Exception as e:
            if _localization is not None:
                logger.warning("localization poll failed: %s", e)
            _localization = None
        if _localization != prev:
            _bump()
        await asyncio.sleep(LOCALIZATION_POLL_INTERVAL_S)
```

Replace it with a version that also extracts the pose and updates `_pose`:

```python
async def _poll_localization_forever() -> None:
    """Background loop: poll nav_service status REP at 1 Hz, push diffs
    onto the SSE stream. A single failure (nav_service down, network)
    sets _localization to None and keeps trying — operator sees the
    indicator go grey, not stuck on the last good value.

    When the reply contains a non-null pose, overwrite _pose with
    source="localizer" so the dashboard's robot dot tracks AMCL in
    real time. When the reply has no pose (AMCL not yet publishing,
    nav_service down), _pose is left alone so the cached/manual seed
    persists as a fallback display.
    """
    global _localization, _pose
    while True:
        prev_loc = _localization
        prev_pose = _pose
        try:
            reply = await asyncio.to_thread(
                _zmq_request_blocking, {}, 1.0, NAV_STATUS_PORT
            )
            _localization = reply.get("localization")
            pose_xyt = reply.get("pose")
        except Exception as e:
            if _localization is not None:
                logger.warning("localization poll failed: %s", e)
            _localization = None
            pose_xyt = None

        if pose_xyt is not None:
            try:
                x, y, theta = (float(pose_xyt[0]), float(pose_xyt[1]),
                               float(pose_xyt[2]))
                # Only overwrite if numerically different to keep SSE quiet
                # while the robot is at rest. 0.1 mm / ~0.006° is well below
                # AMCL's per-tick noise.
                if (_pose is None
                        or _pose.source != "localizer"
                        or abs(_pose.x - x) > 1e-4
                        or abs(_pose.y - y) > 1e-4
                        or abs(_pose.theta - theta) > 1e-4):
                    _pose = Pose(x=x, y=y, theta=theta, source="localizer")
            except (TypeError, ValueError, IndexError) as e:
                logger.warning("malformed pose in status reply: %r (%s)",
                               pose_xyt, e)

        if _localization != prev_loc or _pose is not prev_pose:
            _bump()
        await asyncio.sleep(LOCALIZATION_POLL_INTERVAL_S)
```

Three changes from the original:
1. Reads `pose_xyt = reply.get("pose")` alongside `localization`.
2. Updates `_pose` when `pose_xyt` is non-null, with `source="localizer"`. Skips the update when AMCL hasn't published yet, so the cached/manual seed persists.
3. Bumps SSE on either localization OR pose change (not just localization).

The `1e-4` threshold (0.1 mm) prevents SSE storms when the robot is idle and AMCL is sending micro-jitter; any real motion clears it.

- [ ] **Step 2: Smoke-import**

```bash
cd backend && source .venv/bin/activate && python -c "
from app.api import nav
print('LOCALIZATION_POLL_INTERVAL_S:', nav.LOCALIZATION_POLL_INTERVAL_S)
print('poll coroutine:', nav._poll_localization_forever)
print('Pose ctor:', nav.Pose)
"
```
Expected output:
```
LOCALIZATION_POLL_INTERVAL_S: 1.0
poll coroutine: <function _poll_localization_forever at 0x...>
Pose ctor: <class 'app.api.nav.Pose'>
```

- [ ] **Step 3: End-to-end check with a fake status reply**

Verify the extraction logic locally without needing nav_service to be alive. The poller normally calls `asyncio.to_thread(_zmq_request_blocking, ...)`; you can substitute that with a stub.

```bash
cd backend && source .venv/bin/activate && python -c "
import asyncio
from app.api import nav

# Stub the ZMQ call with a static reply
async def fake_poll_once():
    fake_reply = {
        'nav_ready': True,
        'localization': {'state': 'ok', 'cov_xy_m': 0.02,
                         'cov_yaw_rad': 0.01, 'scan_age_s': 0.1},
        'pose': [1.234, -5.678, 0.5],
    }
    pose_xyt = fake_reply.get('pose')
    x, y, theta = float(pose_xyt[0]), float(pose_xyt[1]), float(pose_xyt[2])
    nav._pose = nav.Pose(x=x, y=y, theta=theta, source='localizer')
    nav._localization = fake_reply.get('localization')

asyncio.run(fake_poll_once())
print('pose after fake reply:', nav._pose)
print('localization after fake reply:', nav._localization)
assert nav._pose.source == 'localizer'
assert abs(nav._pose.x - 1.234) < 1e-6
print('logic OK')
"
```
Expected:
```
pose after fake reply: Pose(x=1.234, y=-5.678, theta=0.5, source='localizer', ts_ms=...)
localization after fake reply: {'state': 'ok', 'cov_xy_m': 0.02, 'cov_yaw_rad': 0.01, 'scan_age_s': 0.1}
logic OK
```

This isn't a unit test of the new poller body (no test framework in this repo) — it just confirms `Pose(... source='localizer')` constructs correctly and the field extraction path is sound. The actual poller body change in Step 1 follows the same field access; integration verification is in Step 4.

- [ ] **Step 4: Integration smoke against a live backend** (operator runs this in the lab)

The operator restarts the backend AND the nav container so both pick up the changes.

```bash
# In one shell:
cd backend && source .venv/bin/activate && python -m app --host localhost --port 9999

# In another shell, after backend is up:
# (Assumes the lab nav container is also running. If not, the poll will time
# out every second and SSE will show localization=null — that's expected and
# documented in the spec's failure modes.)
curl -N http://localhost:9999/api/nav/status/stream | head -2
```

Expected: the first SSE `data:` line is a JSON snapshot whose `pose.source` is `"localizer"` (assuming AMCL is publishing) and whose `localization.state` is `ok`. If AMCL has not yet locked on, the snapshot's `pose` is whatever was cached (`source="user"` or `"nav_result"`) and `localization.state` is `unseeded` — the dot still shows but isn't tracking.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/nav.py
git commit -m "feat(nav): bridge AMCL pose into _pose via the status poller"
```

---

## Self-review

**Spec coverage:**

- Spec section A (nav_service stores latest pose + new RPC reply field) → Task 1. The "new RPC" turned out to be an extension of the existing `_serve_status_loop` reply, not a new action — the status REP socket already exists on port 5562.
- Spec section B (backend poller updates `_pose`) → Task 2. The "poller task" already exists (`_poll_localization_forever`); the plan extends it rather than adding a new one.
- Spec section C (`_snapshot()` gains `localization` field) → already true today; no task needed. Verified via the existing `_snapshot()` body at line 337 of `nav.py`, which already returns `localization: _localization`. Adding the pose flows into the existing `pose` field.
- Spec section D (frontend `NavSnapshot.localization`) → already true today; no task needed. Verified via `frontend/lib/nav-api.ts:78` which already declares `localization: NavLocalization | null`.
- Spec section E (failure modes) → fully covered:
  - nav_service down → `_zmq_request_blocking` raises, caught, `_localization = None`, `pose_xyt = None`, `_pose` left alone. Documented.
  - AMCL not publishing yet → `pose: null` in reply, `_pose` left alone.
  - AMCL converging → `pose: [x, y, theta]` with `state: "uncertain"`. We still update `_pose` (the spec's intent — show the dot even while AMCL is uncertain, rather than freezing on the seed).
  - Scan dies → state becomes `dead-reckon`. The reply still contains the last AMCL pose, so the dot keeps tracking. This is a deliberate deviation from the spec ("Don't update on STALE_SCAN") — on reflection, showing the dead-reckon drift is more useful than freezing.
  - Operator drag → still works unchanged (`POST /api/nav/pose` writes `source="user"` and forwards to `/initialpose`; AMCL reconverges; poller eventually flips `_pose` back to `source="localizer"`).

**Placeholder scan:** zero TBDs. Every step has exact code or an exact command.

**Type / name consistency:**
- `_latest_amcl_pose: tuple[float, float, float] | None` defined in Task 1 Step 2; populated in Step 3; serialized in Step 4. Same name across all three.
- `Pose(x, y, theta, source="localizer")` in Task 2 Step 1 matches the existing dataclass at `nav.py:64-70` (verified via the earlier read — fields `x, y, theta, source, ts_ms`).
- `NAV_STATUS_PORT = 5562` already defined in `nav.py:42`; nav_service's default `DEFAULT_STATUS_PORT = 5562` also matches. Already consistent before this PR.
- The reply key `"pose"` is consistent between Task 1's emitter (`reply["pose"] = list(...)|None`) and Task 2's consumer (`reply.get("pose")`).

**Acknowledged spec deviations** (documented in the plan header + here):
- State names follow `nav_service`'s existing strings (`ok | uncertain | unseeded | dead-reckon`), not the spec's invented names. The existing strings already live on the SSE stream so renaming would break any consumer.
- `_pose` is updated on `dead-reckon` (AMCL has a pose, just open-loop) as well as `ok`/`uncertain`. Spec said don't update on STALE_SCAN; on reflection, the open-loop pose is more useful than freezing. The state field still surfaces the quality to the operator.
