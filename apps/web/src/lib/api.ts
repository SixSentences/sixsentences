/**
 * Typed client for a compatible SixSentences workspace API.
 *
 * Auth is an opaque bearer token kept in localStorage. Error contract:
 * 401 for the current token clears the session and announces `six:unauthorized`.
 * 402 uses stable codes to distinguish feature access and resource limits.
 */

import type { WriterSelection } from "@/lib/writer-selection";
import { PUBLIC_WEB_SEARCH_NOTICE_VERSION } from "@/lib/public-web-search-query";
import {
  availabilityErrorKind,
  type AvailabilityErrorKind,
  userFacingAgentFailureMessage,
  userFacingApiErrorMessage,
} from "@/lib/user-facing-error";

import type {
  AgentTurnState,
  AgentTurnStopResult,
  AssistantPreferences,
  ApiKey,
  ApiKeyScope,
  ApiKeyScopeDefinition,
  BrainstormDeleteChallenge,
  BrainstormFilingChallenge,
  CalibrationReport,
  ChatAnswer,
  ChatMessage,
  ChatModelCatalog,
  ChatStreamEvent,
  ChatTurnState,
  Decision,
  EvidenceCell,
  EvidenceGraph,
  EvidenceRelationship,
  EvidenceTable,
  DocumentEntry,
  DocumentAnnotation,
  DocumentTranslationStatus,
  LibraryDocument,
  LibraryDocumentCitations,
  LibraryCitationFormat,
  LibraryMetadataUpdateResult,
  LibraryPaperMetadata,
  LibraryShare,
  LibraryShareScope,
  PaperEnrichmentJob,
  ReceivedLibraryShare,
  SharedLibraryDocument,
  SharedLibraryWebSource,
  LibraryWebSource,
  BrowserCaptureDevice,
  BrowserCaptureRevision,
  KnowledgePageCreate,
  KnowledgePageFilters,
  KnowledgePageList,
  KnowledgePageRecord,
  KnowledgePageUpdate,
  LivingResearchWorkspace,
  LiveCompanionPairRequest,
  LiveCompanionPairResponse,
  LiveCompanionDevicePage,
  LiveAskReceipt,
  LiveBrainstormCompleteResult,
  LiveBrainstormCreate,
  ProjectBrainstormDocument,
  ProjectBrainstormSynthesisCreate,
  ProjectBrainstormSynthesisPage,
  ProjectBrainstormSynthesisReceipt,
  LiveSession,
  LiveSessionSegment,
  LiveBrainstormPage,
  LiveBrainstormReceipt,
  LiveSessionConfig,
  LiveSessionEventPage,
  LiveSessionPage,
  LiveSegmentAppendResult,
  LiveTranscriptSegment,
  LiveTranscriptSegmentCreate,
  LoginResponse,
  GoogleAuthResponse,
  ExportFormat,
  Figure,
  Health,
  HumanDecision,
  ImportBatch,
  Me,
  TwoFactorSetup,
  TwoFactorStatus,
  Member,
  ProbeResult,
  Project,
  ProjectStudy,
  ProjectSubmission,
  ProjectTask,
  ProjectWorkspace,
  PublicSurvey,
  RepositoryAnalysis,
  RepositoryAnalysisCreate,
  RepositoryConnection,
  GithubRepositoryConnectionCreate,
  RepositoryManuscriptPreview,
  RepositoryManuscriptPreviewRequest,
  RepositoryManuscriptProposal,
  RepositoryManuscriptProposalRequest,
  EvidenceClaim,
  ProtocolResponse,
  QueryTranslations,
  QueuePage,
  ReferenceConnector,
  ReferenceConnectorPushResult,
  DatasetMessage,
  DatasetChatReply,
  DatasetVersion,
  Interview,
  InterviewMessage,
  InterviewQuote,
  InterviewSegment,
  PublicTalkInfo,
  VoiceConfig,
  VoiceGuideSection,
  VoiceInvite,
  LiveVoiceSessionConfig,
  VoiceSession,
  VoiceStudyAction,
  VoiceStudyMessage,
  VoiceSessionConfig,
  VoiceStudy,
  ParticipantInformation,
  VoiceTurn,
  ResearchDataset,
  ResearchControlRoom,
  ScreeningMethod,
  RegisterResponse,
  ReportResponse,
  RetractionDelta,
  ReviewHealth,
  RunCreated,
  RunCreateRequest,
  RunDetail,
  PublicSharedRun,
  RunDiff,
  RunEvent,
  RunShareInfo,
  RunSummary,
  UploadedDocument,
  Survey,
  SurveyAgentReply,
  SurveyMessage,
  SurveyProposalApplyResult,
  SpecialistAgentEvent,
  SpecialistResourceKind,
  Webhook,
  WebhookCreated,
  WebSource,
  WorksPage,
  WriterAsset,
  WriterAudit,
  WriterCitation,
  WriterCollaborator,
  WriterCollaboratorCatalog,
  WriterCompile,
  WriterComment,
  WriterContributionLog,
  WriterDocument,
  WriterInterviewContextCatalog,
  WriterInterviewContextMode,
  WriterSurveyContextCatalog,
  WriterSurveyContextMode,
  WriterMessage,
  WriterProjectFile,
  WriterPresence,
  WriterRetargetPreview,
  WriterResearchObject,
  WriterSnapshot,
  WriterSource,
  WriterShareInfo,
  WriterSummary,
  WriterTemplate,
  WriterTemplateLinkPreview,
  WriterTemplateShare,
  WriterTemplateSharedPreview,
  ZoteroCanaries,
  ZoteroSyncRequest,
  ZoteroSyncResult,
} from "@/lib/types";

/** A run address: the opaque public id (URLs) or the integer key. */
export type RunRef = number | string;
export type WriterRef = number | string;

export const API_URL =
  process.env.NEXT_PUBLIC_SIX_API_URL ?? "http://127.0.0.1:8000";

const TOKEN_KEY = "six_token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string) {
  window.localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken() {
  window.localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(
    status: number,
    message: string,
    detail: unknown = null,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

/**
 * Retry read-only API queries only when another attempt can plausibly recover.
 *
 * A deployment or short network interruption can fail after CORS preflight but
 * before the authenticated request reaches the API. Those failures are exposed
 * as status 0. Server-side 5xx responses are transient too; client, auth and
 * access and resource-limit errors must remain immediate and actionable.
 */
export function retryTransientApiQuery(
  failureCount: number,
  error: unknown,
): boolean {
  if (failureCount >= 5) return false;
  if (error instanceof ApiError) return error.status === 0 || error.status >= 500;
  return !(error instanceof Error && error.name === "AbortError");
}

export function transientApiRetryDelay(attemptIndex: number): number {
  return Math.min(1_000 * 2 ** attemptIndex, 10_000);
}

export type ChatStreamFailureKind = "transport" | "terminal" | "cancelled";

/**
 * A streamed chat can fail in two materially different ways:
 *
 * - `transport`: the live channel disappeared, while persisted history may
 *   still complete the accepted turn.
 * - `terminal`: the server deliberately ended this turn with `turn.failed`.
 * - `cancelled`: the user deliberately stopped the accepted turn.
 *
 * `accepted` becomes true only after a server event proves that the user turn
 * was committed. Callers must never automatically submit an accepted turn a
 * second time.
 */
export class ChatStreamError extends ApiError {
  kind: ChatStreamFailureKind;
  accepted: boolean;

  constructor(
    kind: ChatStreamFailureKind,
    accepted: boolean,
    message: string,
  ) {
    super(0, message);
    this.name = "ChatStreamError";
    this.kind = kind;
    this.accepted = accepted;
  }
}

export type SpecialistStreamFailureKind =
  | "transport"
  | "terminal"
  | "cancelled";

/**
 * Describes why a specialist SSE request ended without a saved result frame.
 *
 * `accepted` is true once the browser observed a successful streaming
 * response. At that point the canonical specialist handler may keep working
 * even when this browser deliberately closes the live channel. A false value
 * only means acceptance was not observed; it cannot prove that a racing
 * request never reached the server. Callers must not present a local abort as
 * proof that server-side work was cancelled.
 */
export class SpecialistStreamError extends ApiError {
  kind: SpecialistStreamFailureKind;
  accepted: boolean;

  constructor(
    kind: SpecialistStreamFailureKind,
    accepted: boolean,
    message: string,
  ) {
    super(0, message);
    this.name = "SpecialistStreamError";
    this.kind = kind;
    this.accepted = accepted;
  }
}

export interface SpecialistStreamOptions {
  /** Stable across every reconnect for one logical specialist turn. */
  turnId?: string;
  /** Cancels this browser's live channel; server cancellation uses agentTurnStop. */
  signal?: AbortSignal;
  /** Called once after the server has durably accepted this turn id. */
  onAccepted?: (turnId: string) => void;
}

export interface SpecialistTurnReplay<T> {
  status: "completed" | "cancelled" | "failed";
  result: T | null;
  message: string | null;
}

export function createSpecialistTurnId(): string {
  return `agent_${globalThis.crypto.randomUUID().replaceAll("-", "")}`;
}

export interface AvailabilityEventDetail {
  kind: AvailabilityErrorKind;
  message: string;
}

function announce(name: string, detail?: unknown) {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent(name, { detail }));
  }
}

function announceAvailabilityError(error: ApiError, sentToken: string | null) {
  const kind = availabilityErrorKind(error.status, error.detail);
  if (!kind || getToken() !== sentToken) return;
  announce("six:availability", {
    kind,
    message: error.message,
  } satisfies AvailabilityEventDetail);
}

async function toApiError(res: Response): Promise<ApiError> {
  let payload: unknown = null;
  if (res.status < 500) {
    try {
      const body: unknown = await res.json();
      const envelope = body !== null && typeof body === "object" && !Array.isArray(body)
        ? body as Record<string, unknown>
        : null;
      const detail = envelope && "detail" in envelope ? envelope.detail : body;
      payload = detail;
    } catch {
      // non-JSON body — keep the plain fallback
    }
  }
  return new ApiError(
    res.status,
    userFacingApiErrorMessage(res.status, payload),
    payload,
  );
}

const CONNECTION_ERROR_MESSAGE =
  "Can't reach SixSentences_ right now. Check your internet connection and try again.";

function connectionError(): ApiError {
  return new ApiError(0, CONNECTION_ERROR_MESSAGE);
}

async function fetchApiResponse(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  try {
    return await fetch(input, init);
  } catch (error) {
    if (error && typeof error === "object" && "name" in error && error.name === "AbortError") {
      throw error;
    }
    throw connectionError();
  }
}

async function readJsonResponse<T>(response: Response): Promise<T> {
  try {
    return await response.json() as T;
  } catch {
    throw new ApiError(
      502,
      "Something went wrong on our side. Please try again in a moment.",
    );
  }
}

async function readBlobResponse(response: Response): Promise<Blob> {
  try {
    return await response.blob();
  } catch {
    throw connectionError();
  }
}

async function readTextResponse(response: Response): Promise<string> {
  try {
    return await response.text();
  } catch {
    throw connectionError();
  }
}

async function request<T>(
  path: string,
  init: {
    method?: string;
    body?: unknown;
    auth?: boolean;
    signal?: AbortSignal;
  } = {},
): Promise<T> {
  const { method = "GET", body, auth = true, signal } = init;
  const headers: Record<string, string> = {};
  const token = auth ? getToken() : null;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (auth) {
    if (token) headers["Authorization"] = `Bearer ${token}`;
  }
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
      // React Query owns the application cache. The browser HTTP cache can
      // otherwise replay an older authenticated response after a mutation.
      cache: auth ? "no-store" : "default",
    });
  } catch (error) {
    if (error && typeof error === "object" && "name" in error && error.name === "AbortError") {
      throw error;
    }
    throw new ApiError(
      0,
      "Can't reach SixSentences_ right now. Check your internet connection and try again.",
    );
  }
  if (!res.ok) {
    const error = await toApiError(res);
    if (res.status === 401 && auth && getToken() === token) {
      clearToken();
      announce("six:unauthorized");
    }
    if (auth && res.status === 402) announceAvailabilityError(error, token);
    throw error;
  }
  if (res.status === 204) return undefined as T;
  return readJsonResponse<T>(res);
}

export interface ChatTurnReplay {
  status: "completed" | "cancelled" | "failed";
  answer: ChatAnswer | null;
  message: string | null;
}

interface ChatEventStreamResult {
  lastEventId: number;
  terminal: ChatTurnReplay | null;
}

/** Parse POST and reconnect frames through one cursor-aware application path. */
async function consumeChatEventStream(
  body: ReadableStream<Uint8Array>,
  turnId: string,
  initialLastEventId: number,
  onEvent: (event: ChatStreamEvent) => void,
): Promise<ChatEventStreamResult> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let lastEventId = initialLastEventId;
  let terminal: ChatTurnReplay | null = null;

  const consumeFrame = (frame: string) => {
    if (!frame || frame.startsWith(":")) return;
    let eventName = "";
    let sseEventId = 0;
    const data: string[] = [];
    for (const line of frame.split("\n")) {
      if (line.startsWith("id:")) {
        const parsed = Number.parseInt(line.slice(3).trim(), 10);
        if (Number.isSafeInteger(parsed) && parsed > 0) sseEventId = parsed;
      }
      if (line.startsWith("event:")) eventName = line.slice(6).trim();
      if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
    }
    if (!eventName || data.length === 0) return;

    let parsed: ChatStreamEvent;
    try {
      parsed = JSON.parse(data.join("\n")) as ChatStreamEvent;
    } catch {
      return;
    }
    const eventId = parsed.id;
    if (
      !Number.isSafeInteger(eventId)
      || eventId <= 0
      || eventId !== sseEventId
      || eventId <= lastEventId
      || parsed.turn_id !== turnId
      || parsed.event !== eventName
    ) return;

    lastEventId = eventId;
    const displayedEvent = parsed.event === "turn.failed"
      ? { ...parsed, id: eventId, message: userFacingAgentFailureMessage() }
      : { ...parsed, id: eventId };
    onEvent(displayedEvent);
    if (parsed.event === "turn.completed") {
      terminal = {
        status: "completed",
        answer: parsed.answer ?? null,
        message: null,
      };
    } else if (parsed.event === "turn.cancelled") {
      terminal = {
        status: "cancelled",
        answer: null,
        message: parsed.message ?? "This answer was stopped.",
      };
    } else if (parsed.event === "turn.failed") {
      terminal = {
        status: "failed",
        answer: null,
        message: userFacingAgentFailureMessage(),
      };
    }
  };

  while (true) {
    let chunk: ReadableStreamReadResult<Uint8Array>;
    try {
      chunk = await reader.read();
    } catch {
      // The returned cursor is durable. The caller resumes strictly after it.
      break;
    }
    const { value, done } = chunk;
    buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, "\n");
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      consumeFrame(buffer.slice(0, boundary));
      buffer = buffer.slice(boundary + 2);
      boundary = buffer.indexOf("\n\n");
    }
    if (done) break;
  }
  if (buffer.trim()) consumeFrame(buffer);
  return { lastEventId, terminal };
}

function chatReplayAnswer(replay: ChatTurnReplay): ChatAnswer {
  if (replay.status === "cancelled") {
    throw new ChatStreamError(
      "cancelled",
      true,
      replay.message ?? "This answer was stopped.",
    );
  }
  if (replay.status === "failed") {
    throw new ChatStreamError(
      "terminal",
      true,
      userFacingAgentFailureMessage(),
    );
  }
  if (!replay.answer) {
    throw new ChatStreamError(
      "transport",
      true,
      "The saved answer is still being finalized.",
    );
  }
  return replay.answer;
}

async function followChatTurnRequest(
  path: string,
  turnId: string,
  onEvent: (event: ChatStreamEvent) => void,
  signal?: AbortSignal,
  initialLastEventId = 0,
): Promise<ChatTurnReplay> {
  let lastEventId = initialLastEventId;
  let reconnectAttempts = 0;
  while (true) {
    if (signal?.aborted) {
      throw new ChatStreamError(
        "transport",
        true,
        "The durable turn will continue in the background.",
      );
    }
    const token = getToken();
    let response: Response;
    try {
      response = await fetch(`${API_URL}${path}`, {
        method: "GET",
        headers: {
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          ...(lastEventId > 0 ? { "Last-Event-ID": String(lastEventId) } : {}),
        },
        signal,
      });
    } catch (error) {
      if (signal?.aborted || (error instanceof Error && error.name === "AbortError")) {
        throw new ChatStreamError(
          "transport",
          true,
          "The durable turn will continue in the background.",
        );
      }
      reconnectAttempts += 1;
      if (await waitForSpecialistReconnect(reconnectAttempts, signal)) continue;
      throw new ChatStreamError("transport", true, "The live answer channel was closed.");
    }

    if (!response.ok) {
      if (response.status === 408 || response.status === 429 || response.status >= 500) {
        reconnectAttempts += 1;
        if (await waitForSpecialistReconnect(reconnectAttempts, signal)) continue;
      }
      const error = await toApiError(response);
      if (response.status === 401 && getToken() === token) {
        clearToken();
        announce("six:unauthorized");
      }
      throw error;
    }
    if (!response.body) {
      reconnectAttempts += 1;
      if (await waitForSpecialistReconnect(reconnectAttempts, signal)) continue;
      throw new ChatStreamError("transport", true, "The live answer channel was closed.");
    }

    reconnectAttempts = 0;
    const consumed = await consumeChatEventStream(
      response.body,
      turnId,
      lastEventId,
      onEvent,
    );
    lastEventId = consumed.lastEventId;
    if (consumed.terminal) return consumed.terminal;
    reconnectAttempts += 1;
    if (await waitForSpecialistReconnect(reconnectAttempts, signal)) continue;
    throw new ChatStreamError("transport", true, "The live answer channel was closed.");
  }
}

async function streamChatRequest(
  path: string,
  followPath: string,
  turnId: string,
  body: unknown,
  onEvent: (event: ChatStreamEvent) => void,
): Promise<ChatAnswer> {
  const token = getToken();
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(body),
    });
  } catch {
    throw new ChatStreamError(
      "transport",
      false,
      "Can't reach SixSentences_ right now. Check your internet connection and try again.",
    );
  }
  if (!response.ok) {
    const error = await toApiError(response);
    if (response.status === 401 && getToken() === token) {
      clearToken();
      announce("six:unauthorized");
    }
    if (response.status === 402) announceAvailabilityError(error, token);
    throw error;
  }

  // HTTP 200 means the server durably owns this turn. From here on, every
  // transport loss resumes the bodyless ledger and never resubmits the POST.
  if (!response.body) {
    return chatReplayAnswer(
      await followChatTurnRequest(followPath, turnId, onEvent),
    );
  }
  const consumed = await consumeChatEventStream(response.body, turnId, 0, onEvent);
  if (consumed.terminal) return chatReplayAnswer(consumed.terminal);
  return chatReplayAnswer(
    await followChatTurnRequest(
      followPath,
      turnId,
      onEvent,
      undefined,
      consumed.lastEventId,
    ),
  );
}

function waitForSpecialistReconnect(
  attempt: number,
  signal?: AbortSignal,
): Promise<boolean> {
  if (signal?.aborted) return Promise.resolve(false);
  const delay = Math.min(1_000, 250 * 2 ** Math.max(0, attempt - 1));
  return new Promise((resolve) => {
    const finish = (continueReconnect: boolean) => {
      globalThis.clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
      resolve(continueReconnect);
    };
    const onAbort = () => finish(false);
    const timer = globalThis.setTimeout(() => finish(true), delay);
    signal?.addEventListener("abort", onAbort, { once: true });
    if (signal?.aborted) finish(false);
  });
}

async function streamSpecialistRequest<T>(
  path: string,
  body: Record<string, unknown>,
  onEvent: (event: SpecialistAgentEvent) => void,
  options: SpecialistStreamOptions = {},
): Promise<T> {
  const signal = options.signal;
  const turnId = options.turnId ?? createSpecialistTurnId();
  const requestBody = { ...body, turn_id: turnId };
  let accepted = false;
  let lastEventId = 0;
  let completed: T | null = null;
  let completedReceived = false;
  let failure = "";
  let cancelled = "";

  const cancellationError = () =>
    new SpecialistStreamError(
      "cancelled",
      accepted,
      accepted
        ? "The live agent channel was closed."
        : "The live request was stopped before server acceptance was confirmed.",
    );

  const consumeFrame = (frame: string) => {
    if (!frame || frame.startsWith(":")) return;
    const data: string[] = [];
    let sseEventId = 0;
    for (const line of frame.split("\n")) {
      if (line.startsWith("id:")) {
        const parsed = Number.parseInt(line.slice(3).trim(), 10);
        if (Number.isSafeInteger(parsed) && parsed > 0) sseEventId = parsed;
      }
      if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
    }
    if (data.length === 0) return;
    try {
      const event = JSON.parse(data.join("\n")) as SpecialistAgentEvent;
      if (event.turn_id && event.turn_id !== turnId) return;
      const eventId = Number.isSafeInteger(event.id) && event.id > 0
        ? event.id
        : sseEventId;
      if (eventId > 0 && eventId <= lastEventId) return;
      const displayedEvent = event.event === "turn.failed"
        ? { ...event, message: userFacingAgentFailureMessage() }
        : event;
      onEvent(displayedEvent);
      if (eventId > 0) lastEventId = eventId;
      if (event.event === "turn.completed" && event.result !== undefined) {
        completed = event.result as T;
        completedReceived = true;
      }
      if (event.event === "turn.cancelled") {
        cancelled = event.message ?? "The specialist turn was cancelled.";
      }
      if (event.event === "turn.failed") {
        failure = userFacingAgentFailureMessage();
      }
    } catch {
      // A malformed frame remains replayable because its cursor is not advanced.
    }
  };

  const terminalResult = (): T | null => {
    if (cancelled) {
      throw new SpecialistStreamError("cancelled", true, cancelled);
    }
    if (failure) {
      throw new SpecialistStreamError("terminal", true, failure);
    }
    return completedReceived ? completed : null;
  };

  const followAcceptedTurn = async (): Promise<T> => {
    const replay = await streamSpecialistTurnEvents<T>(
      turnId,
      onEvent,
      signal,
      lastEventId,
    );
    if (replay.status === "cancelled") {
      throw new SpecialistStreamError(
        "cancelled",
        true,
        replay.message ?? "The specialist turn was cancelled.",
      );
    }
    if (replay.status === "failed") {
      throw new SpecialistStreamError(
        "terminal",
        true,
        userFacingAgentFailureMessage(),
      );
    }
    return replay.result as T;
  };

  if (signal?.aborted) throw cancellationError();
  const token = getToken();
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(requestBody),
      signal,
    });
  } catch (error) {
    if (signal?.aborted || (error instanceof Error && error.name === "AbortError")) {
      throw cancellationError();
    }
    throw new SpecialistStreamError(
      "transport",
      false,
      "Can't reach SixSentences_ right now. Check your internet connection and try again.",
    );
  }

  if (!response.ok) {
    const error = await toApiError(response);
    if (response.status === 401 && getToken() === token) {
      clearToken();
      announce("six:unauthorized");
    }
    if (response.status === 402) announceAvailabilityError(error, token);
    throw error;
  }
  accepted = true;
  options.onAccepted?.(turnId);

  if (!response.body) return followAcceptedTurn();

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, "\n");
      let boundary = buffer.indexOf("\n\n");
      while (boundary >= 0) {
        consumeFrame(buffer.slice(0, boundary));
        buffer = buffer.slice(boundary + 2);
        boundary = buffer.indexOf("\n\n");
      }
      if (done) break;
    }
  } catch (error) {
    const terminal = terminalResult();
    if (terminal !== null) return terminal;
    if (signal?.aborted || (error instanceof Error && error.name === "AbortError")) {
      throw cancellationError();
    }
    return followAcceptedTurn();
  }
  if (buffer.trim()) consumeFrame(buffer);
  const terminal = terminalResult();
  if (terminal !== null) return terminal;
  if (signal?.aborted) throw cancellationError();
  return followAcceptedTurn();
}

async function streamSpecialistTurnEvents<T>(
  turnId: string,
  onEvent: (event: SpecialistAgentEvent) => void,
  signal?: AbortSignal,
  initialLastEventId = 0,
): Promise<SpecialistTurnReplay<T>> {
  let reconnectAttempts = 0;
  let lastEventId = initialLastEventId;

  while (true) {
    if (signal?.aborted) {
      throw new SpecialistStreamError(
        "cancelled",
        true,
        "The live agent channel was closed.",
      );
    }
    const token = getToken();
    let response: Response;
    try {
      response = await fetch(
        `${API_URL}/agent/turns/${encodeURIComponent(turnId)}/events/stream`,
        {
          method: "GET",
          headers: {
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
            ...(lastEventId > 0 ? { "Last-Event-ID": String(lastEventId) } : {}),
          },
          signal,
        },
      );
    } catch (error) {
      if (signal?.aborted || (error instanceof Error && error.name === "AbortError")) {
        throw new SpecialistStreamError(
          "cancelled",
          true,
          "The live agent channel was closed.",
        );
      }
      reconnectAttempts += 1;
      if (await waitForSpecialistReconnect(reconnectAttempts, signal)) continue;
      throw new SpecialistStreamError("cancelled", true, "The live agent channel was closed.");
    }

    if (!response.ok) {
      if (response.status >= 500 || response.status === 408 || response.status === 429) {
        reconnectAttempts += 1;
        if (await waitForSpecialistReconnect(reconnectAttempts, signal)) continue;
      }
      throw await toApiError(response);
    }
    reconnectAttempts = 0;
    if (!response.body) {
      reconnectAttempts += 1;
      if (await waitForSpecialistReconnect(reconnectAttempts, signal)) continue;
      throw new SpecialistStreamError("cancelled", true, "The live agent channel was closed.");
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let terminal: SpecialistTurnReplay<T> | null = null;
    const consumeFrame = (frame: string) => {
      if (!frame || frame.startsWith(":")) return;
      const data: string[] = [];
      let sseEventId = 0;
      for (const line of frame.split("\n")) {
        if (line.startsWith("id:")) {
          const parsed = Number.parseInt(line.slice(3).trim(), 10);
          if (Number.isSafeInteger(parsed) && parsed > 0) sseEventId = parsed;
        }
        if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
      }
      if (data.length === 0) return;
      try {
        const event = JSON.parse(data.join("\n")) as SpecialistAgentEvent;
        if (event.turn_id && event.turn_id !== turnId) return;
        const eventId = Number.isSafeInteger(event.id) && event.id > 0
          ? event.id
          : sseEventId;
        if (eventId > 0 && eventId <= lastEventId) return;
        const displayedEvent = event.event === "turn.failed"
          ? { ...event, message: userFacingAgentFailureMessage() }
          : event;
        onEvent(displayedEvent);
        if (eventId > 0) lastEventId = eventId;
        if (event.event === "turn.completed") {
          terminal = {
            status: "completed",
            result: (event.result ?? null) as T | null,
            message: null,
          };
        } else if (event.event === "turn.cancelled") {
          terminal = {
            status: "cancelled",
            result: null,
            message: event.message ?? "The specialist turn was cancelled.",
          };
        } else if (event.event === "turn.failed") {
          terminal = {
            status: "failed",
            result: null,
            message: userFacingAgentFailureMessage(),
          };
        }
      } catch {
        // Invalid frames remain replayable because their cursor is not advanced.
      }
    };

    try {
      while (true) {
        const { value, done } = await reader.read();
        buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, "\n");
        let boundary = buffer.indexOf("\n\n");
        while (boundary >= 0) {
          consumeFrame(buffer.slice(0, boundary));
          buffer = buffer.slice(boundary + 2);
          boundary = buffer.indexOf("\n\n");
        }
        if (done) break;
      }
    } catch (error) {
      if (terminal) return terminal;
      if (signal?.aborted || (error instanceof Error && error.name === "AbortError")) {
        throw new SpecialistStreamError(
          "cancelled",
          true,
          "The live agent channel was closed.",
        );
      }
    }
    if (buffer.trim()) consumeFrame(buffer);
    if (terminal) return terminal;
    reconnectAttempts += 1;
    if (await waitForSpecialistReconnect(reconnectAttempts, signal)) continue;
    throw new SpecialistStreamError("cancelled", true, "The live agent channel was closed.");
  }
}

export const api = {
  browserCapturePair: (body: {
    code_challenge: string;
    state: string;
    redirect_uri: string;
    device_name: string;
  }) =>
    request<{ callback_url: string; state: string; expires_in_seconds: number }>(
      "/browser-capture/pair",
      { method: "POST", body },
    ),
  browserCaptureDevices: () =>
    request<{ devices: BrowserCaptureDevice[] }>("/browser-capture/devices"),
  browserCaptureRevision: () =>
    request<BrowserCaptureRevision>("/browser-capture/revision"),
  revokeBrowserCaptureDevice: (id: number) =>
    request<{ id: number; revoked: boolean }>(`/browser-capture/devices/${id}`, {
      method: "DELETE",
    }),
  // -- public ---------------------------------------------------------------
  health: () => request<Health>("/health", { auth: false }),
  agentTurnStatus: (turnId: string) =>
    request<AgentTurnState>(`/agent/turns/${encodeURIComponent(turnId)}`),
  agentTurnActive: (resourceKind: string, resourceId: string) =>
    request<AgentTurnState | null>(
      `/agent/turns/active?resource_kind=${encodeURIComponent(resourceKind)}&resource_id=${encodeURIComponent(resourceId)}`,
    ),
  agentTurnLatest: (resourceKind: string, resourceId: string) =>
    request<AgentTurnState | null>(
      `/agent/turns/latest?resource_kind=${encodeURIComponent(resourceKind)}&resource_id=${encodeURIComponent(resourceId)}`,
    ),
  agentTurnEventsStream: <T>(
    turnId: string,
    onEvent: (event: SpecialistAgentEvent) => void,
    signal?: AbortSignal,
  ) => streamSpecialistTurnEvents<T>(turnId, onEvent, signal),
  agentTurnStop: (turnId: string) =>
    request<AgentTurnStopResult>(
      `/agent/turns/${encodeURIComponent(turnId)}/stop`,
      { method: "POST" },
    ),
  models: () => request<ChatModelCatalog>("/models"),
  screeningMethods: () =>
    request<ScreeningMethod[]>("/screening-methods", { auth: false }),
  publicSurvey: (id: string) =>
    request<PublicSurvey>(`/public/surveys/${id}`, { auth: false }),
  accessPublicSurvey: (id: string, password: string) =>
    request<PublicSurvey>(`/public/surveys/${id}/access`, {
      method: "POST",
      body: { password },
      auth: false,
    }),
  submitSurveyResponse: (
    id: string,
    answers: Record<string, unknown>,
    respondentLabel = "",
    password = "",
    participantInformationFingerprint = "",
    consent = false,
  ) =>
    request<{ id: string; confirmation: string }>(
      `/public/surveys/${id}/responses`,
      {
        method: "POST",
        body: { answers, respondent_label: respondentLabel, password, participant_information_fingerprint: participantInformationFingerprint, consent },
        auth: false,
      },
    ),
  register: (
    name: string,
    email: string,
    password: string,
    orgName: string,
    legal: import("@/lib/legal").SignupLegalAcceptance,
  ) =>
    request<RegisterResponse>("/auth/register", {
      method: "POST",
      body: { name, email, password, org_name: orgName, ...legal },
      auth: false,
    }),
  verifyEmail: (token: string) =>
    request<{ token: string; email: string; org_id: number }>("/auth/verify", {
      method: "POST",
      body: { token },
      auth: false,
    }),
  resendVerification: (email: string) =>
    request<{ note: string }>("/auth/verification/resend", {
      method: "POST",
      body: { email },
      auth: false,
    }),
  login: (email: string, password: string) =>
    request<LoginResponse>("/auth/login", {
      method: "POST",
      body: { email, password },
      auth: false,
    }),
  googleAuth: (
    credential: string,
    intent: "login" | "signup",
    orgName = "",
    legal?: import("@/lib/legal").SignupLegalAcceptance,
  ) =>
    request<GoogleAuthResponse>("/auth/google", {
      method: "POST",
      body: { credential, intent, org_name: orgName, ...legal },
      auth: false,
    }),
  verifyTwoFactorLogin: (challenge: string, code: string) =>
    request<{ token: string }>("/auth/2fa/verify", {
      method: "POST",
      body: { challenge, code },
      auth: false,
    }),
  forgotPassword: (email: string) =>
    request<{ note: string }>("/auth/forgot", {
      method: "POST",
      body: { email },
      auth: false,
    }),
  resetPassword: (token: string, password: string) =>
    request<{ note: string }>("/auth/reset", {
      method: "POST",
      body: { token, password },
      auth: false,
    }),

  // -- account --------------------------------------------------------------
  me: () => request<Me>("/auth/me"),
  acceptCurrentLegalTerms: (body: {
    age_requirement_confirmed: boolean;
    terms_accepted: boolean;
    terms_version: string;
    privacy_acknowledged: boolean;
    privacy_version: string;
    dpa_accepted: boolean;
    dpa_version: string;
    controller_name: string;
    controller_authority_confirmed: boolean;
  }) =>
    request<{
      terms_version: string;
      privacy_version: string;
      dpa_version: string | null;
      accepted_at: string;
    }>("/auth/legal-acceptance", { method: "POST", body }),
  recordPrivacyNotice: (privacy_version: string) =>
    request<{ privacy_version: string }>("/auth/privacy-notice", {
      method: "POST", body: { privacy_version },
    }),
  updatePreferences: (preferences: {
    language?: "en" | "de";
    assistant_preferences?: AssistantPreferences;
  }) =>
    request<{
      language: "en" | "de";
      assistant_preferences: AssistantPreferences;
    }>("/auth/preferences", {
      method: "PATCH",
      body: preferences,
    }),
  markOnboarded: () =>
    request<{ onboarded: boolean }>("/auth/onboarded", { method: "POST" }),
  changePassword: (currentPassword: string, newPassword: string) =>
    request<{ note: string }>("/auth/password", {
      method: "POST",
      body: { current_password: currentPassword, new_password: newPassword },
    }),
  twoFactorStatus: () => request<TwoFactorStatus>("/auth/2fa"),
  setupTwoFactor: (password: string) =>
    request<TwoFactorSetup>("/auth/2fa/setup", {
      method: "POST",
      body: { password },
    }),
  enableTwoFactor: (code: string) =>
    request<{ enabled: true; recovery_codes: string[] }>("/auth/2fa/enable", {
      method: "POST",
      body: { code },
    }),
  disableTwoFactor: (password: string, code: string) =>
    request<{ enabled: false }>("/auth/2fa/disable", {
      method: "POST",
      body: { password, code },
    }),
  regenerateTwoFactorRecoveryCodes: (password: string, code: string) =>
    request<{ recovery_codes: string[] }>("/auth/2fa/recovery-codes", {
      method: "POST",
      body: { password, code },
    }),
  createApiKey: (name: string, scopes: ApiKeyScope[], expiresInDays: number) =>
    request<{
      api_key: string;
      scopes: ApiKeyScope[];
      expires_in_days: number;
      note: string;
    }>("/auth/api-keys", {
      method: "POST",
      body: { name, scopes, expires_in_days: expiresInDays },
    }),
  apiKeyScopes: () => request<ApiKeyScopeDefinition[]>("/auth/api-key-scopes"),
  listApiKeys: async () => {
    type ApiKeyResponse = Omit<ApiKey, "scopes" | "expires_at"> & {
      scopes?: ApiKeyScope[];
      expires_at?: string | null;
    };
    const keys = await request<ApiKeyResponse[]>("/auth/api-keys");
    return keys.map((key): ApiKey => ({
      ...key,
      // Keys created before scoped credentials were introduced are treated
      // as read-only during the rotation grace period. Normalising here also
      // keeps staggered frontend/backend deploys from breaking Settings.
      scopes: key.scopes?.length ? key.scopes : ["research:read"],
      expires_at: key.expires_at ?? null,
    }));
  },
  revokeApiKey: (id: number) =>
    request<{ revoked: number }>(`/auth/api-keys/${id}`, { method: "DELETE" }),
  listMembers: () => request<Member[]>("/orgs/current/users"),
  addMember: (email: string, password: string, role: string) =>
    request<{ id: number; email: string; role: string }>("/orgs/current/users", {
      method: "POST",
      body: { email, password, role },
    }),
  deleteAllRuns: () =>
    request<{ deleted_runs: number }>("/runs", { method: "DELETE" }),
  deleteAccount: (password: string) =>
    request<{ deleted: "account" | "workspace" }>("/auth/account", {
      method: "DELETE",
      body: { password },
    }),

  // -- webhooks ---------------------------------------------------------------
  createWebhook: (url: string, events: string[], secret = "") =>
    request<WebhookCreated>("/webhooks", {
      method: "POST",
      body: { url, events, secret },
    }),
  listWebhooks: () => request<Webhook[]>("/webhooks"),
  deleteWebhook: (id: number) =>
    request<{ deleted: number }>(`/webhooks/${id}`, { method: "DELETE" }),

  // -- research features ------------------------------------------------------
  probeRun: (runId: RunRef, query: string) =>
    request<ProbeResult>(`/runs/${runId}/probe`, { method: "POST", body: { query } }),
  queryTranslations: (runId: RunRef) =>
    request<QueryTranslations>(`/runs/${runId}/query-translations`),
  runDiff: (runId: RunRef, against: string) =>
    request<RunDiff>(
      `/runs/${runId}/diff?against=${encodeURIComponent(against)}`,
    ),
  createImport: (filename: string, label: string, text: string) =>
    request<ImportBatch>("/imports", { method: "POST", body: { filename, label, text } }),
  listImports: () => request<ImportBatch[]>("/imports"),
  deleteImport: (id: number) =>
    request<{ ok: boolean }>(`/imports/${id}`, { method: "DELETE" }),
  referenceConnectors: () =>
    request<ReferenceConnector[]>("/reference-connectors"),
  createZoteroConnector: (body: {
    name: string;
    api_key: string;
    library_type: "user" | "group";
    library_id: string;
    collection_key?: string;
  }) =>
    request<ReferenceConnector>("/reference-connectors/zotero", {
      method: "POST",
      body,
    }),
  createCitaviConnector: (name: string) =>
    request<ReferenceConnector>("/reference-connectors/citavi", {
      method: "POST",
      body: { name },
    }),
  syncReferenceConnector: (id: string) =>
    request<ReferenceConnector>(`/reference-connectors/${id}/sync`, {
      method: "POST",
    }),
  importCitaviConnector: (
    id: string,
    body: { filename: string; text: string; replace?: boolean },
  ) =>
    request<ReferenceConnector>(`/reference-connectors/${id}/import`, {
      method: "POST",
      body,
    }),
  deleteReferenceConnector: (id: string) =>
    request<{ deleted: string }>(`/reference-connectors/${id}`, {
      method: "DELETE",
    }),
  pushReferenceConnector: (
    runId: RunRef,
    connectorId: string,
    includedOnly: boolean,
  ) =>
    request<ReferenceConnectorPushResult>(
      `/runs/${runId}/reference-connectors/${connectorId}`,
      { method: "POST", body: { included_only: includedOnly } },
    ),
  zoteroCanaries: (apiKey: string, libraryType: string, libraryId: string) =>
    request<ZoteroCanaries>("/zotero/canaries", {
      method: "POST",
      body: { api_key: apiKey, library_type: libraryType, library_id: libraryId },
    }),
  startExtraction: (runId: RunRef, fields: string[] = [], force = false) =>
    request<{ scheduled: number; fields: string[] }>(`/runs/${runId}/extraction`, {
      method: "POST",
      body: { fields, force },
    }),
  extraction: (runId: RunRef) => request<EvidenceTable>(`/runs/${runId}/extraction`),
  editExtraction: (runId: RunRef, workId: string, field: string, value: string) =>
    request<{ work_id: string; field: string; payload: EvidenceCell }>(
      `/runs/${runId}/extraction/${workId}`,
      { method: "PATCH", body: { field, value } },
    ),
  updateExtractionSchema: (
    runId: RunRef,
    body: {
      name: string;
      fields: string[];
      reviewer_mode: "single" | "double" | "blinded";
      instructions: string;
    },
  ) =>
    request<EvidenceTable["schema"]>(`/runs/${runId}/extraction-schema`, {
      method: "PATCH",
      body,
    }),
  reviewExtraction: (
    runId: RunRef,
    workId: string,
    body: {
      field: string;
      round: 1 | 2;
      verdict: "confirmed" | "corrected" | "needs_attention" | "conflict";
      value?: string;
      note?: string;
    },
  ) =>
    request<{ work_id: string; field: string; payload: EvidenceCell }>(
      `/runs/${runId}/extraction/${encodeURIComponent(workId)}/review`,
      { method: "PATCH", body },
    ),

  // -- writer -----------------------------------------------------------------
  // documents are addressed by their opaque public_id in URLs; the numeric
  // key still resolves for anything older
  writerList: () => request<WriterSummary[]>("/writer"),
  writerCreate: (
    title: string,
    template: string,
    runIds: number[] = [],
    templateId?: number,
    projectId?: number,
    objective?: string,
  ) =>
    request<WriterDocument>("/writer", {
      method: "POST",
      body: {
        title,
        template,
        run_ids: runIds,
        ...(templateId ? { template_id: templateId } : {}),
        ...(projectId ? { project_id: projectId } : {}),
        ...(objective?.trim() ? { objective: objective.trim() } : {}),
      },
    }),
  writerGet: (id: WriterRef) => request<WriterDocument>(`/writer/${id}`),
  writerPatch: (
    id: WriterRef,
    body: {
      title?: string;
      content?: string;
      run_ids?: number[];
      dataset_ids?: string[];
      project_id?: number | null;
      expected_revision?: number;
      base_content?: string;
    },
  ) => request<WriterDocument>(`/writer/${id}`, { method: "PATCH", body }),
  writerDelete: (id: WriterRef) =>
    request<{ ok: boolean }>(`/writer/${id}`, { method: "DELETE" }),
  writerCitations: (id: WriterRef) =>
    request<WriterCitation[]>(`/writer/${id}/citations`),
  writerCompile: (id: WriterRef) =>
    request<WriterCompile>(`/writer/${id}/compile`, { method: "POST" }),
  writerCreateSlides: (id: WriterRef) =>
    request<WriterSummary>(`/writer/${id}/slides`, { method: "POST" }),
  writerShare: (id: WriterRef) => request<WriterShareInfo>(`/writer/${id}/share`),
  writerShareCreate: (id: WriterRef, password = "") =>
    request<WriterShareInfo>(`/writer/${id}/share`, {
      method: "POST",
      body: { password },
    }),
  writerShareDelete: (id: WriterRef) =>
    request<{ ok: boolean }>(`/writer/${id}/share`, { method: "DELETE" }),
  writerShareSetPassword: (id: WriterRef, password: string) =>
    request<WriterShareInfo>(`/writer/${id}/share/password`, {
      method: "PUT",
      body: { password },
    }),
  writerShareRemovePassword: (id: WriterRef) =>
    request<WriterShareInfo>(`/writer/${id}/share/password`, { method: "DELETE" }),
  writerComments: (id: WriterRef) =>
    request<WriterComment[]>(`/writer/${id}/comments`),
  writerCollaborators: (id: WriterRef) =>
    request<WriterCollaboratorCatalog>(`/writer/${id}/collaborators`),
  writerCollaboratorSet: (
    id: WriterRef,
    userId: number,
    role: "editor" | "reviewer" | "viewer" | "none",
  ) =>
    request<WriterCollaborator>(`/writer/${id}/collaborators`, {
      method: "PUT",
      body: { user_id: userId, role },
    }),
  writerPresence: (id: WriterRef) =>
    request<WriterPresence[]>(`/writer/${id}/presence`),
  writerPresenceUpdate: (
    id: WriterRef,
    body: {
      path: string;
      line: number;
      mode: "source" | "preview" | "chat" | "log";
    },
  ) =>
    request<{ ok: boolean; last_seen_at: string }>(`/writer/${id}/presence`, {
      method: "POST",
      body,
    }),
  writerRetargetPreview: (
    id: WriterRef,
    body: {
      template: string;
      template_id?: number;
      title?: string;
      expected_revision?: number;
    },
  ) =>
    request<WriterRetargetPreview>(`/writer/${id}/retarget/preview`, {
      method: "POST",
      body,
    }),
  writerRetarget: (
    id: WriterRef,
    body: {
      template: string;
      template_id?: number;
      title?: string;
      expected_revision?: number;
    },
  ) =>
    request<WriterDocument>(`/writer/${id}/retarget`, {
      method: "POST",
      body,
    }),
  writerCommentCreate: (
    id: WriterRef,
    body: {
      quote?: string;
      page?: number | null;
      anchor_prefix?: string;
      anchor_suffix?: string;
      anchor_revision?: string;
      content: string;
    },
  ) =>
    request<WriterComment>(`/writer/${id}/comments`, {
      method: "POST",
      body,
    }),
  writerCommentResolve: (id: WriterRef, commentId: number) =>
    request<WriterComment>(`/writer/${id}/comments/${commentId}/resolve`, {
      method: "POST",
    }),
  writerSnapshots: (id: WriterRef) =>
    request<WriterSnapshot[]>(`/writer/${id}/snapshots`),
  writerSnapshotDiff: (id: WriterRef, snapshotId: number) =>
    request<{ id: number; summary: string; files: { path: string; diff: string }[] }>(
      `/writer/${id}/snapshots/${snapshotId}/diff`,
    ),
  writerRestore: (id: WriterRef, snapshotId: number) =>
    request<WriterDocument>(`/writer/${id}/restore/${snapshotId}`, {
      method: "POST",
    }),
  writerChatHistory: (id: WriterRef) =>
    request<WriterMessage[]>(`/writer/${id}/chat`),
  writerChatSend: (
    id: WriterRef,
    message: string,
    selection?: WriterSelection | null,
    model?: string,
    activePath = "main.tex",
  ) =>
    request<{
      id: number;
      reply: string;
      edits: WriterMessage["payload"]["edits"];
      verification: WriterMessage["payload"]["verification"];
      visual_request: WriterMessage["payload"]["visual_request"];
      workspace_actions: WriterMessage["payload"]["workspace_actions"];
      artifacts?: WriterMessage["payload"]["artifacts"];
      agent_events?: SpecialistAgentEvent[];
    }>(
      `/writer/${id}/chat`,
      {
        method: "POST",
        body: {
          message,
          ...(model ? { model } : {}),
          active_path: activePath,
          ...(selection ? { selection } : {}),
        },
      },
    ),
  writerChatStream: (
    id: WriterRef,
    message: string,
    selection: WriterSelection | null | undefined,
    model: string | undefined,
    activePath: string,
    onEvent: (event: SpecialistAgentEvent) => void,
    options: SpecialistStreamOptions = {},
  ) =>
    streamSpecialistRequest<{
      id: number;
      reply: string;
      edits: WriterMessage["payload"]["edits"];
      verification: WriterMessage["payload"]["verification"];
      visual_request: WriterMessage["payload"]["visual_request"];
      workspace_actions: WriterMessage["payload"]["workspace_actions"];
      artifacts?: WriterMessage["payload"]["artifacts"];
    }>(
      `/writer/${id}/chat/stream`,
      {
        message,
        ...(model ? { model } : {}),
        active_path: activePath,
        ...(selection ? { selection } : {}),
      },
      onEvent,
      options,
    ),
  writerApplyEdits: (
    id: WriterRef,
    edits: NonNullable<WriterMessage["payload"]["edits"]>,
    opts?: { auto?: boolean; messageId?: number | null },
  ) =>
    request<{
      applied: number[];
      skipped: { index: number; reason: string; occurrences?: number }[];
      files: { path: string; content: string; revision: number }[];
    }>(`/writer/${id}/edits/apply`, {
      method: "POST",
      body: {
        edits: edits.map(({ path, find, replace }) => ({ path, find, replace })),
        auto: Boolean(opts?.auto),
        message_id: opts?.messageId ?? null,
      },
    }),
  writerRejectEdit: (id: WriterRef, messageId: number, index: number) =>
    request<{
      message_id: number;
      index: number;
      status: "rejected";
      superseded: number[];
    }>(`/writer/${id}/edits/reject`, {
      method: "POST",
      body: { message_id: messageId, index },
    }),
  writerAudit: (id: WriterRef) => request<WriterAudit>(`/writer/${id}/audit`),
  writerResearchObjects: (id: WriterRef) =>
    request<WriterResearchObject[]>(`/writer/${id}/research-objects`),
  writerSyncSource: (id: WriterRef, path: string, line: number, column = 1) =>
    request<{ page: number; x: number; y: number; width: number; height: number }>(
      `/writer/${id}/sync/source?path=${encodeURIComponent(path)}&line=${line}&column=${column}`,
    ),
  writerSyncPdf: (id: WriterRef, page: number, x: number, y: number) =>
    request<{ path: string; line: number; column: number }>(
      `/writer/${id}/sync/pdf?page=${page}&x=${x}&y=${y}`,
    ),
  writerAssets: (id: WriterRef) => request<WriterAsset[]>(`/writer/${id}/assets`),
  writerAssetUpload: (id: WriterRef, filename: string, contentBase64: string) =>
    request<WriterAsset>(`/writer/${id}/assets`, {
      method: "POST",
      body: { filename, content_base64: contentBase64 },
    }),
  writerAssetDelete: (id: WriterRef, assetId: number) =>
    request<{ ok: boolean }>(`/writer/${id}/assets/${assetId}`, {
      method: "DELETE",
    }),
  writerSources: (id: WriterRef) =>
    request<WriterSource[]>(`/writer/${id}/sources`),
  writerInterviewContexts: (id: WriterRef) =>
    request<WriterInterviewContextCatalog>(`/writer/${id}/interview-contexts`),
  writerInterviewContextsSet: (
    id: WriterRef,
    sources: Array<{
      interview_id: string;
      mode: WriterInterviewContextMode;
      include_methodology: boolean;
    }>,
  ) =>
    request<WriterInterviewContextCatalog>(`/writer/${id}/interview-contexts`, {
      method: "PUT",
      body: { sources },
    }),
  writerSurveyContexts: (id: WriterRef) =>
    request<WriterSurveyContextCatalog>(`/writer/${id}/survey-contexts`),
  writerSurveyContextsSet: (
    id: WriterRef,
    sources: Array<{
      survey_id: string;
      mode: WriterSurveyContextMode;
    }>,
  ) =>
    request<WriterSurveyContextCatalog>(`/writer/${id}/survey-contexts`, {
      method: "PUT",
      body: { sources },
    }),
  writerSourceUpload: (
    id: WriterRef,
    filename: string,
    contentBase64: string,
    metadata?: { title?: string; authors?: string[]; year?: number; doi?: string },
  ) =>
    request<WriterSource[]>(`/writer/${id}/sources`, {
      method: "POST",
      body: { filename, content_base64: contentBase64, ...metadata },
    }),
  writerLibrarySourcesAdd: (id: WriterRef, documentIds: number[]) =>
    request<{ created: number; skipped: number; sources: WriterSource[] }>(
      `/writer/${id}/sources/library`,
      {
        method: "POST",
        body: { document_ids: documentIds },
      },
    ),
  writerSourceDelete: (id: WriterRef, sourceId: number) =>
    request<{ ok: boolean }>(`/writer/${id}/sources/${sourceId}`, {
      method: "DELETE",
    }),
  writerArtifact: (id: WriterRef, kind: "evidence" | "methods" | "prisma", runId: number) =>
    request<{ kind: string; latex: string }>(
      `/writer/${id}/artifacts/${kind}?run=${runId}`,
    ),
  writerLogContribution: (
    id: WriterRef,
    events: {
      kind: "ai_edit_applied";
      chars_added: number;
      chars_removed: number;
      auto: boolean;
      message_id: number | null;
    }[],
  ) =>
    request<{ logged: number }>(`/writer/${id}/contributions`, {
      method: "POST",
      body: { events },
    }),
  writerContributionLog: (id: WriterRef) =>
    request<WriterContributionLog>(`/writer/${id}/contribution-log`),
  writerTemplates: () => request<WriterTemplate[]>("/writer/templates"),
  writerTemplateLinkPreview: (url: string) =>
    request<WriterTemplateLinkPreview>("/writer/templates/link/preview", {
      method: "POST",
      body: { url },
    }),
  writerTemplateLinkImport: (url: string, rightsConfirmed: boolean) =>
    request<WriterTemplate>("/writer/templates/link", {
      method: "POST",
      body: { url, rights_confirmed: rightsConfirmed },
    }),
  writerTemplateCreate: (name: string, content: string) =>
    request<{ id: number; name: string; chars: number }>("/writer/templates", {
      method: "POST",
      body: { name, content },
    }),
  writerTemplateDelete: (id: number) =>
    request<{ ok: boolean }>(`/writer/templates/${id}`, { method: "DELETE" }),
  writerTemplateShare: (id: number) =>
    request<WriterTemplateShare>(`/writer/templates/${id}/share`, {
      method: "POST",
    }),
  writerTemplateShareRevoke: (id: number) =>
    request<{ ok: boolean }>(`/writer/templates/${id}/share`, {
      method: "DELETE",
    }),
  writerTemplateSharedPreview: (token: string) =>
    request<WriterTemplateSharedPreview>(`/writer/templates/shared/${token}`),
  writerTemplateSharedImport: (token: string) =>
    request<WriterTemplate>(`/writer/templates/shared/${token}/import`, {
      method: "POST",
    }),
  writerImport: (filename: string, contentBase64: string, projectId?: number) =>
    request<WriterDocument>("/writer/import", {
      method: "POST",
      body: {
        filename,
        content_base64: contentBase64,
        ...(projectId ? { project_id: projectId } : {}),
      },
    }),
  writerFiles: (id: WriterRef) =>
    request<WriterProjectFile[]>(`/writer/${id}/files`),
  writerFileCreate: (id: WriterRef, path: string, content = "") =>
    request<WriterProjectFile>(`/writer/${id}/files`, {
      method: "POST",
      body: { path, content },
    }),
  writerFilePatch: (
    id: WriterRef,
    fileId: number,
    content: string,
    expectedRevision?: number,
    baseContent?: string,
  ) =>
    request<WriterProjectFile>(`/writer/${id}/files/${fileId}`, {
      method: "PATCH",
      body: {
        content,
        ...(expectedRevision ? { expected_revision: expectedRevision } : {}),
        ...(baseContent !== undefined ? { base_content: baseContent } : {}),
      },
    }),
  writerFileRename: (id: WriterRef, fileId: number, path: string) =>
    request<WriterProjectFile>(`/writer/${id}/files/${fileId}`, {
      method: "PATCH",
      body: { path },
    }),
  writerFileDelete: (id: WriterRef, fileId: number) =>
    request<{ ok: boolean }>(`/writer/${id}/files/${fileId}`, {
      method: "DELETE",
    }),

  // -- research data ---------------------------------------------------------
  datasets: () => request<ResearchDataset[]>("/datasets"),
  dataset: (id: string) => request<ResearchDataset>(`/datasets/${id}`),
  datasetCreate: (
    filename: string,
    contentBase64: string | null,
    metadata: {
      name?: string;
      description?: string;
      provenance?: string;
      license?: string;
      project_id?: number;
    },
  ) =>
    request<ResearchDataset>("/datasets", {
      method: "POST",
      body: { filename, content_base64: contentBase64, ...metadata },
    }),
  datasetDelete: (id: string) =>
    request<{ ok: boolean }>(`/datasets/${id}`, { method: "DELETE" }),
  datasetUpdate: (
    id: string,
    body: Partial<
      Pick<
        ResearchDataset,
        "name" | "description" | "provenance" | "license" | "project_id"
      >
    >,
  ) => request<ResearchDataset>(`/datasets/${id}`, { method: "PATCH", body }),
  datasetVersions: (id: string) =>
    request<DatasetVersion[]>(`/datasets/${id}/versions`),
  datasetChatHistory: (id: string) =>
    request<DatasetMessage[]>(`/datasets/${id}/chat`),
  datasetChat: (id: string, question: string, model = "auto") =>
    request<DatasetChatReply>(`/datasets/${id}/chat`, {
      method: "POST",
      body: { question, model },
    }),
  datasetChatStream: (
    id: string,
    question: string,
    model: string,
    onEvent: (event: SpecialistAgentEvent) => void,
    options: SpecialistStreamOptions = {},
  ) =>
    streamSpecialistRequest<DatasetChatReply>(
      `/datasets/${id}/chat/stream`,
      { question, model },
      onEvent,
      options,
    ),
  datasetMetaChart: (
    id: string,
    body: {
      kind: "forest" | "funnel";
      label_column: string;
      effect_column: string;
      se_column: string;
      title: string;
    },
  ) => request<Figure>(`/datasets/${id}/meta-chart`, { method: "POST", body }),
  analysisLatex: (id: string) =>
    request<{ public_id: string; name: string; kind: string; latex: string }>(
      `/analyses/${id}/latex`,
    ),
  datasetVersionAdd: (id: string, filename: string, contentBase64: string, note = "") =>
    request<{ id: number; version: number; checksum: string; row_count: number; note: string }>(
      `/datasets/${id}/versions`,
      { method: "POST", body: { filename, content_base64: contentBase64, note } },
    ),
  datasetChart: (
    id: string,
    body: { x_column: string; y_column: string; kind: string; title: string },
  ) => request<Figure>(`/datasets/${id}/chart`, { method: "POST", body }),

  // -- interviews ------------------------------------------------------------
  interviews: () => request<Interview[]>("/interviews"),
  interview: (id: string) => request<Interview>(`/interviews/${id}`),
  interviewCreate: (
    filename: string,
    contentBase64: string,
    options: {
      title?: string;
      language?: "auto" | "en" | "de";
      guide?: string;
      model?: string;
      project_id?: number;
    } = {},
  ) =>
    request<Interview>("/interviews", {
      method: "POST",
      body: { filename, content_base64: contentBase64, ...options },
    }),
  interviewUpdate: (
    id: string,
    body: {
      title?: string;
      guide?: string;
      speakers?: Record<string, string>;
      project_id?: number | null;
    },
  ) => request<Interview>(`/interviews/${id}`, { method: "PATCH", body }),
  interviewSegmentUpdate: (id: string, idx: number, text: string) =>
    request<InterviewSegment>(`/interviews/${id}/segments/${idx}`, {
      method: "PATCH",
      body: { text },
    }),
  interviewAnalyze: (id: string, model = "auto") =>
    request<Interview>(`/interviews/${id}/analyze`, {
      method: "POST",
      body: { model },
    }),
  interviewChatHistory: (id: string) =>
    request<InterviewMessage[]>(`/interviews/${id}/chat`),
  interviewChat: (id: string, question: string, model = "auto") =>
    request<{
      answer: string;
      quotes: InterviewQuote[];
      workspace_actions?: InterviewMessage["payload"]["workspace_actions"];
    }>(
      `/interviews/${id}/chat`,
      { method: "POST", body: { question, model } },
    ),
  interviewChatStream: (
    id: string,
    question: string,
    model: string,
    onEvent: (event: SpecialistAgentEvent) => void,
    options: SpecialistStreamOptions = {},
  ) =>
    streamSpecialistRequest<{
      message_id: number;
      answer: string;
      quotes: InterviewQuote[];
      workspace_actions?: InterviewMessage["payload"]["workspace_actions"];
      agent_events?: SpecialistAgentEvent[];
    }>(`/interviews/${id}/chat/stream`, { question, model }, onEvent, options),
  interviewAudioRemove: (id: string) =>
    request<Interview>(`/interviews/${id}/audio`, { method: "DELETE" }),
  interviewDelete: (id: string) =>
    request<{ ok: boolean }>(`/interviews/${id}`, { method: "DELETE" }),

  // -- desktop Live Companion ------------------------------------------------
  liveSessionConfig: () =>
    request<LiveSessionConfig>("/interviews/live/config"),
  liveCompanionPair: (body: LiveCompanionPairRequest) =>
    request<LiveCompanionPairResponse>("/interviews/live/pair", {
      method: "POST",
      body,
    }),
  liveCompanionDevices: () =>
    request<LiveCompanionDevicePage>("/interviews/live/devices"),
  liveCompanionDeviceRevoke: (id: number) =>
    request<{ id: number; revoked: true }>(
      `/interviews/live/devices/${encodeURIComponent(id)}`,
      { method: "DELETE" },
    ),
  liveSessions: (limit = 100, purpose?: "conversation" | "brainstorm") => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (purpose) params.set("purpose", purpose);
    return request<LiveSessionPage>(`/interviews/live/sessions?${params.toString()}`);
  },
  liveSession: (id: string) =>
    request<LiveSession>(`/interviews/live/sessions/${encodeURIComponent(id)}`),
  liveSessionCreate: (body: {
    client_session_id: string;
    title: string;
    purpose?: "conversation" | "brainstorm";
    project_id?: number | null;
    language: "auto" | "de" | "en";
    consent?: { participants_notified: true; notice_text: string } | null;
  }) => request<LiveSession>("/interviews/live/sessions", { method: "POST", body }),
  liveSessionUpdate: (
    id: string,
    body: {
      title?: string;
      project_id?: number | null;
      language?: "auto" | "de" | "en";
      invalidate_project_document?: boolean;
      invalidation_challenge?: BrainstormFilingChallenge;
    },
  ) => request<LiveSession>(`/interviews/live/sessions/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body,
  }),
  liveSessionDelete: (
    id: string,
    body?: {
      confirm_project_cleanup: true;
      cleanup_challenge: BrainstormDeleteChallenge;
    },
  ) =>
    request<{ deleted: true }>(`/interviews/live/sessions/${encodeURIComponent(id)}`, {
      method: "DELETE",
      body,
    }),
  liveSessionSegments: (id: string, segments: LiveTranscriptSegmentCreate[]) =>
    request<LiveSegmentAppendResult>(
      `/interviews/live/sessions/${encodeURIComponent(id)}/segments`,
      { method: "POST", body: { segments } },
    ),
  liveSessionEvents: (id: string, after = 0, limit = 200) =>
    request<LiveSessionEventPage>(
      `/interviews/live/sessions/${encodeURIComponent(id)}/events?after=${after}&limit=${limit}`,
    ),
  liveSessionSegment: (id: string, clientEventId: string) =>
    request<LiveSessionSegment>(
      `/interviews/live/sessions/${encodeURIComponent(id)}/segments/${encodeURIComponent(clientEventId)}`,
    ),
  liveSessionAsks: (id: string) =>
    request<LiveAskReceipt[]>(`/interviews/live/sessions/${encodeURIComponent(id)}/asks`),
  liveSessionBrainstorms: (id: string, limit = 20) =>
    request<LiveBrainstormPage>(
      `/interviews/live/sessions/${encodeURIComponent(id)}/brainstorms?limit=${limit}`,
    ),
  liveSessionBrainstorm: (id: string, brainstormId: string) =>
    request<LiveBrainstormReceipt>(
      `/interviews/live/sessions/${encodeURIComponent(id)}/brainstorms/${encodeURIComponent(brainstormId)}`,
    ),
  liveSessionBrainstormCreate: (id: string, body: LiveBrainstormCreate) =>
    request<LiveBrainstormReceipt>(
      `/interviews/live/sessions/${encodeURIComponent(id)}/brainstorms`,
      { method: "POST", body },
    ),
  liveSessionBrainstormCancel: (id: string, brainstormId: string) =>
    request<LiveBrainstormReceipt>(
      `/interviews/live/sessions/${encodeURIComponent(id)}/brainstorms/${encodeURIComponent(brainstormId)}/cancel`,
      { method: "POST" },
    ),
  liveBrainstormComplete: (id: string, body: LiveBrainstormCreate) =>
    request<LiveBrainstormCompleteResult>(
      `/interviews/live/sessions/${encodeURIComponent(id)}/complete`,
      { method: "POST", body },
    ),
  liveProjectBrainstormDocument: (projectId: number, signal?: AbortSignal) =>
    request<ProjectBrainstormDocument>(
      `/interviews/live/brainstorm-projects/${encodeURIComponent(projectId)}/document`,
      { signal },
    ),
  liveProjectBrainstormSyntheses: (
    projectId: number,
    limit = 20,
    offset = 0,
    signal?: AbortSignal,
  ) => request<ProjectBrainstormSynthesisPage>(
    `/interviews/live/brainstorm-projects/${encodeURIComponent(projectId)}/syntheses?limit=${limit}&offset=${offset}`,
    { signal },
  ),
  liveProjectBrainstormSynthesis: (
    projectId: number,
    synthesisId: string,
    signal?: AbortSignal,
  ) => request<ProjectBrainstormSynthesisReceipt>(
    `/interviews/live/brainstorm-projects/${encodeURIComponent(projectId)}/syntheses/${encodeURIComponent(synthesisId)}`,
    { signal },
  ),
  liveProjectBrainstormSynthesisCreate: (
    projectId: number,
    body: ProjectBrainstormSynthesisCreate,
  ) => request<ProjectBrainstormSynthesisReceipt>(
    `/interviews/live/brainstorm-projects/${encodeURIComponent(projectId)}/syntheses`,
    { method: "POST", body },
  ),
  liveProjectBrainstormSynthesisCancel: (projectId: number, synthesisId: string) =>
    request<ProjectBrainstormSynthesisReceipt>(
      `/interviews/live/brainstorm-projects/${encodeURIComponent(projectId)}/syntheses/${encodeURIComponent(synthesisId)}/cancel`,
      { method: "POST" },
    ),
  liveProjectBrainstormSynthesisRetry: (
    projectId: number,
    synthesisId: string,
    body: { client_request_id: string; expected_document_revision: number },
  ) => request<ProjectBrainstormSynthesisReceipt>(
    `/interviews/live/brainstorm-projects/${encodeURIComponent(projectId)}/syntheses/${encodeURIComponent(synthesisId)}/retry`,
    { method: "POST", body },
  ),
  liveSessionComplete: (id: string) =>
    request<{
      session: LiveSession;
      interview_id: string;
      analysis_status: "queued" | "completed" | "failed" | "unavailable";
    }>(
      `/interviews/live/sessions/${encodeURIComponent(id)}/complete`,
      { method: "POST" },
    ),
  liveSessionCancel: (id: string) =>
    request<LiveSession>(`/interviews/live/sessions/${encodeURIComponent(id)}/cancel`, {
      method: "POST",
    }),

  // -- voice studies (live AI-led interviews) --------------------------------
  voiceConfig: () =>
    request<VoiceConfig>("/voice/config"),
  voiceStudies: () => request<VoiceStudy[]>("/voice/studies"),
  voiceStudy: (id: string) => request<VoiceStudy>(`/voice/studies/${id}`),
  voiceStudyCreate: (title: string, language: "de" | "en", projectId?: number) =>
    request<VoiceStudy>("/voice/studies", {
      method: "POST",
      body: { title, language, ...(projectId ? { project_id: projectId } : {}) },
    }),
  voiceStudyUpdate: (
    id: string,
    body: Partial<{
      title: string;
      language: "de" | "en";
      voice: string;
      tone: string;
      mode: "guided" | "iterative";
      patience_ms: number;
      max_session_minutes: number;
      retention: "keep" | "transcript_only";
      consent_text: string;
      contact_line: string;
      participant_information: ParticipantInformation;
      budget_minutes: number;
      sections: VoiceGuideSection[];
      project_id: number | null;
    }>,
  ) => request<VoiceStudy>(`/voice/studies/${id}`, { method: "PATCH", body }),
  voiceStudyChatHistory: (id: string) =>
    request<VoiceStudyMessage[]>(`/voice/studies/${id}/chat`),
  voiceStudyChat: (id: string, question: string, model = "auto", autoApply = true) =>
    request<{
      answer: string;
      actions: VoiceStudyAction[];
      proposals: { operation: string; label: string }[];
      workspace_actions?: VoiceStudyMessage["payload"]["workspace_actions"];
      message_id: number;
      agent_events?: SpecialistAgentEvent[];
      study: VoiceStudy;
    }>(`/voice/studies/${id}/chat`, {
      method: "POST",
      body: { question, model, auto_apply: autoApply },
    }),
  voiceStudyChatStream: (
    id: string,
    question: string,
    model: string,
    autoApply: boolean,
    onEvent: (event: SpecialistAgentEvent) => void,
    options: SpecialistStreamOptions = {},
  ) =>
    streamSpecialistRequest<{
      answer: string;
      actions: VoiceStudyAction[];
      proposals: { operation: string; label: string }[];
      workspace_actions?: VoiceStudyMessage["payload"]["workspace_actions"];
      message_id: number;
      study: VoiceStudy;
    }>(
      `/voice/studies/${id}/chat/stream`,
      { question, model, auto_apply: autoApply },
      onEvent,
      options,
    ),
  voiceStudyApplyProposal: (id: string, messageId: number) =>
    request<{ actions: VoiceStudyAction[]; study: VoiceStudy }>(
      `/voice/studies/${id}/chat/${messageId}/apply`,
      { method: "POST" },
    ),
  voiceInviteCreate: (
    studyId: string,
    body: { label?: string; passcode?: string; max_sessions?: number },
  ) =>
    request<VoiceInvite>(`/voice/studies/${studyId}/invites`, {
      method: "POST",
      body,
    }),
  voiceInviteUpdate: (id: string, active: boolean) =>
    request<VoiceInvite>(`/voice/invites/${id}`, {
      method: "PATCH",
      body: { active },
    }),
  publicTalkInfo: (token: string) =>
    request<PublicTalkInfo>(`/public/talk/${token}`, { auth: false }),
  publicTalkStart: (
    token: string,
    consent: boolean,
    ageConfirmed: boolean,
    consentFingerprint: string,
    passcode = "",
    mode: "live" | "text" = "live",
    audioConsent = false,
  ) =>
    request<VoiceSessionConfig & { participant_label: string }>(
      `/public/talk/${token}/sessions`,
      {
        method: "POST",
        body: {
          consent,
          age_confirmed: ageConfirmed,
          audio_consent: audioConsent,
          consent_fingerprint: consentFingerprint,
          passcode,
          mode,
        },
        auth: false,
      },
    ),
  publicTalkFinalize: (
    token: string,
    sessionId: string,
    body: {
      turns: VoiceTurn[];
      duration_ms: number;
      audio_base64?: string;
      aborted?: boolean;
    },
  ) =>
    request<{ status: "completed" | "aborted" }>(
      `/public/talk/${token}/sessions/${sessionId}/finalize`,
      { method: "POST", body, auth: false },
    ),
  publicTalkMessage: (
    token: string,
    sessionId: string,
    message: string,
    history: VoiceTurn[],
  ) =>
    request<{ reply: string }>(
      `/public/talk/${token}/sessions/${sessionId}/messages`,
      { method: "POST", body: { message, history }, auth: false },
    ),
  voiceStudyDelete: (id: string) =>
    request<{ ok: boolean }>(`/voice/studies/${id}`, { method: "DELETE" }),
  voiceSessionStart: (studyId: string) =>
    request<LiveVoiceSessionConfig>(`/voice/studies/${studyId}/sessions`, {
      method: "POST",
    }),
  voiceSessionFinalize: (
    sessionId: string,
    body: {
      turns: VoiceTurn[];
      duration_ms: number;
      audio_base64?: string;
      aborted?: boolean;
    },
  ) =>
    request<{
      status: "completed" | "aborted";
      interview_id: string | null;
      audio_stored?: boolean;
    }>(`/voice/sessions/${sessionId}/finalize`, { method: "POST", body }),
  reviewHealth: (runId: number) =>
    request<ReviewHealth>(`/runs/${runId}/review-health`),
  runShare: (runId: RunRef) =>
    request<RunShareInfo>(`/runs/${runId}/share`),
  runShareCreate: (runId: RunRef) =>
    request<{ token: string; url: string }>(`/runs/${runId}/share`, { method: "POST" }),
  runShareDelete: (runId: RunRef) =>
    request<{ ok: boolean }>(`/runs/${runId}/share`, { method: "DELETE" }),
  publicSharedRun: (token: string) =>
    request<PublicSharedRun>(`/public/runs/${token}`),

  // -- figures ----------------------------------------------------------------
  figures: () => request<Figure[]>("/figures"),
  figureCreate: (
    prompt: string,
    runId: number | null,
    model: string,
    config: {
      kind: string;
      resolution: string;
      aspect_ratio: string;
      review_passes: number;
      dataset_id?: string | null;
      project_id?: number;
      source_image_base64?: string;
      source_figure_id?: string;
      writer_document_id?: string;
      writer_message_id?: number;
      title?: string;
      repository_analysis_id?: string;
    },
  ) =>
    request<Figure>("/figures", {
      method: "POST",
      body: { prompt, model, ...config, ...(runId ? { run_id: runId } : {}) },
    }),
  figureGet: (id: string) => request<Figure>(`/figures/${id}`),
  figureDelete: (id: string) =>
    request<{ ok: boolean }>(`/figures/${id}`, { method: "DELETE" }),
  figureAttach: (id: string, documentId: string) =>
    request<{ filename: string; document_public_id: string }>(
      `/figures/${id}/attach`,
      { method: "POST", body: { document_id: documentId } },
    ),
  figureRename: (id: string, title: string) =>
    request<Figure>(`/figures/${id}`, { method: "PATCH", body: { title } }),
  figureUpdate: (
    id: string,
    body: { title?: string; project_id?: number | null },
  ) => request<Figure>(`/figures/${id}`, { method: "PATCH", body }),

  // -- repository analyses ----------------------------------------------------
  repositoryConnections: () =>
    request<RepositoryConnection[]>("/repository-connections"),
  repositoryConnectionCreate: (body: GithubRepositoryConnectionCreate) =>
    request<RepositoryConnection>("/repository-connections", {
      method: "POST",
      body,
    }),
  repositoryConnectionDelete: (id: string) =>
    request<void>(`/repository-connections/${id}`, { method: "DELETE" }),
  repositoryAnalyses: () =>
    request<RepositoryAnalysis[]>("/repository-analyses"),
  repositoryAnalysisCreate: (body: RepositoryAnalysisCreate) =>
    request<RepositoryAnalysis>("/repository-analyses", {
      method: "POST",
      body,
    }),
  repositoryAnalysis: (id: string) =>
    request<RepositoryAnalysis>(`/repository-analyses/${id}`),
  repositoryAnalysisCancel: (id: string) =>
    request<RepositoryAnalysis>(`/repository-analyses/${id}/cancel`, {
      method: "POST",
    }),
  repositoryAnalysisDelete: (id: string) =>
    request<{ ok: boolean }>(`/repository-analyses/${id}`, {
      method: "DELETE",
    }),
  repositoryManuscriptPreview: (
    id: string,
    body: RepositoryManuscriptPreviewRequest,
  ) =>
    request<RepositoryManuscriptPreview>(
      `/repository-analyses/${id}/manuscript/preview`,
      { method: "POST", body },
    ),
  repositoryManuscriptProposal: (
    id: string,
    body: RepositoryManuscriptProposalRequest,
  ) =>
    request<RepositoryManuscriptProposal>(
      `/repository-analyses/${id}/manuscript/proposals`,
      { method: "POST", body },
    ),

  // -- surveys ----------------------------------------------------------------
  surveys: () => request<Survey[]>("/surveys"),
  survey: (id: string) => request<Survey>(`/surveys/${id}`),
  surveyCreate: (body: {
    title: string;
    description?: string;
    project_id?: number | null;
    questions?: Survey["questions"];
  }) => request<Survey>("/surveys", { method: "POST", body }),
  surveyUpdate: (id: string, body: Partial<Pick<Survey, "title" | "description" | "project_id" | "status" | "questions" | "settings" | "participant_information">>) =>
    request<Survey>(`/surveys/${id}`, { method: "PATCH", body }),
  surveySetPassword: (id: string, password: string) =>
    request<Survey>(`/surveys/${id}/password`, {
      method: "PUT",
      body: { password },
    }),
  surveyRemovePassword: (id: string) =>
    request<Survey>(`/surveys/${id}/password`, { method: "DELETE" }),
  surveyImportDataset: (id: string, writerId?: string) =>
    request<{ dataset: ResearchDataset; version: number; writer_linked: boolean }>(
      `/surveys/${id}/dataset`,
      { method: "POST", body: { writer_id: writerId ?? null } },
    ),
  surveyDelete: (id: string) =>
    request<{ ok: boolean }>(`/surveys/${id}`, { method: "DELETE" }),
  surveyChatHistory: (id: string) =>
    request<SurveyMessage[]>(`/surveys/${id}/chat`),
  surveyChat: (id: string, question: string, model = "auto", autoApply = true) =>
    request<SurveyAgentReply>(`/surveys/${id}/chat`, {
      method: "POST",
      body: { question, model, auto_apply: autoApply },
    }),
  surveyChatStream: (
    id: string,
    question: string,
    model: string,
    autoApply: boolean,
    onEvent: (event: SpecialistAgentEvent) => void,
    options: SpecialistStreamOptions = {},
  ) =>
    streamSpecialistRequest<SurveyAgentReply>(
      `/surveys/${id}/chat/stream`,
      { question, model, auto_apply: autoApply },
      onEvent,
      options,
    ),
  surveyChatApply: (id: string, messageId: number) =>
    request<SurveyProposalApplyResult>(`/surveys/${id}/chat/${messageId}/apply`, {
      method: "POST",
    }),

  specialistChatClear: (kind: SpecialistResourceKind, id: string | number) => {
    const prefixes: Record<SpecialistResourceKind, string> = {
      manuscript: "/writer",
      dataset: "/datasets",
      interview: "/interviews",
      "interview-study": "/voice/studies",
      survey: "/surveys",
    };
    return request<{
      ok: boolean;
      messages_deleted: number;
      turns_deleted: number;
    }>(`${prefixes[kind]}/${id}/chat`, { method: "DELETE" });
  },

  // -- creator-private Knowledge pages --------------------------------------
  knowledgePages: (filters: KnowledgePageFilters = {}, signal?: AbortSignal) => {
    const params = new URLSearchParams();
    if (filters.q) params.set("q", filters.q);
    if (filters.search_scope) params.set("search_scope", filters.search_scope);
    if (filters.state) params.set("state", filters.state);
    if (filters.tag) params.set("tag", filters.tag);
    if (filters.pinned !== undefined) params.set("pinned", String(filters.pinned));
    if (filters.project_id !== undefined) params.set("project_id", String(filters.project_id));
    if (filters.parent_id) params.set("parent_id", filters.parent_id);
    params.set("offset", String(filters.offset ?? 0));
    params.set("limit", String(filters.limit ?? 50));
    return request<KnowledgePageList>(`/knowledge/pages?${params.toString()}`, { signal });
  },
  knowledgePage: (id: string, signal?: AbortSignal) =>
    request<KnowledgePageRecord>(`/knowledge/pages/${encodeURIComponent(id)}`, { signal }),
  createKnowledgePage: (body: KnowledgePageCreate) =>
    request<KnowledgePageRecord>("/knowledge/pages", { method: "POST", body }),
  updateKnowledgePage: (id: string, body: KnowledgePageUpdate) =>
    request<KnowledgePageRecord>(`/knowledge/pages/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body,
    }),
  deleteKnowledgePage: (id: string, expectedRevision: number) =>
    request<void>(
      `/knowledge/pages/${encodeURIComponent(id)}?expected_revision=${expectedRevision}`,
      { method: "DELETE" },
    ),

  // -- projects & runs --------------------------------------------------------
  createProject: (name: string) =>
    request<Project>("/projects", { method: "POST", body: { name } }),
  listProjects: () => request<Project[]>("/projects"),
  projectWorkspace: (id: number) =>
    request<ProjectWorkspace>(`/projects/${id}/workspace`),
  updateProject: (
    id: number,
    body: Partial<
      Pick<
        Project,
        | "name"
        | "description"
        | "kind"
        | "phase"
        | "status"
        | "question"
        | "hypothesis"
        | "metadata"
      >
    >,
  ) => request<Project>(`/projects/${id}`, { method: "PATCH", body }),
  createProjectStudy: (
    id: number,
    body: {
      title: string;
      design?: string;
      registry_id?: string;
      population?: string;
      intervention?: string;
      comparator?: string;
      outcomes?: string[];
      report_work_ids?: string[];
      notes?: string;
    },
  ) => request<ProjectStudy>(`/projects/${id}/studies`, { method: "POST", body }),
  createProjectTask: (
    id: number,
    body: { title: string; description?: string; priority?: string },
  ) => request<ProjectTask>(`/projects/${id}/tasks`, { method: "POST", body }),
  updateProjectTask: (
    projectId: number,
    taskId: number,
    body: Partial<Pick<ProjectTask, "title" | "description" | "status" | "priority">>,
  ) =>
    request<ProjectTask>(`/projects/${projectId}/tasks/${taskId}`, {
      method: "PATCH",
      body,
    }),
  createProjectClaim: (
    id: number,
    body: { text: string; section?: string; writer_document_id?: number },
  ) => request<EvidenceClaim>(`/projects/${id}/claims`, { method: "POST", body }),
  linkClaimEvidence: (
    projectId: number,
    claimId: number,
    body: {
      target_type: "study" | "work" | "quote" | "dataset" | "analysis";
      target_id: string;
      relationship?: "supports" | "contradicts" | "qualifies";
      locator?: string;
      quote?: string;
      note?: string;
      source_version?: string;
      verified?: boolean;
    },
  ) =>
    request<{ id: number }>(`/projects/${projectId}/claims/${claimId}/evidence`, {
      method: "POST",
      body,
    }),
  createRiskOfBias: (
    id: number,
    body: {
      study_id?: number;
      tool: string;
      overall: string;
      rationale?: string;
      domains?: Record<string, unknown>[];
      status?: "draft" | "final";
    },
  ) =>
    request<{ id: number; tool: string; overall: string; status: string }>(
      `/projects/${id}/risk-of-bias`,
      { method: "POST", body },
    ),
  createProjectAnalysis: (
    id: number,
    body: { dataset_id: string; name: string; kind?: string; definition?: Record<string, unknown> },
  ) =>
    request<{ id: number; public_id: string; dataset_version: number; name: string; status: string }>(
      `/projects/${id}/analyses`,
      { method: "POST", body },
    ),
  createSubmission: (
    id: number,
    body: { journal: string; article_type?: string; writer_document_id?: number },
  ) => request<ProjectSubmission>(`/projects/${id}/submissions`, { method: "POST", body }),
  updateSubmission: (
    projectId: number,
    submissionId: number,
    body: Partial<Pick<ProjectSubmission, "journal" | "article_type" | "status" | "checklist">>,
  ) =>
    request<ProjectSubmission>(`/projects/${projectId}/submissions/${submissionId}`, {
      method: "PATCH",
      body,
    }),
  projectReproducibility: (id: number) =>
    request<Record<string, unknown>>(`/projects/${id}/reproducibility`),
  renameProject: (id: number, name: string) =>
    request<Project>(`/projects/${id}`, { method: "PATCH", body: { name } }),
  deleteProject: (id: number) =>
    request<{ deleted: number; runs_deleted: number }>(`/projects/${id}`, {
      method: "DELETE",
    }),
  listRuns: (projectId?: number) =>
    request<RunSummary[]>(
      projectId === undefined ? "/runs" : `/runs?project_id=${projectId}`,
    ),
  createRun: (projectId: number | null, body: RunCreateRequest) =>
    request<RunCreated>(
      projectId === null ? "/runs" : `/projects/${projectId}/runs`,
      { method: "POST", body },
    ),
  run: (id: RunRef) => request<RunDetail>(`/runs/${id}`),
  controlRoom: (id: RunRef) =>
    request<ResearchControlRoom>(`/runs/${id}/control-room`),
  renameRun: (id: RunRef, title: string) =>
    request<{ id: number; title: string | null; project_id: number }>(`/runs/${id}`, {
      method: "PATCH",
      body: { title },
    }),
  moveRun: (id: RunRef, projectId: number) =>
    request<{ id: number; title: string | null; project_id: number }>(`/runs/${id}`, {
      method: "PATCH",
      body: { project_id: projectId },
    }),
  deleteRun: (id: RunRef) =>
    request<{ deleted: number }>(`/runs/${id}`, { method: "DELETE" }),
  runEvents: (id: RunRef) => request<RunEvent[]>(`/runs/${id}/events`),
  runStreamTicket: (id: RunRef) =>
    request<{ ticket: string; expires_in: number }>(`/runs/${id}/events/ticket`, {
      method: "POST",
    }),
  evidenceGraph: (id: RunRef) =>
    request<EvidenceGraph>(`/runs/${id}/evidence-graph`),
  createRunClaim: (
    id: RunRef,
    body: {
      text: string;
      section?: string;
      status?: string;
      confidence?: string;
      writer_document_id?: number | null;
    },
  ) =>
    request<EvidenceClaim>(`/runs/${id}/claims`, {
      method: "POST",
      body,
    }),
  updateRunClaim: (
    id: RunRef,
    claimId: number,
    body: Partial<
      Pick<EvidenceClaim, "text" | "section" | "status" | "confidence">
    >,
  ) =>
    request<EvidenceClaim>(`/runs/${id}/claims/${claimId}`, {
      method: "PATCH",
      body,
    }),
  deleteRunClaim: (id: RunRef, claimId: number) =>
    request<{ deleted: number }>(`/runs/${id}/claims/${claimId}`, {
      method: "DELETE",
    }),
  linkRunClaimEvidence: (
    id: RunRef,
    claimId: number,
    body: {
      target_type: "study" | "work" | "quote" | "dataset" | "analysis";
      target_id: string;
      relationship: EvidenceRelationship;
      locator?: string;
      quote?: string;
      note?: string;
      source_version?: string;
      verified?: boolean;
    },
  ) =>
    request<{ id: number }>(`/runs/${id}/claims/${claimId}/evidence`, {
      method: "POST",
      body,
    }),
  updateRunClaimEvidence: (
    id: RunRef,
    claimId: number,
    evidenceId: number,
    body: Partial<{
      relationship: EvidenceRelationship;
      locator: string;
      quote: string;
      note: string;
      source_version: string;
      verified: boolean;
    }>,
  ) =>
    request<{ id: number }>(
      `/runs/${id}/claims/${claimId}/evidence/${evidenceId}`,
      { method: "PATCH", body },
    ),
  deleteRunClaimEvidence: (
    id: RunRef,
    claimId: number,
    evidenceId: number,
  ) =>
    request<{ deleted: number }>(
      `/runs/${id}/claims/${claimId}/evidence/${evidenceId}`,
      { method: "DELETE" },
    ),
  cancelRun: (id: RunRef) =>
    request<{
      run_id: number;
      control: string;
      status: "cancelled";
      cancelled_jobs: number;
    }>(`/runs/${id}/cancel`, { method: "POST" }),
  pauseRun: (id: RunRef) =>
    request<{ run_id: number; control: string }>(`/runs/${id}/pause`, {
      method: "POST",
    }),
  resumeRun: (id: RunRef) =>
    request<{ run_id: number; status: string }>(`/runs/${id}/resume`, {
      method: "POST",
    }),
  retryRun: (
    id: RunRef,
    options?: { webSearchPublicDataConfirmed?: boolean },
  ) =>
    request<{ run_id: number; public_id: string; status: "pending" }>(
      `/runs/${id}/retry`,
      {
        method: "POST",
        body: {
          web_search_public_data_confirmed:
            options?.webSearchPublicDataConfirmed === true,
        },
      },
    ),
  runMethods: (id: RunRef) =>
    request<{ methods: string }>(`/runs/${id}/methods`),
  runDocuments: (id: RunRef) =>
    request<DocumentEntry[]>(`/runs/${id}/documents`),
  updateChatTable: (
    runId: RunRef,
    messageId: number,
    body: { title: string; columns: string[]; rows: string[][] },
  ) =>
    request<{
      title: string;
      columns: string[];
      rows: string[][];
      revision: number;
      original: { title: string; columns: string[]; rows: string[][] };
      updated_at: string;
    }>(`/runs/${runId}/chat/${messageId}/table`, {
      method: "PATCH",
      body,
    }),
  resetChatTable: (runId: RunRef, messageId: number) =>
    request<{
      title: string;
      columns: string[];
      rows: string[][];
      revision: number;
      original: { title: string; columns: string[]; rows: string[][] };
      updated_at: string;
    }>(`/runs/${runId}/chat/${messageId}/table/reset`, {
      method: "POST",
    }),
  libraryDocuments: (q = "", offset = 0, limit = 200) =>
    request<LibraryDocument[]>(
      `/documents?offset=${offset}&limit=${limit}${q ? `&q=${encodeURIComponent(q)}` : ""}`,
    ),
  libraryDocument: async (id: number) => {
    const rows = await request<LibraryDocument[]>(
      `/documents?entry_id=${id}&offset=0&limit=1`,
    );
    return rows[0] ?? null;
  },
  libraryDocumentCitations: (id: number) =>
    request<LibraryDocumentCitations>(`/documents/${id}/citations`),
  libraryShares: () => request<LibraryShare[]>("/library/shares"),
  createLibraryShare: (body: {
    email: string;
    scope: LibraryShareScope;
    project_id: number | null;
    role: "viewer";
    rights_confirmed: true;
  }) => request<LibraryShare>("/library/shares", { method: "POST", body }),
  deleteLibraryShare: (shareId: string) =>
    request<void>(`/library/shares/${encodeURIComponent(shareId)}`, {
      method: "DELETE",
    }),
  receivedLibraryShares: () =>
    request<ReceivedLibraryShare[]>("/library/shares/received"),
  receivedLibraryDocuments: (
    shareId: string,
    opts: { q?: string; entryId?: number; offset?: number; limit?: number } = {},
  ) => {
    const params = new URLSearchParams();
    params.set("offset", String(opts.offset ?? 0));
    params.set("limit", String(opts.limit ?? 50));
    if (opts.q) params.set("q", opts.q);
    if (opts.entryId !== undefined) params.set("entry_id", String(opts.entryId));
    return request<SharedLibraryDocument[]>(
      `/library/shares/received/${encodeURIComponent(shareId)}/documents?${params}`,
    );
  },
  receivedLibraryDocument: (shareId: string, documentId: number) =>
    request<SharedLibraryDocument>(
      `/library/shares/received/${encodeURIComponent(shareId)}/documents/${documentId}`,
    ),
  receivedLibraryDocumentCitations: (shareId: string, documentId: number) =>
    request<LibraryDocumentCitations>(
      `/library/shares/received/${encodeURIComponent(shareId)}/documents/${documentId}/citations`,
    ),
  receivedLibraryWebSources: (
    shareId: string,
    opts: { q?: string; entryId?: string; offset?: number; limit?: number } = {},
  ) => {
    const params = new URLSearchParams();
    params.set("offset", String(opts.offset ?? 0));
    params.set("limit", String(opts.limit ?? 50));
    if (opts.q) params.set("q", opts.q);
    if (opts.entryId) params.set("entry_id", opts.entryId);
    return request<SharedLibraryWebSource[]>(
      `/library/shares/received/${encodeURIComponent(shareId)}/web-sources?${params}`,
    );
  },
  receivedLibraryWebSource: (shareId: string, sourceId: string) =>
    request<SharedLibraryWebSource>(
      `/library/shares/received/${encodeURIComponent(shareId)}/web-sources/${encodeURIComponent(sourceId)}`,
    ),
  libraryWebSources: (q = "", offset = 0, limit = 200) =>
    request<LibraryWebSource[]>(
      `/library/web-sources?offset=${offset}&limit=${limit}${q ? `&q=${encodeURIComponent(q)}` : ""}`,
    ),
  libraryWebSource: async (id: string) => {
    const rows = await request<LibraryWebSource[]>(
      `/library/web-sources?entry_id=${encodeURIComponent(id)}&offset=0&limit=1`,
    );
    return rows[0] ?? null;
  },
  deleteLibraryWebSource: (id: string) =>
    request<void>(`/library/web-sources/${id}`, { method: "DELETE" }),
  updateLibraryDocument: (
    id: number,
    body: { project_id: number | null; folder?: string | null },
  ) =>
    request<{ id: number; project_id: number | null; folder: string | null }>(
      `/documents/${id}`,
      { method: "PATCH", body },
    ),
  updateLibraryDocumentMetadata: (
    id: number,
    body: {
      expected_revision: string;
      mode: "fill_missing" | "edit";
      metadata: Partial<Omit<LibraryPaperMetadata, "year">>;
    },
  ) =>
    request<LibraryMetadataUpdateResult>(`/documents/${id}/metadata`, {
      method: "PATCH",
      body,
    }),
  latestPaperEnrichment: (id: number) =>
    request<{ job: PaperEnrichmentJob | null }>(
      `/documents/${id}/enrichments/latest`,
    ),
  startPaperEnrichment: (id: number, requestId: string) =>
    request<PaperEnrichmentJob>(`/documents/${id}/enrichments`, {
      method: "POST",
      body: { request_id: requestId },
    }),
  paperEnrichment: (documentId: number, enrichmentId: string) =>
    request<PaperEnrichmentJob>(
      `/documents/${documentId}/enrichments/${enrichmentId}`,
    ),
  cancelPaperEnrichment: (documentId: number, enrichmentId: string) =>
    request<PaperEnrichmentJob>(
      `/documents/${documentId}/enrichments/${enrichmentId}/cancel`,
      { method: "POST" },
    ),
  retryPaperEnrichment: (documentId: number, enrichmentId: string) =>
    request<PaperEnrichmentJob>(
      `/documents/${documentId}/enrichments/${enrichmentId}/retry`,
      { method: "POST" },
    ),
  applyPaperEnrichment: (
    documentId: number,
    enrichmentId: string,
    expectedRevision: string,
  ) =>
    request<{
      job: PaperEnrichmentJob;
      changed_fields: string[];
      metadata?: LibraryPaperMetadata;
      metadata_revision?: string;
      metadata_provenance?: Record<string, unknown>;
    }>(`/documents/${documentId}/enrichments/${enrichmentId}/apply`, {
      method: "POST",
      body: { expected_revision: expectedRevision },
    }),
  updateLibraryWebSourceMetadata: (
    id: string,
    body: {
      expected_revision: string;
      mode: "fill_missing" | "edit";
      metadata: Partial<LibraryPaperMetadata> & {
        site_name?: string | null;
        source_kind?: "paper" | "web" | null;
      };
    },
  ) =>
    request<LibraryWebSource & { mode: "fill_missing" | "edit"; changed_fields: string[] }>(
      `/library/web-sources/${id}`,
      { method: "PATCH", body },
    ),
  documentAnnotations: (id: number) =>
    request<DocumentAnnotation[]>(`/documents/${id}/annotations`),
  createDocumentAnnotation: (
    id: number,
    body: {
      page: number;
      quote: string;
      note?: string;
      source?: "user" | "assistant";
      color?: "moss" | "amber" | "rose" | "blue";
    },
  ) =>
    request<DocumentAnnotation>(`/documents/${id}/annotations`, {
      method: "POST",
      body,
    }),
  updateDocumentAnnotation: (
    documentId: number,
    annotationId: number,
    body: { note?: string; color?: "moss" | "amber" | "rose" | "blue" },
  ) =>
    request<DocumentAnnotation>(
      `/documents/${documentId}/annotations/${annotationId}`,
      { method: "PATCH", body },
    ),
  deleteDocumentAnnotation: (documentId: number, annotationId: number) =>
    request<void>(`/documents/${documentId}/annotations/${annotationId}`, {
      method: "DELETE",
    }),
  translateDocumentPage: (
    documentId: number,
    body: { page: number; language: string },
  ) =>
    request<{
      page: number;
      language: string;
      text: string;
      cached: boolean;
    }>(`/documents/${documentId}/translation`, {
      method: "POST",
      body,
    }),
  documentTranslationStatus: (documentId: number, language: string) =>
    request<DocumentTranslationStatus>(
      `/documents/${documentId}/translations/${encodeURIComponent(language)}`,
    ),
  startDocumentTranslation: (documentId: number, language: string) =>
    request<DocumentTranslationStatus>(`/documents/${documentId}/translations`, {
      method: "POST",
      body: { language },
    }),
  saveDocumentFigure: (
    documentId: number,
    body: { page: number; image_base64: string; caption?: string; source_label?: string },
  ) =>
    request<Figure>(`/documents/${documentId}/figures`, {
      method: "POST",
      body,
    }),
  deleteLibraryDocument: (documentId: number) =>
    request<void>(`/documents/${documentId}`, { method: "DELETE" }),
  bulkDeleteLibraryDocuments: (documentIds: number[]) =>
    request<{
      deleted: number;
      ledger_rows_deleted: number;
      blobs_removed: number;
    }>("/documents/bulk-delete", {
      method: "POST",
      body: { document_ids: documentIds },
    }),
  runWorks: (
    id: RunRef,
    opts: {
      includedOnly?: boolean;
      verdict?: "all" | "include" | "exclude" | "unsure";
      limit?: number;
      offset?: number;
    } = {},
  ) => {
    const params = new URLSearchParams();
    if (opts.includedOnly) params.set("included_only", "true");
    if (opts.verdict && opts.verdict !== "all") params.set("verdict", opts.verdict);
    if (opts.limit !== undefined) params.set("limit", String(opts.limit));
    if (opts.offset !== undefined) params.set("offset", String(opts.offset));
    const qs = params.toString();
    return request<WorksPage>(`/runs/${id}/works${qs ? `?${qs}` : ""}`);
  },
  webSources: (id: RunRef) => request<WebSource[]>(`/runs/${id}/web-sources`),
  zoteroSync: (id: RunRef, body: ZoteroSyncRequest) =>
    request<ZoteroSyncResult>(`/runs/${id}/zotero`, { method: "POST", body }),

  // -- user documents (uploaded PDFs / links) ----------------------------------
  uploadDocument: (
    body: {
      url?: string;
      filename?: string;
      content_base64?: string;
      project_id?: number;
    },
    runId?: RunRef,
  ) =>
    request<UploadedDocument>(
      runId === undefined ? "/orgs/current/documents" : `/runs/${runId}/documents`,
      { method: "POST", body },
    ),
  collectPdfs: (id: RunRef) =>
    request<{ run_id: number; status: string; note: string }>(
      `/runs/${id}/acquire`,
      { method: "POST" },
    ),

  // -- grounded chat ----------------------------------------------------------
  // context sizes itself server-side (adaptive); selection anchors the turn
  // on a passage the user marked in the paper reader
  chat: (
    id: RunRef,
    question: string,
    selection?: { document_id: number; page: number; quote: string },
    model?: string,
    webSearchPublicDataConfirmed = false,
    webSearchQuery?: string,
  ) =>
    request<ChatAnswer>(`/runs/${id}/chat`, {
      method: "POST",
      body: {
        question,
        ...(selection && { selection }),
        ...(model && { model }),
        web_search_public_data_confirmed: webSearchPublicDataConfirmed,
        ...(webSearchPublicDataConfirmed && webSearchQuery && {
          web_search_query: webSearchQuery.trim(),
          web_search_notice_version: PUBLIC_WEB_SEARCH_NOTICE_VERSION,
        }),
      },
    }),
  chatStream: (
    id: RunRef,
    question: string,
    selection: { document_id: number; page: number; quote: string } | undefined,
    model: string | undefined,
    turnId: string,
    onEvent: (event: ChatStreamEvent) => void,
    options?: { webSearchPublicDataConfirmed?: boolean; webSearchQuery?: string },
  ) =>
    streamChatRequest(
      `/runs/${id}/chat/stream`,
      `/runs/${id}/chat/turns/${encodeURIComponent(turnId)}/events/stream`,
      turnId,
      {
        question,
        turn_id: turnId,
        ...(selection && { selection }),
        ...(model && { model }),
        web_search_public_data_confirmed:
          options?.webSearchPublicDataConfirmed === true,
        ...(options?.webSearchPublicDataConfirmed && options?.webSearchQuery && {
          web_search_query: options.webSearchQuery.trim(),
          web_search_notice_version: PUBLIC_WEB_SEARCH_NOTICE_VERSION,
        }),
      },
      onEvent,
    ),
  stopChatTurn: (id: RunRef, turnId: string) =>
    request<{
      turn_id: string;
      status: "cancel_requested" | "cancelled" | "completed" | "failed";
    }>(
      `/runs/${id}/chat/turns/${encodeURIComponent(turnId)}/stop`,
      { method: "POST" },
    ),
  activeChatTurn: (id: RunRef) =>
    request<ChatTurnState | null>(`/runs/${id}/chat/turns/active`),
  latestChatTurn: (id: RunRef) =>
    request<ChatTurnState | null>(`/runs/${id}/chat/turns/latest`),
  chatTurnStatus: (id: RunRef, turnId: string) =>
    request<ChatTurnState>(
      `/runs/${id}/chat/turns/${encodeURIComponent(turnId)}`,
    ),
  followChatTurn: (
    id: RunRef,
    turnId: string,
    onEvent: (event: ChatStreamEvent) => void,
    signal?: AbortSignal,
  ) =>
    followChatTurnRequest(
      `/runs/${id}/chat/turns/${encodeURIComponent(turnId)}/events/stream`,
      turnId,
      onEvent,
      signal,
    ),
  chatHistory: (id: RunRef) => request<ChatMessage[]>(`/runs/${id}/chat`),
  reportChat: (
    id: RunRef,
    body: { category: string; note: string; consent: boolean },
  ) =>
    request<{ id: string; status: string }>(`/runs/${id}/chat-report`, {
      method: "POST",
      body,
    }),

  // -- reports ------------------------------------------------------------------
  generateReport: (id: RunRef, force = false) =>
    request<ReportResponse>(`/runs/${id}/report`, {
      method: "POST",
      body: { force },
    }),
  getReport: (id: RunRef) => request<ReportResponse>(`/runs/${id}/report`),

  // -- human gates --------------------------------------------------------------
  protocol: (id: RunRef) => request<ProtocolResponse>(`/runs/${id}/protocol`),
  regenerateProtocol: (id: RunRef) =>
    request<ProtocolResponse>(`/runs/${id}/protocol/regenerate`, { method: "POST" }),
  approveProtocol: (
    id: RunRef,
    edits: {
      inclusion_criteria?: string[];
      exclusion_criteria?: string[];
      query_string?: string;
      web_search_public_data_confirmed?: true;
    },
  ) =>
    request<{ id: number; status: string }>(`/runs/${id}/protocol/approve`, {
      method: "POST",
      body: edits,
    }),
  screeningQueuePage: (id: RunRef, limit = 25, offset = 0) =>
    request<QueuePage>(`/runs/${id}/queue?limit=${limit}&offset=${offset}`),
  submitDecisions: (id: RunRef, decisions: HumanDecision[]) =>
    request<{ recorded: number }>(`/runs/${id}/decisions`, {
      method: "POST",
      body: decisions,
    }),
  decisions: (id: RunRef) => request<Decision[]>(`/runs/${id}/decisions`),
  calibrate: (id: RunRef, seeds: { work_id: string; included: boolean }[]) =>
    request<CalibrationReport>(`/runs/${id}/calibration`, {
      method: "POST",
      body: seeds,
    }),

  // -- living reviews -----------------------------------------------------------
  setLiving: (id: RunRef, enabled: boolean) =>
    request<{ run_id: number; living: boolean }>(`/runs/${id}/living`, {
      method: "POST",
      body: { enabled },
    }),
  livingWorkspace: (id: RunRef) =>
    request<LivingResearchWorkspace>(`/runs/${id}/living`),
  updateLivingSettings: (
    id: RunRef,
    body: {
      enabled: boolean;
      cadence: "weekly" | "monthly" | "quarterly" | "manual";
      auto_screen: boolean;
      notify: boolean;
      watch_sources: string[];
    },
  ) =>
    request<{
      run_id: number;
      enabled: boolean;
      cadence: string;
      auto_screen: boolean;
      notify: boolean;
      watch_sources: string[];
    }>(`/runs/${id}/living/settings`, { method: "PUT", body }),
  refreshLiving: (id: RunRef, scope: "delta" | "full" = "delta") =>
    request<RunCreated & { baseline_run: string; scope: string }>(
      `/runs/${id}/living/refresh`,
      { method: "POST", body: { scope } },
    ),
  recheck: (id: RunRef) =>
    request<RetractionDelta>(`/runs/${id}/recheck`, { method: "POST" }),
};

/** A stream URL only carries a short-lived, one-use, run-bound ticket. */
export function runStreamUrl(runId: RunRef, ticket: string): string {
  return `${API_URL}/runs/${runId}/events/stream?ticket=${encodeURIComponent(ticket)}`;
}

async function fetchAuthenticatedDocumentBytes(path: string): Promise<ArrayBuffer> {
  const token = getToken();
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      cache: "no-store",
    });
  } catch {
    throw new ApiError(
      0,
      "Can't reach SixSentences_ right now. Check your internet connection and try again.",
    );
  }
  if (!res.ok) {
    const error = await toApiError(res);
    if (res.status === 401 && getToken() === token) {
      clearToken();
      announce("six:unauthorized");
    }
    throw error;
  }
  try {
    return await res.arrayBuffer();
  } catch {
    throw new ApiError(
      0,
      "Can't reach SixSentences_ right now. Check your internet connection and try again.",
    );
  }
}

/** Fetch a stored document's bytes for the in-app reader (auth header needed,
 * so the PDF travels as an ArrayBuffer rather than a plain URL). */
export async function fetchDocumentBytes(documentId: number): Promise<ArrayBuffer> {
  return fetchAuthenticatedDocumentBytes(`/documents/${documentId}/file`);
}

/** Fetch viewer-authorized PDF bytes without crossing into owned-document APIs. */
export async function fetchReceivedLibraryDocumentBytes(
  shareId: string,
  documentId: number,
): Promise<ArrayBuffer> {
  return fetchAuthenticatedDocumentBytes(
    `/library/shares/received/${encodeURIComponent(shareId)}/documents/${documentId}/file`,
  );
}

export async function fetchTranslatedDocumentBytes(
  documentId: number,
  language: string,
): Promise<ArrayBuffer> {
  return fetchAuthenticatedDocumentBytes(
    `/documents/${documentId}/translations/${encodeURIComponent(language)}/file`,
  );
}

/** Read a File into the base64 payload the upload endpoint expects. */
export function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error);
    reader.onload = () => {
      const url = String(reader.result ?? "");
      resolve(url.slice(url.indexOf(",") + 1)); // strip the data: prefix
    };
    reader.readAsDataURL(file);
  });
}

/** Download an export (needs the Authorization header, hence fetch → blob). */
export async function downloadExport(
  runId: RunRef,
  format: ExportFormat,
  includedOnly: boolean,
): Promise<void> {
  const token = getToken();
  const res = await fetchApiResponse(
    `${API_URL}/runs/${runId}/export?format=${format}&included_only=${includedOnly}`,
    { headers: token ? { Authorization: `Bearer ${token}` } : {} },
  );
  if (!res.ok) throw await toApiError(res);
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const match = /filename="([^"]+)"/.exec(disposition);
  const extension = { bibtex: "bib", ris: "ris", csl: "json" }[format];
  const filename = match?.[1] ?? `run-${runId}.${extension}`;
  const blob = await readBlobResponse(res);
  triggerDownload(blob, filename);
}

/** Download creator-selected Library papers in one server-rendered citation format. */
export async function downloadLibraryCitations(
  documentIds: number[],
  format: LibraryCitationFormat,
): Promise<void> {
  const uniqueIds = [...new Set(documentIds)];
  if (
    uniqueIds.length < 1
    || uniqueIds.length > 100
    || uniqueIds.some((id) => !Number.isSafeInteger(id) || id < 1)
  ) {
    throw new Error("Choose between 1 and 100 valid Library papers.");
  }
  const token = getToken();
  let res: Response;
  try {
    res = await fetch(`${API_URL}/documents/citations/export`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({ document_ids: uniqueIds, format }),
      cache: "no-store",
    });
  } catch {
    throw new ApiError(
      0,
      "Can't reach SixSentences_ right now. Check your internet connection and try again.",
    );
  }
  if (!res.ok) {
    const error = await toApiError(res);
    if (res.status === 401 && getToken() === token) {
      clearToken();
      announce("six:unauthorized");
    }
    if (res.status === 402) announceAvailabilityError(error, token);
    throw error;
  }
  const fallbackNames: Record<LibraryCitationFormat, string> = {
    apa: "sixsentences-citations-apa.txt",
    mla: "sixsentences-citations-mla.txt",
    chicago: "sixsentences-citations-chicago.txt",
    harvard: "sixsentences-citations-harvard.txt",
    bibtex: "sixsentences-citations.bib",
    ris: "sixsentences-citations.ris",
  };
  const fallback = fallbackNames[format];
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const candidate = /filename="([^"\\/\r\n]+)"/.exec(disposition)?.[1] ?? "";
  const filename = /^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$/.test(candidate)
    ? candidate
    : fallback;
  triggerDownload(await readBlobResponse(res), filename);
}

export async function downloadReferenceConnector(
  connectorId: string,
  format: "ris" | "bibtex" | "endnote",
): Promise<void> {
  const token = getToken();
  const res = await fetchApiResponse(
    `${API_URL}/reference-connectors/${connectorId}/export?format=${format}`,
    { headers: token ? { Authorization: `Bearer ${token}` } : {} },
  );
  if (!res.ok) throw await toApiError(res);
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const extension = { ris: "ris", bibtex: "bib", endnote: "enw" }[format];
  const filename =
    /filename="([^"]+)"/.exec(disposition)?.[1] ??
    `reference-library.${extension}`;
  triggerDownload(await readBlobResponse(res), filename);
}

function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

/** Download a reauthenticated privacy export without ever putting the
 * password or bearer token into a URL. */
export async function downloadAccountExport(password: string): Promise<void> {
  const token = getToken();
  let res: Response;
  try {
    res = await fetch(`${API_URL}/auth/account/export`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({ password }),
      cache: "no-store",
    });
  } catch {
    throw new ApiError(
      0,
      "Can't reach SixSentences_ right now. Check your internet connection and try again.",
    );
  }
  if (!res.ok) {
    const error = await toApiError(res);
    if (res.status === 401 && getToken() === token) {
      clearToken();
      announce("six:unauthorized");
    }
    throw error;
  }
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const filename =
    /filename="([^"\\/]+)"/.exec(disposition)?.[1] ??
    "sixsentences-account-export.zip";
  triggerDownload(await readBlobResponse(res), filename);
}

/** Render the SVG in an offscreen canvas at 2x for a print-crisp PNG. */
function svgToPng(svgText: string): Promise<Blob> {
  return new Promise((resolve, reject) => {
    const svgUrl = URL.createObjectURL(new Blob([svgText], { type: "image/svg+xml" }));
    const image = new Image();
    image.onerror = () => {
      URL.revokeObjectURL(svgUrl);
      reject(new Error("Could not render the diagram."));
    };
    image.onload = () => {
      const scale = 2;
      const canvas = document.createElement("canvas");
      canvas.width = image.naturalWidth * scale;
      canvas.height = image.naturalHeight * scale;
      const context = canvas.getContext("2d");
      if (!context) {
        URL.revokeObjectURL(svgUrl);
        reject(new Error("Canvas is unavailable in this browser."));
        return;
      }
      context.fillStyle = "#ffffff";
      context.fillRect(0, 0, canvas.width, canvas.height);
      context.scale(scale, scale);
      context.drawImage(image, 0, 0);
      URL.revokeObjectURL(svgUrl);
      canvas.toBlob(
        (png) => (png ? resolve(png) : reject(new Error("PNG export failed."))),
        "image/png",
      );
    };
    image.src = svgUrl;
  });
}

/** Download the run's PRISMA 2020 flow diagram (SVG from the API, PNG via canvas). */
export async function downloadPrismaDiagram(
  runId: RunRef,
  format: "svg" | "png",
): Promise<void> {
  const token = getToken();
  const res = await fetchApiResponse(`${API_URL}/runs/${runId}/prisma.svg`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw await toApiError(res);
  const svgText = await readTextResponse(res);
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const base = /filename="([^"]+)"/.exec(disposition)?.[1] ?? `prisma-${runId}.svg`;
  if (format === "svg") {
    triggerDownload(new Blob([svgText], { type: "image/svg+xml" }), base);
    return;
  }
  triggerDownload(await svgToPng(svgText), base.replace(/\.svg$/, ".png"));
}

/** Download any run asset that needs the auth header (bundle, evidence). */
export async function downloadRunAsset(
  runId: RunRef,
  path: string,
  fallbackName: string,
): Promise<void> {
  const token = getToken();
  const res = await fetchApiResponse(`${API_URL}/runs/${runId}/${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw await toApiError(res);
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const filename = /filename="([^"]+)"/.exec(disposition)?.[1] ?? fallbackName;
  triggerDownload(await readBlobResponse(res), filename);
}

/** Fetch the compiled PDF as an object URL for the preview iframe. */
/** Fetch a generated figure as an object URL (the image route needs auth). */
export async function fetchFigureUrl(figureId: string): Promise<string> {
  const token = getToken();
  const res = await fetchApiResponse(`${API_URL}/figures/${figureId}/image`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw await toApiError(res);
  return URL.createObjectURL(await readBlobResponse(res));
}

/** Fetch a writer asset's pixels as an object URL for the Visuals menu. */
export async function fetchWriterAssetImageUrl(
  docId: string,
  assetId: number,
): Promise<string> {
  const token = getToken();
  const res = await fetchApiResponse(`${API_URL}/writer/${docId}/assets/${assetId}/image`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw await toApiError(res);
  return URL.createObjectURL(await readBlobResponse(res));
}

/** Fetch the interview recording as an object URL (the audio route needs
 * auth, and a blob makes browser seeking instant). */
export async function fetchInterviewAudioUrl(
  interviewId: string,
  signal?: AbortSignal,
): Promise<string> {
  const token = getToken();
  const res = await fetchApiResponse(`${API_URL}/interviews/${interviewId}/audio`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    signal,
  });
  if (!res.ok) throw await toApiError(res);
  return URL.createObjectURL(await readBlobResponse(res));
}

export async function downloadInterviewReport(
  interviewId: string,
  format: "pdf" | "tex" | "txt",
): Promise<void> {
  const token = getToken();
  const res = await fetchApiResponse(
    `${API_URL}/interviews/${interviewId}/report?format=${format}`,
    { headers: token ? { Authorization: `Bearer ${token}` } : {} },
  );
  if (!res.ok) throw await toApiError(res);
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const filename =
    /filename="([^"]+)"/.exec(disposition)?.[1] ?? `interview-report.${format}`;
  triggerDownload(await readBlobResponse(res), filename);
}

export async function downloadFigureSource(figureId: string): Promise<void> {
  const token = getToken();
  const res = await fetchApiResponse(`${API_URL}/figures/${figureId}/source`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw await toApiError(res);
  triggerDownload(await readBlobResponse(res), `figure-${figureId}-source.txt`);
}

export async function downloadSurveyCsv(surveyId: string): Promise<void> {
  const token = getToken();
  const res = await fetchApiResponse(`${API_URL}/surveys/${surveyId}/export.csv`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw await toApiError(res);
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const filename = /filename="([^"]+)"/.exec(disposition)?.[1] ?? `survey-${surveyId}.csv`;
  triggerDownload(await readBlobResponse(res), filename);
}

export async function fetchWriterPdfUrl(docId: WriterRef): Promise<string> {
  const token = getToken();
  const res = await fetchApiResponse(`${API_URL}/writer/${docId}/pdf`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw await toApiError(res);
  return URL.createObjectURL(await readBlobResponse(res));
}

/** Download the live references.bib of a Writer document. */
export async function downloadWriterBib(docId: WriterRef): Promise<void> {
  const token = getToken();
  const res = await fetchApiResponse(`${API_URL}/writer/${docId}/references.bib`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw await toApiError(res);
  triggerDownload(await readBlobResponse(res), "references.bib");
}

/** Download a Writer project with every source file, asset and bibliography. */
export async function downloadWriterProject(docId: WriterRef): Promise<void> {
  const token = getToken();
  const res = await fetchApiResponse(`${API_URL}/writer/${docId}/project.zip`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw await toApiError(res);
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const filename = /filename="([^"]+)"/.exec(disposition)?.[1] ?? "manuscript.zip";
  triggerDownload(await readBlobResponse(res), filename);
}
