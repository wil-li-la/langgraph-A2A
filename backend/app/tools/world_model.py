"""What the robot currently knows about the world.

This is the boundary between LLM ambition and hardware reality. The
agent can ask for any location or object, but only entries listed here
will resolve to a known cure target. Anything else falls through and
the underlying skill is allowed to try (and likely fail with a clear
error) so the LLM can adapt — e.g. ask the user where the object is.

When `cure/config.yaml` learns new locations or graspable classes, add
them here so the LLM's `what_can_i_see` tool can advertise them.
"""

# Friendly name (LLM-facing) -> cure target string (skill-facing).
KNOWN_LOCATIONS: dict[str, str] = {
    "pharmacy": "medicine",        # where medications are stored
    "patient_room": "patient",     # current patient's bedside
    "charging_dock": "origin",     # robot home / idle position
}

# Objects the robot has been trained to grasp. Using a friendly name
# the LLM is likely to produce; the value is what cure expects.
KNOWN_GRASPABLE_OBJECTS: dict[str, str] = {
    "medicine": "medicine",
    "medication": "medicine",
    "pill": "medicine",
    "pills": "medicine",
}

# Short human-readable description per location, surfaced via what_can_i_see.
LOCATION_DESCRIPTIONS: dict[str, str] = {
    "pharmacy": "the medication storage shelf area",
    "patient_room": "the current patient's bedside",
    "charging_dock": "the robot's home / charging position",
}
