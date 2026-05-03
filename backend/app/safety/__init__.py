"""Deterministic safety guardrails wrapping LLM-driven tool calls.

The guard exists OUTSIDE the LLM. It enforces preconditions the LLM
cannot bypass and counts tool calls against a per-task budget. Tools
read the current guard via a contextvar so concurrent tasks don't
share state.
"""

from app.safety.guard import RobotGuard, get_guard, set_guard, reset_guard

__all__ = ["RobotGuard", "get_guard", "set_guard", "reset_guard"]
