"use client";

import { useEffect, useRef } from "react";
import type { CameraName } from "@/types/robot";
import type { DetectionBox } from "@/lib/api";

interface LabelRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

interface PlacedLabel {
  box: DetectionBox;
  label: string;
  bg: LabelRect;
  fontSize: number;
  padX: number;
  stroke: number;
  // Dashed leader connecting bbox to displaced label, drawn only when
  // the label couldn't sit flush against the bbox edge.
  leader: { x1: number; y1: number; x2: number; y2: number } | null;
}

/**
 * Greedy collision-aware label placement.
 *
 * For each detection, try a series of candidate positions adjacent to
 * the bbox (above → inside-top → inside-bottom → below). The first
 * candidate that doesn't overlap a previously placed label wins. If
 * none fit, the label is pushed straight down until it finds a gap and
 * gets a dashed leader line back to the bbox so the operator can still
 * tell which label belongs to which box.
 *
 * Order matters: place high-confidence boxes first so their preferred
 * (flush-with-bbox) spots aren't stolen by less important detections.
 */
function placeLabels(
  boxes: DetectionBox[],
  vbW: number,
  vbH: number,
): PlacedLabel[] {
  const stroke = Math.max(2, Math.min(vbW, vbH) * 0.004);
  const fontSize = Math.max(10, Math.min(vbW, vbH) * 0.03);
  const padX = fontSize * 0.4;
  const bgH = fontSize * 1.35;

  const overlap = (a: LabelRect, b: LabelRect) =>
    a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;

  const placed: LabelRect[] = [];
  const sorted = [...boxes]
    .map((b, idx) => ({ b, idx }))
    .sort((a, b) => (b.b.confidence || 0) - (a.b.confidence || 0));

  // Sparse output array so we restore original order at the end.
  const out: PlacedLabel[] = new Array(boxes.length);

  for (const { b, idx } of sorted) {
    const [x1, y1, , y2] = b.bbox_2d;
    const label = `${b.label} ${(b.confidence * 100).toFixed(0)}%`;
    const wantW = label.length * fontSize * 0.62 + padX * 2;
    const bgW = Math.min(vbW - Math.max(0, x1), wantW);
    const lx = Math.min(x1, vbW - bgW);

    const candidates: { y: number; flush: boolean }[] = [
      { y: y1 - bgH, flush: true },     // above
      { y: y1 + stroke, flush: true },  // inside-top
      { y: y2 - bgH - stroke, flush: true }, // inside-bottom
      { y: y2 + stroke, flush: true },  // below
    ];

    let chosen: LabelRect | null = null;
    let flush = true;
    for (const c of candidates) {
      if (c.y < 0 || c.y + bgH > vbH) continue;
      const cand: LabelRect = { x: lx, y: c.y, w: bgW, h: bgH };
      if (!placed.some((p) => overlap(cand, p))) {
        chosen = cand;
        flush = c.flush;
        break;
      }
    }

    // No flush spot — push label straight down from above-bbox position
    // until there's a gap. Will get a leader line.
    if (!chosen) {
      flush = false;
      let y = Math.max(0, y1 - bgH);
      const cand: LabelRect = { x: lx, y, w: bgW, h: bgH };
      let bumps = 0;
      while (placed.some((p) => overlap(cand, p)) && bumps < 50) {
        y += bgH;
        cand.y = y;
        if (y + bgH > vbH) {
          // Stack to the right of the bbox as a last resort.
          cand.x = Math.min(x1 + bgW, vbW - bgW);
          y = Math.max(0, y1 - bgH);
          cand.y = y;
        }
        bumps++;
      }
      chosen = cand;
    }

    placed.push(chosen);

    const leader =
      !flush
        ? {
            x1: Math.max(0, Math.min(x1 + (chosen.w / 2), x1 + (boxes[idx]?.bbox_2d[2] - x1) / 2)),
            y1: y1,
            x2: chosen.x + chosen.w / 2,
            y2: chosen.y + chosen.h / 2,
          }
        : null;

    out[idx] = {
      box: b,
      label,
      bg: chosen,
      fontSize,
      padX,
      stroke,
      leader,
    };
  }

  return out;
}

const CAMERA_ROTATION: Record<CameraName, number> = {
  overhead: -90,
  realsense: 90,
  gripper: 0,
};

export interface CameraDetectionOverlay {
  // Image dims the VLM saw (post-rotation, upright). Used as the SVG
  // viewBox so bboxes in those pixel coords line up exactly with the
  // canvas (which uses `object-contain` against the same aspect ratio).
  imageW: number;
  imageH: number;
  boxes: DetectionBox[];
  // For sanity logging in the UI; otherwise unused.
  query?: string;
  ts?: string;
}

interface CameraViewProps {
  name: CameraName;
  src: string | null;
  detections?: CameraDetectionOverlay | null;
}

export function CameraView({ name, src, detections }: CameraViewProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);

  useEffect(() => {
    if (!imgRef.current) {
      imgRef.current = new Image();
    }
    const img = imgRef.current;
    const canvas = canvasRef.current;
    if (!canvas || !src) return;

    img.onload = () => {
      const ctx = canvas.getContext("2d");
      if (!ctx) return;

      const rot = CAMERA_ROTATION[name] ?? 0;

      if (rot === 90 || rot === -90) {
        canvas.width = img.naturalHeight;
        canvas.height = img.naturalWidth;
        ctx.save();
        ctx.translate(canvas.width / 2, canvas.height / 2);
        ctx.rotate((rot * Math.PI) / 180);
        ctx.drawImage(img, -img.naturalWidth / 2, -img.naturalHeight / 2);
        ctx.restore();
      } else {
        canvas.width = img.naturalWidth;
        canvas.height = img.naturalHeight;
        ctx.drawImage(img, 0, 0);
      }
    };
    img.src = src;
  }, [src, name]);

  const overlayBoxes = detections?.boxes ?? [];
  // viewBox in the SVG matches the post-rotation pixel space the VLM
  // reasoned in, so we can place rects directly in bbox_2d coords. The
  // SVG uses `preserveAspectRatio=xMidYMid meet` (the default), mirroring
  // the canvas's `object-contain` letterbox behavior.
  const vbW = detections?.imageW ?? 1;
  const vbH = detections?.imageH ?? 1;

  return (
    <div className="relative w-full h-full rounded-md border border-border bg-background/50 overflow-hidden flex items-center justify-center">
      <canvas
        ref={canvasRef}
        className="max-w-full max-h-full object-contain"
      />
      {overlayBoxes.length > 0 && (
        <svg
          viewBox={`0 0 ${vbW} ${vbH}`}
          className="pointer-events-none absolute inset-0 w-full h-full"
        >
          {placeLabels(overlayBoxes, vbW, vbH).map((p, i) => {
            const [x1, y1, x2, y2] = p.box.bbox_2d;
            const w = Math.max(1, x2 - x1);
            const h = Math.max(1, y2 - y1);
            return (
              <g key={i}>
                <rect
                  x={x1}
                  y={y1}
                  width={w}
                  height={h}
                  fill="none"
                  stroke="#22d3ee"
                  strokeWidth={p.stroke}
                />
                {/* Leader line — only drawn when the label was pushed
                    away from the bbox edge to resolve a collision. */}
                {p.leader && (
                  <line
                    x1={p.leader.x1}
                    y1={p.leader.y1}
                    x2={p.leader.x2}
                    y2={p.leader.y2}
                    stroke="#22d3ee"
                    strokeWidth={p.stroke}
                    strokeDasharray={`${p.stroke * 2} ${p.stroke * 2}`}
                    opacity={0.6}
                  />
                )}
                <rect
                  x={p.bg.x}
                  y={p.bg.y}
                  width={p.bg.w}
                  height={p.bg.h}
                  fill="#22d3ee"
                  opacity={0.92}
                />
                <text
                  x={p.bg.x + p.padX}
                  y={p.bg.y + p.fontSize * 0.98}
                  fill="#0b0b0b"
                  fontFamily="ui-monospace, monospace"
                  fontSize={p.fontSize}
                  fontWeight={600}
                >
                  {p.label}
                </text>
              </g>
            );
          })}
        </svg>
      )}
      {!src && (
        <div className="absolute inset-0 flex items-center justify-center font-mono text-sm text-muted-foreground">
          NO SIGNAL &mdash; {name.toUpperCase()}
        </div>
      )}
    </div>
  );
}
