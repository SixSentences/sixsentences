"use client";

import {
  type FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  AudioLines,
  BrainCircuit,
  ChevronDown,
  ChevronRight,
  CircleStop,
  FileText,
  FolderKanban,
  ListTree,
  Loader2,
  Mic,
  Plus,
  Search,
  Send,
  Trash2,
  X,
} from "lucide-react";
import { toast } from "sonner";

import { BrainstormResult } from "@/components/brainstorming/brainstorm-result";
import { ProjectBrainstormWorkspace } from "@/components/brainstorming/project-brainstorm-workspace";
import { ConfirmDeleteDialog, type ConfirmDeleteTarget } from "@/components/confirm-delete-dialog";
import { EditorialEmptyState } from "@/components/workspace/editorial-workspace";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { ResizableWorkspaceSplit } from "@/components/workspace/resizable-workspace-split";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  buildAppendIntent,
  buildSpeechVocabulary,
  createClientId,
  normalizeSpeechHints,
  parseBrainstormDeleteChallenge,
  parseBrainstormFilingChallenge,
  readCompleteIntent,
  readComposerDraft,
  readCreateIntent,
  readSpeechHints,
  removeCompleteIntent,
  removeComposerDraft,
  removeCreateIntent,
  writeCompleteIntent,
  writeComposerDraft,
  writeCreateIntent,
  writeSpeechHints,
  type BrainstormInputMode,
} from "@/lib/brainstorming";
import { api, ApiError, retryTransientApiQuery, transientApiRetryDelay } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useActiveProject } from "@/lib/project-context";
import type {
  BrainstormDeleteChallenge,
  BrainstormFilingChallenge,
  LiveAudioChannel,
  LiveBrainstormReceipt,
  LiveSession,
  LiveSessionEvent,
  LiveSessionPage,
  LiveSessionSegment,
} from "@/lib/types";
import { cn } from "@/lib/utils";

const SESSION_ID = /^[A-Za-z0-9_-]{1,128}$/;
const EVIDENCE_ID = /^[A-Za-z0-9_.:-]{8,100}$/;
const RECENT_SESSION_LIMIT = 100;
const VISIBLE_EVENT_LIMIT = 300;

type SpeechResultLike = {
  isFinal: boolean;
  0: { transcript: string };
};

type SpeechEventLike = {
  resultIndex: number;
  results: ArrayLike<SpeechResultLike>;
};

type SpeechRecognitionLike = {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  phrases?: SpeechRecognitionPhraseLike[];
  start: () => void;
  stop: () => void;
  abort: () => void;
  onresult: ((event: SpeechEventLike) => void) | null;
  onerror: ((event: { error?: string }) => void) | null;
  onend: (() => void) | null;
  onspeechstart: (() => void) | null;
  onspeechend: (() => void) | null;
};

type SpeechNotice = {
  tone: "neutral" | "warning" | "error";
  message: string;
};

type SpeechRecognitionPhraseLike = {
  phrase: string;
  boost: number;
};

type SpeechRecognitionPhraseConstructor = new (
  phrase: string,
  boost: number,
) => SpeechRecognitionPhraseLike;

type SpeechRecognitionConstructor = new () => SpeechRecognitionLike;

type TimelineSegment = {
  id: string;
  channel: LiveAudioChannel;
  text: string;
  startMs: number;
  endMs: number;
};

type ProjectMoveConfirmation = {
  userId: number;
  sessionId: string;
  sourceProjectName: string;
  targetProjectId: number | null;
  targetProjectName: string;
  challenge: BrainstormFilingChallenge;
};

type DeleteConfirmation = {
  userId: number;
  sessionId: string;
  sessionTitle: string;
} & (
  | { kind: "session" }
  | { kind: "project_cleanup"; challenge: BrainstormDeleteChallenge }
);

function speechRecognitionConstructor(): SpeechRecognitionConstructor | null {
  if (typeof window === "undefined") return null;
  const speechWindow = window as typeof window & {
    SpeechRecognition?: SpeechRecognitionConstructor;
    webkitSpeechRecognition?: SpeechRecognitionConstructor;
  };
  return speechWindow.SpeechRecognition ?? speechWindow.webkitSpeechRecognition ?? null;
}

function applySpeechVocabulary(
  recognition: SpeechRecognitionLike,
  vocabulary: string[],
): boolean {
  if (typeof window === "undefined" || !("phrases" in recognition)) return false;
  const phraseWindow = window as typeof window & {
    SpeechRecognitionPhrase?: SpeechRecognitionPhraseConstructor;
  };
  const Phrase = phraseWindow.SpeechRecognitionPhrase;
  if (!Phrase) return false;
  try {
    recognition.phrases = vocabulary.map(
      (phrase) => new Phrase(phrase, 5),
    );
    return true;
  } catch {
    return false;
  }
}

function speechNoticeForError(error: string | undefined, german: boolean): SpeechNotice | null {
  switch (error) {
    case "aborted":
      return null;
    case "no-speech":
      return {
        tone: "neutral",
        message: german
          ? "Ich habe noch keine Sprache erkannt. Tippe auf das Mikrofon, sobald du bereit bist."
          : "I did not hear any speech yet. Tap the microphone when you are ready.",
      };
    case "not-allowed":
      return {
        tone: "error",
        message: german
          ? "Der Mikrofonzugriff ist blockiert. Erlaube ihn in den Website-Einstellungen deines Browsers und versuche es erneut."
          : "Microphone access is blocked. Allow it in your browser's site settings, then try again.",
      };
    case "service-not-allowed":
      return {
        tone: "error",
        message: german
          ? "Die Spracherkennung ist in diesem Browser deaktiviert. Aktiviere sie in den Browser-Einstellungen oder wechsle zu Tippen."
          : "Speech recognition is disabled in this browser. Enable it in browser settings or switch to Type.",
      };
    case "audio-capture":
      return {
        tone: "error",
        message: german
          ? "Es ist kein verfügbares Mikrofon verbunden. Verbinde oder wähle ein Mikrofon und versuche es erneut."
          : "No available microphone was found. Connect or select a microphone, then try again.",
      };
    case "network":
      return {
        tone: "warning",
        message: german
          ? "Die Spracherkennung hat die Verbindung verloren. Prüfe deine Internetverbindung und setze dann fort."
          : "Speech recognition lost its connection. Check your internet connection, then continue.",
      };
    case "language-not-supported":
      return {
        tone: "warning",
        message: german
          ? "Dieser Browser kann die gewählte Sprache nicht erkennen. Wechsle die Session-Sprache oder nutze Tippen."
          : "This browser cannot recognise the selected language. Change the session language or use Type.",
      };
    default:
      return {
        tone: "warning",
        message: german
          ? "Die Spracherkennung wurde unerwartet beendet. Dein Entwurf ist weiterhin da; tippe zum Fortsetzen erneut auf das Mikrofon."
          : "Speech recognition ended unexpectedly. Your draft is still here; tap the microphone again to continue.",
      };
  }
}

function appendRecognisedText(current: string, next: string, maxChars: number): string {
  const addition = next.trim();
  if (!addition || maxChars < 1) return current;
  return `${current}${current.trim() ? " " : ""}${addition}`.slice(0, maxChars);
}

function SpeechWave({
  active,
  pulse,
  side,
}: {
  active: boolean;
  pulse: number;
  side: "left" | "right";
}) {
  return (
    <span
      aria-hidden="true"
      data-speech-wave={side}
      data-active={active ? "true" : "false"}
      className="flex h-10 w-16 items-center justify-center gap-1"
    >
      {[0, 1, 2, 3, 4].map((index) => {
        const distanceFromMic = side === "left" ? index : 4 - index;
        const height = 8 + ((pulse * 7 + distanceFromMic * 11) % 24);
        return (
          <span
            key={index}
            className={cn(
              "w-1 rounded-full bg-moss transition-[height,opacity] duration-150 motion-reduce:transition-none",
              active
                ? "animate-pulse opacity-75 motion-reduce:animate-none"
                : "h-1 opacity-0",
            )}
            style={active ? {
              height: `${height}px`,
              animationDelay: `${distanceFromMic * 55}ms`,
            } : undefined}
          />
        );
      })}
    </span>
  );
}

function formatDate(value: string, german: boolean): string {
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return german ? "Datum unbekannt" : "Date unknown";
  return new Intl.DateTimeFormat(german ? "de-DE" : "en-US", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(timestamp);
}

function statusCopy(session: LiveSession, german: boolean): string {
  if (session.status === "recording") return german ? "Offen" : "Open";
  if (session.status === "completed") return german ? "Abgeschlossen" : "Closed";
  if (session.status === "failed") return german ? "Fehlgeschlagen" : "Failed";
  return german ? "Abgebrochen" : "Cancelled";
}

function errorCode(error: unknown): string | null {
  if (!(error instanceof ApiError) || !error.detail || typeof error.detail !== "object") {
    return null;
  }
  const code = (error.detail as { code?: unknown }).code;
  return typeof code === "string" ? code : null;
}

function newestReceipt(receipts: LiveBrainstormReceipt[]): LiveBrainstormReceipt | null {
  return receipts[0] ?? null;
}

function segmentFromEvent(event: LiveSessionEvent): TimelineSegment | null {
  if (event.type !== "segment") return null;
  const payload = event.payload;
  const channel = payload.channel;
  if (channel !== "microphone" && channel !== "system" && channel !== "typed") return null;
  if (
    typeof payload.segment_id !== "string"
    || typeof payload.text !== "string"
    || typeof payload.start_ms !== "number"
    || typeof payload.end_ms !== "number"
  ) return null;
  return {
    id: payload.segment_id,
    channel,
    text: payload.text,
    startMs: payload.start_ms,
    endMs: payload.end_ms,
  };
}

function segmentFromExact(
  segment: LiveSessionSegment | undefined,
  expectedId: string | null,
): TimelineSegment | null {
  if (!segment || !expectedId || segment.id !== expectedId) return null;
  return {
    id: segment.id,
    channel: segment.channel,
    text: segment.text,
    startMs: segment.start_ms,
    endMs: segment.end_ms,
  };
}

function defaultTitle(german: boolean): string {
  const date = new Intl.DateTimeFormat(german ? "de-DE" : "en-US", {
    dateStyle: "medium",
  }).format(new Date());
  return `${german ? "Brainstorming" : "Brainstorm"} · ${date}`;
}

export default function BrainstormingPage() {
  const { me } = useAuth();
  const userId = me?.user_id ?? null;
  const german = me?.language === "de";
  const router = useRouter();
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();
  const { activeProjectId, setActiveProjectId } = useActiveProject();
  const requestedSession = searchParams.get("session") ?? "";
  const selectedSessionId = SESSION_ID.test(requestedSession) ? requestedSession : null;
  const invalidDeepLink = Boolean(requestedSession) && !selectedSessionId;
  const requestedProject = searchParams.get("project") ?? "";
  const parsedProjectId = /^\d{1,12}$/.test(requestedProject) ? Number(requestedProject) : null;
  const projectScopeId = parsedProjectId ?? activeProjectId;
  const requestedProjectView = searchParams.get("view") === "project";
  const requestedEvidence = EVIDENCE_ID.test(searchParams.get("evidence") ?? "")
    ? searchParams.get("evidence")
    : null;
  const hasMobileDetail = Boolean(selectedSessionId || (projectScopeId && requestedProjectView));
  const [search, setSearch] = useState("");
  const [composerText, setComposerText] = useState("");
  const [inputMode, setInputMode] = useState<BrainstormInputMode>("typed");
  const [interimText, setInterimText] = useState("");
  const [speechNotice, setSpeechNotice] = useState<SpeechNotice | null>(null);
  const [recording, setRecording] = useState(false);
  const [speechStopping, setSpeechStopping] = useState(false);
  const [speechActive, setSpeechActive] = useState(false);
  const [speechPulse, setSpeechPulse] = useState(0);
  const [starting, setStarting] = useState(false);
  const [appending, setAppending] = useState(false);
  const [pendingAppend, setPendingAppend] = useState(false);
  const [appendFailure, setAppendFailure] = useState<"retryable" | "rejected" | null>(null);
  const [revalidatingDraft, setRevalidatingDraft] = useState(false);
  const [structuring, setStructuring] = useState(false);
  const [structureFailureCode, setStructureFailureCode] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [brainstormLanguage, setBrainstormLanguage] = useState<"de" | "en">(german ? "de" : "en");
  const [languageUpdating, setLanguageUpdating] = useState(false);
  const [projectUpdating, setProjectUpdating] = useState(false);
  const [projectUpdateError, setProjectUpdateError] = useState<string | null>(null);
  const [projectMoveConfirmation, setProjectMoveConfirmation] = useState<ProjectMoveConfirmation | null>(null);
  const [projectMoveDialogError, setProjectMoveDialogError] = useState<string | null>(null);
  const [discardDraftSessionId, setDiscardDraftSessionId] = useState<string | null>(null);
  const [deleteConfirmation, setDeleteConfirmation] = useState<DeleteConfirmation | null>(null);
  const [deleteDialogError, setDeleteDialogError] = useState<string | null>(null);
  const [speechHintText, setSpeechHintText] = useState("");
  const [speechHintSupport, setSpeechHintSupport] = useState<boolean | null>(null);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const recordingWantedRef = useRef(false);
  const interimTextRef = useRef("");
  const speechEventActiveRef = useRef(false);
  const speechActivityTimerRef = useRef<number | null>(null);
  const speechStopTimerRef = useRef<number | null>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const newButtonRef = useRef<HTMLButtonElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const detailHeadingRef = useRef<HTMLHeadingElement>(null);
  const restoreSessionFocusRef = useRef<string | null>(null);
  const restoreListRequestedRef = useRef(false);
  const sessionRowRefs = useRef(new Map<string, HTMLButtonElement>());
  const locatedEvidenceRef = useRef<string | null>(null);

  const config = useQuery({
    queryKey: ["live-session-config"],
    queryFn: api.liveSessionConfig,
  });
  const projectScope = config.data?.projects.find((project) => project.id === projectScopeId) ?? null;
  const sessions = useQuery({
    queryKey: ["brainstorming-sessions", userId],
    queryFn: () => api.liveSessions(RECENT_SESSION_LIMIT, "brainstorm"),
    enabled: userId !== null,
    refetchInterval: (query) =>
      query.state.data?.sessions.some((session) => session.purpose === "brainstorm" && session.status === "recording")
        ? 2_000
        : 10_000,
    refetchIntervalInBackground: false,
    refetchOnReconnect: true,
    refetchOnWindowFocus: true,
  });
  const brainstorms = useMemo(
    () => (sessions.data?.sessions ?? []).filter((session) => session.purpose === "brainstorm"),
    [sessions.data?.sessions],
  );
  const visibleBrainstorms = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase(german ? "de-DE" : "en-US");
    return brainstorms.filter((session) => (
      (projectScopeId === null || session.project_id === projectScopeId)
      && (!needle || session.title.toLocaleLowerCase(german ? "de-DE" : "en-US").includes(needle))
    ));
  }, [brainstorms, german, projectScopeId, search]);

  const selected = useQuery({
    queryKey: ["brainstorming-session", userId, selectedSessionId],
    queryFn: () => api.liveSession(selectedSessionId!),
    enabled: userId !== null && selectedSessionId !== null,
    retry: retryTransientApiQuery,
    retryDelay: transientApiRetryDelay,
    refetchInterval: (query) => query.state.data?.status === "recording" ? 2_000 : false,
    refetchOnWindowFocus: true,
  });
  const session = selected.data ?? brainstorms.find((item) => item.id === selectedSessionId) ?? null;
  // A list entry is not a substitute for detail authorization. Only an explicit
  // denied/missing response (or the wrong resource kind) gets unavailable copy.
  const selectedUnavailable = selected.error instanceof ApiError
    && [401, 403, 404].includes(selected.error.status);
  const selectedWrongPurpose = !selected.isError && session !== null && session.purpose !== "brainstorm";
  const eventAfter = Math.max(0, (session?.last_segment_sequence ?? 0) - VISIBLE_EVENT_LIMIT);
  const events = useQuery({
    queryKey: ["brainstorming-events", userId, selectedSessionId, eventAfter],
    queryFn: () => api.liveSessionEvents(selectedSessionId!, eventAfter, VISIBLE_EVENT_LIMIT),
    enabled: Boolean(userId && selectedSessionId && session?.purpose === "brainstorm"),
    refetchInterval: session?.status === "recording" ? 2_000 : false,
    refetchOnWindowFocus: true,
  });
  const segments = useMemo(
    () => (events.data?.events ?? []).map(segmentFromEvent).filter((item): item is NonNullable<typeof item> => item !== null),
    [events.data?.events],
  );
  const requestedEvidenceInTail = Boolean(
    requestedEvidence && segments.some((segment) => segment.id === requestedEvidence),
  );
  const exactEvidence = useQuery({
    queryKey: ["brainstorming-segment", userId, selectedSessionId, requestedEvidence],
    queryFn: () => api.liveSessionSegment(selectedSessionId!, requestedEvidence!),
    enabled: Boolean(
      userId
      && selectedSessionId
      && requestedEvidence
      && session?.purpose === "brainstorm"
      && !events.isLoading
      && !events.isError
      && !requestedEvidenceInTail
    ),
    retry: false,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });
  const exactEvidenceSegment = useMemo(
    () => segmentFromExact(exactEvidence.data, requestedEvidence),
    [exactEvidence.data, requestedEvidence],
  );
  const locatedEvidenceSegment = requestedEvidenceInTail ? null : exactEvidenceSegment;
  const exactEvidenceFailed = !requestedEvidenceInTail && (
    exactEvidence.isError || (exactEvidence.isSuccess && !exactEvidenceSegment)
  );
  const exactEvidenceNotFound = exactEvidence.error instanceof ApiError
    && exactEvidence.error.status === 404;
  const receipts = useQuery({
    queryKey: ["brainstorming-receipts", userId, selectedSessionId],
    queryFn: () => api.liveSessionBrainstorms(selectedSessionId!, 20),
    enabled: Boolean(userId && selectedSessionId && session?.purpose === "brainstorm"),
    refetchInterval: (query) => {
      const status = newestReceipt(query.state.data?.brainstorms ?? [])?.status;
      return status === "pending" || session?.status === "recording" ? 2_000 : 10_000;
    },
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
  });
  const latestReceipt = newestReceipt(receipts.data?.brainstorms ?? []);
  const remainingTranscriptChars = Math.max(
    0,
    (config.data?.brainstorm.max_transcript_chars ?? 500_000)
      - (session?.transcript_char_count ?? 0),
  );
  const remainingSegmentSlots = Math.max(
    0,
    (config.data?.brainstorm.max_segments ?? 10_000) - (session?.segment_count ?? 0),
  );
  const composerSegmentLimit = Math.min(
    config.data?.max_batch_segments ?? 100,
    remainingSegmentSlots,
  );
  const composerMaxChars = Math.min(
    remainingTranscriptChars,
    composerSegmentLimit * (config.data?.max_segment_chars ?? 4_000),
  );

  const resetSpeechActivity = useCallback(() => {
    speechEventActiveRef.current = false;
    if (speechActivityTimerRef.current !== null) {
      window.clearTimeout(speechActivityTimerRef.current);
      speechActivityTimerRef.current = null;
    }
    setSpeechActive(false);
  }, []);

  const beginSpeechActivity = useCallback(() => {
    speechEventActiveRef.current = true;
    if (speechActivityTimerRef.current !== null) {
      window.clearTimeout(speechActivityTimerRef.current);
      speechActivityTimerRef.current = null;
    }
    setSpeechPulse((current) => current + 1);
    setSpeechActive(true);
  }, []);

  const endSpeechActivity = useCallback(() => {
    speechEventActiveRef.current = false;
    if (speechActivityTimerRef.current !== null) {
      window.clearTimeout(speechActivityTimerRef.current);
      speechActivityTimerRef.current = null;
    }
    setSpeechActive(false);
  }, []);

  const markRecognitionActivity = useCallback(() => {
    setSpeechPulse((current) => current + 1);
    setSpeechActive(true);
    if (speechEventActiveRef.current) return;
    if (speechActivityTimerRef.current !== null) {
      window.clearTimeout(speechActivityTimerRef.current);
    }
    speechActivityTimerRef.current = window.setTimeout(() => {
      speechActivityTimerRef.current = null;
      setSpeechActive(false);
    }, 700);
  }, []);

  const preserveInterimText = useCallback(() => {
    const retained = interimTextRef.current.trim();
    interimTextRef.current = "";
    setInterimText("");
    if (!retained) return;
    setComposerText((current) => appendRecognisedText(current, retained, composerMaxChars));
  }, [composerMaxChars]);

  useEffect(() => {
    if (!userId || !session || !latestReceipt) return;
    setStructureFailureCode(null);
    const pending = readCompleteIntent(userId, session.id);
    if (pending?.request.client_request_id === latestReceipt.client_request_id) {
      removeCompleteIntent(userId, session.id);
    }
  }, [latestReceipt, session, userId]);

  const selectSession = useCallback((
    id: string | null,
    replace = false,
    evidenceId?: string,
  ) => {
    const params = new URLSearchParams();
    if (projectScopeId) params.set("project", String(projectScopeId));
    if (id) params.set("session", id);
    if (id && evidenceId && EVIDENCE_ID.test(evidenceId)) params.set("evidence", evidenceId);
    const query = params.toString();
    const href = query ? `/brainstorming?${query}` : "/brainstorming";
    if (replace) router.replace(href);
    else router.push(href);
  }, [projectScopeId, router]);

  const openProjectDocument = useCallback(() => {
    if (!projectScopeId) return;
    router.push(`/brainstorming?project=${projectScopeId}&view=project`);
  }, [projectScopeId, router]);

  useEffect(() => {
    if (!invalidDeepLink) return;
    router.replace("/brainstorming");
  }, [invalidDeepLink, router]);

  useEffect(() => {
    if (!config.isSuccess || !requestedProject) return;
    if (parsedProjectId && config.data.projects.some((project) => project.id === parsedProjectId)) {
      setActiveProjectId(parsedProjectId);
      return;
    }
    router.replace("/brainstorming");
  }, [config.data?.projects, config.isSuccess, parsedProjectId, requestedProject, router, setActiveProjectId]);

  useEffect(() => {
    if (!userId || !session) {
      setComposerText("");
      setInputMode("typed");
      setPendingAppend(false);
      setAppendFailure(null);
      return;
    }
    const stored = readComposerDraft(userId, session.id);
    const pendingText = stored?.pending_append?.segments.map((segment) => segment.text).join("\n\n") ?? "";
    setComposerText(stored?.text || pendingText);
    setInputMode(stored?.mode ?? "typed");
    interimTextRef.current = "";
    setInterimText("");
    setSpeechNotice(null);
    setPendingAppend(Boolean(stored?.pending_append));
    setAppendFailure(stored?.pending_append ? "retryable" : null);
    setStructureFailureCode(null);
    setProjectUpdateError(null);
    setSpeechHintSupport(null);
    setBrainstormLanguage(
      session.language === "de" || session.language === "en"
        ? session.language
        : (german ? "de" : "en"),
    );
  }, [german, session?.id, userId]);

  useEffect(() => {
    setProjectMoveConfirmation((current) => (
      current
      && (current.userId !== userId || current.sessionId !== selectedSessionId)
        ? null
        : current
    ));
    setDeleteConfirmation((current) => (
      current
      && (current.userId !== userId || current.sessionId !== selectedSessionId)
        ? null
        : current
    ));
    setDiscardDraftSessionId((current) => (
      current && current !== selectedSessionId ? null : current
    ));
    setProjectMoveDialogError(null);
    setDeleteDialogError(null);
  }, [selectedSessionId, userId]);

  useEffect(() => {
    if (!userId || !session || !pendingAppend || segments.length === 0) return;
    const stored = readComposerDraft(userId, session.id);
    const pendingIds = stored?.pending_append?.segments.map((segment) => segment.client_event_id) ?? [];
    if (pendingIds.length === 0) return;
    const visibleIds = new Set(segments.map((segment) => segment.id));
    if (!pendingIds.every((id) => visibleIds.has(id))) return;
    removeComposerDraft(userId, session.id);
    removeCompleteIntent(userId, session.id);
    setComposerText("");
    setInterimText("");
    setPendingAppend(false);
    setAppendFailure(null);
  }, [pendingAppend, segments, session, userId]);

  useEffect(() => {
    if (!userId || !session || session.status !== "recording") return;
    const stored = readComposerDraft(userId, session.id);
    if (pendingAppend || stored?.pending_append) return;
    writeComposerDraft({
      version: 1,
      user_id: userId,
      session_id: session.id,
      mode: inputMode,
      text: composerText,
      microphone_text: inputMode === "microphone" ? composerText : (stored?.microphone_text ?? ""),
      pending_append: stored?.pending_append ?? null,
    });
  }, [composerText, inputMode, pendingAppend, session, userId]);

  const abortSpeech = useCallback(() => {
    recordingWantedRef.current = false;
    const recognition = recognitionRef.current;
    recognitionRef.current = null;
    if (recognition) {
      recognition.onresult = null;
      recognition.onerror = null;
      recognition.onend = null;
      recognition.onspeechstart = null;
      recognition.onspeechend = null;
      recognition.abort();
    }
    if (speechStopTimerRef.current !== null) {
      window.clearTimeout(speechStopTimerRef.current);
      speechStopTimerRef.current = null;
    }
    resetSpeechActivity();
    setRecording(false);
    setSpeechStopping(false);
    interimTextRef.current = "";
    setInterimText("");
  }, [resetSpeechActivity]);

  const changeProjectScope = useCallback((projectId: number | null) => {
    abortSpeech();
    setActiveProjectId(projectId);
    setSearch("");
    if (projectId) router.push(`/brainstorming?project=${projectId}`);
    else router.push("/brainstorming");
  }, [abortSpeech, router, setActiveProjectId]);

  useEffect(() => () => abortSpeech(), [abortSpeech]);

  useEffect(() => {
    abortSpeech();
  }, [abortSpeech, selectedSessionId]);

  useEffect(() => {
    if (!userId) {
      setSpeechHintText("");
      return;
    }
    const hintProjectId = session?.project_id ?? projectScopeId;
    setSpeechHintText(readSpeechHints(userId, hintProjectId).join(", "));
  }, [projectScopeId, session?.id, session?.project_id, userId]);

  useEffect(() => {
    if (session && session.status !== "recording") abortSpeech();
  }, [abortSpeech, session?.id, session?.status]);

  useEffect(() => {
    if (
      selectedSessionId
      && !selected.isLoading
      && !selected.isError
      && selected.data?.purpose === "brainstorm"
    ) {
      window.requestAnimationFrame(() => detailHeadingRef.current?.focus());
      return;
    }
    if (!restoreListRequestedRef.current) return;
    restoreListRequestedRef.current = false;
    const restoreId = restoreSessionFocusRef.current;
    restoreSessionFocusRef.current = null;
    window.requestAnimationFrame(() => {
      if (restoreId && sessionRowRefs.current.get(restoreId)?.isConnected) {
        sessionRowRefs.current.get(restoreId)?.focus();
        return;
      }
      if (searchInputRef.current?.isConnected) searchInputRef.current.focus();
      else newButtonRef.current?.focus();
    });
  }, [selected.data?.id, selected.data?.purpose, selected.isError, selected.isLoading, selectedSessionId]);

  const updateSessionCaches = useCallback((next: LiveSession) => {
    if (!userId) return;
    queryClient.setQueryData(["brainstorming-session", userId, next.id], next);
    queryClient.setQueryData<LiveSessionPage>(["brainstorming-sessions", userId], (current) => {
      if (!current) return current;
      const exists = current.sessions.some((item) => item.id === next.id);
      return {
        ...current,
        sessions: exists
          ? current.sessions.map((item) => item.id === next.id ? next : item)
          : [next, ...current.sessions].slice(0, RECENT_SESSION_LIMIT),
      };
    });
  }, [queryClient, userId]);

  const changeBrainstormLanguage = async (nextLanguage: "de" | "en") => {
    if (!session || languageUpdating || nextLanguage === brainstormLanguage) return;
    const previousLanguage = brainstormLanguage;
    setBrainstormLanguage(nextLanguage);
    if (session.status !== "recording") return;
    setLanguageUpdating(true);
    try {
      const updated = await api.liveSessionUpdate(session.id, { language: nextLanguage });
      updateSessionCaches(updated);
    } catch {
      setBrainstormLanguage(previousLanguage);
      toast.error(german ? "Die Sprache konnte nicht für alle Clients aktualisiert werden." : "Could not update the language for all clients.");
    } finally {
      setLanguageUpdating(false);
    }
  };

  const acceptProjectChange = async ({
    updated,
    previousProjectId,
    nextProjectId,
    sessionId,
  }: {
    updated: LiveSession;
    previousProjectId: number | null;
    nextProjectId: number | null;
    sessionId: string;
  }) => {
    updateSessionCaches(updated);
    await Promise.all([
      ...(previousProjectId ? [queryClient.invalidateQueries({
        queryKey: ["project-brainstorm-document", userId, previousProjectId],
      })] : []),
      ...(nextProjectId ? [queryClient.invalidateQueries({
        queryKey: ["project-brainstorm-document", userId, nextProjectId],
      })] : []),
    ]);
    setActiveProjectId(nextProjectId);
    const params = new URLSearchParams();
    if (nextProjectId) params.set("project", String(nextProjectId));
    params.set("session", sessionId);
    router.replace(`/brainstorming?${params.toString()}`);
  };

  const changeSessionProject = async (nextProjectId: number | null) => {
    if (!userId || !session || projectUpdating || nextProjectId === session.project_id) return;
    const previousProjectId = session.project_id;
    const sessionId = session.id;
    setProjectUpdating(true);
    setProjectUpdateError(null);
    setProjectMoveDialogError(null);
    try {
      const updated = await api.liveSessionUpdate(sessionId, { project_id: nextProjectId });
      await acceptProjectChange({ updated, previousProjectId, nextProjectId, sessionId });
    } catch (error) {
      const code = errorCode(error);
      if (code === "brainstorm_session_already_synthesized") {
        const invalidationChallenge = error instanceof ApiError && error.status === 409
          ? parseBrainstormFilingChallenge(error.detail)
          : null;
        if (!invalidationChallenge) {
          setProjectUpdateError(german
            ? "Die Sicherheitsbestätigung ist unvollständig. Die Session wurde nicht verschoben. Aktualisiere sie und versuche es erneut."
            : "The safety confirmation is incomplete. The session was not moved. Refresh it and try again.");
          return;
        }
        if (
          previousProjectId === null
          || invalidationChallenge.source_project_id !== previousProjectId
          || invalidationChallenge.session_revision !== session.revision
        ) {
          setProjectUpdateError(german
            ? "Die Sicherheitsbestätigung passt nicht mehr zum sichtbaren Session-Stand. Es wurde nichts verschoben. Aktualisiere die Session und versuche es erneut."
            : "The safety confirmation no longer matches the visible session state. Nothing was moved. Refresh the session and try again.");
          void selected.refetch();
          return;
        }
        const sourceProjectName = session.project?.name
          ?? config.data?.projects.find((project) => project.id === previousProjectId)?.name
          ?? (german ? "bisheriges Projekt" : "current project");
        const targetProjectName = nextProjectId === null
          ? (german ? "Nicht zugeordnet" : "Unfiled")
          : config.data?.projects.find((project) => project.id === nextProjectId)?.name
            ?? (german ? "ausgewähltes Projekt" : "selected project");
        setProjectMoveConfirmation({
          userId,
          sessionId,
          sourceProjectName,
          targetProjectId: nextProjectId,
          targetProjectName,
          challenge: invalidationChallenge,
        });
        return;
      }
      setProjectUpdateError(
        code === "project_brainstorm_in_progress"
          ? (german ? "Die Projektzuordnung ist während einer laufenden Projektsynthese gesperrt." : "Project assignment is locked while a project synthesis is running.")
          : (german ? "Die Projektzuordnung wurde nicht geändert. Aktualisiere die Session und versuche es erneut." : "Project assignment was not changed. Refresh the session and try again."),
      );
    } finally {
      setProjectUpdating(false);
    }
  };

  const confirmProjectMove = async () => {
    const confirmation = projectMoveConfirmation;
    if (!confirmation || projectUpdating) return;
    if (
      !userId
      || confirmation.userId !== userId
      || !session
      || session.id !== confirmation.sessionId
      || session.project_id !== confirmation.challenge.source_project_id
      || session.revision !== confirmation.challenge.session_revision
    ) {
      setProjectMoveConfirmation(null);
      setProjectMoveDialogError(null);
      setProjectUpdateError(german
        ? "Die Session oder ihre Zuordnung hat sich geändert. Es wurde nichts verschoben. Wähle das Zielprojekt erneut aus."
        : "The session or its assignment changed. Nothing was moved. Select the target project again.");
      void selected.refetch();
      return;
    }

    setProjectUpdating(true);
    setProjectMoveDialogError(null);
    try {
      const updated = await api.liveSessionUpdate(confirmation.sessionId, {
        project_id: confirmation.targetProjectId,
        invalidate_project_document: true,
        invalidation_challenge: confirmation.challenge,
      });
      await acceptProjectChange({
        updated,
        previousProjectId: confirmation.challenge.source_project_id,
        nextProjectId: confirmation.targetProjectId,
        sessionId: confirmation.sessionId,
      });
      setProjectMoveConfirmation(null);
    } catch (error) {
      const code = errorCode(error);
      if (code === "brainstorm_filing_challenge_stale") {
        setProjectMoveConfirmation(null);
        setProjectUpdateError(german
          ? "Das Hauptdokument oder die Session hat sich geändert. Es wurde nichts verschoben. Wähle das Zielprojekt erneut aus, um die Auswirkungen neu zu prüfen."
          : "The master document or session changed. Nothing was moved. Select the target project again to review the current impact.");
        void selected.refetch();
        void queryClient.invalidateQueries({
          queryKey: ["project-brainstorm-document", userId, confirmation.challenge.source_project_id],
        });
      } else {
        setProjectMoveDialogError(code === "project_brainstorm_in_progress"
          ? (german
              ? "Die Zuordnung ist während einer laufenden Projektsynthese gesperrt. Es wurde nichts verändert."
              : "Assignment is locked while a project synthesis is running. Nothing was changed.")
          : (german
              ? "Die Verschiebung wurde nicht gespeichert. Session und beide Projektdokumente bleiben unverändert."
              : "The move was not saved. The session and both project documents remain unchanged."));
      }
    } finally {
      setProjectUpdating(false);
    }
  };

  const startNew = async () => {
    if (!userId || starting || !config.data?.enabled) return;
    setStarting(true);
    try {
      const prior = readCreateIntent(userId);
      if (prior && prior.project_id !== projectScopeId) {
        toast.error(german
          ? "Für einen anderen Projektbereich liegt noch ein unbestätigter Start vor. Wechsle dorthin und versuche denselben Start erneut."
          : "An unconfirmed start exists for another project scope. Switch back and retry that same start.");
        return;
      }
      const intent = prior ?? {
        version: 1 as const,
        user_id: userId,
        client_session_id: createClientId("session"),
        title: defaultTitle(german),
        language: brainstormLanguage,
        project_id: projectScopeId,
      };
      if (!prior && !writeCreateIntent(intent)) {
        toast.error(german ? "Der sichere Startbeleg konnte in diesem Browser nicht gespeichert werden. Es wurde nichts gesendet." : "The safe start receipt could not be stored in this browser. Nothing was sent.");
        return;
      }
      const created = await api.liveSessionCreate({
        client_session_id: intent.client_session_id,
        title: intent.title,
        purpose: "brainstorm",
        project_id: intent.project_id,
        language: intent.language,
        consent: null,
      });
      removeCreateIntent(userId);
      updateSessionCaches(created);
      selectSession(created.id);
      window.setTimeout(() => composerRef.current?.focus(), 0);
    } catch (error) {
      if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
        removeCreateIntent(userId);
      }
      const code = errorCode(error);
      toast.error(
        code === "active_live_session_exists"
          ? (german ? "Es läuft bereits eine Aufnahme. Öffne sie zuerst oder beende sie im Companion." : "A capture is already running. Open it first or end it in the companion.")
          : (german ? "Das Browser-Brainstorming konnte nicht gestartet werden. Ein erneuter Versuch ist sicher." : "Could not start the browser brainstorm. It is safe to retry."),
      );
    } finally {
      setStarting(false);
    }
  };

  const submitThought = async (event?: FormEvent) => {
    event?.preventDefault();
    if (
      !userId
      || !session
      || session.status !== "recording"
      || appending
      || events.isLoading
      || recording
    ) return;
    const text = composerText.trim();
    const storedBeforeSend = readComposerDraft(userId, session.id);
    if (!text && !storedBeforeSend?.pending_append) return;
    if (session.duration_ms >= session.max_duration_ms) {
      toast.error(german ? "Die maximale Dauer dieses Brainstormings ist erreicht." : "This brainstorm reached its maximum duration.");
      return;
    }
    setAppending(true);
    try {
      const stored = storedBeforeSend;
      const channelEnds = segments
        .filter((item) => item.channel === inputMode)
        .map((item) => item.endMs);
      const startMs = Math.max(
        session.duration_ms,
        channelEnds.length > 0 ? Math.max(...channelEnds) : 0,
      );
      const hasStoredPending = Boolean(stored?.pending_append);
      const pending = stored?.pending_append ?? buildAppendIntent({
        text,
        channel: inputMode,
        startMs,
        maxSegmentChars: config.data?.max_segment_chars ?? 4_000,
      });
      if (!hasStoredPending && pending.segments.length > composerSegmentLimit) {
        toast.error(german ? "Dieses Brainstorming hat seine Segmentgrenze erreicht. Strukturiere den gespeicherten Stand." : "This brainstorm reached its segment limit. Structure the stored snapshot.");
        return;
      }
      if (pending.segments.at(-1)!.end_ms > session.max_duration_ms) {
        toast.error(german ? "Die maximale Dauer dieses Brainstormings ist erreicht." : "This brainstorm reached its maximum duration.");
        return;
      }
      const pendingWasStored = hasStoredPending || writeComposerDraft({
        version: 1,
        user_id: userId,
        session_id: session.id,
        mode: inputMode,
        text: "",
        microphone_text: "",
        pending_append: pending,
      });
      if (!pendingWasStored) {
        toast.error(german ? "Die sichere Retry-ID konnte in diesem Browser nicht gespeichert werden. Es wurde nichts gesendet." : "The safe retry ID could not be stored in this browser. Nothing was sent.");
        return;
      }
      setPendingAppend(true);
      setAppendFailure(null);
      const result = await api.liveSessionSegments(session.id, pending.segments);
      removeCompleteIntent(userId, session.id);
      removeComposerDraft(userId, session.id);
      setComposerText("");
      setInterimText("");
      setPendingAppend(false);
      setAppendFailure(null);
      let nextSession: LiveSession = {
        ...session,
        revision: result.revision,
        transcript_char_count: result.transcript_char_count,
        segment_count: session.segment_count + result.accepted,
        last_segment_sequence: result.last_event_sequence,
        updated_at: new Date().toISOString(),
      };
      if (session.segment_count === 0 && session.title.startsWith("Brainstorm")) {
        const meaningfulTitle = text.replaceAll(/\s+/g, " ").slice(0, 120).trim();
        if (meaningfulTitle) {
          try {
            nextSession = await api.liveSessionUpdate(session.id, { title: meaningfulTitle });
          } catch {
            // The thought is durable even if the optional display-title update races.
          }
        }
      }
      updateSessionCaches(nextSession);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["brainstorming-events", userId, session.id] }),
        queryClient.invalidateQueries({ queryKey: ["brainstorming-sessions", userId] }),
      ]);
      window.setTimeout(() => composerRef.current?.focus(), 0);
    } catch (error) {
      const rejected = error instanceof ApiError && [409, 413, 422].includes(error.status);
      setAppendFailure(rejected ? "rejected" : "retryable");
      toast.error(
        rejected
          ? (german ? "Der Server hat diesen Entwurf abgelehnt. Du kannst ihn prüfen und lokal verwerfen." : "The server rejected this draft. You can review and discard it locally.")
          : (german ? "Der Gedanke konnte nicht bestätigt werden. Text und sichere Retry-ID bleiben erhalten." : "The thought could not be confirmed. Its text and safe retry ID are preserved."),
      );
    } finally {
      setAppending(false);
    }
  };

  const requestDiscardLocalDraft = () => {
    if (!userId || !session || (!pendingAppend && !composerText.trim())) return;
    setDiscardDraftSessionId(session.id);
  };

  const confirmDiscardLocalDraft = async () => {
    const sessionId = discardDraftSessionId;
    if (!sessionId || revalidatingDraft) return;
    if (
      !userId
      || !session
      || session.id !== sessionId
      || (!pendingAppend && !composerText.trim())
    ) {
      setDiscardDraftSessionId(null);
      toast.error(german
        ? "Der lokale Entwurf oder die geöffnete Session hat sich geändert. Es wurde nichts verworfen."
        : "The local draft or open session changed. Nothing was discarded.");
      return;
    }
    setRevalidatingDraft(true);
    removeComposerDraft(userId, sessionId);
    removeCompleteIntent(userId, sessionId);
    setComposerText("");
    setInterimText("");
    setPendingAppend(false);
    setAppendFailure(null);
    try {
      await Promise.all([selected.refetch(), events.refetch()]);
    } finally {
      setRevalidatingDraft(false);
      setDiscardDraftSessionId(null);
      window.setTimeout(() => composerRef.current?.focus(), 0);
    }
  };

  const copyLocalDraft = async () => {
    if (!composerText.trim()) return;
    try {
      await navigator.clipboard.writeText(composerText);
      toast.success(german ? "Lokaler Entwurf kopiert." : "Local draft copied.");
    } catch {
      toast.error(german ? "Der Entwurf konnte nicht automatisch kopiert werden. Der Text bleibt sichtbar und auswählbar." : "The draft could not be copied automatically. It remains visible and selectable.");
    }
  };

  const structure = async () => {
    if (
      !userId
      || !session
      || !["recording", "completed"].includes(session.status)
      || structuring
      || appending
      || pendingAppend
      || composerText.trim()
      || recording
      || receipts.isError
      || receipts.isFetching
      || revalidatingDraft
      || structureFailureCode === "brainstorm_too_large"
    ) return;
    const cutoff = session.completed_through_sequence ?? session.last_segment_sequence;
    if (cutoff < 1) return;
    setStructuring(true);
    try {
      const stored = readCompleteIntent(userId, session.id);
      const request = stored?.request ?? {
        client_request_id: createClientId("structure"),
        output_language: brainstormLanguage,
        context_through_sequence: cutoff,
        schema_version: 1 as const,
      };
      if (!stored && !writeCompleteIntent({ version: 1, user_id: userId, session_id: session.id, request })) {
        toast.error(german ? "Der sichere Strukturierungsbeleg konnte in diesem Browser nicht gespeichert werden. Es wurde nichts gesendet." : "The safe structuring receipt could not be stored in this browser. Nothing was sent.");
        return;
      }
      const response = session.status === "recording"
        ? await api.liveBrainstormComplete(session.id, request)
        : { session, brainstorm: await api.liveSessionBrainstormCreate(session.id, request) };
      removeCompleteIntent(userId, session.id);
      removeComposerDraft(userId, session.id);
      updateSessionCaches(response.session);
      queryClient.setQueryData(["brainstorming-receipts", userId, session.id], {
        brainstorms: [response.brainstorm, ...(receipts.data?.brainstorms ?? []).filter((item) => item.id !== response.brainstorm.id)],
      });
      await queryClient.invalidateQueries({ queryKey: ["brainstorming-receipts", userId, session.id] });
    } catch (error) {
      const code = errorCode(error);
      const confirmedRejection = error instanceof ApiError && error.status >= 400 && error.status < 500;
      if (confirmedRejection) {
        removeCompleteIntent(userId, session.id);
        setStructureFailureCode(code === "brainstorm_too_large" ? code : null);
        await Promise.allSettled([selected.refetch(), events.refetch(), receipts.refetch()]);
      }
      if (code === "brainstorm_too_large") {
        toast.error(german ? "Dieser Gedankenstrom ist für eine vollständige KI-Struktur zu groß. Die Rohgedanken bleiben erhalten." : "This thought stream is too large for a complete AI structure. Its raw thoughts remain stored.");
      } else if (code === "capacity_unavailable") {
        toast.error(german ? "Für die Strukturierung ist gerade keine Kapazität verfügbar. Versuche es später erneut." : "No structuring capacity is available right now. Try again later.");
      } else if (code === "brainstorm_in_progress") {
        toast.info(german ? "Dieser Stand wird bereits in einem anderen Client strukturiert. Die Ergebnisliste wird aktualisiert." : "This snapshot is already being structured in another client. The result list is refreshing.");
      } else if (confirmedRejection) {
        toast.error(german ? "Der Gedankenstand hat sich geändert oder ist nicht mehr strukturierbar. Die Ansicht wurde aktualisiert." : "The thought stream changed or can no longer be structured. The view was refreshed.");
      } else {
        toast.error(german ? "Die Strukturierung konnte nicht bestätigt werden. Der gleiche eingefrorene Stand kann sicher erneut gesendet werden." : "Structuring could not be confirmed. The same frozen snapshot can be retried safely.");
      }
    } finally {
      setStructuring(false);
    }
  };

  const cancelReceipt = async () => {
    if (!session || !latestReceipt || cancelling) return;
    setCancelling(true);
    try {
      const cancelled = await api.liveSessionBrainstormCancel(session.id, latestReceipt.id);
      queryClient.setQueryData(["brainstorming-receipts", userId, session.id], {
        brainstorms: [cancelled, ...(receipts.data?.brainstorms ?? []).filter((item) => item.id !== cancelled.id)],
      });
    } catch {
      toast.error(german ? "Die Strukturierung konnte nicht abgebrochen werden." : "Could not cancel structuring.");
    } finally {
      setCancelling(false);
    }
  };

  const stopSession = async () => {
    if (
      !session
      || session.status !== "recording"
      || cancelling
      || recording
      || appending
      || pendingAppend
      || Boolean(composerText.trim())
      || revalidatingDraft
    ) return;
    setCancelling(true);
    try {
      abortSpeech();
      const cancelled = await api.liveSessionCancel(session.id);
      updateSessionCaches(cancelled);
      removeComposerDraft(userId!, session.id);
    } catch {
      toast.error(german ? "Das Brainstorming konnte nicht beendet werden." : "Could not stop the brainstorm.");
    } finally {
      setCancelling(false);
    }
  };

  const acceptDeletedSession = (deletedSessionId: string) => {
    if (userId) {
      removeComposerDraft(userId, deletedSessionId);
      removeCompleteIntent(userId, deletedSessionId);
      queryClient.removeQueries({ queryKey: ["brainstorming-session", userId, deletedSessionId] });
      queryClient.removeQueries({ queryKey: ["brainstorming-events", userId, deletedSessionId] });
      queryClient.removeQueries({ queryKey: ["brainstorming-receipts", userId, deletedSessionId] });
      queryClient.removeQueries({ queryKey: ["project-brainstorm-document", userId] });
      queryClient.removeQueries({ queryKey: ["project-brainstorm-syntheses", userId] });
      queryClient.setQueryData<LiveSessionPage>(["brainstorming-sessions", userId], (current) => current ? {
        ...current,
        sessions: current.sessions.filter((item) => item.id !== deletedSessionId),
        total: Math.max(0, current.total - 1),
      } : current);
      void queryClient.invalidateQueries({ queryKey: ["brainstorming-sessions", userId] });
    }
    const currentIndex = visibleBrainstorms.findIndex((item) => item.id === deletedSessionId);
    restoreListRequestedRef.current = true;
    restoreSessionFocusRef.current = visibleBrainstorms[currentIndex + 1]?.id
      ?? visibleBrainstorms[currentIndex - 1]?.id
      ?? null;
    selectSession(null, true);
  };

  const requestDeleteSession = () => {
    if (!userId || !session || deleting || session.status === "recording") return;
    setDeleteDialogError(null);
    setDeleteConfirmation({
      kind: "session",
      userId,
      sessionId: session.id,
      sessionTitle: session.title,
    });
  };

  const confirmDeleteSession = async () => {
    const confirmation = deleteConfirmation;
    if (!confirmation || deleting) return;
    if (
      !userId
      || confirmation.userId !== userId
      || !session
      || session.id !== confirmation.sessionId
      || session.status === "recording"
    ) {
      setDeleteConfirmation(null);
      setDeleteDialogError(null);
      toast.error(german
        ? "Die geöffnete Session hat sich geändert. Es wurde nichts gelöscht."
        : "The open session changed. Nothing was deleted.");
      return;
    }
    if (
      confirmation.kind === "project_cleanup"
      && session.revision !== confirmation.challenge.session_revision
    ) {
      setDeleteConfirmation({
        kind: "session",
        userId: confirmation.userId,
        sessionId: confirmation.sessionId,
        sessionTitle: confirmation.sessionTitle,
      });
      setDeleteDialogError(german
        ? "Die Session hat sich seit der Auswirkungsprüfung geändert. Bestätige erneut, damit die Auswirkungen frisch geprüft werden."
        : "The session changed since its impact was checked. Confirm again to request a fresh impact check.");
      void selected.refetch();
      return;
    }

    setDeleting(true);
    setDeleteDialogError(null);
    try {
      if (confirmation.kind === "project_cleanup") {
        await api.liveSessionDelete(confirmation.sessionId, {
          confirm_project_cleanup: true,
          cleanup_challenge: confirmation.challenge,
        });
      } else {
        await api.liveSessionDelete(confirmation.sessionId);
      }
      setDeleteConfirmation(null);
      acceptDeletedSession(confirmation.sessionId);
    } catch (error) {
      const code = errorCode(error);
      if (
        confirmation.kind === "session"
        && code === "brainstorm_session_delete_requires_cleanup"
      ) {
        const cleanupChallenge = error instanceof ApiError && error.status === 409
          ? parseBrainstormDeleteChallenge(error.detail)
          : null;
        if (!cleanupChallenge || cleanupChallenge.session_revision !== session.revision) {
          setDeleteDialogError(german
            ? "Die Sicherheitsbestätigung für die Projektbereinigung ist unvollständig oder veraltet. Es wurde nichts gelöscht. Aktualisiere die Session und versuche es erneut."
            : "The project-cleanup confirmation is incomplete or stale. Nothing was deleted. Refresh the session and try again.");
          void selected.refetch();
          return;
        }
        setDeleteConfirmation({
          ...confirmation,
          kind: "project_cleanup",
          challenge: cleanupChallenge,
        });
        return;
      }
      if (
        confirmation.kind === "project_cleanup"
        && code === "brainstorm_delete_challenge_stale"
      ) {
        setDeleteConfirmation({
          kind: "session",
          userId: confirmation.userId,
          sessionId: confirmation.sessionId,
          sessionTitle: confirmation.sessionTitle,
        });
        setDeleteDialogError(german
          ? "Die betroffenen Projektdaten haben sich geändert. Es wurde nichts gelöscht. Bestätige erneut, damit die Auswirkungen frisch geprüft werden."
          : "The affected project data changed. Nothing was deleted. Confirm again to request a fresh impact check.");
        void selected.refetch();
        return;
      }
      setDeleteDialogError(code === "project_brainstorm_in_progress"
        ? (german
            ? "Brich zuerst die laufende Projektsynthese ab oder warte auf ihren Abschluss. Die Session bleibt erhalten."
            : "Cancel the active project synthesis first or wait for it to finish. The session remains available.")
        : (german
            ? "Das Brainstorming konnte nicht gelöscht werden. Session und Projektstände bleiben erhalten."
            : "Could not delete the brainstorm. The session and project state remain intact."));
    } finally {
      setDeleting(false);
    }
  };

  const deleteDialogTarget = useMemo<ConfirmDeleteTarget | null>(() => {
    if (!deleteConfirmation) return null;
    const errorSuffix = deleteDialogError
      ? ` ${german ? "Fehler:" : "Error:"} ${deleteDialogError}`
      : "";
    if (deleteConfirmation.kind === "session") {
      return {
        title: german ? "Brainstorming dauerhaft löschen?" : "Permanently delete brainstorm?",
        description: german
          ? `Die Session „${deleteConfirmation.sessionTitle}“, alle bestätigten Rohgedanken und alle daraus erzeugten KI-Strukturen werden dauerhaft gelöscht. Das kann nicht rückgängig gemacht werden. Falls Projektdokumente betroffen sind, zeigen wir deren genaue Auswirkungen vor dem Löschen separat an.${errorSuffix}`
          : `The session “${deleteConfirmation.sessionTitle}”, all confirmed raw thoughts, and every AI structure created from them will be permanently deleted. This cannot be undone. If project documents are affected, their exact impact will be shown separately before deletion.${errorSuffix}`,
        action: german ? "Dauerhaft löschen" : "Delete permanently",
        cancel: german ? "Brainstorming behalten" : "Keep brainstorm",
      };
    }
    const challenge = deleteConfirmation.challenge;
    return {
      title: german ? "Projektdaten bereinigen und löschen?" : "Clean up project data and delete?",
      description: german
        ? `Diese Session wird von ${challenge.affected_document_count} Projekthauptdokument(en) und ${challenge.affected_synthesis_count} Syntheseverlauf/-verläufen verwendet; ${challenge.pending_synthesis_count} Synthese(n) laufen noch. Fortfahren bricht diese laufenden Synthesen ab, löscht die betroffenen Syntheseverläufe und entfernt nur die betroffenen KI-Ebenen samt Quellen aus den Hauptdokumenten. Manuelle Projektnotizen bleiben erhalten. Danach werden Session, Rohgedanken und KI-Strukturen dauerhaft gelöscht.${errorSuffix}`
        : `This session is used by ${challenge.affected_document_count} project master document(s) and ${challenge.affected_synthesis_count} synthesis history item(s); ${challenge.pending_synthesis_count} synthesis job(s) are still running. Continuing cancels those running syntheses, deletes the affected synthesis history, and removes only the affected AI layers and provenance from the master documents. Manual project notes are preserved. The session, raw thoughts, and AI structures are then permanently deleted.${errorSuffix}`,
      action: german ? "Bereinigen und löschen" : "Clean up and delete",
      cancel: german ? "Alles behalten" : "Keep everything",
    };
  }, [deleteConfirmation, deleteDialogError, german]);

  const stopSpeech = useCallback(() => {
    recordingWantedRef.current = false;
    setSpeechNotice(null);
    setSpeechStopping(true);
    if (speechStopTimerRef.current !== null) window.clearTimeout(speechStopTimerRef.current);
    speechStopTimerRef.current = window.setTimeout(() => {
      preserveInterimText();
      abortSpeech();
    }, 1_500);
    recognitionRef.current?.stop();
  }, [abortSpeech, preserveInterimText]);

  const startSpeech = () => {
    const Recognition = speechRecognitionConstructor();
    if (!Recognition) {
      setSpeechNotice({
        tone: "warning",
        message: german
          ? "Dieser Browser unterstützt keine Spracheingabe. Nutze Tippen oder öffne die Seite in Chrome oder Edge."
          : "This browser does not support speech input. Use Type or open this page in Chrome or Edge.",
      });
      return;
    }
    const previousRecognition = recognitionRef.current;
    recognitionRef.current = null;
    if (previousRecognition) {
      previousRecognition.onresult = null;
      previousRecognition.onerror = null;
      previousRecognition.onend = null;
      previousRecognition.onspeechstart = null;
      previousRecognition.onspeechend = null;
      previousRecognition.abort();
    }
    const recognition = new Recognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = brainstormLanguage === "de" ? "de-DE" : "en-US";
    const speechHints = normalizeSpeechHints(speechHintText);
    const speechProjectId = session?.project_id ?? projectScopeId;
    if (userId) writeSpeechHints(userId, speechProjectId, speechHints);
    const hintsApplied = applySpeechVocabulary(
      recognition,
      buildSpeechVocabulary(
        brainstormLanguage,
        session?.project?.name ?? projectScope?.name ?? null,
        speechHints,
      ),
    );
    setSpeechHintSupport(hintsApplied);
    recognition.onspeechstart = beginSpeechActivity;
    recognition.onspeechend = endSpeechActivity;
    recognition.onresult = (event) => {
      let finalText = "";
      let interim = "";
      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        const result = event.results[index];
        if (result.isFinal) finalText += `${result[0].transcript.trim()} `;
        else interim += result[0].transcript;
      }
      const nextInterim = interim.trim();
      interimTextRef.current = nextInterim;
      setInterimText(nextInterim);
      if (finalText.trim() || nextInterim) markRecognitionActivity();
      if (finalText.trim()) {
        setComposerText((current) => appendRecognisedText(current, finalText, composerMaxChars));
      }
    };
    recognition.onerror = (event) => {
      const userRequestedStop = speechStopping || !recordingWantedRef.current;
      recordingWantedRef.current = false;
      preserveInterimText();
      resetSpeechActivity();
      setRecording(false);
      setSpeechStopping(false);
      setSpeechNotice(userRequestedStop ? null : speechNoticeForError(event.error, german));
    };
    recognition.onend = () => {
      if (speechStopTimerRef.current !== null) {
        window.clearTimeout(speechStopTimerRef.current);
        speechStopTimerRef.current = null;
      }
      preserveInterimText();
      resetSpeechActivity();
      setSpeechStopping(false);
      if (!recordingWantedRef.current) {
        setRecording(false);
        if (recognitionRef.current === recognition) recognitionRef.current = null;
        return;
      }
      try {
        recognition.start();
      } catch {
        recordingWantedRef.current = false;
        setRecording(false);
        setSpeechNotice({
          tone: "warning",
          message: german
            ? "Die Spracherkennung konnte nicht weiterlaufen. Dein Entwurf ist weiterhin da; tippe zum Fortsetzen erneut auf das Mikrofon."
            : "Speech recognition could not continue. Your draft is still here; tap the microphone again to continue.",
        });
      }
    };
    recognitionRef.current = recognition;
    recordingWantedRef.current = true;
    setInputMode("microphone");
    setSpeechNotice(null);
    setSpeechStopping(false);
    resetSpeechActivity();
    setRecording(true);
    try {
      recognition.start();
    } catch {
      recordingWantedRef.current = false;
      setRecording(false);
      setSpeechNotice({
        tone: "error",
        message: german
          ? "Die Spracheingabe konnte nicht gestartet werden. Prüfe den Mikrofonzugriff für diese Website und versuche es erneut."
          : "Speech input could not start. Check microphone access for this site, then try again.",
      });
    }
  };

  const focusEvidence = useCallback((segmentId: string): boolean => {
    const target = document.getElementById(`brainstorm-segment-${segmentId}`);
    if (!target) return false;
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    target.focus({ preventScroll: true });
    return true;
  }, []);

  const requestEvidenceLocation = useCallback((segmentId: string) => {
    if (focusEvidence(segmentId)) return;
    if (session) selectSession(session.id, true, segmentId);
  }, [focusEvidence, selectSession, session]);

  useEffect(() => {
    if (!requestedEvidence || !session || events.isLoading || events.isError) return;
    if (!requestedEvidenceInTail && !locatedEvidenceSegment) return;
    const identity = `${session.id}:${requestedEvidence}`;
    if (locatedEvidenceRef.current === identity) return;
    const rawDetails = document.getElementById("brainstorm-raw-thoughts");
    if (rawDetails instanceof HTMLDetailsElement) rawDetails.open = true;
    const frame = window.requestAnimationFrame(() => {
      if (focusEvidence(requestedEvidence)) locatedEvidenceRef.current = identity;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [events.isError, events.isLoading, focusEvidence, locatedEvidenceSegment, requestedEvidence, requestedEvidenceInTail, session]);

  const listPane = (
    <section
      aria-labelledby="brainstorming-history-heading"
      className={cn(
        "min-h-0 flex-col border-r border-border bg-background group-data-[workspace-layout=split]/workspace:!flex",
        hasMobileDetail ? "hidden" : "flex",
      )}
    >
      <header className="shrink-0 border-b border-border px-4 pb-4 pt-5 sm:px-6">
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="flex items-center gap-2 font-mono text-[0.625rem] uppercase tracking-[0.2em] text-moss">
              <BrainCircuit className="size-3.5" /> Brainstorming
            </p>
            <h1 id="brainstorming-history-heading" className="mt-1 font-display text-3xl text-foreground">
              {german ? "Gedanken entfalten" : "Develop your thinking"}
            </h1>
            <p className="mt-1 max-w-md text-xs leading-relaxed text-muted-foreground">
              {german ? "Tippen, sprechen oder im Companion frei denken – anschließend sauber strukturiert." : "Type, speak or think freely in the companion – then turn it into a clear structure."}
            </p>
          </div>
          <Button ref={newButtonRef} type="button" size="sm" className="rounded-full" disabled={starting || !config.data?.enabled} onClick={() => void startNew()}>
            {starting ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />}
            {german ? "Neu" : "New"}
          </Button>
        </div>
        <div className="relative mt-4">
          <label htmlFor="brainstorming-history-search" className="sr-only">{german ? "Brainstormings durchsuchen" : "Search brainstorms"}</label>
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input ref={searchInputRef} id="brainstorming-history-search" value={search} onChange={(event) => setSearch(event.target.value)} className="h-10 rounded-full pl-9" placeholder={german ? "Brainstormings durchsuchen…" : "Search brainstorms…"} />
        </div>
        <div className="mt-3 flex items-center gap-2">
          <label htmlFor="brainstorming-project-filter" className="sr-only">{german ? "Brainstorming-Projekt filtern" : "Filter brainstorm project"}</label>
          <div className="relative min-w-0 flex-1">
            <select
              id="brainstorming-project-filter"
              value={projectScopeId ?? "all"}
              onChange={(event) => changeProjectScope(event.target.value === "all" ? null : Number(event.target.value))}
              disabled={projectUpdating || starting || config.isLoading || config.isError}
              className="peer h-9 w-full appearance-none rounded-full border border-border bg-background pl-3 pr-9 text-xs text-foreground disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moss forced-colors:appearance-auto"
              aria-label={german ? "Brainstorming-Projekt filtern" : "Filter brainstorm project"}
            >
              <option value="all">{german ? "Alle Projekte und unzugeordnet" : "All projects and unfiled"}</option>
              {(config.data?.projects ?? []).map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
            </select>
            <ChevronDown aria-hidden="true" className="pointer-events-none absolute right-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground peer-disabled:opacity-50 forced-colors:hidden" />
          </div>
          {projectScopeId && projectScope && (
            <Button type="button" variant="outline" size="sm" className="shrink-0 rounded-full" onClick={openProjectDocument} aria-current={requestedProjectView ? "page" : undefined}>
              <FolderKanban className="size-3.5" />
              <span className="hidden sm:inline">{german ? "Hauptdokument" : "Master document"}</span>
            </Button>
          )}
        </div>
        {config.isError && (
          <div role="alert" className="mt-3 flex items-center justify-between gap-3 rounded-xl border border-destructive/20 bg-destructive/5 px-3 py-2 text-[0.6875rem] text-muted-foreground">
            <span>{german ? "Brainstorming-Verfügbarkeit konnte nicht geprüft werden." : "Could not check Brainstorming availability."}</span>
            <button type="button" className="shrink-0 font-medium text-foreground underline-offset-2 hover:underline" onClick={() => void config.refetch()}>{german ? "Erneut" : "Retry"}</button>
          </div>
        )}
        {config.data && !config.data.enabled && (
          <div role="status" className="mt-3 rounded-xl border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-[0.6875rem] leading-relaxed text-muted-foreground">
            <p>{german ? "Neue Brainstormings sind für deinen aktuellen Zugang nicht freigeschaltet. Vorhandene Gedanken und Ergebnisse bleiben lesbar." : "Creating brainstorms is not enabled for your current access. Existing thoughts and results remain available."}</p>
          </div>
        )}
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto p-3 sm:p-4">
        {sessions.isLoading ? (
          <div role="status" className="flex justify-center py-16 text-muted-foreground"><Loader2 className="size-5 animate-spin" /><span className="sr-only">{german ? "Wird geladen" : "Loading"}</span></div>
        ) : sessions.isError ? (
          <div role="alert" className="rounded-2xl border border-destructive/20 bg-destructive/5 p-5 text-sm text-muted-foreground">
            <p>{german ? "Brainstormings konnten nicht geladen werden." : "Could not load brainstorms."}</p>
            <Button type="button" variant="outline" size="sm" className="mt-3 rounded-full" onClick={() => void sessions.refetch()}>{german ? "Erneut versuchen" : "Retry"}</Button>
          </div>
        ) : visibleBrainstorms.length === 0 ? (
          <EditorialEmptyState
            align="start"
            className="p-7"
            eyebrow={search ? (german ? "Suche" : "Search") : (german ? "Neuer Gedankenstrom" : "New thought stream")}
            title={search ? (german ? "Kein Treffer" : "No match") : (german ? "Dein erster Gedankenstrom" : "Your first thought stream")}
            description={search
              ? (german ? "Versuche einen anderen Suchbegriff oder lösche den Filter." : "Try another search term or clear the filter.")
              : (german ? "Starte hier mit Text oder Mikrofon. Companion-Brainstormings erscheinen automatisch in derselben Liste." : "Start here with text or microphone. Companion brainstorms appear automatically in the same list.")}
            titleClassName="text-xl"
          >
            {!search && (
              <Button type="button" className="rounded-full" disabled={starting || !config.data?.enabled} onClick={() => void startNew()}>
                {starting ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />}
                {german ? "Brainstorming starten" : "Start brainstorming"}
              </Button>
            )}
          </EditorialEmptyState>
        ) : (
          <ul className="space-y-2">
            {visibleBrainstorms.map((item) => (
              <li key={item.id}>
                <button
                  ref={(node) => {
                    if (node) sessionRowRefs.current.set(item.id, node);
                    else sessionRowRefs.current.delete(item.id);
                  }}
                  type="button"
                  onClick={() => selectSession(item.id)}
                  aria-current={item.id === selectedSessionId ? "page" : undefined}
                  className={cn(
                    "group w-full rounded-2xl border p-4 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moss",
                    item.id === selectedSessionId ? "border-moss/35 bg-moss-surface/8" : "border-border bg-card/55 hover:border-moss/25 hover:bg-card",
                  )}
                >
                  <div className="flex items-start gap-3">
                    <span className={cn("mt-1.5 size-2 shrink-0 rounded-full", item.status === "recording" ? "animate-pulse bg-moss" : item.status === "completed" ? "bg-moss/55" : "bg-muted-foreground/45")} />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium text-foreground">{item.title}</span>
                      <span className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[0.6875rem] text-muted-foreground">
                        <span>{statusCopy(item, german)}</span>
                        <span aria-hidden="true">·</span>
                        <span>{item.segment_count} {item.segment_count === 1 ? (german ? "Gedanke" : "thought") : (german ? "Gedanken" : "thoughts")}</span>
                        <span aria-hidden="true">·</span>
                        <span>{formatDate(item.updated_at, german)}</span>
                      </span>
                    </span>
                    <ChevronRight className="mt-0.5 size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
                  </div>
                </button>
              </li>
            ))}
          </ul>
        )}
        {brainstorms.length >= RECENT_SESSION_LIMIT && (
          <p className="px-2 py-4 text-center text-[0.6875rem] text-muted-foreground">{german ? "Angezeigt werden die letzten 100 Brainstormings." : "Showing the latest 100 brainstorms."}</p>
        )}
      </div>
    </section>
  );

  const detailPane = (
    <section
      aria-label={german ? "Brainstorming-Arbeitsbereich" : "Brainstorm workspace"}
      className={cn(
        "min-h-0 flex-col bg-background group-data-[workspace-layout=split]/workspace:!flex",
        hasMobileDetail ? "flex" : "hidden",
      )}
    >
      {!selectedSessionId && projectScope && userId ? (
        <>
          <header className="flex shrink-0 items-center gap-3 border-b border-border px-4 py-3 group-data-[workspace-layout=split]/workspace:hidden">
            <Button type="button" variant="ghost" size="icon" className="rounded-full" onClick={() => router.push(`/brainstorming?project=${projectScope.id}`)} aria-label={german ? "Zurück zur Brainstorming-Liste" : "Back to brainstorm list"}><ArrowLeft className="size-4" /></Button>
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-foreground">{projectScope.name}</p>
              <p className="text-[0.6875rem] text-muted-foreground">{german ? "Projektdokument" : "Project document"}</p>
            </div>
          </header>
          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-5 sm:px-6 lg:px-8">
            <ProjectBrainstormWorkspace
              key={`${userId}:${projectScope.id}`}
              userId={userId}
              german={german}
              project={projectScope}
              sessions={brainstorms}
              onOpenSession={(sessionId, segmentId) => selectSession(sessionId, false, segmentId)}
            />
          </div>
        </>
      ) : !selectedSessionId ? (
        <div className="grid min-h-0 flex-1 place-items-center p-8">
          <EditorialEmptyState
            framed={false}
            className="max-w-lg"
            eyebrow={german ? "Privater Arbeitsbereich" : "Private workspace"}
            title={german ? "Raum für unfertige Gedanken" : "Space for unfinished thoughts"}
            description={german ? "Sammle mehrere Gedanken wie in einem Chat. Wenn du fertig bist, ordnet die KI ausschließlich diesen eingefrorenen Gedankenstrom in Themen, Ideen, Fragen, Entscheidungen und nächste Schritte." : "Collect several thoughts like a chat. When you are ready, AI organises only that frozen thought stream into themes, ideas, questions, decisions and next steps."}
            note={german ? "Bei Browser-Spracheingabe verarbeitet dein Browser das Mikrofon. SixSentences erhält und speichert nur den final erkannten Text, kein Roh-Audio." : "For browser speech input, your browser handles the microphone. SixSentences receives and stores only the final recognised text, never raw audio."}
            titleClassName="font-display text-3xl"
          >
            <Button type="button" className="rounded-full" disabled={starting || !config.data?.enabled} onClick={() => void startNew()}>
              {starting ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />}
              {german ? "Im Browser starten" : "Start in browser"}
            </Button>
            {config.data?.desktop.download_url && (
              <Button asChild variant="outline" className="rounded-full">
                <a href={config.data.desktop.download_url}>{german ? "Companion Preview" : "Companion preview"}</a>
              </Button>
            )}
          </EditorialEmptyState>
        </div>
      ) : selected.isLoading ? (
        <div role="status" className="grid min-h-0 flex-1 place-items-center text-muted-foreground"><Loader2 className="size-5 animate-spin" /><span className="sr-only">{german ? "Wird geladen" : "Loading"}</span></div>
      ) : selected.isError || !session || session.purpose !== "brainstorm" ? (
        <div className="grid min-h-0 flex-1 place-items-center p-6">
          <div role="alert" className="max-w-md rounded-2xl border border-border bg-card p-6 text-center">
            <p className="text-sm text-foreground">{selectedUnavailable || selectedWrongPurpose
              ? (german ? "Dieses Brainstorming ist nicht verfügbar. Es wurde entfernt oder du hast keinen Zugriff darauf." : "This brainstorm is unavailable. It was removed or you do not have access to it.")
              : (german ? "Das Brainstorming konnte gerade nicht geladen werden. Bitte versuche es erneut." : "This brainstorm could not be loaded right now. Please try again.")}</p>
            {!selectedWrongPurpose && (!selected.error || retryTransientApiQuery(0, selected.error)) && (
              <Button type="button" variant="outline" size="sm" className="mr-2 mt-4 rounded-full" disabled={selected.isFetching} onClick={() => void selected.refetch()}>
                {selected.isFetching && <Loader2 className="size-4 animate-spin" />}
                {german ? "Erneut versuchen" : "Try again"}
              </Button>
            )}
            <Button type="button" variant="outline" size="sm" className="mt-4 rounded-full" onClick={() => { restoreListRequestedRef.current = true; restoreSessionFocusRef.current = selectedSessionId; selectSession(null); }}><ArrowLeft className="size-4" />{german ? "Zur Übersicht" : "Back to overview"}</Button>
          </div>
        </div>
      ) : (
        <>
          <header className="shrink-0 border-b border-border px-4 py-3 sm:px-6">
            <div className="flex items-center gap-3">
              <Button type="button" variant="ghost" size="icon" className="rounded-full group-data-[workspace-layout=split]/workspace:hidden" onClick={() => { restoreListRequestedRef.current = true; restoreSessionFocusRef.current = session.id; selectSession(null); }} aria-label={german ? "Zurück zur Liste" : "Back to list"}><ArrowLeft className="size-4" /></Button>
              <div className="min-w-0 flex-1">
                <h2 ref={detailHeadingRef} tabIndex={-1} className="truncate font-serif text-xl text-foreground outline-none">{session.title}</h2>
                <p className="mt-0.5 text-[0.6875rem] text-muted-foreground">{statusCopy(session, german)} · {session.segment_count} {german ? "gespeicherte Gedanken" : "stored thoughts"}{session.project ? ` · ${session.project.name}` : ""}</p>
              </div>
              <div className="relative hidden shrink-0 md:block">
                <label htmlFor="brainstorming-session-project" className="sr-only">{german ? "Session einem Projekt zuordnen" : "Assign session to a project"}</label>
                <select
                  id="brainstorming-session-project"
                  value={session.project_id ?? "none"}
                  onChange={(event) => void changeSessionProject(event.target.value === "none" ? null : Number(event.target.value))}
                  disabled={config.isLoading || config.isError || projectUpdating || structuring || latestReceipt?.status === "pending" || session.status === "cancelled" || session.status === "failed"}
                  className="peer h-8 max-w-44 appearance-none rounded-full border border-border bg-background pl-3 pr-8 text-xs text-foreground disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moss forced-colors:appearance-auto"
                  aria-label={german ? "Session einem Projekt zuordnen" : "Assign session to a project"}
                >
                  <option value="none">{german ? "Kein Projekt" : "No project"}</option>
                  {(config.data?.projects ?? []).map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
                </select>
                <ChevronDown aria-hidden="true" className="pointer-events-none absolute right-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground peer-disabled:opacity-50 forced-colors:hidden" />
              </div>
              {projectUpdating && <Loader2 aria-label={german ? "Projektzuordnung wird gespeichert" : "Saving project assignment"} className="size-3.5 animate-spin text-muted-foreground" />}
              <div className="relative shrink-0">
                <label htmlFor="brainstorming-language" className="sr-only">{german ? "Sprache für Erkennung und KI-Ausgabe" : "Language for recognition and AI output"}</label>
                <select
                  id="brainstorming-language"
                  value={brainstormLanguage}
                  onChange={(event) => void changeBrainstormLanguage(event.target.value === "de" ? "de" : "en")}
                  disabled={recording || structuring || languageUpdating || latestReceipt?.status === "pending"}
                  className="peer h-8 appearance-none rounded-full border border-border bg-background pl-3 pr-8 text-xs text-foreground disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moss forced-colors:appearance-auto"
                  aria-label={german ? "Sprache für Erkennung und KI-Ausgabe" : "Language for recognition and AI output"}
                >
                  <option value="de">DE</option>
                  <option value="en">EN</option>
                </select>
                <ChevronDown aria-hidden="true" className="pointer-events-none absolute right-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground peer-disabled:opacity-50 forced-colors:hidden" />
              </div>
              {languageUpdating && <Loader2 aria-hidden="true" className="size-3.5 animate-spin text-muted-foreground" />}
              {session.status === "recording" && <Button type="button" variant="outline" size="sm" className="rounded-full" disabled={cancelling || recording || appending || pendingAppend || Boolean(composerText.trim()) || revalidatingDraft} onClick={() => void stopSession()} aria-label={german ? "Aufnahme beenden" : "Stop capture"}>{cancelling ? <Loader2 className="size-3.5 animate-spin" /> : <CircleStop className="size-3.5" />}<span className="hidden sm:inline">{german ? "Aufnahme beenden" : "Stop capture"}</span></Button>}
              {session.status !== "recording" && <Button type="button" variant="ghost" size="icon" className="rounded-full text-muted-foreground hover:text-destructive" disabled={deleting} onClick={requestDeleteSession} aria-label={german ? "Brainstorming löschen" : "Delete brainstorm"}>{deleting ? <Loader2 className="size-4 animate-spin" /> : <Trash2 className="size-4" />}</Button>}
            </div>
            <div className="mt-2 md:hidden">
              <div className="relative">
                <label htmlFor="brainstorming-session-project-mobile" className="sr-only">{german ? "Session einem Projekt zuordnen" : "Assign session to a project"}</label>
                <select
                  id="brainstorming-session-project-mobile"
                  value={session.project_id ?? "none"}
                  onChange={(event) => void changeSessionProject(event.target.value === "none" ? null : Number(event.target.value))}
                  disabled={config.isLoading || config.isError || projectUpdating || structuring || latestReceipt?.status === "pending" || session.status === "cancelled" || session.status === "failed"}
                  className="peer h-8 w-full appearance-none rounded-full border border-border bg-background pl-3 pr-9 text-xs text-foreground disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moss forced-colors:appearance-auto"
                  aria-label={german ? "Session einem Projekt zuordnen" : "Assign session to a project"}
                >
                  <option value="none">{german ? "Kein Projekt" : "No project"}</option>
                  {(config.data?.projects ?? []).map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
                </select>
                <ChevronDown aria-hidden="true" className="pointer-events-none absolute right-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground peer-disabled:opacity-50 forced-colors:hidden" />
              </div>
            </div>
            {projectUpdateError && <div role="alert" className="mt-2 flex items-center justify-between gap-3 rounded-lg border border-destructive/20 bg-destructive/5 px-3 py-2 text-[0.6875rem] text-muted-foreground"><span>{projectUpdateError}</span><button type="button" className="shrink-0 font-medium text-foreground underline underline-offset-2" onClick={() => { setProjectUpdateError(null); void selected.refetch(); }}>{german ? "Aktualisieren" : "Refresh"}</button></div>}
          </header>

          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-5 sm:px-6 lg:px-8">
            {session.status !== "recording" && (pendingAppend || Boolean(composerText.trim())) && (
              <section aria-labelledby="brainstorm-local-draft" className="mx-auto mb-5 max-w-5xl rounded-2xl border border-amber-500/25 bg-amber-500/5 p-4">
                <h3 id="brainstorm-local-draft" className="text-sm font-medium text-foreground">{german ? "Lokaler Entwurf nicht im bestätigten Stand" : "Local draft outside the confirmed snapshot"}</h3>
                <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{german ? "Die Session wurde in einem anderen Client beendet. Dieser lokale Text war dabei nicht sicher als Bestandteil des eingefrorenen Gedankenstroms bestätigt." : "The session was closed in another client. This local text was not confirmed as part of the frozen thought stream."}</p>
                {composerText.trim() && <p className="mt-3 max-h-32 overflow-y-auto whitespace-pre-wrap rounded-xl bg-background/75 p-3 text-xs leading-relaxed text-foreground">{composerText}</p>}
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button type="button" variant="outline" size="sm" className="rounded-full" disabled={!composerText.trim()} onClick={() => void copyLocalDraft()}>{german ? "Text kopieren" : "Copy text"}</Button>
                  <Button type="button" variant="ghost" size="sm" className="rounded-full text-destructive" disabled={revalidatingDraft} onClick={requestDiscardLocalDraft}>{revalidatingDraft ? <Loader2 className="size-3.5 animate-spin" /> : null}{german ? "Lokalen Entwurf verwerfen" : "Discard local draft"}</Button>
                </div>
              </section>
            )}
            {latestReceipt || session.status === "completed" ? (
              <div className="mx-auto max-w-5xl">
                {receipts.isLoading ? (
                  <div role="status" className="flex justify-center py-16 text-muted-foreground"><Loader2 className="size-5 animate-spin" /><span className="sr-only">{german ? "Struktur wird geladen" : "Loading structure"}</span></div>
                ) : receipts.isError ? (
                  <div role="alert" className="rounded-2xl border border-destructive/20 bg-destructive/5 p-5 text-sm text-muted-foreground"><p>{german ? "Die KI-Struktur konnte nicht geladen werden." : "Could not load the AI structure."}</p><Button type="button" variant="outline" size="sm" className="mt-3 rounded-full" onClick={() => void receipts.refetch()}>{german ? "Erneut versuchen" : "Retry"}</Button></div>
                ) : (
                  <BrainstormResult receipt={latestReceipt} german={german} retrying={structuring || receipts.isFetching || revalidatingDraft} cancelling={cancelling} canRetry={session.status === "recording" || session.status === "completed"} onRetry={() => void structure()} onCancel={() => void cancelReceipt()} onLocateEvidence={requestEvidenceLocation} />
                )}
                <details id="brainstorm-raw-thoughts" className="mt-6 rounded-2xl border border-border bg-card/45" open={!latestReceipt?.result || Boolean(requestedEvidence)}>
                  <summary className="cursor-pointer px-5 py-4 text-sm font-medium text-foreground">{german ? "Bestätigtes Rohtranskript anzeigen" : "Show confirmed raw transcript"}</summary>
                  <ThoughtTimeline session={session} segments={segments} locatedSegment={locatedEvidenceSegment} locatedLoading={!requestedEvidenceInTail && exactEvidence.isLoading} locatedFailed={exactEvidenceFailed} locatedNotFound={exactEvidenceNotFound} german={german} loading={events.isLoading} failed={events.isError} onRetry={() => void events.refetch()} onRetryLocated={() => void exactEvidence.refetch()} />
                </details>
              </div>
            ) : (
              <div className="mx-auto max-w-3xl">
                {session.status !== "recording" && (
                  <div role="status" className="mb-4 rounded-2xl border border-border bg-secondary/35 p-4 text-xs leading-relaxed text-muted-foreground">{session.status === "cancelled" ? (german ? "Diese Aufnahme wurde beendet. Die bereits gespeicherten Rohgedanken bleiben sichtbar, wurden aber nicht strukturiert." : "This capture was stopped. Its stored raw thoughts remain visible but were not structured.") : (german ? "Diese Session konnte nicht abgeschlossen werden. Die gespeicherten Rohgedanken bleiben sichtbar." : "This session could not complete. Its stored raw thoughts remain visible.")}</div>
                )}
                <div className="mb-5 flex items-center justify-between gap-3">
                  <div>
                    <h3 className="font-serif text-2xl text-foreground">{german ? "Bestätigte Rohgedanken" : "Confirmed raw thoughts"}</h3>
                    <p className="mt-1 text-xs text-muted-foreground">{german ? "Unfertig ist ausdrücklich erlaubt. Die KI-Struktur ist eine getrennte Sicht und überschreibt diesen Wortlaut nie." : "Unfinished is welcome. The AI structure is a separate view and never overwrites this wording."}</p>
                  </div>
                  {session.status === "recording" && <Button type="button" className="rounded-full" disabled={structuring || appending || pendingAppend || Boolean(composerText.trim()) || session.last_segment_sequence < 1 || recording || receipts.isError || receipts.isFetching || revalidatingDraft} onClick={() => void structure()}>{structuring ? <Loader2 className="size-4 animate-spin" /> : <ListTree className="size-4" />}{german ? "Strukturieren" : "Structure"}</Button>}
                </div>
                {receipts.isError && <div role="alert" className="mb-4 flex items-center justify-between gap-3 rounded-xl border border-destructive/20 bg-destructive/5 px-3 py-2 text-xs text-muted-foreground"><span>{german ? "Vorhandene KI-Strukturen konnten nicht geprüft werden." : "Could not check existing AI structures."}</span><button type="button" className="font-medium text-foreground underline underline-offset-2" onClick={() => void receipts.refetch()}>{german ? "Erneut" : "Retry"}</button></div>}
                <ThoughtTimeline session={session} segments={segments} locatedSegment={locatedEvidenceSegment} locatedLoading={!requestedEvidenceInTail && exactEvidence.isLoading} locatedFailed={exactEvidenceFailed} locatedNotFound={exactEvidenceNotFound} german={german} loading={events.isLoading} failed={events.isError} onRetry={() => void events.refetch()} onRetryLocated={() => void exactEvidence.refetch()} />
              </div>
            )}
          </div>

          {session.status === "recording" && (
            <form method="post" onSubmit={(event) => void submitThought(event)} className="shrink-0 border-t border-border bg-background/95 px-4 py-3 backdrop-blur sm:px-6 lg:px-8">
              <div className="mx-auto max-w-3xl">
                <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                  <div
                    role="group"
                    aria-label={german ? "Eingabeart" : "Input method"}
                    aria-describedby={composerText.trim() ? "brainstorming-input-mode-note" : undefined}
                    className="flex rounded-full border border-border bg-secondary/25 p-0.5"
                  >
                    <Button
                      type="button"
                      aria-pressed={inputMode === "typed"}
                      variant={inputMode === "typed" ? "secondary" : "ghost"}
                      size="sm"
                      className="h-8 rounded-full"
                      disabled={recording || appending || pendingAppend || (inputMode !== "typed" && Boolean(composerText.trim()))}
                      onClick={() => {
                        setInputMode("typed");
                        setSpeechNotice(null);
                        window.requestAnimationFrame(() => composerRef.current?.focus());
                      }}
                    >
                      <FileText className="size-3.5" />
                      {german ? "Tippen" : "Type"}
                    </Button>
                    <Button
                      type="button"
                      aria-pressed={inputMode === "microphone"}
                      variant={inputMode === "microphone" ? "secondary" : "ghost"}
                      size="sm"
                      className="h-8 rounded-full"
                      disabled={appending || pendingAppend || composerMaxChars < 1 || (inputMode !== "microphone" && Boolean(composerText.trim()))}
                      onClick={() => {
                        setInputMode("microphone");
                        setSpeechNotice(null);
                      }}
                    >
                      <Mic className="size-3.5" />
                      {german ? "Sprechen" : "Speak"}
                    </Button>
                  </div>
                  {composerText.trim() && (
                    <p id="brainstorming-input-mode-note" className="text-[0.625rem] text-muted-foreground">
                      {german ? "Sende den aktuellen Entwurf, bevor du die Eingabeart wechselst." : "Send the current draft before changing input method."}
                    </p>
                  )}
                </div>

                {inputMode === "typed" ? (
                  <div id="brainstorming-typed-composer" className="rounded-2xl border border-border bg-card p-2 shadow-sm focus-within:border-moss/35">
                    <Textarea
                      ref={composerRef}
                      value={composerText}
                      onChange={(event) => setComposerText(event.target.value.slice(0, composerMaxChars))}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                          event.preventDefault();
                          void submitThought();
                        }
                      }}
                      rows={2}
                      maxLength={composerMaxChars}
                      disabled={appending || pendingAppend || composerMaxChars < 1}
                      className="max-h-40 min-h-14 resize-none border-0 bg-transparent px-2 py-2 text-sm shadow-none focus-visible:ring-0"
                      placeholder={german ? "Gedanken eingeben…" : "Type a thought…"}
                      aria-label={german ? "Neuer Gedanke" : "New thought"}
                    />
                    <div className="flex items-center justify-end border-t border-border/70 px-1 pt-2">
                      <Button type="submit" size="icon" className="size-8 rounded-full" disabled={(!composerText.trim() && !pendingAppend) || appending || events.isLoading} aria-label={pendingAppend ? (german ? "Gedanke erneut bestätigen" : "Retry thought") : (german ? "Gedanke senden" : "Send thought")}>{appending ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}</Button>
                    </div>
                  </div>
                ) : (
                  <section id="brainstorming-speech-composer" aria-labelledby="brainstorming-speech-state" className="overflow-hidden rounded-[1.75rem] border border-moss/25 bg-card shadow-sm focus-within:border-moss/45">
                    <div className="flex min-h-36 flex-col items-center justify-center px-3 py-4 text-center sm:min-h-40 sm:px-6">
                      <div className="flex items-center justify-center gap-1 sm:gap-3">
                        <SpeechWave active={recording && speechActive && !speechStopping} pulse={speechPulse} side="left" />
                        <Button
                          type="button"
                          size="icon"
                          variant={recording ? "default" : "outline"}
                          className={cn(
                            "relative z-10 size-20 shrink-0 rounded-full border-moss/35 transition-[box-shadow,transform] duration-200 motion-reduce:transition-none",
                            recording && "bg-moss text-primary-foreground hover:bg-moss/90",
                            recording && speechActive && !speechStopping && "ring-8 ring-moss/10",
                          )}
                          disabled={appending || pendingAppend || composerMaxChars < 1 || speechStopping}
                          aria-label={recording ? (german ? "Spracheingabe stoppen" : "Stop speech input") : (german ? "Spracheingabe starten" : "Start speech input")}
                          aria-pressed={recording}
                          aria-describedby="brainstorming-speech-state"
                          onClick={recording ? stopSpeech : startSpeech}
                        >
                          {speechStopping
                            ? <Loader2 className="size-7 animate-spin motion-reduce:animate-none" />
                            : recording
                              ? <CircleStop className="size-7" />
                              : <Mic className="size-7" />}
                        </Button>
                        <SpeechWave active={recording && speechActive && !speechStopping} pulse={speechPulse + 3} side="right" />
                      </div>
                      <p id="brainstorming-speech-state" role="status" aria-live="polite" className="mt-3 text-sm font-medium text-foreground">
                        {speechStopping
                          ? (german ? "Erkennung wird abgeschlossen…" : "Finishing recognition…")
                          : recording && speechActive
                            ? (german ? "Ich höre dich" : "Capturing your words")
                            : recording
                              ? (german ? "Ich höre zu — sprich einfach los" : "Listening — start speaking")
                              : composerText.trim()
                                ? (german ? "Dein Sprachentwurf ist bereit" : "Your speech draft is ready")
                                : (german ? "Tippe auf das Mikrofon und sprich frei" : "Tap the microphone and speak freely")}
                      </p>
                      <p className="mt-1 min-h-5 max-w-lg text-xs leading-relaxed text-muted-foreground">
                        {interimText
                          ? <span aria-hidden="true">„{interimText}“</span>
                          : recording
                            ? (german ? "Die Wellen erscheinen nur, wenn Sprache erkannt wird." : "The waves appear only while speech is detected.")
                            : (german ? "Erneut tippen stoppt die Erkennung. Bereits erkannter Text bleibt im Entwurf." : "Tap again to stop. Recognised text stays in your draft.")}
                      </p>
                    </div>
                    <div className="border-t border-border/70 bg-background/25 p-2">
                      <Textarea
                        ref={composerRef}
                        value={composerText}
                        onChange={(event) => setComposerText(event.target.value.slice(0, composerMaxChars))}
                        rows={2}
                        maxLength={composerMaxChars}
                        disabled={appending || pendingAppend || composerMaxChars < 1}
                        className="max-h-32 min-h-14 resize-none border-0 bg-transparent px-2 py-2 text-sm shadow-none focus-visible:ring-0"
                        placeholder={german ? "Final erkannter Text sammelt sich hier…" : "Final recognised text collects here…"}
                        aria-label={german ? "Erkannter Sprachentwurf" : "Recognised speech draft"}
                      />
                      <div className="flex items-center justify-between gap-3 border-t border-border/70 px-1 pt-2">
                        <span className="text-[0.625rem] text-muted-foreground">{german ? "Text bleibt bearbeitbar" : "Text remains editable"}</span>
                        <Button type="submit" size="icon" className="size-8 rounded-full" disabled={(!composerText.trim() && !pendingAppend) || appending || recording || events.isLoading} aria-label={pendingAppend ? (german ? "Gedanke erneut bestätigen" : "Retry thought") : (german ? "Gedanke senden" : "Send thought")}>{appending ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}</Button>
                      </div>
                    </div>
                  </section>
                )}
              </div>
              {inputMode === "microphone" && (
                <details className="mx-auto mt-2 max-w-3xl rounded-xl border border-border/70 bg-secondary/20 px-3 py-2">
                  <summary className="cursor-pointer text-[0.6875rem] font-medium text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moss">
                    {german ? "Erkennungshilfe" : "Recognition hints"}
                    <span className="ml-1 font-normal text-muted-foreground">({german ? "optional" : "optional"})</span>
                  </summary>
                  <div className="pt-1">
                    <label htmlFor="brainstorming-speech-hints" className="sr-only">{german ? "Begriffe für die Spracherkennung" : "Terms for speech recognition"}</label>
                    <Input
                      id="brainstorming-speech-hints"
                      value={speechHintText}
                      onChange={(event) => setSpeechHintText(event.target.value.slice(0, 500))}
                      onBlur={() => {
                        const hints = normalizeSpeechHints(speechHintText);
                        setSpeechHintText(hints.join(", "));
                        if (userId) writeSpeechHints(userId, session.project_id, hints);
                      }}
                      disabled={recording}
                      maxLength={500}
                      className="h-8 border-0 bg-transparent px-0 text-xs shadow-none focus-visible:ring-0"
                      placeholder={german ? "z. B. Methodology, Terraform, IaC" : "e.g. Methodology, Terraform, IaC"}
                      aria-describedby="brainstorming-speech-hints-note"
                    />
                    <p id="brainstorming-speech-hints-note" className="text-[0.625rem] leading-relaxed text-muted-foreground">
                      {speechHintSupport === false
                        ? (german ? "Dieser Browser unterstützt kontextuelle Erkennungshilfen nicht; die normale Erkennung läuft weiter. Erkannter Rohtext wird nicht umgeschrieben." : "This browser does not support contextual recognition hints; normal recognition continues. Recognised raw text is not rewritten.")
                        : speechHintSupport === true
                          ? (german ? "Erkennungshilfe aktiv. Sie beeinflusst nur die Browser-Erkennung; erkannter Rohtext wird nicht umgeschrieben." : "Recognition hints active. They only bias browser recognition; recognised raw text is not rewritten.")
                          : (german ? "Bis zu 8 Begriffe. Unterstützung wird beim Start geprüft; erkannter Rohtext wird niemals nachträglich umgeschrieben." : "Up to 8 terms. Support is checked when capture starts; recognised raw text is never rewritten afterwards.")}
                    </p>
                  </div>
                </details>
              )}
              {pendingAppend && !appending && <div role={appendFailure === "rejected" ? "alert" : "status"} className="mx-auto mt-2 flex max-w-3xl flex-wrap items-center justify-between gap-2 text-[0.6875rem] text-amber-600 dark:text-amber-400"><span>{appendFailure === "rejected" ? (german ? "Dieser Entwurf wurde abgelehnt. Prüfe ihn und verwirf ihn lokal oder behalte eine Kopie." : "This draft was rejected. Review it and discard it locally or keep a copy.") : (german ? "Speichern nicht bestätigt. Der Entwurf ist gesperrt; erneutes Senden nutzt dieselbe sichere ID." : "Save was not confirmed. The draft is locked; retrying uses the same safe ID.")}</span><Button type="button" variant="ghost" size="sm" className="h-7 rounded-full" disabled={revalidatingDraft} onClick={requestDiscardLocalDraft}>{revalidatingDraft ? <Loader2 className="size-3.5 animate-spin" /> : null}{german ? "Entwurf verwerfen" : "Discard draft"}</Button></div>}
              {composerMaxChars < 1 && !pendingAppend && <p role="alert" className="mx-auto mt-2 max-w-3xl text-[0.6875rem] text-destructive">{remainingSegmentSlots < 1 ? (german ? "Dieses Brainstorming hat seine Segmentgrenze erreicht. Strukturiere den gespeicherten Stand." : "This brainstorm reached its segment limit. Structure the stored snapshot.") : (german ? "Dieses Brainstorming hat seine Textgrenze erreicht. Strukturiere den gespeicherten Stand." : "This brainstorm reached its text limit. Structure the stored snapshot.")}</p>}
              {speechNotice && (
                <div
                  role={speechNotice.tone === "error" ? "alert" : "status"}
                  data-speech-notice={speechNotice.tone}
                  className={cn(
                    "mx-auto mt-2 flex max-w-3xl items-start justify-between gap-2 rounded-lg border px-3 py-2 text-xs",
                    speechNotice.tone === "error" && "border-destructive/25 bg-destructive/5 text-destructive",
                    speechNotice.tone === "warning" && "border-amber-500/25 bg-amber-500/5 text-amber-700 dark:text-amber-300",
                    speechNotice.tone === "neutral" && "border-border bg-secondary/25 text-muted-foreground",
                  )}
                >
                  <span>{speechNotice.message}</span>
                  <button type="button" className="shrink-0 rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moss" onClick={() => setSpeechNotice(null)} aria-label={german ? "Hinweis schließen" : "Dismiss notice"}><X className="size-3.5" /></button>
                </div>
              )}
              {inputMode === "microphone" && !speechNotice && <p className="mx-auto mt-2 max-w-3xl text-[0.625rem] leading-relaxed text-muted-foreground">{german ? "Dein Browser übernimmt die Spracherkennung. An SixSentences wird nur finaler Text gesendet; je nach Browser kann dessen Anbieter die Erkennung verarbeiten." : "Your browser provides speech recognition. Only final text is sent to SixSentences; depending on the browser, its provider may process recognition."}</p>}
            </form>
          )}
        </>
      )}
    </section>
  );

  return (
    <main data-tour="brainstorming-page" className="flex min-h-0 w-full flex-1 flex-col overflow-hidden">
      <ResizableWorkspaceSplit storageKey="six:brainstorming-split" label={german ? "Brainstorming-Liste und Arbeitsbereich in der Größe ändern" : "Resize brainstorm list and workspace"} defaultPercent={36} primaryMinPx={320} secondaryMinPx={420}>
        {listPane}
        {detailPane}
      </ResizableWorkspaceSplit>

      <AlertDialog
        open={projectMoveConfirmation !== null}
        onOpenChange={(open) => {
          if (open || projectUpdating) return;
          setProjectMoveConfirmation(null);
          setProjectMoveDialogError(null);
          setProjectUpdateError(german
            ? "Die Session wurde nicht verschoben; das Hauptdokument bleibt unverändert."
            : "The session was not moved; the master document remains unchanged.");
        }}
      >
        <AlertDialogContent size="sm">
          <AlertDialogHeader>
            <AlertDialogTitle>
              {german ? "Session verschieben und KI-Dokument leeren?" : "Move session and clear AI document?"}
            </AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-2">
                <p>
                  {german
                    ? `Diese Session belegt das aktuelle KI-Hauptdokument von „${projectMoveConfirmation?.sourceProjectName ?? ""}“. Beim Verschieben nach „${projectMoveConfirmation?.targetProjectName ?? ""}“ werden dort die KI-generierten Cluster, Verbindungen und Quellen entfernt, damit keine veralteten Belege stehen bleiben.`
                    : `This session supports the current AI master document in “${projectMoveConfirmation?.sourceProjectName ?? ""}”. Moving it to “${projectMoveConfirmation?.targetProjectName ?? ""}” removes that document's AI-generated clusters, connections, and sources so stale evidence cannot remain.`}
                </p>
                <p>
                  {german
                    ? "Deine eigenen Projektnotizen bleiben erhalten. Die Session selbst und ihre Rohgedanken werden nicht gelöscht."
                    : "Your manual project notes are preserved. The session itself and its raw thoughts are not deleted."}
                </p>
                {projectMoveDialogError && (
                  <p role="alert" className="text-destructive">{projectMoveDialogError}</p>
                )}
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={projectUpdating}>
              {german ? "Zuordnung behalten" : "Keep assignment"}
            </AlertDialogCancel>
            <AlertDialogAction
              disabled={projectUpdating}
              onClick={(event) => {
                event.preventDefault();
                void confirmProjectMove();
              }}
            >
              {projectUpdating ? <Loader2 className="size-3.5 animate-spin" /> : null}
              {german ? "Session verschieben" : "Move session"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog
        open={discardDraftSessionId !== null}
        onOpenChange={(open) => {
          if (!open && !revalidatingDraft) setDiscardDraftSessionId(null);
        }}
      >
        <AlertDialogContent size="sm">
          <AlertDialogHeader>
            <AlertDialogTitle>
              {german ? "Lokalen Entwurf verwerfen?" : "Discard local draft?"}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {german
                ? "Nur der nicht bestätigte Entwurf in diesem Browser wird entfernt. Bereits serverseitig gespeicherte Rohgedanken und KI-Strukturen bleiben vollständig erhalten. Das Verwerfen des lokalen Texts kann nicht rückgängig gemacht werden."
                : "Only the unconfirmed draft in this browser will be removed. Raw thoughts and AI structures already stored on the server remain fully available. Discarding the local text cannot be undone."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={revalidatingDraft}>
              {german ? "Entwurf behalten" : "Keep draft"}
            </AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={revalidatingDraft}
              onClick={(event) => {
                event.preventDefault();
                void confirmDiscardLocalDraft();
              }}
            >
              {revalidatingDraft ? <Loader2 className="size-3.5 animate-spin" /> : null}
              {german ? "Lokal verwerfen" : "Discard locally"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <ConfirmDeleteDialog
        target={deleteDialogTarget}
        pending={deleting}
        onCancel={() => {
          if (deleting) return;
          setDeleteConfirmation(null);
          setDeleteDialogError(null);
        }}
        onConfirm={() => void confirmDeleteSession()}
      />
    </main>
  );
}

function ThoughtTimeline({
  session,
  segments,
  locatedSegment,
  locatedLoading,
  locatedFailed,
  locatedNotFound,
  german,
  loading,
  failed,
  onRetry,
  onRetryLocated,
}: {
  session: LiveSession;
  segments: TimelineSegment[];
  locatedSegment: TimelineSegment | null;
  locatedLoading: boolean;
  locatedFailed: boolean;
  locatedNotFound: boolean;
  german: boolean;
  loading: boolean;
  failed: boolean;
  onRetry: () => void;
  onRetryLocated: () => void;
}) {
  if (loading) return <div role="status" className="flex justify-center py-12 text-muted-foreground"><Loader2 className="size-4 animate-spin" /><span className="sr-only">{german ? "Gedanken werden geladen" : "Loading thoughts"}</span></div>;
  if (failed) return <div role="alert" className="rounded-2xl border border-destructive/20 bg-destructive/5 p-5 text-sm text-muted-foreground"><p>{german ? "Die Rohgedanken konnten nicht geladen werden." : "Could not load raw thoughts."}</p><Button type="button" variant="outline" size="sm" className="mt-3 rounded-full" onClick={onRetry}>{german ? "Erneut versuchen" : "Retry"}</Button></div>;
  if (segments.length === 0 && !locatedSegment && !locatedLoading && !locatedFailed) {
    return <div className="rounded-2xl border border-dashed border-border bg-card/40 p-6 text-center text-sm text-muted-foreground">{german ? "Noch keine Gedanken. Tippe unten oder starte die Spracheingabe." : "No thoughts yet. Type below or start speech input."}</div>;
  }
  const earlier = Math.max(0, session.segment_count - segments.length);
  return (
    <div className="space-y-3 pb-2">
      {locatedLoading && (
        <div role="status" className="flex items-center justify-center gap-2 rounded-2xl border border-moss/20 bg-moss-surface/5 px-4 py-5 text-xs text-muted-foreground">
          <Loader2 className="size-4 animate-spin" />
          {german ? "Belegten Rohgedanken laden…" : "Loading the cited raw thought…"}
        </div>
      )}
      {locatedFailed && (
        <div role="alert" className="rounded-2xl border border-destructive/20 bg-destructive/5 p-4 text-xs leading-relaxed text-muted-foreground">
          <p>{locatedNotFound
            ? (german ? "Dieser belegte Rohgedanke wurde nicht gefunden oder ist für dich nicht mehr zugänglich." : "This cited raw thought was not found or is no longer accessible to you.")
            : (german ? "Der belegte ältere Rohgedanke konnte nicht geladen werden." : "The cited older raw thought could not be loaded.")}</p>
          <Button type="button" variant="outline" size="sm" className="mt-3 rounded-full" onClick={onRetryLocated}>{german ? "Erneut versuchen" : "Retry"}</Button>
        </div>
      )}
      {locatedSegment && <ThoughtSegmentArticle segment={locatedSegment} german={german} located />}
      {earlier > 0 && <p className="rounded-xl bg-secondary/40 px-3 py-2 text-center text-[0.6875rem] text-muted-foreground">{german ? `${earlier} frühere Gedanken bleiben gespeichert. Angezeigt werden die neuesten ${segments.length}.` : `${earlier} earlier thoughts remain stored. Showing the latest ${segments.length}.`}</p>}
      {segments.map((segment) => <ThoughtSegmentArticle key={segment.id} segment={segment} german={german} />)}
    </div>
  );
}

function ThoughtSegmentArticle({
  segment,
  german,
  located = false,
}: {
  segment: TimelineSegment;
  german: boolean;
  located?: boolean;
}) {
  const channelLabel = segment.channel === "microphone"
    ? (german ? "Gesprochen" : "Spoken")
    : segment.channel === "system"
      ? (german ? "Systemaudio" : "System audio")
      : (german ? "Getippt" : "Typed");
  return (
    <article
      id={`brainstorm-segment-${segment.id}`}
      tabIndex={-1}
      className={cn(
        "rounded-2xl border bg-card px-4 py-3 outline-none focus-visible:ring-2 focus-visible:ring-moss",
        located ? "border-moss/35 bg-moss-surface/5" : "border-border",
      )}
    >
      {located && (
        <div className="mb-3 rounded-lg bg-moss-surface/10 px-3 py-2">
          <p className="font-mono text-[0.5625rem] uppercase tracking-[0.14em] text-moss">{german ? "Exakt geladene Quelle" : "Located source"}</p>
          <p className="mt-1 text-[0.6875rem] leading-relaxed text-muted-foreground">{german ? "Nur der belegte Rohgedanke wurde geladen; umliegender Kontext ist nicht Teil dieser kompakten Ansicht." : "Only the cited raw thought was loaded; surrounding context is not part of this compact view."}</p>
        </div>
      )}
      <div className="flex items-center gap-2 font-mono text-[0.5625rem] uppercase tracking-[0.12em] text-muted-foreground">
        {segment.channel === "microphone" ? <Mic className="size-3 text-moss" /> : segment.channel === "system" ? <AudioLines className="size-3 text-moss" /> : <FileText className="size-3 text-moss" />}
        {channelLabel}
      </div>
      <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-foreground/90">{segment.text}</p>
    </article>
  );
}
