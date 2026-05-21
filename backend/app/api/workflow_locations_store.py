"""Per-workflow runtime store for named (x, y, theta) poses.

Each workflow owns its own JSON file at ~/.cache/langgraph-A2A/locations/
<workflow_id>.json. Names inside a file are scoped to that workflow — the
medication_delivery workflow's "patient" entry is independent of any other
workflow's "patient".

This replaces the hardcoded `objects:` block in cure/config.yaml, which
contained placeholder coordinates that didn't match the lab's actual map.

Operations are atomic via tmp+os.replace. Failures are logged but never
raise — callers get an empty dict (load), a successful Location (save), or
False (delete miss).
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_VALID_ID = re.compile(r"^[a-z][a-z0-9_]{0,31}$")

_DEFAULT_DIR = Path.home() / ".cache" / "langgraph-A2A" / "locations"
LOCATIONS_DIR = Path(os.getenv("LOCATIONS_CACHE_DIR", str(_DEFAULT_DIR)))


@dataclass
class Location:
    x: float
    y: float
    theta: float
    ts_ms: int = field(default_factory=lambda: int(time.time() * 1000))


class InvalidIdentifierError(ValueError):
    """workflow_id or location name failed validation."""


def _validate(label: str, value: str) -> None:
    if not _VALID_ID.match(value):
        raise InvalidIdentifierError(
            f"{label} {value!r} must match ^[a-z][a-z0-9_]{{0,31}}$"
        )


def _path_for(workflow_id: str) -> Path:
    _validate("workflow_id", workflow_id)
    return LOCATIONS_DIR / f"{workflow_id}.json"


def load(workflow_id: str) -> dict[str, Location]:
    """Read the store for `workflow_id`. Empty dict if the file is missing
    or malformed (with a warning log)."""
    path = _path_for(workflow_id)
    try:
        with path.open() as f:
            raw = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("locations cache unreadable at %s: %s", path, e)
        return {}
    out: dict[str, Location] = {}
    for name, data in raw.items():
        if not isinstance(data, dict):
            continue
        try:
            out[name] = Location(
                x=float(data["x"]),
                y=float(data["y"]),
                theta=float(data["theta"]),
                ts_ms=int(data.get("ts_ms", time.time() * 1000)),
            )
        except (KeyError, TypeError, ValueError) as e:
            logger.warning("locations cache entry %r malformed: %s", name, e)
            continue
    return out


def save_one(workflow_id: str, name: str,
             x: float, y: float, theta: float) -> Location:
    """Upsert `name` in `workflow_id`'s store. Returns the saved Location."""
    _validate("name", name)
    store = load(workflow_id)
    loc = Location(x=float(x), y=float(y), theta=float(theta))
    store[name] = loc
    _write(workflow_id, store)
    return loc


def delete(workflow_id: str, name: str) -> bool:
    """Remove `name` from `workflow_id`'s store. Returns True if it existed."""
    _validate("name", name)
    store = load(workflow_id)
    if name not in store:
        return False
    del store[name]
    _write(workflow_id, store)
    return True


def list_all(workflow_id: str) -> dict[str, Location]:
    """Alias for `load()` — keeps the public API symmetric with the future
    /api/workflows/<wf>/locations GET handler."""
    return load(workflow_id)


def _write(workflow_id: str, store: dict[str, Location]) -> None:
    path = _path_for(workflow_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with tmp.open("w") as f:
            json.dump({n: asdict(loc) for n, loc in store.items()}, f, indent=2)
        os.replace(tmp, path)
    except OSError as e:
        logger.warning("locations cache write failed at %s: %s", path, e)
