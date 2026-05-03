"""EnvironmentStore — single in-process owner of the robot's runtime state.

See docs/superpowers/specs/2026-05-03-environment-state-design.md.

This module ships isolated. Nothing else in the codebase imports it yet;
RobotGuard and tools/world_model.py keep their own state until the next
PR explicitly migrates them.
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import yaml

from app.environment.types import (
    EnvironmentSnapshot,
    GraspableClass,
    KnownPerson,
    Location,
    PerceptionCache,
    PerceptionEvent,
    RobotKinematicState,
    TaskContext,
    TaskStatus,
    WorldCatalog,
)

logger = logging.getLogger(__name__)


_DEFAULT_BUDGET = 30
_WORLD_YAML_PATH = Path(__file__).resolve().parent / "world.yaml"


class EnvironmentStore:
    """Lock-protected blackboard for robot state, perception, and task context.

    Reads return atomic Pydantic copies (no shared references). Writes acquire
    the lock briefly. Catalog is loaded once at construction; hot-reload is a
    later spec.
    """

    def __init__(self, world_yaml_path: Path | None = None) -> None:
        self._lock = threading.RLock()
        self._robot = RobotKinematicState()
        self._perception = PerceptionCache()
        self._task: TaskContext | None = None
        self._catalog = _load_catalog(world_yaml_path or _WORLD_YAML_PATH)

    # ---- read (lock-free; Pydantic models are returned by deep copy) -----

    def snapshot(self) -> EnvironmentSnapshot:
        """Atomic deep-copy of everything. Safe to serialize."""
        with self._lock:
            return EnvironmentSnapshot(
                robot=self._robot.model_copy(deep=True),
                perception=PerceptionCache(
                    events=self._fresh_perception_events(),
                    ttl_seconds=self._perception.ttl_seconds,
                ),
                task=self._task.model_copy(deep=True) if self._task else None,
                catalog_version=self._catalog.version,
            )

    def robot(self) -> RobotKinematicState:
        with self._lock:
            return self._robot.model_copy(deep=True)

    def perception(
        self, kind: str | None = None, source: str | None = None
    ) -> list[PerceptionEvent]:
        """Return perception events newer than `ttl_seconds`. Optionally filter."""
        with self._lock:
            fresh = self._fresh_perception_events()
        out = fresh
        if kind is not None:
            out = [e for e in out if e.kind == kind]
        if source is not None:
            out = [e for e in out if e.source == source]
        # newest first
        out.sort(key=lambda e: e.timestamp, reverse=True)
        return out

    def task(self) -> TaskContext | None:
        with self._lock:
            return self._task.model_copy(deep=True) if self._task else None

    def catalog(self) -> WorldCatalog:
        # Catalog is treated as immutable post-load; safe to return without copy.
        return self._catalog

    # ---- write -----------------------------------------------------------

    def update_robot(self, **fields: Any) -> RobotKinematicState:
        """Patch the kinematic state. Touches only fields explicitly passed."""
        with self._lock:
            current = self._robot.model_dump()
            current.update({k: v for k, v in fields.items() if v is not None or k == "holding"})
            current["last_updated"] = datetime.utcnow()
            self._robot = RobotKinematicState(**current)
            return self._robot.model_copy(deep=True)

    def record_perception(self, event: PerceptionEvent) -> None:
        """Append a perception event. Eviction of stale entries happens on read."""
        with self._lock:
            self._perception.events.append(event)

    def begin_task(
        self,
        target: dict[str, Any] | None = None,
        budget: int = _DEFAULT_BUDGET,
        task_id: str | None = None,
    ) -> TaskContext:
        """Start a new task context. Replaces any existing one (single-task store)."""
        with self._lock:
            self._task = TaskContext(
                task_id=task_id or uuid.uuid4().hex,
                target=target or {},
                tool_call_budget=budget,
            )
            logger.info("env: task %s started, target=%s, budget=%d",
                        self._task.task_id, self._task.target, budget)
            return self._task.model_copy(deep=True)

    def end_task(self, status: TaskStatus = "done") -> None:
        with self._lock:
            if self._task is None:
                return
            self._task.status = status
            logger.info("env: task %s ended, status=%s, calls_used=%d",
                        self._task.task_id, status, self._task.tool_calls_used)
            # Keep _task around for one snapshot read so callers can inspect
            # the final state, then clear on next begin_task.
            # For a strict "no task active" semantic, uncomment:
            # self._task = None

    def tick_tool_call(self) -> tuple[bool, str]:
        """Charge one tool call against the active task budget.

        Returns (allowed, reason_if_blocked). Outside a task context, the
        call is allowed (some tool calls happen during setup / introspection).
        """
        with self._lock:
            if self._task is None:
                return True, ""
            self._task.tool_calls_used += 1
            if self._task.tool_calls_used > self._task.tool_call_budget:
                return False, (
                    f"BUDGET_EXCEEDED: used {self._task.tool_calls_used} of "
                    f"{self._task.tool_call_budget} tool calls. Stop and report."
                )
            return True, ""

    # ---- internals -------------------------------------------------------

    def _fresh_perception_events(self) -> list[PerceptionEvent]:
        """Return events whose timestamp is within ttl_seconds of now."""
        cutoff = datetime.utcnow() - timedelta(seconds=self._perception.ttl_seconds)
        # eviction also compacts the underlying list to bound memory
        kept = [e for e in self._perception.events if e.timestamp >= cutoff]
        if len(kept) != len(self._perception.events):
            self._perception.events = kept
        # return copies so callers can't mutate internal state
        return [e.model_copy(deep=True) for e in kept]


# ---------- catalog loading ----------------------------------------------


def _load_catalog(path: Path) -> WorldCatalog:
    """Load Layer 1 catalog from a YAML file. Fails loudly if the file is bad."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        logger.error("env: world catalog file missing at %s; using empty catalog", path)
        return WorldCatalog()
    except yaml.YAMLError as e:
        raise RuntimeError(f"Failed to parse world catalog at {path}: {e}") from e

    locations = {
        name: Location(**(spec | {"friendly_name": spec.get("friendly_name", name)}))
        for name, spec in (raw.get("locations") or {}).items()
    }
    graspable = {
        name: GraspableClass(**(spec | {"friendly_name": spec.get("friendly_name", name)}))
        for name, spec in (raw.get("graspable") or {}).items()
    }
    people = {
        pid: KnownPerson(**(spec | {"person_id": spec.get("person_id", pid)}))
        for pid, spec in (raw.get("people") or {}).items()
    }
    catalog = WorldCatalog(
        locations=locations,
        graspable=graspable,
        people=people,
        version=str(raw.get("version", "0")),
    )
    logger.info(
        "env: loaded catalog v%s — %d locations, %d graspable, %d people",
        catalog.version, len(catalog.locations), len(catalog.graspable), len(catalog.people),
    )
    return catalog


# ---------- singleton ----------------------------------------------------


_instance_lock = threading.Lock()
_instance: Optional[EnvironmentStore] = None


def get_environment() -> EnvironmentStore:
    """Return the process-wide EnvironmentStore, building it on first call."""
    global _instance
    if _instance is not None:
        return _instance
    with _instance_lock:
        if _instance is None:
            _instance = EnvironmentStore()
    return _instance


def _reset_for_tests() -> None:
    """Drop the singleton so the next `get_environment()` rebuilds.

    Test-only; do not call from production code.
    """
    global _instance
    with _instance_lock:
        _instance = None
