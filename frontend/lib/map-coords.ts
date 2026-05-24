// frontend/lib/map-coords.ts
//
// Pure world↔pixel coord helpers for any SVG map rendering the
// /api/nav/map metadata. Consumed by both the /nav page's NavMap and
// the dashboard's WorkflowLocationsMap so both speak the same frame.
//
// World y is flipped relative to SVG y: world origin is the bottom-left
// corner of the map, SVG origin is top-left. The conversions account
// for that and for the resolution (metres per pixel).

import type { NavMapMetadata } from "@/lib/nav-api"

export function worldToPx(
  meta: NavMapMetadata | null, x: number, y: number,
): { px: number; py: number } {
  if (!meta) return { px: 0, py: 0 }
  return {
    px: (x - meta.origin[0]) / meta.resolution,
    py: meta.height_px - (y - meta.origin[1]) / meta.resolution,
  }
}

export function pxToWorld(
  meta: NavMapMetadata | null, px: number, py: number,
): { x: number; y: number } {
  if (!meta) return { x: 0, y: 0 }
  return {
    x: px * meta.resolution + meta.origin[0],
    y: (meta.height_px - py) * meta.resolution + meta.origin[1],
  }
}

export function eventToWorld(
  svg: SVGSVGElement | null,
  meta: NavMapMetadata | null,
  e: { clientX: number; clientY: number },
): { x: number; y: number } | null {
  if (!svg || !meta) return null
  const pt = svg.createSVGPoint()
  pt.x = e.clientX
  pt.y = e.clientY
  const ctm = svg.getScreenCTM()
  if (!ctm) return null
  const local = pt.matrixTransform(ctm.inverse())
  return pxToWorld(meta, local.x, local.y)
}
