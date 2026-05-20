# Refactor request: `goto` service on stretch3-zmq (x, y, theta)

> **Status: implemented (2026-05-18).** The robot team shipped this on branch
> `feat/nvblox-robot-driver-migration` (commits `3a28a9b` → `a0bc2ea`). The
> wire contract on port 5557 is unchanged from this spec; the legacy
> `{"linear","angular"}` endpoint moved to port 5559 as `goto_velocity`.
> The on-robot implementation is a **proxy** to the lab `nav_service`
> rather than a local `BasicNavigator` — see "Decisions that diverge"
> below — but the dashboard side does not need to change.
>
> See the implementation report from the robot team for the full picture
> (reply payload status codes, config knobs, test coverage, smoke-test
> snippet). Anything in this doc that conflicts with the shipped behavior
> is **superseded** by that report.

**Target repo:** [`lnfu/stretch3-zmq`](https://github.com/lnfu/stretch3-zmq)
**Driver path:** `Desktop/stretch3-zmq/` on `stretch-se3-3099.local`
**Requested by:** dashboard team (LangGraph workflow + LLM agent)

## Reply codes the dashboard now receives

The shipped server returns structured strings on failure:
`"<status>: <reason>"` (e.g. `"no_path: planner returned no plan"`,
`"timeout: 60.0s deadline exceeded"`, `"obstructed: controller failed to
progress"`, `"cancelled: ..."`, `"robot_error: ..."`, `"bad_target: ..."`,
`"invalid_goal: ..."`).

The client's `if reply != "ok": raise RuntimeError(f"goto failed: {reply}")`
handles all of these correctly — the structured string surfaces as the
exception message and bubbles up to the workflow's `error_handler` node.
Future improvement (not in scope here): distinguish transient failures
(`timeout`, `obstructed`) from permanent ones (`no_path`, `bad_target`,
`invalid_goal`) for selective retry.

## Why

The client-side `navigate_skill` in this repo
(`backend/app/tools/stretch_tools.py`) has been refactored to send a **single
absolute pose goal** instead of a hand-decomposed rotate → translate → rotate
sequence of velocity commands.

Reasons for the change:

1. **Obstacle avoidance.** The old client-side decomposition assumed an empty
   floor — three open-loop velocity commands cannot replan around a person, a
   chair, or a closed door. Nav2 already has a global + local planner; we
   should use it.
2. **Localization drift tolerance.** Rotate-then-translate accumulates error
   from `status.odometry.pose` every leg. A single absolute goal lets Nav2
   continuously correct against `/amcl_pose` or `/odom`.
3. **Symmetry with `cure.skills.navigate.navigate_avoidance`**, which already
   uses the `{"x", "y", "theta"}` wire format and was the reference for this
   refactor.
4. **One round trip per `navigate_skill()` call** instead of three. Reduces
   tail latency on flaky Wi-Fi.

## New wire protocol

**Port:** `cfg.ports.goto` — recommended `5557`.
**Socket pattern:** ZMQ `REP` on the server, `REQ` on the client.
**Request payload:** msgpack-encoded dict:

```python
{"x": float, "y": float, "theta": float}
```

- `x`, `y` — target pose in **the robot's `odom` frame** (meters).
- `theta` — target heading in `odom` (radians, conventional yaw).

**Reply payload:** ZMQ string frame.

- `"ok"` — Nav2 reached the goal (or the goal was already satisfied).
- Anything else — surfaced verbatim to the client as the failure reason.
  Suggested values: `"aborted"`, `"canceled"`, `"timeout: <s>s"`,
  `"nav2_unavailable"`, `"invalid_goal: <reason>"`.

The reply MUST be sent only after the navigation task terminates — the client
treats this call as blocking and uses it as the synchronization point for the
next workflow step (pickup, handover, etc.).

### Old protocol (to remove or keep as a deprecated path)

The current driver — if/when it implements port 5557 — speaks:

```python
{"linear": float, "angular": float}  # per-leg velocity command
```

The new client no longer sends this format. If you want to keep the old
protocol for ad-hoc teleop, gate it behind a different port (e.g. `5559`
`goto_velocity`) so the two services don't share a socket.

## Server-side implementation sketch

Add a new service file at `driver/services/goto.py`:

```python
import threading
import msgpack
import rclpy
import zmq
from nav2_simple_commander.robot_navigator import BasicNavigator, TaskResult
from geometry_msgs.msg import PoseStamped
from tf_transformations import quaternion_from_euler

def _make_pose(x: float, y: float, theta: float, frame_id: str = "odom") -> PoseStamped:
    p = PoseStamped()
    p.header.frame_id = frame_id
    # stamp filled in by the navigator
    p.pose.position.x = x
    p.pose.position.y = y
    qx, qy, qz, qw = quaternion_from_euler(0.0, 0.0, theta)
    p.pose.orientation.x = qx
    p.pose.orientation.y = qy
    p.pose.orientation.z = qz
    p.pose.orientation.w = qw
    return p


def run(port: int, frame_id: str = "odom") -> None:
    """Blocking REP loop. Run in its own threading.Thread from driver/__main__."""
    rclpy.init(args=None)
    nav = BasicNavigator()
    nav.waitUntilNav2Active()       # blocks until /amcl + /bt_navigator are up

    ctx = zmq.Context.instance()
    sock = ctx.socket(zmq.REP)
    sock.bind(f"tcp://*:{port}")

    try:
        while True:
            try:
                msg = msgpack.unpackb(sock.recv(), raw=False)
                x = float(msg["x"]); y = float(msg["y"]); th = float(msg["theta"])
            except Exception as e:
                sock.send_string(f"invalid_goal: {e}")
                continue

            pose = _make_pose(x, y, th, frame_id=frame_id)
            pose.header.stamp = nav.get_clock().now().to_msg()
            nav.goToPose(pose)

            while not nav.isTaskComplete():
                pass  # feedback not currently surfaced; add if useful

            result = nav.getResult()
            if result == TaskResult.SUCCEEDED:
                sock.send_string("ok")
            elif result == TaskResult.CANCELED:
                sock.send_string("canceled")
            elif result == TaskResult.FAILED:
                sock.send_string("aborted")
            else:
                sock.send_string(f"unknown: {result}")
    finally:
        sock.close()
        rclpy.shutdown()
```

Then in `driver/__main__.py`, alongside the other service threads:

```python
from .services import goto as goto_service

threading.Thread(
    target=goto_service.run,
    args=(config.ports.goto,),
    daemon=True,
    name="goto-service",
).start()
```

And in `driver/config.py` (if not already there):

```python
@dataclass
class Ports:
    ...
    goto: int = 5557
```

## Preconditions for the service to start

- **Nav2 must be running on the robot** before the driver starts:
  `ros2 launch stretch_nav2 navigation.launch.py`
  If Nav2 isn't up, `BasicNavigator.waitUntilNav2Active()` will block — that
  is the desired behavior; surface this clearly in the driver startup logs so
  the operator knows what they're waiting for.
- The driver process must be able to `import rclpy` and
  `nav2_simple_commander`. On the Stretch this means launching it inside the
  same ROS2 environment that Nav2 uses (source `/opt/ros/humble/setup.bash`).
  If the driver currently runs under a venv that masks the ROS2 site-packages,
  add the `nav2_simple_commander` deb to `requirements.system` or run the
  service as a separate process.

## Frame choice

The client currently passes coordinates **as written in `cure/config.yaml`
`objects:`**, which were authored in `odom`. Keep `frame_id="odom"` on the
server unless / until both sides switch to `map`.

If you want to support `map` later, add an optional `"frame"` key to the
request:

```python
{"x": float, "y": float, "theta": float, "frame": "map" | "odom"}
```

…and treat absence as `"odom"` for back-compat. The dashboard client will
remain on `odom` until SLAM/AMCL is reliable.

## Timeouts and cancellation

- **Server-side timeout:** none today; `goToPose` will retry until Nav2 itself
  gives up. If a workflow gets stuck waiting on a phantom goal, the operator
  hits ctrl-C in the dashboard, which currently does NOT cancel the goal —
  see "Open questions" below.
- **Recommendation:** wrap the `while not nav.isTaskComplete()` loop with a
  ROS-clock deadline (e.g. 90 s) and reply `"timeout: 90s"` if it expires,
  also calling `nav.cancelTask()`.

## Rollout / back-compat plan

1. **Land the new service** behind the same port (`5557`). The client expects
   `{"x","y","theta"}` already — any time you ship the new driver, the new
   client starts working.
2. **The old `{"linear","angular"}` callers are gone** from the langgraph
   repo as of this refactor. If other consumers of port 5557 exist (e.g. a
   teleop tool), move them to a separate port before you remove the old
   handler — there is no shared compatibility path on a single port.
3. **No client-side migration needed** for langgraph: this doc lands together
   with the client refactor, so the two halves go live in the same change
   window.

## How the dashboard side calls it

For reference (`backend/app/tools/stretch_tools.py::navigate_skill`):

```python
goto_sock = _connect_req(f"tcp://{SERVER_IP}:{cfg.ports['goto']}")
goto_sock.send(msgpack.packb({"x": tx, "y": ty, "theta": ttheta}))
reply = goto_sock.recv_string()
if reply != "ok":
    raise RuntimeError(f"goto failed: {reply}")
```

`tx, ty, ttheta` come from `cure/config.yaml` `objects.<name>.location`,
keyed by `"medicine" | "patient" | "origin"`.

## Open questions for the robot team

1. **Cancellation.** Should the goto service expose a separate `cancel` port,
   or accept a sentinel request like `{"cancel": true}` on the same port?
   Current dashboard "stop" only stops the workflow graph — the robot keeps
   moving until Nav2 finishes the leg.
2. **Frame transform.** Confirm whether `objects.<name>.location` is in
   `odom` or `map` — once we have AMCL initialized, switching to `map` would
   let us power-cycle the robot without re-authoring poses.
3. **Feedback streaming.** Worth publishing `getFeedback()` (ETA, distance
   remaining) as a PUB topic so the dashboard can show a progress bar?

## Testing

- **Bench test (no Nav2):** server should reply `"nav2_unavailable"` (or
  block in `waitUntilNav2Active` — pick one and document it) instead of
  silently hanging the client.
- **Smoke test:** with Nav2 running and the robot at origin, send
  `{"x":1.0, "y":0.0, "theta":0.0}` and verify the robot drives forward 1 m
  and the reply is `"ok"`.
- **Failure path:** unplug the lidar / kill `nav2_amcl` mid-goal — the
  server should reply `"aborted"` rather than hang.
- **Round-trip from dashboard:** trigger the medication delivery workflow
  with `DRY_RUN=0`; `navigate_to_pharmacy_node`, `navigate_to_patient_node`,
  and `return_to_origin_node` should each issue exactly one goto round-trip.
