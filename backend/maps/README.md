# Maps

Nav2 occupancy grids for the lab. Each room/map lives in its own subdirectory
(e.g. `305/`) and is loaded by `backend/nav_bridge/launch/nav.launch.py` via
`map.yaml`.

## Layout

```
backend/maps/
├── bake_world_frame.py     ← ingest tool (see "Baking" below)
├── README.md               ← you are here
└── 305/
    ├── map.pgm             ← canonical, baked to world frame
    ├── map.yaml            ← canonical, what Nav2 loads
    ├── map_stats.json      ← canonical, rotation = 0
    └── raw/                ← exactly what the GS pipeline emitted
        ├── map.pgm
        ├── map.yaml
        └── map_stats.json  ← rotation_deg ≠ 0 lives here
```

The files at the top level of each map directory (`305/map.yaml` etc.) are
**generated** — never edit them by hand. Edit `raw/` and re-bake.

## Why "baked"

The 3DGS → PGM pipeline (`NYCU_3DGS/indoor_panorama/...`) emits the map in
its own coordinate frame: rotated by `world_to_map_rotation_deg` about
`world_to_map_pivot_xy`, both recorded in `map_stats.json`. For map `305`
this rotation is **161°**.

Nothing else in this repo reads that rotation. Every downstream consumer
(Nav2 goals, `cure/config.yaml objects:`, the frontend visualizer, any
hand-authored waypoint) implicitly assumes rotation = 0. If we let the
rotated map flow through, those consumers each have to remember to apply
the same 161° rotation, and the first one that forgets ships a bug where
"forward 30 cm" goes sideways into a wall.

Instead, we absorb the rotation **once**, at ingest time, into the asset
itself. Everything downstream then speaks one frame (the world / room frame)
and `world_to_map_rotation_deg` becomes traceability metadata —
`baked_world_to_map_rotation_deg_applied` records what was rolled in — not
a transform anyone has to apply.

## Baking

```bash
# from repo root, with the backend venv active
python backend/maps/bake_world_frame.py backend/maps/305
```

What it does:

1. Reads `raw/map.pgm`, `raw/map.yaml`, `raw/map_stats.json`.
2. Rotates the PGM by `-world_to_map_rotation_deg` about `world_to_map_pivot_xy`
   (nearest-neighbor; the PGM is categorical occupancy, not a photo).
3. Computes the new axis-aligned bounding box and the world-frame origin
   (lower-left corner in meters).
4. Writes baked `map.pgm`, `map.yaml` (new `origin`), and `map_stats.json`
   (rotation = 0, pivot = [0, 0], plus the `baked_*_applied` fields).

The baked output replaces whatever was at the top level of the map dir. Raw
is never touched.

Re-run after any change to `raw/` (new map build from the GS pipeline). The
script is idempotent: if `world_to_map_rotation_deg` in `raw/map_stats.json`
is already 0, the bake is a no-op rotation but still re-emits canonical
files.

## Coordinate migration for existing waypoints

The bake rotates **the map**. Anything else that lives in coordinates —
specifically `backend/cure/config.yaml` `objects:` (medicine, water,
patient, origin) — does not move automatically.

Two cases:

- **If those waypoints were authored in world frame** (someone measured them
  in the room with a tape, or set them in the dashboard before the map
  existed): they are already correct. No migration needed.
- **If they were authored in the raw map frame** (e.g. by clicking on the
  raw PGM in RViz): they need the same `R(-161°)` rotation about the pivot.
  See `bake_world_frame.py` for the math. A migration helper can be added
  here when needed — `transform_point_map_to_world(pt, raw_stats) -> pt`.

Pick the case by checking that `navigate_skill(*get_object_pose("origin"))`
actually returns the robot to a sensible spot under the **baked** map. If
it goes 161° off, the waypoints are in raw map frame and need migration.

## When to add a new map

Drop the GS pipeline output into `backend/maps/<name>/raw/`, run
`bake_world_frame.py backend/maps/<name>`, then point
`nav.launch.py::map_yaml` at `backend/maps/<name>/map.yaml`.
