/**
 * Render a ROS2 nav_msgs/OccupancyGrid into an HTMLCanvasElement that can
 * be used as the href of an SVG <image>. Caches by message timestamp so
 * we don't redraw on identical updates.
 *
 * Color scheme (matches RViz/Foxglove conventions):
 *   - data == -1 (unknown):  transparent
 *   - data == 0  (free):     transparent (or faint white if `freeAlpha`)
 *   - data 1..99:            graded red, alpha = data/100
 *   - data == 100 (occupied): solid red
 *
 * Override colors per-layer for visual separation (nvblox vs static
 * map vs costmaps).
 */

import type { OccupancyGrid } from "./ros-client"

export interface OccupancyGridStyle {
  /** RGB for occupied cells (0..255). */
  rgb: [number, number, number]
  /** Alpha for free cells (0..1). 0 = transparent. */
  freeAlpha: number
  /** Alpha multiplier for occupied (1.0 = full). */
  occupiedAlpha: number
}

export const STYLE_NVBLOX_2D: OccupancyGridStyle = {
  rgb: [239, 68, 68],   // tailwind red-500
  freeAlpha: 0,
  occupiedAlpha: 0.7,
}
export const STYLE_LOCAL_COSTMAP: OccupancyGridStyle = {
  rgb: [59, 130, 246],  // blue-500
  freeAlpha: 0,
  occupiedAlpha: 0.55,
}
export const STYLE_GLOBAL_COSTMAP: OccupancyGridStyle = {
  rgb: [99, 102, 241],  // indigo-500
  freeAlpha: 0,
  occupiedAlpha: 0.4,
}

interface CachedRender {
  width: number
  height: number
  dataUrl: string
}

// WeakMap keyed by the OccupancyGrid object — same object reference
// short-circuits the redraw. New messages get new objects, so we
// re-render once per update.
const cache = new WeakMap<OccupancyGrid, CachedRender>()

export function renderOccupancyGrid(
  grid: OccupancyGrid,
  style: OccupancyGridStyle,
): { dataUrl: string; width: number; height: number } | null {
  if (!grid?.info?.width || !grid?.info?.height) return null
  const cached = cache.get(grid)
  if (cached) return cached

  const { width, height } = grid.info
  const data = grid.data

  const canvas = document.createElement("canvas")
  canvas.width = width
  canvas.height = height
  const ctx = canvas.getContext("2d")
  if (!ctx) return null

  const img = ctx.createImageData(width, height)
  const out = img.data
  const [r, g, b] = style.rgb
  // ROS OccupancyGrid is row-major, origin at lower-left. Canvas origin
  // is upper-left → flip y while writing.
  for (let py = 0; py < height; py++) {
    for (let px = 0; px < width; px++) {
      const v = data[py * width + px]   // -128..127, but ROS uses -1, 0..100
      const dst = ((height - 1 - py) * width + px) * 4
      let alpha: number
      if (v < 0) {
        alpha = 0  // unknown → transparent
      } else if (v === 0) {
        alpha = style.freeAlpha
      } else {
        // 1..100 → graded
        alpha = (v / 100) * style.occupiedAlpha
      }
      out[dst]     = r
      out[dst + 1] = g
      out[dst + 2] = b
      out[dst + 3] = Math.round(alpha * 255)
    }
  }
  ctx.putImageData(img, 0, 0)
  const dataUrl = canvas.toDataURL("image/png")
  const result = { dataUrl, width, height }
  cache.set(grid, result)
  return result
}
