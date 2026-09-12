/**
 * Optional product analytics are disabled for the public release.
 * The closed schemas below remain available for regression tests, but neither
 * loading this module nor calling track reads storage or sends an event.
 *
 * Dormant normalizers are retained as regression-tested constraints for any
 * future opt-in implementation. They are not connected to a browser tracker.
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

type TrackerPayload = {
  url?: string;
  referrer?: string;
  title?: string;
  [key: string]: unknown;
};

type UmamiTracker = {
  track: (name: string, data?: EventData) => void;
};

declare global {
  interface Window {
    umami?: UmamiTracker;
    sixBeforeSend?: (
      type: string,
      payload: TrackerPayload,
    ) => TrackerPayload | false;
  }
}

/**
 * Route patterns whose id segment is masked. Ordered: the studies route has
 * to win over the plain interview route it is nested under.
 */
const ID_ROUTES: ReadonlyArray<readonly [RegExp, string]> = [
  [/^\/interviews\/studies\/[^/]+/, "/interviews/studies/:id"],
  [/^\/interviews\/[^/]+/, "/interviews/:id"],
  [/^\/projects\/[^/]+/, "/projects/:id"],
  [/^\/writer\/[^/]+/, "/writer/:id"],
  [/^\/surveys\/[^/]+/, "/surveys/:id"],
  [/^\/data\/[^/]+/, "/data/:id"],
  [/^\/r\/[^/]+/, "/r/:id"],
];

/** Readable names for the dashboard, keyed by normalized path. */
const ROUTE_LABELS: Record<string, string> = {
  "/": "New search",
  "/library": "Library",
  "/brainstorming": "Brainstorming",
  "/writer": "Writer",
  "/writer/:id": "Writer document",
  "/figures": "Figures",
  "/data": "Data",
  "/data/:id": "Dataset",
  "/interviews": "Interviews",
  "/interviews/:id": "Interview",
  "/interviews/studies/:id": "Voice study",
  "/surveys": "Surveys",
  "/surveys/:id": "Survey",
  "/projects/:id": "Project",
  "/r/:id": "Run",
  "/docs": "Docs",
  "/ideas": "Ideas",
};

/** Replaces the id segment of a workspace route with `:id`. */
export function normalizePath(path: string): string {
  for (const [pattern, replacement] of ID_ROUTES) {
    if (pattern.test(path)) return path.replace(pattern, replacement);
  }
  return path;
}

export function routeLabel(path: string): string {
  return ROUTE_LABELS[path] ?? "SixSentences_";
}

/** Same-origin referrers are app routes, so they need the same masking. */
function normalizeReferrer(value: string): string {
  if (!value) return value;
  try {
    const parsed = new URL(value, window.location.origin);
    if (parsed.origin !== window.location.origin) return parsed.origin;
    return normalizePath(parsed.pathname);
  } catch {
    return "";
  }
}

function beforeSend(
  _type: string,
  payload: TrackerPayload,
): TrackerPayload | false {
  const [path] = (payload.url ?? "").split(/[?#]/);
  const normalized = normalizePath(path);
  return {
    ...payload,
    url: normalized,
    referrer: normalizeReferrer(payload.referrer ?? ""),
    // titles are static today, but pinning them to the route keeps a future
    // `document.title = doc.title` from leaking a document name in here
    title: routeLabel(normalized),
  };
}

// No browser hook is installed while optional measurement is disabled.

/** Deliberate no-op, including when a legacy tracker is present in the browser. */
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
