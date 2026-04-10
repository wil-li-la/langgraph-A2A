"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { RobotCommand } from "@/lib/teleop-protocol";
import { MAP_CONFIG, type NavState, type RobotPose, type NavPathPoint } from "@/types/robot";

interface NavMapProps {
  navState: NavState;
  robotPose: RobotPose | null;
  navPath: NavPathPoint[];
  sendCommand: (cmd: RobotCommand) => void;
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

export function NavMap({ navState, robotPose, navPath, sendCommand }: NavMapProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const mapImgRef = useRef<HTMLImageElement | null>(null);
  const [mapLoaded, setMapLoaded] = useState(false);

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

      const arrowLen = 8;
      const ax = rx + arrowLen * Math.cos(-robotPose.theta);
      const ay = ry + arrowLen * Math.sin(-robotPose.theta);
      ctx.strokeStyle = "rgba(255,255,255,0.8)";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(rx, ry);
      ctx.lineTo(ax, ay);
      ctx.stroke();

      ctx.fillStyle = "rgba(255,255,255,0.9)";
      ctx.beginPath();
      ctx.arc(rx, ry, 5, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = "rgba(0,0,0,0.5)";
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }
  }, [mapLoaded, robotPose, navPath]);

  const handleClick = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      if (navState === "navigating") return;

      const canvas = canvasRef.current;
      if (!canvas) return;

      const rect = canvas.getBoundingClientRect();
      const scaleX = MAP_CONFIG.width / rect.width;
      const scaleY = MAP_CONFIG.height / rect.height;
      const px = (e.clientX - rect.left) * scaleX;
      const py = (e.clientY - rect.top) * scaleY;
      const [mx, my] = pixelToMap(px, py);

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

  return (
    <div className="relative w-full h-full rounded-md border border-border bg-background/50 overflow-hidden flex flex-col">
      <div className="flex items-center justify-between px-2 py-1.5 shrink-0">
        <span className={`font-mono text-sm font-medium ${
          navState === "failed" ? "text-red-400" : "text-muted-foreground"
        }`}>
          NAV: {stateLabel[navState]}
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
