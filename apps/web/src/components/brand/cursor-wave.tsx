"use client";

import type { CSSProperties } from "react";
import { useEffect, useRef } from "react";

type Shape = "circle" | "square" | "triangle";

type SolidColor = {
  kind: "solid";
  value: string;
};

type GradientColor = {
  kind: "gradient";
  stops: readonly [string, string];
};

type ShapeColor = SolidColor | GradientColor;

type Cell = {
  x: number;
  y: number;
  shape: Shape;
  color: ShapeColor;
  peakScale: number;
  rotation: number;
  rotationSpeed: number;
  scale: number;
};

type Burst = {
  x: number;
  y: number;
  startedAt: number;
};

type CursorWaveProps = {
  cellSize?: number;
  influenceRadiusVmin?: number;
  attackTime?: number;
  releaseTime?: number;
  idleScale?: number;
  minPeakScale?: number;
  maxPeakScale?: number;
  burstSpeed?: number;
  burstThickness?: number;
  backgroundColor?: string;
  colors?: readonly string[];
  opacity?: number;
  className?: string;
  style?: CSSProperties;
};

const TAU = Math.PI * 2;
const SHAPES: readonly Shape[] = ["circle", "triangle", "square"];
const COLORS: readonly ShapeColor[] = [
  { kind: "solid", value: "#ff3d8d" },
  { kind: "solid", value: "#ff553d" },
  { kind: "solid", value: "#ff8a1f" },
  { kind: "solid", value: "#ffd60a" },
  { kind: "solid", value: "#2dd67b" },
  { kind: "solid", value: "#21c997" },
  { kind: "solid", value: "#1fc7d4" },
  { kind: "solid", value: "#21b8f2" },
  { kind: "solid", value: "#4f75ff" },
  { kind: "solid", value: "#725bff" },
  { kind: "solid", value: "#9b6dff" },
  { kind: "solid", value: "#c4b5fd" },
  { kind: "solid", value: "#b9c4cf" },
  { kind: "gradient", stops: ["#ff8a1f", "#ff3d8d"] },
  { kind: "gradient", stops: ["#ffd60a", "#ff553d"] },
  { kind: "gradient", stops: ["#2dd67b", "#21b8f2"] },
  { kind: "gradient", stops: ["#4f75ff", "#c04bff"] },
];

/** Return a stable pseudo-random value for a grid coordinate and salt. */
function cellNoise(column: number, row: number, salt: number): number {
  let value = Math.imul(column + 1, 374761393);
  value = Math.imul(value ^ Math.imul(row + 1, 668265263), 1274126177);
  value ^= Math.imul(salt + 1, 2246822519);
  value = Math.imul(value ^ (value >>> 13), 3266489917);
  return ((value ^ (value >>> 16)) >>> 0) / 4294967296;
}

function smoothstep(edge0: number, edge1: number, value: number): number {
  const t = Math.min(1, Math.max(0, (value - edge0) / (edge1 - edge0)));
  return t * t * (3 - 2 * t);
}

function drawCell(
  context: CanvasRenderingContext2D,
  cell: Cell,
  cellSize: number,
  rotation: number,
): void {
  const size = cellSize * 0.37 * cell.scale;
  if (size < 0.16) return;

  context.save();
  context.translate(cell.x, cell.y);
  context.rotate(rotation);
  context.beginPath();

  if (cell.shape === "circle") {
    context.arc(0, 0, size * 0.5, 0, TAU);
  } else if (cell.shape === "square") {
    context.rect(-size * 0.5, -size * 0.5, size, size);
  } else {
    const radius = size * 0.64;
    context.moveTo(0, -radius);
    context.lineTo(radius * 0.866, radius * 0.5);
    context.lineTo(-radius * 0.866, radius * 0.5);
    context.closePath();
  }

  if (cell.color.kind === "gradient") {
    const gradient = context.createLinearGradient(0, -size * 0.55, 0, size * 0.55);
    gradient.addColorStop(0, cell.color.stops[0]);
    gradient.addColorStop(1, cell.color.stops[1]);
    context.fillStyle = gradient;
  } else {
    context.fillStyle = cell.color.value;
  }

  context.fill();
  context.restore();
}

/**
 * Full-surface cursor-reactive shape field.
 *
 * The component owns only its canvas and pointer interaction, so it can be
 * mounted behind any future product UI without depending on application state.
 */
export default function CursorWave({
  cellSize = 40,
  influenceRadiusVmin = 22,
  attackTime = 0.5,
  releaseTime = 0.6,
  idleScale = 0.09,
  minPeakScale = 1,
  maxPeakScale = 3,
  burstSpeed = 1200,
  burstThickness = 180,
  backgroundColor = "#080808",
  colors,
  opacity = 1,
  className,
  style,
}: CursorWaveProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const container = containerRef.current;
    const canvas = canvasRef.current;
    const context = canvas?.getContext("2d");
    if (!container || !canvas || !context) return;

    let width = 0;
    let height = 0;
    let cells: Cell[] = [];
    let bursts: Burst[] = [];
    let animationFrame = 0;
    let lastTime = performance.now();
    let pointerX = Number.POSITIVE_INFINITY;
    let pointerY = Number.POSITIVE_INFINITY;
    let pointerVisible = false;
    let reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    const buildGrid = () => {
      const previousScales = new Map(
        cells.map((cell) => [`${cell.x}:${cell.y}`, cell.scale]),
      );
      const columns = Math.ceil(width / cellSize);
      const rows = Math.ceil(height / cellSize);
      const offsetX = (width - (columns - 1) * cellSize) * 0.5;
      const offsetY = (height - (rows - 1) * cellSize) * 0.5;
      const nextCells: Cell[] = [];

      for (let row = 0; row < rows; row += 1) {
        for (let column = 0; column < columns; column += 1) {
          const x = offsetX + column * cellSize;
          const y = offsetY + row * cellSize;
          const key = `${x}:${y}`;
          const color = colors?.length
            ? {
                kind: "solid" as const,
                value:
                  colors[
                    Math.floor(cellNoise(column, row, 1) * colors.length)
                  ],
              }
            : COLORS[Math.floor(cellNoise(column, row, 1) * COLORS.length)];
          nextCells.push({
            x,
            y,
            shape: SHAPES[Math.floor(cellNoise(column, row, 0) * SHAPES.length)],
            color,
            peakScale:
              minPeakScale +
              cellNoise(column, row, 2) * (maxPeakScale - minPeakScale),
            rotation: cellNoise(column, row, 3) * TAU,
            rotationSpeed: (cellNoise(column, row, 4) - 0.5) * 0.22,
            scale: previousScales.get(key) ?? idleScale,
          });
        }
      }

      cells = nextCells;
    };

    const resize = () => {
      const bounds = container.getBoundingClientRect();
      width = Math.max(1, bounds.width);
      height = Math.max(1, bounds.height);
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
      buildGrid();
    };

    const localPointerPosition = (event: PointerEvent) => {
      const bounds = container.getBoundingClientRect();
      pointerX = event.clientX - bounds.left;
      pointerY = event.clientY - bounds.top;
      pointerVisible = true;
    };

    const handlePointerMove = (event: PointerEvent) => {
      localPointerPosition(event);
    };

    const handlePointerDown = (event: PointerEvent) => {
      localPointerPosition(event);
      bursts.push({ x: pointerX, y: pointerY, startedAt: performance.now() });
    };

    const handlePointerLeave = () => {
      pointerVisible = false;
    };

    const resetInteractionClock = () => {
      lastTime = performance.now();
      pointerVisible = false;
      bursts = [];
    };

    const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    const handleMotionPreference = (event: MediaQueryListEvent) => {
      reduceMotion = event.matches;
    };

    const render = (now: number) => {
      animationFrame = 0;
      if (document.hidden) {
        lastTime = now;
        return;
      }

      const deltaSeconds = Math.min((now - lastTime) / 1000, 0.05);
      lastTime = now;
      const influenceRadius = (Math.min(width, height) * influenceRadiusVmin) / 100;
      const longestDistance = Math.hypot(width, height);

      context.globalAlpha = 1;
      if (backgroundColor === "transparent") {
        context.clearRect(0, 0, width, height);
      } else {
        context.fillStyle = backgroundColor;
        context.fillRect(0, 0, width, height);
      }
      context.globalAlpha = opacity;

      bursts = bursts.filter((burst) => {
        const age = (now - burst.startedAt) / 1000;
        return age * burstSpeed - burstThickness < longestDistance;
      });

      for (const cell of cells) {
        let influence = 0;

        if (pointerVisible) {
          const pointerDistance = Math.hypot(cell.x - pointerX, cell.y - pointerY);
          const pointerInfluence = 1 - smoothstep(0, influenceRadius, pointerDistance);
          influence = Math.max(influence, pointerInfluence);
        }

        if (!reduceMotion) {
          for (const burst of bursts) {
            const age = (now - burst.startedAt) / 1000;
            const radius = age * burstSpeed;
            const distance = Math.hypot(cell.x - burst.x, cell.y - burst.y);
            const ringDistance = Math.abs(distance - radius);
            const ringInfluence = 1 - smoothstep(0, burstThickness, ringDistance);
            influence = Math.max(influence, ringInfluence);
          }
        }

        const targetScale = idleScale + (cell.peakScale - idleScale) * influence;
        const timeConstant = targetScale > cell.scale ? attackTime : releaseTime;
        const interpolation = reduceMotion
          ? 1
          : 1 - Math.exp(-deltaSeconds / Math.max(0.001, timeConstant));
        cell.scale += (targetScale - cell.scale) * interpolation;

        const activeScale = Math.max(
          0,
          (cell.scale - idleScale) / (cell.peakScale - idleScale),
        );
        if (!reduceMotion) {
          cell.rotation =
            (cell.rotation + deltaSeconds * cell.rotationSpeed * activeScale) % TAU;
        }
        drawCell(context, cell, cellSize, cell.rotation);
      }

      context.globalAlpha = 1;
      animationFrame = window.requestAnimationFrame(render);
    };

    const handleVisibilityChange = () => {
      resetInteractionClock();
      if (document.hidden) {
        window.cancelAnimationFrame(animationFrame);
        animationFrame = 0;
      } else if (!animationFrame) {
        animationFrame = window.requestAnimationFrame(render);
      }
    };

    const resizeObserver = new ResizeObserver(resize);
    resizeObserver.observe(container);
    container.addEventListener("pointermove", handlePointerMove, { passive: true });
    container.addEventListener("pointerdown", handlePointerDown, { passive: true });
    container.addEventListener("pointerleave", handlePointerLeave);
    motionQuery.addEventListener("change", handleMotionPreference);
    document.addEventListener("visibilitychange", handleVisibilityChange);
    resize();
    if (!document.hidden) {
      animationFrame = window.requestAnimationFrame(render);
    }

    return () => {
      window.cancelAnimationFrame(animationFrame);
      resizeObserver.disconnect();
      container.removeEventListener("pointermove", handlePointerMove);
      container.removeEventListener("pointerdown", handlePointerDown);
      container.removeEventListener("pointerleave", handlePointerLeave);
      motionQuery.removeEventListener("change", handleMotionPreference);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [
    attackTime,
    backgroundColor,
    burstSpeed,
    burstThickness,
    cellSize,
    colors,
    idleScale,
    influenceRadiusVmin,
    maxPeakScale,
    minPeakScale,
    opacity,
    releaseTime,
  ]);

  return (
    <div
      ref={containerRef}
      className={className}
      style={{
        position: "relative",
        width: "100%",
        height: "100%",
        overflow: "hidden",
        background: backgroundColor,
        touchAction: "pan-y",
        ...style,
      }}
      aria-hidden="true"
    >
      <canvas
        ref={canvasRef}
        style={{ display: "block", width: "100%", height: "100%" }}
      />
    </div>
  );
}
