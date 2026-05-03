"""LangChain tool wrappers around cure robot skills.

Generic, domain-agnostic surface — `pick_up('water bottle')` works the
same way as `pick_up('medicine')`. The robot may not actually know what
a water bottle is; in that case the underlying skill fails and the tool
returns a clear error string for the LLM to reason about.

Tools are safe to call without an active RobotGuard (the guard is
optional — see app.safety.guard.get_guard for details).
"""

from app.tools.cure_tools import get_robot_tools, build_world_summary

__all__ = ["get_robot_tools", "build_world_summary"]
