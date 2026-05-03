"""RobotGuard: deterministic precondition + budget enforcement.

Lives outside the LLM. Every robot tool consults the guard before
acting; if a precondition fails the tool returns a structured error
string to the LLM instead of touching the hardware. The LLM cannot
bypass these checks because it never receives the guard object.

Per-task isolation via contextvars: the agent runtime sets a fresh
RobotGuard for each task, so concurrent tasks don't collide.
"""

from __future__ import annotations

import logging
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


CHARGING_DOCK = "charging_dock"


@dataclass
class RobotGuard:
    """Tracks robot state + enforces what the LLM is allowed to do next."""

    location: str = CHARGING_DOCK
    holding: Optional[str] = None
    calls_made: int = 0
    budget: int = 30
    started_at: float = field(default_factory=time.time)

    # ----- budget ------------------------------------------------------

    def tick(self) -> tuple[bool, str]:
        """Charge one tool call. Returns (allowed, reason_if_blocked)."""
        self.calls_made += 1
        if self.calls_made > self.budget:
            return False, (
                f"BUDGET_EXCEEDED: used {self.calls_made} of {self.budget} "
                "tool calls. Stop and report what you have done so far."
            )
        return True, ""

    # ----- preconditions ----------------------------------------------

    def may_navigate(self, location: str) -> tuple[bool, str]:
        # Navigation is unconditionally permitted — the LLM should be
        # able to move around freely. Underlying skill may still fail.
        return True, ""

    def may_pick_up(self, object_name: str) -> tuple[bool, str]:
        if self.holding is not None:
            return False, (
                f"PRECONDITION_FAILED: already holding '{self.holding}'. "
                "Use hand_over() or place it before picking up something else."
            )
        if self.location == CHARGING_DOCK:
            return False, (
                "PRECONDITION_FAILED: cannot pick up at charging_dock. "
                "Navigate to a location where the object is first."
            )
        return True, ""

    def may_hand_over(self) -> tuple[bool, str]:
        if self.holding is None:
            return False, (
                "PRECONDITION_FAILED: not holding anything to hand over. "
                "Pick something up first."
            )
        if self.location == CHARGING_DOCK:
            return False, (
                "PRECONDITION_FAILED: cannot hand over at charging_dock. "
                "Navigate to the recipient first."
            )
        return True, ""

    # ----- state mutators (called only on successful skill execution) -

    def record_navigate(self, location: str) -> None:
        self.location = location

    def record_pick_up(self, object_name: str) -> None:
        self.holding = object_name

    def record_hand_over(self) -> None:
        self.holding = None

    # ----- introspection -----------------------------------------------

    def snapshot(self) -> dict:
        return {
            "location": self.location,
            "holding": self.holding,
            "calls_made": self.calls_made,
            "budget": self.budget,
            "elapsed_seconds": round(time.time() - self.started_at, 2),
        }


_current_guard: ContextVar[Optional[RobotGuard]] = ContextVar(
    "_current_guard", default=None
)


def get_guard() -> Optional[RobotGuard]:
    """Return the guard for the current task, or None if no agent task is active.

    Tools called outside an agent context (e.g. by the legacy scripted
    workflow) get None and skip guard enforcement entirely. This is
    intentional — the scripted workflow has its own deterministic
    sequence and doesn't need the guard.
    """
    return _current_guard.get()


def set_guard(guard: Optional[RobotGuard]):
    """Install a guard for the current async/contextvar context. Returns the Token."""
    return _current_guard.set(guard)


def reset_guard(token) -> None:
    """Restore the previous guard using the Token returned by set_guard."""
    _current_guard.reset(token)
