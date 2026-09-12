"use client";

import {
  Children,
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { GripVertical } from "lucide-react";

import {
  clampWorkspaceSplitPercent,
  isWorkspaceSplitFeasible,
} from "@/lib/workspace-split-layout.mjs";
import { cn } from "@/lib/utils";

type ResizableWorkspaceSplitProps = {
  children: ReactNode;
  storageKey: string;
  label: string;
  defaultPercent?: number;
  primaryMinPx?: number;
  secondaryMinPx?: number;
  mobileSwitch?: ReactNode;
  className?: string;
};

const DIVIDER_WIDTH = 8;
const WORKSPACE_PRIMARY_MIN_PERCENT = 38;
const WORKSPACE_PRIMARY_MAX_PERCENT = 58;
const WORKSPACE_PRIMARY_MIN_PX = 400;
const WORKSPACE_SECONDARY_MIN_PX = 600;

/** Two-pane research workspace with a persistent, keyboard-accessible divider. */
export function ResizableWorkspaceSplit({
  children,
  storageKey,
  label,
  defaultPercent = 50,
  primaryMinPx = WORKSPACE_PRIMARY_MIN_PX,
  secondaryMinPx = WORKSPACE_SECONDARY_MIN_PX,
  mobileSwitch,
  className,
}: ResizableWorkspaceSplitProps) {
  const panes = useMemo(() => Children.toArray(children), [children]);
  const containerRef = useRef<HTMLDivElement>(null);
  const initialPercent = Math.min(
    WORKSPACE_PRIMARY_MAX_PERCENT,
    Math.max(WORKSPACE_PRIMARY_MIN_PERCENT, defaultPercent),
  );
  const preferredPercentRef = useRef(initialPercent);
  const [primaryPercent, setPrimaryPercent] = useState(() =>
    Math.min(
      WORKSPACE_PRIMARY_MAX_PERCENT,
      Math.max(WORKSPACE_PRIMARY_MIN_PERCENT, defaultPercent),
    ));
  const [containerWidth, setContainerWidth] = useState(0);
  const [dragging, setDragging] = useState(false);

  const clampPercent = useCallback(
    (candidate: number, measuredWidth?: number) => {
      const width = measuredWidth
        ?? containerRef.current?.getBoundingClientRect().width
        ?? 0;
      return clampWorkspaceSplitPercent(candidate, {
        containerWidth: width,
        primaryMinPx,
        secondaryMinPx,
        dividerPx: DIVIDER_WIDTH,
        minPercent: WORKSPACE_PRIMARY_MIN_PERCENT,
        maxPercent: WORKSPACE_PRIMARY_MAX_PERCENT,
      });
    },
    [primaryMinPx, secondaryMinPx],
  );

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const stored = Number.parseFloat(localStorage.getItem(storageKey) ?? "");
    preferredPercentRef.current = Number.isFinite(stored)
      ? stored
      : initialPercent;

    const renderPreferredSplit = () => {
      const measuredWidth = container.getBoundingClientRect().width;
      setContainerWidth(measuredWidth);
      setPrimaryPercent(
        clampPercent(preferredPercentRef.current, measuredWidth),
      );
    };
    renderPreferredSplit();

    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", renderPreferredSplit);
      return () => window.removeEventListener("resize", renderPreferredSplit);
    }
    const observer = new ResizeObserver(renderPreferredSplit);
    observer.observe(container);
    return () => observer.disconnect();
  }, [clampPercent, initialPercent, storageKey]);

  const commitPreferredSplit = useCallback(
    (value: number) => {
      const clamped = clampPercent(value);
      preferredPercentRef.current = clamped;
      setPrimaryPercent(clamped);
      localStorage.setItem(storageKey, clamped.toFixed(2));
    },
    [clampPercent, storageKey],
  );

  const splitLayout = isWorkspaceSplitFeasible({
    containerWidth,
    primaryMinPx,
    secondaryMinPx,
    dividerPx: DIVIDER_WIDTH,
    minPercent: WORKSPACE_PRIMARY_MIN_PERCENT,
    maxPercent: WORKSPACE_PRIMARY_MAX_PERCENT,
  });

  const startDragging = (event: ReactPointerEvent<HTMLButtonElement>) => {
    if (!splitLayout) return;
    event.preventDefault();
    setDragging(true);
    const move = (pointer: PointerEvent) => {
      const bounds = containerRef.current?.getBoundingClientRect();
      if (!bounds) return;
      commitPreferredSplit(((pointer.clientX - bounds.left) / bounds.width) * 100);
    };
    const finish = () => {
      setDragging(false);
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", finish);
      window.removeEventListener("pointercancel", finish);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", finish);
    window.addEventListener("pointercancel", finish);
  };

  const style = {
    "--workspace-primary-width": `${primaryPercent}%`,
  } as CSSProperties;

  const primaryMinimum = Math.round(clampPercent(0, containerWidth));
  const primaryMaximum = Math.round(clampPercent(100, containerWidth));

  return (
    <div
      ref={containerRef}
      data-workspace-layout={splitLayout ? "split" : "single"}
      className={cn(
        "group/workspace flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden",
        className,
      )}
    >
      {!splitLayout ? mobileSwitch : null}
      <div
        style={style}
        className={cn(
          "grid min-h-0 min-w-0 flex-1 grid-cols-1 overflow-hidden [&>*]:min-w-0",
          splitLayout
            && "grid-cols-[minmax(0,var(--workspace-primary-width))_8px_minmax(0,1fr)]",
          dragging && "cursor-col-resize select-none",
        )}
      >
        {panes[0]}
        {splitLayout ? (
          <button
            type="button"
            role="separator"
            aria-label={label}
            aria-orientation="vertical"
            aria-valuemin={primaryMinimum}
            aria-valuemax={primaryMaximum}
            aria-valuenow={Math.round(primaryPercent)}
            onPointerDown={startDragging}
            onDoubleClick={() => commitPreferredSplit(defaultPercent)}
            onKeyDown={(event) => {
              if (event.key === "ArrowLeft") {
                event.preventDefault();
                commitPreferredSplit(primaryPercent - (event.shiftKey ? 5 : 1));
              } else if (event.key === "ArrowRight") {
                event.preventDefault();
                commitPreferredSplit(primaryPercent + (event.shiftKey ? 5 : 1));
              } else if (event.key === "Home") {
                event.preventDefault();
                commitPreferredSplit(0);
              } else if (event.key === "End") {
                event.preventDefault();
                commitPreferredSplit(100);
              }
            }}
            className={cn(
              "group relative z-20 min-h-0 cursor-col-resize touch-none outline-none",
              "after:absolute after:inset-y-0 after:left-1/2 after:w-px after:-translate-x-1/2 after:bg-border",
              "focus-visible:after:w-0.5 focus-visible:after:bg-moss hover:after:w-0.5 hover:after:bg-moss/65",
              dragging && "after:w-0.5 after:bg-moss",
            )}
            title="Drag to resize. Double-click to reset."
          >
            <span
              className={cn(
                "absolute left-1/2 top-1/2 z-10 grid h-12 w-4 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full border border-border bg-card text-muted-foreground shadow-sm transition-colors",
                "group-hover:border-moss/40 group-hover:text-moss group-focus-visible:border-moss group-focus-visible:text-moss",
                dragging && "border-moss/50 text-moss",
              )}
            >
              <GripVertical className="size-3" />
            </span>
          </button>
        ) : null}
        {panes[1]}
      </div>
    </div>
  );
}
