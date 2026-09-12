import type { RunStatus } from "@/lib/types";

/** Display metadata per run status — one source of truth for dots & badges. */
export const STATUS_META: Record<
  RunStatus,
  { label: string; dot: string; badge: string; live: boolean }
> = {
  pending: {
    label: "Queued",
    dot: "bg-moss-soft",
    badge: "bg-secondary text-secondary-foreground",
    live: true,
  },
  running: {
    label: "Running",
    dot: "bg-moss-soft",
    badge: "bg-accent text-accent-foreground",
    live: true,
  },
  awaiting_protocol_approval: {
    label: "Awaiting approval",
    dot: "bg-amber-500",
    badge: "bg-amber-100 text-amber-900",
    live: false,
  },
  paused: {
    label: "Paused",
    dot: "bg-stone-400",
    badge: "bg-secondary text-muted-foreground",
    live: false,
  },
  completed: {
    label: "Completed",
    dot: "bg-moss",
    badge: "bg-accent text-accent-foreground",
    live: false,
  },
  failed: {
    label: "Failed",
    dot: "bg-destructive",
    badge: "bg-destructive/10 text-destructive",
    live: false,
  },
  cancelled: {
    label: "Cancelled",
    dot: "bg-stone-300",
    badge: "bg-secondary text-muted-foreground",
    live: false,
  },
};

export function isTerminal(status: RunStatus): boolean {
  return ["completed", "failed", "cancelled"].includes(status);
}

export function isMoving(status: RunStatus): boolean {
  return status === "pending" || status === "running";
}
