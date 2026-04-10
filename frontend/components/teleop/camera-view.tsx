"use client";

import { useEffect, useRef } from "react";
import type { CameraName } from "@/types/robot";

const CAMERA_ROTATION: Record<CameraName, number> = {
  overhead: -90,
  realsense: 90,
  gripper: 0,
};

interface CameraViewProps {
  name: CameraName;
  src: string | null;
}

export function CameraView({ name, src }: CameraViewProps) {
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

  return (
    <div className="relative w-full h-full rounded-md border border-border bg-background/50 overflow-hidden flex items-center justify-center">
      <canvas
        ref={canvasRef}
        className="max-w-full max-h-full object-contain"
      />
      {!src && (
        <div className="absolute inset-0 flex items-center justify-center font-mono text-xs text-muted-foreground">
          NO SIGNAL &mdash; {name.toUpperCase()}
        </div>
      )}
    </div>
  );
}
