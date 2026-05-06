"""Direct ZMQ wrappers for the stretch3-zmq driver, plus LangChain @tool wrappers.

Replaces five of the six cure.skills imports with thin direct-ZMQ helpers
(navigate, handover, speak, wait_for_speech_completion, listen). grasp_skill
is still imported lazily from cure inside pick_up — it depends on ArUco
detection, Pinocchio IK, and synchronized RGB+depth, which is too heavy to
inline. Plan is to swap it for a VLA endpoint or a decomposed step sequence
later; when that lands, the cure dep can be removed entirely.

Wire formats below match cure exactly so the on-robot driver sees identical
traffic. Cross-checked against:
  - cure.skills.{speak,listen,navigate,handover}
  - cure.utils.motion.move_and_wait2  (trapezoidal-ETA sleep)
  - cure.utils.network                (recv_fresh)

Two layers:
  - Drop-in replacements (`navigate_skill`, `handover_skill`, `speak_skill`,
    `wait_for_speech_completion`, `listen_skill`): same names + signatures
    as cure, so callers in `app/workflows/medication_delivery.py` only
    change their import path.
  - `@tool` wrappers (`navigate_to`, `pick_up`, `hand_over`, `speak`,
    `listen`, `what_can_i_see`): unchanged surface for the LLM agent.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Optional

import msgpack
import numpy as np
import yaml
import zmq
from langchain_core.tools import tool
from stretch3_zmq.core.messages.command import ManipulatorCommand
from stretch3_zmq.core.messages.protocol import (
    decode_with_timestamp,
    encode_with_timestamp,
)
from stretch3_zmq.core.messages.status import Status

from app.safety.guard import get_guard
from app.tools.world_model import (
    KNOWN_LOCATIONS,
    KNOWN_GRASPABLE_OBJECTS,
    LOCATION_DESCRIPTIONS,
)

logger = logging.getLogger(__name__)


# ---------- Robot connection + config ------------------------------------

SERVER_IP = os.getenv("ROBOT_IP", "localhost")

_DEFAULT_PORTS = {
    "status": 5555,
    "command": 5556,
    "goto": 5557,
    "arducam": 6000,
    "d435if": 6001,
    "d405": 6002,
    "tts": 6101,
    "tts_status": 6102,
    "asr": 6103,
}

_DEFAULT_TIMING = {
    "max_age_ns": 500_000_000,
    "wait_interval_ns": 50_000_000,
}

# (max velocity, max acceleration) per joint. Defaults match cure.config.
_DEFAULT_TRAPEZOID: dict[str, tuple[float, float]] = {
    "lift": (0.13, 0.25),
    "arm": (0.05, 0.05),
    "wrist_yaw": (0.75, 1.5),
    "wrist_pitch": (1.0, 4.0),
    "wrist_roll": (1.0, 4.0),
    "head_pan": (1.0, 4.0),
    "head_tilt": (3.0, 8.0),
    "gripper": (6.0, 19.0),
}

_DEFAULT_HANDOVER = {
    "lift": 0.5,
    "arm": 0.2,
    "head_pan": 3.0 - np.pi / 2,
    "head_tilt": -np.pi / 2,
}

_DEFAULT_GRIPPER = {"open": 100.0, "close": 0.0}


class _RobotConfig:
    """Lazy-loaded subset of cure's config.yaml — only what these tools need.

    Reads from $ROBOT_CONFIG_FILE if set, else backend/cure/config.yaml.
    Falls back silently to built-in defaults so DRY_RUN paths never fail
    on a missing file.
    """

    def __init__(self) -> None:
        self.ports: dict[str, int] = dict(_DEFAULT_PORTS)
        self.timing: dict[str, int] = dict(_DEFAULT_TIMING)
        self.trapezoid: dict[str, tuple[float, float]] = dict(_DEFAULT_TRAPEZOID)
        self.handover: dict[str, float] = dict(_DEFAULT_HANDOVER)
        self.gripper: dict[str, float] = dict(_DEFAULT_GRIPPER)
        self.objects: dict[str, tuple[float, float, float]] = {}
        self.cameras: dict[str, dict] = {}
        self._loaded = False

    def _resolve_path(self) -> Path:
        env = os.getenv("ROBOT_CONFIG_FILE")
        if env:
            return Path(env)
        # Default lives at backend/cure/config.yaml — the directory name is
        # historical (it used to be loaded by cure.config); the file itself
        # has nothing to do with the cure pip package.
        return Path(__file__).resolve().parent.parent.parent / "cure" / "config.yaml"

    def _maybe_load(self) -> None:
        if self._loaded:
            return
        path = self._resolve_path()
        if not path.is_file():
            logger.warning("Robot config not found at %s; using built-in defaults", path)
            self._loaded = True
            return

        with open(path) as f:
            data = yaml.safe_load(f) or {}

        for k, v in (data.get("ports") or {}).items():
            self.ports[k] = int(v)
        for k, v in (data.get("timing") or {}).items():
            self.timing[k] = int(v)
        for k, v in (data.get("gripper") or {}).items():
            self.gripper[k] = float(v)

        skills_handover = ((data.get("skills") or {}).get("handover") or {})
        for k, v in skills_handover.items():
            self.handover[k] = float(v)

        for joint, vals in (data.get("trapezoid_profile") or {}).items():
            if "v" in vals and "a" in vals:
                self.trapezoid[joint] = (float(vals["v"]), float(vals["a"]))

        for name, obj in (data.get("objects") or {}).items():
            loc = obj.get("location") or {}
            self.objects[name] = (
                float(loc.get("x", 0.0)),
                float(loc.get("y", 0.0)),
                float(loc.get("theta", 0.0)),
            )
        self.cameras = data.get("cameras") or {}
        self._loaded = True


_config = _RobotConfig()


def get_config() -> _RobotConfig:
    """Return the (lazily-loaded) robot config. Public so api/camera.py can use it."""
    _config._maybe_load()
    return _config


# ---------- Low-level ZMQ helpers ----------------------------------------

# zmq.Context.instance() is the recommended single shared context for the
# whole process. Each skill creates its own sockets per call (matching cure)
# and closes them in the finally block — no socket reuse, no thread issues.

def _ctx() -> zmq.Context:
    return zmq.Context.instance()


def _connect_sub(addr: str, topic: bytes = b"") -> zmq.Socket:
    s = _ctx().socket(zmq.SUB)
    s.setsockopt(zmq.RCVHWM, 64)
    s.connect(addr)
    s.setsockopt(zmq.SUBSCRIBE, topic)
    return s


def _connect_pub(addr: str) -> zmq.Socket:
    s = _ctx().socket(zmq.PUB)
    s.connect(addr)
    return s


def _connect_req(addr: str) -> zmq.Socket:
    s = _ctx().socket(zmq.REQ)
    s.connect(addr)
    return s


def _recv_fresh(socket: zmq.Socket, max_age_ns: int) -> tuple[int, bytes]:
    """Block until a message younger than max_age_ns is received.

    Mirrors cure.utils.network.recv_fresh.
    """
    while True:
        parts = socket.recv_multipart()
        ts_ns, payload = decode_with_timestamp(parts)
        if time.time_ns() - ts_ns <= max_age_ns:
            return ts_ns, payload


# ---------- Drop-in cure.skills replacements ------------------------------

# These match the cure skill APIs verbatim (same names, signatures, return
# types). medication_delivery.py imports them under the original names.


def speak_skill(text: str) -> Optional[str]:
    """Submit text to TTS. Returns the job_id, or None if text is empty."""
    if not text:
        return None
    cfg = get_config()
    sock = _connect_req(f"tcp://{SERVER_IP}:{cfg.ports['tts']}")
    try:
        sock.send_string(text)
        return sock.recv_string()
    finally:
        sock.close()


def wait_for_speech_completion(job_id: str, timeout_s: float) -> bool:
    """Block until the TTS job finishes (or timeout). True on success."""
    cfg = get_config()
    sock = _connect_sub(
        f"tcp://{SERVER_IP}:{cfg.ports['tts_status']}",
        topic=job_id.encode("utf-8"),
    )
    poller = zmq.Poller()
    poller.register(sock, zmq.POLLIN)
    deadline = time.monotonic() + timeout_s
    try:
        while True:
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            if remaining_ms <= 0:
                logger.warning("TTS job %s timed out", job_id)
                return False
            if not poller.poll(remaining_ms):
                logger.warning("TTS job %s timed out", job_id)
                return False
            frames = sock.recv_multipart()
            if len(frames) < 2:
                continue
            status = frames[1]
            if status == b"done":
                logger.info("TTS job %s done", job_id)
                return True
            if status == b"error":
                logger.error("TTS job %s failed", job_id)
                return False
    finally:
        sock.close()


def listen_skill() -> str:
    """Block on ASR until a transcript is returned. Empty string on silence."""
    cfg = get_config()
    sock = _connect_req(f"tcp://{SERVER_IP}:{cfg.ports['asr']}")
    try:
        sock.send_string("listen")
        return sock.recv_string() or ""
    finally:
        sock.close()


def _wrap_angle(a: float) -> float:
    """Wrap to [-pi, pi] so the base always takes the shortest turn."""
    return float((a + np.pi) % (2 * np.pi) - np.pi)


def navigate_skill(object_name: str) -> None:
    """Drive the base to a named location from config.yaml `objects:`.

    Reads current pose from the status stream, decomposes the XY motion
    into rotate → translate → rotate, and sends each leg to the goto
    REQ port. Mirrors cure.skills.navigate.navigate_skill.
    """
    cfg = get_config()
    if object_name not in cfg.objects:
        raise ValueError(f"Object {object_name} not found")
    tx, ty, ttheta = cfg.objects[object_name]

    status_sock = _connect_sub(f"tcp://{SERVER_IP}:{cfg.ports['status']}")
    goto_sock = _connect_req(f"tcp://{SERVER_IP}:{cfg.ports['goto']}")
    try:
        _, payload = _recv_fresh(status_sock, cfg.timing["max_age_ns"])
        status = Status.from_bytes(payload)
        sx = status.odometry.pose.x
        sy = status.odometry.pose.y
        stheta = status.odometry.pose.theta

        dx, dy = tx - sx, ty - sy
        angle_to_target = float(np.arctan2(dy, dx))
        relative_angle = _wrap_angle(angle_to_target - stheta)

        # Drive backwards if target is behind us — minimizes rotation.
        if relative_angle > np.pi / 2:
            relative_angle -= np.pi
            distance = -float(np.sqrt(dx * dx + dy * dy))
        elif relative_angle < -np.pi / 2:
            relative_angle += np.pi
            distance = -float(np.sqrt(dx * dx + dy * dy))
        else:
            distance = float(np.sqrt(dx * dx + dy * dy))

        actual_heading = stheta + relative_angle

        def _goto(linear: float, angular: float) -> None:
            goto_sock.send(msgpack.packb({"linear": linear, "angular": angular}))
            reply = goto_sock.recv_string()
            if reply != "ok":
                raise RuntimeError(f"goto failed: {reply}")

        _goto(0.0, relative_angle)
        _goto(distance, 0.0)
        _goto(0.0, _wrap_angle(ttheta - actual_heading))
    finally:
        status_sock.close()
        goto_sock.close()


def _move_and_wait(target_positions: list[float]) -> None:
    """Send a 10-DOF manipulator command and sleep for trapezoidal ETA.

    Mirrors cure.utils.motion.move_and_wait2: t_joint = v/a + s/v,
    sleep for max across joints. Skips base translate/rotate (indices 0, 1).
    """
    if len(target_positions) != 10:
        raise ValueError(f"need 10 joint positions, got {len(target_positions)}")
    cfg = get_config()

    status_sock = _connect_sub(f"tcp://{SERVER_IP}:{cfg.ports['status']}")
    cmd_sock = _connect_pub(f"tcp://{SERVER_IP}:{cfg.ports['command']}")
    try:
        # Reading status first gives the PUB socket time to settle before
        # the first publish — same trick cure uses to avoid losing the
        # initial frame.
        _, payload = _recv_fresh(status_sock, cfg.timing["max_age_ns"])
        current = Status.from_bytes(payload).joint_positions

        cmd_sock.send_multipart(
            [b"manipulator"]
            + encode_with_timestamp(
                ManipulatorCommand(joint_positions=tuple(target_positions)).to_bytes()
            )
        )

        # Joint indices 2..9 in target_positions / current.
        joint_order = [
            "lift", "arm",
            "wrist_yaw", "wrist_pitch", "wrist_roll",
            "head_pan", "head_tilt",
            "gripper",
        ]
        GRIPPER_MIN, GRIPPER_MAX = 0.2, 4.86425

        max_t = 0.0
        for i, name in enumerate(joint_order, start=2):
            t = float(target_positions[i])
            c = float(current[i])
            if name == "gripper":
                t = float(np.clip(t, GRIPPER_MIN, GRIPPER_MAX))
                c = float(np.clip(c, GRIPPER_MIN, GRIPPER_MAX))
            s = abs(t - c)
            if s == 0.0:
                continue
            v, a = cfg.trapezoid[name]
            max_t = max(max_t, v / a + s / v)

        logger.info("[move_and_wait] sleeping %.3fs for motion", max_t)
        time.sleep(max_t)
    finally:
        cmd_sock.close()
        status_sock.close()


def handover_skill() -> None:
    """3-step handover: present → release → reset arm.

    Mirrors cure.skills.handover.handover_skill exactly: same 10-DOF
    targets, same order.
    """
    cfg = get_config()
    h = cfg.handover
    g_open = cfg.gripper["open"]
    g_close = cfg.gripper["close"]

    # 1. Extend arm to handover pose with gripper still closed.
    _move_and_wait([
        0.0, 0.0,                                  # base translate, rotate
        h["lift"], h["arm"],                       # lift, arm
        h["head_pan"], h["head_tilt"],             # head pan, tilt
        0.0, 0.0, 0.0,                             # wrist yaw, pitch, roll
        g_close,
    ])
    # 2. Open gripper to release the object.
    _move_and_wait([
        0.0, 0.0,
        h["lift"], h["arm"],
        h["head_pan"], h["head_tilt"],
        0.0, 0.0, 0.0,
        g_open,
    ])
    # 3. Retract arm to a stowed pose.
    _move_and_wait([
        0.0, 0.0,
        0.29, 0.0,                                 # lift down, arm in
        h["head_pan"], h["head_tilt"],
        3.0, 0.0, 0.0,                             # wrist yaw rotated
        g_open,
    ])


# ---------- @tool wrappers -----------------------------------------------

# Same surface the LLM agent has used. Only the cure imports inside the
# tool bodies are replaced with calls to the local *_skill functions above.
# pick_up still defers to cure.skills.grasp pending the VLA / breakdown swap.


def _dry_run() -> bool:
    """Read DRY_RUN env on every call so it can be flipped without restart in tests."""
    return os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes", "on")


def _dry_run_transcript() -> str:
    """Canned ASR response for listen() in dry-run mode. Override per-task via env."""
    return os.environ.get("DRY_RUN_TRANSCRIPT", "好的，我是病患")


# Optional: log tool calls to rerun if available, but never fail when it isn't.
try:
    import rerun as rr
    _HAS_RERUN = True
except Exception:
    _HAS_RERUN = False


def _rr_log(channel: str, message: str, level: str = "INFO") -> None:
    if not _HAS_RERUN:
        return
    try:
        rr.log(channel, rr.TextLog(message, level=level))
    except Exception:
        pass


def _check_budget() -> Optional[str]:
    """Return a BLOCKED string if the guard says we're out of budget."""
    guard = get_guard()
    if guard is None:
        return None
    ok, reason = guard.tick()
    if not ok:
        return f"BLOCKED: {reason}"
    return None


@tool
def navigate_to(location: str) -> str:
    """Move the robot to a named location.

    Args:
        location: A friendly location name. Use what_can_i_see() to discover
                  what locations the robot knows about. Common names include
                  "pharmacy", "patient_room", "charging_dock". If you ask for
                  an unknown location the call will fail and you should ask
                  the user where to go.

    Returns:
        Status string. Starts with "OK:" on success, or one of the
        recognizable failure tokens (BLOCKED, UNKNOWN_LOCATION, FAILED).
    """
    if (b := _check_budget()): return b

    guard = get_guard()
    if guard is not None:
        ok, reason = guard.may_navigate(location)
        if not ok:
            return f"BLOCKED: {reason}"

    cure_target = KNOWN_LOCATIONS.get(location)
    if cure_target is None:
        known = sorted(KNOWN_LOCATIONS)
        _rr_log("agent/tool/navigate_to", f"unknown location: {location}", level="WARN")
        return (
            f"UNKNOWN_LOCATION: '{location}' is not in the robot's map. "
            f"Known locations: {known}. Ask the user for guidance or pick one of these."
        )

    if _dry_run():
        msg = (
            f"[DRY_RUN] would call navigate_skill({cure_target!r}). "
            f"VALIDATION: config must know how to plan a path to '{cure_target}' and Nav2 must be running."
        )
        logger.info(msg)
        _rr_log("agent/tool/navigate_to", msg)
        if guard is not None:
            guard.record_navigate(location)
        return f"OK [DRY_RUN]: would arrive at {location}. {msg}"

    _rr_log("agent/tool/navigate_to", f"navigating to {location} (target: {cure_target})")
    try:
        navigate_skill(cure_target)
    except Exception as e:
        _rr_log("agent/tool/navigate_to", f"FAILED: {e}", level="ERROR")
        return f"FAILED: navigation to {location} failed: {e}"

    if guard is not None:
        guard.record_navigate(location)
    return f"OK: arrived at {location}."


@tool
def pick_up(object_name: str) -> str:
    """Use the robot arm to pick up an object the robot can see.

    Preconditions enforced by the guard:
      - Must NOT already be holding something.
      - Must NOT be at the charging_dock.

    Args:
        object_name: What to pick up (e.g. "medicine", "water bottle"). The
                     robot may not be trained to recognize arbitrary objects;
                     unknown objects will cause the underlying grasp to fail.

    Returns:
        Status string starting with "OK:" or a failure token.
    """
    if (b := _check_budget()): return b

    guard = get_guard()
    if guard is not None:
        ok, reason = guard.may_pick_up(object_name)
        if not ok:
            return f"BLOCKED: {reason}"

    cure_target = KNOWN_GRASPABLE_OBJECTS.get(object_name.lower(), object_name)
    is_known = object_name.lower() in KNOWN_GRASPABLE_OBJECTS
    if not is_known:
        _rr_log(
            "agent/tool/pick_up",
            f"attempting unknown object: {object_name} (passing through to cure)",
            level="WARN",
        )

    if _dry_run():
        warn = "" if is_known else (
            f" WARNING: '{object_name}' is NOT in the robot's known grasp classes "
            f"({sorted(set(KNOWN_GRASPABLE_OBJECTS))}); a real grasp would likely fail."
        )
        msg = (
            f"[DRY_RUN] would call grasp_skill({cure_target!r}). "
            f"VALIDATION: ArUco / vision must detect '{cure_target}', IK must reach the grasp pose, "
            f"gripper must close on a real object.{warn}"
        )
        logger.info(msg)
        _rr_log("agent/tool/pick_up", msg)
        if guard is not None:
            guard.record_pick_up(object_name)
        return f"OK [DRY_RUN]: picked up {object_name}. {msg}"

    _rr_log("agent/tool/pick_up", f"grasping {object_name} (target: {cure_target})")
    try:
        # Last remaining cure dependency. Lazy import: the heavy module load
        # (rerun, scipy, opencv, pinocchio) only happens when a real grasp runs.
        from cure.skills.grasp import grasp_skill
        success = grasp_skill(cure_target)
    except TypeError as e:
        _rr_log("agent/tool/pick_up", f"IK failed: {e}", level="ERROR")
        return (
            f"FAILED: detected '{object_name}' but the arm could not reach the grasp pose "
            f"(IK solver returned None): {e}. Reposition or ask the user for help."
        )
    except Exception as e:
        _rr_log("agent/tool/pick_up", f"FAILED: {e}", level="ERROR")
        return f"FAILED: grasp_skill raised: {e}"

    if not success:
        _rr_log("agent/tool/pick_up", f"grasp returned False for {object_name}", level="WARN")
        return (
            f"FAILED: could not grasp '{object_name}'. The object may not be visible, "
            "may not be a class the robot recognizes, or the gripper missed."
        )

    if guard is not None:
        guard.record_pick_up(object_name)
    return f"OK: picked up {object_name}."


@tool
def hand_over() -> str:
    """Hand the currently-held object to the person in front of the robot.

    Preconditions enforced by the guard:
      - Must be holding something.
      - Must NOT be at the charging_dock.

    Returns:
        Status string starting with "OK:" or a failure token.
    """
    if (b := _check_budget()): return b

    guard = get_guard()
    if guard is not None:
        ok, reason = guard.may_hand_over()
        if not ok:
            return f"BLOCKED: {reason}"

    held = guard.holding if guard else "<unknown>"

    if _dry_run():
        msg = (
            f"[DRY_RUN] would call handover_skill(). "
            f"VALIDATION: arm extends to handover pose, gripper opens, recipient must take the item."
        )
        logger.info(msg)
        _rr_log("agent/tool/hand_over", msg)
        if guard is not None:
            guard.record_hand_over()
        return f"OK [DRY_RUN]: handed over {held}. {msg}"

    _rr_log("agent/tool/hand_over", f"handing over {held}")
    try:
        handover_skill()
    except Exception as e:
        _rr_log("agent/tool/hand_over", f"FAILED: {e}", level="ERROR")
        return f"FAILED: handover_skill raised: {e}"

    if guard is not None:
        guard.record_hand_over()
    return f"OK: handed over {held}."


@tool
def speak(text: str) -> str:
    """Make the robot say something out loud (text-to-speech). Blocks until done.

    Args:
        text: What to say. Keep it short and clear; long speech delays the
              entire workflow.

    Returns:
        Status string starting with "OK:" on completion or a failure token.
    """
    if (b := _check_budget()): return b

    if not text or not text.strip():
        return "FAILED: speak requires non-empty text."

    if _dry_run():
        msg = (
            f"[DRY_RUN] would call speak_skill({text!r}). "
            f"VALIDATION: TTS provider (fish_audio per config.yaml) must render and play within 30s."
        )
        logger.info(msg)
        _rr_log("agent/tool/speak", msg)
        return f"OK [DRY_RUN]: said \"{text}\". {msg}"

    _rr_log("agent/tool/speak", f"saying: {text}")
    try:
        job_id = speak_skill(text)
        if job_id is None:
            return "FAILED: speak_skill returned no job id (TTS service may be down)."
        wait_for_speech_completion(job_id, timeout_s=30.0)
    except Exception as e:
        _rr_log("agent/tool/speak", f"FAILED: {e}", level="ERROR")
        return f"FAILED: speak raised: {e}"
    return f"OK: said \"{text}\"."


@tool
def listen() -> str:
    """Listen to the human via the robot microphone and return the transcript.

    Returns:
        The transcribed text on success, or a string starting with FAILED:
        on error / silence.
    """
    if (b := _check_budget()): return b

    if _dry_run():
        canned = _dry_run_transcript()
        msg = (
            f"[DRY_RUN] would call listen_skill(). "
            f"VALIDATION: ASR (deepgram zh-TW per config.yaml, DJI MIC MINI) must capture audio "
            f"and transcribe within timeout. Returning canned: {canned!r}."
        )
        logger.info(msg)
        _rr_log("agent/tool/listen", msg)
        return f"HEARD: {canned}"

    _rr_log("agent/tool/listen", "listening...")
    try:
        transcript = listen_skill() or ""
    except Exception as e:
        _rr_log("agent/tool/listen", f"FAILED: {e}", level="ERROR")
        return f"FAILED: listen raised: {e}"

    transcript = transcript.strip()
    if not transcript:
        return "FAILED: heard nothing — silence or ASR failure."
    _rr_log("agent/tool/listen", f"heard: {transcript}")
    return f"HEARD: {transcript}"


@tool
def what_can_i_see() -> str:
    """Inspect what locations and graspable objects the robot currently knows about,
    plus the robot's current state (location, what it's holding, calls used).

    Call this BEFORE planning navigation or grasping if you are unsure
    whether a target is in the robot's vocabulary. Does not move the robot.
    """
    # Intentionally NOT counted against budget — it's a free read.
    guard = get_guard()
    locs = "\n".join(
        f"  - {name}: {LOCATION_DESCRIPTIONS.get(name, '')}".rstrip()
        for name in sorted(KNOWN_LOCATIONS)
    )
    objs = ", ".join(sorted(set(KNOWN_GRASPABLE_OBJECTS)))
    if guard is not None:
        snap = guard.snapshot()
        state = (
            f"\nCurrent state:\n"
            f"  - location: {snap['location']}\n"
            f"  - holding: {snap['holding'] or 'nothing'}\n"
            f"  - tool calls used: {snap['calls_made']}/{snap['budget']}"
        )
    else:
        state = "\nCurrent state: (no active task — guard not installed)"
    return (
        "Known locations (use these names with navigate_to):\n"
        f"{locs}\n\n"
        f"Graspable object classes (use these with pick_up): {objs}\n"
        "Other object names may be attempted but will likely fail at the "
        "hardware layer; in that case ask the user for help."
        f"{state}"
    )


# ---------- Public exports -----------------------------------------------


def get_robot_tools() -> list:
    """Return the list of tools the agent can call. Order is for prompt clarity."""
    return [
        what_can_i_see,
        navigate_to,
        pick_up,
        hand_over,
        speak,
        listen,
    ]


def build_world_summary() -> str:
    """Summary of the robot's vocabulary, for inclusion in the agent's system prompt."""
    locs = ", ".join(sorted(KNOWN_LOCATIONS))
    objs = ", ".join(sorted(set(KNOWN_GRASPABLE_OBJECTS)))
    return (
        f"Locations the robot can navigate to: {locs}.\n"
        f"Object classes the robot can grasp: {objs}."
    )
