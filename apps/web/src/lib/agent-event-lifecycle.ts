import type { SpecialistAgentEvent } from "@/lib/types";

export type StableAgentEvent = SpecialistAgentEvent & {
  frames: SpecialistAgentEvent[];
};

type ToolPresentationState =
  | "running"
  | "completed"
  | "review"
  | "failed"
  | "cancelled"
  | "neutral";

function isPlanEvent(event: SpecialistAgentEvent) {
  return event.event === "plan.created" || event.event === "plan.updated";
}

function isGroupedLifecycleEvent(event: SpecialistAgentEvent) {
  return event.event.startsWith("tool.") || event.event.startsWith("checkpoint.");
}

function closesGroupedLifecycle(event: SpecialistAgentEvent) {
  if (event.lifecycle === "completed" || event.lifecycle === "failed") return true;
  return event.event === "tool.completed" ||
    event.event === "tool.failed" ||
    event.event === "checkpoint.completed" ||
    event.event === "checkpoint.failed";
}

/** Group immutable lifecycle frames while retaining their first visible title. */
export function stabilizeAgentEventLedger(
  events: SpecialistAgentEvent[],
): StableAgentEvent[] {
  const seen = new Set<number>();
  const timeline: StableAgentEvent[] = [];
  const planIndexes = new Map<string, number>();
  const lifecycleIndexes = new Map<string, number>();

  for (const event of events) {
    if (seen.has(event.id)) continue;
    seen.add(event.id);

    if (isPlanEvent(event)) {
      const planKey = event.turn_id?.trim() || "__legacy_plan__";
      const existingIndex = planIndexes.get(planKey);
      if (existingIndex !== undefined) {
        const existing = timeline[existingIndex];
        timeline[existingIndex] = { ...existing, frames: [...existing.frames, event] };
      } else {
        planIndexes.set(planKey, timeline.length);
        timeline.push({ ...event, frames: [event] });
      }
      continue;
    }

    if (isGroupedLifecycleEvent(event) && event.call_id) {
      const existingIndex = lifecycleIndexes.get(event.call_id);
      if (existingIndex !== undefined) {
        const existing = timeline[existingIndex];
        timeline[existingIndex] = { ...existing, frames: [...existing.frames, event] };
        if (closesGroupedLifecycle(event)) lifecycleIndexes.delete(event.call_id);
        continue;
      }
      if (!closesGroupedLifecycle(event)) {
        lifecycleIndexes.set(event.call_id, timeline.length);
      }
    }
    timeline.push({ ...event, frames: [event] });
  }

  return timeline;
}

export function terminalAgentEvent(events: SpecialistAgentEvent[]) {
  return [...events].reverse().find(
    (event) =>
      event.event === "turn.completed" ||
      event.event === "turn.cancelled" ||
      event.event === "turn.failed",
  );
}

/** Verification misses that the agent can revise without user intervention. */
export function isRecoverableReview(event: SpecialistAgentEvent) {
  return event.event === "checkpoint.failed" ||
    (event.event === "tool.failed" && event.tool === "manuscript.compile_candidate");
}

/** Terminal ledger frames always win over stale local loading state. */
export function shouldShowAgentThinking(
  events: SpecialistAgentEvent[],
  requestedRunning: boolean,
  activeToolCall: boolean,
) {
  return requestedRunning && !terminalAgentEvent(events) && !activeToolCall;
}

/** A terminal turn can never leave an earlier lifecycle card animating. */
export function resolveTerminalToolState(
  liveState: ToolPresentationState,
  terminalEvent: SpecialistAgentEvent | undefined,
): ToolPresentationState {
  if (liveState !== "running" || !terminalEvent) return liveState;
  if (terminalEvent.event === "turn.failed") return "failed";
  if (terminalEvent.event === "turn.cancelled") return "cancelled";
  return "completed";
}
