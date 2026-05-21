# Runtime location store + frame source of truth — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hardcoded `cure/config.yaml objects: location:` blocks with a per-workflow runtime store (`~/.cache/langgraph-A2A/locations/<wf>.json`) exposed via REST and a `LocationsPanel` on the dashboard, AND fix the dashboard/Nav2 frame mismatch by sourcing `MAP_METADATA` from `backend/maps/305/map.yaml`.

**Architecture:** Backend adds one pure-data module (`workflow_locations_store.py`), four CRUD routes on `workflow.py`, and a new helper `get_workflow_location()` in `stretch_tools.py`. Workflow nodes are updated to call the new helper. Frontend adds one REST client + one panel mounted in `RobotDashboard`. `/nav` is untouched. The map PNG and `MAP_METADATA` are migrated to derive from the baked map.

**Tech Stack:** Python 3.12 (Starlette routes), Next.js 14 (App Router, React 18, Tailwind, TypeScript). No new dependencies. ImageMagick `convert` used once for PNG regen (already installed).

**Repo specifics:** No test framework configured (per `CLAUDE.md`: "verification is manual/integration only"). Verification = `pnpm exec tsc --noEmit` on frontend, `python -c "import …"` smoke imports on backend, plus `curl` against the running backend. No `*.test.*` files exist or should be written.

**Branching:** Direct commits on `main`, per repo workflow.

---

## File map

**Backend (new):**
- `backend/app/api/workflow_locations_store.py` — JSON-backed per-workflow CRUD store.

**Backend (edited):**
- `backend/app/api/nav.py` — `MAP_METADATA` parsed from `maps/305/map.yaml` at import.
- `backend/app/api/workflow.py` — adds `GET /api/workflows` manifest + 4 location routes.
- `backend/app/tools/stretch_tools.py` — adds `get_workflow_location()` + `LocationNotTaughtError`; removes `get_object_pose()` and its config-yaml-derived `objects` field; the agentic `navigate_to` tool uses the new helper with a hardcoded `WORKFLOW_ID`.
- `backend/app/workflows/medication_delivery.py` — adds `WORKFLOW_ID`/`REQUIRED_LOCATIONS` constants; rewrites the 3 nav-node sites; fixes the DRY_RUN shim.
- `backend/cure/config.yaml` — deletes the `location:` block from each `objects:` entry.

**Backend (deleted):**
- `backend/nav_bridge/config/poses.yaml` — unused stub.

**Assets:**
- `frontend/public/maps/305_map.png` — regenerated from `backend/maps/305/map.pgm` (baked).

**Frontend (new):**
- `frontend/lib/workflow-locations-api.ts` — REST client.
- `frontend/components/locations-panel.tsx` — the per-workflow Locations card.

**Frontend (edited):**
- `frontend/components/robot-dashboard.tsx` — mounts `LocationsPanel` in scripted mode.

**Not touched:** `/nav` page, `nav-map.tsx`, `nav-bar.tsx`, `nav-status` context, SSE stream payload.

---

### Task 1: `nav.py` reads `MAP_METADATA` from baked map yaml

**Files:**
- Modify: `backend/app/api/nav.py:51-60`

The current code hardcodes raw-frame numbers. After this task, the values come from `backend/maps/305/map.yaml` (baked) and the PGM dimensions are read via OpenCV.

- [ ] **Step 1: Add helper + replace MAP_METADATA**

In `backend/app/api/nav.py`, locate the existing `MAP_METADATA = { ... }` block (currently lines 51-60) and replace it with the following. The helper goes immediately before it; the `MAP_METADATA` declaration becomes a single call:

```python
def _load_map_metadata() -> dict[str, Any]:
    """Parse backend/maps/305/map.yaml + the referenced PGM to build the
    metadata blob the frontend's /nav page renders against. Fatal on
    error — we'd rather refuse to boot than serve a stale frame."""
    map_dir = Path(__file__).resolve().parents[3] / "maps" / "305"
    yaml_path = map_dir / "map.yaml"
    with yaml_path.open() as f:
        data = yaml.safe_load(f)
    pgm_path = map_dir / data["image"]
    pgm = cv2.imread(str(pgm_path), cv2.IMREAD_GRAYSCALE)
    if pgm is None:
        raise RuntimeError(f"cannot read map PGM at {pgm_path}")
    height_px, width_px = pgm.shape
    return {
        "image": "/maps/305_map.png",   # frontend asset, derived from pgm_path
        "resolution": float(data["resolution"]),
        "origin": [float(x) for x in data["origin"]],
        "width_px": int(width_px),
        "height_px": int(height_px),
        "frame_id": "map",
    }


MAP_METADATA = _load_map_metadata()
logger.info(
    "MAP_METADATA loaded: origin=%s resolution=%.4f size=%dx%d",
    MAP_METADATA["origin"],
    MAP_METADATA["resolution"],
    MAP_METADATA["width_px"],
    MAP_METADATA["height_px"],
)
```

At the top of the file, ensure `import yaml` and `import cv2` are present (both are already used elsewhere in the backend; `yaml` may need to be added).

- [ ] **Step 2: Verify the import succeeds**

Run from the repo root:
```bash
cd backend && source .venv/bin/activate && python -c "from app.api import nav; print(nav.MAP_METADATA)"
```
Expected: a single dict printout with `'origin': [-7.184580902847442, -7.0851230425867575, 0.0]`, `'resolution': 0.006`, `'width_px'` and `'height_px'` matching the baked PGM dimensions.

If you see `[-6.048, …]` something is still hardcoded. If you see `FileNotFoundError`, fix the `parents[3]` path.

- [ ] **Step 3: Commit**

```bash
git add backend/app/api/nav.py
git commit -m "fix(nav): source MAP_METADATA from baked maps/305/map.yaml"
```

---

### Task 2: Regenerate baked map PNG for the frontend

**Files:**
- Regenerate: `frontend/public/maps/305_map.png` (from `backend/maps/305/map.pgm`)

The committed PNG is from the pre-bake PGM (May 5). After this task it matches the baked PGM (May 20).

- [ ] **Step 1: Regenerate the PNG**

```bash
convert backend/maps/305/map.pgm frontend/public/maps/305_map.png
```

- [ ] **Step 2: Sanity-check dimensions**

```bash
identify -format "%wx%h\n" frontend/public/maps/305_map.png
identify -format "%wx%h\n" backend/maps/305/map.pgm
```
Both should print the same `WxH`. They should equal the `width_px` / `height_px` from Task 1.

- [ ] **Step 3: Commit**

```bash
git add frontend/public/maps/305_map.png
git commit -m "chore(maps): regenerate 305_map.png from baked PGM"
```

---

### Task 3: Delete unused `nav_bridge/config/poses.yaml`

**Files:**
- Delete: `backend/nav_bridge/config/poses.yaml`

- [ ] **Step 1: Confirm zero callers**

```bash
grep -rn "poses.yaml\|nav_bridge/config/poses" /home/helin/Documents/github-xiang/langgraph-A2A --include="*.py" --include="*.ts" --include="*.tsx" --include="*.yaml" --include="*.yml" 2>/dev/null
```
Expected: no output (or only a hit inside the file being deleted, which we accept).

- [ ] **Step 2: Delete the file**

```bash
git rm backend/nav_bridge/config/poses.yaml
```

- [ ] **Step 3: Commit**

```bash
git commit -m "chore(nav_bridge): drop unused poses.yaml stub"
```

---

### Task 4: New `workflow_locations_store` module

**Files:**
- Create: `backend/app/api/workflow_locations_store.py`

Pure data layer. Per-workflow JSON files at `~/.cache/langgraph-A2A/locations/<workflow_id>.json`.

- [ ] **Step 1: Create the module**

```python
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
```

- [ ] **Step 2: Smoke-test the module**

```bash
cd backend && source .venv/bin/activate && python -c "
from app.api import workflow_locations_store as s
s.save_one('test_wf', 'spot_a', 1.0, 2.0, 0.5)
print('saved:', s.load('test_wf'))
print('deleted:', s.delete('test_wf', 'spot_a'))
print('after:', s.load('test_wf'))
"
```
Expected:
```
saved: {'spot_a': Location(x=1.0, y=2.0, theta=0.5, ts_ms=...)}
deleted: True
after: {}
```

Then clean up:
```bash
rm -rf ~/.cache/langgraph-A2A/locations/test_wf.json
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/api/workflow_locations_store.py
git commit -m "feat(locations): add per-workflow runtime pose store"
```

---

### Task 5: Manifest + location routes on `/api/workflows`

**Files:**
- Modify: `backend/app/api/workflow.py`

- [ ] **Step 1: Add imports**

In `backend/app/api/workflow.py`, add to the existing imports at the top:

```python
from app.api import workflow_locations_store as locations_store
from app.api.workflow_locations_store import InvalidIdentifierError
from app.api import nav as nav_api   # for reading the current pose snapshot
```

- [ ] **Step 2: Add the workflow registry**

Add this near the top of the module (after the imports, before the first route handler). It is the single source of truth for the manifest:

```python
# Workflows that expose teach-and-save UI on the dashboard.
# When a new workflow lands, append it here.
_WORKFLOW_REGISTRY: list[dict] = [
    {
        "id": "medication_delivery",
        "required_locations": ["medicine", "patient", "origin"],
    },
]
_REGISTERED_IDS = {w["id"] for w in _WORKFLOW_REGISTRY}
```

- [ ] **Step 3: Add the manifest GET handler**

Add this function alongside the existing handlers in `workflow.py`:

```python
async def get_workflows(_request: Request) -> JSONResponse:
    return JSONResponse(_WORKFLOW_REGISTRY)
```

- [ ] **Step 4: Add the 4 location route handlers**

Add these alongside the existing handlers:

```python
def _check_workflow_id(workflow_id: str) -> JSONResponse | None:
    if workflow_id not in _REGISTERED_IDS:
        return JSONResponse(
            {"error": f"unknown workflow_id: {workflow_id!r}"},
            status_code=404,
        )
    return None


async def list_workflow_locations(request: Request) -> JSONResponse:
    workflow_id = request.path_params["workflow_id"]
    err = _check_workflow_id(workflow_id)
    if err:
        return err
    try:
        store = locations_store.list_all(workflow_id)
    except InvalidIdentifierError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({n: asdict(loc) for n, loc in store.items()})


async def put_workflow_location(request: Request) -> JSONResponse:
    workflow_id = request.path_params["workflow_id"]
    name = request.path_params["name"]
    err = _check_workflow_id(workflow_id)
    if err:
        return err
    body = await request.json()
    try:
        x = float(body["x"]); y = float(body["y"]); theta = float(body["theta"])
    except (KeyError, TypeError, ValueError) as e:
        return JSONResponse({"error": f"bad body: {e}"}, status_code=400)
    try:
        loc = locations_store.save_one(workflow_id, name, x, y, theta)
    except InvalidIdentifierError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(asdict(loc))


async def teach_workflow_location(request: Request) -> JSONResponse:
    workflow_id = request.path_params["workflow_id"]
    name = request.path_params["name"]
    err = _check_workflow_id(workflow_id)
    if err:
        return err
    pose = nav_api._pose  # the current backend pose snapshot
    if pose is None:
        return JSONResponse({"error": "no_pose"}, status_code=409)
    try:
        loc = locations_store.save_one(
            workflow_id, name, pose.x, pose.y, pose.theta,
        )
    except InvalidIdentifierError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(asdict(loc))


async def delete_workflow_location(request: Request) -> JSONResponse:
    workflow_id = request.path_params["workflow_id"]
    name = request.path_params["name"]
    err = _check_workflow_id(workflow_id)
    if err:
        return err
    try:
        existed = locations_store.delete(workflow_id, name)
    except InvalidIdentifierError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if not existed:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse({"deleted": True})
```

Make sure `asdict` is imported from `dataclasses` at the top of the file (add `from dataclasses import asdict` if it isn't already).

- [ ] **Step 5: Register the routes**

In the `workflow_routes` list at the bottom of `workflow.py`, append the five new routes (after the existing routes; keep camera routes in place):

```python
    Route("/api/workflows", get_workflows, methods=["GET"]),
    Route("/api/workflows/{workflow_id}/locations",
          list_workflow_locations, methods=["GET"]),
    Route("/api/workflows/{workflow_id}/locations/{name}",
          put_workflow_location, methods=["PUT"]),
    Route("/api/workflows/{workflow_id}/locations/{name}/teach",
          teach_workflow_location, methods=["POST"]),
    Route("/api/workflows/{workflow_id}/locations/{name}",
          delete_workflow_location, methods=["DELETE"]),
```

- [ ] **Step 6: Verify the backend starts and serves the new routes**

In one terminal:
```bash
cd backend && source .venv/bin/activate && python -m app --host localhost --port 9999
```
Wait for the `MAP_METADATA loaded` log line from Task 1.

In another terminal:
```bash
curl -sS http://localhost:9999/api/workflows
# expect: [{"id":"medication_delivery","required_locations":["medicine","patient","origin"]}]

curl -sS http://localhost:9999/api/workflows/medication_delivery/locations
# expect: {}

curl -sS -X POST http://localhost:9999/api/workflows/medication_delivery/locations/medicine/teach -w "\n%{http_code}\n"
# if _pose is None (likely on a fresh boot): expect 409, body {"error":"no_pose"}
# if _pose is set (cached or you've dragged the marker): expect 200 with the Location JSON

curl -sS -X PUT \
    -H "Content-Type: application/json" \
    -d '{"x":1.5,"y":-2.0,"theta":0.0}' \
    http://localhost:9999/api/workflows/medication_delivery/locations/test_loc
# expect 200 with the saved location

curl -sS http://localhost:9999/api/workflows/medication_delivery/locations
# expect a dict containing "test_loc"

curl -sS -X DELETE http://localhost:9999/api/workflows/medication_delivery/locations/test_loc
# expect 200 {"deleted":true}

curl -sS http://localhost:9999/api/workflows/unknown_wf/locations -w "\n%{http_code}\n"
# expect 404
```

Stop the dev backend (`Ctrl-C`).

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/workflow.py
git commit -m "feat(workflow): expose /api/workflows manifest + locations CRUD"
```

---

### Task 6: `stretch_tools.py` — new helper, drop `get_object_pose`

**Files:**
- Modify: `backend/app/tools/stretch_tools.py`

- [ ] **Step 1: Add the new helper and error**

Add this near the top of the file, after the existing imports (around line 50):

```python
from app.api import workflow_locations_store as _locations_store


class LocationNotTaughtError(LookupError):
    """A named location has no pose in the workflow's runtime store."""


def get_workflow_location(
    workflow_id: str, name: str,
) -> tuple[float, float, float]:
    """Resolve a named pose for `workflow_id` from the runtime store.

    Raises `LocationNotTaughtError` if the name has not been taught yet —
    callers must surface this to the operator, not silently substitute
    a default.
    """
    locs = _locations_store.load(workflow_id)
    if name not in locs:
        raise LocationNotTaughtError(
            f"Location {name!r} for workflow {workflow_id!r} has not been "
            f"taught. Open the dashboard, drive the robot to the spot, and "
            f"click Save in the {workflow_id} card's Locations panel."
        )
    loc = locs[name]
    return float(loc.x), float(loc.y), float(loc.theta)
```

- [ ] **Step 2: Remove `get_object_pose` and the `objects` config field**

In the same file, locate and delete the existing `get_object_pose` function (currently around lines 285-296):

```python
def get_object_pose(object_name: str) -> tuple[float, float, float]:
    """Resolve a named target from config.yaml `objects:` to (x, y, theta).
    ...
    """
    cfg = get_config()
    if object_name not in cfg.objects:
        raise ValueError(f"Object {object_name!r} not found in robot config")
    x, y, theta = cfg.objects[object_name]
    return float(x), float(y), float(theta)
```

Also remove the `objects` field from `_RobotConfig`:
- Delete the line `self.objects: dict[str, tuple[float, float, float]] = {}` from `__init__` (around line 120).
- Delete the entire `for name, obj in (data.get("objects") or {}).items(): ...` block from `_maybe_load` (around lines 160-166).

- [ ] **Step 3: Update the agentic `navigate_to` tool**

The `navigate_to` @tool (around line 609) currently calls `get_object_pose(cure_target)`. The agentic delivery agent shares the medication_delivery workflow's locations.

Add a module-level constant near the top of the file (next to the other workflow-related code if any, or alongside `LocationNotTaughtError`):

```python
# The LLM-driven delivery agent uses the same teach-and-save store as the
# scripted medication_delivery workflow. If a new workflow gets an agentic
# counterpart later, give it its own WORKFLOW_ID and override.
_DELIVERY_AGENT_WORKFLOW_ID = "medication_delivery"
```

Then in the `navigate_to` @tool, replace the lines:

```python
    try:
        tx, ty, ttheta = get_object_pose(cure_target)
    except ValueError as e:
        _rr_log("agent/tool/navigate_to", f"pose lookup failed: {e}", level="ERROR")
        return (
            f"UNKNOWN_LOCATION: '{location}' resolves to '{cure_target}' but that "
            f"name has no pose in the robot config. {e}"
        )
```

with:

```python
    try:
        tx, ty, ttheta = get_workflow_location(
            _DELIVERY_AGENT_WORKFLOW_ID, cure_target,
        )
    except LocationNotTaughtError as e:
        _rr_log("agent/tool/navigate_to", f"pose lookup failed: {e}", level="ERROR")
        return f"UNKNOWN_LOCATION: {e}"
```

- [ ] **Step 4: Smoke-import**

```bash
cd backend && source .venv/bin/activate && python -c "
from app.tools.stretch_tools import get_workflow_location, LocationNotTaughtError
try:
    get_workflow_location('medication_delivery', 'medicine')
except LocationNotTaughtError as e:
    print('caught expected:', e)
"
```
Expected: a line starting `caught expected: Location 'medicine' for workflow …`.

Also confirm `get_object_pose` is gone:
```bash
python -c "from app.tools.stretch_tools import get_object_pose" 2>&1 | head -1
```
Expected: `ImportError: cannot import name 'get_object_pose' from 'app.tools.stretch_tools'`

- [ ] **Step 5: Commit**

```bash
git add backend/app/tools/stretch_tools.py
git commit -m "feat(tools): replace get_object_pose with workflow-scoped lookup"
```

---

### Task 7: Update medication_delivery workflow

**Files:**
- Modify: `backend/app/workflows/medication_delivery.py`

- [ ] **Step 1: Add constants and rename imports**

Replace the import block (currently lines 19-26):

```python
from app.tools.stretch_tools import (
    navigate_skill,
    get_object_pose,
    handover_skill,
    speak_skill,
    wait_for_speech_completion,
    listen_skill,
)
```

with:

```python
from app.tools.stretch_tools import (
    navigate_skill,
    get_workflow_location,
    handover_skill,
    speak_skill,
    wait_for_speech_completion,
    listen_skill,
)

# Identifier the dashboard and runtime location store use to scope this
# workflow's pose registry. Must match the entry in
# backend/app/api/workflow.py's _WORKFLOW_REGISTRY.
WORKFLOW_ID = "medication_delivery"

# Names this workflow looks up from the runtime location store. The
# dashboard's Locations panel reads this list to show "required" markers
# next to each name.
REQUIRED_LOCATIONS: tuple[str, ...] = ("medicine", "patient", "origin")
```

- [ ] **Step 2: Rewrite the 3 nav-node call sites**

Find each `tx, ty, ttheta = get_object_pose("…")` call and replace with the workflow-scoped helper:

At line 198 (currently): change
```python
    tx, ty, ttheta = get_object_pose("medicine")
```
to
```python
    tx, ty, ttheta = get_workflow_location(WORKFLOW_ID, "medicine")
```

At line 250 (currently): change
```python
    tx, ty, ttheta = get_object_pose("patient")
```
to
```python
    tx, ty, ttheta = get_workflow_location(WORKFLOW_ID, "patient")
```

At line 383 (currently): change
```python
    tx, ty, ttheta = get_object_pose("origin")
```
to
```python
    tx, ty, ttheta = get_workflow_location(WORKFLOW_ID, "origin")
```

- [ ] **Step 3: Update the DRY_RUN shim**

The DRY_RUN block at the bottom of the file rebinds `get_object_pose` (currently line 747):

```python
        get_object_pose = lambda name: (0.0, 0.0, 0.0)  # noqa: F811
```

Replace it with:

```python
        get_workflow_location = lambda wf, name: (0.0, 0.0, 0.0)  # noqa: F811
```

- [ ] **Step 4: Smoke-import the workflow module**

```bash
cd backend && source .venv/bin/activate && python -c "
from app.workflows import medication_delivery as m
print('WORKFLOW_ID:', m.WORKFLOW_ID)
print('REQUIRED_LOCATIONS:', m.REQUIRED_LOCATIONS)
print('get_workflow_location:', m.get_workflow_location)
"
```
Expected output:
```
WORKFLOW_ID: medication_delivery
REQUIRED_LOCATIONS: ('medicine', 'patient', 'origin')
get_workflow_location: <function get_workflow_location at 0x…>
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/workflows/medication_delivery.py
git commit -m "feat(workflow): wire medication_delivery to workflow-locations store"
```

---

### Task 8: Strip `location:` blocks from `cure/config.yaml`

**Files:**
- Modify: `backend/cure/config.yaml`

- [ ] **Step 1: Edit each `objects:` entry**

For each named entry under `objects:` (currently `medicine`, `water`, `patient`, `test`, `origin`), delete its `location:` block. Keep the other fields (`mode`, `marker_size`, `grasp_id`, `verify_id`).

Concretely, the existing block (lines 58-91 roughly):

```yaml
objects:
  medicine:
    mode: marker # vision
    marker_size: 20 # TODO(lnfu) grasp & verify different marker size
    grasp_id: 0
    verify_id: 1
    location:
      x: 1.0
      y: 0.0
      theta: 0.0
  water:
    mode: marker
    marker_size: 20
    grasp_id: 0
    verify_id: 1
    location:
      x: 0.0
      y: 0.0
      theta: 0.0
  patient:
    location:
      x: 2.0
      y: 1.5
      theta: 3.14
  test:
    location:
      x: 1.0
      y: 0.0
    theta: 0.0
  origin:
    location:
      x: 0.0
      y: 0.0
      theta: 0.0
```

becomes:

```yaml
objects:
  # Pose data has moved to the runtime store at
  # ~/.cache/langgraph-A2A/locations/<workflow_id>.json. The fields below
  # describe how to grasp / detect each object — they are NOT location data.
  medicine:
    mode: marker # vision
    marker_size: 20 # TODO(lnfu) grasp & verify different marker size
    grasp_id: 0
    verify_id: 1
  water:
    mode: marker
    marker_size: 20
    grasp_id: 0
    verify_id: 1
  patient: {}
  test: {}
  origin: {}
```

(`patient`, `test`, and `origin` had no non-location fields, so they collapse to empty mappings.)

- [ ] **Step 2: Verify yaml parses cleanly**

```bash
cd backend && source .venv/bin/activate && python -c "
import yaml
with open('cure/config.yaml') as f:
    data = yaml.safe_load(f)
objs = data['objects']
assert 'location' not in objs['medicine'], objs['medicine']
print('objects after strip:', {k: v for k, v in objs.items()})
"
```
Expected: prints a dict where no entry has a `'location'` key.

- [ ] **Step 3: Smoke-run the full backend import chain**

```bash
cd backend && source .venv/bin/activate && python -c "
from app.workflows.medication_delivery import WORKFLOW_ID
from app.tools.stretch_tools import get_workflow_location
print('all imports clean; WORKFLOW_ID=', WORKFLOW_ID)
"
```
Expected: `all imports clean; WORKFLOW_ID= medication_delivery`. No deprecation warnings about `get_object_pose`.

- [ ] **Step 4: Commit**

```bash
git add backend/cure/config.yaml
git commit -m "refactor(config): drop placeholder location: blocks from cure/config.yaml"
```

---

### Task 9: Frontend REST client

**Files:**
- Create: `frontend/lib/workflow-locations-api.ts`

- [ ] **Step 1: Create the client**

```ts
// frontend/lib/workflow-locations-api.ts
//
// REST client for /api/workflows + /api/workflows/<wf>/locations.
// Mirrors the structure of nav-api.ts.

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:9999"

export interface Location {
  x: number
  y: number
  theta: number
  ts_ms: number
}

export interface WorkflowManifest {
  id: string
  required_locations: string[]
}

export async function fetchWorkflowManifest(): Promise<WorkflowManifest[]> {
  const r = await fetch(`${API_BASE}/api/workflows`)
  if (!r.ok) throw new Error(`fetchWorkflowManifest: ${r.status}`)
  return r.json()
}

export async function listWorkflowLocations(
  workflowId: string,
): Promise<Record<string, Location>> {
  const r = await fetch(
    `${API_BASE}/api/workflows/${workflowId}/locations`,
  )
  if (!r.ok) throw new Error(`listWorkflowLocations: ${r.status}`)
  return r.json()
}

export async function teachWorkflowLocation(
  workflowId: string, name: string,
): Promise<Location> {
  const r = await fetch(
    `${API_BASE}/api/workflows/${workflowId}/locations/${name}/teach`,
    { method: "POST" },
  )
  if (!r.ok) {
    const detail = await r.json().catch(() => ({}))
    throw new Error(detail.error ?? `teachWorkflowLocation: ${r.status}`)
  }
  return r.json()
}

export async function setWorkflowLocation(
  workflowId: string, name: string,
  p: { x: number; y: number; theta: number },
): Promise<Location> {
  const r = await fetch(
    `${API_BASE}/api/workflows/${workflowId}/locations/${name}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(p),
    },
  )
  if (!r.ok) {
    const detail = await r.json().catch(() => ({}))
    throw new Error(detail.error ?? `setWorkflowLocation: ${r.status}`)
  }
  return r.json()
}

export async function deleteWorkflowLocation(
  workflowId: string, name: string,
): Promise<void> {
  const r = await fetch(
    `${API_BASE}/api/workflows/${workflowId}/locations/${name}`,
    { method: "DELETE" },
  )
  if (!r.ok && r.status !== 404) {
    throw new Error(`deleteWorkflowLocation: ${r.status}`)
  }
}
```

- [ ] **Step 2: Type-check**

```bash
cd frontend && pnpm exec tsc --noEmit
```
Expected: clean exit, no output.

- [ ] **Step 3: Commit**

```bash
git add frontend/lib/workflow-locations-api.ts
git commit -m "feat(frontend): add workflow-locations REST client"
```

---

### Task 10: `LocationsPanel` component

**Files:**
- Create: `frontend/components/locations-panel.tsx`

- [ ] **Step 1: Create the component**

```tsx
// frontend/components/locations-panel.tsx
"use client"

import { useCallback, useEffect, useState } from "react"
import { useNavStatus } from "@/contexts/nav-status"
import {
  deleteWorkflowLocation,
  fetchWorkflowManifest,
  listWorkflowLocations,
  teachWorkflowLocation,
  type Location,
  type WorkflowManifest,
} from "@/lib/workflow-locations-api"

interface Props {
  workflowId: string
}

/**
 * Per-workflow teach-and-save UI for named (x, y, theta) poses. The
 * "current pose" used by Save is whatever the backend has cached as
 * _pose (set by drag-to-set-pose on /nav, teleop, or a future
 * localizer). The panel does not own the map.
 */
export function LocationsPanel({ workflowId }: Props) {
  const { pose } = useNavStatus()
  const [manifest, setManifest] = useState<WorkflowManifest | null>(null)
  const [stored, setStored] = useState<Record<string, Location>>({})
  const [selectedName, setSelectedName] = useState<string>("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Initial fetch
  useEffect(() => {
    let cancelled = false
    Promise.all([
      fetchWorkflowManifest(),
      listWorkflowLocations(workflowId),
    ]).then(([allWorkflows, locs]) => {
      if (cancelled) return
      const m = allWorkflows.find((w) => w.id === workflowId) ?? null
      setManifest(m)
      setStored(locs)
      if (m && m.required_locations.length > 0) {
        setSelectedName(m.required_locations[0])
      }
    }).catch((e) => !cancelled && setError(String(e)))
    return () => { cancelled = true }
  }, [workflowId])

  const refresh = useCallback(async () => {
    try {
      const locs = await listWorkflowLocations(workflowId)
      setStored(locs)
    } catch (e) {
      setError(String(e))
    }
  }, [workflowId])

  const handleSave = async () => {
    if (!selectedName) return
    setBusy(true); setError(null)
    try {
      await teachWorkflowLocation(workflowId, selectedName)
      await refresh()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  const handleDelete = async (name: string) => {
    setBusy(true); setError(null)
    try {
      await deleteWorkflowLocation(workflowId, name)
      await refresh()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  if (!manifest) {
    return (
      <div className="rounded-md border border-border bg-card p-3 font-mono text-xs text-muted-foreground">
        Locations: loading…
        {error && <div className="mt-1 text-red-500">{error}</div>}
      </div>
    )
  }

  const required = manifest.required_locations
  const missing = required.filter((n) => !(n in stored))
  const allTaught = missing.length === 0

  return (
    <div className="rounded-md border border-border bg-card p-3 font-mono text-xs space-y-2">
      <div className="flex items-center justify-between">
        <span className="font-medium text-foreground">Locations</span>
        <span className={allTaught ? "text-green-500" : "text-amber-500"}>
          {allTaught ? "all taught" : `${missing.length} missing`}
        </span>
      </div>

      <div className="flex flex-wrap gap-x-3 gap-y-1">
        {required.map((name) => {
          const taught = name in stored
          return (
            <span key={name} className={taught ? "text-green-500" : "text-red-500"}>
              {taught ? "✓" : "✗"} {name}
            </span>
          )
        })}
      </div>

      <div className="flex items-center gap-2 border-t border-border pt-2">
        <span className="text-muted-foreground">Save current pose as:</span>
        <select
          value={selectedName}
          onChange={(e) => setSelectedName(e.target.value)}
          className="rounded border border-border bg-background px-2 py-0.5 font-mono"
        >
          {required.map((n) => (
            <option key={n} value={n}>{n}</option>
          ))}
        </select>
        <button
          onClick={handleSave}
          disabled={busy || !pose || !selectedName}
          className="rounded border border-foreground/30 bg-foreground/10 px-2 py-0.5 text-foreground hover:bg-foreground/20 disabled:cursor-not-allowed disabled:opacity-40"
          title={!pose ? "Drive the robot to a pose first (drag on /nav or teleop)" : "Save"}
        >
          {busy ? "saving…" : "Save"}
        </button>
      </div>

      {Object.keys(stored).length > 0 && (
        <div className="space-y-0.5 border-t border-border pt-2">
          {Object.entries(stored).map(([name, loc]) => {
            const headingDeg = (loc.theta * 180) / Math.PI
            return (
              <div key={name} className="flex items-center justify-between">
                <span>
                  <span className="text-foreground">{name}</span>{" "}
                  <span className="text-muted-foreground">
                    ({loc.x.toFixed(2)}, {loc.y.toFixed(2)}) {headingDeg.toFixed(0)}°
                  </span>
                </span>
                <button
                  onClick={() => handleDelete(name)}
                  disabled={busy}
                  className="text-muted-foreground hover:text-red-500 disabled:opacity-40"
                  aria-label={`delete ${name}`}
                  title={`delete ${name}`}
                >
                  ×
                </button>
              </div>
            )
          })}
        </div>
      )}

      {error && (
        <div className="border-t border-border pt-2 text-red-500">{error}</div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Type-check**

```bash
cd frontend && pnpm exec tsc --noEmit
```
Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add frontend/components/locations-panel.tsx
git commit -m "feat(frontend): add per-workflow LocationsPanel component"
```

---

### Task 11: Mount `LocationsPanel` in `RobotDashboard`

**Files:**
- Modify: `frontend/components/robot-dashboard.tsx`

- [ ] **Step 1: Add the import**

In `frontend/components/robot-dashboard.tsx`, add to the existing imports at the top (alongside `WorkflowControls`, `PauseGuide`, etc.):

```tsx
import { LocationsPanel } from "@/components/locations-panel"
```

- [ ] **Step 2: Mount the panel in scripted mode**

In the right-column scripted-mode block, between `WorkflowControls` and the `PauseGuide` conditional (i.e. after the `</div>` that closes the WorkflowControls card, before the `{isPaused && pausedNodeId && pauseReason && (` line), insert:

```tsx
{/* Per-workflow teach-and-save Locations panel */}
<LocationsPanel workflowId="medication_delivery" />
```

The surrounding structure for context (after edit):

```tsx
{/* Workflow controls */}
<div className="rounded-md border border-border bg-card p-3 shrink-0">
  <WorkflowControls ... />
</div>

{/* Per-workflow teach-and-save Locations panel */}
<LocationsPanel workflowId="medication_delivery" />

{/* Pause guide */}
{isPaused && pausedNodeId && pauseReason && (
  ...
)}
```

- [ ] **Step 3: Type-check + visual verify**

```bash
cd frontend && pnpm exec tsc --noEmit
```
Expected: clean.

Then, with the backend running (from the earlier task) and the dev server already running (the user keeps one on port 3000), reload `http://localhost:3000/` and confirm:
- A new "Locations" card appears below "Workflow controls" in the right column when the dashboard is in scripted mode.
- It reads from `/api/workflows` (manifest) and `/api/workflows/medication_delivery/locations` (initially empty after a `rm -rf ~/.cache/langgraph-A2A/locations/`).
- All three required names render as red ✗ initially.
- The Save button is disabled when the backend reports no current pose.
- Switching to agentic mode hides the panel (because it's only rendered inside the scripted branch).

- [ ] **Step 4: Commit**

```bash
git add frontend/components/robot-dashboard.tsx
git commit -m "feat(dashboard): mount LocationsPanel in medication_delivery card"
```

---

## Self-review

**Spec coverage:**

- Goal 1 (one frame source of truth) → Task 1 (nav.py reads yaml) + Task 2 (PNG regen) + Task 3 (delete duplicate `poses.yaml`).
- Goal 2 (per-workflow runtime store + teach UI off `/nav`) → Tasks 4-11.
- Goal 3 (loud failure on untaught name) → Task 6 (`LocationNotTaughtError`) + Task 7 (workflow uses the helper; existing `_fail` path surfaces it).
- Goal 4 (`/nav` stays generic) → No `/nav` files in the file map. Tasks 1 and 2 touch backend metadata + asset only; the page itself is unchanged.
- Spec section A → Tasks 1, 2, 3.
- Spec section B → Task 4.
- Spec section C → Task 5.
- Spec section D → Tasks 6, 7.
- Spec section E (workflow registry / manifest) → Task 5 step 2 (`_WORKFLOW_REGISTRY`).
- Spec section F → Tasks 9, 10, 11.
- Spec section G (`/nav` unchanged) → No tasks needed; verified by absence.
- Spec section H (migration) → Documented in plan header and Task 11 verification; the operator runs teach-and-save once in the lab.

**Placeholder scan:** No TBDs, no "implement appropriately", no missing code blocks. Every step has the exact commands or code.

**Type / name consistency:**
- `Location` dataclass in store (Task 4) has fields `x, y, theta, ts_ms` — matches the TS `Location` interface in Task 9 and the JSON returned by handlers in Task 5.
- `WORKFLOW_ID = "medication_delivery"` declared in Task 7 matches the `_WORKFLOW_REGISTRY` id in Task 5 step 2 and the frontend hardcoded prop in Task 11.
- `REQUIRED_LOCATIONS = ("medicine", "patient", "origin")` in Task 7 matches `required_locations` in Task 5's registry.
- `get_workflow_location(workflow_id, name)` signature: defined in Task 6, called in Task 7 (3x) and the agentic tool in Task 6, exposed nowhere in the frontend.
- `LocationNotTaughtError` defined in Task 6, raised in Task 6's helper, caught in Task 6 step 3 (agentic tool) and reaches the workflow via the existing `_fail()` path. The TS frontend does not reference it directly.
- Route paths: `/api/workflows`, `/api/workflows/{workflow_id}/locations[/<name>[/teach]]` consistent between Task 5 (backend) and Task 9 (frontend client).
- `_DELIVERY_AGENT_WORKFLOW_ID` in Task 6 step 3 is module-local; it intentionally equals `WORKFLOW_ID` from Task 7 but lives in a different file, since `stretch_tools.py` cannot import `medication_delivery.py` without a circular dependency.

**Test framework:** No `*.test.*` files exist or are required (CLAUDE.md). Verification = `tsc --noEmit` + import smoke tests + `curl`.
