"use client";

import { useState, type ReactNode } from "react";
import { AnimatePresence, motion } from "motion/react";
import { ChevronDown, type LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

export type AgentToolCallState =
  | "running"
  | "completed"
  | "review"
  | "failed"
  | "cancelled"
  | "neutral";

/**
 * One shared connector segment for chronological agent rows.
 *
 * The one-pixel rail begins half a pixel before 1.25rem, so its visual centre
 * is exactly 1.25rem: the midpoint of the shared `size-10` semantic node at
 * every responsive root-font scale. `gap` extends the segment through the
 * parent's vertical spacing while `node` terminates it behind the node
 * centre. Adjacent rows overlap in the gap, which keeps the timeline
 * continuous even when narrative rows sit between tool calls.
 */
export function AgentTimelineRail({
  start = "node",
  end = "node",
  spacing = "specialist",
  className,
}: {
  start?: "node" | "gap";
  end?: "node" | "gap";
  spacing?: "specialist" | "quick";
  className?: string;
}) {
  return (
    <span
      aria-hidden="true"
      data-agent-timeline-rail
      data-agent-timeline-rail-start={start}
      data-agent-timeline-rail-end={end}
      className={cn(
        "pointer-events-none absolute left-[calc(1.25rem-0.5px)] z-0 w-px",
        start === "node"
          ? "top-5"
          : spacing === "quick"
            ? "-top-5"
            : "-top-4",
        end === "node"
          ? "bottom-[calc(100%-1.25rem)]"
          : spacing === "quick"
            ? "-bottom-5"
            : "-bottom-4",
        className,
      )}
    />
  );
}

/**
 * Shared visual shell for observable tool calls in Quick Answer and specialist
 * workspaces. It owns presentation only; callers retain the authoritative
 * event title and decide how lifecycle frames are correlated.
 */
export function AgentToolCallCard({
  icon: Icon,
  title,
  status,
  state,
  meta,
  details,
  defaultOpen = false,
  className,
}: {
  icon: LucideIcon;
  title: string;
  status: string;
  state: AgentToolCallState;
  meta?: ReactNode;
  details?: ReactNode;
  defaultOpen?: boolean;
  className?: string;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const hasDetails = details !== undefined && details !== null;

  return (
    <div
      data-agent-tool-call
      data-agent-tool-state={state}
      className={cn("relative flex min-w-0 max-w-full gap-3", className)}
    >
      <span
        data-agent-tool-node
        className={cn(
          "relative z-10 grid size-10 shrink-0 place-items-center rounded-full border",
          state === "failed"
            ? "border-destructive/30 bg-destructive/10 text-destructive"
            : state === "review"
              ? "border-sky-500/25 bg-sky-500/10 text-sky-700 dark:text-sky-300"
            : state === "cancelled" || state === "neutral"
              ? "border-dashed border-border bg-secondary/35 text-muted-foreground"
              : "border-moss/30 bg-accent/50 text-moss",
        )}
        aria-hidden="true"
      >
        <Icon className="relative z-10 size-4" />
        {state === "running" ? (
          <motion.span
            className="absolute inset-1 rounded-full border border-moss/25"
            animate={{ opacity: [0.25, 0.8, 0.25], scale: [0.86, 1, 0.86] }}
            transition={{ duration: 1.8, repeat: Infinity, ease: "easeInOut" }}
          />
        ) : null}
      </span>

      <div className="min-w-0 flex-1 pt-1">
        <button
          type="button"
          onClick={() => hasDetails && setOpen((current) => !current)}
          aria-expanded={hasDetails ? open : undefined}
          disabled={!hasDetails}
          className={cn(
            "inline-flex min-h-8 max-w-full items-center gap-2 rounded-full border border-border bg-secondary/60 py-1.5 pl-3 pr-2.5 text-left transition-colors",
            hasDetails
              ? "cursor-pointer hover:border-moss/40 hover:bg-secondary"
              : "cursor-default",
          )}
        >
          <span className="truncate text-[0.8125rem] font-medium text-foreground/85">
            {title}
          </span>
          {meta ? (
            <span className="shrink-0 font-mono text-[0.59375rem] text-muted-foreground">
              {meta}
            </span>
          ) : null}
          <span
            className={cn(
              "shrink-0 rounded-full px-1.5 py-0.5 font-mono text-[0.59375rem] uppercase tracking-[0.1em]",
              state === "failed"
                ? "bg-destructive/10 text-destructive"
                : state === "review"
                  ? "bg-sky-500/10 text-sky-700 dark:text-sky-300"
                : state === "cancelled" || state === "neutral"
                  ? "bg-secondary text-muted-foreground"
                  : "bg-accent text-moss",
            )}
          >
            {status}
          </span>
          {hasDetails ? (
            <ChevronDown
              className={cn(
                "size-3.5 shrink-0 text-muted-foreground/60 transition-transform",
                open && "rotate-180",
              )}
            />
          ) : null}
        </button>

        {state === "running" ? (
          <div
            data-agent-tool-progress
            className="relative mt-2 h-px max-w-[24rem] overflow-hidden bg-border"
          >
            <motion.span
              aria-hidden="true"
              className="absolute inset-y-0 w-1/3 bg-gradient-to-r from-transparent via-moss to-transparent"
              animate={{ x: ["-120%", "360%"] }}
              transition={{ duration: 1.7, repeat: Infinity, ease: "easeInOut" }}
            />
          </div>
        ) : null}

        <AnimatePresence initial={false}>
          {open && hasDetails ? (
            <motion.div
              initial={{ opacity: 0, height: 0, y: -4 }}
              animate={{ opacity: 1, height: "auto", y: 0 }}
              exit={{ opacity: 0, height: 0, y: -4 }}
              transition={{ duration: 0.18, ease: "easeOut" }}
              className="overflow-hidden"
            >
              <div className="mt-2 max-w-[46rem] overflow-hidden rounded-2xl border border-border bg-card px-4 py-3 shadow-sm">
                {details}
              </div>
            </motion.div>
          ) : null}
        </AnimatePresence>
      </div>
    </div>
  );
}
