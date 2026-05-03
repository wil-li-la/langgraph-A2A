"""LangChain @tool wrappers around cure robot skills.

Design principles:
- Generic surface: pick_up("water bottle") works the same as pick_up("medicine").
- Tools never raise. On failure they return a string starting with a
  recognizable token (BLOCKED:, UNKNOWN_LOCATION:, FAILED:, etc.) so the
  LLM can reason about what went wrong instead of crashing the loop.
- Tools consult app.safety.guard.RobotGuard for preconditions and budget.
- Cure imports are lazy so non-agent code can import this module without
  triggering cure's module-load side effects.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from langchain_core.tools import tool

from app.safety.guard import get_guard
from app.tools.world_model import (
    KNOWN_LOCATIONS,
    KNOWN_GRASPABLE_OBJECTS,
    LOCATION_DESCRIPTIONS,
)

logger = logging.getLogger(__name__)


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


# ---------- Tools ---------------------------------------------------------


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
            f"VALIDATION: cure must know how to plan a path to '{cure_target}' and Nav2 must be running."
        )
        logger.info(msg)
        _rr_log("agent/tool/navigate_to", msg)
        if guard is not None:
            guard.record_navigate(location)
        return f"OK [DRY_RUN]: would arrive at {location}. {msg}"

    _rr_log("agent/tool/navigate_to", f"navigating to {location} (cure target: {cure_target})")
    try:
        from cure.skills.navigate import navigate_skill
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

    _rr_log("agent/tool/pick_up", f"grasping {object_name} (cure target: {cure_target})")
    try:
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
        from cure.skills.handover import handover_skill
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
        from cure.skills.speak import speak_skill, wait_for_speech_completion
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
        from cure.skills.listen import listen_skill
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
