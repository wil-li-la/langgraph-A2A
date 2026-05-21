# Per-workflow runtime location store + frame-source-of-truth fix

**Date:** 2026-05-21
**Status:** Design approved (no-clarifying-questions mode)

## Problem

`backend/cure/config.yaml objects:` hardcodes the medication-delivery
workflow's named locations (`medicine`, `patient`, `origin`) as
round-number placeholders:

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
- `frontend/public/maps/305_map.png` (mtime 2026-05-05, before the bake)
  is rendered from the raw PGM; Nav2 actually has the baked PGM loaded.
- `backend/nav_bridge/config/poses.yaml` is a parallel TBD pose registry
  with no grep hits — a documentation trap.

Re-authoring the YAML coordinates would patch today's symptoms but not
the structure: the next time the map is re-baked or furniture moves, the
same bug returns and someone has to remember to edit YAML in three places.

## Goals

1. Make `backend/maps/305/map.yaml` the **only** declarator of the map
   frame. Everything downstream (backend dashboard metadata, frontend
   image) reads from it at runtime or is derived from it.
2. Move named-location poses out of source code into **per-workflow**
   runtime stores. Each workflow declares its own required label set
   and owns its own teach UI; the workflow speaks in semantic names
   (`get_workflow_location(WORKFLOW_ID, "pharmacy")`); resolution
   happens at call time against that workflow's store.
3. When a name has not been taught, the workflow fails loudly
   (`LocationNotTaughtError`) rather than silently driving to placeholder
   coordinates.
4. Keep `/nav` a generic navigation primitive — no workflow labels, no
   teach UI, no per-workflow markers.

## Non-goals

- Live localization (room-camera or otherwise). The runtime store works
  with any pose source.
- Multi-map / multi-room support. One store per workflow per backend
  instance.
- Authentication / authorization on the teach endpoints — same trust
  boundary as the rest of the local dashboard.
- Updating the grasp-config fields (`marker_size`, `grasp_id`,
  `verify_id`, `mode`) in `cure/config.yaml objects:`. Those stay; only
  the `location:` blocks are removed.
- Generic on-map markers showing taught locations. Per-workflow labels
  don't belong on the generic map.

## Design

### A) Frame source of truth

`backend/maps/305/map.yaml` is the canonical declaration of map frame
parameters (`origin`, `resolution`, `image`). Two consumers move to
reading it dynamically:

1. **Backend `nav.py`**: at module import, parse `maps/305/map.yaml` and
   populate `MAP_METADATA` from it. The `width_px` / `height_px` come
   from `cv2.imread(...).shape` on the referenced PGM. Failure to read
   the yaml/pgm is fatal at boot (logged with the path and exception) —
   the backend won't start serving `/api/nav/map` with stale numbers.

2. **Frontend PNG**: `frontend/public/maps/305_map.png` is regenerated
   from the **baked** `backend/maps/305/map.pgm` (one-time `convert` +
   commit for now; later this should be a build step that runs after
   `bake_world_frame.py`).

`backend/nav_bridge/config/poses.yaml` is deleted (already unused).

### B) Per-workflow location store

**File layout:** `~/.cache/langgraph-A2A/locations/<workflow_id>.json`
(env override: `LOCATIONS_CACHE_DIR` overrides the parent directory).
Sibling tree to the existing `nav-pose.json`. Atomic write via tmp +
`os.replace`.

For the medication delivery workflow today:
`~/.cache/langgraph-A2A/locations/medication_delivery.json`.

**File shape:**
```json
{
  "medicine": {"x": -1.23, "y":  0.45, "theta":  1.5708, "ts_ms": 1778…},
  "patient":  {"x":  0.40, "y": -2.10, "theta": -1.5708, "ts_ms": 1778…},
  "origin":   {"x":  0.00, "y":  0.00, "theta":  0.0,    "ts_ms": 1778…}
}
```

Top-level keys are location names scoped to this workflow. Values are
`(x, y, theta)` in map-frame metres + millisecond UTC timestamp of when
they were taught.

**Module:** `backend/app/api/workflow_locations_store.py` — pure data
layer, workflow-scoped:
```python
def load(workflow_id: str) -> dict[str, Location]
def save_one(workflow_id: str, name: str,
             x: float, y: float, theta: float) -> Location
def delete(workflow_id: str, name: str) -> bool   # True if existed
def list_all(workflow_id: str) -> dict[str, Location]
```

`workflow_id` and `name` validation: both must match
`^[a-z][a-z0-9_]{0,31}$`. Disk reads/writes are per-workflow; one
workflow's file is unaffected by another's.

### C) HTTP API

Added under `/api/workflows/<workflow_id>/locations`:

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/api/workflows/<wf>/locations` | — | `{name: Location}` |
| PUT | `/api/workflows/<wf>/locations/<name>` | `{x, y, theta}` | the saved `Location` |
| POST | `/api/workflows/<wf>/locations/<name>/teach` | — (no body) | the saved `Location` — snapshots the current `_pose` from `nav.py` |
| DELETE | `/api/workflows/<wf>/locations/<name>` | — | `204` (or `404` if absent) |

Validation:
- `<workflow_id>` and `<name>` must match `^[a-z][a-z0-9_]{0,31}$`.
  Anything else returns `400`.
- POST `/teach` returns `409` with `{"error": "no_pose"}` when `_pose`
  is `None` (no current pose to snapshot).

Routes are added to `backend/app/api/workflow.py` (the existing
workflow REST module), not to `nav.py` — they are workflow concerns.
The store module imports cleanly into both.

**SSE deliberately unchanged.** `/api/nav/status/stream` does *not*
gain a `locations` field. Workflow location data is consumed via
direct GET (and refetched after teach/delete actions) so the
generic nav SSE stays generic.

### D) Workflow integration

`backend/app/tools/stretch_tools.py` adds a new function:

```python
class LocationNotTaughtError(LookupError):
    """Named location has no pose in the workflow's runtime store."""

def get_workflow_location(
    workflow_id: str, name: str,
) -> tuple[float, float, float]:
    locs = workflow_locations_store.load(workflow_id)
    if name not in locs:
        raise LocationNotTaughtError(
            f"Location {name!r} for workflow {workflow_id!r} "
            f"has not been taught. Open the dashboard, drive the "
            f"robot to the spot, and click Save in the "
            f"{workflow_id} card's Locations panel."
        )
    loc = locs[name]
    return float(loc["x"]), float(loc["y"]), float(loc["theta"])
```

`get_object_pose(name)` (the existing function) is rewritten as a thin
shim that calls `get_workflow_location(WORKFLOW_ID, name)` where
`WORKFLOW_ID` is imported from the caller's workflow module. The
existing call sites in `medication_delivery.py` (lines 198, 250, 383)
keep working; only their resolution path changes. The
`location:` blocks inside `cure/config.yaml objects:` are deleted in
this PR — they become dead config.

`medication_delivery.py` declares:

```python
WORKFLOW_ID = "medication_delivery"
REQUIRED_LOCATIONS = ("medicine", "patient", "origin")
```

`REQUIRED_LOCATIONS` is the list the dashboard reads to know which
labels to display in the teach UI. It's a tuple of strings — the
dashboard does not depend on the workflow's internal logic, only on
this exported manifest.

`get_object_pose()` already wraps exceptions; `LocationNotTaughtError`
flows through the existing `_fail()` / `error_handler_node` path. The
error message (verbatim) appears in `state["errors"]`.

### E) Workflow registry (minimal)

A tiny manifest exposes per-workflow metadata to the frontend without
hardcoding workflow names there:

`backend/app/api/workflow.py` adds a `GET /api/workflows` route that
returns:
```json
[
  {"id": "medication_delivery",
   "required_locations": ["medicine", "patient", "origin"]}
]
```

The manifest is hand-maintained for now (single workflow), assembled
from each workflow module's `WORKFLOW_ID` + `REQUIRED_LOCATIONS`
constants. When a second workflow lands, its module is added to the
manifest builder.

### F) Frontend: workflow Locations panel

A new `LocationsPanel` component is added to the medication-delivery
dashboard at `/`. It is mounted inside `RobotDashboard`
(`frontend/components/robot-dashboard.tsx`) near the existing workflow
input form (above or beside the patient/medication input prompt). The
panel is collapsible — its default state is collapsed when all
`required_locations` are taught, expanded when any are missing, so a
fresh backend boot surfaces the setup step prominently.

Behavior:
```
medication_delivery — Locations
  required: medicine ✓  patient ✗ (not taught)  origin ✓
  ───
  Save current pose as: [▼ medicine | patient | origin]  [Save]
  ───
  medicine     (-1.23,  0.45)  90°       [×]
  origin       ( 0.00,  0.00)   0°       [×]
```

- Required-label status row reads `required_locations` from
  `/api/workflows` and the stored set from
  `/api/workflows/medication_delivery/locations`. Green check next to
  names present in both; red ✗ next to required-but-missing.
- "Save current pose as": dropdown of `required_locations` (plus a
  free-text option for ad-hoc names — future workflow extensibility).
  Disabled when `pose` from `useNavStatus()` is null.
- Each stored row has a delete button (`×`) → DELETE endpoint.
- The pose used for `/teach` is the current `_pose` on the backend —
  set previously by whatever means (drag on `/nav`, drive via
  `/teleop`, or a future live localizer).

New frontend module: `frontend/lib/workflow-locations-api.ts` —
`listWorkflowLocations(workflowId)`, `teachWorkflowLocation(workflowId,
name)`, `setWorkflowLocation(workflowId, name, pose)`,
`deleteWorkflowLocation(workflowId, name)`. Mirrors the structure of
`nav-api.ts`.

The frontend does not subscribe to a locations SSE — it re-fetches
after any mutating action. With one operator and per-name updates,
polling pressure is negligible.

### G) `/nav` unchanged

`frontend/components/nav-map.tsx` does **not** gain a locations panel,
location markers, or teach UI. The only `/nav` change is downstream of
section A — the frame fix propagates through `worldToPx`/`pxToWorld`
once the backend serves the corrected `MAP_METADATA`.

### H) Migration / one-time setup

After the upgraded backend ships, the operator must:

1. Start the backend; the
   `~/.cache/langgraph-A2A/locations/medication_delivery.json` file
   does not exist.
2. Drag the red robot on `/nav` to the pharmacy shelf.
3. On `/` (dashboard), open the medication-delivery Locations panel,
   pick `medicine` from the dropdown, click Save.
4. Repeat: drag robot to patient bedside → save as `patient`.
5. Repeat: drag robot to charging dock → save as `origin`.
6. Try a workflow run. It should navigate to real coordinates.

This is a one-shot operation. The file persists across backend
restarts. Whoever ships this PR is responsible for performing the
teach-and-save in the lab.

## Files touched

**Backend:**
- **new**: `backend/app/api/workflow_locations_store.py` —
  per-workflow JSON-backed CRUD store.
- **edit**: `backend/app/api/nav.py` — read `MAP_METADATA` from
  `maps/305/map.yaml`. **No locations changes.**
- **edit**: `backend/app/api/workflow.py` — add `GET /api/workflows`
  manifest endpoint; add the 4 workflow-locations routes.
- **edit**: `backend/app/tools/stretch_tools.py` —
  `get_workflow_location()` + `LocationNotTaughtError`. Rewrite
  `get_object_pose(name)` as a shim.
- **edit**: `backend/app/workflows/medication_delivery.py` — add
  `WORKFLOW_ID` and `REQUIRED_LOCATIONS` module-level constants;
  call `get_workflow_location(WORKFLOW_ID, ...)` at the existing
  nav-node sites.
- **edit**: `backend/cure/config.yaml` — remove `location:` blocks
  from `medicine`, `water`, `patient`, `test`, `origin`. Keep other
  fields.
- **delete**: `backend/nav_bridge/config/poses.yaml`.

**Frontend:**
- **new**: `frontend/lib/workflow-locations-api.ts` — REST client.
- **new**: `frontend/components/locations-panel.tsx` — the
  Locations card UI.
- **edit**: `frontend/components/robot-dashboard.tsx` — mount
  `LocationsPanel` for the medication-delivery workflow.

**Assets:**
- **regen**: `frontend/public/maps/305_map.png` — derived from baked
  `backend/maps/305/map.pgm` via `convert` (ImageMagick) at the same
  pixel resolution. Committed as a binary asset.

**Not touched:**
- `/nav` page, `nav-map.tsx`, `nav-bar.tsx`, `nav-status` context.
  SSE stream payload is unchanged.

## Testing

No test framework configured. Manual verification:

1. `cd backend && python -m app --host localhost --port 9999`. Confirm
   the log line shows `MAP_METADATA: origin=[-7.1846, -7.0851, …]`
   matching `maps/305/map.yaml`. Backend fails to start if the yaml
   is missing or unreadable.

2. `curl http://localhost:9999/api/workflows` → returns the manifest
   with `medication_delivery` and `required_locations`.

3. `curl http://localhost:9999/api/workflows/medication_delivery/locations`
   → `{}`.

4. `curl -X POST .../teach` while `_pose` is `None` → `409 no_pose`.

5. On `/nav`, drag the robot. Then on `/`, open the Locations panel,
   select `medicine`, click Save. The panel shows `medicine` as taught.
   `curl .../locations` shows it. Refresh `/` → still there.

6. Restart backend → location survives.

7. With only `medicine` taught, run the workflow → it fails at
   `navigate_to_patient_node` (or earlier) with
   `LocationNotTaughtError: Location 'patient' for workflow
   'medication_delivery' has not been taught.`. Error reaches the
   dashboard via the existing error path.

8. Teach all three. Run workflow → drives to actual map-frame coords.

9. Dashboard frame check: drag robot to a known physical landmark.
   The displayed `(x, y)` matches `ros2 topic echo /amcl_pose` for
   the same place within ~10 cm.

10. `frontend/public/maps/305_map.png` visually matches the RViz view
    of the baked PGM (no ~161° rotation mismatch).

11. `/nav` page behavior is otherwise unchanged: drag-to-set-pose,
    drag-to-set-goal, layer controls, hover readout. No new UI on
    that page.
