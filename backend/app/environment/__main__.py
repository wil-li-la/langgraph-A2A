"""Smoke runner: exercises the env store end-to-end without booting the server.

Run from the backend dir:

    python -m app.environment

Verifies the four spec success criteria for Phase 0:
  1. Module imports cleanly + standalone unit checks pass.
  2. get_environment() returns the same singleton across calls.
  3. Camera identity-match flow is exercisable via hand-fed perception.
  4. Catalog loads from world.yaml.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta

from app.environment import (
    PerceptionEvent,
    _reset_for_tests,
    get_environment,
)


def _hr(label: str) -> None:
    print(f"\n=== {label} ===")


def main() -> int:
    failures: list[str] = []

    def check(cond: bool, msg: str) -> None:
        if cond:
            print(f"  ✓ {msg}")
        else:
            print(f"  ✗ {msg}")
            failures.append(msg)

    _reset_for_tests()

    _hr("1. Singleton + catalog load")
    env_a = get_environment()
    env_b = get_environment()
    check(env_a is env_b, "get_environment() returns the same instance across calls")
    cat = env_a.catalog()
    check(len(cat.locations) >= 3, f"catalog loaded {len(cat.locations)} locations from world.yaml")
    check("pharmacy" in cat.locations, "catalog has expected 'pharmacy' location")
    check(cat.locations["pharmacy"].cure_target == "medicine",
          "pharmacy maps to cure_target='medicine'")
    check(len(cat.people) == 0,
          "people catalog is empty (relaxed allow-list per spec)")

    _hr("2. Initial robot state is at the dock, not holding anything")
    rob = env_a.robot()
    check(rob.location == "charging_dock", f"location={rob.location!r}")
    check(rob.holding is None, "holding=None")

    _hr("3. update_robot mutates only the fields you pass")
    env_a.update_robot(location="pharmacy")
    check(env_a.robot().location == "pharmacy", "location updated")
    check(env_a.robot().holding is None, "holding still None")
    env_a.update_robot(holding="medicine")
    check(env_a.robot().holding == "medicine", "holding updated")
    check(env_a.robot().location == "pharmacy", "location preserved")

    _hr("4. Task lifecycle + tool-call budget")
    task = env_a.begin_task(target={"patient": "張小明", "medicine": "阿斯匹靈"}, budget=3)
    check(task.task_id != "", "task_id assigned")
    check(env_a.task() is not None and env_a.task().target.get("patient") == "張小明",
          "task target stored")
    ok1, _ = env_a.tick_tool_call()
    ok2, _ = env_a.tick_tool_call()
    ok3, _ = env_a.tick_tool_call()
    ok4, reason4 = env_a.tick_tool_call()
    check(ok1 and ok2 and ok3, "first 3 ticks within budget")
    check(not ok4 and "BUDGET_EXCEEDED" in reason4,
          f"4th tick over budget=3 → blocked ({reason4!r})")
    env_a.end_task("done")
    check(env_a.task().status == "done", "task ended with status=done")

    _hr("5. Camera identity-match flow (the motivating use case)")
    # 5a. Camera service publishes a face detection
    env_a.record_perception(PerceptionEvent(
        kind="face_detected",
        payload={"person_id": "face_001", "name": None, "bbox": [120, 80, 60, 90]},
        confidence=0.93,
        source="camera_d435if",
    ))
    detections = env_a.perception(kind="face_detected")
    check(len(detections) == 1, "1 face_detected event in perception cache")
    check(detections[0].payload["person_id"] == "face_001",
          "agent reads back person_id='face_001'")
    check(detections[0].confidence == 0.93, "confidence preserved (0.93)")
    # 5b. Filter by source
    from_lidar = env_a.perception(kind="face_detected", source="lidar")
    check(len(from_lidar) == 0, "filter by source: lidar sees no face events")
    # 5c. Agent compares against task target — but task ended; begin a fresh one
    env_a.begin_task(target={"patient": "張小明"})
    snap = env_a.snapshot()
    check(snap.task is not None and snap.task.target["patient"] == "張小明",
          "snapshot exposes task target for comparison")
    check(len(snap.perception.events) == 1,
          "snapshot bundles latest perception (visible to dashboard polls)")

    _hr("6. Perception TTL evicts stale events")
    # Forge a stale event by writing then setting timestamp into the past.
    stale = PerceptionEvent(
        kind="object_seen",
        payload={"object_class": "water_bottle"},
        confidence=0.7,
        source="camera_d435if",
        timestamp=datetime.utcnow() - timedelta(seconds=99),
    )
    env_a.record_perception(stale)
    fresh = env_a.perception(kind="object_seen")
    check(len(fresh) == 0, "stale object_seen (99s old) is evicted on read")

    _hr("7. Snapshot is a deep copy — mutating the copy doesn't touch the store")
    snap = env_a.snapshot()
    snap.robot.location = "MUTATED"
    check(env_a.robot().location == "pharmacy",
          "store.location unaffected after mutating snapshot")

    _hr("Result")
    if failures:
        print(f"\n✗ {len(failures)} check(s) failed:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"\n✓ all checks passed")
    print(f"  final state: {json.dumps(env_a.snapshot().model_dump(mode='json'), indent=2, ensure_ascii=False, default=str)[:400]}...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
