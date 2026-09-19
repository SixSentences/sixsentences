"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion } from "motion/react";
import {
  ArrowUpRight,
  SendHorizontal,
  BadgeCheck,
  BookMarked,
  ChartColumn,
  ChevronDown,
  Columns2,
  Download,
  FileSearch,
  FileText,
  Globe,
  History,
  Highlighter,
  Languages,
  Library,
  ListChecks,
  ListPlus,
  Loader2,
  MessageCircleQuestion,
  Network,
  Paperclip,
  PanelRightOpen,
  Quote,
  Scale,
  ShieldCheck,
  Square,
  Table2,
  Telescope,
  TextQuote,
  TriangleAlert,
  Trash2,
  UserRound,
  X,
} from "lucide-react";
import { toast } from "sonner";

import FollowUpChips from "@/components/ai/follow-up-chips";
import { CodeAwareText } from "@/components/ai/code-aware-text";
import {
  AgentTimelineRail,
  AgentToolCallCard,
} from "@/components/agent/tool-call-card";
import { WorkspaceActionList } from "@/components/agent/workspace-action-card";
import { AgentThinkingIndicator } from "@/components/agent-work-status";
import ModelPicker from "@/components/search/model-picker";
import PublicWebSearchApproval from "@/components/search/public-web-search-approval";
import {
  isSubstantialTable,
  tableArtifactFromPayload,
  type ResearchArtifact,
  type TableArtifact,
} from "@/components/run/research-artifact-workspace";
import UiResourceFrame from "@/components/run/ui-resource-frame";
import SixMark from "@/components/brand/six-mark";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useChatHistory, useModels, useWorks } from "@/hooks/queries";
import { resolvePrivateModelId } from "@/lib/private-model-selection";
import { track } from "@/lib/analytics";
import {
  ChatStreamError,
  api,
  downloadExport,
  fileToBase64,
  type RunRef,
} from "@/lib/api";
import { buildRunFollowUps } from "@/lib/follow-ups";
import { useAuth } from "@/lib/auth";
import {
  observedSourceTextParts,
  safeAgentDisplayText,
  safeAgentProgressText,
  safeExternalHttpUrl,
} from "@/lib/safe-agent-display";
import {
  userFacingErrorMessage,
  userFacingStoredErrorMessage,
} from "@/lib/user-facing-error";
import { explicitWebResearchRequested } from "@/lib/web-search-consent";
import {
  suggestPublicWebSearchQuery,
  validPublicWebSearchQuery,
} from "@/lib/public-web-search-query";
import {
  normalizeScholarlyWorkId,
  scholarlyWorkUrl,
} from "@/lib/scholarly-work";
import { useRouter } from "next/navigation";
import type {
  ChatAnswer,
  ChatMessage,
  ChatSelection,
  ChatStreamEvent,
  ChatTurnState,
  EvidenceRef,
  RunEvent,
  ToolStepPayload,
  WorkspaceAction,
} from "@/lib/types";
import { cn } from "@/lib/utils";

// Models cite provider-neutral work ids such as [W123] or [pubmed:123]
// (sometimes several ids or a page inside one bracket, despite the prompt),
// invent page anchors like [Seite 1, erstes Highlight], and
// cite web findings by URL-bound [web:...] keys (legacy [domain.tld]);
// all render as chips — unmatched
// brackets would fall through as raw text
const WORK_ID_SOURCE = String.raw`(?:W\d+|pubmed:[1-9]\d{0,11})`;
const CITATION_PATTERN = new RegExp(
  String.raw`\[((?:${WORK_ID_SOURCE}(?:\s*[,;]\s*${WORK_ID_SOURCE})*(?:[\s,;]*(?:pages?|pp\.?|p\.?|seite|s\.?)\s*\d+(?:\s*[-–]\s*\d+)?)?)|(?:(?:seite|pages?|pp\.?|p\.?|s\.?)\s*\d+(?:\s*[-–]\s*\d+)?[^\]]{0,48})|(?:web:[a-f0-9]{16})|(?:[a-z0-9][a-z0-9.-]*\.[a-z]{2,}))\]`,
  "gi",
);
const WORK_ID = new RegExp(WORK_ID_SOURCE, "gi");
const LOOKS_LIKE_WORKS = new RegExp(`^${WORK_ID_SOURCE}`, "i");
const LOOKS_LIKE_DOMAIN = /^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}$/i;
const LOOKS_LIKE_WEB_SOURCE = /^web:[a-f0-9]{16}$/i;
const BRACKET_PAGE = /(?:pages?|pp\.?|p\.?|seite|s\.?)\s*(\d+(?:\s*[-–]\s*\d+)?)/i;

function normalizedAnswer(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

function recoveredAnswer(message: ChatMessage): ChatAnswer {
  const payload = message.payload ?? {};
  const claims = payload.claims as
    | { checked?: number; flagged?: Array<unknown> }
    | undefined;
  return {
    answer: message.content,
    reasoning: typeof payload.reasoning === "string" ? payload.reasoning : null,
    citations: (message.citations ?? []).map((id) => ({ id, title: "" })),
    sources_considered: Number(payload.sources_considered ?? 0),
    tools_used: Array.isArray(payload.tools_used)
      ? payload.tools_used.map(String)
      : [],
    evidence: Array.isArray(payload.evidence)
      ? (payload.evidence as EvidenceRef[])
      : [],
    claims_checked: Number(claims?.checked ?? 0),
    claims_supported: 0,
    claims_flagged: claims?.flagged?.length ?? 0,
    claim_checks: [],
  };
}

async function recoverInterruptedTurn(
  runId: RunRef,
  question: string,
  afterMessageId: number,
  acceptedByStream: boolean,
  turnId: string,
  signal: AbortSignal,
): Promise<
  | { state: "completed"; answer: ChatAnswer }
  | { state: "cancelled" }
  | { state: "not-accepted" }
> {
  const normalizedQuestion = normalizedAnswer(question);
  let acceptedUserId: number | null = null;
  let accepted = acceptedByStream;

  const followAcceptedTurn = async (): Promise<
    | { state: "completed"; answer: ChatAnswer }
    | { state: "cancelled" }
  > => {
    const persistedAnswer = async (): Promise<ChatAnswer | null> => {
      const messages = await api.chatHistory(runId);
      const exact = [...messages].reverse().find(
        (message) =>
          message.role === "assistant" &&
          (message.payload as { turn_id?: unknown } | null)?.turn_id === turnId,
      );
      if (exact) return recoveredAnswer(exact);
      if (acceptedUserId === null) return null;
      const fallback = messages.find(
        (message) =>
          (message.id ?? 0) > acceptedUserId! && message.role === "assistant",
      );
      return fallback ? recoveredAnswer(fallback) : null;
    };
    let attempt = 0;
    while (!signal.aborted) {
      try {
        const terminal = await api.followChatTurn(runId, turnId, () => {}, signal);
        if (terminal.status === "cancelled") return { state: "cancelled" };
        if (terminal.status === "failed") {
          throw new ChatStreamError(
            "terminal",
            true,
            userFacingStoredErrorMessage(
              terminal.message,
              "The answer could not be completed. Please try again.",
            ),
          );
        }
        if (terminal.status === "completed" && terminal.answer) {
          return { state: "completed", answer: terminal.answer };
        }
        if (terminal.status === "completed") {
          const answer = await persistedAnswer();
          if (answer) return { state: "completed", answer };
        }
      } catch (error) {
        if (signal.aborted) {
          throw new ChatStreamError(
            "transport",
            true,
            "The durable turn will continue in the background.",
          );
        }
        if (error instanceof ChatStreamError) throw error;
        try {
          const state = await api.chatTurnStatus(runId, turnId);
          if (state.status === "cancelled") return { state: "cancelled" };
          if (state.status === "failed") {
            throw new ChatStreamError(
              "terminal",
              true,
              userFacingStoredErrorMessage(
                state.error_message,
                "The answer could not be completed. Please try again.",
              ),
            );
          }
          if (state.status === "completed" && state.answer) {
            return { state: "completed", answer: state.answer };
          }
          if (state.status === "completed") {
            const answer = await persistedAnswer();
            if (answer) return { state: "completed", answer };
          }
        } catch (statusError) {
          if (statusError instanceof ChatStreamError) throw statusError;
          // Both owner-scoped recovery routes may be unavailable briefly.
        }
      }
      attempt += 1;
      if (!(await waitForChatReconnect(attempt, signal))) break;
    }
    throw new ChatStreamError(
      "transport",
      true,
      "The durable turn will continue in the background.",
    );
  };

  if (accepted) return followAcceptedTurn();

  // A streamed request commits its user row before it starts tools. If that
  // row appears, the server accepted the turn and a blind retry would create
  // a duplicate. Switch to the owner-scoped, bodyless stream immediately.
  for (let attempt = 0; attempt < 8; attempt += 1) {
    if (signal.aborted) {
      throw new ChatStreamError("transport", false, "The live request was closed.");
    }
    const messages = await api.chatHistory(runId);
    const acceptedMessage = messages.find(
      (message) =>
        (message.id ?? 0) > afterMessageId &&
        message.role === "user" &&
        normalizedAnswer(message.content) === normalizedQuestion,
    );
    if (acceptedMessage) {
      acceptedUserId = acceptedMessage.id ?? afterMessageId + 1;
      accepted = true;
    }

    if (acceptedUserId !== null && accepted) {
      const answer = messages.find(
        (message) =>
          (message.id ?? 0) > acceptedUserId! &&
          message.role === "assistant",
      );
      if (answer) {
        return { state: "completed", answer: recoveredAnswer(answer) };
      }
      return followAcceptedTurn();
    }
    if (attempt < 7) {
      await new Promise((resolve) => window.setTimeout(resolve, 700));
    }
  }
  return { state: "not-accepted" };
}

export interface QueuedChatTurn {
  id: string;
  text: string;
  selection: ChatSelection | null;
  model: string;
  webSearchPublicDataConfirmed: boolean;
  webSearchQuery?: string;
  steering: boolean;
}

function createClientTurnId(): string {
  if (typeof globalThis.crypto?.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  return `turn-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function chatTurnIsActive(turn: ChatTurnState | null): boolean {
  return Boolean(
    turn && ["queued", "running", "cancel_requested"].includes(turn.status),
  );
}

function waitForChatReconnect(attempt: number, signal: AbortSignal): Promise<boolean> {
  if (signal.aborted) return Promise.resolve(false);
  const delay = Math.min(5_000, 300 * 2 ** Math.min(attempt, 4));
  return new Promise((resolve) => {
    const finish = (continueRecovery: boolean) => {
      window.clearTimeout(timer);
      signal.removeEventListener("abort", onAbort);
      resolve(continueRecovery);
    };
    const onAbort = () => finish(false);
    const timer = window.setTimeout(() => finish(true), delay);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

/** Shared chat state: history, the in-flight question, and how to send one.
 * The paper context sizes itself server-side; `selection` carries a passage
 * the user marked in the reader so the next question discusses it. */
export function useRunChat(
  runId: RunRef,
  enabled: boolean,
  runInProgress = false,
  runEvents: RunEvent[] = [],
  expectInitialAnswer = false,
  initialModel = "auto",
) {
  const queryClient = useQueryClient();
  const { data: history } = useChatHistory(runId, enabled);
  const runKey = String(runId);
  const [turnDiscovery, setTurnDiscovery] = useState<{
    runKey: string;
    checked: boolean;
  } | null>(null);
  const [recoveredTurn, setRecoveredTurn] = useState<ChatTurnState | null>(null);
  const checkingActiveTurn = Boolean(
    enabled && (turnDiscovery?.runKey !== runKey || !turnDiscovery.checked),
  );
  const [pendingQuestion, setPendingQuestion] = useState<string | null>(null);
  const [selection, setSelection] = useState<ChatSelection | null>(null);
  const [pendingSelection, setPendingSelection] = useState<ChatSelection | null>(null);
  // The API persists this per conversation. While this run is mounted, a
  // user's fresh selection must win over later query-cache refreshes.
  const { data: modelCatalog } = useModels();
  const [requestedModel, setModelState] = useState(initialModel);
  const model = resolvePrivateModelId(requestedModel, modelCatalog);
  const modelTouchedRef = useRef(false);
  const modelRunRef = useRef(String(runId));
  // React mutation state updates after the event handler returns. Keep an
  // imperative lock as well so a fast double click cannot enqueue the same
  // follow-up twice before `ask.isPending` reaches the rendered controls.
  const sendLockRef = useRef(false);
  const stopLockRef = useRef(false);
  const stoppedTurnIdsRef = useRef(new Set<string>());
  const activeTurnIdRef = useRef<string | null>(null);
  const recoveryAbortRef = useRef<AbortController | null>(null);
  const postRecoveryAbortRef = useRef<AbortController | null>(null);
  const appliedChatEventCursorRef = useRef(new Map<string, number>());
  const queuedSequenceRef = useRef(0);
  const [queuedTurns, setQueuedTurns] = useState<QueuedChatTurn[]>([]);
  const [stopping, setStopping] = useState(false);
  const [failedTurn, setFailedTurn] = useState<{
    text: string;
    selection: ChatSelection | null;
    model: string;
    message: string;
    webSearchQuery?: string;
  } | null>(null);
  const setModel = useCallback((nextModel: string) => {
    modelTouchedRef.current = true;
    setModelState(nextModel);
  }, []);

  useEffect(() => {
    const runKey = String(runId);
    if (modelRunRef.current !== runKey) {
      modelRunRef.current = runKey;
      modelTouchedRef.current = false;
      sendLockRef.current = false;
      stopLockRef.current = false;
      stoppedTurnIdsRef.current.clear();
      activeTurnIdRef.current = null;
      recoveryAbortRef.current?.abort();
      recoveryAbortRef.current = null;
      postRecoveryAbortRef.current?.abort();
      postRecoveryAbortRef.current = null;
      appliedChatEventCursorRef.current.clear();
      setRecoveredTurn(null);
      setTurnDiscovery(null);
      setQueuedTurns([]);
      setModelState(initialModel);
      return;
    }
    if (!modelTouchedRef.current) setModelState(initialModel);
  }, [initialModel, runId]);
  useEffect(
    () => () => {
      postRecoveryAbortRef.current?.abort();
    },
    [],
  );
  // the fresh answer's verification chip attaches to the newest assistant row
  const [lastAnswer, setLastAnswer] = useState<ChatAnswer | null>(null);
  const [streamingText, setStreamingText] = useState("");
  const streamingTextRef = useRef("");
  const [streamingReasoning, setStreamingReasoning] = useState("");
  const [handoffText, setHandoffText] = useState("");
  const [handoffAssistantCount, setHandoffAssistantCount] = useState<
    number | null
  >(null);
  const backgroundAttemptRef = useRef<string | null>(null);
  const [liveActivity, setLiveActivity] = useState<ChatStreamEvent | null>(null);

  // POST streaming and owner-scoped replay feed the same immutable event
  // application path. The API cursor prevents duplicates within a channel;
  // this turn-scoped cursor also protects state if recovery itself reconnects.
  const applyChatStreamEvent = useCallback(
    (event: ChatStreamEvent) => {
      if (event.turn_id) {
        const appliedId = appliedChatEventCursorRef.current.get(event.turn_id) ?? 0;
        if (event.id <= appliedId) return;
        appliedChatEventCursorRef.current.set(event.turn_id, event.id);
      }
      if (event.event === "answer.delta" && event.delta) {
        streamingTextRef.current += event.delta;
        setStreamingText((current) => current + event.delta);
        return;
      }
      if (event.event === "reasoning.delta" && event.reasoning) {
        setStreamingReasoning((current) => current + event.reasoning);
        return;
      }
      if (event.event === "answer.reset") {
        streamingTextRef.current = "";
        setStreamingText("");
        setStreamingReasoning("");
        setHandoffText("");
        setLiveActivity(event);
        return;
      }
      if (
        event.event === "tool.started" ||
        event.event === "tool.completed" ||
        event.event === "tool.failed"
      ) {
        void queryClient.invalidateQueries({ queryKey: ["chat", runKey] });
      }
      setLiveActivity(event);
    },
    [queryClient, runKey],
  );

  // Recovery is keyed only by the server-owned turn id. The first enabled
  // render is gated synchronously by `checkingActiveTurn`, before this effect
  // can run, so a remount cannot race a second submission against discovery.
  useEffect(() => {
    if (!enabled) {
      recoveryAbortRef.current?.abort();
      recoveryAbortRef.current = null;
      activeTurnIdRef.current = null;
      setRecoveredTurn(null);
      setTurnDiscovery({ runKey, checked: true });
      return;
    }
    const controller = new AbortController();
    recoveryAbortRef.current?.abort();
    recoveryAbortRef.current = controller;
    setTurnDiscovery({ runKey, checked: false });

    void (async () => {
      let attempt = 0;
      while (!controller.signal.aborted) {
        try {
          const active = await api.activeChatTurn(runId);
          const turn = active ?? (await api.latestChatTurn(runId));
          if (controller.signal.aborted) return;
          setRecoveredTurn(turn);
          if (chatTurnIsActive(turn)) {
            activeTurnIdRef.current = turn!.turn_id;
            setLiveActivity({
              id: Date.now(),
              event: "activity",
              turn_id: turn!.turn_id,
              phase: "planning",
              label: "Reconnecting to the answer already in progress",
            });
          } else {
            activeTurnIdRef.current = null;
          }
          setTurnDiscovery({ runKey, checked: true });
          return;
        } catch {
          attempt += 1;
          if (!(await waitForChatReconnect(attempt, controller.signal))) return;
        }
      }
    })();

    return () => {
      controller.abort();
      if (recoveryAbortRef.current === controller) recoveryAbortRef.current = null;
    };
  }, [enabled, runId, runKey]);

  useEffect(() => {
    if (!recoveredTurn) return;
    const turnId = recoveredTurn.turn_id;
    if (!chatTurnIsActive(recoveredTurn)) {
      void (async () => {
        await queryClient.refetchQueries({ queryKey: ["chat", runKey] });
        if (recoveredTurn.status === "completed" && recoveredTurn.answer) {
          setLastAnswer(recoveredTurn.answer);
        }
        setRecoveredTurn((current) =>
          current?.turn_id === turnId ? null : current,
        );
      })();
      return;
    }

    const controller = new AbortController();
    recoveryAbortRef.current?.abort();
    recoveryAbortRef.current = controller;
    activeTurnIdRef.current = turnId;

    void (async () => {
      let attempt = 0;
      while (!controller.signal.aborted) {
        try {
          const terminal = await api.followChatTurn(
            runId,
            turnId,
            applyChatStreamEvent,
            controller.signal,
          );
          if (controller.signal.aborted) return;
          await queryClient.refetchQueries({ queryKey: ["chat", runKey] });
          if (terminal.status === "completed" && terminal.answer) {
            setLastAnswer(terminal.answer);
          }
          activeTurnIdRef.current = null;
          setRecoveredTurn(null);
          setLiveActivity(null);
          setStopping(false);
          return;
        } catch {
          if (controller.signal.aborted) return;
          attempt += 1;
          try {
            const state = await api.chatTurnStatus(runId, turnId);
            if (controller.signal.aborted) return;
            setRecoveredTurn(state);
            if (!chatTurnIsActive(state)) continue;
          } catch {
            // The owner-scoped status route may be temporarily unavailable too.
          }
          if (!(await waitForChatReconnect(attempt, controller.signal))) return;
        }
      }
    })();

    return () => {
      controller.abort();
      if (recoveryAbortRef.current === controller) recoveryAbortRef.current = null;
    };
  }, [applyChatStreamEvent, queryClient, recoveredTurn?.turn_id, runId, runKey]);

  const assistantCount = (history ?? []).filter(
    (message) => message.role === "assistant",
  ).length;
  const recoveredActiveTurnId = chatTurnIsActive(recoveredTurn)
    ? recoveredTurn!.turn_id
    : null;
  const recoveredVisibleTurnId = recoveredTurn?.turn_id ?? null;
  const recoveredQuestion = useMemo(() => {
    if (!recoveredVisibleTurnId) return null;
    const message = [...(history ?? [])]
      .reverse()
      .find(
        (entry) =>
          entry.role === "user" &&
          (entry.payload as { turn_id?: unknown } | null)?.turn_id ===
            recoveredVisibleTurnId,
      );
    return message?.content ?? null;
  }, [history, recoveredVisibleTurnId]);
  const visiblePendingQuestion = pendingQuestion ?? recoveredQuestion;
  const persistedAskAnswer = (history ?? []).some(
    (message) =>
      message.role === "assistant" &&
      (message.payload as { kind?: string } | null)?.kind === "ask_answer",
  );
  const askStreamCanBridge =
    (expectInitialAnswer && !persistedAskAnswer) ||
    runInProgress ||
    runEvents.some(
      (event) =>
        event.event === "run_completed" && event.payload.mode === "ask",
    );
  const latestAskAttemptId = useMemo(() => {
    const started = [...runEvents]
      .reverse()
      .find((event) => event.event === "ask_answer_started");
    return typeof started?.payload.attempt_id === "string"
      ? started.payload.attempt_id
      : null;
  }, [runEvents]);
  const latestAskAttemptAborted = useMemo(
    () =>
      latestAskAttemptId !== null &&
      runEvents.some(
        (event) =>
          event.event === "ask_answer_aborted" &&
          event.payload.attempt_id === latestAskAttemptId,
      ),
    [latestAskAttemptId, runEvents],
  );
  const backgroundStreamingText = useMemo(
    () =>
      askStreamCanBridge && !persistedAskAnswer && !latestAskAttemptAborted
        ? runEvents
            .filter(
              (event) =>
                event.event === "ask_answer_delta" &&
                (latestAskAttemptId === null ||
                  event.payload.attempt_id === latestAskAttemptId),
            )
            .map((event) =>
              typeof event.payload.delta === "string" ? event.payload.delta : "",
            )
            .join("")
        : "",
    [
      askStreamCanBridge,
      latestAskAttemptAborted,
      latestAskAttemptId,
      persistedAskAnswer,
      runEvents,
    ],
  );
  const backgroundReasoning = useMemo(
    () =>
      askStreamCanBridge && !persistedAskAnswer && !latestAskAttemptAborted
        ? runEvents
            .filter(
              (event) =>
                event.event === "ask_reasoning_delta" &&
                (latestAskAttemptId === null ||
                  event.payload.attempt_id === latestAskAttemptId),
            )
            .map((event) =>
              typeof event.payload.reasoning === "string"
                ? event.payload.reasoning
                : "",
            )
            .join("")
        : "",
    [
      askStreamCanBridge,
      latestAskAttemptAborted,
      latestAskAttemptId,
      persistedAskAnswer,
      runEvents,
    ],
  );
  const backgroundActivity = useMemo<ChatStreamEvent | null>(() => {
    if (!askStreamCanBridge || persistedAskAnswer || latestAskAttemptAborted) return null;
    const event = [...runEvents]
      .reverse()
      .find(
        (entry) =>
          [
            "ask_queued",
            "ask_planned",
            "ask_retrieval_done",
            "ask_reader_started",
            "ask_library_started",
            "ask_answer_started",
            "ask_answer_finalizing",
            "ask_answer_verifying",
          ].includes(entry.event) &&
          (latestAskAttemptId === null ||
            entry.event === "ask_queued" ||
            !entry.event.startsWith("ask_answer_") ||
            entry.payload.attempt_id === latestAskAttemptId),
      );
    const label = event?.payload.label;
    if (!event || typeof label !== "string") return null;
    return {
      id: event.id,
      event: "activity",
      phase:
        event.event === "ask_answer_finalizing" ||
        event.event === "ask_answer_verifying"
          ? "verification"
          : event.event === "ask_reader_started"
            ? "reading"
            : event.event === "ask_library_started"
              ? "library"
              : event.event === "ask_retrieval_done"
                ? "retrieval"
                : event.event === "ask_answer_started"
                  ? "writing"
                  : "planning",
      label,
      sources:
        typeof event.payload.sources === "number"
          ? event.payload.sources
          : undefined,
    };
  }, [
    askStreamCanBridge,
    latestAskAttemptAborted,
    latestAskAttemptId,
    persistedAskAnswer,
    runEvents,
  ]);
  const liveStreamingText = streamingText || backgroundStreamingText;
  const normalizedHandoff = normalizedAnswer(handoffText);
  const handoffPersisted =
    normalizedHandoff.length > 0 &&
    ((handoffAssistantCount !== null &&
      assistantCount > handoffAssistantCount) ||
      (history ?? []).some(
        (message) =>
          message.role === "assistant" &&
          normalizedAnswer(message.content) === normalizedHandoff,
      ));

  useEffect(() => {
    if (latestAskAttemptId === backgroundAttemptRef.current) return;
    if (backgroundAttemptRef.current !== null) {
      setHandoffText("");
      setHandoffAssistantCount(null);
    }
    backgroundAttemptRef.current = latestAskAttemptId;
  }, [latestAskAttemptId]);

  // Run events and chat history arrive independently. Keep the last live
  // draft mounted until this turn's persisted assistant row reaches history.
  useEffect(() => {
    if (!liveStreamingText) return;
    setHandoffText(liveStreamingText);
    setHandoffAssistantCount((current) => current ?? assistantCount);
  }, [assistantCount, liveStreamingText]);

  useEffect(() => {
    if (!handoffPersisted) return;
    setHandoffText("");
    setHandoffAssistantCount(null);
  }, [handoffPersisted]);

  useEffect(() => {
    if (!latestAskAttemptAborted) return;
    setHandoffText("");
    setHandoffAssistantCount(null);
  }, [latestAskAttemptAborted]);

  // The API publishes the user turn and each real tool call as it happens.
  // Poll only while a turn is active; this keeps the normal chat completely
  // quiet while making long research actions visible immediately.
  useEffect(() => {
    const awaitingAskPersistence = askStreamCanBridge && !persistedAskAnswer;
    const awaitingHandoff = handoffText.length > 0 && !handoffPersisted;
    const activeTurn =
      Boolean(pendingQuestion) || Boolean(recoveredVisibleTurnId) || runInProgress;
    if (!activeTurn && !awaitingAskPersistence && !awaitingHandoff) return;
    const refresh = () =>
      void queryClient.invalidateQueries({ queryKey: ["chat", String(runId)] });
    refresh();
    const timer = window.setInterval(refresh, 700);
    const settleTimer = activeTurn
      ? null
      : window.setTimeout(() => window.clearInterval(timer), 15_000);
    return () => {
      window.clearInterval(timer);
      if (settleTimer !== null) window.clearTimeout(settleTimer);
    };
  }, [
    askStreamCanBridge,
    handoffPersisted,
    handoffText,
    pendingQuestion,
    recoveredVisibleTurnId,
    persistedAskAnswer,
    expectInitialAnswer,
    queryClient,
    runId,
    runInProgress,
  ]);

  const ask = useMutation({
    mutationFn: async ({
      text,
      sel,
      afterMessageId,
      selectedModel,
      turnId,
      webSearchPublicDataConfirmed,
      webSearchQuery,
    }: {
      text: string;
      sel: ChatSelection | null;
      afterMessageId: number;
      selectedModel: string;
      turnId: string;
      webSearchPublicDataConfirmed: boolean;
      webSearchQuery?: string;
    }) => {
      const stream = () =>
        api.chatStream(
          runId,
          text,
          sel
            ? { document_id: sel.document_id, page: sel.page, quote: sel.quote }
            : undefined,
          selectedModel,
          turnId,
          applyChatStreamEvent,
          { webSearchPublicDataConfirmed, webSearchQuery },
        );
      try {
        return await stream();
      } catch (error) {
        if (!(error instanceof ChatStreamError)) throw error;
        // `turn.failed` is an intentional terminal result, not a broken
        // network channel. Retrying it here would duplicate a committed user
        // message and repeat every completed tool action.
        if (error.kind === "terminal" || error.kind === "cancelled") {
          await queryClient.invalidateQueries({
            queryKey: ["chat", String(runId)],
          });
          throw error;
        }
        setLiveActivity({
          id: Date.now(),
          event: "activity",
          phase: "planning",
          label: "Reconnecting to the answer already in progress",
        });
        const controller = new AbortController();
        postRecoveryAbortRef.current?.abort();
        postRecoveryAbortRef.current = controller;
        let recovered: Awaited<ReturnType<typeof recoverInterruptedTurn>>;
        try {
          recovered = await recoverInterruptedTurn(
            runId,
            text,
            afterMessageId,
            error.accepted,
            turnId,
            controller.signal,
          );
        } finally {
          if (postRecoveryAbortRef.current === controller) {
            postRecoveryAbortRef.current = null;
          }
        }
        if (recovered.state === "cancelled") {
          throw new ChatStreamError("cancelled", true, "This answer was stopped.");
        }
        if (recovered.state === "completed") return recovered.answer;
        // History proves that no user row was committed. Only this state may
        // receive one transport retry; accepted and terminal turns never do.
        return stream();
      }
    },
    onMutate: ({ text, sel, turnId }) => {
      setFailedTurn(null);
      activeTurnIdRef.current = turnId;
      setPendingQuestion(text);
      setPendingSelection(sel);
      streamingTextRef.current = "";
      setStreamingText("");
      setStreamingReasoning("");
      setHandoffText("");
      setHandoffAssistantCount(assistantCount);
      setLiveActivity(null);
    },
    onSuccess: async (answer) => {
      track("chat_message", { surface: "run" });
      setHandoffText(answer.answer);
      streamingTextRef.current = "";
      setStreamingText("");
      setStreamingReasoning("");
      await queryClient.invalidateQueries({ queryKey: ["chat", String(runId)] });
      if (answer.tools_used?.includes("edit_pdf_comment")) {
        await queryClient.invalidateQueries({ queryKey: ["document-annotations"] });
      }
      setLastAnswer(answer);
      setLiveActivity(null);
    },
    onError: (error, variables) => {
      const intentionallyCancelled =
        error instanceof ChatStreamError && error.kind === "cancelled";
      if (intentionallyCancelled && streamingTextRef.current) {
        // Keep the useful partial answer, but never expose the structured
        // citation trailer that is only meant for server-side postprocessing.
        setHandoffText(visibleStreamingText(streamingTextRef.current));
        setHandoffAssistantCount((current) => current ?? assistantCount);
      } else if (!intentionallyCancelled) {
        setHandoffText("");
        setHandoffAssistantCount(null);
      }
      streamingTextRef.current = "";
      setStreamingText("");
      setStreamingReasoning("");
      setLiveActivity(null);
      if (!intentionallyCancelled) {
        const message = userFacingErrorMessage(
          error,
          "The answer could not be completed. Please try again.",
        );
        setFailedTurn({
          text: variables.text,
          selection: variables.sel,
          model: variables.selectedModel,
          message,
          webSearchQuery: variables.webSearchQuery,
        });
        toast.error(message);
      }
    },
    onSettled: (_data, _error, variables) => {
      sendLockRef.current = false;
      if (activeTurnIdRef.current === variables.turnId) {
        activeTurnIdRef.current = null;
      }
      stoppedTurnIdsRef.current.delete(variables.turnId);
      setStopping(false);
      setPendingQuestion(null);
      setPendingSelection(null);
    },
  });

  const turnActive =
    runInProgress ||
    ask.isPending ||
    checkingActiveTurn ||
    Boolean(recoveredVisibleTurnId);

  useEffect(() => {
    if (turnActive) return;
    stopLockRef.current = false;
    stoppedTurnIdsRef.current.delete("initial-run");
    setStopping(false);
  }, [turnActive]);

  function launchTurn(turn: Omit<QueuedChatTurn, "id" | "steering">): boolean {
    if (sendLockRef.current || ask.isPending || runInProgress) return false;
    if (checkingActiveTurn || recoveredVisibleTurnId) return false;
    const turnId = createClientTurnId();
    sendLockRef.current = true;
    activeTurnIdRef.current = turnId;
    const afterMessageId = Math.max(
      0,
      ...(history ?? []).map((message) => message.id ?? 0),
    );
    ask.mutate({
      text: turn.text,
      sel: turn.selection,
      afterMessageId,
      selectedModel: resolvePrivateModelId(turn.model, modelCatalog),
      turnId,
      webSearchPublicDataConfirmed: turn.webSearchPublicDataConfirmed,
      webSearchQuery: turn.webSearchQuery,
    });
    return true;
  }

  function enqueueTurn(
    text: string,
    queuedSelection: ChatSelection | null,
    webSearchPublicDataConfirmed: boolean,
    webSearchQuery?: string,
    steering = false,
  ): string {
    queuedSequenceRef.current += 1;
    const id = `queued-${queuedSequenceRef.current}`;
    setQueuedTurns((current) => [
      ...current,
      {
        id,
        text,
        selection: queuedSelection,
        model,
        webSearchPublicDataConfirmed,
        webSearchQuery,
        steering,
      },
    ]);
    return id;
  }

  // A queued instruction starts only after the previous turn has reached a
  // terminal state. The imperative send lock closes the one-render window in
  // which React Query has not exposed `isPending` yet.
  useEffect(() => {
    if (runInProgress || ask.isPending || sendLockRef.current) return;
    if (checkingActiveTurn || recoveredVisibleTurnId) return;
    const next = queuedTurns[0];
    if (!next) return;
    if (
      launchTurn({
        text: next.text,
        selection: next.selection,
        model: next.model,
        webSearchPublicDataConfirmed: next.webSearchPublicDataConfirmed,
        webSearchQuery: next.webSearchQuery,
      })
    ) {
      setQueuedTurns((current) => current.filter((turn) => turn.id !== next.id));
    }
  // `launchTurn` intentionally reads the latest history snapshot while this
  // effect is driven by terminal turn state and the stable queue order.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ask.isPending, checkingActiveTurn, queuedTurns, recoveredVisibleTurnId, runInProgress]);

  async function stopActiveTurn(): Promise<void> {
    if (stopLockRef.current || !turnActive || checkingActiveTurn) return;
    stopLockRef.current = true;
    setStopping(true);
    const activeTurnId = activeTurnIdRef.current;
    const stopKey = activeTurnId ?? "initial-run";
    if (stoppedTurnIdsRef.current.has(stopKey)) {
      stopLockRef.current = false;
      return;
    }
    stoppedTurnIdsRef.current.add(stopKey);
    try {
      if (activeTurnId) {
        await api.stopChatTurn(runId, activeTurnId);
      } else {
        await api.cancelRun(runId);
      }
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["run", String(runId)] }),
        queryClient.invalidateQueries({ queryKey: ["run-events", String(runId)] }),
        queryClient.invalidateQueries({ queryKey: ["chat", String(runId)] }),
      ]);
    } catch (error) {
      stoppedTurnIdsRef.current.delete(stopKey);
      setStopping(false);
      toast.error(error instanceof Error ? error.message : "Stopping failed.");
    } finally {
      stopLockRef.current = false;
    }
  }

  async function steerQueuedTurn(id: string): Promise<void> {
    const queued = queuedTurns.find((turn) => turn.id === id);
    if (!queued) return;
    setQueuedTurns((current) => [
      ...current
        .filter((turn) => turn.id === id)
        .map((turn) => ({ ...turn, steering: true })),
      ...current
        .filter((turn) => turn.id !== id)
        .map((turn) => ({ ...turn, steering: false })),
    ]);
    if (turnActive) await stopActiveTurn();
  }

  return {
    messages: (history ?? []) as ChatMessage[],
    pendingQuestion: visiblePendingQuestion,
    activeTurnId: activeTurnIdRef.current,
    pendingSelection,
    lastAnswer,
    streamingText:
      liveStreamingText || (handoffPersisted ? "" : handoffText),
    streamingReasoning: streamingReasoning || backgroundReasoning,
    liveActivity: liveActivity ?? backgroundActivity,
    selection,
    setSelection,
    model,
    setModel,
    send: (
      text: string,
      options?: { webSearchPublicDataConfirmed?: boolean; webSearchQuery?: string },
    ) => {
      const queuedSelection = selection;
      const webSearchPublicDataConfirmed = Boolean(
        explicitWebResearchRequested(text) &&
          options?.webSearchPublicDataConfirmed,
      );
      const webSearchQuery = webSearchPublicDataConfirmed
        ? options?.webSearchQuery?.trim()
        : undefined;
      if (turnActive || sendLockRef.current) {
        enqueueTurn(text, queuedSelection, webSearchPublicDataConfirmed, webSearchQuery);
      } else {
        launchTurn({
          text,
          selection: queuedSelection,
          model,
          webSearchPublicDataConfirmed,
          webSearchQuery,
        });
      }
      setSelection(null); // consumed by this turn
    },
    queue: (
      text: string,
      options?: { webSearchPublicDataConfirmed?: boolean; webSearchQuery?: string },
    ) => {
      enqueueTurn(
        text,
        selection,
        Boolean(
          explicitWebResearchRequested(text) &&
            options?.webSearchPublicDataConfirmed,
        ),
        explicitWebResearchRequested(text) && options?.webSearchPublicDataConfirmed
          ? options?.webSearchQuery?.trim()
          : undefined,
      );
      setSelection(null); // consumed by the queued turn
    },
    sending: ask.isPending,
    active: turnActive,
    stopping:
      stopping || checkingActiveTurn || Boolean(recoveredTurn && !recoveredActiveTurnId),
    reconnecting: checkingActiveTurn || Boolean(recoveredVisibleTurnId),
    discovering: checkingActiveTurn,
    stop: stopActiveTurn,
    queuedTurns,
    failedTurn,
    dismissFailedTurn: () => setFailedTurn(null),
    retryFailedTurn: (webSearchPublicDataConfirmed = false) => {
      if (!failedTurn || turnActive || sendLockRef.current) return;
      const retry = failedTurn;
      const confirmedForThisRetry = Boolean(
        explicitWebResearchRequested(retry.text) &&
          webSearchPublicDataConfirmed,
      );
      setFailedTurn(null);
      launchTurn({
        text: retry.text,
        selection: retry.selection,
        model: retry.model,
        webSearchPublicDataConfirmed: confirmedForThisRetry,
        webSearchQuery: confirmedForThisRetry ? retry.webSearchQuery : undefined,
      });
    },
    removeQueued: (id: string) =>
      setQueuedTurns((current) => current.filter((turn) => turn.id !== id)),
    steerQueued: steerQueuedTurn,
  };
}

export type RunChat = ReturnType<typeof useRunChat>;

function ReasoningDisclosure({
  reasoning,
  live = false,
}: {
  reasoning: string;
  live?: boolean;
}) {
  const [open, setOpen] = useState(live);
  const { me } = useAuth();
  const language = me?.language ?? "en";

  useEffect(() => {
    if (live) setOpen(true);
  }, [live]);

  const phaseIds = Array.from(
    new Set(
      reasoning
        .split("\n")
        .map((phase) => phase.trim())
        .filter(Boolean),
    ),
  );
  const phaseLabels: Record<string, { en: string; de: string }> = {
    research_plan: {
      en: "Planning the next research step",
      de: "Nächsten Rechercheschritt planen",
    },
    source_comparison: {
      en: "Comparing source coverage and evidence",
      de: "Quellenabdeckung und Evidenz vergleichen",
    },
    conflict_check: {
      en: "Checking conflicting evidence",
      de: "Widersprüchliche Evidenz prüfen",
    },
    citation_check: {
      en: "Verifying claims against the sources",
      de: "Aussagen anhand der Quellen prüfen",
    },
    answer_structure: {
      en: "Structuring the grounded response",
      de: "Belegte Antwort strukturieren",
    },
    evidence_analysis: {
      en: "Analysing the retrieved material",
      de: "Gefundenes Material analysieren",
    },
  };
  const visiblePhases = phaseIds
    .map((phase) => phaseLabels[phase]?.[language])
    .filter((phase): phase is string => Boolean(phase));

  // Never turn unknown provider scratchpad text into generic fake progress.
  // The timeline already shows actual searches, page reads and other tools.
  // Only explicitly safe phase identifiers belong in this optional panel.
  if (!visiblePhases.length) return null;
  return (
    <div className="mb-3 max-w-3xl overflow-hidden rounded-xl border border-border/75 bg-secondary/35">
      <button
        type="button"
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-[0.71875rem] font-medium text-muted-foreground transition-colors hover:bg-accent/55 hover:text-foreground"
        onClick={() => setOpen((current) => !current)}
        aria-expanded={open}
      >
        <motion.span
          className={cn(
            "size-1.5 rounded-full bg-moss-surface",
            live && "shadow-[0_0_0_3px_hsl(var(--moss)/0.12)]",
          )}
          animate={live ? { opacity: [0.35, 1, 0.35] } : undefined}
          transition={live ? { duration: 1.2, repeat: Infinity } : undefined}
        />
        <span className="flex-1">
          {language === "de"
            ? live
              ? "Analyse läuft"
              : "Analyse abgeschlossen"
            : live
              ? "Analysis in progress"
              : "Analysis complete"}
        </span>
        <ChevronDown
          className={cn("size-3.5 transition-transform", open && "rotate-180")}
        />
      </button>
      <AnimatePresence initial={false}>
        {open ? (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2, ease: [0.22, 1, 0.36, 1] }}
          >
            <div className="max-h-56 space-y-2 overflow-y-auto border-t border-border/65 px-3 py-2.5 text-[0.75rem] leading-relaxed text-muted-foreground">
              {visiblePhases.map((phase, index) => {
                const active = live && index === visiblePhases.length - 1;
                return (
                  <div className="flex items-center gap-2" key={`${phase}-${index}`}>
                    {active ? (
                      <motion.span
                        className="size-1.5 shrink-0 rounded-full bg-moss-surface"
                        animate={{ opacity: [0.35, 1, 0.35] }}
                        transition={{ duration: 1.2, repeat: Infinity }}
                      />
                    ) : (
                      <BadgeCheck className="size-3.5 shrink-0 text-moss" />
                    )}
                    <span>{phase}</span>
                  </div>
                );
              })}
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}

/** Render answer text with [W…], [web:…] and legacy domain citation chips.
 * Uploaded papers (present in `docs`) open the in-app reader on click —
 * their synthetic ids have no page anywhere else on the web. */
function CitedProse({
  text,
  numbering,
  titles,
  webLinks,
  docs,
}: {
  text: string;
  numbering: Map<string, number>;
  titles: Map<string, string>;
  webLinks?: Map<string, string>;
  docs?: Map<string, { documentId: number; title?: string }>;
}) {
  const parts = text.split(CITATION_PATTERN);
  const chipClass =
    "mx-0.5 inline-flex h-[1.0625rem] min-w-[1.0625rem] translate-y-[-1px] cursor-pointer items-center justify-center rounded-full bg-accent px-1 align-middle font-mono text-[0.625rem] font-medium text-moss ring-1 ring-inset ring-moss/25 transition-colors hover:bg-moss-surface hover:text-ivory";
  return (
    <span className="whitespace-pre-wrap">
      {parts.map((part, index) => {
        if (index % 2 === 0) {
          return (
            <span key={index}>
              {observedSourceTextParts(part, webLinks?.values() ?? []).map((segment, segmentIndex) =>
                segment.href ? (
                  <a
                    key={segmentIndex}
                    href={segment.href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="break-words text-moss underline decoration-moss/40 underline-offset-2 hover:decoration-moss"
                  >
                    {segment.text}
                  </a>
                ) : <span key={segmentIndex}>{segment.text}</span>,
              )}
            </span>
          );
        }
        if (LOOKS_LIKE_DOMAIN.test(part) || LOOKS_LIKE_WEB_SOURCE.test(part)) {
          // Resolve only observed page URLs. A missing or ambiguous legacy
          // domain citation must not invent a homepage or choose the first hit.
          const sourceKey = part.toLowerCase().replace(/^www\./, "");
          const href = safeExternalHttpUrl(webLinks?.get(sourceKey));
          const domain = href
            ? new URL(href).hostname.replace(/^www\./, "")
            : LOOKS_LIKE_DOMAIN.test(part) ? sourceKey : "Source";
          const webChipClass = "mx-0.5 inline-flex max-w-[13.75rem] translate-y-[-1px] items-center gap-1 truncate rounded-full bg-secondary px-2 py-0.5 align-middle text-[0.65625rem] font-medium text-foreground/75 ring-1 ring-inset ring-border";
          if (!href) {
            return (
              <span key={index} className={webChipClass}>
                <Globe className="size-2.5 shrink-0" />
                {domain}
              </span>
            );
          }
          return (
            <a
              key={index}
              href={href}
              target="_blank"
              rel="noreferrer"
              className={cn(webChipClass, "transition-colors hover:bg-accent hover:text-moss")}
            >
              <Globe className="size-2.5 shrink-0" />
              {domain}
            </a>
          );
        }
        if (!LOOKS_LIKE_WORKS.test(part)) {
          // a bare page anchor ([Seite 1, erstes Highlight] / [page 3]):
          // clicking it opens the discussed paper's reader on that page
          const anchorPage = BRACKET_PAGE.exec(part)?.[1];
          const anchorDoc = docs && docs.size > 0 ? [...docs.values()][0] : undefined;
          if (!anchorPage) return <span key={index}>[{part}]</span>;
          const label = `p. ${anchorPage}`;
          return anchorDoc ? (
            <button
              key={index}
              type="button"
              onClick={() =>
                window.dispatchEvent(
                  new CustomEvent("six:open-paper", {
                    detail: {
                      documentId: anchorDoc.documentId,
                      title: anchorDoc.title ?? "Paper",
                      highlights: [],
                      page: Number(anchorPage.split(/\D/)[0]),
                    },
                  }),
                )
              }
              className="mx-0.5 inline-flex translate-y-[-1px] cursor-pointer items-center rounded-full bg-moss-surface/10 px-1.5 py-0.5 align-middle font-mono text-[0.625rem] font-medium text-moss transition-colors hover:bg-moss-surface hover:text-ivory"
              title="Show this page in the paper"
            >
              {label}
            </button>
          ) : (
            <span
              key={index}
              className="mx-0.5 inline-flex translate-y-[-1px] items-center rounded-full bg-secondary px-1.5 py-0.5 align-middle font-mono text-[0.625rem] text-muted-foreground"
            >
              {label}
            </span>
          );
        }
        const ids = (part.match(WORK_ID) ?? []).map(normalizeScholarlyWorkId);
        const page = BRACKET_PAGE.exec(part)?.[1];
        return (
          <span key={index}>
            {ids.map((id) => {
              const number = numbering.get(id);
              const doc = docs?.get(id);
              return (
                <Tooltip key={id}>
                  <TooltipTrigger asChild>
                    {doc ? (
                      <button
                        type="button"
                        className={chipClass}
                        onClick={() =>
                          window.dispatchEvent(
                            new CustomEvent("six:open-paper", {
                              detail: {
                                documentId: doc.documentId,
                                title: titles.get(id) ?? doc.title ?? "Paper",
                                highlights: [],
                                page: page ? Number(page.split(/\D/)[0]) : undefined,
                              },
                            }),
                          )
                        }
                      >
                        {number ?? "•"}
                      </button>
                    ) : (
                      <a
                        href={scholarlyWorkUrl(id) ?? undefined}
                        target="_blank"
                        rel="noreferrer"
                        className={chipClass}
                      >
                        {number ?? "•"}
                      </a>
                    )}
                  </TooltipTrigger>
                  <TooltipContent side="top" className="max-w-[18.75rem]">
                    {doc
                      ? `${titles.get(id) ?? doc.title ?? "Your upload"} · opens in the reader`
                      : `${titles.get(id) ?? id}`}
                  </TooltipContent>
                </Tooltip>
              );
            })}
            {page && (
              <span className="text-[0.71875rem] text-muted-foreground">
                {" "}
                (p. {page})
              </span>
            )}
          </span>
        );
      })}
    </span>
  );
}

function CitedText({
  text,
  ...cited
}: {
  text: string;
  numbering: Map<string, number>;
  titles: Map<string, string>;
  webLinks?: Map<string, string>;
  docs?: Map<string, { documentId: number; title?: string }>;
}) {
  return (
    <CodeAwareText
      text={text}
      renderText={(part, index) => (
        <CitedProse key={index} text={part} {...cited} />
      )}
    />
  );
}

function AssistantAvatar() {
  return (
    <span className="grid size-10 shrink-0 place-items-center rounded-full bg-muted ring-1 ring-inset ring-border">
      <SixMark className="h-5 w-5 text-foreground" />
    </span>
  );
}

/** The passage a user question discusses, shown as a quote above the bubble.
 * Clicking it re-opens the reader on that page. */
function SelectionQuote({ selection }: { selection: ChatSelection }) {
  return (
    <button
      type="button"
      onClick={() =>
        window.dispatchEvent(
          new CustomEvent("six:open-paper", {
            detail: {
              documentId: selection.document_id,
              title: selection.title ?? "Paper",
              highlights: [],
              page: selection.page,
            },
          }),
        )
      }
      className="max-w-[85%] cursor-pointer rounded-2xl rounded-br-md border border-moss/25 bg-accent/60 px-3.5 py-2 text-left transition-colors hover:border-moss/50"
    >
      <span className="flex items-start gap-2">
        <TextQuote className="mt-0.5 size-3 shrink-0 text-moss" />
        <span className="min-w-0">
          <span className="line-clamp-2 block text-[0.75rem] italic leading-snug text-foreground/80">
            “{selection.quote}”
          </span>
          <span className="mt-0.5 block font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
            p.{selection.page}
            {selection.title ? ` · ${selection.title.slice(0, 60)}` : ""}
          </span>
        </span>
      </span>
    </button>
  );
}

/** Verified passage references under an answer about an attached paper.
 * Clicking one opens the reader on that page with the passage lit up. */
function EvidenceChips({
  evidence,
  titles,
}: {
  evidence: EvidenceRef[];
  titles: Map<string, string>;
}) {
  if (evidence.length === 0) return null;
  return (
    <div className="mt-2.5 flex flex-wrap gap-1.5">
      {evidence.map((ref, index) => (
        <button
          key={index}
          type="button"
          onClick={() =>
            window.dispatchEvent(
              new CustomEvent("six:open-paper", {
                detail: {
                  documentId: ref.document_id,
                  title: titles.get(ref.work_id) ?? "Paper",
                  highlights: [],
                  page: ref.page,
                  flash: { page: ref.page, quote: ref.quote },
                },
              }),
            )
          }
          className="group/evidence flex max-w-full cursor-pointer items-center gap-1.5 rounded-full border border-moss/25 bg-accent/50 py-1 pl-2 pr-2.5 text-left transition-colors hover:border-moss/50 hover:bg-accent"
          title="Show this passage in the paper"
        >
          <span className="shrink-0 rounded-full bg-moss-surface/10 px-1.5 py-px font-mono text-[0.59375rem] font-medium text-moss">
            p.{ref.page}
          </span>
          <span className="truncate text-[0.6875rem] italic text-foreground/70 group-hover/evidence:text-foreground/90">
            “{ref.quote.length > 90 ? `${ref.quote.slice(0, 90)}…` : ref.quote}”
          </span>
        </button>
      ))}
    </div>
  );
}

/** Download card for a citation-file export prepared by the agent. */
function ExportCard({
  runId,
  format,
  count,
  includedOnly,
}: {
  runId: RunRef;
  format: string;
  count: number;
  includedOnly: boolean;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <div className="flex gap-3">
      <span className="grid size-10 shrink-0 place-items-center rounded-full border border-dashed border-moss/40 bg-accent/50 text-moss">
        <Download className="size-4" />
      </span>
      <div className="flex min-w-0 max-w-full items-center gap-3 rounded-2xl border border-border bg-card px-4 py-3">
        <span className="min-w-0">
          <span className="block truncate text-[0.8125rem] font-medium">
            {format.toUpperCase()} export
          </span>
          <span className="mt-0.5 block font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
            {count} {includedOnly ? "included works" : "works"}
          </span>
        </span>
        <Button
          size="sm"
          className="h-7 shrink-0 rounded-full text-[0.75rem]"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            try {
              await downloadExport(runId, format as "bibtex" | "ris" | "csl", includedOnly);
            } catch (error) {
              toast.error(userFacingErrorMessage(error, "The download failed."));
            } finally {
              setBusy(false);
            }
          }}
        >
          {busy ? <Loader2 className="size-3 animate-spin" /> : <Download className="size-3" />}
          Download
        </Button>
      </div>
    </div>
  );
}

/** The agent proposed a systematic search; it starts only on this click. */
function SearchProposalCard({
  question,
  query,
  projectId,
}: {
  question: string;
  query: string;
  projectId: number | null;
}) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const start = useMutation({
    mutationFn: () =>
      api.createRun(projectId, {
        question,
        ...(query && { query }),
        screen: true,
        exhaustive: true,
      }),
    onSuccess: (created) => {
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
      router.push(`/r/${created.public_id}`);
    },
    onError: (error) =>
      toast.error(
        error instanceof Error ? error.message : "The search could not start.",
      ),
  });
  return (
    <div className="flex gap-3">
      <span className="grid size-10 shrink-0 place-items-center rounded-full border border-dashed border-moss/40 bg-accent/50 text-moss">
        <Telescope className="size-4" />
      </span>
      <div className="min-w-0 max-w-full rounded-2xl border border-moss/30 bg-accent/40 px-4 py-3">
        <p className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
          Proposed systematic search
        </p>
        <p className="mt-1 text-[0.875rem] font-medium leading-snug">{question}</p>
        {query && (
          <p className="mt-1 truncate font-mono text-[0.71875rem] text-muted-foreground">
            {query}
          </p>
        )}
        <div className="mt-2.5 flex items-center gap-2.5">
          <Button
            size="sm"
            className="h-7 rounded-full text-[0.75rem]"
            disabled={start.isPending}
            onClick={() => start.mutate()}
          >
            {start.isPending ? (
              <Loader2 className="size-3 animate-spin" />
            ) : (
              <Telescope className="size-3" />
            )}
            Start the search
          </Button>
          <span className="text-[0.6875rem] text-muted-foreground">
            Runs the full pipeline with screening across the selected scope.
          </span>
        </div>
      </div>
    </div>
  );
}

/** Compact thread handle for a table that lives in the split workspace. */
function TableArtifactCard({
  id,
  table,
}: {
  id: string;
  table: TableArtifact;
}) {
  const columns = table.columns ?? [];
  const rows = table.rows ?? [];

  function openTable() {
    const artifact: ResearchArtifact = {
      id,
      kind: "table",
      title: table.title,
      table,
    };
    window.dispatchEvent(
      new CustomEvent("six:open-table", { detail: artifact }),
    );
  }

  return (
    <div className="flex gap-3">
      <span className="grid size-10 shrink-0 place-items-center rounded-full border border-dashed border-moss/40 bg-accent/50 text-moss">
        <Table2 className="size-4" />
      </span>
      <button
        type="button"
        onClick={openTable}
        className="group/table flex min-w-0 flex-1 cursor-pointer items-center gap-3 rounded-2xl border border-border bg-card px-4 py-3 text-left transition-colors hover:border-moss/40"
      >
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[0.8125rem] font-medium text-foreground">
            {table.title || "Research table"}
          </span>
          <span className="mt-1 flex min-w-0 flex-wrap items-center gap-1.5">
            {columns.length > 0 ? (
              <span className="font-mono text-[0.59375rem] uppercase tracking-[0.14em] text-muted-foreground">
                {rows.length} × {columns.length}
              </span>
            ) : (
              <span className="font-mono text-[0.59375rem] uppercase tracking-[0.14em] text-muted-foreground">
                Interactive table
              </span>
            )}
            {columns.slice(0, 3).map((column) => (
              <span
                key={column}
                className="max-w-[9rem] truncate rounded-full bg-secondary px-2 py-0.5 text-[0.625rem] text-muted-foreground"
              >
                {column}
              </span>
            ))}
            {columns.length > 3 && (
              <span className="text-[0.625rem] text-muted-foreground">
                +{columns.length - 3}
              </span>
            )}
          </span>
        </span>
        <span className="shrink-0 text-[0.6875rem] font-medium text-moss transition-colors group-hover/table:text-foreground">
          Open table
        </span>
      </button>
    </div>
  );
}

/** A small evidence table stays readable in the conversation itself. */
function InlineTableArtifact({
  id,
  table,
  onPrompt,
}: {
  id: string;
  table: TableArtifact;
  onPrompt: (prompt: string) => void;
}) {
  const columns = table.columns ?? [];
  const rows = table.rows ?? [];

  function openTable() {
    const artifact: ResearchArtifact = {
      id,
      kind: "table",
      title: table.title,
      table,
    };
    window.dispatchEvent(new CustomEvent("six:open-table", { detail: artifact }));
  }

  return (
    <div className="flex min-w-0 gap-3">
      <span className="grid size-10 shrink-0 place-items-center rounded-full border border-dashed border-moss/40 bg-accent/50 text-moss">
        <Table2 className="size-4" />
      </span>
      <div className="min-w-0 flex-1 overflow-hidden rounded-2xl border border-border bg-card">
        <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
          <div className="min-w-0">
            <p className="truncate text-[0.8125rem] font-medium text-foreground">
              {table.title || "Research table"}
            </p>
            <p className="mt-0.5 font-mono text-[0.59375rem] uppercase tracking-[0.14em] text-muted-foreground">
              {rows.length} × {columns.length}
            </p>
          </div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-8 shrink-0 rounded-full px-3 text-[0.6875rem]"
            onClick={openTable}
          >
            Expand
            <ArrowUpRight className="ml-1 size-3" />
          </Button>
        </div>
        <div className="max-h-[22rem] overflow-auto">
          <table className="w-max min-w-full border-separate border-spacing-0 text-left text-[0.71875rem]">
            <thead>
              <tr>
                {columns.map((column, index) => (
                  <th
                    key={`${column}-${index}`}
                    className={cn(
                      "sticky top-0 z-10 min-w-[9rem] border-b border-r border-border bg-secondary px-3 py-2 font-mono text-[0.5625rem] uppercase tracking-[0.14em] text-muted-foreground last:border-r-0",
                      index === 0 && "left-0 z-20 min-w-[14rem]",
                    )}
                  >
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={rowIndex} className="group/row">
                  {columns.map((_, columnIndex) => {
                    const value = row[columnIndex]?.trim() || "Not reported";
                    return (
                      <td
                        key={columnIndex}
                        className={cn(
                          "max-w-[22rem] border-b border-r border-border/70 bg-card px-3 py-2 align-top leading-relaxed text-foreground/85 last:border-r-0",
                          columnIndex === 0 &&
                            "sticky left-0 z-[5] max-w-[18rem] bg-card font-medium text-foreground",
                        )}
                      >
                        {value.split(/(\[W\d+\]|\bW\d{4,}\b)/g).map((part, index) => {
                          const workId = part.replace(/[\[\]]/g, "");
                          if (!/^W\d+$/.test(workId)) {
                            return <span key={index}>{part}</span>;
                          }
                          return (
                            <button
                              key={`${workId}-${index}`}
                              type="button"
                              className="mr-1 inline-flex rounded-full border border-moss/25 bg-accent px-1.5 py-0.5 font-mono text-[0.5625rem] font-semibold text-moss hover:bg-moss-surface hover:text-ivory"
                              onClick={() =>
                                onPrompt(`Tell me more about ${workId} in this comparison.`)
                              }
                            >
                              {workId}
                            </button>
                          );
                        })}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

/** A faithful translation of a passage, original above its rendering. */
function TranslationCard({
  targetLanguage,
  original,
  translation,
}: {
  targetLanguage: string;
  original: string;
  translation: string;
}) {
  return (
    <div className="flex gap-3">
      <span className="grid size-10 shrink-0 place-items-center rounded-full border border-dashed border-moss/40 bg-accent/50 text-moss">
        <Languages className="size-4" />
      </span>
      <div className="min-w-0 flex-1 rounded-2xl border border-border bg-card px-4 py-3">
        <p className="mb-2 font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
          Translation → {targetLanguage}
        </p>
        {original && (
          <p className="mb-2 border-l-2 border-border pl-3 text-[0.78125rem] italic leading-relaxed text-muted-foreground">
            {original}
          </p>
        )}
        <p className="border-l-2 border-moss pl-3 text-[0.875rem] leading-relaxed text-foreground">
          {translation}
        </p>
      </div>
    </div>
  );
}

function ClaimVerificationCard({
  result,
  onOpen,
}: {
  result: NonNullable<ToolStepPayload["results"][number]>;
  onOpen?: () => void;
}) {
  const verdict = result.verdict ?? "insufficient";
  const evidence = result.evidence ?? [];
  const presentation = {
    supported: {
      label: "Supported",
      tone: "border-moss/35 bg-accent/45 text-moss",
    },
    contradicted: {
      label: "Contradicted",
      tone: "border-destructive/25 bg-destructive/5 text-destructive",
    },
    mixed: {
      label: "Mixed evidence",
      tone:
        "border-amber-200 bg-amber-50/70 text-amber-800 dark:border-amber-300/25 dark:bg-amber-300/10 dark:text-amber-200",
    },
    insufficient: {
      label: "Not enough evidence",
      tone: "border-border bg-secondary/60 text-foreground/75",
    },
  }[verdict];
  return (
    <div className="flex gap-3">
      <span className="grid size-10 shrink-0 place-items-center rounded-full border border-dashed border-moss/40 bg-accent/50 text-moss">
        <ShieldCheck className="size-4" />
      </span>
      <div className="min-w-0 flex-1 overflow-hidden rounded-2xl border border-border bg-card">
        <div className="border-b border-border px-4 py-3.5">
          <div className="flex flex-wrap items-center gap-2">
            <span className={cn("rounded-full border px-2.5 py-1 text-[0.6875rem] font-medium", presentation.tone)}>
              {presentation.label}
            </span>
            <span className="font-mono text-[0.59375rem] uppercase tracking-[0.17em] text-muted-foreground">
              {result.confidence ?? "low"} confidence
            </span>
            {onOpen ? (
              <Button
                variant="ghost"
                size="sm"
                className="ml-auto h-7 rounded-full px-2.5 text-[0.6875rem]"
                onClick={onOpen}
              >
                <PanelRightOpen className="size-3.5" />
                Open audit
              </Button>
            ) : null}
          </div>
          <p className="mt-2 text-[0.875rem] font-medium leading-relaxed text-foreground">
            {result.claim}
          </p>
          {result.rationale && (
            <p className="mt-1.5 text-[0.78125rem] leading-relaxed text-muted-foreground">
              {result.rationale}
            </p>
          )}
        </div>
        {evidence.length > 0 && (
          <div className="divide-y divide-border">
            {evidence.map((item) => (
              <a
                key={`${item.work_id}-${item.stance}`}
                href={scholarlyWorkUrl(item.work_id) ?? undefined}
                target="_blank"
                rel="noreferrer"
                className="flex gap-3 px-4 py-3 transition-colors hover:bg-secondary/55"
              >
                <Scale className="mt-0.5 size-3.5 shrink-0 text-moss" />
                <span className="min-w-0 flex-1">
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="truncate text-[0.78125rem] font-medium">{item.title}</span>
                    <span
                      className={cn(
                        "rounded-full px-1.5 py-0.5 font-mono text-[0.5625rem] uppercase tracking-[0.18em]",
                        item.stance === "supports"
                          ? "bg-accent text-moss"
                          : item.stance === "contradicts"
                            ? "bg-destructive/10 text-destructive"
                            : "bg-secondary text-muted-foreground",
                      )}
                    >
                      {item.stance}
                    </span>
                  </span>
                  <span className="mt-0.5 block text-[0.71875rem] leading-relaxed text-muted-foreground">
                    {item.reason}
                  </span>
                </span>
              </a>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

const TOOL_ICONS: Partial<
  Record<NonNullable<ToolStepPayload["tool"]>, typeof Globe>
> = {
  agent_update: ListChecks,
  show_chart: ChartColumn,
  clarify: MessageCircleQuestion,
  cite: Quote,
  show_paper: FileText,
  save_paper: BookMarked,
  add_document: Paperclip,
  web_search: Globe,
  read_webpage: Globe,
  citation_graph: Network,
  author_lookup: UserRound,
  search_in_document: FileSearch,
  search_library: Library,
  recall_history: History,
  export_works: Download,
  compare_papers: Columns2,
  extract_data: Table2,
  edit_table: Table2,
  edit_pdf_comment: Highlighter,
  translate_passage: Languages,
  start_search: Telescope,
  verify_claim: ShieldCheck,
  workspace_action: ArrowUpRight,
};

const PERSISTENT_TURN_OUTPUT_KINDS = new Set<
  NonNullable<ToolStepPayload["kind"]>
>([
  "ui",
  "paper",
  "export",
  "search_proposal",
  "runs",
  "table",
  "table_mutation",
  "annotation_mutation",
  "translation",
  "claim_verification",
  "workspace_action",
  "library_save",
]);

/**
 * Outputs that remain useful after a turn has finished. The operational step
 * still appears in the completed work disclosure, while the interactive
 * result itself is rendered once, below the assistant's final answer.
 */
function isPersistentTurnOutput(message: ChatMessage): boolean {
  const payload = (message.payload ?? null) as ToolStepPayload | null;
  if (
    !payload ||
    payload.status === "running" ||
    payload.status === "failed"
  ) {
    return false;
  }
  if (tableArtifactFromPayload(payload)) return true;
  return payload.kind
    ? PERSISTENT_TURN_OUTPUT_KINDS.has(payload.kind)
    : false;
}

/** An agentic tool step the assistant took, shown as an expandable chip. */
function ToolMessage({
  message,
  onPrompt,
  runId,
  historical = false,
}: {
  message: ChatMessage;
  onPrompt: (prompt: string) => void;
  runId: RunRef;
  /** Completed turns preserve started rows without leaving a live animation behind. */
  historical?: boolean;
}) {
  const payload = (message.payload ?? null) as ToolStepPayload | null;
  const [open, setOpen] = useState(false);
  const { me } = useAuth();
  const toolLanguage = me?.language === "de" ? "de" : "en";
  const displayTool = payload?.target_tool ?? payload?.tool;
  const preservesFailure = payload?.status === "failed";
  const displayContent = preservesFailure
    ? userFacingStoredErrorMessage(
        message.content,
        "Agent activity",
      )
    : safeAgentProgressText(message.content, "Agent activity");
  const displayReason = preservesFailure
    ? userFacingStoredErrorMessage(payload?.reason, "")
    : safeAgentProgressText(payload?.reason);
  const isWeb =
    displayTool === "web_search" || displayTool === "read_webpage";
  const Icon = (displayTool && TOOL_ICONS[displayTool]) || BookMarked;
  const results = payload?.results ?? [];

  if (payload?.kind === "agent_work" && payload.results?.[0]) {
      const update = payload.results[0];
      const items = Array.isArray(update.items)
        ? update.items
          .map((item) => safeAgentProgressText(item))
          .filter((item): item is string => Boolean(item))
      : [];
    const stage = String(update.stage ?? "checkpoint");
    const stageLabel =
      stage === "plan"
        ? "Plan"
        : stage === "synthesis"
          ? "Checkpoint"
          : stage === "complete"
            ? "Complete"
            : "Update";
    return (
      <div className="flex gap-3">
        <span className="grid size-10 shrink-0 place-items-center rounded-full border border-dashed border-moss/40 bg-accent/50 text-moss">
          <ListChecks className="size-4" />
        </span>
        <div className="min-w-0 flex-1 pt-0.5">
          <button
            type="button"
            aria-expanded={open}
            onClick={() => setOpen((value) => !value)}
            className="group/work inline-flex max-w-full items-center gap-2 rounded-full border border-border bg-secondary/60 py-1.5 pl-3 pr-2.5 text-left transition-colors hover:border-moss/40 hover:bg-secondary"
          >
            <span className="truncate text-[0.8125rem] font-medium text-foreground/85">
              {displayContent}
            </span>
            <span className="shrink-0 rounded-full bg-accent px-1.5 py-0.5 font-mono text-[0.59375rem] uppercase tracking-[0.12em] text-moss">
              {stageLabel}
            </span>
            <ChevronDown
              className={cn(
                "size-3.5 shrink-0 text-muted-foreground/60 transition-transform",
                open && "rotate-180",
              )}
            />
          </button>
          <AnimatePresence initial={false}>
            {open ? (
              <motion.div
                initial={{ opacity: 0, height: 0, y: -4 }}
                animate={{ opacity: 1, height: "auto", y: 0 }}
                exit={{ opacity: 0, height: 0, y: -4 }}
                transition={{ duration: 0.18, ease: "easeOut" }}
                className="overflow-hidden"
              >
                <div className="mt-2 max-w-[46rem] rounded-2xl border border-border bg-card px-4 py-3">
                  {displayReason ? (
                    <p className="text-[0.75rem] leading-relaxed text-muted-foreground">
                      {displayReason}
                    </p>
                  ) : null}
                  {items.length > 0 ? (
                    <ol className="mt-2 space-y-1.5">
                      {items.map((item, index) => (
                        <li
                          key={`${index}:${item}`}
                          className="flex gap-2 text-[0.75rem] leading-relaxed text-foreground/85"
                        >
                          <span className="mt-0.5 font-mono text-[0.625rem] text-moss">
                            {String(index + 1).padStart(2, "0")}
                          </span>
                          <span>{item}</span>
                        </li>
                      ))}
                    </ol>
                  ) : null}
                  {safeAgentProgressText(update.completion_reason) ? (
                    <p className="mt-2 border-t border-border pt-2 text-[0.71875rem] leading-relaxed text-muted-foreground">
                      {safeAgentProgressText(update.completion_reason)}
                    </p>
                  ) : null}
                </div>
              </motion.div>
            ) : null}
          </AnimatePresence>
        </div>
      </div>
    );
  }

  if (payload?.status === "running" && historical) {
    return (
      <AgentToolCallCard
        icon={Icon}
        title={displayContent}
        status={toolLanguage === "de" ? "Gestartet" : "Started"}
        state="neutral"
      />
    );
  }

  if (payload?.status === "running") {
    return (
      <AgentToolCallCard
        icon={Icon}
        title={displayContent}
        status={toolLanguage === "de" ? "Läuft" : "Working"}
        state="running"
        details={
          displayReason ? (
            <p className="text-[0.75rem] leading-relaxed text-muted-foreground">
              {displayReason}
            </p>
          ) : undefined
        }
      />
    );
  }

  if (
    (payload?.kind === "table_mutation" ||
      payload?.kind === "annotation_mutation") &&
    payload.results?.[0]
  ) {
    const result = payload.results[0];
    const failed = payload.status === "failed" || Boolean(result.error);
    const isTableMutation = payload.kind === "table_mutation";
    const changes = result.changes ?? [];
    return (
      <div className="flex gap-3">
        <span
          className={cn(
            "grid size-10 shrink-0 place-items-center rounded-full border",
            failed
              ? "border-destructive/30 bg-destructive/10 text-destructive"
              : "border-moss/30 bg-accent/55 text-moss",
          )}
        >
          {isTableMutation ? (
            <Table2 className="size-4" />
          ) : (
            <Highlighter className="size-4" />
          )}
        </span>
        <div className="flex min-w-0 max-w-full items-center gap-4 rounded-2xl border border-border bg-card px-4 py-3">
          <span className="min-w-0">
            <span className="block text-[0.8125rem] font-medium">
              {failed
                ? userFacingStoredErrorMessage(
                    result.error,
                    "The requested change was not applied",
                  )
                : isTableMutation
                  ? "Research table updated"
                  : `PDF comment ${result.operation ?? "updated"}`}
            </span>
            {!failed ? (
              <span className="mt-0.5 block font-mono text-[0.625rem] uppercase tracking-[0.16em] text-muted-foreground">
                {isTableMutation
                  ? changes.length > 0
                    ? changes.join(" · ")
                    : `${result.row_count ?? 0} rows · ${result.column_count ?? 0} columns`
                  : result.operation === "deleted"
                    ? `Page ${result.page ?? "—"} · removed from the reader`
                    : `Page ${result.page ?? "—"} · saved in the reader`}
              </span>
            ) : null}
          </span>
          {!failed && isTableMutation && result.message_id ? (
            <Button
              variant="outline"
              size="sm"
              className="h-8 shrink-0 rounded-full px-3 text-[0.6875rem]"
              onClick={() =>
                window.dispatchEvent(
                  new CustomEvent("six:open-artifact", {
                    detail: { id: `table:${result.message_id}` },
                  }),
                )
              }
            >
              <PanelRightOpen className="size-3.5" />
              Open table
            </Button>
          ) : null}
          {!failed && !isTableMutation && result.document_id ? (
            <Button
              variant="outline"
              size="sm"
              className="h-8 shrink-0 rounded-full px-3 text-[0.6875rem]"
              onClick={() =>
                window.dispatchEvent(
                  new CustomEvent("six:open-paper", {
                    detail: {
                      documentId: result.document_id,
                      title: result.title ?? "Paper",
                      highlights: [],
                    },
                  }),
                )
              }
            >
              <PanelRightOpen className="size-3.5" />
              Open reader
            </Button>
          ) : null}
        </div>
      </div>
    );
  }

  if (payload?.kind === "workspace_action") {
    const actions = (
      message.payload as { workspace_actions?: WorkspaceAction[] } | null
    )?.workspace_actions;
    return (
      <div className="max-w-[46rem] pl-[3.25rem]">
        <WorkspaceActionList actions={actions} />
      </div>
    );
  }

  const parsedTable = payload ? tableArtifactFromPayload(payload) : null;
  const tableArtifact = parsedTable
    ? { ...parsedTable, messageId: message.id }
    : null;
  if (tableArtifact) {
    const id = `table:${message.id ?? `${payload?.resource?.uri ?? payload?.query ?? "table"}:${message.created_at}`}`;
    if (!isSubstantialTable(tableArtifact)) {
      return (
        <InlineTableArtifact
          id={id}
          table={tableArtifact}
          onPrompt={onPrompt}
        />
      );
    }
    return (
      <TableArtifactCard
        id={id}
        table={tableArtifact}
      />
    );
  }

  // the paper reader: a card that (re)opens the split view next to the chat
  if (payload?.kind === "paper" && payload.document_id) {
    const count = payload.highlights?.length ?? 0;
    return (
      <div className="flex gap-3">
        <span className="grid size-10 shrink-0 place-items-center rounded-full border border-dashed border-moss/40 bg-accent/50 text-moss">
          <FileText className="size-4" />
        </span>
        <button
          type="button"
          onClick={() =>
            window.dispatchEvent(
              new CustomEvent("six:open-paper", {
                detail: {
                  documentId: payload.document_id,
                  title: payload.title ?? "Paper",
                  highlights: payload.highlights ?? [],
                  legalBasis: payload.legal_basis,
                  license: payload.license,
                },
              }),
            )
          }
          className="group/paper flex min-w-0 max-w-full cursor-pointer items-center gap-3 rounded-2xl border border-border bg-card px-4 py-3 text-left transition-colors hover:border-moss/40"
        >
          <span className="min-w-0">
            <span className="block truncate text-[0.8125rem] font-medium">
              {payload.title ?? "Paper"}
            </span>
            <span className="mt-0.5 flex items-center gap-2 font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
              {payload.page_count ? <span>{payload.page_count} pages</span> : null}
              {count > 0 && (
                <span className="flex items-center gap-1 text-moss">
                  <Highlighter className="size-2.5" />
                  {count} highlight{count === 1 ? "" : "s"}
                </span>
              )}
              <span className="text-moss/80 transition-colors group-hover/paper:text-moss">
                Open reader
              </span>
            </span>
          </span>
        </button>
      </div>
    );
  }

  if (payload?.kind === "library_save" && payload.document_id) {
    return (
      <div className="flex gap-3">
        <span className="grid size-10 shrink-0 place-items-center rounded-full border border-moss/30 bg-accent/55 text-moss">
          <BookMarked className="size-4" />
        </span>
        <div className="flex min-w-0 max-w-full items-center gap-4 rounded-2xl border border-border bg-card px-4 py-3">
          <span className="min-w-0">
            <span className="block truncate text-[0.8125rem] font-medium">
              {payload.title ?? "Paper"}
            </span>
            <span className="mt-0.5 block font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
              Saved to your Library
            </span>
          </span>
          <a
            href="/library"
            className="shrink-0 rounded-full border border-border px-3 py-1.5 text-[0.6875rem] font-medium text-moss transition-colors hover:border-moss/40 hover:bg-accent"
          >
            Open Library
          </a>
        </div>
      </div>
    );
  }

  // export download card: the file is fetched on click, never pasted
  if (payload?.kind === "export" && payload.results?.[0]) {
    return (
      <ExportCard
        runId={runId}
        format={String(payload.results[0].format ?? "bibtex")}
        count={Number(payload.results[0].count ?? 0)}
        includedOnly={Boolean(payload.results[0].included_only)}
      />
    );
  }

  // translation card: original passage beside its faithful translation
  if (payload?.kind === "translation" && payload.results?.[0]) {
    return (
      <TranslationCard
        targetLanguage={String(payload.results[0].target_language ?? "")}
        original={String(payload.results[0].original ?? "")}
        translation={String(payload.results[0].translation ?? "")}
      />
    );
  }

  if (payload?.kind === "claim_verification" && payload.results?.[0]) {
    const id = `claim:${String(payload.results[0].claim ?? "").toLocaleLowerCase()}`;
    return (
      <ClaimVerificationCard
        result={payload.results[0]}
        onOpen={() =>
          window.dispatchEvent(
            new CustomEvent("six:open-artifact", { detail: { id } }),
          )
        }
      />
    );
  }

  // systematic-search proposal: starts ONLY on the user's click
  if (payload?.kind === "search_proposal" && payload.results?.[0]) {
    return (
      <SearchProposalCard
        question={String(payload.results[0].question ?? "")}
        query={String(payload.results[0].query ?? "")}
        projectId={payload.project_id ?? null}
      />
    );
  }

  // recalled searches: clickable run cards
  if (payload?.kind === "runs") {
    return (
      <div className="flex gap-3">
        <span className="grid size-10 shrink-0 place-items-center rounded-full border border-dashed border-moss/40 bg-accent/50 text-moss">
          <History className="size-4" />
        </span>
        <div className="min-w-0 flex-1 space-y-1.5 pt-0.5">
          <p className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
            {displayContent}
          </p>
          {results.length === 0 ? (
            <p className="text-[0.78125rem] text-muted-foreground">
              No earlier searches match.
            </p>
          ) : (
            results.map((entry) => (
              <a
                key={String(entry.public_id)}
                href={`/r/${entry.public_id}`}
                className="flex min-w-0 items-center gap-2.5 rounded-xl border border-border bg-card px-3 py-2 transition-colors hover:border-moss/40"
              >
                <Telescope className="size-3.5 shrink-0 text-moss" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[0.8125rem]">
                    {entry.title || entry.question}
                  </span>
                  <span className="block font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
                    {entry.mode === "ask" ? "quick answer" : "search"} · {entry.status}
                    {typeof entry.included === "number" ? ` · ${entry.included} included` : ""}
                  </span>
                </span>
              </a>
            ))
          )}
        </div>
      </div>
    );
  }

  // interactive MCP-UI step: render the resource itself, not a results list
  if (payload?.kind === "ui" && payload.resource) {
    return (
      <div className="flex gap-3">
        <span className="grid size-10 shrink-0 place-items-center rounded-full border border-dashed border-moss/40 bg-accent/50 text-moss">
          <Icon className="size-4" />
        </span>
        <div className="min-w-0 flex-1 pt-0.5">
          <p className="mb-1.5 font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
            {displayContent}
          </p>
          <UiResourceFrame resource={payload.resource} onPrompt={onPrompt} />
        </div>
      </div>
    );
  }

  return (
    <div className="flex gap-3">
      <span className="grid size-10 shrink-0 place-items-center rounded-full border border-dashed border-moss/40 bg-accent/50 text-moss">
        <Icon className="size-4" />
      </span>
      <div className="min-w-0 flex-1 pt-1">
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          className="inline-flex max-w-full cursor-pointer items-center gap-2 rounded-full border border-border bg-secondary/60 py-1.5 pl-3 pr-2.5 text-left transition-colors hover:border-moss/40 hover:bg-secondary"
        >
          <span className="truncate text-[0.8125rem] font-medium text-foreground/85">
            {displayContent}
          </span>
          <span
            className={cn(
              "shrink-0 rounded-full px-1.5 py-0.5 font-mono text-[0.59375rem]",
              payload?.status === "failed"
                ? "bg-destructive/10 text-destructive"
                : "bg-accent text-moss",
            )}
          >
            {payload?.status === "failed"
              ? "Not completed"
              : `${results.length} result${results.length === 1 ? "" : "s"}`}
          </span>
          <ChevronDown
            className={cn(
              "size-3.5 shrink-0 text-muted-foreground/60 transition-transform",
              open && "rotate-180",
            )}
          />
        </button>

        {open && results.length > 0 && (
          <div className="mt-2 space-y-2 rounded-2xl border border-border bg-card p-3.5">
            <div className="flex items-start gap-2">
              {displayReason ? (
                <p className="min-w-0 flex-1 text-[0.71875rem] italic text-muted-foreground">
                  {displayReason}
                </p>
              ) : (
                <span className="flex-1" />
              )}
              {displayTool === "read_webpage" && results[0]?.excerpt ? (
                <Button
                  variant="outline"
                  size="sm"
                  className="h-7 shrink-0 rounded-full px-2.5 text-[0.6875rem]"
                  onClick={() =>
                    window.dispatchEvent(
                      new CustomEvent("six:open-artifact", {
                        detail: {
                          id: `source:${String(results[0].url)}`,
                        },
                      }),
                    )
                  }
                >
                  <PanelRightOpen className="size-3.5" />
                  Read source
                </Button>
              ) : null}
            </div>
            {results.slice(0, 4).map((result, index) => {
              const href = safeExternalHttpUrl(
                isWeb
                  ? result.url
                  : (result.publisher_url ??
                    scholarlyWorkUrl(result.id ?? "", result.doi) ??
                    undefined),
              );
              const meta = isWeb
                ? result.domain
                : [result.year, result.venue].filter(Boolean).join(" · ");
              return (
                <a
                  key={index}
                  href={href || undefined}
                  target="_blank"
                  rel="noreferrer"
                  aria-disabled={!href}
                  className={cn(
                    "block rounded-lg px-2 py-1.5 transition-colors",
                    href ? "hover:bg-secondary/70" : "pointer-events-none",
                  )}
                >
                  <p className="truncate text-[0.8125rem] font-medium text-foreground">
                    {result.title || result.url}
                  </p>
                  <p className="mt-0.5 flex items-center gap-2 font-mono text-[0.65625rem] text-muted-foreground">
                    {meta}
                    {isWeb && result.category ? (
                      <span className="rounded-full bg-secondary px-1.5 py-0.5 text-[0.5625rem] uppercase tracking-[0.1em]">
                        {result.category}
                      </span>
                    ) : null}
                    {!isWeb && typeof result.cited_by_count === "number" && (
                      <span>{result.cited_by_count} citations</span>
                    )}
                  </p>
                  {isWeb && (result.snippet || result.excerpt) && (
                    <p className="mt-0.5 line-clamp-2 text-[0.71875rem] leading-relaxed text-muted-foreground">
                      {result.snippet || result.excerpt}
                    </p>
                  )}
                  {displayTool === "read_webpage" && result.characters_read ? (
                    <>
                      <p className="mt-1 font-mono text-[0.5625rem] uppercase tracking-[0.12em] text-muted-foreground/70">
                        {result.word_count
                          ? `${result.word_count.toLocaleString()} words`
                          : `${result.characters_read.toLocaleString()} characters`}
                        {result.page_count ? ` · ${result.page_count} pages` : ""}
                        {" · read in full"}
                      </p>
                      {result.headings && result.headings.length > 0 ? (
                        <p className="mt-1 line-clamp-1 text-[0.65625rem] text-muted-foreground/80">
                          Sections: {result.headings.slice(0, 3).join(" · ")}
                        </p>
                      ) : null}
                    </>
                  ) : null}
                  {result.error && (
                    <p className="mt-0.5 text-[0.71875rem] leading-relaxed text-amber-800">
                      {safeAgentDisplayText(
                        result.error,
                        "This source result needs attention.",
                      )}
                      {result.publisher_url && " · publisher link above"}
                    </p>
                  )}
                </a>
              );
            })}
            {results.length > 4 ? (
              <p className="px-2 pt-1 font-mono text-[0.5625rem] uppercase tracking-[0.12em] text-muted-foreground">
                + {results.length - 4} more result{results.length - 4 === 1 ? "" : "s"}
              </p>
            ) : null}
          </div>
        )}
      </div>
    </div>
  );
}

type CompletedAgentTurn = {
  assistantIndex: number;
  firstToolIndex: number;
  tools: Array<{ index: number; message: ChatMessage }>;
  outputs: ChatMessage[];
  terminalStatus: "completed" | "failed" | "cancelled";
};

/**
 * A persisted assistant row is the durable completion receipt for a turn.
 * Until that row exists, every tool row remains in the live chronological
 * timeline. Once it exists, the same rows can safely move into one collapsed
 * history disclosure without guessing from transient client state.
 */
function collectCompletedAgentTurns(
  messages: ChatMessage[],
): CompletedAgentTurn[] {
  const turns: CompletedAgentTurn[] = [];
  let segmentStart = 0;

  const messageTurnId = (message: ChatMessage) => {
    const value = (message.payload as { turn_id?: unknown } | null)?.turn_id;
    return typeof value === "string" ? value.trim() : "";
  };

  for (let assistantIndex = 0; assistantIndex < messages.length; assistantIndex += 1) {
    if (messages[assistantIndex].role !== "assistant") continue;
    const assistantTurnId = messageTurnId(messages[assistantIndex]);
    const tools = messages
      .slice(segmentStart, assistantIndex)
      .map((message, offset) => ({
        index: segmentStart + offset,
        message,
      }))
      .filter((entry) => {
        if (entry.message.role !== "tool") return false;
        const toolTurnId = messageTurnId(entry.message);
        // Durable turns are correlated by their authoritative server id. The
        // positional fallback is reserved for fully legacy, unscoped rows so
        // tools from an earlier failed turn can never be folded into a later
        // successful answer.
        return assistantTurnId
          ? toolTurnId === assistantTurnId
          : toolTurnId === "";
      });
    if (tools.length > 0) {
      const assistantPayload = messages[assistantIndex].payload as {
        kind?: unknown;
        status?: unknown;
        cancelled?: unknown;
      } | null;
      const terminalStatus =
        assistantPayload?.cancelled === true || assistantPayload?.status === "cancelled"
          ? "cancelled"
          : assistantPayload?.kind === "turn_receipt" &&
              assistantPayload.status === "failed"
            ? "failed"
          : "completed";
      turns.push({
        assistantIndex,
        firstToolIndex: tools[0].index,
        tools,
        outputs: tools
          .map((entry) => entry.message)
          .filter(isPersistentTurnOutput),
        terminalStatus,
      });
    }
    segmentStart = assistantIndex + 1;
  }
  return turns;
}

function CompletedOutputStep({ message }: { message: ChatMessage }) {
  const payload = (message.payload ?? null) as ToolStepPayload | null;
  const Icon = (payload && TOOL_ICONS[payload.tool]) || ArrowUpRight;
  return (
    <div className="flex gap-3">
      <span className="grid size-10 shrink-0 place-items-center rounded-full border border-dashed border-moss/30 bg-accent/40 text-moss">
        <Icon className="size-4" />
      </span>
      <div className="flex min-w-0 flex-1 items-center gap-2 self-center">
        <span className="truncate text-[0.8125rem] font-medium text-foreground/80">
          {message.content}
        </span>
        <BadgeCheck className="size-3.5 shrink-0 text-moss" />
      </div>
    </div>
  );
}

function CompletedAgentWorkDisclosure({
  turn,
  onPrompt,
  runId,
}: {
  turn: CompletedAgentTurn;
  onPrompt: (prompt: string) => void;
  runId: RunRef;
}) {
  const [open, setOpen] = useState(turn.terminalStatus === "failed");
  const { me } = useAuth();
  const language = me?.language ?? "en";
  const failed = turn.terminalStatus === "failed";
  const cancelled = turn.terminalStatus === "cancelled";
  const statusLabel = failed
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
  const StatusIcon = failed ? TriangleAlert : cancelled ? Square : BadgeCheck;

  return (
    <div className="flex gap-3" data-agent-work-disclosure>
      <span
        className={cn(
          "grid size-10 shrink-0 place-items-center rounded-full border",
          failed
            ? "border-destructive/30 bg-destructive/5 text-destructive"
            : cancelled
              ? "border-border bg-secondary/60 text-muted-foreground"
              : "border-moss/25 bg-accent/45 text-moss",
        )}
      >
        <StatusIcon className="size-4" />
      </span>
      <div className="min-w-0 flex-1 pt-0.5">
        <button
          type="button"
          aria-expanded={open}
          aria-label={
            language === "de"
              ? `Agent-Arbeit · ${statusLabel}`
              : `Agent work · ${statusLabel}`
          }
          onClick={() => setOpen((current) => !current)}
          className="inline-flex max-w-full items-center gap-2 rounded-full border border-border bg-secondary/60 py-1.5 pl-3 pr-2.5 text-left transition-colors hover:border-moss/40 hover:bg-secondary"
        >
          <span className="truncate text-[0.8125rem] font-medium text-foreground/85">
            {language === "de" ? "Agent-Arbeit" : "Agent work"}
          </span>
          <span aria-hidden="true" className="text-muted-foreground/55">
            ·
          </span>
          <span
            className={cn(
              "shrink-0 rounded-full px-1.5 py-0.5 font-mono text-[0.59375rem] uppercase tracking-[0.12em]",
              failed
                ? "bg-destructive/10 text-destructive"
                : cancelled
                  ? "bg-secondary text-muted-foreground"
                  : "bg-accent text-moss",
            )}
          >
            {statusLabel}
          </span>
          <ChevronDown
            className={cn(
              "size-3.5 shrink-0 text-muted-foreground/60 transition-transform",
              open && "rotate-180",
            )}
          />
        </button>
        <AnimatePresence initial={false}>
          {open ? (
            <motion.div
              initial={{ opacity: 0, height: 0, y: -4 }}
              animate={{ opacity: 1, height: "auto", y: 0 }}
              exit={{ opacity: 0, height: 0, y: -4 }}
              transition={{ duration: 0.18, ease: "easeOut" }}
              className="overflow-hidden"
            >
              <div className="mt-3 max-w-[46rem] space-y-4 rounded-2xl border border-border bg-card/55 p-3.5">
                {turn.tools.map(({ index, message }, toolIndex) => (
                  <div
                    key={message.id ?? `${index}-${message.created_at}`}
                    data-quick-agent-history-row
                    className="relative min-w-0"
                  >
                    {turn.tools.length > 1 ? (
                      <AgentTimelineRail
                        start={toolIndex === 0 ? "node" : "gap"}
                        end={toolIndex === turn.tools.length - 1 ? "node" : "gap"}
                        className="bg-border/75"
                      />
                    ) : null}
                    <div className="relative z-10">
                      {isPersistentTurnOutput(message) ? (
                        <CompletedOutputStep message={message} />
                      ) : (
                        <ToolMessage
                          message={message}
                          onPrompt={onPrompt}
                          runId={runId}
                          historical
                        />
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </motion.div>
          ) : null}
        </AnimatePresence>
      </div>
    </div>
  );
}

function CompletedTurnOutputs({
  messages,
  onPrompt,
  runId,
}: {
  messages: ChatMessage[];
  onPrompt: (prompt: string) => void;
  runId: RunRef;
}) {
  if (messages.length === 0) return null;
  return (
    <div className="mt-4 space-y-3" data-completed-turn-outputs>
      {messages.map((message, index) => (
        <ToolMessage
          key={message.id ?? `${index}-${message.created_at}`}
          message={message}
          onPrompt={onPrompt}
          runId={runId}
          historical
        />
      ))}
    </div>
  );
}

function ClaimSummary({ checked, flagged }: { checked: number; flagged: number }) {
  if (checked === 0) return null;
  const allSupported = flagged === 0;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          className={cn(
            "mt-2 inline-flex cursor-default items-center gap-1.5 rounded-full px-2.5 py-1 text-[0.6875rem] font-medium",
            allSupported
              ? "bg-accent text-moss"
              : "bg-amber-50 text-amber-800 dark:bg-amber-300/10 dark:text-amber-200",
          )}
        >
          {allSupported ? (
            <BadgeCheck className="size-3.5" />
          ) : (
            <TriangleAlert className="size-3.5" />
          )}
          {allSupported
            ? `All ${checked} cited claims verified against the sources`
            : `${flagged} of ${checked} cited claims lack clear support`}
        </span>
      </TooltipTrigger>
      <TooltipContent side="bottom" className="max-w-[20rem]">
        Every cited sentence is checked for entailment against the source: the
        full text when it was acquired, else the abstract. Sentences that did
        not verify are tinted amber in the answer.
      </TooltipContent>
    </Tooltip>
  );
}

/** The persisted claim-verification result on an assistant message. */
type ClaimsPayload = {
  checked: number;
  flagged: { claim: string; support: string }[];
};

/** Sentences whose cited claim failed verification get a soft amber wash,
 * so the shaky spots are visible in place, not just counted in a badge. */
function ClaimAwareText({
  text,
  flagged,
  ...cited
}: {
  text: string;
  flagged?: string[];
  numbering: Map<string, number>;
  titles: Map<string, string>;
  webLinks?: Map<string, string>;
  docs?: Map<string, { documentId: number; title?: string }>;
}) {
  const segments = useMemo(() => {
    if (!flagged || flagged.length === 0) {
      return [{ text, flagged: false }];
    }
    const ranges: Array<[number, number]> = [];
    for (const claim of flagged) {
      const needle = claim.trim();
      if (needle.length < 8) continue;
      const at = text.indexOf(needle);
      if (at >= 0) ranges.push([at, at + needle.length]);
    }
    if (ranges.length === 0) return [{ text, flagged: false }];
    ranges.sort((a, b) => a[0] - b[0]);
    const merged: Array<[number, number]> = [];
    for (const range of ranges) {
      const last = merged[merged.length - 1];
      if (last && range[0] <= last[1]) last[1] = Math.max(last[1], range[1]);
      else merged.push([range[0], range[1]]);
    }
    const out: Array<{ text: string; flagged: boolean }> = [];
    let cursor = 0;
    for (const [start, end] of merged) {
      if (start > cursor) out.push({ text: text.slice(cursor, start), flagged: false });
      out.push({ text: text.slice(start, end), flagged: true });
      cursor = end;
    }
    if (cursor < text.length) out.push({ text: text.slice(cursor), flagged: false });
    return out;
  }, [text, flagged]);

  return (
    <>
      {segments.map((segment, index) =>
        segment.flagged ? (
          <Tooltip key={index}>
            <TooltipTrigger asChild>
              <mark className="rounded-[3px] bg-amber-100/80 box-decoration-clone px-0.5 text-inherit underline decoration-amber-500/70 decoration-dotted underline-offset-[3px]">
                <CitedText text={segment.text} {...cited} />
              </mark>
            </TooltipTrigger>
            <TooltipContent side="top" className="max-w-[18rem]">
              This claim could not be clearly verified against its cited
              source. Read it with care.
            </TooltipContent>
          </Tooltip>
        ) : (
          <CitedText key={index} text={segment.text} {...cited} />
        ),
      )}
    </>
  );
}

export interface AskActivityPlan {
  academic_search?: boolean;
  web_search?: boolean;
  show_paper?: boolean;
  chart?: boolean;
  cite?: boolean;
}

function compactActivityText(value: string, fallback: string): string {
  const text = value.replace(/\s+/g, " ").trim() || fallback;
  return text.length > 78 ? `${text.slice(0, 77).trimEnd()}…` : text;
}

function visibleStreamingText(value: string): string {
  const marker = value.search(/\n\s*SOURCES:\s*/i);
  return (marker >= 0 ? value.slice(0, marker) : value).trimEnd();
}

/** A truthful live label derived from the active question, route and latest tool. */
function agentActivityLabel({
  question,
  messages,
  activeStartIndex,
  plan,
}: {
  question: string;
  messages: ChatMessage[];
  activeStartIndex: number;
  plan?: AskActivityPlan;
}): string {
  const turnMessages = messages.slice(Math.max(0, activeStartIndex + 1));
  const latestTool = [...turnMessages]
    .reverse()
    .find((message) => message.role === "tool");
  const payload = latestTool?.payload as ToolStepPayload | null | undefined;
  const query = compactActivityText(payload?.query ?? "", question);

  switch (payload?.target_tool ?? payload?.tool) {
    case "web_search":
      return `Checking the retrieved web sources for “${query}”…`;
    case "read_webpage":
      return "Reading the relevant web pages and reconciling their claims…";
    case "find_papers":
      return `Reviewing the academic matches for “${query}”…`;
    case "read_paper":
    case "show_paper":
    case "search_in_document":
      return "Reading the source passages that matter for this question…";
    case "search_library":
      return "Checking the papers stored in your Library…";
    case "compare_papers":
      return "Comparing the evidence across the selected papers…";
    case "extract_data":
      return "Structuring the extracted evidence into a usable answer…";
    case "edit_table":
      return "Applying the requested changes to the saved table…";
    case "edit_pdf_comment":
      return "Updating the saved PDF comments…";
    case "show_chart":
      return "Turning the findings into a clear visual summary…";
    case "citation_graph":
    case "author_lookup":
      return "Following the research network and checking the strongest leads…";
    case "cite":
    case "export_works":
      return "Checking the source metadata and preparing the citation…";
    case "translate_passage":
      return "Translating the passage while preserving its meaning…";
    case "verify_claim":
      return `Checking evidence for “${query}”…`;
    case "recall_history":
      return "Connecting this question to your earlier research…";
    case "start_search":
      return "Shaping the question into a documented search plan…";
    case "clarify":
    case "suggest_followups":
      return "Preparing the most useful next step…";
    case "add_document":
      return "Adding the source and checking what it contributes…";
    default:
      break;
  }

  const compactQuestion = compactActivityText(question, "your question");
  if (plan?.show_paper) {
    return `Locating the relevant passages for “${compactQuestion}”…`;
  }
  if (plan?.web_search) {
    return `Searching current web sources for “${compactQuestion}”…`;
  }
  if (plan?.academic_search) {
    return `Finding relevant research for “${compactQuestion}”…`;
  }
  if (plan) {
    return `Building a clear answer to “${compactQuestion}”…`;
  }
  return `Understanding what “${compactQuestion}” needs…`;
}

/** The message thread (rendered inside the scroll area). */
export function ChatThread({
  runId,
  chat,
  heading = "Ask the results",
  askMode = false,
  initialQuestion = "",
  working = false,
  activityPlan,
}: {
  runId: RunRef;
  chat: RunChat;
  /** Divider label above the thread; null renders the thread without one. */
  heading?: string | null;
  /** Quick answers can suggest promoting the question to a full review. */
  askMode?: boolean;
  /** The run question precedes seeded or initial answers without a chat row. */
  initialQuestion?: string;
  /** Initial quick answers start in the background before a chat mutation exists. */
  working?: boolean;
  /** The committed ask router decision keeps the live label honest before tools land. */
  activityPlan?: AskActivityPlan;
}) {
  const { me } = useAuth();
  const { data: worksPage } = useWorks(runId, false);
  const endRef = useRef<HTMLDivElement>(null);
  const [activityElapsedSeconds, setActivityElapsedSeconds] = useState(0);
  const {
    messages,
    pendingQuestion,
    lastAnswer,
    streamingText,
    streamingReasoning,
    liveActivity,
    reconnecting,
    activeTurnId,
  } = chat;
  const completedHistory = useMemo(() => {
    const byToolIndex = new Map<number, CompletedAgentTurn>();
    const byAssistantIndex = new Map<number, CompletedAgentTurn>();
    for (const turn of collectCompletedAgentTurns(messages)) {
      byAssistantIndex.set(turn.assistantIndex, turn);
      for (const { index } of turn.tools) byToolIndex.set(index, turn);
    }
    return { byToolIndex, byAssistantIndex };
  }, [messages]);
  const settledToolStartIds = useMemo(
    () =>
      new Set(
        messages.flatMap((message) => {
          const startedMessageId = (
            message.payload as ToolStepPayload | null
          )?.started_message_id;
          return typeof startedMessageId === "number" ? [startedMessageId] : [];
        }),
      ),
    [messages],
  );
  const pendingUserIndex = useMemo(() => {
    if (!pendingQuestion && !activeTurnId) return -1;
    let userIndex = -1;
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const message = messages[index];
      const messageId = (message.payload as { turn_id?: unknown } | null)?.turn_id;
      if (
        message.role === "user" &&
        (activeTurnId ? messageId === activeTurnId : message.content === pendingQuestion)
      ) {
        userIndex = index;
        break;
      }
    }
    return userIndex;
  }, [activeTurnId, messages, pendingQuestion]);
  const activeStartIndex = pendingQuestion || reconnecting
    ? pendingUserIndex >= 0
      ? pendingUserIndex
      : messages.length
    : working
      ? -1
      : messages.length;
  const activeMessages = messages.slice(activeStartIndex + 1);
  const activeToolCount = activeMessages.filter(
    (message) => message.role === "tool",
  ).length;
  const activeTurnHasAnswer = activeMessages.some(
    (message) => message.role === "assistant",
  );
  const activeToolRunning = activeMessages.some(
    (message) =>
      message.role === "tool" &&
      (message.payload as ToolStepPayload | null)?.status === "running" &&
      (typeof message.id !== "number" || !settledToolStartIds.has(message.id)),
  );
  const visibleDraft = visibleStreamingText(streamingText);
  const visibleReasoning = streamingReasoning.trim();
  const showActivity =
    (working || Boolean(pendingQuestion) || reconnecting) &&
    !activeTurnHasAnswer &&
    !activeToolRunning &&
    !visibleDraft &&
    !visibleReasoning;
  useEffect(() => {
    if (!working && !pendingQuestion && !reconnecting) {
      setActivityElapsedSeconds(0);
      return;
    }
    const startedAt = Date.now();
    const updateElapsed = () => {
      setActivityElapsedSeconds(Math.floor((Date.now() - startedAt) / 1_000));
    };
    updateElapsed();
    const interval = window.setInterval(updateElapsed, 1_000);
    return () => window.clearInterval(interval);
  }, [pendingQuestion, reconnecting, working]);
  const activityQuestion = pendingQuestion ?? initialQuestion;
  const activityLabel =
    liveActivity?.label ??
    agentActivityLabel({
      question: activityQuestion,
      messages,
      activeStartIndex,
      plan: pendingQuestion ? undefined : activityPlan,
    });

  const titles = useMemo(
    () => new Map((worksPage?.works ?? []).map((work) => [work.id, work.title])),
    [worksPage],
  );

  // stable numbering across the whole thread, in order of first appearance
  const numbering = useMemo(() => {
    const map = new Map<string, number>();
    for (const message of messages) {
      if (message.role === "tool") continue;
      for (const match of message.content.matchAll(CITATION_PATTERN)) {
        if (!LOOKS_LIKE_WORKS.test(match[1])) continue;
        for (const rawId of match[1].match(WORK_ID) ?? []) {
          const id = normalizeScholarlyWorkId(rawId);
          if (!map.has(id)) map.set(id, map.size + 1);
        }
      }
    }
    return map;
  }, [messages]);

  // Exact citation keys resolve one page. Legacy domain-only citations remain
  // clickable only when all observed results agree on that single URL.
  const webLinks = useMemo(() => {
    const map = new Map<string, string>();
    const candidates = new Map<string, Set<string>>();
    for (const message of messages) {
      if (message.role !== "tool") continue;
      const payload = message.payload as ToolStepPayload | null;
      if (!payload || !["web_search", "read_webpage"].includes(payload.tool) || payload.status === "failed") continue;
      for (const result of payload?.results ?? []) {
        if (result.error) continue;
        const href = safeExternalHttpUrl(result.url);
        if (!href) continue;
        const domain = new URL(href).hostname.replace(/^www\./, "");
        const keys = [domain];
        if (result.citation_key && LOOKS_LIKE_WEB_SOURCE.test(result.citation_key)) {
          keys.push(result.citation_key.toLowerCase());
        }
        for (const key of keys) {
          const urls = candidates.get(key) ?? new Set<string>();
          urls.add(href);
          candidates.set(key, urls);
        }
      }
    }
    for (const [key, urls] of candidates) {
      if (urls.size === 1) map.set(key, [...urls][0]);
    }
    return map;
  }, [messages]);

  // works whose PDF lives in this run (uploads, reader panels): their
  // citation chips open the in-app reader instead of a dead external link
  const docByWork = useMemo(() => {
    const map = new Map<string, { documentId: number; title?: string }>();
    for (const message of messages) {
      if (message.role !== "tool") continue;
      const payload = message.payload as ToolStepPayload | null;
      const workId = payload?.results?.[0]?.id;
      if (payload?.document_id && typeof workId === "string" && !map.has(workId)) {
        map.set(workId, {
          documentId: payload.document_id,
          title: payload.title ?? (payload.results?.[0]?.title as string | undefined),
        });
      }
    }
    return map;
  }, [messages]);

  // the fresh answer's claim chip belongs to the last assistant row
  const lastAssistantIndex = useMemo(() => {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      if (messages[index].role === "assistant") return index;
    }
    return -1;
  }, [messages]);

  const lastAssistantQuestion = useMemo(() => {
    if (lastAssistantIndex < 0) return "";
    for (let index = lastAssistantIndex - 1; index >= 0; index -= 1) {
      if (messages[index].role === "user") return messages[index].content;
    }
    return initialQuestion;
  }, [initialQuestion, lastAssistantIndex, messages]);

  const followUps = useMemo(() => {
    const message = messages[lastAssistantIndex];
    const evidence = (
      message?.payload as { evidence?: EvidenceRef[] } | null
    )?.evidence ?? [];
    const evidenceSourceCount = new Set(
      evidence.map((reference) => reference.work_id).filter(Boolean),
    ).size;
    return buildRunFollowUps({
      question: lastAssistantQuestion,
      answer: message?.content ?? "",
      askMode,
      paperCount: worksPage?.works.length ?? 0,
      evidencePassageCount: evidence.length,
      evidenceSourceCount,
    });
  }, [askMode, lastAssistantIndex, lastAssistantQuestion, messages, worksPage]);

  // a new question or answer always scrolls the thread into view
  useEffect(() => {
    endRef.current?.scrollIntoView({
      behavior: streamingText ? "auto" : "smooth",
      block: "end",
    });
  }, [messages.length, pendingQuestion, reconnecting, streamingText]);

  if (
    messages.length === 0 &&
    !pendingQuestion &&
    !working &&
    !reconnecting &&
    !visibleDraft
  ) {
    return null;
  }

  return (
    <div>
      {heading !== null && (
        <div className="mb-2 mt-8 flex items-center gap-3">
          <div className="h-px flex-1 bg-border" />
          <span className="font-mono text-[0.625rem] uppercase tracking-[0.22em] text-muted-foreground">
            {heading}
          </span>
          <div className="h-px flex-1 bg-border" />
        </div>
      )}

      <div className="space-y-5 py-3">
        {(working || pendingQuestion || reconnecting) && !activeTurnHasAnswer ? (
          <div
            data-chat-turn-elapsed
            className="flex justify-end font-mono text-[0.5625rem] uppercase tracking-[0.14em] text-muted-foreground"
            aria-label={`Elapsed ${Math.floor(activityElapsedSeconds / 60)} minutes ${activityElapsedSeconds % 60} seconds`}
          >
            {me?.language === "de" ? "Laufzeit" : "Elapsed"}{" "}
            {String(Math.floor(activityElapsedSeconds / 60)).padStart(2, "0")}:
            {String(activityElapsedSeconds % 60).padStart(2, "0")}
          </div>
        ) : null}
        <AnimatePresence initial={false}>
          {messages.map((message, index) => {
            const completedTurn =
              message.role === "tool"
                ? completedHistory.byToolIndex.get(index)
                : undefined;
            if (completedTurn && completedTurn.firstToolIndex !== index) {
              return null;
            }
            const nextIsTool = messages[index + 1]?.role === "tool";
            const previousIsTool = messages[index - 1]?.role === "tool";
            const connectsToActivity =
              message.role === "tool" &&
              showActivity &&
              index === messages.length - 1;

            return (
              <motion.div
                key={message.id ?? `${index}-${message.created_at}`}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
              >
                {message.role === "user" ? (
                  <div className="flex flex-col items-end gap-1.5">
                    {(message.payload as { selection?: ChatSelection } | null)
                      ?.selection && (
                      <SelectionQuote
                        selection={
                          (message.payload as { selection: ChatSelection }).selection
                        }
                      />
                    )}
                    <div className="max-w-[85%] rounded-2xl rounded-br-md bg-moss-surface px-3.5 py-2.5 text-[0.875rem] leading-relaxed text-ivory">
                      {message.content}
                    </div>
                  </div>
                ) : message.role === "tool" && completedTurn ? (
                  <CompletedAgentWorkDisclosure
                    turn={completedTurn}
                    onPrompt={chat.send}
                    runId={runId}
                  />
                ) : message.role === "tool" ? (
                  <div className="relative">
                    {(previousIsTool || nextIsTool || connectsToActivity) && (
                      <AgentTimelineRail
                        start={previousIsTool ? "gap" : "node"}
                        end={nextIsTool || connectsToActivity ? "gap" : "node"}
                        spacing="quick"
                        className="bg-moss-surface/20"
                      />
                    )}
                    <div className="relative z-10">
                      <ToolMessage
                        message={message}
                        onPrompt={chat.send}
                        runId={runId}
                        historical={
                          typeof message.id === "number" &&
                          settledToolStartIds.has(message.id)
                        }
                      />
                    </div>
                  </div>
                ) : (
                (() => {
                  const claims = (
                    message.payload as { claims?: ClaimsPayload } | null
                  )?.claims;
                  const isFresh =
                    lastAnswer !== null && index === lastAssistantIndex;
                  const flaggedClaims =
                    claims?.flagged.map((f) => f.claim)
                    ?? (isFresh
                      ? lastAnswer.claim_checks
                          .filter((c) => c.support !== "supported")
                          .map((c) => c.claim)
                      : undefined);
                  const completedOutputs =
                    completedHistory.byAssistantIndex.get(index)?.outputs ?? [];
                  return (
                    <div className="flex gap-3">
                      <AssistantAvatar />
                      <div className="min-w-0 flex-1 pt-0.5">
                        <div className="text-[0.875rem] leading-relaxed">
                          <ClaimAwareText
                            text={message.content}
                            flagged={flaggedClaims}
                            numbering={numbering}
                            titles={titles}
                            webLinks={webLinks}
                            docs={docByWork}
                          />
                        </div>
                        <EvidenceChips
                          evidence={
                            ((message.payload as { evidence?: EvidenceRef[] } | null)
                              ?.evidence ?? []) as EvidenceRef[]
                          }
                          titles={titles}
                        />
                        {claims && claims.checked > 0 ? (
                          <ClaimSummary
                            checked={claims.checked}
                            flagged={claims.flagged.length}
                          />
                        ) : isFresh ? (
                          <ClaimSummary
                            checked={lastAnswer.claims_checked}
                            flagged={lastAnswer.claims_flagged}
                          />
                        ) : null}
                        <CompletedTurnOutputs
                          messages={completedOutputs}
                          onPrompt={chat.send}
                          runId={runId}
                        />
                        {index === lastAssistantIndex &&
                        lastAssistantIndex === messages.length - 1 &&
                        !pendingQuestion && !reconnecting ? (
                          <FollowUpChips
                            suggestions={followUps}
                            onSelect={chat.send}
                            disabled={chat.sending}
                            className="mt-3"
                          />
                        ) : null}
                      </div>
                    </div>
                  );
                  })()
                )}
              </motion.div>
            );
          })}

          {pendingQuestion && pendingUserIndex < 0 && (
            <motion.div
              key="pending-question"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
            >
              <div className="flex flex-col items-end gap-1.5">
                {chat.pendingSelection && (
                  <SelectionQuote selection={chat.pendingSelection} />
                )}
                <div className="max-w-[85%] rounded-2xl rounded-br-md bg-moss-surface px-3.5 py-2.5 text-[0.875rem] leading-relaxed text-ivory">
                  {pendingQuestion}
                </div>
              </div>
            </motion.div>
          )}

          {(visibleDraft || visibleReasoning) && !activeTurnHasAnswer && (
            <motion.div
              key="streaming-answer"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              className="flex gap-3"
            >
              <AssistantAvatar />
              <div className="min-w-0 flex-1 pt-0.5">
                {visibleReasoning ? (
                  <ReasoningDisclosure
                    reasoning={visibleReasoning}
                    live={!visibleDraft}
                  />
                ) : null}
                {visibleDraft ? (
                <div className="whitespace-pre-wrap text-[0.875rem] leading-relaxed">
                  <CitedText
                    text={visibleDraft}
                    numbering={numbering}
                    titles={titles}
                    webLinks={webLinks}
                    docs={docByWork}
                  />
                  <motion.span
                    aria-hidden="true"
                    className="ml-0.5 inline-block h-[1em] w-px translate-y-[2px] bg-moss-surface"
                    animate={{ opacity: [1, 0.2, 1] }}
                    transition={{ duration: 0.9, repeat: Infinity }}
                  />
                </div>
                ) : null}
                {liveActivity?.phase === "verification" && liveActivity.label ? (
                  <div className="mt-3 inline-flex items-center gap-2 rounded-full bg-accent/65 px-2.5 py-1 text-[0.6875rem] text-moss">
                    <motion.span
                      className="size-1.5 rounded-full bg-moss-surface"
                      animate={{ opacity: [0.3, 1, 0.3] }}
                      transition={{ duration: 1.2, repeat: Infinity }}
                    />
                    {liveActivity.label}
                  </div>
                ) : null}
              </div>
            </motion.div>
          )}

          {showActivity && (
            <motion.div
              key="live-activity"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
            >
              <div className="relative">
                {activeToolCount > 0 && (
                  <AgentTimelineRail
                    start="gap"
                    end="node"
                    spacing="quick"
                    className="bg-moss-surface/20"
                  />
                )}
                <AgentThinkingIndicator label={activityLabel} />
              </div>
            </motion.div>
          )}
        </AnimatePresence>
        <div ref={endRef} />
      </div>
    </div>
  );
}

/** Upload a PDF (file or public link) into a run: shared by the paperclip
 * and the drag-and-drop zones. */
export function useDocumentUpload(runId: RunRef, onDone?: () => void) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: { file?: File; url?: string }) => {
      if (input.file) {
        const content_base64 = await fileToBase64(input.file);
        return api.uploadDocument(
          { filename: input.file.name, content_base64 },
          runId,
        );
      }
      return api.uploadDocument({ url: input.url }, runId);
    },
    onSuccess: async (doc) => {
      toast.success(
        doc.verified
          ? `Added ${doc.title} (metadata verified)`
          : `Added ${doc.title}`,
      );
      onDone?.();
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["chat", String(runId)] }),
        queryClient.invalidateQueries({ queryKey: ["works", String(runId)] }),
        queryClient.invalidateQueries({ queryKey: ["documents", String(runId)] }),
      ]);
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "The upload failed."),
  });
}

/** Paperclip: opens the file dialog straight away (PDFs and photos of
 * sources); links just go into the message itself. */
function AttachButton({
  runId,
  disabled,
}: {
  runId: RunRef;
  disabled: boolean;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const upload = useDocumentUpload(runId);

  return (
    <>
      <input
        ref={fileRef}
        type="file"
        accept="application/pdf,.pdf,image/png,image/jpeg,image/webp,image/gif"
        className="hidden"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) upload.mutate({ file });
          event.target.value = "";
        }}
      />
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            className="size-9 shrink-0 rounded-full text-muted-foreground"
            disabled={disabled || upload.isPending}
            aria-label="Attach a PDF or a photo of a source"
            onClick={() => fileRef.current?.click()}
          >
            {upload.isPending ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Paperclip className="size-4" />
            )}
          </Button>
        </TooltipTrigger>
        <TooltipContent side="top">
          Attach a PDF or a photo of a source. Dropping files here or pasting
          a link into the message works too.
        </TooltipContent>
      </Tooltip>
    </>
  );
}

/** The input bar, pinned below the scroll area so it never leaves the screen. */
export function ChatComposer({
  runId,
  chat,
  disabledReason,
  askMode = false,
  initialQuestion,
}: {
  runId: RunRef;
  chat: RunChat;
  disabledReason: string | null;
  /** Quick-answer conversations: no results, so no context-size control. */
  askMode?: boolean;
  initialQuestion?: string;
}) {
  const [question, setQuestion] = useState("");
  const [webSearchQueryDraft, setWebSearchQueryDraft] = useState<{
    message: string;
    query: string;
  } | null>(null);
  const [approvedWebSearchScope, setApprovedWebSearchScope] = useState<string | null>(null);
  const [retryWebSearchPublicDataConfirmed, setRetryWebSearchPublicDataConfirmed] =
    useState(false);
  const { me } = useAuth();
  const language = me?.language === "de" ? "de" : "en";
  const {
    selection,
    setSelection,
    send,
    queue,
    active,
    stopping,
    stop,
    queuedTurns,
    failedTurn,
    dismissFailedTurn,
    retryFailedTurn,
    removeQueued,
    steerQueued,
    model,
    setModel,
    discovering,
  } = chat;
  const discoveryDisabledReason = discovering
    ? language === "de"
      ? "Laufende Antwort wird wieder verbunden…"
      : "Reconnecting the active answer…"
    : disabledReason;
  const webSearchRequested = explicitWebResearchRequested(question);
  const webSearchQuery = webSearchQueryDraft?.message === question
    ? webSearchQueryDraft.query
    : suggestPublicWebSearchQuery(question, [
        // The first Quick Answer question belongs to the run, not chat history.
        ...(initialQuestion ? [{ role: "user", content: initialQuestion }] : []),
        ...chat.messages,
      ], Boolean(selection));
  // A new question, topic, workspace or context suggestion invalidates approval
  // synchronously, including before React effects run after a history refresh.
  const webSearchScope = JSON.stringify([String(runId), question, webSearchQuery.trim()]);
  const webSearchPublicDataConfirmed = approvedWebSearchScope === webSearchScope;
  function setWebSearchPublicDataConfirmed(confirmed: boolean) {
    setApprovedWebSearchScope(confirmed ? webSearchScope : null);
  }
  const retryWebSearchRequested = Boolean(
    failedTurn && explicitWebResearchRequested(failedTurn.text),
  );
  const webSearchConfirmationMissing =
    webSearchRequested && (
      !webSearchPublicDataConfirmed || !validPublicWebSearchQuery(webSearchQuery)
    );

  useEffect(() => {
    setRetryWebSearchPublicDataConfirmed(false);
  }, [failedTurn]);

  function submit() {
    const text = question.trim();
    if (
      !text ||
      discoveryDisabledReason ||
      webSearchConfirmationMissing
    ) {
      return;
    }
    setQuestion("");
    setWebSearchQueryDraft(null);
    setWebSearchPublicDataConfirmed(false);
    send(text, {
      webSearchPublicDataConfirmed:
        webSearchRequested && webSearchPublicDataConfirmed,
      webSearchQuery: webSearchRequested ? webSearchQuery.trim() : undefined,
    });
  }

  function queueMessage() {
    const text = question.trim();
    if (
      !text ||
      discoveryDisabledReason ||
      webSearchConfirmationMissing
    ) {
      return;
    }
    setQuestion("");
    setWebSearchQueryDraft(null);
    setWebSearchPublicDataConfirmed(false);
    queue(text, {
      webSearchPublicDataConfirmed:
        webSearchRequested && webSearchPublicDataConfirmed,
      webSearchQuery: webSearchRequested ? webSearchQuery.trim() : undefined,
    });
  }

  return (
    <div>
      {/* the passage marked in the reader rides along with the next question */}
      {selection && (
        <div className="mb-1.5 flex items-start gap-2.5 rounded-2xl border border-moss/30 bg-accent/60 px-3.5 py-2.5">
          <TextQuote className="mt-0.5 size-3.5 shrink-0 text-moss" />
          <div className="min-w-0 flex-1">
            <p className="line-clamp-2 text-[0.75rem] italic leading-snug text-foreground/85">
              “{selection.quote}”
            </p>
            <p className="mt-0.5 font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
              Discussing p.{selection.page}
              {selection.title ? ` · ${selection.title.slice(0, 60)}` : ""}
            </p>
          </div>
          <button
            type="button"
            onClick={() => setSelection(null)}
            className="grid size-5 shrink-0 cursor-pointer place-items-center rounded-full text-muted-foreground transition-colors hover:bg-moss-surface hover:text-ivory"
            aria-label="Drop the marked passage"
          >
            <X className="size-3" />
          </button>
        </div>
      )}
      <AnimatePresence initial={false}>
        {failedTurn && !active && (
          <motion.div
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 4 }}
            role="alert"
            className="mb-1.5 flex items-center gap-2 rounded-2xl border border-destructive/25 bg-destructive/5 px-3 py-2 text-[0.75rem]"
          >
            <TriangleAlert className="size-4 shrink-0 text-destructive" />
            <div className="min-w-0 flex-1">
              <p className="text-foreground/80">
                {language === "de"
                  ? "Die Antwort konnte nicht abgeschlossen werden. Deine Nachricht ist gespeichert."
                  : "The answer could not be completed. Your message is saved."}
              </p>
              {retryWebSearchRequested && (
                <PublicWebSearchApproval
                  className="mt-2"
                  language={language}
                  query={failedTurn.webSearchQuery ?? failedTurn.text}
                  exactQuery={Boolean(failedTurn.webSearchQuery)}
                  confirmed={retryWebSearchPublicDataConfirmed}
                  onConfirmedChange={setRetryWebSearchPublicDataConfirmed}
                />
              )}
            </div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-7 shrink-0 rounded-full px-2.5 text-[0.6875rem]"
              onClick={() => {
                retryFailedTurn(retryWebSearchPublicDataConfirmed);
                setRetryWebSearchPublicDataConfirmed(false);
              }}
              disabled={
                retryWebSearchRequested && !retryWebSearchPublicDataConfirmed
              }
            >
              {language === "de" ? "Erneut versuchen" : "Retry"}
            </Button>
            <button
              type="button"
              onClick={() => {
                setRetryWebSearchPublicDataConfirmed(false);
                dismissFailedTurn();
              }}
              className="grid size-7 shrink-0 place-items-center rounded-full text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
              aria-label={language === "de" ? "Fehlerhinweis schließen" : "Dismiss error"}
            >
              <X className="size-3.5" />
            </button>
          </motion.div>
        )}
        {queuedTurns.length > 0 && (
          <motion.div
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 4 }}
            className="mb-1.5 max-h-40 space-y-1 overflow-y-auto rounded-2xl border border-border bg-card/95 p-1.5 shadow-[0_8px_24px_rgba(12,29,25,0.06)]"
            aria-label={language === "de" ? "Nachrichten in der Warteschlange" : "Queued chat messages"}
          >
            {queuedTurns.map((turn, index) => (
              <motion.div
                layout
                key={turn.id}
                className={cn(
                  "flex min-w-0 items-center gap-2 rounded-xl px-2.5 py-2",
                  turn.steering ? "bg-moss/10 ring-1 ring-inset ring-moss/25" : "bg-accent/55",
                )}
              >
                <div className="min-w-0 flex-1">
                  <p className="font-mono text-[0.5625rem] uppercase tracking-[0.18em] text-muted-foreground">
                    {turn.steering
                      ? language === "de"
                        ? "Kurskorrektur"
                        : "Course correction"
                      : `${language === "de" ? "Danach" : "Next"} · ${index + 1}`}
                  </p>
                  <p className="truncate text-[0.75rem] text-foreground/85">{turn.text}</p>
                </div>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="h-7 shrink-0 rounded-full px-2.5 text-[0.6875rem]"
                  onClick={() => void steerQueued(turn.id)}
                  disabled={stopping || turn.steering}
                  title={
                    language === "de"
                      ? "Aktuelle Antwort stoppen und mit dieser Anweisung fortfahren"
                      : "Stop the current answer and continue with this instruction"
                  }
                >
                  {language === "de" ? "Steuern" : "Steer"}
                </Button>
                <button
                  type="button"
                  onClick={() => removeQueued(turn.id)}
                  className="grid size-7 shrink-0 place-items-center rounded-full text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
                  aria-label={
                    language === "de"
                      ? `Nachricht ${index + 1} aus der Warteschlange entfernen`
                      : `Remove queued message ${index + 1}`
                  }
                >
                  <Trash2 className="size-3.5" />
                </button>
              </motion.div>
            ))}
          </motion.div>
        )}
      </AnimatePresence>
      <div
        className={cn(
          "flex items-end gap-1.5 rounded-2xl border bg-card p-1.5 shadow-[0_2px_16px_rgba(12,29,25,0.05)] transition-all focus-within:border-moss/40",
          selection ? "border-moss/40" : "border-border",
          discoveryDisabledReason && "opacity-70",
        )}
      >
        <AttachButton runId={runId} disabled={Boolean(discoveryDisabledReason)} />
        <div className="self-end pb-0.5">
          <ModelPicker value={model} onChange={setModel} compact />
        </div>

        <Textarea
          value={question}
          onChange={(event) => {
            setQuestion(event.target.value);
            setWebSearchPublicDataConfirmed(false);
            setWebSearchQueryDraft(null);
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              submit();
            }
          }}
          placeholder={
            discoveryDisabledReason ??
            (selection
              ? "Ask about the marked passage…"
              : askMode
                ? "Ask a follow-up…"
                : "Ask about these results…")
          }
          aria-label={selection ? "Ask about the marked passage" : "Ask a follow-up question"}
          disabled={Boolean(discoveryDisabledReason)}
          rows={1}
          className="max-h-32 min-h-[2.4rem] flex-1 resize-none border-0 bg-transparent px-2.5 py-2 text-[0.875rem] shadow-none focus-visible:ring-0 dark:bg-transparent"
        />

        {active && question.trim() && (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                onClick={queueMessage}
                disabled={
                  Boolean(discoveryDisabledReason) || webSearchConfirmationMissing
                }
                className="size-8 shrink-0 rounded-full text-muted-foreground hover:text-foreground"
                aria-label={
                  language === "de"
                    ? "Diese Nachricht zur Warteschlange hinzufügen"
                    : "Add this message to the queue"
                }
              >
                <ListPlus className="size-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="top">
              {language === "de" ? "Zur Warteschlange hinzufügen" : "Add to queue"}
            </TooltipContent>
          </Tooltip>
        )}

        <Button
          size="icon"
          onClick={() => (active ? void stop() : submit())}
          disabled={
            Boolean(discoveryDisabledReason) ||
            (active
              ? stopping
              : !question.trim() || webSearchConfirmationMissing)
          }
          className="size-9 shrink-0 rounded-full"
          aria-label={
            active
              ? language === "de"
                ? "Aktuelle Antwort stoppen"
                : "Stop the current answer"
              : language === "de"
                ? "Frage senden"
                : "Ask"
          }
        >
          {active ? (
            stopping ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Square className="size-3.5 fill-current" />
            )
          ) : (
            <SendHorizontal className="size-4" />
          )}
        </Button>
      </div>
      {webSearchRequested && (
        <PublicWebSearchApproval
          className="mt-1.5"
          language={language}
          query={webSearchQuery}
          onQueryChange={(query) => {
            setWebSearchQueryDraft({ message: question, query });
            setWebSearchPublicDataConfirmed(false);
          }}
          confirmed={webSearchPublicDataConfirmed}
          onConfirmedChange={setWebSearchPublicDataConfirmed}
        />
      )}
      <p className="mt-1.5 text-center text-[0.6875rem] text-muted-foreground/80">
        {language === "de"
          ? "KI-Assistent · Prüfe Aussagen und Quellen vor der Übernahme."
          : "AI assistant · Review claims and sources before using the result."}
      </p>
    </div>
  );
}
