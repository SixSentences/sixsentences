import type { ControlRoomStage, ResearchControlRoom, RunStatus } from "@/lib/types";

const PROGRESS_EVENTS: Record<string, string> = {
  screening_title_abstract: "screening_progress",
  snowball: "snowball_screening_progress",
  acquisition: "acquisition_progress",
  screening_full_text: "fulltext_screening_progress",
};

const RESTART_BOUNDARIES = new Set([
  "run_paused",
  "run_control_cleared",
  "run_retry_requested",
]);

/** Estimate only from a useful sample within one uninterrupted stage attempt. */
export function checkpointEstimate(data: ResearchControlRoom): {
  etaSeconds: number;
  recordsPerMinute: number;
  sampleUnits: number;
} | null {
  if (data.status !== "running" || !data.current_stage) return null;
  const stage = data.stages.find((item) => item.id === data.current_stage);
  const progressEvent = PROGRESS_EVENTS[data.current_stage];
  if (!stage || stage.status !== "active" || !progressEvent) return null;
  const events = [...data.activity].sort((left, right) => left.id - right.id);
  const boundary = events.reduce((latest, event) => (
    RESTART_BOUNDARIES.has(event.event)
      || (event.stage === stage.id && event.event.endsWith("_started"))
      ? Math.max(latest, event.id)
      : latest
  ), -1);
  let progress = events.filter((event) =>
    event.id > boundary && event.stage === stage.id && event.event === progressEvent,
  );
  if (stage.id === "snowball" && progress.length > 0) {
    const round = progress[progress.length - 1].payload.round;
    progress = progress.filter((event) => event.payload.round === round);
  }
  const points = progress.slice(-10).map((event) => ({
    completed: event.payload.completed,
    at: Date.parse(event.created_at),
  }));
  if (points.length < 3 || points.some((point) =>
    typeof point.completed !== "number"
      || !Number.isFinite(point.completed)
      || point.completed < 0
      || !Number.isFinite(point.at),
  )) return null;
  const samples = points as { completed: number; at: number }[];
  if (samples.some((point, index) => index > 0 && (
    point.completed < samples[index - 1].completed || point.at <= samples[index - 1].at
  ))) return null;
  const first = samples[0];
  const last = samples[samples.length - 1];
  const seconds = (last.at - first.at) / 1_000;
  const units = last.completed - first.completed;
  // One completed record is not a throughput sample. Never fall back to the
  // first-ever stage timestamp: it can include protocol waits and long pauses.
  if (units < 5 || seconds < 30) return null;
  const remaining = stage.total_units - stage.completed_units;
  if (remaining <= 0 || stage.completed_units < last.completed) return null;
  return {
    etaSeconds: Math.ceil(remaining * seconds / units),
    recordsPerMinute: units / seconds * 60,
    sampleUnits: units,
  };
}

/** A cached worker snapshot must not turn a stopped run into active work. */
export function controlStageStatus(
  stage: ControlRoomStage,
  status: RunStatus,
): ControlRoomStage["status"] | "stopped" {
  if (stage.status === "completed" || stage.status === "failed") return stage.status;
  if (status === "cancelled" || status === "failed" || status === "completed") {
    return stage.event_count > 0 || stage.status === "active" || stage.status === "paused"
      ? "stopped"
      : "waiting";
  }
  if (stage.status === "active" && (status === "paused" || status === "awaiting_protocol_approval")) {
    return "paused";
  }
  return stage.status;
}
