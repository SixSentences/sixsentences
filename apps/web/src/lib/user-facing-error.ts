const DEFAULT_PUBLIC_ERROR = "That didn't work. Please try again.";
const DEFAULT_STORED_ERROR = "We couldn't process this item. Please try again.";

export type EntitlementErrorKind = "feature" | "capacity" | "concurrency" | "limit" | "unavailable";

const ENTITLEMENT_KIND_BY_CODE: Readonly<Record<string, EntitlementErrorKind>> = {
  feature_not_in_plan: "feature",
  upgrade_required: "feature",
  capacity_exhausted: "capacity",
  capacity_unavailable: "capacity",
  voice_interview_capacity_unavailable: "capacity",
  concurrency_limit: "concurrency",
  resource_limit: "limit",
  resource_limit_reached: "limit",
  action_capacity_limit: "limit",
  entitlement_limit: "unavailable",
};

/**
 * Server error text is untrusted, even when it looks harmless: older workers,
 * reverse proxies and SDKs can put credentials or diagnostics in `message`.
 * Only local copy in this table may be selected by a server-owned stable code.
 */
const PUBLIC_ERROR_BY_CODE: Readonly<Record<string, string>> = {
  action_capacity_limit:
    "This action is larger than the per-action limit. Choose a smaller request and try again.",
  api_key_endpoint_forbidden: "Sign in to use this action.",
  api_key_rate_limited: "Too many requests at once. Please wait a moment and try again.",
  ask_queue_unavailable: "The answer could not be started. Please try again in a moment.",
  brainstorm_complete_payload_required: "Add at least one thought before completing this brainstorm.",
  brainstorm_delete_challenge_stale: "This brainstorm changed in the meantime. Refresh and try again.",
  brainstorm_in_progress: "Finish or stop the active brainstorm first.",
  brainstorm_is_solo: "This brainstorm does not have a companion session.",
  brainstorm_session_already_synthesized: "This brainstorm has already been structured.",
  brainstorm_session_delete_requires_cleanup: "Finish or cancel this brainstorm before deleting it.",
  brainstorm_session_revision_conflict: "This brainstorm changed in the meantime. Refresh and try again.",
  brainstorm_too_large: "This brainstorm is too large to process at once.",
  brainstorm_too_many_segments: "This brainstorm has too many separate thoughts to process at once.",
  browser_capture_rate_limited: "Too many saves at once. Please wait a moment and try again.",
  capacity_exhausted:
    "There is not enough available capacity to start this action. Review current usage or choose a smaller request.",
  capacity_unavailable:
    "There is not enough available capacity to start this action. Review current usage or choose a smaller request.",
  companion_upload_busy: "Paper upload is busy. Please try again shortly.",
  companion_upload_rate_limited: "Too many paper uploads at once. Please wait a moment and try again.",
  concurrency_limit:
    "The workspace already has the maximum number of actions running. Wait for one to finish or stop it before trying again.",
  conversation_complete_has_no_payload: "There is no completed conversation to save yet.",
  conversation_consent_required: "Confirm the participant information before starting the conversation.",
  entitlement_limit: "This action is currently unavailable. Please try again or contact support.",
  feature_not_in_plan: "This action is not enabled on the current deployment.",
  fulltext_access_gap: "Some full texts are unavailable. Review the available sources before continuing.",
  fulltext_review_pending: "Complete the pending full-text review before continuing.",
  github_connection_failed: "The repository could not be connected. Review the access settings and try again.",
  insufficient_scope: "This access key does not permit that action.",
  integrity_sources_partial: "Some integrity checks were unavailable. Review the available results before continuing.",
  knowledge_content_limit_reached: "This page has reached its content limit.",
  knowledge_hierarchy_cycle: "A page cannot be moved inside itself.",
  knowledge_hierarchy_too_deep: "This page would be nested too deeply.",
  knowledge_idempotency_conflict: "This page changed in the meantime. Refresh and try again.",
  knowledge_page_limit_reached: "This workspace has reached its page limit.",
  knowledge_revision_conflict: "This page changed in the meantime. Refresh and try again.",
  legal_reaccept_required: "Review and accept the current legal terms to continue.",
  library_metadata_identity_conflict: "That identifier already belongs to another Library item.",
  library_metadata_revision_conflict: "This Library item changed in the meantime. Refresh and try again.",
  library_share_limit_reached: "This Library share has reached its item limit.",
  library_share_scope_overlap: "This access already exists in another Library share.",
  live_provider_interrupted: "The live conversation was interrupted. Reconnect to continue.",
  live_results_capped: "The live search reached its result limit. Narrow the question and try again.",
  living_refresh_web_search_not_reused: "Web sources need fresh confirmation before this review can be refreshed.",
  paper_chat_processing: "This paper is still being prepared. Please try again shortly.",
  paper_enrichment_already_active: "Metadata recovery is already running for this paper.",
  paper_enrichment_capacity_busy: "Metadata recovery is busy. Please try again shortly.",
  paper_enrichment_daily_limit: "The daily metadata recovery limit has been reached.",
  paper_enrichment_rate_limited: "Too many metadata requests at once. Please wait a moment and try again.",
  paper_enrichment_request_conflict: "This metadata request changed in the meantime. Refresh and try again.",
  paper_enrichment_retry_limit: "This paper has reached its metadata retry limit.",
  paper_enrichment_source_changed: "This paper changed in the meantime. Refresh and try again.",
  paper_enrichment_strong_identity_required: "Add a DOI or another stable publication identifier first.",
  participant_information_required: "Confirm the participant information before starting the interview.",
  project_brainstorm_in_progress: "Finish or stop the active project brainstorm first.",
  project_brainstorm_queue_unavailable: "The project brainstorm could not be started. Please try again shortly.",
  project_brainstorm_revision_conflict: "This project brainstorm changed in the meantime. Refresh and try again.",
  project_brainstorm_selection_invalid: "Choose completed brainstorms from this project.",
  project_brainstorm_too_large: "The selected brainstorms are too large to process at once.",
  project_brainstorm_too_many_sessions: "Choose fewer brainstorm sessions and try again.",
  project_delete_owner_required: "Only the project owner can delete this project.",
  rate_limit_unavailable: "This action is temporarily unavailable. Please try again.",
  repository_analysis_disabled: "Repository analysis is not available yet.",
  repository_connection_exists: "This repository is already connected.",
  repository_connection_limit_reached: "This workspace has reached its repository connection limit.",
  repository_connection_mismatch: "Choose the connection that belongs to this repository.",
  repository_connection_not_found: "The repository connection could not be found.",
  repository_connection_rate_limited: "Too many repository requests at once. Please wait a moment and try again.",
  repository_credentials_unavailable: "Reconnect the repository and try again.",
  repository_goal_mismatch: "The repository changed in the meantime. Start a new analysis.",
  repository_prose_candidate_compile_failed: "The proposed manuscript change could not be validated.",
  repository_request_id_conflict: "This repository request changed in the meantime. Start a new analysis.",
  resource_limit:
    "This action exceeds a workspace limit. Review usage or reduce the size of the request.",
  resource_limit_reached:
    "This action exceeds a workspace limit. Review usage or reduce the size of the request.",
  screening_recall_not_certified: "Review the screening results before continuing.",
  search_coverage_uncertain: "Search coverage could not be confirmed. Review the search before continuing.",
  search_coverage_undetermined: "Search coverage could not be determined. Review the search before continuing.",
  selection_not_on_page: "Select text that is visible on the current page.",
  upgrade_required: "This action is not enabled on the current deployment.",
  voice_interview_capacity_unavailable:
    "This interview cannot start right now because the study does not have enough capacity for a full session. Please try again later or contact the research team.",
  voice_study_budget_below_session_cap:
    "Fieldwork budget must be at least the session cap. Raise the budget or lower the session cap.",
  web_search_public_scope_confirmation_required: "Confirm that this web search contains only public, non-sensitive information.",
  web_search_unavailable: "Web source search is temporarily unavailable. Please try again later.",
  writer_revision_conflict: "Another author changed this passage. Refresh and compare the latest version.",
};

/**
 * Exact application-authored copy that may pass through component boundaries.
 * Do not replace this with shape-based matching: reflected user input can look
 * like friendly validation text while still containing sensitive content.
 */
const SAFE_PUBLIC_MESSAGES = new Set<string>([
  DEFAULT_PUBLIC_ERROR,
  DEFAULT_STORED_ERROR,
  "Agent activity",
  "Can't reach SixSentences_ right now. Check your internet connection and try again.",
  "Choose a manuscript section before applying this edit.",
  "Choose a PDF smaller than 100 MB.",
  "Choose between 1 and 100 valid Library papers.",
  "Could not render the diagram.",
  "Download failed.",
  "PNG export failed.",
  "Retry the request.",
  "Retry to continue from the last completed page.",
  "Something went wrong on our side. Please try again in a moment.",
  "That didn't work with the current input. Review it and try again.",
  "This action is not enabled on the current deployment.",
  "This action is currently unavailable. Please try again or contact support.",
  "This action exceeds a workspace limit. Review usage or reduce the size of the request.",
  "This action is larger than the per-action limit. Choose a smaller request and try again.",
  "There is not enough available capacity to start this action. Review current usage or choose a smaller request.",
  "The workspace already has the maximum number of actions running. Wait for one to finish or stop it before trying again.",
  "This item changed in the meantime. Refresh and try again.",
  "This item could not be found.",
  "This request took too long. Please try again.",
  "This upload is too large. Choose a smaller file and try again.",
  "Too many requests at once. Please wait a moment and try again.",
  "The analysis could not be completed. Please try again.",
  "The answer could not be completed. Please try again.",
  "The download failed.",
  "The requested change was not applied",
  "The saved preview request ended. Start a new preview.",
  "The saved proposal request ended. Start a new proposal.",
  "The visual could not be generated. Please edit the brief and try again.",
  "This background task needs attention. Please try it again.",
  "This step could not be completed. Please try processing the interview again.",
  "This step could not be completed. Try the figure again.",
  "We couldn't finish this request. Your saved work is unchanged. Please try again.",
  "We couldn't finish this request. Your workspace was left unchanged. Please try again.",
  "We couldn't process this interview. Open it to review your options.",
  "We couldn't process this interview. Return to Interviews and try again.",
  "You don't have permission to do that.",
  "Your session is no longer valid. Please sign in again.",
  "Canvas is unavailable in this browser.",
  "Die Analyse konnte nicht abgeschlossen werden. Versuche es bitte erneut.",
  "Der Analyseumfang ist zu groß. Ein Unterpfad oder ein enger formuliertes Ziel kann den relevanten Datei- und Textumfang reduzieren.",
  "Die gespeicherte Preview-Anfrage wurde beendet. Starte eine neue Preview.",
  "Die gespeicherte Proposal-Anfrage wurde beendet. Du kannst eine neue Review-Anfrage starten; das Manuskript blieb unverändert.",
  "Die gespeicherte Vorschlagsanfrage wurde beendet. Starte einen neuen Vorschlag.",
  "The analysis scope is too large. A subpath or a more focused goal can reduce the relevant file and text scope.",
  "The saved proposal request ended. You can start a new review request; the manuscript stayed unchanged.",
]);

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function normalizedExactMessage(candidate: unknown): string | null {
  if (typeof candidate !== "string") return null;
  const message = candidate.replace(/[\u0000-\u001f\u007f]+/g, " ").trim();
  return SAFE_PUBLIC_MESSAGES.has(message) ? message : null;
}

function stableErrorCode(candidate: unknown): string | null {
  const envelope = record(candidate);
  if (!envelope) return null;
  const nested = record(envelope.detail);
  return typeof envelope.code === "string"
    ? envelope.code
    : typeof nested?.code === "string"
      ? nested.code
      : null;
}

function approvedCodeMessage(candidate: unknown): string | null {
  const code = stableErrorCode(candidate);
  return code && Object.prototype.hasOwnProperty.call(PUBLIC_ERROR_BY_CODE, code)
    ? PUBLIC_ERROR_BY_CODE[code]
    : null;
}

/** Only stable server codes classify a 402; messages and arbitrary hints do not. */
export function entitlementErrorKind(status: number, candidate: unknown): EntitlementErrorKind | null {
  if (status !== 402) return null;
  const code = stableErrorCode(candidate);
  return code && Object.prototype.hasOwnProperty.call(ENTITLEMENT_KIND_BY_CODE, code)
    ? ENTITLEMENT_KIND_BY_CODE[code]
    : "unavailable";
}

function safeFallback(candidate: unknown, defaultMessage: string): string {
  if (candidate === "") return "";
  return normalizedExactMessage(candidate) ?? defaultMessage;
}

function fallbackForStatus(status: number): string {
  if (status === 401) return "Your session is no longer valid. Please sign in again.";
  if (status === 402) return "This action is currently unavailable. Please try again or contact support.";
  if (status === 403) return "You don't have permission to do that.";
  if (status === 404) return "This item could not be found.";
  if (status === 408) return "This request took too long. Please try again.";
  if (status === 409) return "This item changed in the meantime. Refresh and try again.";
  if (status === 413) return "This upload is too large. Choose a smaller file and try again.";
  if (status === 429) return "Too many requests at once. Please wait a moment and try again.";
  if (status >= 500 || status === 0) {
    return "Something went wrong on our side. Please try again in a moment.";
  }
  return "That didn't work with the current input. Review it and try again.";
}

/**
 * Select customer copy from a stable allowlisted code or an exact local
 * message. Unknown server text always fails closed to status-based copy.
 */
export function userFacingApiErrorMessage(
  status: number,
  candidate: unknown,
): string {
  if (status === 401 || status === 403) return fallbackForStatus(status);
  return approvedCodeMessage(candidate)
    ?? normalizedExactMessage(candidate)
    ?? fallbackForStatus(status);
}

/**
 * Sanitize an Error (or an event-provided failure) at a component boundary.
 * This intentionally applies the same allowlist to Error.message; being in an
 * Error object does not make server or browser text safe.
 */
export function userFacingErrorMessage(
  error: unknown,
  fallback = DEFAULT_PUBLIC_ERROR,
): string {
  const envelope = record(error);
  const statusValue = envelope && "status" in envelope ? Number(envelope.status) : null;
  const status = statusValue !== null && Number.isFinite(statusValue) ? statusValue : null;
  if (status === 401 || status === 403) return fallbackForStatus(status);
  const coded = approvedCodeMessage(error);
  if (coded) return coded;
  const candidate = error instanceof Error
    ? error.message
    : typeof error === "string"
      ? error
      : envelope?.message;
  const approved = normalizedExactMessage(candidate);
  if (approved) return approved;
  return safeFallback(fallback, status === null ? DEFAULT_PUBLIC_ERROR : fallbackForStatus(status));
}

const PUBLIC_TALK_ERROR_BY_CODE: Readonly<
  Record<string, Readonly<Record<"de" | "en", string>>>
> = {
  voice_interview_capacity_unavailable: {
    de: "Das Interview kann gerade nicht gestartet werden, weil nicht genügend Kapazität für eine vollständige Sitzung verfügbar ist. Bitte versuchen Sie es später erneut oder wenden Sie sich an das Forschungsteam.",
    en: "This interview cannot start right now because the study does not have enough capacity for a full session. Please try again later or contact the research team.",
  },
};

/** Participant pages use local, translated copy selected only by stable API codes. */
export function userFacingPublicTalkErrorMessage(
  error: unknown,
  language: "de" | "en",
): string {
  const code = stableErrorCode(error);
  const localized = code ? PUBLIC_TALK_ERROR_BY_CODE[code] : undefined;
  if (localized) return localized[language];
  return language === "de"
    ? "Das Interview konnte gerade nicht gestartet werden. Bitte versuchen Sie es erneut oder wenden Sie sich an das Forschungsteam."
    : "The interview could not start right now. Please try again or contact the research team.";
}

/**
 * Durable worker/tool failures are operational records and may pre-date
 * current redaction rules. Never inspect or render their contents.
 */
export function userFacingStoredErrorMessage(
  _storedError: unknown,
  fallback = DEFAULT_STORED_ERROR,
): string {
  return safeFallback(fallback, DEFAULT_STORED_ERROR);
}

/** Stream failures are operational incidents, never a place for raw details. */
export function userFacingAgentFailureMessage(): string {
  return "We couldn't finish this request. Your workspace was left unchanged. Please try again.";
}
