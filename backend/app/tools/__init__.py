"""LangChain tool wrappers around the stretch3-zmq driver.

Generic, domain-agnostic surface — `pick_up('water bottle')` works the
same way as `pick_up('medicine')`. The robot may not actually know what
a water bottle is; in that case the underlying skill fails and the tool
returns a clear error string for the LLM to reason about.

Tools are safe to call without an active RobotGuard (the guard is
optional — see app.safety.guard.get_guard for details).
"""

from app.tools.detect_tools import get_detect_tools
from app.tools.stretch_tools import build_world_summary
from app.tools.stretch_tools import get_robot_tools as _get_stretch_tools


def get_robot_tools() -> list:
    """All tools the agent can call: stretch skills + VLM detect/recall."""
    return [*_get_stretch_tools(), *get_detect_tools()]


__all__ = ["get_robot_tools", "build_world_summary", "get_detect_tools"]
