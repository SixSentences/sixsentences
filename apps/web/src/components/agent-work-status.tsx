"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  ArrowUpRight,
  Check,
  ChevronDown,
  Circle,
  ClipboardList,
  Database,
  FileSpreadsheet,
  FileText,
  Image as ImageIcon,
  ListChecks,
  MessagesSquare,
  Mic2,
  Paperclip,
  PencilLine,
  Square,
} from "lucide-react";

import SixMark from "@/components/brand/six-mark";
import { CodeAwareText } from "@/components/ai/code-aware-text";
import {
  AgentTimelineRail,
  AgentToolCallCard,
  type AgentToolCallState,
} from "@/components/agent/tool-call-card";
import ActivityOrb from "@/components/run/activity-orb";
import { useAuth } from "@/lib/auth";
import type {
  SpecialistAgentEvent,
  SpecialistArtifact,
} from "@/lib/types";
import {
  safeAgentArtifactHref,
  safeAgentDisplayText,
  safeAgentDisplayValue,
  safeAgentProgressText,
} from "@/lib/safe-agent-display";
import { userFacingStoredErrorMessage } from "@/lib/user-facing-error";
import {
  isRecoverableReview,
  resolveTerminalToolState,
  shouldShowAgentThinking,
  stabilizeAgentEventLedger,
  terminalAgentEvent,
  type StableAgentEvent,
} from "@/lib/agent-event-lifecycle";
import { cn } from "@/lib/utils";

type AgentWorkKind =
  | "dataset"
  | "interview"
  | "interview-study"
  | "manuscript"
  | "survey";

const ACTIVITY: Record<
  AgentWorkKind,
  {
    completedLabel: string;
    icon: typeof FileText;
  }
> = {
  dataset: {
    completedLabel: "Inspected the dataset",
    icon: Database,
  },
  interview: {
    completedLabel: "Read the interview",
    icon: Mic2,
  },
  "interview-study": {
    completedLabel: "Inspected the interview study",
    icon: MessagesSquare,
  },
  manuscript: {
    completedLabel: "Read the manuscript workspace",
    icon: FileText,
  },
  survey: {
    completedLabel: "Inspected the survey",
    icon: ClipboardList,
  },
};

export function AgentResponseAvatar({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "grid size-10 shrink-0 place-items-center rounded-full bg-muted ring-1 ring-inset ring-border",
        className,
      )}
      aria-label="SixSentences_"
    >
      <SixMark className="h-5 w-5 text-foreground" />
    </span>
  );
}

export function AgentFinalResponse({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      data-agent-final-message
      className={cn("flex min-w-0 max-w-full items-start gap-3", className)}
    >
      <AgentResponseAvatar />
      <div className="min-w-0 flex-1 overflow-hidden break-words pt-1 text-[0.875rem] leading-relaxed text-foreground">
        {typeof children === "string" ? <CodeAwareText text={children} /> : children}
      </div>
    </div>
  );
}

function artifactIcon(kind?: string) {
  if (kind === "table" || kind === "dataset") return FileSpreadsheet;
  if (kind === "visual") return ImageIcon;
  if (kind === "pdf" || kind === "manuscript") return FileText;
  return Paperclip;
}

/** Durable outputs stay directly below the final answer and open in one click. */
export function AgentArtifactLinks({
  artifacts,
  className,
}: {
  artifacts?: SpecialistArtifact[];
  className?: string;
}) {
  const visible = (artifacts ?? []).flatMap((artifact) => {
    const href = safeAgentArtifactHref(artifact.href);
    return href ? [{ ...artifact, href }] : [];
  });
  if (visible.length === 0) return null;
  return (
    <div
      data-agent-artifacts
      className={cn("ml-[3.25rem] mt-2 flex min-w-0 flex-wrap gap-2", className)}
      aria-label="Created outputs"
    >
      {visible.map((artifact) => {
        const Icon = artifactIcon(artifact.kind);
        const external = artifact.href.startsWith("https://");
        return (
          <a
            key={artifact.id}
            href={artifact.href}
            target={external ? "_blank" : undefined}
            rel={external ? "noreferrer" : undefined}
            className="group flex min-w-0 max-w-full items-center gap-2 rounded-xl border border-border bg-secondary/35 px-3 py-2 text-left transition-colors hover:border-moss/45 hover:bg-accent/45"
          >
            <span className="grid size-7 shrink-0 place-items-center rounded-lg bg-accent text-moss">
              <Icon className="size-3.5" />
            </span>
            <span className="min-w-0">
              <span className="block truncate text-[0.71875rem] font-medium text-foreground">
                {artifact.title || artifact.filename || "Open output"}
              </span>
              {artifact.filename && artifact.filename !== artifact.title ? (
                <span className="block truncate text-[0.625rem] text-muted-foreground">
                  {artifact.filename}
                </span>
              ) : null}
            </span>
            <ArrowUpRight className="size-3.5 shrink-0 text-muted-foreground transition-transform group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-moss" />
          </a>
        );
      })}
    </div>
  );
}

export function AgentLoadingOrb({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "relative grid size-11 shrink-0 place-items-center rounded-full bg-background",
        className,
      )}
      aria-hidden="true"
    >
      <ActivityOrb className="absolute left-1/2 top-1/2 size-14 -translate-x-1/2 -translate-y-1/2" />
    </span>
  );
}

/** A quiet turn clock that stays outside the agent and tool timeline. */
export function AgentTurnElapsed({ className }: { className?: string }) {
  const [seconds, setSeconds] = useState(0);

  useEffect(() => {
    const startedAt = Date.now();
    const update = () => setSeconds(Math.floor((Date.now() - startedAt) / 1_000));
    update();
    const interval = window.setInterval(update, 1_000);
    return () => window.clearInterval(interval);
  }, []);

  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;

  return (
    <div
      data-agent-turn-elapsed
      className={cn(
        "flex min-w-0 justify-end font-mono text-[0.5625rem] uppercase tracking-[0.14em] text-muted-foreground",
        className,
      )}
      aria-label={`Elapsed ${minutes} minutes ${remainingSeconds} seconds`}
    >
      <span>
        Elapsed {String(minutes).padStart(2, "0")}:{String(remainingSeconds).padStart(2, "0")}
      </span>
    </div>
  );
}

export function AgentContextReceipt({
  kind,
  className,
}: {
  kind: AgentWorkKind;
  className?: string;
}) {
  const activity = ACTIVITY[kind];
  const Icon = activity.icon;
  return (
    <div
      className={cn(
        "mb-2 inline-flex max-w-full items-center gap-2 rounded-full border border-border bg-secondary/55 px-3 py-1.5",
        className,
      )}
    >
      <Icon className="size-3.5 shrink-0 text-moss" />
      <span className="truncate text-[0.6875rem] font-medium text-foreground/85">
        {activity.completedLabel}
      </span>
      <span className="size-1.5 shrink-0 rounded-full bg-moss-surface" />
    </div>
  );
}

const INTERNAL_LIMIT_TEXT =
  /\b(?:iteration(?:s)?(?:[\s_-]+(?:\d+(?:\s*\/\s*\d+)?|limit))?|tool[\s_-]*(?:calls?|limit)|(?:per[\s_-]*turn[\s_-]*call|safe[\s_-]*work|failure|time)[\s_-]*limit|remaining[\s_-]*(?:budget|calls?|tools?))\b/i;
const EXPECTED_DUPLICATE_TEXT =
  /(?:skipped a duplicate agent action|identical .+ call already ran|duplicate agent action)/i;
const EXPECTED_EOF_TEXT =
  /(?:\bend of file\b|\beof\b|reached (?:the )?end of|beyond (?:the )?(?:end|linked source)|no more (?:source )?(?:content|text))/i;

function eventSearchText(event: SpecialistAgentEvent) {
  let output = "";
  try {
    output = JSON.stringify(event.output ?? "");
  } catch {
    output = "";
  }
  return [event.label, event.detail, event.message, ...(event.steps ?? []), event.tool, output]
    .filter(Boolean)
    .join("\n");
}

type ExpectedObservation = "duplicate" | "eof" | "budget" | null;

function expectedObservation(event: SpecialistAgentEvent): ExpectedObservation {
  const searchable = eventSearchText(event);
  if (EXPECTED_DUPLICATE_TEXT.test(searchable)) return "duplicate";
  if (EXPECTED_EOF_TEXT.test(searchable)) return "eof";
  if (
    event.event === "tool.failed" &&
    (event.tool?.endsWith(".agent_loop") || INTERNAL_LIMIT_TEXT.test(searchable))
  ) {
    return "budget";
  }
  return null;
}

function isFailureEvent(event: SpecialistAgentEvent) {
  return (
    event.lifecycle === "failed" ||
    event.event === "tool.failed" ||
    event.event === "checkpoint.failed" ||
    event.event === "turn.failed"
  );
}

function safeFailureEventText(value: unknown, fallback = "") {
  return userFacingStoredErrorMessage(value, fallback);
}

function eventTitle(event: SpecialistAgentEvent) {
  const observation = expectedObservation(event);
  if (observation === "duplicate") return "Reviewed the available results";
  if (observation === "eof") return "Finished reviewing the source";
  if (observation === "budget") return "Reviewed the current work";
  const preservesFailure = isFailureEvent(event);
  const safeLabel = preservesFailure
    ? safeFailureEventText(event.label)
    : safeAgentProgressText(event.label);
  if (safeLabel) return safeLabel;
  if (event.event === "plan.created") return "Prepared the work plan";
  if (event.event === "plan.updated") return "Updated the work plan";
  if (event.event === "context.loaded") return "Reviewed the workspace";
  if (event.event === "tool.started") return "Started the next step";
  if (event.event === "tool.progress") return "Working on the next step";
  if (event.event === "tool.completed") return "Completed the step";
  if (event.event === "tool.failed") return "Workspace action needs attention";
  if (event.event === "checkpoint.started") return "Reviewing the result against your request";
  if (event.event === "checkpoint.progress") return "Reviewing the result";
  if (event.event === "checkpoint.completed") return "Reviewed the result";
  if (event.event === "checkpoint.failed") return "Result review needs attention";
  if (event.event === "agent.update") return "Work update";
  if (event.event === "change.proposed") return "Prepared a change";
  if (event.event === "change.completed") return "Applied the change";
  if (event.event === "change.rejected") return "Rejected the change";
  if (event.event === "context.compacted") return "Reviewed the latest workspace state";
  if (event.event === "answer.completed") return "Prepared the response";
  if (event.event === "turn.cancelled") {
    const message = userFacingStoredErrorMessage(
      event.message,
      "",
    );
    return message || "Agent turn stopped";
  }
  if (event.event === "turn.failed") {
    const message = userFacingStoredErrorMessage(
      event.message,
      "",
    );
    return message || "Agent turn could not be completed";
  }
  return "Agent activity";
}

function eventProgressText(event: SpecialistAgentEvent, value: unknown) {
  if (isFailureEvent(event)) {
    return safeFailureEventText(value);
  }
  return safeAgentProgressText(value);
}

function eventState(
  event: SpecialistAgentEvent,
): "progress" | "completed" | "review" | "failed" | "cancelled" | "neutral" {
  if (expectedObservation(event)) return "neutral";
  // A recoverable verification miss is part of the agent's revision loop.
  // Reserve destructive red for actual tool/turn failures that need attention.
  if (isRecoverableReview(event)) return "review";
  if (
    event.lifecycle === "failed" ||
    event.event === "tool.failed" ||
    event.event === "turn.failed"
  ) {
    return "failed";
  }
  if (event.event === "turn.cancelled") return "cancelled";
  if (event.event === "change.rejected") return "neutral";
  if (
    event.lifecycle === "completed" ||
    event.event === "tool.completed" ||
    event.event === "checkpoint.completed" ||
    event.event === "agent.update" ||
    event.event === "change.proposed" ||
    event.event === "change.completed" ||
    event.event === "answer.completed" ||
    event.event === "context.compacted"
  ) {
    return "completed";
  }
  return "progress";
}

function eventStatusLabel(
  event: SpecialistAgentEvent,
  language: "de" | "en" = "en",
) {
  if (expectedObservation(event)) return language === "de" ? "Erledigt" : "Done";
  if (isRecoverableReview(event)) {
    return language === "de" ? "Prüfung" : "Review";
  }
  if (
    event.lifecycle === "failed" ||
    event.event === "tool.failed" ||
    event.event === "turn.failed"
  ) {
    return language === "de" ? "Prüfen" : "Check";
  }
  if (event.event === "turn.cancelled") return language === "de" ? "Gestoppt" : "Stopped";
  if (event.lifecycle === "started" || event.event === "tool.started" || event.event === "checkpoint.started") {
    return language === "de" ? "Gestartet" : "Started";
  }
  if (event.lifecycle === "progress" || event.event === "tool.progress" || event.event === "checkpoint.progress") {
    return language === "de" ? "Läuft" : "Update";
  }
  if (event.event === "change.proposed") return language === "de" ? "Bereit" : "Ready";
  if (event.event === "change.rejected") {
    return language === "de" ? "Abgelehnt" : "Rejected";
  }
  return language === "de" ? "Erledigt" : "Done";
}

function eventKindLabel(event: SpecialistAgentEvent) {
  if (event.event === "plan.created" || event.event === "plan.updated") return "Plan";
  if (event.event === "context.loaded" || event.event === "context.compacted") {
    return "Context";
  }
  if (event.event.startsWith("checkpoint.")) return "Review";
  if (event.event === "change.rejected") return "Rejected";
  if (event.event.startsWith("change.")) return event.applied ? "Applied" : "Proposal";
  if (event.event === "agent.update") return "Update";
  if (event.event === "answer.completed") return "Answer";
  if (event.event === "turn.cancelled" || event.event === "turn.failed") return "Turn";
  return "Tool";
}

function eventDuration(event: AgentEventGroup) {
  const started = event.frames[0]?.created_at;
  const finished = event.frames.at(-1)?.created_at;
  if (!started || !finished || started === finished) return "";
  const duration = new Date(finished).getTime() - new Date(started).getTime();
  if (!Number.isFinite(duration) || duration < 1_000) return "";
  if (duration < 60_000) return `${Math.round(duration / 1_000)}s`;
  const minutes = Math.floor(duration / 60_000);
  const seconds = Math.round((duration % 60_000) / 1_000);
  return `${minutes}m ${seconds}s`;
}

function eventIcon(event: SpecialistAgentEvent) {
  const tool = event.tool?.toLowerCase() ?? "";
  if (event.event.startsWith("checkpoint.")) return Check;
  if (event.event.startsWith("change.")) return PencilLine;
  if (tool.startsWith("dataset.")) return Database;
  if (tool.startsWith("survey.")) return ClipboardList;
  if (tool.startsWith("interview-study.") || tool.startsWith("voice-study.")) {
    return MessagesSquare;
  }
  if (tool.startsWith("interview.")) return Mic2;
  if (tool.startsWith("manuscript.") || tool.startsWith("writer.")) return FileText;
  return ClipboardList;
}

function toolCallState(event: SpecialistAgentEvent): AgentToolCallState {
  const state = eventState(event);
  if (state === "progress") return "running";
  return state;
}

const HIDDEN_DETAIL_KEYS = new Set([
  "id",
  "question_id",
  "request",
  "workspace_state",
  "call_id",
  "effect",
  "iteration",
  "iterations",
  "max_iterations",
  "next_action",
  "remaining_tools",
  "tool_calls",
  "attempt",
  "attempts",
  "retry_count",
  "work_limit",
  "safety_limit",
  "remaining_budget",
]);

function humanizeKey(value: string) {
  return value
    .replaceAll("_", " ")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/^./, (character) => character.toUpperCase());
}

function humanizeOperation(value: string) {
  return value
    .replace(/^set_/, "Update ")
    .replace(/^add_/, "Add ")
    .replace(/^remove_/, "Remove ")
    .replace(/^delete_/, "Delete ")
    .replace(/^rename$/, "Rename")
    .replaceAll("_", " ")
    .replace(/^./, (character) => character.toUpperCase());
}

function readableScalar(value: unknown) {
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") return value.toLocaleString();
  if (typeof value === "string") return value.trim();
  return "";
}

function objectTitle(value: Record<string, unknown>, fallback: string) {
  for (const key of ["label", "title", "name", "question", "operation", "type"]) {
    const candidate = readableScalar(value[key]);
    if (!candidate) continue;
    return key === "operation" ? humanizeOperation(candidate) : candidate;
  }
  return fallback;
}

function ReadableValue({ value, depth = 0 }: { value: unknown; depth?: number }) {
  return (
    <ReadableValueContent
      value={safeAgentDisplayValue(value)}
      depth={depth}
    />
  );
}

function ReadableValueContent({ value, depth = 0 }: { value: unknown; depth?: number }) {
  if (value === undefined || value === null || value === "") return null;

  if (Array.isArray(value)) {
    if (value.length === 0) {
      return <p className="text-[0.71875rem] text-muted-foreground">None</p>;
    }
    const scalarValues = value.filter(
      (item) => item === null || typeof item !== "object",
    );
    if (scalarValues.length === value.length) {
      return (
        <div className="flex flex-wrap gap-1.5">
          {scalarValues.slice(0, 24).map((item, index) => (
            <span
              key={`${index}-${String(item)}`}
              className="max-w-full break-words rounded-full border border-border bg-secondary/45 px-2.5 py-1 text-[0.6875rem] text-foreground/80"
            >
              {String(item)}
            </span>
          ))}
          {value.length > 24 ? (
            <span className="px-1 py-1 text-[0.6875rem] text-muted-foreground">
              +{value.length - 24} more
            </span>
          ) : null}
        </div>
      );
    }
    return (
      <div className="space-y-2">
        {value.slice(0, 20).map((item, index) => {
          if (!item || typeof item !== "object" || Array.isArray(item)) {
            return (
              <p key={index} className="text-[0.71875rem] text-foreground/80">
                {readableScalar(item)}
              </p>
            );
          }
          const record = item as Record<string, unknown>;
          return (
            <div key={index} className="rounded-xl border border-border/80 bg-secondary/25 px-3 py-2.5">
              <p className="text-[0.75rem] font-medium text-foreground">
                {objectTitle(record, `Item ${index + 1}`)}
              </p>
              {depth < 2 ? (
                <div className="mt-1.5">
                  <ReadableValue value={record} depth={depth + 1} />
                </div>
              ) : null}
            </div>
          );
        })}
        {value.length > 20 ? (
          <p className="text-[0.6875rem] text-muted-foreground">
            {value.length - 20} additional items
          </p>
        ) : null}
      </div>
    );
  }

  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>).filter(
      ([key, entry]) => !HIDDEN_DETAIL_KEYS.has(key) && entry !== undefined && entry !== null,
    );
    if (entries.length === 0) return null;
    return (
      <dl className="space-y-2">
        {entries.slice(0, 24).map(([key, entry]) => {
          if (key === "operation" && typeof entry === "string") return null;
          const scalar = readableScalar(entry);
          return (
            <div key={key} className="grid gap-1 sm:grid-cols-[8.5rem_minmax(0,1fr)] sm:gap-3">
              <dt className="font-mono text-[0.53125rem] uppercase tracking-[0.14em] text-muted-foreground">
                {humanizeKey(key)}
              </dt>
              <dd className="min-w-0 break-words text-[0.71875rem] leading-relaxed text-foreground/85">
                {scalar ? scalar : depth < 3 ? <ReadableValue value={entry} depth={depth + 1} /> : null}
              </dd>
            </div>
          );
        })}
      </dl>
    );
  }

  return (
    <p className="whitespace-pre-wrap break-words text-[0.71875rem] leading-relaxed text-foreground/85">
      {readableScalar(value)}
    </p>
  );
}

function hasReadableValue(value: unknown) {
  const safeValue = safeAgentDisplayValue(value);
  value = safeValue;
  if (value === undefined || value === null || value === "") return false;
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === "object") {
    return Object.entries(value as Record<string, unknown>).some(
      ([key, entry]) => !HIDDEN_DETAIL_KEYS.has(key) && entry !== undefined && entry !== null,
    );
  }
  return true;
}

function AgentThinkingBubble({ label = "Thinking…" }: { label?: string }) {
  return (
    <span
      data-agent-thinking
      className="shimmer-text block min-w-0 break-words text-[0.8125rem] font-medium leading-snug text-foreground/85"
    >
      {label}
    </span>
  );
}

/** One aligned live-thinking row shared by Quick Answer and every specialist. */
export function AgentThinkingIndicator({
  label = "Thinking…",
  className,
}: {
  label?: string;
  className?: string;
}) {
  return (
    <div
      data-agent-thinking-row
      className={cn(
        "flex min-h-11 min-w-0 max-w-full items-center gap-2.5",
        className,
      )}
    >
      <AgentLoadingOrb className="size-10" />
      <AgentThinkingBubble label={label} />
    </div>
  );
}

/** Append an immutable SSE event without duplicating reconnect/replay frames. */
export function appendAgentEvent(
  current: SpecialistAgentEvent[],
  event: SpecialistAgentEvent,
) {
  const alreadyVisible = current.some((candidate) => candidate.id === event.id);
  return alreadyVisible ? current : [...current, event];
}

type AgentEventGroup = StableAgentEvent;

function isPlanEvent(event: SpecialistAgentEvent) {
  return event.event === "plan.created" || event.event === "plan.updated";
}

/**
 * Project the durable ledger into immutable chronological entries.
 *
 * Exact replay ids are deduplicated. Tool lifecycle frames with the same
 * server-owned call id share one visual card while the first (started) frame
 * remains the authoritative title and every later frame stays inspectable.
 * Plan revisions from the same durable turn follow the same append-only rule:
 * one stable card retains every plan frame instead of impersonating a new
 * parallel agent for each update.
 */
export function stabilizeAgentEvents(events: SpecialistAgentEvent[]): AgentEventGroup[] {
  return stabilizeAgentEventLedger(events);
}

/** Backward-compatible export for callers that used the former projection. */
export const coalesceAgentEvents = stabilizeAgentEvents;

/**
 * The runtime mirrors the final assistant prose into this event immediately
 * before persisting the actual final response. Keep that transport projection
 * out of the activity ledger so the answer is rendered exactly once.
 */
function isFinalReportProjection(event: SpecialistAgentEvent) {
  return event.event === "agent.update" && event.tool?.endsWith(".report_results") === true;
}

function isVisibleAgentEvent(event: SpecialistAgentEvent) {
  return (
    event.event !== "turn.started" &&
    event.event !== "turn.completed" &&
    event.event !== "answer.completed" &&
    !isFinalReportProjection(event)
  );
}

export interface AgentTimelineHandoff {
  key: string;
  turnId: string;
  events: SpecialistAgentEvent[];
}

function visibleEventIds(events: SpecialistAgentEvent[]) {
  return events.filter(isVisibleAgentEvent).map((event) => event.id);
}

/**
 * A live timeline is acknowledged only by one persisted assistant message from
 * the same durable turn containing every event row the user actually saw.
 */
export function agentTimelineIsPersisted(
  handoff: AgentTimelineHandoff,
  persistedTimelines: SpecialistAgentEvent[][],
) {
  const expectedIds = visibleEventIds(handoff.events);
  if (expectedIds.length === 0) return true;
  if (!handoff.turnId) {
    return persistedTimelines.some((events) => {
      const persistedIds = new Set(events.filter(isVisibleAgentEvent).map((event) => event.id));
      return expectedIds.every((id) => persistedIds.has(id));
    });
  }
  return persistedTimelines.some((events) => {
    const sameTurn = events.some((event) => event.turn_id === handoff.turnId);
    if (!sameTurn) return false;
    const persistedIds = new Set(
      events
        .filter(
          (event) => event.turn_id === handoff.turnId && isVisibleAgentEvent(event),
        )
        .map((event) => event.id),
    );
    return expectedIds.every((id) => persistedIds.has(id));
  });
}

export function agentTurnIdsFromTimelines(
  persistedTimelines: SpecialistAgentEvent[][],
) {
  return Array.from(
    new Set(
      persistedTimelines.flatMap((events) =>
        events.flatMap((event) => event.turn_id ? [event.turn_id] : []),
      ),
    ),
  );
}

/** Keep received event rows visible while a live turn crosses into chat history. */
export function useAgentTimelineLedger() {
  const [events, setEvents] = useState<SpecialistAgentEvent[]>([]);
  const [handoffs, setHandoffs] = useState<AgentTimelineHandoff[]>([]);
  const eventsRef = useRef<SpecialistAgentEvent[]>([]);
  const fallbackKeyRef = useRef(0);

  const startAgentTurn = useCallback(() => {
    eventsRef.current = [];
    setEvents([]);
  }, []);

  const recordAgentEvent = useCallback((event: SpecialistAgentEvent) => {
    const next = appendAgentEvent(eventsRef.current, event);
    if (next === eventsRef.current) return;
    eventsRef.current = next;
    setEvents(next);
  }, []);

  const handoffAgentTurn = useCallback(() => {
    const snapshot = eventsRef.current;
    const visible = snapshot.filter(isVisibleAgentEvent);
    if (visible.length > 0) {
      const turnId = visible.find((event) => event.turn_id)?.turn_id ?? "";
      fallbackKeyRef.current += 1;
      const key = turnId
        ? `${turnId}:${visible.map((event) => event.id).join(",")}`
        : `local:${fallbackKeyRef.current}`;
      setHandoffs((current) =>
        current.some((handoff) => handoff.key === key)
          ? current
          : [...current, { key, turnId, events: [...snapshot] }],
      );
    }
    eventsRef.current = [];
    setEvents([]);
  }, []);

  const resetAgentTimeline = useCallback(() => {
    eventsRef.current = [];
    setEvents([]);
    setHandoffs([]);
  }, []);

  return {
    events,
    handoffs,
    recordAgentEvent,
    startAgentTurn,
    handoffAgentTurn,
    resetAgentTimeline,
  };
}

export function AgentTimelineHandoffs({
  handoffs,
  persistedTimelines,
  className,
}: {
  handoffs: AgentTimelineHandoff[];
  persistedTimelines: SpecialistAgentEvent[][];
  className?: string;
}) {
  const pending = handoffs.filter(
    (handoff) => !agentTimelineIsPersisted(handoff, persistedTimelines),
  );
  if (pending.length === 0) return null;
  return (
    <div
      data-agent-timeline-handoffs
      className={cn("space-y-3", className)}
    >
      {pending.map((handoff) => {
        const terminal = handoff.events.some((event) =>
          event.event === "turn.completed"
          || event.event === "turn.cancelled"
          || event.event === "turn.failed",
        );
        return terminal ? (
          <AgentWorkDisclosure key={handoff.key} events={handoff.events} />
        ) : (
          <AgentActivityTimeline key={handoff.key} events={handoff.events} running />
        );
      })}
    </div>
  );
}

function AgentTimelineConnector({
  index,
  firstNodeIndex,
  lastNodeIndex,
}: {
  index: number;
  firstNodeIndex: number;
  lastNodeIndex: number;
}) {
  if (
    firstNodeIndex < 0 ||
    firstNodeIndex === lastNodeIndex ||
    index < firstNodeIndex ||
    index > lastNodeIndex
  ) {
    return null;
  }
  return (
    <AgentTimelineRail
      start={index === firstNodeIndex ? "node" : "gap"}
      end={index === lastNodeIndex ? "node" : "gap"}
      className="bg-border/75"
    />
  );
}

/**
 * Inspectable activity stream backed by real server events.
 *
 * The expanded content contains safe operational summaries, plans and
 * validated outputs. Private model reasoning is intentionally never shown.
 */
export function AgentActivityTimeline({
  events,
  running = false,
  className,
}: {
  events: SpecialistAgentEvent[];
  running?: boolean;
  className?: string;
}) {
  const visibleEvents = useMemo(
    () => stabilizeAgentEvents(events.filter(isVisibleAgentEvent)),
    [events],
  );
  const terminalEvent = terminalAgentEvent(events);
  const { me } = useAuth();
  const language = me?.language === "de" ? "de" : "en";
  const isQuietEvent = (event: AgentEventGroup) =>
    event.event === "agent.update" ||
    event.event === "context.loaded" ||
    event.event === "context.compacted";
  const activeToolCall = visibleEvents.some((event) => {
    const current = event.frames.at(-1) ?? event;
    return !isPlanEvent(event) && !isQuietEvent(event) && !expectedObservation(current) && eventState(current) === "progress";
  });
  // Match Quick Answer: a running tool owns the animated semantic node. The
  // assistant orb and Thinking return only between observable tool calls.
  const showThinking = shouldShowAgentThinking(events, running, activeToolCall);
  const semanticNodeRows = visibleEvents.map((event) => {
    const current = event.frames.at(-1) ?? event;
    return isPlanEvent(event) || (!isQuietEvent(event) && !expectedObservation(current));
  });
  if (showThinking) semanticNodeRows.push(true);
  const firstNodeIndex = semanticNodeRows.indexOf(true);
  const lastNodeIndex = semanticNodeRows.lastIndexOf(true);

  if (visibleEvents.length === 0) {
    if (!running) return null;
    return (
      <div
        data-agent-timeline
        className={cn("min-w-0 max-w-full", className)}
        role="status"
        aria-live="polite"
        aria-label="Agent progress"
      >
        {showThinking ? <AgentThinkingIndicator /> : null}
      </div>
    );
  }

  return (
    <div
      data-agent-timeline
      className={cn(
        "min-w-0 max-w-full space-y-4 overflow-hidden",
        className,
      )}
      role="status"
      aria-live="polite"
      aria-label="Agent progress"
    >
      {visibleEvents.map((event, index) => {
          const currentEvent = event.frames.at(-1) ?? event;
          const liveState = toolCallState(currentEvent);
          const state = resolveTerminalToolState(liveState, terminalEvent);
          const statusLabel = liveState === "running" && terminalEvent
            ? eventStatusLabel(terminalEvent, language)
            : eventStatusLabel(currentEvent, language);
          // A terminal observation may recolor a correlated card, but it must
          // never replace the already visible started title with a new row.
          const observation =
            event.frames.length === 1 ? expectedObservation(currentEvent) : null;
          const duration = eventDuration(event);
          const details = observation
            ? []
            : (Array.from(
                new Set(
                  event.frames
                    .flatMap((frame, frameIndex) =>
                      [
                        frameIndex > 0 ? frame.label : undefined,
                        frame.detail,
                        ...(frame.steps ?? []),
                      ].map((detail) => eventProgressText(frame, detail)),
                    )
                    .filter((detail): detail is string => Boolean(detail)),
                ),
              ) as string[]);
          const firstInput = event.frames.find((frame) => hasReadableValue(frame.input))?.input;
          const firstBefore = event.frames.find((frame) => hasReadableValue(frame.before))?.before;
          const latestAfter = [...event.frames]
            .reverse()
            .find((frame) => hasReadableValue(frame.after))?.after;
          const latestOutput = [...event.frames]
            .reverse()
            .find((frame) => hasReadableValue(frame.output))?.output;
          const structuredDetails = observation
            ? []
            : ([
                ["Requested change", safeAgentDisplayValue(firstInput)],
                ["Before", safeAgentDisplayValue(firstBefore)],
                ["After", safeAgentDisplayValue(latestAfter)],
                [
                  "Result",
                  isFailureEvent(currentEvent)
                    ? undefined
                    : safeAgentDisplayValue(latestOutput),
                ],
              ].filter((entry) => hasReadableValue(entry[1])) as [string, unknown][]);
          const isPlan = isPlanEvent(event);
          const planFrames = isPlan
            ? event.frames.map((frame) => {
                const frameTitle = eventTitle(frame);
                const frameDetails = Array.from(
                  new Set(
                    [frame.detail, frame.message]
                      .map((detail) => eventProgressText(frame, detail))
                      .filter(
                        (detail): detail is string =>
                          Boolean(detail) && detail !== frameTitle,
                      ),
                  ),
                );
                const frameSteps = (frame.steps ?? [])
                  .map((step) => eventProgressText(frame, step))
                  .filter((step): step is string => Boolean(step));
                return { frame, frameTitle, frameDetails, frameSteps };
              })
            : [];
          const isNarrative =
            event.event === "agent.update" ||
            event.event === "context.loaded" ||
            event.event === "context.compacted";
          const resultCount = currentEvent.result_count ?? event.result_count;
          const hasDetails =
            details.length > 0 ||
            structuredDetails.length > 0 ||
            resultCount !== undefined;

          if (isPlan) {
            const planStatus =
              currentEvent.event === "plan.updated"
                ? language === "de"
                  ? "Aktualisiert"
                  : "Updated"
                : language === "de"
                  ? "Erstellt"
                  : "Created";
            return (
              <div
                key={event.id}
                data-agent-event={event.event}
                data-agent-event-plan
                className="relative min-w-0"
              >
                <AgentTimelineConnector
                  index={index}
                  firstNodeIndex={firstNodeIndex}
                  lastNodeIndex={lastNodeIndex}
                />
                <AgentToolCallCard
                  icon={ListChecks}
                  title={eventTitle(event)}
                  status={planStatus}
                  state="completed"
                  details={
                    <div data-agent-plan-frames className="space-y-3">
                      {planFrames.map(
                        ({ frame, frameTitle, frameDetails, frameSteps }, frameIndex) => (
                          <section
                            key={frame.id}
                            data-agent-plan-frame={frame.event}
                            className={cn(
                              "min-w-0",
                              frameIndex > 0 && "border-t border-border pt-3",
                            )}
                          >
                            <div className="flex min-w-0 items-center gap-2">
                              <span className="shrink-0 font-mono text-[0.53125rem] text-moss">
                                {String(frameIndex + 1).padStart(2, "0")}
                              </span>
                              <p className="min-w-0 break-words text-[0.75rem] font-medium leading-relaxed text-foreground/85">
                                {frameTitle}
                              </p>
                              <span className="ml-auto shrink-0 font-mono text-[0.53125rem] uppercase tracking-[0.12em] text-muted-foreground">
                                {frame.event === "plan.updated"
                                  ? language === "de"
                                    ? "Aktualisierung"
                                    : "Update"
                                  : "Plan"}
                              </span>
                            </div>
                            {frameDetails.length > 0 ? (
                              <div className="mt-1.5 space-y-1">
                                {frameDetails.map((detail, detailIndex) => (
                                  <p
                                    key={`${frame.id}-detail-${detailIndex}`}
                                    className="whitespace-pre-wrap break-words text-[0.71875rem] leading-relaxed text-muted-foreground"
                                  >
                                    {detail}
                                  </p>
                                ))}
                              </div>
                            ) : null}
                            {frameSteps.length > 0 ? (
                              <ol className="mt-2 space-y-1.5">
                                {frameSteps.map((step, stepIndex) => (
                                  <li
                                    key={`${frame.id}-step-${stepIndex}`}
                                    className="flex min-w-0 items-start gap-2 text-[0.75rem] leading-relaxed text-foreground/80"
                                  >
                                    <span className="mt-0.5 shrink-0 font-mono text-[0.625rem] text-moss">
                                      {String(stepIndex + 1).padStart(2, "0")}
                                    </span>
                                    <span className="min-w-0 break-words">{step}</span>
                                  </li>
                                ))}
                              </ol>
                            ) : null}
                          </section>
                        ),
                      )}
                    </div>
                  }
                />
              </div>
            );
          }

          if (isNarrative) {
            return (
              <div
                key={event.id}
                data-agent-event={event.event}
                data-agent-event-message
                className="relative flex min-w-0 items-start gap-2 pl-[3.25rem]"
              >
                <AgentTimelineConnector
                  index={index}
                  firstNodeIndex={firstNodeIndex}
                  lastNodeIndex={lastNodeIndex}
                />
                <span
                  aria-hidden="true"
                  className="mt-[0.4375rem] size-1.5 shrink-0 rounded-full bg-moss/55"
                />
                <div className="min-w-0 flex-1">
                  <p className="text-[0.75rem] leading-relaxed text-foreground/75">
                    {eventTitle(event)}
                  </p>
                </div>
              </div>
            );
          }

          if (observation) {
            return (
              <div
                key={event.id}
                data-agent-event={event.event}
                data-agent-event-observation={observation}
                className="relative flex min-w-0 items-start gap-2 pl-[3.25rem]"
              >
                <AgentTimelineConnector
                  index={index}
                  firstNodeIndex={firstNodeIndex}
                  lastNodeIndex={lastNodeIndex}
                />
                <span
                  aria-hidden="true"
                  className="mt-[0.4375rem] size-1.5 shrink-0 rounded-full bg-muted-foreground/40"
                />
                <p className="min-w-0 text-[0.75rem] leading-relaxed text-muted-foreground">
                  {eventTitle(event)}
                </p>
              </div>
            );
          }

          return (
            <div
              key={event.id}
              data-agent-event={event.event}
              data-agent-event-card
              className="relative min-w-0"
            >
              <AgentTimelineConnector
                index={index}
                firstNodeIndex={firstNodeIndex}
                lastNodeIndex={lastNodeIndex}
              />
              <AgentToolCallCard
                icon={eventIcon(event)}
                title={eventTitle(event)}
                status={statusLabel}
                state={state}
                meta={event.index && event.total ? `${event.index} / ${event.total}` : undefined}
                details={
                  hasDetails ? (
                    <>
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                      <p className="font-mono text-[0.53125rem] uppercase tracking-[0.16em] text-moss">
                        {event.tool?.endsWith(".agent_loop")
                          ? "Agent review"
                          : event.tool
                          ? event.tool.replaceAll(".", " / ").replaceAll("_", " ")
                          : eventKindLabel(currentEvent)}
                      </p>
                      {duration ? (
                        <span className="font-mono text-[0.53125rem] text-muted-foreground">
                          {duration}
                        </span>
                      ) : null}
                    </div>
                    {details.length > 0 ? (
                      <div className="mt-2 space-y-1.5">
                        {details.map((detail, detailIndex) => (
                          <p
                            key={`${event.id}-detail-${detailIndex}`}
                            className="whitespace-pre-wrap text-[0.75rem] leading-relaxed text-muted-foreground"
                          >
                            {detail}
                          </p>
                        ))}
                      </div>
                    ) : null}
                    {structuredDetails.length > 0 ? (
                      <div className="mt-3 space-y-3 border-t border-border pt-3">
                        {structuredDetails.map(([label, value]) => (
                          <section
                            key={`${event.id}-${label}`}
                            className={cn(
                              "min-w-0",
                              label === "After" &&
                                "rounded-xl border border-moss/20 bg-accent/15 px-3 py-2.5",
                            )}
                          >
                            <p className="mb-1.5 font-mono text-[0.53125rem] uppercase tracking-[0.16em] text-muted-foreground">
                              {label}
                            </p>
                            <ReadableValue value={value} />
                          </section>
                        ))}
                      </div>
                    ) : null}
                    {resultCount !== undefined ? (
                      <p className="mt-3 font-mono text-[0.5625rem] uppercase tracking-[0.14em] text-muted-foreground">
                        {resultCount} validated result{resultCount === 1 ? "" : "s"}
                      </p>
                    ) : null}
                    </>
                  ) : undefined
                }
              />
            </div>
          );
        })}
      {showThinking ? (
        <div className="relative min-w-0">
          <AgentTimelineConnector
            index={visibleEvents.length}
            firstNodeIndex={firstNodeIndex}
            lastNodeIndex={lastNodeIndex}
          />
          <AgentThinkingIndicator />
        </div>
      ) : null}
    </div>
  );
}

/** Completed work is compact by default while every durable event stays inspectable. */
export function AgentWorkDisclosure({
  events,
  className,
  defaultOpen = false,
}: {
  events: SpecialistAgentEvent[];
  className?: string;
  defaultOpen?: boolean;
}) {
  const visibleEvents = useMemo(
    () => events.filter(isVisibleAgentEvent),
    [events],
  );
  const failed = events.some((event) => event.event === "turn.failed");
  const cancelled = events.some((event) => event.event === "turn.cancelled");
  const { me } = useAuth();
  const language = me?.language === "de" ? "de" : "en";
  const [open, setOpen] = useState(defaultOpen || failed);
  if (visibleEvents.length === 0) return null;
  const stateLabel = failed
    ? language === "de"
      ? "Prüfen"
      : "Needs attention"
    : cancelled
      ? language === "de"
        ? "Gestoppt"
        : "Stopped"
      : language === "de"
        ? "Erledigt"
        : "Done";
  const workLabel = language === "de" ? "Agent-Arbeit" : "Agent work";
  const StatusIcon = failed ? Circle : cancelled ? Square : Check;

  return (
    <div
      data-agent-work-disclosure
      className={cn("flex min-w-0 max-w-full gap-3", className)}
    >
      <span
        data-agent-work-node
        className={cn(
          "grid size-10 shrink-0 place-items-center rounded-full border",
          failed
            ? "border-destructive/30 bg-destructive/10 text-destructive"
            : cancelled
              ? "border-border bg-secondary/45 text-muted-foreground"
              : "border-moss/30 bg-accent/50 text-moss",
        )}
        aria-hidden="true"
      >
        <StatusIcon className={cn("size-4", failed && "fill-current")} />
      </span>
      <div className="min-w-0 flex-1 pt-1">
        <button
          type="button"
          onClick={() => setOpen((current) => !current)}
          aria-expanded={open}
          aria-label={`${workLabel} · ${stateLabel}`}
          className="group inline-flex min-h-8 max-w-full items-center gap-2 rounded-full border border-border bg-secondary/60 py-1.5 pl-3 pr-2.5 text-left transition-colors hover:border-moss/40 hover:bg-secondary"
        >
          <span className="truncate text-[0.8125rem] font-medium text-foreground/85">
            {workLabel}
          </span>
          <span
            className={cn(
              "shrink-0 rounded-full px-1.5 py-0.5 font-mono text-[0.59375rem] uppercase tracking-[0.1em]",
              failed
                ? "bg-destructive/10 text-destructive"
                : cancelled
                  ? "bg-secondary text-muted-foreground"
                  : "bg-accent text-moss",
            )}
          >
            {stateLabel}
          </span>
          <ChevronDown
            className={cn(
              "size-3.5 shrink-0 text-muted-foreground/60 transition-transform",
              open && "rotate-180",
            )}
          />
        </button>
        {open ? (
          <AgentActivityTimeline events={events} className="mt-3" />
        ) : null}
      </div>
    </div>
  );
}

/** Shared persisted specialist message layout used by every workspace. */
export function SpecialistCompletedTurn({
  events,
  kind,
  answer,
  artifacts,
  children,
  className,
}: {
  events?: SpecialistAgentEvent[];
  kind: AgentWorkKind;
  answer: ReactNode;
  artifacts?: SpecialistArtifact[];
  children?: ReactNode;
  className?: string;
}) {
  const hasEvents = (events?.length ?? 0) > 0;
  return (
    <div data-specialist-completed-turn className={cn("min-w-0 max-w-full", className)}>
      {hasEvents ? (
        <AgentWorkDisclosure events={events ?? []} className="mb-3" />
      ) : (
        <AgentContextReceipt kind={kind} />
      )}
      <AgentFinalResponse>{answer}</AgentFinalResponse>
      <AgentArtifactLinks artifacts={artifacts} />
      {children ? <div className="mt-2 min-w-0">{children}</div> : null}
    </div>
  );
}

/**
 * Compatibility fallback for older callers.
 *
 * It intentionally shows no timer-driven, invented phases. Specialist pages
 * should render AgentActivityTimeline with events emitted by the server.
 */
export default function AgentWorkStatus({ className }: { kind: AgentWorkKind; className?: string }) {
  return <AgentThinkingIndicator className={className} />;
}

export type AgentChangeItem = {
  operation?: string;
  label: string;
  detail?: string;
  applied?: boolean;
};

/** Apply controls for a proposal whose exact changes already live in the timeline. */
export function AgentProposalControls({
  count,
  applying = false,
  onApply,
}: {
  count: number;
  applying?: boolean;
  onApply: () => void;
}) {
  return (
    <div className="mb-3 ml-10 flex flex-wrap items-center gap-2">
      <p className="text-[0.6875rem] leading-relaxed text-muted-foreground">
        {count} validated {count === 1 ? "change is" : "changes are"} ready for review.
      </p>
      <button
        type="button"
        onClick={onApply}
        disabled={applying}
        className="inline-flex h-8 cursor-pointer items-center gap-2 rounded-full bg-pine px-4 text-[0.6875rem] font-medium text-ivory transition-opacity disabled:cursor-not-allowed disabled:opacity-50 dark:bg-ivory dark:text-pine"
      >
        {applying ? (
          <Circle className="size-3 animate-pulse" />
        ) : (
          <Check className="size-3" />
        )}
        Apply {count === 1 ? "change" : `${count} changes`}
      </button>
    </div>
  );
}

/** Exact, individually inspectable changes returned by a specialist agent. */
export function AgentChangeSequence({
  changes,
  pending = false,
  applying = false,
  onApply,
}: {
  changes: AgentChangeItem[];
  pending?: boolean;
  applying?: boolean;
  onApply?: () => void;
}) {
  return (
    <div
      className="mb-3 space-y-2"
      aria-label={pending ? "Proposed changes" : "Applied changes"}
    >
      {changes.map((change, index) => (
        <div
          key={`${change.operation ?? "change"}-${index}-${change.label}`}
          className="relative flex gap-2.5"
        >
          {index < changes.length - 1 ? (
            <span className="absolute bottom-[-0.625rem] left-[0.8125rem] top-7 w-px bg-border" />
          ) : null}
          <span
            className={cn(
              "relative z-10 grid size-7 shrink-0 place-items-center rounded-full border bg-card",
              pending
                ? "border-moss/35 text-moss"
                : change.applied === false
                  ? "border-destructive/30 text-destructive"
                  : "border-moss/35 text-moss",
            )}
          >
            {pending ? (
              <PencilLine className="size-3.5" />
            ) : change.applied === false ? (
              <Circle className="size-3.5" />
            ) : (
              <Check className="size-3.5" />
            )}
          </span>
          <div className="min-w-0 flex-1 rounded-2xl border border-border bg-secondary/30 px-3.5 py-2.5">
            <div className="flex items-center justify-between gap-3">
              <p className="text-[0.75rem] font-medium text-foreground">
                {change.label}
              </p>
              <span className="shrink-0 font-mono text-[0.5rem] uppercase tracking-[0.16em] text-muted-foreground">
                {index + 1} / {changes.length}
              </span>
            </div>
            {change.detail ? (
              <p className="mt-1 whitespace-pre-wrap text-[0.6875rem] leading-relaxed text-muted-foreground">
                {change.detail}
              </p>
            ) : null}
          </div>
        </div>
      ))}
      {pending && onApply ? (
        <button
          type="button"
          onClick={onApply}
          disabled={applying}
          className="ml-9 inline-flex h-8 cursor-pointer items-center gap-2 rounded-full bg-pine px-4 text-[0.6875rem] font-medium text-ivory transition-opacity disabled:cursor-not-allowed disabled:opacity-50 dark:bg-ivory dark:text-pine"
        >
          {applying ? (
            <Circle className="size-3 animate-pulse" />
          ) : (
            <Check className="size-3" />
          )}
          Apply {changes.length === 1 ? "change" : `${changes.length} changes`}
        </button>
      ) : null}
    </div>
  );
}
