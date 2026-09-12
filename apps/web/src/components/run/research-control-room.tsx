"use client";

import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  Check,
  Clock3,
  Gauge,
  Pause,
  RotateCcw,
  Rows3,
} from "lucide-react";

import { describeEvent, STAGE_META, type StageId } from "@/components/run/stage-meta";
import { checkpointEstimate, controlStageStatus } from "@/components/run/control-room-presentation";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import { formatNumber, formatTime } from "@/lib/format";
import type { RunDetail, RunEvent } from "@/lib/types";
import { userFacingStoredErrorMessage } from "@/lib/user-facing-error";
import { cn } from "@/lib/utils";

function duration(seconds: number | null): string {
  if (seconds === null) return "Not started";
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

function eventLine(event: RunEvent): string {
  const described = describeEvent(event);
  if (described) return described;
  if (event.event === "run_control_requested") {
    return event.payload.signal === "cancel" ? "Cancellation requested" : "Pause requested";
  }
  const labels: Record<string, string> = {
    run_control_cleared: "Resume requested",
    run_retry_requested: "Retry requested",
    screening_started: "Screening workload prepared",
    screening_progress: "Screening decisions checkpointed",
    screening_paused: "Screening paused safely",
    snowball_screening_started: "Citation candidates ready for screening",
    snowball_screening_progress: "Citation screening checkpointed",
    acquisition_progress: "Full-text retrieval checkpointed",
    fulltext_screening_started: "Full-text screening prepared",
    fulltext_screening_progress: "Full-text decision checkpointed",
    fulltext_screening_paused: "Full-text screening paused safely",
    provider_unavailable: "Model routes unavailable; run paused safely",
    extraction_started: "Evidence extraction started",
    extraction_progress: "Evidence row extracted",
    extraction_cell_reviewed: "Extraction cell reviewed",
    extraction_schema_updated: "Extraction contract updated",
    living_refresh_started: "Living review refresh started",
    living_settings_updated: "Living review monitor updated",
  };
  return labels[event.event] ?? event.event.replaceAll("_", " ");
}

export default function ResearchControlRoom({ run }: { run: RunDetail }) {
  const moving = ["pending", "running"].includes(run.status);
  const { data, isLoading } = useQuery({
    queryKey: ["control-room", run.id, run.status],
    queryFn: () => api.controlRoom(run.id),
    refetchInterval: moving ? 2_000 : 15_000,
  });

  if (isLoading || !data) {
    return (
      <div className="grid h-full gap-4 overflow-y-auto p-5 lg:grid-cols-4">
        {Array.from({ length: 8 }).map((_, index) => (
          <Skeleton key={index} className="h-32 rounded-2xl" />
        ))}
      </div>
    );
  }

  const running = run.status === "running";
  const snapshotCurrent = data.status === run.status;
  const current = snapshotCurrent && !["cancelled", "completed", "failed"].includes(run.status)
    ? data.stages.find((stage) => stage.id === data.current_stage)
    : undefined;
  const completedStages = data.stages.filter((stage) => stage.status === "completed").length;
  const estimate = running && snapshotCurrent ? checkpointEstimate(data) : null;
  const stateLabel = run.status === "cancelled" ? "Cancelled"
    : run.status === "failed" ? "Stopped"
      : run.status === "paused" ? "Paused"
        : run.status === "awaiting_protocol_approval" ? "Awaiting approval"
          : run.status === "completed" ? "Complete"
            : "Queued";
  const etaValue = running
    ? estimate ? `~${duration(estimate.etaSeconds)}` : "Estimating…"
    : stateLabel;
  const elapsed = Math.max(0, Math.floor((
    (run.finished_at ? Date.parse(run.finished_at) : Date.now()) - Date.parse(run.created_at)
  ) / 1_000));
  const etaDetail = running
    ? estimate
      ? `Current stage only · based on ${formatNumber(estimate.sampleUnits)} recent records`
      : "Waiting for enough recent progress in this stage."
    : `${Number.isFinite(elapsed) ? duration(elapsed) : "—"} wall-clock elapsed, including pauses`;
  const heading = run.status === "cancelled" ? "Review cancelled"
    : run.status === "failed" ? "Review interrupted"
      : run.status === "completed" ? "Research record complete"
        : current?.label ?? (run.status === "paused" ? "Review paused" : "Preparing review");
  const headingDetail = run.status === "cancelled"
    ? "Completed work remains saved. This cancelled run will not continue."
    : run.status === "failed"
      ? "Completed work remains preserved. Restart the review to continue."
      : run.status === "paused"
        ? "The current checkpoint is preserved. Resume when you are ready."
        : run.status === "awaiting_protocol_approval"
          ? "Review and approve the protocol before research continues."
          : current?.description ?? "Every retrieval, screening and synthesis decision is preserved below.";

  return (
    <div className="h-full overflow-y-auto px-5 pb-8 pt-5">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Metric
          icon={Gauge}
          label="Pipeline"
          value={`${Math.round(data.overall_progress * 100)}%`}
          detail={`${completedStages} of ${data.stages.length} stages complete`}
        />
        <Metric
          icon={Rows3}
          label="Decisions"
          value={formatNumber(data.decisions.total)}
          detail={`${formatNumber(data.decisions.pending)} records not yet screened`}
        />
        <Metric
          icon={Clock3}
          label={running ? "Current stage ETA" : "Run state"}
          value={etaValue}
          detail={etaDetail}
        />
        <Metric
          icon={RotateCcw}
          label="Recent throughput"
          value={running ? estimate ? `${estimate.recordsPerMinute.toFixed(1)}/min` : "Estimating…" : "—"}
          detail={
            !running ? stateLabel
              : !snapshotCurrent ? "Refreshing the current run state…"
                : data.worker.attempt > 1
              ? `Worker attempt ${data.worker.attempt} of ${data.worker.max_attempts}`
              : `${data.worker.status} worker`
          }
          warning={running && snapshotCurrent && data.worker.attempt > 1}
        />
      </div>

      <section className="mt-4 overflow-hidden rounded-3xl border border-border bg-card">
        <div className="grid gap-5 border-b border-border/70 px-5 py-5 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-end">
          <div>
            <p className="font-mono text-[0.625rem] uppercase tracking-[0.22em] text-moss">
              {running ? "Working now" : run.status === "completed" ? "Completed review" : stateLabel}
            </p>
            <h2 className="mt-1 font-display text-3xl text-foreground">
              {heading}
            </h2>
            <p className="mt-1 max-w-2xl text-[0.8125rem] leading-relaxed text-muted-foreground">
              {headingDetail}
            </p>
          </div>
          {current && (
            <div className="min-w-52">
              <div className="mb-2 flex items-center justify-between font-mono text-[0.625rem] uppercase tracking-[0.16em] text-muted-foreground">
                <span>
                  {formatNumber(current.completed_units)} /{" "}
                  {formatNumber(current.total_units)}
                </span>
                <span>{Math.round(current.progress * 100)}%</span>
              </div>
              <Progress value={current.progress * 100} className="h-1.5" />
            </div>
          )}
        </div>

        <div className="grid border-b border-border/70 sm:grid-cols-2 xl:grid-cols-4">
          <CompactMetric
            label="Identified"
            value={data.records.identified}
          />
          <CompactMetric
            label="Citation candidates"
            value={data.records.snowball_candidates}
          />
          <CompactMetric
            label="Full texts"
            value={data.records.full_texts_retrieved}
          />
          <CompactMetric
            label="Extracted studies"
            value={data.records.extractions_completed}
          />
        </div>

        <div className="grid xl:grid-cols-[minmax(0,1.45fr)_minmax(320px,0.55fr)]">
          <div className="border-b border-border/70 p-5 xl:border-b-0 xl:border-r">
            <div className="mb-4 flex items-center justify-between">
              <p className="font-mono text-[0.625rem] uppercase tracking-[0.2em] text-muted-foreground">
                Research pipeline
              </p>
              <div className="flex items-center gap-3 text-[0.6875rem] text-muted-foreground">
                <span className="inline-flex items-center gap-1.5">
                  <span className="size-1.5 rounded-full bg-moss-surface" /> active
                </span>
                <span className="inline-flex items-center gap-1.5">
                  <span className="size-1.5 rounded-full bg-border" /> waiting
                </span>
              </div>
            </div>
            <div className="grid gap-2 md:grid-cols-2">
              {data.stages.map((stage, index) => {
                const meta = STAGE_META[stage.id as StageId];
                const Icon = meta?.icon ?? Rows3;
                const stageStatus = controlStageStatus(stage, run.status);
                // The API duration spans replayed stage events across pauses.
                // Show completion state, not an unsupported active-time claim.
                const stageTime = stageStatus === "completed" ? "Done"
                  : stageStatus === "active" ? "Working"
                    : stageStatus === "paused" ? "Paused"
                      : stageStatus === "failed" ? "Failed"
                        : stageStatus === "stopped" ? "Stopped" : "Waiting";
                const stageProgress = stageStatus === "stopped"
                  ? Math.min(1, Math.max(0, stage.completed_units / Math.max(1, stage.total_units)))
                  : stage.progress;
                const stagePercent =
                  stageStatus === "waiting"
                    ? "—"
                    : `${Math.round(stageProgress * 100)}%`;
                return (
                  <div
                    key={stage.id}
                    className={cn(
                      "relative overflow-hidden rounded-2xl border px-4 py-3.5 transition-colors",
                      stageStatus === "active"
                        ? "border-moss/40 bg-accent/55"
                        : stageStatus === "failed"
                          ? "border-destructive/35 bg-destructive/5"
                        : stageStatus === "completed"
                          ? "border-border/60 bg-secondary/25"
                          : stageStatus === "paused"
                            ? "border-amber-300/60 bg-amber-50/50 dark:border-amber-300/25 dark:bg-amber-300/10"
                            : "border-border/60 bg-background",
                    )}
                  >
                    <div className="flex items-start gap-3">
                      <span
                        className={cn(
                          "grid size-8 shrink-0 place-items-center rounded-full",
                          stageStatus === "active"
                            ? "bg-primary text-primary-foreground"
                            : stageStatus === "failed"
                              ? "bg-destructive text-destructive-foreground"
                            : stageStatus === "completed"
                              ? "bg-accent text-moss"
                              : "bg-secondary text-muted-foreground",
                        )}
                      >
                        {stageStatus === "completed" ? (
                          <Check className="size-3.5" />
                        ) : stageStatus === "failed" ? (
                          <AlertTriangle className="size-3.5" />
                        ) : stageStatus === "paused" || stageStatus === "stopped" ? (
                          <Pause className="size-3.5" />
                        ) : (
                          <Icon className="size-3.5" />
                        )}
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center justify-between gap-2">
                          <p className="truncate text-[0.8125rem] font-medium text-foreground">
                            {index + 1}. {stage.label}
                          </p>
                          <span className="shrink-0 font-mono text-[0.59375rem] uppercase tracking-[0.14em] text-muted-foreground">
                            {stageTime}
                          </span>
                        </div>
                        <p className="mt-0.5 line-clamp-1 text-[0.6875rem] text-muted-foreground">
                          {stage.description}
                        </p>
                        <div className="mt-2 flex items-center gap-2">
                          <Progress value={stageProgress * 100} className="h-1 flex-1" />
                          <span className="w-7 text-right font-mono text-[0.59375rem] text-muted-foreground">
                            {stagePercent}
                          </span>
                        </div>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          <aside className="p-5">
            <p className="font-mono text-[0.625rem] uppercase tracking-[0.2em] text-muted-foreground">
              Live decision mix
            </p>
            <div className="mt-4 flex h-2 overflow-hidden rounded-full bg-secondary">
              {data.decisions.total > 0 && (
                <>
                  <span
                    className="bg-moss-surface"
                    style={{
                      width: `${(data.decisions.include / data.decisions.total) * 100}%`,
                    }}
                  />
                  <span
                    className="bg-amber-300"
                    style={{
                      width: `${(data.decisions.unsure / data.decisions.total) * 100}%`,
                    }}
                  />
                  <span
                    className="bg-pine/25"
                    style={{
                      width: `${(data.decisions.exclude / data.decisions.total) * 100}%`,
                    }}
                  />
                </>
              )}
            </div>
            <div className="mt-3 grid grid-cols-3 gap-2">
              <Decision label="Include" value={data.decisions.include} tone="text-moss" />
              <Decision label="Unsure" value={data.decisions.unsure} tone="text-amber-700" />
              <Decision label="Exclude" value={data.decisions.exclude} tone="text-foreground/60" />
            </div>

            <p className="mb-3 mt-7 font-mono text-[0.625rem] uppercase tracking-[0.2em] text-muted-foreground">
              Latest checkpoints
            </p>
            <div className="relative space-y-0">
              {data.activity.slice(0, 8).map((event, index) => (
                <div key={event.id} className="relative flex gap-3 pb-4">
                  {index < Math.min(data.activity.length, 8) - 1 && (
                    <span className="absolute left-[0.21875rem] top-3 h-full w-px bg-border" />
                  )}
                  <span
                    className={cn(
                      "relative mt-1.5 size-2 shrink-0 rounded-full ring-4 ring-card",
                      index === 0 && moving ? "bg-moss-surface" : "bg-border",
                    )}
                  />
                  <div className="min-w-0">
                    <p className="text-[0.75rem] leading-snug text-foreground/85">
                      {eventLine(event)}
                    </p>
                    <p className="mt-1 font-mono text-[0.5625rem] uppercase tracking-[0.14em] text-muted-foreground">
                      {formatTime(event.created_at)} · {event.stage.replaceAll("_", " ")}
                    </p>
                  </div>
                </div>
              ))}
            </div>

            {running && snapshotCurrent && data.worker.last_error && data.worker.status !== "running" && (
              <div className="mt-2 flex gap-2 rounded-xl border border-amber-300/60 bg-amber-50/60 p-3 dark:border-amber-300/25 dark:bg-amber-300/10">
                <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-amber-700" />
                <p className="line-clamp-3 text-[0.6875rem] leading-relaxed text-amber-900">
                  {userFacingStoredErrorMessage(
                    data.worker.last_error,
                    "This background task needs attention. Please try it again.",
                  )}
                </p>
              </div>
            )}
          </aside>
        </div>
      </section>
    </div>
  );
}

function Metric({
  icon: Icon,
  label,
  value,
  detail,
  warning = false,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  value: string;
  detail: string;
  warning?: boolean;
}) {
  return (
    <div className="rounded-2xl border border-border bg-card px-4 py-4">
      <div className="flex items-center justify-between">
        <p className="font-mono text-[0.59375rem] uppercase tracking-[0.18em] text-muted-foreground">
          {label}
        </p>
        <Icon className={cn("size-3.5", warning ? "text-amber-700" : "text-moss")} />
      </div>
      <p className="mt-3 font-display text-3xl text-foreground">{value}</p>
      <p className="mt-1 text-[0.6875rem] text-muted-foreground">{detail}</p>
    </div>
  );
}

function CompactMetric({ label, value }: { label: string; value: number }) {
  return (
    <div className="border-b border-border/70 px-5 py-4 last:border-b-0 sm:border-b-0 sm:border-r sm:last:border-r-0">
      <p className="font-display text-2xl text-foreground">{formatNumber(value)}</p>
      <p className="mt-0.5 text-[0.6875rem] text-muted-foreground">{label}</p>
    </div>
  );
}

function Decision({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: string;
}) {
  return (
    <div className="rounded-xl bg-secondary/45 px-2.5 py-2">
      <p className={cn("font-display text-xl", tone)}>{formatNumber(value)}</p>
      <p className="text-[0.625rem] text-muted-foreground">{label}</p>
    </div>
  );
}
