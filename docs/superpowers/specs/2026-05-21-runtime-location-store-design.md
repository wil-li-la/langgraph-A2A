# Runtime location store + frame-source-of-truth fix

**Date:** 2026-05-21
**Status:** Design approved (no-clarifying-questions mode)

## Problem

`backend/cure/config.yaml objects:` hardcodes the workflow's named locations
(`medicine`, `patient`, `origin`) as round-number placeholders:

```yaml
medicine: { location: { x: 1.0, y: 0.0, theta: 0.0 } }
patient:  { location: { x: 2.0, y: 1.5, theta: 3.14 } }
origin:   { location: { x: 0.0, y: 0.0, theta: 0.0 } }
```

These are not real lab coordinates. `get_object_pose()`
(`backend/app/tools/stretch_tools.py:285`) hands them straight to
`navigate_skill()`, which ZMQ-sends them to Nav2. The workflow therefore
drives to wherever `(1.0, 0.0)` happens to be in the room, not the pharmacy.

Compounding this:

- `backend/app/api/nav.py:51-60` hardcodes `MAP_METADATA` with the **raw**
  map origin `[-6.048, -4.6439, 0.0]`, but `nav.launch.py:30` loads
  `backend/maps/305/map.yaml`, which is the **baked** canonical map with
  origin `[-7.1846, -7.0851, 0.0]` (commit `cbca6e3`, 2026-05-20). The
  dashboard's `worldToPx`/`pxToWorld` math is therefore off-frame from Nav2
  by ~1.1 m in x and ~2.5 m in y.
- `frontend/public/maps/305_map.png` (mtime 2026-05-05, before the bake) is
  rendered from the raw PGM; Nav2 actually has the baked PGM loaded.
- `backend/nav_bridge/config/poses.yaml` is a parallel TBD pose registry
  with no grep hits — a documentation trap.

Re-authoring the YAML coordinates would patch today's symptoms but not the
structure: the next time the map is re-baked or furniture moves, the same
bug returns and someone has to remember to edit YAML in three places.

## Goals

1. Make `backend/maps/305/map.yaml` the **only** declarator of the map
   frame. Everything downstream (backend dashboard metadata, frontend image,
   workflow object poses) reads from it or is derived from it at runtime.
2. Move named-location poses out of source code into a **runtime store**
   that the dashboard can read, write, and delete. Workflow speaks in
   semantic names (`get_object_pose("pharmacy")`); resolution happens at
   call time against the store.
3. When a name has not been taught, the workflow fails loudly
   (`LocationNotTaughtError`) rather than silently driving to placeholder
   coordinates.

## Non-goals

- Live localization (room-camera or otherwise). Out of scope; the runtime
  store works with any pose source.
- Multi-map / multi-room support. One JSON store per backend instance;
  if/when multi-map becomes a requirement, add a `map_id` field then.
- Authentication / authorization on the teach endpoints — same trust
  boundary as the rest of the local dashboard.
- Updating the grasp-config fields (`marker_size`, `grasp_id`, `verify_id`,
  `mode`) in `cure/config.yaml objects:`. Those stay; only the `location:`
  blocks are removed.

## Design

### A) Frame source of truth

`backend/maps/305/map.yaml` is the canonical declaration of map frame
parameters (`origin`, `resolution`, `image`). Two consumers move to reading
it dynamically:

1. **Backend `nav.py`**: at module import, parse `maps/305/map.yaml` and
   populate `MAP_METADATA` from it. The `width_px` / `height_px` come from
   `cv2.imread(...).shape` on the referenced PGM. Failure to read the
   yaml/pgm is fatal at boot (logged with the path and exception) — the
   backend won't start serving `/api/nav/map` with stale numbers.

2. **Frontend PNG**: `frontend/public/maps/305_map.png` is regenerated from
   the **baked** `backend/maps/305/map.pgm` (one-time `convert` + commit
   for now; later this should be a build step). The bake script
   `backend/maps/bake_world_frame.py` already runs at map-update time; an
   additional step writes the PNG.

`backend/nav_bridge/config/poses.yaml` is deleted (already unused).

### B) Runtime location store

**File:** `~/.cache/langgraph-A2A/locations.json` (env override:
`NVBLOX_NAV_LOCATIONS_CACHE`). Sibling to the existing `nav-pose.json`.
Atomic write via tmp + `os.replace`.

**Shape:**
```json
{
  "pharmacy":    {"x": -1.23, "y":  0.45, "theta":  1.5708, "ts_ms": 1778…},
  "patient_room":{"x":  0.40, "y": -2.10, "theta": -1.5708, "ts_ms": 1778…},
  "charging_dock":{"x": 0.00, "y":  0.00, "theta": 0.0,    "ts_ms": 1778…}
}
```

Top-level keys are location names. Values are `(x, y, theta)` in map-frame
metres + millisecond UTC timestamp of when they were taught.

**Module:** `backend/app/api/locations_store.py` — pure data layer:
```python
def load() -> dict[str, Location]
def save_one(name: str, x: float, y: float, theta: float) -> Location
def delete(name: str) -> bool   # True if existed
def list_all() -> dict[str, Location]
```
The store is process-local but persisted to disk; concurrent writes are
not expected (single backend, single laptop).

### C) HTTP API

Added under `/api/nav/locations` in `backend/app/api/nav.py`:

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/api/nav/locations` | — | `{name: Location}` |
| PUT | `/api/nav/locations/<name>` | `{x, y, theta}` | the saved `Location` |
| POST | `/api/nav/locations/<name>/teach` | — (no body) | the saved `Location` — snapshots the current `_pose` from `nav.py` |
| DELETE | `/api/nav/locations/<name>` | — | `204` (or `404` if absent) |

Validation:
- `<name>` must match `^[a-z][a-z0-9_]{0,31}$` (lowercase ASCII with
  underscores). Anything else returns `400`.
- POST `/teach` returns `409` with `{"error": "no_pose"}` when `_pose` is
  `None` (no current pose to snapshot).

After any successful mutation, the route handler calls the existing
`_bump()` to signal SSE subscribers. The store is the single source of
truth — handlers re-read it before bumping.

### D) SSE snapshot extension

The `_snapshot()` payload in `nav.py:301-306` gains a `locations` field:

```json
{
  "pose": {...} | null,
  "task": {...},
  "teleop_active": false,
  "locations": {name: Location}
}
```

Frontend `NavSnapshot` and `useNavStatus()` context are extended to
expose `locations`. NavBar is unchanged. NavMap consumes it for rendering
and the locations panel (below).

### E) Workflow integration

`backend/app/tools/stretch_tools.py:get_object_pose(name)` is rewritten:

```python
class LocationNotTaughtError(LookupError):
    """Named location has no pose in the runtime store."""

def get_object_pose(name: str) -> tuple[float, float, float]:
    locs = locations_store.load()
    if name not in locs:
        raise LocationNotTaughtError(
            f"Location {name!r} has not been taught. "
            f"Drive the robot there and save it from the dashboard /nav page."
        )
    loc = locs[name]
    return float(loc["x"]), float(loc["y"]), float(loc["theta"])
```

The `location:` blocks inside `cure/config.yaml objects:` are deleted in
this PR (they become dead config). The other fields stay.

The workflow's `confirm_task_node` and `navigate_to_*_node` already wrap
exceptions in `_fail()` via the existing `error_handler_node` pattern;
`LocationNotTaughtError` flows through that same path. The error message
(verbatim) appears in `state["errors"]` and on the dashboard.

**Canonical names the workflow expects:**
- `medicine` — pharmacy pickup
- `patient` — patient bedside
- `origin` — charging dock / return-to-base

These names are referenced in `medication_delivery.py`. The dashboard does
not enforce that all three exist (the user is free to teach others), but
the dashboard's locations panel highlights which canonical names are
currently missing.

### F) Frontend: locations panel + map markers

New UI on `/nav`, rendered below `LayerControls`:

```
─────────────────────────────────────────────────────────
 Locations
   pharmacy     (-1.23,  0.45)  90°       [×]
   patient_room ( 0.40, -2.10)  -90°      [×]
   charging_dock( 0.00,  0.00)   0°       [×]

   missing canonical: (none)
   ───
   Save current pose as:  [______________]  [Save]
─────────────────────────────────────────────────────────
```

Behavior:
- Each row shows name, (x, y), heading in degrees, and a delete button.
- "Save current pose as": typed name → POST
  `/api/nav/locations/<name>/teach`. Snapshot the current `_pose` (which
  the user has just dragged the robot to). The button is disabled when
  `pose` is null or the typed name is invalid.
- "missing canonical" lists any of `medicine | patient | origin` not yet
  present in the store. Empty when all three are taught.
- The SVG renders each location as a small filled diamond (or circle —
  pick something visually distinct from the red robot marker and the
  green goal marker) at its `(x, y)`, with the name in monospace next to
  it. `pointer-events: none` so it doesn't interfere with drag-to-set
  gestures.

New file: `frontend/lib/locations-api.ts` — `listLocations()`,
`teachLocation(name)`, `setLocation(name, pose)`, `deleteLocation(name)`.
These complement the existing `nav-api.ts` clients.

### G) Migration / one-time setup

After the upgraded backend ships, the operator must:

1. Start the backend; the locations store is empty.
2. On `/nav`, drag the red robot to the pharmacy shelf, type `medicine`,
   click Save.
3. Drag to the patient bedside, type `patient`, click Save.
4. Drag to the charging dock, type `origin`, click Save.
5. Try a workflow run. It should now navigate to real locations.

This is a one-shot operation. The cache file persists across backend
restarts. Whoever ships this PR is responsible for performing the
teach-and-save in the lab.

## Files touched

**Backend:**
- **new**: `backend/app/api/locations_store.py` — JSON-backed CRUD store.
- **edit**: `backend/app/api/nav.py` — read `MAP_METADATA` from
  `maps/305/map.yaml`; add 4 location routes; extend `_snapshot()`.
- **edit**: `backend/app/tools/stretch_tools.py` — `get_object_pose()`
  consults the runtime store; `LocationNotTaughtError` defined.
- **edit**: `backend/cure/config.yaml` — remove `location:` blocks from
  `medicine`, `water`, `patient`, `test`, `origin` (keep other fields).
- **delete**: `backend/nav_bridge/config/poses.yaml`.

**Frontend:**
- **new**: `frontend/lib/locations-api.ts` — REST client.
- **edit**: `frontend/lib/nav-api.ts` — extend `NavSnapshot` with
  `locations: Record<string, Location>`. Add `Location` type.
- **edit**: `frontend/contexts/nav-status.tsx` — expose `locations` from
  the context.
- **edit**: `frontend/components/nav-map.tsx` — render location markers on
  the SVG; render the locations panel below `LayerControls`.

**Assets:**
- **regen**: `frontend/public/maps/305_map.png` — derived from baked
  `backend/maps/305/map.pgm` via `convert` (ImageMagick) at the same
  pixel resolution. Committed as a binary asset for now (a future
  improvement is to derive at build time).

## Testing

No test framework configured. Manual verification:

1. `cd backend && python -m app --host localhost --port 9999`. Confirm
   the log line shows `MAP_METADATA: origin=[-7.1846, -7.0851, …]`
   matching `maps/305/map.yaml`. Backend fails to start if the yaml
   is missing or unreadable.

2. `curl http://localhost:9999/api/nav/locations` → `{}`.

3. `curl -X POST http://localhost:9999/api/nav/locations/pharmacy/teach`
   while `_pose` is `None` → `409 no_pose`.

4. On `/nav`, drag the robot. POST `/teach` succeeds. The locations panel
   shows `pharmacy (...) (...)`. Refresh the page → still there (persisted).

5. Restart backend → location survives, panel still shows it.

6. With `pharmacy` taught and `medicine`+`patient` not taught, run the
   medication delivery workflow → it fails at the `navigate_to_pharmacy`
   node with `LocationNotTaughtError: Location 'medicine' has not been
   taught.`. The error reaches the dashboard via the existing error
   path.

7. Teach all three canonical names. Run the workflow → it now drives to
   actual map-frame coords. (Behavior beyond that depends on Nav2 and
   the physical robot; out of scope for this spec.)

8. Frontend network tab: still exactly one `/api/nav/status/stream`
   connection. The location markers update without polling.

9. Dashboard frame check: drag the robot to a known physical landmark
   (the door, the corner of a table). The displayed `(x, y)` matches
   the value `ros2 topic echo /amcl_pose` would publish in the same
   place, within ~10 cm. (Pre-fix, this was off by ~1.1 m / ~2.5 m.)

10. Confirm `frontend/public/maps/305_map.png` visually matches the
    layout in RViz when you point RViz at the same baked PGM. The map
    should not be rotated ~161° relative to RViz any more.
