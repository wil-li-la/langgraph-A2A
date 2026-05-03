"""Pydantic data shapes for the environment store.

See docs/superpowers/specs/2026-05-03-environment-state-design.md for
the rationale and the layered architecture this slots into.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


PerceptionKind = Literal["face_detected", "object_seen", "speech_heard", "obstacle"]


# ---------- Layer 2 — kinematic + perception ----------------------------


class RobotKinematicState(BaseModel):
    """Current physical state of the robot."""

    location: str = "charging_dock"          # friendly name from world catalog
    holding: str | None = None               # object name, or None if gripper empty
    battery_pct: float | None = None
    joint_positions: dict[str, float] = Field(default_factory=dict)
    last_updated: datetime = Field(default_factory=datetime.utcnow)


class PerceptionEvent(BaseModel):
    """One observation from a perception source.

    `payload` shape varies by `kind`:
      - face_detected : {"person_id": str, "name": str | None, "bbox": [x,y,w,h]}
      - object_seen   : {"object_class": str, "bbox": [...], "pose": [...] | None}
      - speech_heard  : {"transcript": str, "language": str}
      - obstacle      : {"distance_m": float, "direction_deg": float}
    """

    kind: PerceptionKind
    payload: dict[str, Any]
    confidence: float
    source: str                              # "camera_d435if", "deepgram", "lidar", ...
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class PerceptionCache(BaseModel):
    """All known perception events. Stale ones are filtered on read by the store."""

    events: list[PerceptionEvent] = Field(default_factory=list)
    ttl_seconds: float = 5.0


# ---------- Layer 2 — task context --------------------------------------


TaskStatus = Literal["running", "paused", "done", "failed"]


class TaskContext(BaseModel):
    """The currently active task, if any. The env store holds at most one."""

    task_id: str
    started_at: datetime = Field(default_factory=datetime.utcnow)
    target: dict[str, Any] = Field(default_factory=dict)   # task-specific shape
    tool_calls_used: int = 0
    tool_call_budget: int = 30
    status: TaskStatus = "running"


# ---------- Layer 1 — world catalog -------------------------------------


class Location(BaseModel):
    friendly_name: str                       # "pharmacy"
    cure_target: str                         # "medicine"  (what cure.skills.navigate accepts)
    description: str = ""


class GraspableClass(BaseModel):
    friendly_name: str                       # "medicine"
    cure_target: str
    aliases: list[str] = Field(default_factory=list)


class KnownPerson(BaseModel):
    person_id: str                           # opaque id, e.g. "face_001"
    display_name: str = ""                   # for greeting; optional
    notes: str = ""                          # free-form, optional (allergies etc.)


class WorldCatalog(BaseModel):
    """Read-mostly knowledge base. Loaded from world.yaml at store init."""

    locations: dict[str, Location] = Field(default_factory=dict)
    graspable: dict[str, GraspableClass] = Field(default_factory=dict)
    people: dict[str, KnownPerson] = Field(default_factory=dict)
    version: str = "0"                       # bumped when YAML is reloaded


# ---------- Composite snapshot ------------------------------------------


class EnvironmentSnapshot(BaseModel):
    """Atomic deep-copy of all store state. Safe to serialize / hand to a tool."""

    robot: RobotKinematicState
    perception: PerceptionCache
    task: TaskContext | None
    catalog_version: str
