import type {
  BrainstormDeleteChallenge,
  BrainstormFilingChallenge,
  LiveBrainstormCreate,
  LiveTranscriptSegmentCreate,
  ProjectBrainstormSynthesisCreate,
} from "@/lib/types";

export const BRAINSTORMING_STORAGE_PREFIX = "six:brainstorming:";
export const BRAINSTORMING_CREATE_STORAGE_PREFIX = `${BRAINSTORMING_STORAGE_PREFIX}create:v1:`;
export const BRAINSTORMING_COMPOSER_STORAGE_PREFIX = `${BRAINSTORMING_STORAGE_PREFIX}composer:v1:`;
export const BRAINSTORMING_COMPLETE_STORAGE_PREFIX = `${BRAINSTORMING_STORAGE_PREFIX}complete:v1:`;
export const BRAINSTORMING_PROJECT_SYNTHESIS_STORAGE_PREFIX = `${BRAINSTORMING_STORAGE_PREFIX}project-synthesis:v1:`;
export const BRAINSTORMING_PROJECT_RETRY_STORAGE_PREFIX = `${BRAINSTORMING_STORAGE_PREFIX}project-retry:v1:`;
export const BRAINSTORMING_SPEECH_HINT_STORAGE_PREFIX = `${BRAINSTORMING_STORAGE_PREFIX}speech-hints:v1:`;

const MAX_STORED_CHARS = 1_200_000;
const SESSION_ID = /^[A-Za-z0-9_-]{1,128}$/;
const CLIENT_ID = /^[A-Za-z0-9_.:-]{8,100}$/;
const MAX_SESSION_MS = 240 * 60 * 1_000;
const MAX_SPEECH_HINTS = 8;
const MAX_SPEECH_HINT_CHARS = 60;
const MAX_SPEECH_VOCABULARY = 16;

export const BRAINSTORMING_STRUCTURAL_TERMS = [
  "Introduction",
  "Methodology",
  "Results",
  "Discussion",
  "Einleitung",
  "Methodik",
  "Ergebnisse",
  "Diskussion",
] as const;

export type BrainstormInputMode = "typed" | "microphone";

export interface BrainstormCreateIntent {
  version: 1;
  user_id: number;
  client_session_id: string;
  title: string;
  language: "auto" | "de" | "en";
  project_id: number | null;
}

export interface BrainstormAppendIntent {
  segments: LiveTranscriptSegmentCreate[];
}

export interface BrainstormComposerDraft {
  version: 1;
  user_id: number;
  session_id: string;
  mode: BrainstormInputMode;
  text: string;
  microphone_text: string;
  pending_append: BrainstormAppendIntent | null;
}

export interface BrainstormCompleteIntent {
  version: 1;
  user_id: number;
  session_id: string;
  request: LiveBrainstormCreate;
}

export interface ProjectBrainstormSynthesisIntent {
  version: 1;
  user_id: number;
  project_id: number;
  request: ProjectBrainstormSynthesisCreate;
}

export interface ProjectBrainstormRetryIntent {
  version: 1;
  user_id: number;
  project_id: number;
  synthesis_id: string;
  request: {
    client_request_id: string;
    expected_document_revision: number;
  };
}

function randomHex(): string {
  const bytes = new Uint8Array(16);
  window.crypto.getRandomValues(bytes);
  return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
}

export function createClientId(kind: "session" | "event" | "structure"): string {
  return `web_${kind}_${randomHex()}`;
}

export function splitBrainstormText(text: string, requestedLimit: number): string[] {
  const limit = Math.max(1, Math.min(4_000, Math.floor(requestedLimit)));
  let remaining = text.trim();
  const chunks: string[] = [];
  while (remaining.length > limit) {
    let cut = remaining.lastIndexOf("\n", limit);
    if (cut < Math.floor(limit / 2)) cut = remaining.lastIndexOf(" ", limit);
    if (cut < Math.floor(limit / 2)) cut = limit;
    const chunk = remaining.slice(0, cut).trim();
    if (chunk) chunks.push(chunk);
    remaining = remaining.slice(cut).trimStart();
  }
  if (remaining) chunks.push(remaining);
  return chunks;
}

export function buildAppendIntent({
  text,
  channel,
  startMs,
  maxSegmentChars,
}: {
  text: string;
  channel: BrainstormInputMode;
  startMs: number;
  maxSegmentChars: number;
}): BrainstormAppendIntent {
  const baseId = createClientId("event");
  const chunks = splitBrainstormText(text, maxSegmentChars);
  const safeStart = Math.max(0, Math.floor(startMs));
  if (safeStart + chunks.length > MAX_SESSION_MS) {
    throw new RangeError("brainstorm segment timecode exceeds the session limit");
  }
  return {
    segments: chunks.map((chunk, index) => ({
      client_event_id: `${baseId}.${index}`,
      channel,
      speaker: "Me",
      start_ms: safeStart + index,
      end_ms: safeStart + index + 1,
      text: chunk,
      is_final: true,
    })),
  };
}

function createKey(userId: number): string {
  return `${BRAINSTORMING_CREATE_STORAGE_PREFIX}${userId}`;
}

function composerKey(userId: number, sessionId: string): string {
  return `${BRAINSTORMING_COMPOSER_STORAGE_PREFIX}${userId}:${sessionId}`;
}

function completeKey(userId: number, sessionId: string): string {
  return `${BRAINSTORMING_COMPLETE_STORAGE_PREFIX}${userId}:${sessionId}`;
}

function projectSynthesisKey(userId: number, projectId: number): string {
  return `${BRAINSTORMING_PROJECT_SYNTHESIS_STORAGE_PREFIX}${userId}:${projectId}`;
}

function projectRetryKey(userId: number, projectId: number, synthesisId: string): string {
  return `${BRAINSTORMING_PROJECT_RETRY_STORAGE_PREFIX}${userId}:${projectId}:${synthesisId}`;
}

function speechHintKey(userId: number, projectId: number | null): string {
  return `${BRAINSTORMING_SPEECH_HINT_STORAGE_PREFIX}${userId}:${projectId ?? "unfiled"}`;
}

function readJson(key: string): unknown {
  try {
    const raw = window.sessionStorage.getItem(key);
    if (!raw) return null;
    if (raw.length > MAX_STORED_CHARS) {
      window.sessionStorage.removeItem(key);
      return null;
    }
    return JSON.parse(raw) as unknown;
  } catch {
    try {
      window.sessionStorage.removeItem(key);
    } catch {
      // Storage is unavailable in some hardened browser contexts.
    }
    return null;
  }
}

function writeJson(key: string, value: unknown): boolean {
  try {
    const raw = JSON.stringify(value);
    if (raw.length > MAX_STORED_CHARS) return false;
    window.sessionStorage.setItem(key, raw);
    return true;
  } catch {
    // The in-memory draft remains usable when session storage is unavailable.
    return false;
  }
}

function removeKey(key: string): void {
  try {
    window.sessionStorage.removeItem(key);
  } catch {
    // Storage is unavailable in some hardened browser contexts.
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function hasExactKeys(value: Record<string, unknown>, keys: string[]): boolean {
  return Object.keys(value).sort().join(",") === [...keys].sort().join(",");
}

/**
 * Validate the exact one-time Core challenge needed to invalidate an AI
 * project document. Callers must echo the returned object without deriving or
 * replacing any revision from local state.
 */
export function parseBrainstormFilingChallenge(detail: unknown): BrainstormFilingChallenge | null {
  if (
    !isRecord(detail)
    || detail.code !== "brainstorm_session_already_synthesized"
    || !isRecord(detail.challenge)
    || !hasExactKeys(detail.challenge, [
      "session_revision",
      "source_document_id",
      "source_document_revision",
      "source_project_id",
    ])
  ) return null;
  const challenge = detail.challenge;
  if (
    !Number.isSafeInteger(challenge.session_revision)
    || Number(challenge.session_revision) < 0
    || !Number.isSafeInteger(challenge.source_project_id)
    || Number(challenge.source_project_id) < 1
    || typeof challenge.source_document_id !== "string"
    || challenge.source_document_id.length < 8
    || challenge.source_document_id.length > 16
    || !Number.isSafeInteger(challenge.source_document_revision)
    || Number(challenge.source_document_revision) < 0
  ) return null;
  return challenge as unknown as BrainstormFilingChallenge;
}

/** Validate, but never recreate, the exact delete impact snapshot from Core. */
export function parseBrainstormDeleteChallenge(detail: unknown): BrainstormDeleteChallenge | null {
  if (
    !isRecord(detail)
    || detail.code !== "brainstorm_session_delete_requires_cleanup"
    || !isRecord(detail.challenge)
    || !hasExactKeys(detail.challenge, [
      "affected_document_count",
      "affected_synthesis_count",
      "impact_sha256",
      "pending_synthesis_count",
      "session_revision",
    ])
  ) return null;
  const challenge = detail.challenge;
  const counts = [
    challenge.affected_document_count,
    challenge.affected_synthesis_count,
    challenge.pending_synthesis_count,
  ];
  if (
    !Number.isSafeInteger(challenge.session_revision)
    || Number(challenge.session_revision) < 0
    || typeof challenge.impact_sha256 !== "string"
    || !/^[a-f0-9]{64}$/.test(challenge.impact_sha256)
    || counts.some((count) => !Number.isSafeInteger(count) || Number(count) < 0)
  ) return null;
  return challenge as unknown as BrainstormDeleteChallenge;
}

function isSegment(value: unknown): value is LiveTranscriptSegmentCreate {
  if (!isRecord(value)) return false;
  return (
    hasExactKeys(value, [
      "channel",
      "client_event_id",
      "end_ms",
      "is_final",
      "speaker",
      "start_ms",
      "text",
    ])
    && typeof value.client_event_id === "string"
    && CLIENT_ID.test(value.client_event_id)
    && (value.channel === "typed" || value.channel === "microphone")
    && typeof value.speaker === "string"
    && value.speaker.length >= 1
    && value.speaker.length <= 160
    && Number.isInteger(value.start_ms)
    && Number.isInteger(value.end_ms)
    && Number(value.start_ms) >= 0
    && Number(value.end_ms) > Number(value.start_ms)
    && Number(value.end_ms) <= MAX_SESSION_MS
    && typeof value.text === "string"
    && value.text.trim().length >= 1
    && value.text.length <= 4_000
    && value.is_final === true
  );
}

export function readCreateIntent(userId: number): BrainstormCreateIntent | null {
  const value = readJson(createKey(userId));
  if (!isRecord(value) || !hasExactKeys(value, [
    "client_session_id", "language", "project_id", "title", "user_id", "version",
  ])) return null;
  if (
    value.version !== 1
    || value.user_id !== userId
    || typeof value.client_session_id !== "string"
    || !CLIENT_ID.test(value.client_session_id)
    || typeof value.title !== "string"
    || value.title.trim().length < 1
    || value.title.length > 300
    || (value.language !== "auto" && value.language !== "de" && value.language !== "en")
    || (value.project_id !== null && (!Number.isInteger(value.project_id) || Number(value.project_id) < 1))
  ) return null;
  return value as unknown as BrainstormCreateIntent;
}

export function writeCreateIntent(value: BrainstormCreateIntent): boolean {
  return writeJson(createKey(value.user_id), value);
}

export function removeCreateIntent(userId: number): void {
  removeKey(createKey(userId));
}

export function readComposerDraft(userId: number, sessionId: string): BrainstormComposerDraft | null {
  if (!SESSION_ID.test(sessionId)) return null;
  const value = readJson(composerKey(userId, sessionId));
  if (!isRecord(value) || !hasExactKeys(value, [
    "microphone_text", "mode", "pending_append", "session_id", "text", "user_id", "version",
  ])) return null;
  const pending = value.pending_append;
  if (
    value.version !== 1
    || value.user_id !== userId
    || value.session_id !== sessionId
    || (value.mode !== "typed" && value.mode !== "microphone")
    || typeof value.text !== "string"
    || value.text.length > 500_000
    || typeof value.microphone_text !== "string"
    || value.microphone_text.length > 500_000
    || (pending !== null && (
      !isRecord(pending)
      || !hasExactKeys(pending, ["segments"])
      || !Array.isArray(pending.segments)
      || pending.segments.length < 1
      || pending.segments.length > 100
      || !pending.segments.every(isSegment)
    ))
  ) return null;
  return value as unknown as BrainstormComposerDraft;
}

export function writeComposerDraft(value: BrainstormComposerDraft): boolean {
  return writeJson(composerKey(value.user_id, value.session_id), value);
}

export function removeComposerDraft(userId: number, sessionId: string): void {
  removeKey(composerKey(userId, sessionId));
}

export function readCompleteIntent(userId: number, sessionId: string): BrainstormCompleteIntent | null {
  if (!SESSION_ID.test(sessionId)) return null;
  const value = readJson(completeKey(userId, sessionId));
  if (!isRecord(value) || !hasExactKeys(value, ["request", "session_id", "user_id", "version"])) return null;
  const request = value.request;
  if (
    value.version !== 1
    || value.user_id !== userId
    || value.session_id !== sessionId
    || !isRecord(request)
    || !hasExactKeys(request, ["client_request_id", "context_through_sequence", "output_language", "schema_version"])
    || typeof request.client_request_id !== "string"
    || !CLIENT_ID.test(request.client_request_id)
    || !Number.isInteger(request.context_through_sequence)
    || Number(request.context_through_sequence) < 1
    || (request.output_language !== "de" && request.output_language !== "en")
    || request.schema_version !== 1
  ) return null;
  return value as unknown as BrainstormCompleteIntent;
}

export function writeCompleteIntent(value: BrainstormCompleteIntent): boolean {
  return writeJson(completeKey(value.user_id, value.session_id), value);
}

export function removeCompleteIntent(userId: number, sessionId: string): void {
  removeKey(completeKey(userId, sessionId));
}

function isProjectSynthesisRequest(value: unknown): value is ProjectBrainstormSynthesisCreate {
  if (!isRecord(value) || !hasExactKeys(value, [
    "client_request_id",
    "expected_document_revision",
    "include_all_completed",
    "output_language",
    "schema_version",
    "session_ids",
  ])) return false;
  return (
    typeof value.client_request_id === "string"
    && CLIENT_ID.test(value.client_request_id)
    && Number.isInteger(value.expected_document_revision)
    && Number(value.expected_document_revision) >= 0
    && typeof value.include_all_completed === "boolean"
    && (value.output_language === "de" || value.output_language === "en")
    && value.schema_version === 1
    && Array.isArray(value.session_ids)
    && value.session_ids.length <= 50
    && value.session_ids.every((id) => typeof id === "string" && SESSION_ID.test(id))
    && new Set(value.session_ids).size === value.session_ids.length
    && (
      (value.include_all_completed && value.session_ids.length === 0)
      || (!value.include_all_completed && value.session_ids.length >= 2)
    )
  );
}

export function readProjectSynthesisIntent(
  userId: number,
  projectId: number,
): ProjectBrainstormSynthesisIntent | null {
  const value = readJson(projectSynthesisKey(userId, projectId));
  if (!isRecord(value) || !hasExactKeys(value, ["project_id", "request", "user_id", "version"])) {
    return null;
  }
  if (
    value.version !== 1
    || value.user_id !== userId
    || value.project_id !== projectId
    || !isProjectSynthesisRequest(value.request)
  ) return null;
  return value as unknown as ProjectBrainstormSynthesisIntent;
}

export function writeProjectSynthesisIntent(value: ProjectBrainstormSynthesisIntent): boolean {
  return writeJson(projectSynthesisKey(value.user_id, value.project_id), value);
}

export function removeProjectSynthesisIntent(userId: number, projectId: number): void {
  removeKey(projectSynthesisKey(userId, projectId));
}

export function readProjectRetryIntent(
  userId: number,
  projectId: number,
  synthesisId: string,
): ProjectBrainstormRetryIntent | null {
  if (!SESSION_ID.test(synthesisId)) return null;
  const value = readJson(projectRetryKey(userId, projectId, synthesisId));
  if (!isRecord(value) || !hasExactKeys(value, [
    "project_id", "request", "synthesis_id", "user_id", "version",
  ])) return null;
  const request = value.request;
  if (
    value.version !== 1
    || value.user_id !== userId
    || value.project_id !== projectId
    || value.synthesis_id !== synthesisId
    || !isRecord(request)
    || !hasExactKeys(request, ["client_request_id", "expected_document_revision"])
    || typeof request.client_request_id !== "string"
    || !CLIENT_ID.test(request.client_request_id)
    || !Number.isInteger(request.expected_document_revision)
    || Number(request.expected_document_revision) < 0
  ) return null;
  return value as unknown as ProjectBrainstormRetryIntent;
}

export function writeProjectRetryIntent(value: ProjectBrainstormRetryIntent): boolean {
  return writeJson(
    projectRetryKey(value.user_id, value.project_id, value.synthesis_id),
    value,
  );
}

export function removeProjectRetryIntent(
  userId: number,
  projectId: number,
  synthesisId: string,
): void {
  if (!SESSION_ID.test(synthesisId)) return;
  removeKey(projectRetryKey(userId, projectId, synthesisId));
}

export function normalizeSpeechHints(value: string | string[]): string[] {
  const entries = Array.isArray(value) ? value : value.split(/[\n,;]/);
  const seen = new Set<string>();
  const hints: string[] = [];
  for (const entry of entries) {
    const hint = entry.trim().replaceAll(/\s+/g, " ").slice(0, MAX_SPEECH_HINT_CHARS);
    const identity = hint.toLocaleLowerCase();
    if (!hint || seen.has(identity)) continue;
    seen.add(identity);
    hints.push(hint);
    if (hints.length >= MAX_SPEECH_HINTS) break;
  }
  return hints;
}

export function buildSpeechVocabulary(
  language: "de" | "en",
  projectName: string | null,
  hints: string[],
): string[] {
  const languageTerms = language === "de"
    ? [
        "Einleitung", "Methodik", "Ergebnisse", "Diskussion",
        "Introduction", "Methodology", "Results", "Discussion",
      ]
    : [
        "Introduction", "Methodology", "Results", "Discussion",
        "Einleitung", "Methodik", "Ergebnisse", "Diskussion",
      ];
  const entries = [
    ...languageTerms,
    ...(projectName ? [projectName] : []),
    ...hints,
  ];
  const seen = new Set<string>();
  const vocabulary: string[] = [];
  for (const entry of entries) {
    const phrase = entry.trim().replaceAll(/\s+/g, " ").slice(0, MAX_SPEECH_HINT_CHARS);
    const identity = phrase.toLocaleLowerCase();
    if (!phrase || seen.has(identity)) continue;
    seen.add(identity);
    vocabulary.push(phrase);
    if (vocabulary.length >= MAX_SPEECH_VOCABULARY) break;
  }
  return vocabulary;
}

export function readSpeechHints(userId: number, projectId: number | null): string[] {
  try {
    const raw = window.sessionStorage.getItem(speechHintKey(userId, projectId));
    if (!raw || raw.length > 2_000) return [];
    const parsed = JSON.parse(raw) as unknown;
    return Array.isArray(parsed) && parsed.every((item) => typeof item === "string")
      ? normalizeSpeechHints(parsed)
      : [];
  } catch {
    return [];
  }
}

export function writeSpeechHints(userId: number, projectId: number | null, hints: string[]): boolean {
  try {
    window.sessionStorage.setItem(
      speechHintKey(userId, projectId),
      JSON.stringify(normalizeSpeechHints(hints)),
    );
    return true;
  } catch {
    return false;
  }
}
