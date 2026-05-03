"""Single source of truth for the robot's runtime environment.

See docs/superpowers/specs/2026-05-03-environment-state-design.md for
the layered architecture (world catalog / environment store / episodic
memory) and the camera identity-match flow this enables.

Phase 0: this package ships isolated. The store is built on first
`get_environment()` call but nothing in the rest of the codebase
imports from here yet. Wiring happens in Phase 1.
"""

from app.environment.store import (
    EnvironmentStore,
    _reset_for_tests,
    get_environment,
)
from app.environment.types import (
    EnvironmentSnapshot,
    GraspableClass,
    KnownPerson,
    Location,
    PerceptionCache,
    PerceptionEvent,
    PerceptionKind,
    RobotKinematicState,
    TaskContext,
    TaskStatus,
    WorldCatalog,
)

__all__ = [
    "EnvironmentSnapshot",
    "EnvironmentStore",
    "GraspableClass",
    "KnownPerson",
    "Location",
    "PerceptionCache",
    "PerceptionEvent",
    "PerceptionKind",
    "RobotKinematicState",
    "TaskContext",
    "TaskStatus",
    "WorldCatalog",
    "get_environment",
    "_reset_for_tests",
]
