"""Bake a raw GS-pipeline map into the canonical world frame.

The 3DGS → PGM pipeline (`NYCU_3DGS/indoor_panorama/...`) emits a map that
sits in the *map* frame: rotated by `world_to_map_rotation_deg` about the
pivot `world_to_map_pivot_xy`, both recorded in `map_stats.json`. Nothing in
this repo reads that rotation, so every downstream consumer (Nav2 goals,
`cure/config.yaml objects:`, the frontend visualizer) silently assumes
rotation = 0 — which means coordinates authored in one of those places
disagree with the others by ~161°.

This script absorbs the rotation at ingest time: it rotates the raw PGM
about the pivot so the saved file *is* the world-frame map, then rewrites
`map.yaml` (new origin) and `map_stats.json` (rotation = 0). After baking,
every consumer can speak one frame and `world_to_map_rotation_deg` is
metadata, not a transform anyone has to remember to apply.

Usage:

    python backend/maps/bake_world_frame.py backend/maps/305
    python backend/maps/bake_world_frame.py backend/maps/305 --raw-subdir raw

The map directory must contain `raw/{map.pgm, map.yaml, map_stats.json}`.
Baked outputs are written into the map directory itself (one level above
`raw/`), which is where `nav.launch.py` already expects `map.yaml`.

Conventions:
- Nav2 PGM image: row 0 is the top of the image; the file's `origin: [x, y, 0]`
  is the lower-left corner in map-frame meters. Pixel grayscale follows
  Nav2's standard: 0 = occupied, 254 = free, 205 = unknown.
- `world_to_map_rotation_deg` is interpreted as a counter-clockwise rotation
  applied to a point expressed in the world frame to land it in the map
  frame, about `world_to_map_pivot_xy` (the rotation's fixed point, same
  numeric value in both frames). Forward: P_map = R(θ)·(P_world − pivot) + pivot.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import yaml

# Nav2 grayscale conventions (must match what nav2_map_server reads back).
NAV2_FREE = 254
NAV2_UNKNOWN = 205
NAV2_OCCUPIED = 0

# Counting heuristics for the baked map_stats.json. The raw counts in the
# input use slightly different thresholds (the source counts don't quite
# add up to W*H either — likely the pipeline excludes a thin border), so
# our "occupied/free/unknown" cells use the same probability thresholds
# Nav2 uses to interpret the PGM. See backend/maps/305/raw/map.yaml.
def _classify(image: np.ndarray, occupied_thresh: float, free_thresh: float) -> tuple[int, int, int]:
    prob = (255.0 - image.astype(np.float32)) / 255.0
    occ = int((prob > occupied_thresh).sum())
    free = int((prob < free_thresh).sum())
    unk = int(image.size - occ - free)
    return occ, free, unk


def _read_raw(raw_dir: Path) -> tuple[dict, dict, np.ndarray]:
    with open(raw_dir / "map.yaml") as f:
        map_yaml = yaml.safe_load(f)
    with open(raw_dir / "map_stats.json") as f:
        stats = json.load(f)
    pgm_path = raw_dir / map_yaml["image"]
    image = cv2.imread(str(pgm_path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise FileNotFoundError(f"Could not read {pgm_path}")
    return map_yaml, stats, image


def bake(map_dir: Path, raw_subdir: str = "raw") -> dict[str, Any]:
    raw_dir = map_dir / raw_subdir
    if not raw_dir.is_dir():
        raise FileNotFoundError(f"{raw_dir} does not exist")

    map_yaml, stats, raw_image = _read_raw(raw_dir)
    H_old, W_old = raw_image.shape
    res = float(map_yaml["resolution"])
    origin_old = np.array(map_yaml["origin"][:2], dtype=np.float64)  # map frame

    theta_deg = float(stats["world_to_map_rotation_deg"])
    theta = math.radians(theta_deg)
    pivot = np.array(stats["world_to_map_pivot_xy"], dtype=np.float64)

    # Forward rotation: P_map = R · (P_world − pivot) + pivot  (R is CCW).
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    R = np.array([[cos_t, -sin_t], [sin_t, cos_t]])

    # Helpers: image pixel ↔ map-frame meters.
    # Nav2 convention: row 0 is the top of the image, so y axis is flipped.
    def pix_to_map(col: float, row: float) -> np.ndarray:
        x = origin_old[0] + col * res
        y = origin_old[1] + (H_old - 1 - row) * res
        return np.array([x, y])

    # 4 corners of the raw PGM in map-frame meters → world frame via R^{-1}.
    corners_map = np.stack([
        pix_to_map(0,         0),
        pix_to_map(W_old - 1, 0),
        pix_to_map(0,         H_old - 1),
        pix_to_map(W_old - 1, H_old - 1),
    ])
    corners_world = (corners_map - pivot) @ R + pivot  # R^{-1} = R.T; (v) @ R.T == R @ v

    min_world = corners_world.min(axis=0)
    max_world = corners_world.max(axis=0)

    W_new = int(math.ceil((max_world[0] - min_world[0]) / res)) + 1
    H_new = int(math.ceil((max_world[1] - min_world[1]) / res)) + 1
    origin_new = min_world.copy()

    # For each new pixel, compute the corresponding world-frame point, then
    # forward-rotate into map frame, then sample the raw image (nearest).
    cols_new = np.arange(W_new, dtype=np.float64)
    rows_new = np.arange(H_new, dtype=np.float64)
    cc, rr = np.meshgrid(cols_new, rows_new)  # (H_new, W_new)

    world_x = origin_new[0] + cc * res
    world_y = origin_new[1] + (H_new - 1 - rr) * res

    # P_world − pivot, rotated by R, then + pivot
    wx = world_x - pivot[0]
    wy = world_y - pivot[1]
    map_x = cos_t * wx - sin_t * wy + pivot[0]
    map_y = sin_t * wx + cos_t * wy + pivot[1]

    # map-frame meters → old pixel indices (col, row).
    old_col_f = (map_x - origin_old[0]) / res
    old_row_f = (H_old - 1) - (map_y - origin_old[1]) / res

    # cv2.remap takes float32 maps and does bilinear/nearest with proper
    # boundary handling. For occupancy grids we want nearest-neighbor so
    # categorical cell values (free/occupied/unknown) aren't blended.
    out = cv2.remap(
        raw_image,
        old_col_f.astype(np.float32),
        old_row_f.astype(np.float32),
        interpolation=cv2.INTER_NEAREST,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=NAV2_UNKNOWN,
    )

    # ---- Write baked outputs at map_dir/{map.pgm, map.yaml, map_stats.json}

    cv2.imwrite(str(map_dir / "map.pgm"), out)

    baked_yaml = {
        "image": "map.pgm",
        "resolution": res,
        "origin": [float(origin_new[0]), float(origin_new[1]), 0.0],
        "negate": int(map_yaml.get("negate", 0)),
        "occupied_thresh": float(map_yaml.get("occupied_thresh", 0.65)),
        "free_thresh": float(map_yaml.get("free_thresh", 0.196)),
        "mode": map_yaml.get("mode", "trinary"),
    }
    with open(map_dir / "map.yaml", "w") as f:
        yaml.safe_dump(baked_yaml, f, sort_keys=False)

    occ, free, unk = _classify(
        out,
        occupied_thresh=baked_yaml["occupied_thresh"],
        free_thresh=baked_yaml["free_thresh"],
    )
    baked_stats = dict(stats)
    baked_stats.update({
        "world_to_map_rotation_deg": 0.0,
        "world_to_map_pivot_xy": [0.0, 0.0],
        "grid_size_px": [int(W_new), int(H_new)],
        "map_origin_in_map_frame": [float(origin_new[0]), float(origin_new[1])],
        "occupied_cells": occ,
        "free_cells": free,
        "unknown_cells": unk,
        "baked_from": f"{raw_subdir}/map.pgm",
        "baked_world_to_map_rotation_deg_applied": theta_deg,
        "baked_world_to_map_pivot_xy_applied": [float(pivot[0]), float(pivot[1])],
    })
    with open(map_dir / "map_stats.json", "w") as f:
        json.dump(baked_stats, f, indent=2)

    return {
        "map_dir": str(map_dir),
        "raw_size_px": [int(W_old), int(H_old)],
        "baked_size_px": [int(W_new), int(H_new)],
        "origin_old_map_frame": origin_old.tolist(),
        "origin_new_world_frame": origin_new.tolist(),
        "rotation_applied_deg": theta_deg,
        "pivot": pivot.tolist(),
        "occupied_cells": occ,
        "free_cells": free,
        "unknown_cells": unk,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("map_dir", type=Path, help="e.g. backend/maps/305")
    p.add_argument("--raw-subdir", default="raw",
                   help="subdirectory holding the raw PGM/yaml/stats (default: raw)")
    args = p.parse_args(argv)

    if not args.map_dir.is_dir():
        print(f"error: {args.map_dir} is not a directory", file=sys.stderr)
        return 2

    report = bake(args.map_dir, raw_subdir=args.raw_subdir)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
