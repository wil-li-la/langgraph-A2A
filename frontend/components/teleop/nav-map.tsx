"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { RobotCommand } from "@/lib/teleop-protocol";
import { MAP_CONFIG, type NavState, type RobotPose, type NavPathPoint } from "@/types/robot";
import { setNavPose, type SetNavPoseResult } from "@/lib/nav-api";
import { useNavStatus } from "@/contexts/nav-status";

interface NavMapProps {
  navState: NavState;
  robotPose: RobotPose | null;
  navPath: NavPathPoint[];
  sendCommand: (cmd: RobotCommand) => void;
}

interface DragState {
  startMx: number;
  startMy: number;
  curMx: number;
  curMy: number;
}

function mapToPixel(mx: number, my: number): [number, number] {
  const px = (mx - MAP_CONFIG.originX) / MAP_CONFIG.resolution;
  const py = MAP_CONFIG.height - (my - MAP_CONFIG.originY) / MAP_CONFIG.resolution;
  return [px, py];
}

function pixelToMap(px: number, py: number): [number, number] {
  const mx = MAP_CONFIG.originX + px * MAP_CONFIG.resolution;
  const my = MAP_CONFIG.originY + (MAP_CONFIG.height - py) * MAP_CONFIG.resolution;
  return [mx, my];
}

function eventToMap(canvas: HTMLCanvasElement, e: React.MouseEvent): [number, number] {
  const rect = canvas.getBoundingClientRect();
  const scaleX = MAP_CONFIG.width / rect.width;
  const scaleY = MAP_CONFIG.height / rect.height;
  return pixelToMap((e.clientX - rect.left) * scaleX, (e.clientY - rect.top) * scaleY);
}

function summarizeSeed(r: SetNavPoseResult): { kind: "ok" | "err"; text: string } {
  if (!r.seed.forwarded) return { kind: "err", text: `forward failed: ${r.seed.error ?? "unknown"}` };
  if (!r.seed.ok) return { kind: "err", text: `robot rejected: ${r.seed.reply ?? "unknown"}` };
  return { kind: "ok", text: `seeded → robot ${r.seed.reply ?? "ok"}` };
}

export function NavMap({ navState, robotPose, navPath, sendCommand }: NavMapProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const mapImgRef = useRef<HTMLImageElement | null>(null);
  const dragRef = useRef<DragState | null>(null);
  const [mapLoaded, setMapLoaded] = useState(false);
  // Re-render tick used both to repaint during a drag (dragRef mutates
  // without triggering React) and to flush the drag-end overlay clear.
  const [dragTick, setDragTick] = useState(0);
  const [seedStatus, setSeedStatus] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const { localization } = useNavStatus();

  useEffect(() => {
    const img = new Image();
    img.onload = () => {
      mapImgRef.current = img;
      setMapLoaded(true);
    };
    img.src = MAP_CONFIG.imageUrl;
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    const img = mapImgRef.current;
    if (!canvas || !img || !mapLoaded) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    canvas.width = MAP_CONFIG.width;
    canvas.height = MAP_CONFIG.height;

    ctx.drawImage(img, 0, 0);

    if (navPath.length > 1) {
      ctx.strokeStyle = "rgba(255,255,255,0.5)";
      ctx.lineWidth = 2;
      ctx.beginPath();
      const [sx, sy] = mapToPixel(navPath[0].x, navPath[0].y);
      ctx.moveTo(sx, sy);
      for (let i = 1; i < navPath.length; i++) {
        const [px, py] = mapToPixel(navPath[i].x, navPath[i].y);
        ctx.lineTo(px, py);
      }
      ctx.stroke();
    }

    if (robotPose) {
      const [rx, ry] = mapToPixel(robotPose.x, robotPose.y);
      const arrowLen = 14;
      const ax = rx + arrowLen * Math.cos(-robotPose.theta);
      const ay = ry + arrowLen * Math.sin(-robotPose.theta);
      ctx.strokeStyle = "rgba(220,38,38,0.95)";
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.moveTo(rx, ry);
      ctx.lineTo(ax, ay);
      ctx.stroke();
      ctx.fillStyle = "rgba(220,38,38,1)";
      ctx.beginPath();
      ctx.arc(rx, ry, 7, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = "rgba(255,255,255,0.9)";
      ctx.lineWidth = 2;
      ctx.stroke();
    }

    // Live preview of the in-progress shift+drag seed gesture.
    const drag = dragRef.current;
    if (drag) {
      const [sx, sy] = mapToPixel(drag.startMx, drag.startMy);
      const [cx, cy] = mapToPixel(drag.curMx, drag.curMy);
      ctx.strokeStyle = "rgba(56,189,248,0.95)";   // sky-400
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.moveTo(sx, sy);
      ctx.lineTo(cx, cy);
      ctx.stroke();
      ctx.fillStyle = "rgba(56,189,248,1)";
      ctx.beginPath();
      ctx.arc(sx, sy, 8, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = "rgba(255,255,255,0.9)";
      ctx.lineWidth = 2;
      ctx.stroke();
    }
  }, [mapLoaded, robotPose, navPath, dragTick]);

  const onMouseDown = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas || !e.shiftKey || e.button !== 0) return;
    e.preventDefault();
    const [mx, my] = eventToMap(canvas, e);
    dragRef.current = { startMx: mx, startMy: my, curMx: mx, curMy: my };
    setDragTick((t) => t + 1);
  }, []);

  const onMouseMove = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas || !dragRef.current) return;
    const [mx, my] = eventToMap(canvas, e);
    dragRef.current.curMx = mx;
    dragRef.current.curMy = my;
    setDragTick((t) => t + 1);
  }, []);

  const finishDrag = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    const drag = dragRef.current;
    if (!canvas || !drag) return;
    dragRef.current = null;
    setDragTick((t) => t + 1);

    const [mx, my] = eventToMap(canvas, e);
    const dx = mx - drag.startMx;
    const dy = my - drag.startMy;
    // Below ~0.05m drag (≈8 px on this map) the heading vector is noise;
    // default to 0 rad. Operator can re-seed with a bigger drag for heading.
    const theta = Math.hypot(dx, dy) >= 0.05 ? Math.atan2(dy, dx) : 0;

    setSeedStatus({ kind: "ok", text: `seeding (${drag.startMx.toFixed(2)}, ${drag.startMy.toFixed(2)}, ${(theta * 180 / Math.PI).toFixed(0)}°)…` });
    setNavPose({ x: drag.startMx, y: drag.startMy, theta })
      .then((r) => setSeedStatus(summarizeSeed(r)))
      .catch((err) => setSeedStatus({ kind: "err", text: `error: ${err.message ?? err}` }));
  }, []);

  const onMouseUp = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!dragRef.current) return;
    finishDrag(e);
  }, [finishDrag]);

  const onMouseLeave = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    // Treat leaving the canvas as commit at last-known cursor — avoids
    // a stranded ghost arrow if the operator drags off the map.
    if (!dragRef.current) return;
    finishDrag(e);
  }, [finishDrag]);

  const handleClick = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      // Shift-clicks are handled by the drag handlers above; ignore the
      // synthesized click event so we don't double-fire setNavPose.
      if (e.shiftKey) return;
      if (navState === "navigating") return;

      const canvas = canvasRef.current;
      if (!canvas) return;
      const [mx, my] = eventToMap(canvas, e);
      sendCommand({ type: "nav_goal", x: mx, y: my, theta: 0.0 });
    },
    [navState, sendCommand],
  );

  const stateLabel: Record<NavState, string> = {
    idle: "IDLE",
    navigating: "NAVIGATING",
    succeeded: "SUCCEEDED",
    failed: "FAILED",
  };

  const locColor =
    localization?.state === "ok" ? "text-emerald-400"
    : localization?.state === "stale" ? "text-amber-400"
    : localization?.state === "unseeded" ? "text-red-400"
    : "text-muted-foreground/60";
  const locLabel =
    localization?.state === "ok" ? "AMCL: OK"
    : localization?.state === "stale" ? "AMCL: STALE"
    : localization?.state === "unseeded" ? "AMCL: UNSEEDED"
    : "AMCL: ?";

  return (
    <div className="relative w-full h-full rounded-md border border-border bg-background/50 overflow-hidden flex flex-col">
      <div className="flex items-center justify-between gap-2 px-2 py-1.5 shrink-0">
        <span className={`font-mono text-sm font-medium ${
          navState === "failed" ? "text-red-400" : "text-muted-foreground"
        }`}>
          NAV: {stateLabel[navState]}
        </span>
        <span className={`font-mono text-xs ${locColor}`}>{locLabel}</span>
        <span className="font-mono text-xs text-muted-foreground/60 ml-auto">
          click: goal · shift+drag: seed pose
        </span>
        {navState === "navigating" && (
          <button
            className="rounded-md border border-red-400/30 bg-red-400/10 px-3 py-1 font-mono text-sm font-medium text-red-400 hover:bg-red-400/20"
            onClick={() => sendCommand({ type: "cancel_nav" })}
          >
            CANCEL
          </button>
        )}
      </div>
      {seedStatus && (
        <div className={`px-2 py-1 font-mono text-xs border-b border-border ${
          seedStatus.kind === "ok" ? "bg-emerald-500/10 text-emerald-300" : "bg-red-500/10 text-red-300"
        }`}>
          {seedStatus.text}
        </div>
      )}
      <div className="flex-1 min-h-0 flex items-center justify-center p-1">
        <canvas
          ref={canvasRef}
          className="cursor-crosshair"
          style={{
            maxWidth: "100%",
            maxHeight: "100%",
            width: "auto",
            height: "100%",
            aspectRatio: `${MAP_CONFIG.width} / ${MAP_CONFIG.height}`,
            imageRendering: "pixelated",
          }}
          onClick={handleClick}
          onMouseDown={onMouseDown}
          onMouseMove={onMouseMove}
          onMouseUp={onMouseUp}
          onMouseLeave={onMouseLeave}
        />
        {!mapLoaded && (
          <div className="absolute inset-0 flex items-center justify-center font-mono text-sm text-muted-foreground">
            LOADING MAP...
          </div>
        )}
      </div>
    </div>
  );
}
