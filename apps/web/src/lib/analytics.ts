/**
 * Optional product analytics are disabled for the public release.
 * The closed schemas below remain available for regression tests, but neither
 * loading this module nor calling track reads storage or sends an event.
 */

/** Event names, closed on purpose: a typo should not create a new metric. */
export type AnalyticsEvent =
  | "run_started"
  | "run_exported"
  | "run_shared"
  | "chat_message"
  | "doc_created"
  | "figure_generated"
  | "dataset_created"
  | "interview_created"
  | "study_created"
  | "survey_created"
  | "onboarding_done";

export interface AnalyticsPayloads {
  run_started: {
    mode: "ask" | "search";
    filed: boolean;
    refine: boolean;
    documents: number;
    live: boolean;
    full_text: boolean;
  };
  run_exported: { kind: string; included_only?: boolean };
  run_shared: undefined;
  chat_message: { surface: "run" };
  doc_created: { source: string };
  figure_generated: {
    kind: string;
    resolution: string;
    review_passes: number;
  };
  dataset_created: { files: number; mode: "version" | "new" };
  interview_created: { source: "upload"; language: string };
  study_created: { language: string };
  survey_created: undefined;
  onboarding_done: { step: number };
}

type EventData = Record<string, string | number | boolean>;

const EVENT_FIELDS: Record<AnalyticsEvent, readonly string[]> = {
  run_started: ["mode", "filed", "refine", "documents", "live", "full_text"],
  run_exported: ["kind", "included_only"],
  run_shared: [],
  chat_message: ["surface"],
  doc_created: ["source"],
  figure_generated: ["kind", "resolution", "review_passes"],
  dataset_created: ["files", "mode"],
  interview_created: ["source", "language"],
  study_created: ["language"],
  survey_created: [],
  onboarding_done: ["step"],
};

const SAFE_EVENT_VALUE = /^[a-z0-9_.:-]{1,80}$/i;

/** Runtime enforcement backs up TypeScript at the browser trust boundary. */
export function sanitizeAnalyticsData(
  event: AnalyticsEvent,
  data: unknown,
): EventData | undefined {
  if (!data || typeof data !== "object" || Array.isArray(data)) return undefined;
  const allowed = new Set(EVENT_FIELDS[event]);
  const result: EventData = {};
  for (const [key, value] of Object.entries(data)) {
    if (!allowed.has(key)) continue;
    if (typeof value === "boolean") {
      result[key] = value;
    } else if (typeof value === "number" && Number.isFinite(value)) {
      result[key] = Math.max(-1_000_000_000, Math.min(1_000_000_000, value));
    } else if (typeof value === "string" && SAFE_EVENT_VALUE.test(value)) {
      result[key] = value;
    }
  }
  return Object.keys(result).length > 0 ? result : undefined;
}

/** Deliberate no-op while optional measurement is disabled. */
export function track<E extends AnalyticsEvent>(
  event: E,
  data?: AnalyticsPayloads[E],
): void {
  void event;
  void data;
}

export function analyticsDisabled(): boolean {
  return true;
}

/** Compatibility no-op: an old preference cannot re-enable measurement. */
export function setAnalyticsDisabled(disabled: boolean): void {
  void disabled;
}
