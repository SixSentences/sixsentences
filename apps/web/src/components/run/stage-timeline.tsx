"use client";

import { AnimatePresence, motion } from "motion/react";
import { Check, ChevronDown } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import {
  activeStage,
  describeEvent,
  expectedStages,
  STAGE_META,
  type StageId,
} from "@/components/run/stage-meta";
import { formatDuration, formatTime } from "@/lib/format";
import { isMoving } from "@/lib/status";
import type { RunDetail, RunEvent } from "@/lib/types";
import { cn } from "@/lib/utils";

type StageTimelineProps = {
  run: RunDetail;
  events: RunEvent[];
};

export default function StageTimeline({ run, events }: StageTimelineProps) {
  const stages = useMemo(() => expectedStages(run.config), [run.config]);
  const byStage = useMemo(() => {
    const map = new Map<StageId, RunEvent[]>();
    for (const event of events) {
      const stage = event.stage as StageId;
      const list = map.get(stage) ?? [];
      list.push(event);
      map.set(stage, list);
    }
    return map;
  }, [events]);

  const moving = isMoving(run.status);
  const current = moving ? activeStage(events) : null;

  // collapsed by default once the run is done (the results tell the story)
  const [open, setOpen] = useState(true);
  useEffect(() => {
    if (!moving && run.prisma) setOpen(false);
  }, [moving, run.prisma]);

  // the elapsed clock ticks every second while the run is moving
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!moving) return;
    const interval = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(interval);
  }, [moving]);

  return (
    <div className="rounded-2xl border border-border bg-card">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className={cn(
          "flex w-full cursor-pointer items-center justify-between px-5 py-3 text-left",
          open && "border-b border-border/70",
        )}
      >
        <span className="font-mono text-[0.625rem] uppercase tracking-[0.22em] text-muted-foreground">
          Audit trail{moving ? " · Live" : ""}
        </span>
        <span className="flex items-center gap-3">
          <span className="font-mono text-[0.6875rem] tabular-nums text-muted-foreground">
            {formatDuration(run.created_at, run.finished_at)}
          </span>
          <ChevronDown
            className={cn(
              "size-4 text-muted-foreground/60 transition-transform",
              open && "rotate-180",
            )}
          />
        </span>
      </button>

      {open && (
      <div className="px-5 py-4">
        {stages.map((stageId, index) => {
          const meta = STAGE_META[stageId];
          const stageEvents = byStage.get(stageId) ?? [];
          const visited = stageEvents.length > 0;
          const isActive = moving && current === stageId;
          const isLast = index === stages.length - 1;
          const Icon = meta.icon;

          return (
            <div key={stageId} className="relative flex gap-4">
              {/* rail */}
              <div className="flex flex-col items-center">
                <div
                  className={cn(
                    "relative z-10 grid size-8 shrink-0 place-items-center rounded-full border transition-colors duration-500",
                    visited && !isActive
                      ? "border-moss/30 bg-accent text-moss"
                      : isActive
                        ? "border-moss bg-accent text-moss"
                        : "border-border bg-background text-muted-foreground/50",
                  )}
                >
                  {visited && !isActive && stageId !== "report" ? (
                    <Check className="size-3.5" />
                  ) : (
                    <Icon className="size-3.5" />
                  )}
                  {isActive && (
                    <span className="absolute inset-0 animate-ping rounded-full border border-moss/40" />
                  )}
                </div>
                {!isLast && (
                  <div
                    className={cn(
                      "w-px flex-1 transition-colors duration-500",
                      visited ? "bg-moss-surface/30" : "bg-border",
                    )}
                  />
                )}
              </div>

              {/* content */}
              <div className={cn("min-w-0 flex-1 pb-5", isLast && "pb-1")}>
                <div className="flex h-8 items-center gap-2.5">
                  <span
                    className={cn(
                      "text-[0.84375rem] font-medium",
                      visited || isActive ? "text-foreground" : "text-muted-foreground/60",
                    )}
                  >
                    {meta.label}
                  </span>
                  {isActive && (
                    <span className="shimmer-text font-mono text-[0.625rem] uppercase tracking-[0.18em]">
                      working
                    </span>
                  )}
                </div>

                {stageEvents.length > 0 && (
                  <ul className="mt-0.5 space-y-1">
                    <AnimatePresence initial={false}>
                      {stageEvents.map((event) => {
                        const line = describeEvent(event);
                        if (!line) return null;
                        return (
                          <motion.li
                            key={event.id}
                            initial={{ opacity: 0, y: 6, filter: "blur(3px)" }}
                            animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
                            transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
                            className="flex items-baseline gap-2.5"
                          >
                            <span className="shrink-0 font-mono text-[0.625rem] tabular-nums text-muted-foreground/60">
                              {formatTime(event.created_at)}
                            </span>
                            <span className="text-[0.8125rem] leading-relaxed text-muted-foreground">
                              {line}
                            </span>
                          </motion.li>
                        );
                      })}
                    </AnimatePresence>
                  </ul>
                )}
              </div>
            </div>
          );
        })}
      </div>
      )}
    </div>
  );
}
