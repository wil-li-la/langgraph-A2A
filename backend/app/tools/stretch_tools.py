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

import base64
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional, Union

import cv2
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
from app.api import workflow_locations_store as _locations_store

logger = logging.getLogger(__name__)


class LocationNotTaughtError(LookupError):
    """A named location has no pose in the workflow's runtime store."""


def get_workflow_location(
    workflow_id: str, name: str,
) -> tuple[float, float, float]:
    """Resolve a named pose for `workflow_id` from the runtime store.

    Raises `LocationNotTaughtError` if the name has not been taught yet —
    callers must surface this to the operator, not silently substitute
    a default.
    """
    locs = _locations_store.load(workflow_id)
    if name not in locs:
        raise LocationNotTaughtError(
            f"Location {name!r} for workflow {workflow_id!r} has not been "
            f"taught. Open the dashboard, drive the robot to the spot, and "
            f"click Save in the {workflow_id} card's Locations panel."
        )
    loc = locs[name]
    return float(loc.x), float(loc.y), float(loc.theta)


# The LLM-driven delivery agent uses the same teach-and-save store as the
# scripted medication_delivery workflow. If a new workflow gets an agentic
# counterpart later, give it its own WORKFLOW_ID and override.
_DELIVERY_AGENT_WORKFLOW_ID = "medication_delivery"


# ---------- Robot connection + config ------------------------------------

SERVER_IP = os.getenv("ROBOT_IP", "localhost")

_DEFAULT_PORTS = {
    "status": 5555,
    "command": 5556,
    "goto": 5557,
    "arducam": 6000,
    "d435if": 6001,
    "d405": 6002,
    # Head (top) camera: an independent publisher with its own wire format —
    # single-part msgpack {ts_ns, h, w, encoding, data}. Color is JPEG bytes
    # (encoding="rgb8" describes the *source*, not the bytes), depth is raw
    # 16UC1. Distinct from the multi-part 6001 stream above.
    "head_color": 6011,
    "head_depth": 6010,
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


def navigate_skill(x: float, y: float, theta: float) -> None:
    """Drive the base to an absolute (x, y, theta) pose.

    Sends a single msgpack {"x", "y", "theta"} goal to the on-robot goto
    service (port `goto`), which proxies to the lab nav_service and plans
    via Nav2's `BasicNavigator.goToPose()`. Blocks until the server replies
    "ok"; raises RuntimeError with the server's status string on any other
    reply (e.g. "no_path: ...", "timeout: ...", "obstructed: ...").

    Wire format must match the goto service in stretch3-zmq — see
    docs/stretch_server_goto_refactor.md for the server-side contract.

    Callers that want to navigate by name should resolve coordinates with
    `get_object_pose()` first, then pass them here.
    """
    cfg = get_config()
    goto_sock = _connect_req(f"tcp://{SERVER_IP}:{cfg.ports['goto']}")
    try:
        goto_sock.send(msgpack.packb({"x": float(x), "y": float(y), "theta": float(theta)}))
        reply = goto_sock.recv_string()
        if reply != "ok":
            raise RuntimeError(f"goto failed: {reply}")
    finally:
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


def _read_current_joints(timeout_s: float = 1.0) -> tuple[float, ...]:
    """Snapshot the latest 10-DOF joint positions from the status stream."""
    cfg = get_config()
    sock = _connect_sub(f"tcp://{SERVER_IP}:{cfg.ports['status']}")
    try:
        # max_age controls freshness; bound the overall wait via RCVTIMEO too.
        sock.setsockopt(zmq.RCVTIMEO, int(timeout_s * 1000))
        _, payload = _recv_fresh(sock, cfg.timing["max_age_ns"])
        return Status.from_bytes(payload).joint_positions
    finally:
        sock.close()


def set_gripper_skill(opening_value: float) -> None:
    """Open or close the gripper by overriding only joint index 9.

    `opening_value` is the raw gripper position (cure config units —
    typically 0.0..100.0 with the driver clamping to [0.2, 4.86425]).
    Use cfg.gripper["open"] / ["close"] for the canonical values rather
    than hardcoding here.

    The trapezoidal ETA in `_move_and_wait` underestimates the real
    gripper settling time by ~1 s in measurements, so we additionally
    poll the joint until it stops moving (or hit a 2 s ceiling). This
    makes "OK: gripper open/close" honest about the gripper being at
    rest.
    """
    current = list(_read_current_joints())
    if len(current) != 10:
        raise RuntimeError(f"expected 10 joint positions, got {len(current)}")
    target = list(current)
    target[0] = 0.0
    target[1] = 0.0
    target[9] = float(opening_value)
    _move_and_wait(target)

    # Poll until the gripper stops drifting, with a hard ceiling.
    deadline = time.monotonic() + 2.0
    last = float(_read_current_joints()[9])
    while time.monotonic() < deadline:
        time.sleep(0.2)
        now = float(_read_current_joints()[9])
        if abs(now - last) < 0.01:
            return
        last = now


def move_arm_skill(height_m: float) -> None:
    """Set the lift (vertical arm position) to height_m meters.

    Reads current joint positions and sends a ManipulatorCommand that
    overrides only index 2 (lift). Base translate/rotate are zeroed (no-op,
    same as handover_skill); arm extension and wrist remain wherever the
    LLM last left them.
    """
    current = list(_read_current_joints())
    if len(current) != 10:
        raise RuntimeError(f"expected 10 joint positions, got {len(current)}")
    target = list(current)
    target[0] = 0.0
    target[1] = 0.0
    target[2] = float(height_m)
    _move_and_wait(target)


def look_around_skill(pan: float, tilt: float) -> None:
    """Aim the head to (pan, tilt) radians, holding every other joint in place.

    Reads the current joint positions and sends a ManipulatorCommand that
    overrides only indices 7 (head_pan) and 8 (head_tilt). Indices 0,1
    (base translate/rotate) are zeroed — matching handover_skill — so the
    base does not move.
    """
    current = list(_read_current_joints())
    if len(current) != 10:
        raise RuntimeError(f"expected 10 joint positions, got {len(current)}")
    target = list(current)
    target[0] = 0.0
    target[1] = 0.0
    target[7] = float(pan)
    target[8] = float(tilt)
    _move_and_wait(target)


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


# Providers whose default chat model in app.llm.factory supports image input.
# Ollama is excluded from auto-on because most local models are text-only; users
# running llava / qwen2-vl / gemma3 can opt in with AGENT_VISION=1.
_VISION_DEFAULT_PROVIDERS = {"openai", "google", "anthropic"}


def _vision_enabled() -> bool:
    """Whether take_photo should return image content blocks to the LLM.

    Priority: explicit AGENT_VISION override → auto-detect from LLM_PROVIDER.
    Read on every call so tests / agent.py callers can flip without restart.
    """
    explicit = os.environ.get("AGENT_VISION", "").lower()
    if explicit in ("1", "true", "yes", "on"):
        return True
    if explicit in ("0", "false", "no", "off"):
        return False
    return os.environ.get("LLM_PROVIDER", "none").lower() in _VISION_DEFAULT_PROVIDERS


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

    Worked example — "deliver aspirin to Mr. Wang in patient_room":
        navigate_to("pharmacy")        # go pick up the medicine
        pick_up("medicine")
        navigate_to("patient_room")    # then go to the patient
        hand_over()

    If you get back UNKNOWN_LOCATION, do NOT retry the same name — the
    error message lists the locations that DO exist. Pick one of those,
    or call what_can_i_see() / ask the user.
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

    try:
        tx, ty, ttheta = get_workflow_location(
            _DELIVERY_AGENT_WORKFLOW_ID, cure_target,
        )
    except LocationNotTaughtError as e:
        _rr_log("agent/tool/navigate_to", f"pose lookup failed: {e}", level="ERROR")
        return f"UNKNOWN_LOCATION: {e}"

    if _dry_run():
        msg = (
            f"[DRY_RUN] would call navigate_skill(x={tx:.3f}, y={ty:.3f}, theta={ttheta:.3f}) "
            f"for '{cure_target}'. "
            f"VALIDATION: Nav2 must be running and able to plan a path to that pose."
        )
        logger.info(msg)
        _rr_log("agent/tool/navigate_to", msg)
        if guard is not None:
            guard.record_navigate(location)
        return f"OK [DRY_RUN]: would arrive at {location}. {msg}"

    _rr_log(
        "agent/tool/navigate_to",
        f"navigating to {location} (target: {cure_target}, "
        f"pose: x={tx:.3f}, y={ty:.3f}, theta={ttheta:.3f})",
    )
    try:
        navigate_skill(tx, ty, ttheta)
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

    Worked example — pick up the bottle of pills sitting on the pharmacy shelf:
        navigate_to("pharmacy")        # must be at the shelf first
        view_arm_camera()              # confirm the bottle is in view
        pick_up("medicine")            # grasp it
        # If pick_up returns FAILED, the bottle was not detected or the IK
        # could not reach. Recover with move_arm() / look_around() to reposition,
        # or ask the user. Do NOT keep retrying pick_up unchanged.
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

    Worked example — give the medication to a verified patient:
        speak("Mr. Wang, here is your aspirin. Please take it from me.")
        hand_over()                          # arm extends, gripper opens
        speak("Thank you. I will return to the dock.")
        navigate_to("charging_dock")

    Do not call hand_over() until you have verified the patient with
    speak() / listen() — handing medication to the wrong person is the
    worst failure mode for this robot.
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
def move_arm(height_m: float) -> str:
    """Raise or lower the robot arm to a target vertical height (meters).

    Controls the `lift` joint only — the mast slider that moves the whole
    arm assembly up and down. Horizontal arm extension and wrist pose are
    not changed.

    Args:
        height_m: Target lift height in meters from the base. 0.15 = arm
                  fully down (just clear of the base), ~1.05 = arm near
                  the top of the mast. The cure handover pose uses 0.5.

    Returns:
        Status string starting with "OK:" or a failure token.

    Worked example — pick up an object from a low shelf:
        move_arm(0.20)                       # lower the arm to shelf height
        view_arm_camera()                    # confirm what is in front of gripper
        pick_up("medicine")

    Common height presets:
        move_arm(0.20)   # low — floor / bottom shelf
        move_arm(0.50)   # mid — table / handover height
        move_arm(0.90)   # high — eye level / top shelf
    """
    if (b := _check_budget()): return b

    LIFT_MIN_M, LIFT_MAX_M = 0.15, 1.05
    if not (LIFT_MIN_M <= height_m <= LIFT_MAX_M):
        return (
            f"FAILED: height_m={height_m} out of safe range "
            f"[{LIFT_MIN_M}, {LIFT_MAX_M}] m."
        )

    if _dry_run():
        msg = (
            f"[DRY_RUN] would set lift to {height_m:.3f} m. "
            f"VALIDATION: mast must reach target without colliding with the arm or environment."
        )
        logger.info(msg)
        _rr_log("agent/tool/move_arm", msg)
        return f"OK [DRY_RUN]: arm lift set to {height_m:.3f} m. {msg}"

    _rr_log("agent/tool/move_arm", f"setting lift to {height_m:.3f} m")
    try:
        move_arm_skill(float(height_m))
    except Exception as e:
        _rr_log("agent/tool/move_arm", f"FAILED: {e}", level="ERROR")
        return f"FAILED: move_arm raised: {e}"

    return f"OK: arm lift set to {height_m:.3f} m."


@tool
def set_gripper(state: str) -> str:
    """Open or close the robot's gripper.

    Opening the gripper releases whatever is held (so it will also clear
    the robot's "holding" state). Closing the gripper alone does NOT count
    as a successful grasp — use pick_up() for that; close_gripper here
    is mainly for staging the gripper before a manual grasp attempt or
    after dropping something accidentally.

    Args:
        state: "open" or "close" (also accepts "closed", "shut").

    Returns:
        Status string starting with "OK:" or a failure token.

    Worked example — recover after fumbling an object you were holding:
        # guard.holding still says "medicine" but you just dropped it
        set_gripper("open")        # clears holding state too
        view_arm_camera()          # see where the object landed
        # navigate / move_arm to reposition, then pick_up again

    Or — stage gripper before a manual grasp attempt:
        set_gripper("open")
        move_arm(0.30)
        pick_up("water bottle")    # closes gripper as part of grasping
    """
    if (b := _check_budget()): return b

    s = (state or "").strip().lower()
    if s == "open":
        opening = float(get_config().gripper["open"])
        action = "open"
    elif s in ("close", "closed", "shut"):
        opening = float(get_config().gripper["close"])
        action = "close"
    else:
        return (
            f"FAILED: state={state!r} not understood. "
            "Use 'open' or 'close'."
        )

    if _dry_run():
        msg = (
            f"[DRY_RUN] would set gripper to {action} "
            f"(joint index 9 = {opening}). "
            f"VALIDATION: gripper servo must reach target without crushing the held object."
        )
        logger.info(msg)
        _rr_log("agent/tool/set_gripper", msg)
        if action == "open":
            guard = get_guard()
            if guard is not None and guard.holding is not None:
                guard.holding = None
        return f"OK [DRY_RUN]: gripper {action}. {msg}"

    _rr_log("agent/tool/set_gripper", f"setting gripper to {action} ({opening})")
    try:
        set_gripper_skill(opening)
    except Exception as e:
        _rr_log("agent/tool/set_gripper", f"FAILED: {e}", level="ERROR")
        return f"FAILED: set_gripper raised: {e}"

    # Opening the gripper physically releases anything that was held.
    # Mirror that in guard state so subsequent pick_up() preconditions
    # don't think we're still holding something.
    if action == "open":
        guard = get_guard()
        if guard is not None and guard.holding is not None:
            released = guard.holding
            guard.holding = None
            return f"OK: gripper opened (released '{released}')."

    return f"OK: gripper {action}."


@tool
def view_arm_camera() -> Union[str, list]:
    """Capture a single RGB frame from the robot's arm/wrist camera (d405).

    The wrist camera looks down past the gripper, so this is the right tool
    when you need to see the workspace immediately in front of the arm —
    e.g., to verify what you are about to grasp, or to confirm a placement.
    For a wider scene view, use look_around() + take_photo() (head camera).

    The image is saved to disk and logged to Rerun. When AGENT_VISION=1
    (or LLM_PROVIDER is a vision-capable provider), the image is also
    returned inline as a content block so the model can see it.

    Returns:
        On success, either a "OK:" status string (text-only mode) or a list
        of content blocks `[{"type":"text",...}, {"type":"image_url",...}]`
        (vision mode). On failure, a string starting with FAILED:.

    Worked example — verify the right object is under the gripper before grasping:
        navigate_to("pharmacy")
        move_arm(0.30)              # lower toward the shelf
        view_arm_camera()           # see what is under the gripper
        # If wrong item is visible, move_arm() / pan the base, look again.
        pick_up("medicine")
    """
    if (b := _check_budget()): return b

    if _dry_run():
        msg = (
            "[DRY_RUN] would capture one RGB frame from arm camera (d405). "
            "VALIDATION: stretch3-zmq driver must be publishing on the d405 port."
        )
        logger.info(msg)
        _rr_log("agent/tool/view_arm_camera", msg)
        return f"OK [DRY_RUN]: captured arm-camera photo. {msg}"

    try:
        frame, ts_ns = _capture_arm_frame(timeout_s=5.0)
    except Exception as e:
        _rr_log("agent/tool/view_arm_camera", f"FAILED: {e}", level="ERROR")
        return f"FAILED: view_arm_camera raised: {e}"

    return _photo_response(
        frame,
        ts_ns,
        file_prefix="arm",
        rerun_channel="agent/tool/view_arm_camera",
        camera_label="arm (d405 wrist) camera",
        followup_hint=(
            "call move_arm() or pick_up() if you want to act on what you see"
        ),
    )


@tool
def look_around(pan_deg: float = 0.0, tilt_deg: float = 0.0) -> str:
    """Aim the robot's head camera by setting pan and tilt angles (in degrees).

    The robot's "top camera" (d435if) is mounted on the head, so this moves
    the camera's field of view without moving the base or arm. Call this
    to look at something, then call take_photo() to capture what you see.

    Args:
        pan_deg: Horizontal head rotation in degrees. Positive = look LEFT,
                 negative = look RIGHT, 0 = straight ahead. Range: -210..90.
        tilt_deg: Vertical head rotation in degrees. Positive = look UP,
                  negative = look DOWN, 0 = horizon. Range: -85..45.

    Returns:
        Status string starting with "OK:" or a failure token.

    Worked example — "find the medication bottle on the table to my right":
        look_around(pan_deg=-45, tilt_deg=-25)   # head turns right and down
        take_photo()                              # see what is on the table
        # ... reason about the image, then act ...

    Common aim presets:
        look_around(0, 0)        # reset — eye-level, straight ahead
        look_around(0, -30)      # look down at the workspace in front of the
                                 # gripper (useful before pick_up)
        look_around(0, 20)       # look slightly up — patient's face when the
                                 # robot is at bedside
        look_around(-45, 0)      # scan to the right (e.g., next bed over)
        look_around(45, 0)       # scan to the left

    Sign convention is from the robot's point of view: imagine you are the
    robot. Positive pan rotates your head toward your own left hand.
    """
    if (b := _check_budget()): return b

    PAN_MIN_DEG, PAN_MAX_DEG = -210.0, 90.0
    TILT_MIN_DEG, TILT_MAX_DEG = -85.0, 45.0
    if not (PAN_MIN_DEG <= pan_deg <= PAN_MAX_DEG):
        return (
            f"FAILED: pan_deg={pan_deg} out of range "
            f"[{PAN_MIN_DEG}, {PAN_MAX_DEG}]."
        )
    if not (TILT_MIN_DEG <= tilt_deg <= TILT_MAX_DEG):
        return (
            f"FAILED: tilt_deg={tilt_deg} out of range "
            f"[{TILT_MIN_DEG}, {TILT_MAX_DEG}]."
        )

    pan_rad = float(np.deg2rad(pan_deg))
    tilt_rad = float(np.deg2rad(tilt_deg))

    if _dry_run():
        msg = (
            f"[DRY_RUN] would aim head to pan={pan_deg}° ({pan_rad:.3f} rad), "
            f"tilt={tilt_deg}° ({tilt_rad:.3f} rad). "
            f"VALIDATION: head servos must reach target without colliding with arm."
        )
        logger.info(msg)
        _rr_log("agent/tool/look_around", msg)
        return f"OK [DRY_RUN]: head aimed to pan={pan_deg}°, tilt={tilt_deg}°. {msg}"

    _rr_log(
        "agent/tool/look_around",
        f"aiming head to pan={pan_deg}°, tilt={tilt_deg}°",
    )
    try:
        look_around_skill(pan_rad, tilt_rad)
    except Exception as e:
        _rr_log("agent/tool/look_around", f"FAILED: {e}", level="ERROR")
        return f"FAILED: look_around raised: {e}"

    return (
        f"OK: head aimed to pan={pan_deg}°, tilt={tilt_deg}°. "
        f"Call take_photo() to see what is in view."
    )


@tool
def take_photo() -> Union[str, list]:
    """Capture a single RGB frame from the robot's head camera (d435if).

    The image is saved to disk and logged to the operator's monitoring tool
    (Rerun). When the configured LLM is vision-capable, the image is also
    returned inline as a content block so the model can SEE the scene.
    Use after look_around() to inspect a specific direction.

    Returns:
        On success, either a "OK:" status string (text-only mode) or a list of
        content blocks `[{"type":"text",...}, {"type":"image_url",...}]` (vision
        mode). On failure, a string starting with FAILED:.

    Worked example — scan for the patient in a room before approaching:
        navigate_to("patient_room")
        look_around(0, 0)           # face forward
        take_photo()                # is the patient in view?
        # If not, scan:
        look_around(-45, 0); take_photo()
        look_around(45, 0);  take_photo()
        # Then act on what you saw.
    """
    if (b := _check_budget()): return b

    if _dry_run():
        msg = (
            "[DRY_RUN] would capture one RGB frame from head camera (d435if). "
            "VALIDATION: stretch3-zmq driver must be publishing on the d435if port."
        )
        logger.info(msg)
        _rr_log("agent/tool/take_photo", msg)
        return f"OK [DRY_RUN]: captured photo. {msg}"

    try:
        frame, ts_ns = _capture_head_frame(timeout_s=5.0)
    except Exception as e:
        _rr_log("agent/tool/take_photo", f"FAILED: {e}", level="ERROR")
        return f"FAILED: take_photo raised: {e}"

    return _photo_response(
        frame,
        ts_ns,
        file_prefix="photo",
        rerun_channel="agent/tool/take_photo",
        camera_label="head camera",
        followup_hint=(
            "call look_around() to look elsewhere, then take_photo() again"
        ),
    )


def _photo_response(
    frame: np.ndarray,
    ts_ns: int,
    *,
    file_prefix: str,
    rerun_channel: str,
    camera_label: str,
    followup_hint: str,
) -> Union[str, list]:
    """Save a captured frame, log to Rerun, and produce the tool return value.

    Shared by take_photo() (head camera) and view_arm_camera() (d405). Returns
    either a plain "OK:" string (text-only mode) or a list of OpenAI-style
    content blocks (vision mode) — the latter is what enables a vision-capable
    LLM to actually see the image.
    """
    h, w = frame.shape[:2]
    save_dir = Path(os.getenv("ROBOT_SCREENSHOT_DIR", "/tmp/robot_screenshots"))
    save_dir.mkdir(parents=True, exist_ok=True)
    path = save_dir / f"{file_prefix}_{ts_ns}.jpg"
    cv2.imwrite(str(path), frame)

    if _HAS_RERUN:
        try:
            rr.log(
                f"{rerun_channel}/image",
                rr.Image(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)),
            )
        except Exception:
            pass
    _rr_log(rerun_channel, f"saved {path} ({w}x{h})")

    text_summary = (
        f"OK: captured {w}x{h} photo from {camera_label}, saved to {path}."
    )

    if not _vision_enabled():
        return (
            f"{text_summary} (Vision disabled — the operator can view the image "
            "in Rerun. Set AGENT_VISION=1 with a vision-capable LLM to see it "
            f"inline. To get another view, {followup_hint}.)"
        )

    # Encode JPEG and base64 for the LLM. Quality 85 keeps payloads under
    # ~150KB while remaining sharp enough for VLM reasoning.
    ok, jpg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    if not ok:
        return (
            f"{text_summary} (Failed to encode the image for the LLM; "
            "operator can still view it in Rerun.)"
        )
    b64 = base64.b64encode(jpg.tobytes()).decode("ascii")
    data_url = f"data:image/jpeg;base64,{b64}"

    # OpenAI-style content blocks. langchain's ChatOpenAI, ChatAnthropic, and
    # ChatGoogleGenerativeAI all accept this shape from ToolMessage.content
    # and convert it to provider-native multimodal input internally.
    return [
        {
            "type": "text",
            "text": (
                f"{text_summary} What you see is below. Reason from the image, "
                f"then decide your next action ({followup_hint}, or another tool)."
            ),
        },
        {"type": "image_url", "image_url": {"url": data_url}},
    ]


def _capture_head_frame(timeout_s: float = 5.0) -> tuple[np.ndarray, int]:
    """Receive one color frame from the head/top camera (port head_color=6011).

    Wire format (separate publisher from the d435if multi-part stream):
        single ZMQ frame containing msgpack-encoded
        {ts_ns, h, w, encoding, data}
    where `data` is a JPEG-encoded buffer for color streams (the `encoding`
    field describes the source format pre-encoding, not the bytes on the
    wire). Returned frame is BGR uint8, ready for cv2.imwrite / imencode.
    """
    cfg = get_config()
    sock = _ctx().socket(zmq.SUB)
    sock.setsockopt(zmq.RCVHWM, 1)
    sock.setsockopt(zmq.SUBSCRIBE, b"")  # publisher sends single-part frames
    sock.setsockopt(zmq.RCVTIMEO, int(timeout_s * 1000))
    sock.connect(f"tcp://{SERVER_IP}:{cfg.ports['head_color']}")
    try:
        raw = sock.recv()
        msg = msgpack.unpackb(raw, raw=False)
        ts_ns = int(msg.get("ts_ns", time.time_ns()))
        data = msg.get("data")
        if not isinstance(data, (bytes, bytearray)):
            raise RuntimeError(
                f"head camera frame missing 'data' field; got keys={list(msg.keys())}"
            )
        frame = cv2.imdecode(
            np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR
        )
        if frame is None:
            raise RuntimeError(
                f"cv2 could not decode head camera buffer "
                f"(encoding={msg.get('encoding')!r}, {len(data)} bytes)"
            )
        # The d435if is physically mounted rotated 90° CW on the Stretch head,
        # so the publisher's raw frames come out sideways. Rotate CCW here so
        # disk dumps and VLM input are right-side-up.
        frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
        return frame, ts_ns
    finally:
        sock.close()


def _capture_arm_frame(timeout_s: float = 5.0) -> tuple[np.ndarray, int]:
    """Receive one color frame from the arm/wrist camera (d405, port 6002).

    Uses the legacy stretch3-zmq multi-part wire format `[topic, ts, payload]`
    with the payload blosc2-compressed (or raw, with cv2.imdecode fallback).
    Mirrors the receive path in app.api.camera.mjpeg_generator. No physical
    rotation needed — the d405 is mounted upright on the wrist.
    """
    cfg = get_config()
    sock = _ctx().socket(zmq.SUB)
    sock.setsockopt(zmq.RCVHWM, 1)
    sock.setsockopt(zmq.SUBSCRIBE, b"rgb")
    sock.setsockopt(zmq.RCVTIMEO, int(timeout_s * 1000))
    sock.connect(f"tcp://{SERVER_IP}:{cfg.ports['d405']}")
    try:
        parts = sock.recv_multipart()
        if not parts or parts[0] != b"rgb":
            raise RuntimeError(
                f"expected rgb topic, got {parts[0] if parts else 'empty'!r}"
            )
        ts_ns, payload = decode_with_timestamp(parts[1:])

        try:
            import blosc2  # type: ignore
            raw = bytes(blosc2.decompress(payload))
        except Exception:
            raw = payload

        if len(raw) == 640 * 480 * 3:
            frame = np.frombuffer(raw, dtype=np.uint8).reshape(480, 640, 3)
        elif len(raw) == 1280 * 720 * 3:
            frame = np.frombuffer(raw, dtype=np.uint8).reshape(720, 1280, 3)
        else:
            frame = cv2.imdecode(np.frombuffer(payload, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise RuntimeError(
                f"failed to decode d405 frame (raw_len={len(raw)})"
            )
        return frame, ts_ns
    finally:
        sock.close()


@tool
def speak(text: str) -> str:
    """Make the robot say something out loud (text-to-speech). Blocks until done.

    Args:
        text: What to say. Keep it short and clear; long speech delays the
              entire workflow.

    Returns:
        Status string starting with "OK:" on completion or a failure token.

    Worked example — verify patient identity before handing over medication:
        speak("Hello, are you Mr. Wang?")
        reply = listen()                     # returns "HEARD: yes I am" etc.
        if "yes" in reply.lower() or "是" in reply:
            hand_over()
        else:
            speak("I am sorry, I was looking for Mr. Wang. Goodbye.")

    Match the user's language. Use short sentences (<15 words). The robot's
    TTS quality drops on long or complex sentences.
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

    Worked example — pair with speak() in a question/answer loop. listen()
    always follows speak() and never the other way around (the human is not
    going to monologue at an idle robot):
        speak("What is your name?")
        reply = listen()           # e.g. "HEARD: 王小明" or "FAILED: heard nothing"
        if reply.startswith("FAILED:"):
            speak("I did not catch that. Could you repeat please?")
            reply = listen()       # retry once, not in a loop
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
    Does NOT count against your tool-call budget — it's a free read.

    Worked example — start of an open-ended task like "bring me my pills":
        what_can_i_see()       # → shows: locations [pharmacy, patient_room, ...]
                               #          objects   [medicine, pills, ...]
                               #          state     location=charging_dock, holding=nothing
        # Now you know "pills" is a recognized object and you start at the
        # dock. Plan: navigate_to("pharmacy") -> pick_up("pills") -> ...

    A call here is cheaper than a UNKNOWN_LOCATION / failed pick_up later.
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
        look_around,
        take_photo,
        move_arm,
        view_arm_camera,
        set_gripper,
    ]


def build_world_summary() -> str:
    """Summary of the robot's vocabulary, for inclusion in the agent's system prompt."""
    locs = ", ".join(sorted(KNOWN_LOCATIONS))
    objs = ", ".join(sorted(set(KNOWN_GRASPABLE_OBJECTS)))
    return (
        f"Locations the robot can navigate to: {locs}.\n"
        f"Object classes the robot can grasp: {objs}."
    )
