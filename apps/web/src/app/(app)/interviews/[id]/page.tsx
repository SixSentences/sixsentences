"use client";
import { AiInteractionNotice } from "@/components/ai-interaction-notice";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  ArrowLeft,
  AudioLines,
  Check,
  ChevronDown,
  Download,
  FileText,
  FolderKanban,
  Loader2,
  MessageSquareQuote,
  Pencil,
  Play,
  RefreshCw,
  SendHorizontal,
  Square,
  Trash2,
  Users,
  X,
} from "lucide-react";
import { toast } from "sonner";

import {
  AgentActivityTimeline,
  AgentLoadingOrb,
  AgentTimelineHandoffs,
  AgentTurnElapsed,
  SpecialistCompletedTurn,
  agentTurnIdsFromTimelines,
  useAgentTimelineLedger,
} from "@/components/agent-work-status";
import {
  AgentTurnQueue,
  useAgentTurnQueue,
} from "@/components/agent/agent-turn-queue";
import {
  isClearChatCommand,
  SpecialistChatResetButton,
  SpecialistChatResetDialog,
  useSpecialistChatReset,
} from "@/components/agent/specialist-chat-reset";
import { WorkspaceActionList } from "@/components/agent/workspace-action-card";
import { ConfirmDeleteDialog } from "@/components/confirm-delete-dialog";
import { DetailError } from "@/components/detail-error";
import { InlineTitle } from "@/components/inline-title";
import {
  MobileWorkspaceSwitch,
  type MobileWorkspacePane,
} from "@/components/mobile-workspace-switch";
import { ResizableWorkspaceSplit } from "@/components/workspace/resizable-workspace-split";
import {
  useDurableSpecialistTurn,
  useSpecialistTurnControl,
} from "@/hooks/use-durable-specialist-turn";

import ModelPicker from "@/components/search/model-picker";
import { usePrivateModelPreference } from "@/hooks/use-private-model-preference";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useProjects } from "@/hooks/queries";
import {
  SpecialistStreamError,
  api,
  createSpecialistTurnId,
  downloadInterviewReport,
  fetchInterviewAudioUrl,
} from "@/lib/api";
import { formatClock } from "@/lib/format";
import { useActiveProject } from "@/lib/project-context";
import type {
  Interview,
  InterviewMessage,
  InterviewQuote,
} from "@/lib/types";
import { userFacingStoredErrorMessage } from "@/lib/user-facing-error";
import { cn } from "@/lib/utils";

const STARTERS = [
  {
    label: "Core statements",
    prompt: "What are the core statements of this interview? Back each with a quote.",
  },
  {
    label: "Stance on a topic",
    prompt: "What does the participant say about the main topic of the study? Quote the decisive passages.",
  },
  {
    label: "Contradictions",
    prompt: "Where does the participant contradict themselves or hedge? Show the exact passages.",
  },
  {
    label: "Methods paragraph",
    prompt: "Write a short methods-ready description of this interview: setting, speakers, duration, language.",
  },
] as const;

function QuoteCard({
  quote,
  speakers,
  onJump,
}: {
  quote: InterviewQuote;
  speakers: Record<string, string>;
  onJump?: (idx: number) => void;
}) {
  const speaker = quote.speaker ? speakers[quote.speaker] ?? quote.speaker : "";
  return (
    <button
      type="button"
      onClick={() => onJump?.(quote.segment)}
      className={cn(
        "block w-full cursor-pointer rounded-xl border px-3 py-2.5 text-left transition-colors",
        quote.verified
          ? "border-moss/25 bg-accent/40 hover:border-moss/50"
          : "border-amber-500/30 bg-amber-50 hover:border-amber-500/50 dark:bg-amber-300/10",
      )}
    >
      <p className="text-[0.75rem] italic leading-relaxed text-foreground">
        “{quote.text}”
      </p>
      <p className="mt-1.5 flex items-center gap-1.5 font-mono text-[0.59375rem] uppercase tracking-[0.18em]">
        {quote.verified ? (
          <span className="flex items-center gap-1 text-moss">
            <Check className="size-3" /> Seg. {quote.segment}
            {quote.timestamp ? ` · ${quote.timestamp}` : ""}
            {speaker ? ` · ${speaker}` : ""}
          </span>
        ) : (
          <span className="flex items-center gap-1 text-amber-700">
            <AlertCircle className="size-3" /> Not found verbatim in the transcript
          </span>
        )}
      </p>
    </button>
  );
}

function PipelineCard({ interview }: { interview: Interview }) {
  const active =
    interview.pipeline.find((stage) => stage.status === "running") ??
    interview.pipeline.find((stage) => stage.status === "pending") ??
    interview.pipeline.at(-1);
  return (
    <div className="grid min-h-0 flex-1 place-items-center px-6">
      <div className="w-full max-w-sm" role="status" aria-live="polite">
        <div className="flex items-center justify-center">
          <AgentLoadingOrb />
        </div>
        <p className="text-center text-[0.8125rem] font-medium text-foreground">
          {active?.label ?? "Preparing the recording"}
        </p>
        <p className="mt-1 text-center text-[0.6875rem] text-muted-foreground">
          You can leave this page; transcription continues in the background.
        </p>
        <div className="mx-auto mt-5 w-fit max-w-full space-y-2 text-left">
          {interview.pipeline.map((stage) => (
            <div key={stage.id} className="flex items-center gap-2">
              <span
                className={cn(
                  "grid size-4 shrink-0 place-items-center rounded-full border",
                  stage.status === "completed"
                    ? "border-moss bg-moss-surface text-ivory"
                    : stage.status === "running"
                      ? "border-moss bg-accent"
                      : stage.status === "failed"
                        ? "border-destructive/60 bg-destructive/10"
                        : "border-border bg-card",
                )}
              >
                {stage.status === "completed" ? <Check className="size-2.5" /> : null}
                {stage.status === "running" ? (
                  <span className="size-1.5 animate-pulse rounded-full bg-moss-surface" />
                ) : null}
              </span>
              <span className="min-w-0">
                <span
                  className={cn(
                    "block truncate text-[0.71875rem] leading-tight",
                    stage.status === "running"
                      ? "font-medium text-foreground"
                      : stage.status === "completed"
                        ? "text-foreground/70"
                        : "text-muted-foreground/65",
                  )}
                >
                  {stage.label}
                </span>
                {stage.detail && (stage.status === "running" || stage.status === "failed") ? (
                  <span className="mt-0.5 block truncate text-[0.625rem] text-muted-foreground">
                    {stage.status === "failed"
                      ? userFacingStoredErrorMessage(
                          stage.detail,
                          "This step could not be completed. Please try processing the interview again.",
                        )
                      : stage.detail}
                  </span>
                ) : null}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export default function InterviewWorkspacePage() {
  const params = useParams<{ id: string }>();
  const interviewId = params.id;
  const queryClient = useQueryClient();
  const { setActiveProjectId } = useActiveProject();
  const chatEndRef = useRef<HTMLDivElement>(null);
  const audioRef = useRef<HTMLAudioElement>(null);

  const [chatInput, setChatInput] = useState("");
  const [mobilePane, setMobilePane] = useState<MobileWorkspacePane>("workspace");
  const [agentWorking, setAgentWorking] = useState(false);
  const [pendingQuestion, setPendingQuestion] = useState<string | null>(null);
  const {
    activeTurnId,
    turnAccepted,
    stopping,
    beginLocalTurn,
    recoverTurn,
    finishTurn,
    stopTurn,
  } = useSpecialistTurnControl();
  const {
    events: agentEvents,
    handoffs: agentTimelineHandoffs,
    recordAgentEvent,
    startAgentTurn,
    handoffAgentTurn,
    resetAgentTimeline,
  } = useAgentTimelineLedger();
  const [view, setView] = useState<"transcript" | "analysis">("transcript");
  const [audioLoadAttempt, setAudioLoadAttempt] = useState(0);
  const [audioState, setAudioState] = useState<{
    interviewId: string;
    attempt: number;
    status: "loading" | "ready" | "error";
    url: string | null;
  } | null>(null);
  const [highlightIdx, setHighlightIdx] = useState<number | null>(null);
  const [editingIdx, setEditingIdx] = useState<number | null>(null);
  const [editText, setEditText] = useState("");
  const [speakersOpen, setSpeakersOpen] = useState(false);
  const [speakerDraft, setSpeakerDraft] = useState<Record<string, string>>({});
  const [downloading, setDownloading] = useState(false);
  const [removeAudioTarget, setRemoveAudioTarget] = useState<{
    id: string;
    title: string;
  } | null>(null);

  const [model, pickModel] = usePrivateModelPreference("six:interview-model");

  const { data: interview, isLoading, error: loadError } = useQuery({
    queryKey: ["interview", interviewId],
    queryFn: () => api.interview(interviewId),
    refetchInterval: (query) => {
      const current = query.state.data;
      return current && (current.status === "pending" || current.analyzing)
        ? 2_500
        : false;
    },
  });
  const ready = interview?.status === "ready";
  const interviewStatus = interview?.status;
  const interviewAnalyzing = interview?.analyzing;
  useEffect(() => {
    if (!interviewStatus || interviewStatus === "pending" || interviewAnalyzing) return;
    // Terminal analysis status is an additional shared-state refresh signal,
    // including when a participant interview completed in another browser.
  }, [interviewId, interviewStatus, interviewAnalyzing, queryClient]);
  useEffect(() => {
    if (interview?.project_id) setActiveProjectId(interview.project_id);
  }, [interview?.project_id, setActiveProjectId]);
  const { data: history } = useQuery({
    queryKey: ["interview-chat", interviewId],
    queryFn: () => api.interviewChatHistory(interviewId),
    enabled: ready,
  });

  // The recording route needs authentication; each load owns its blob URL.
  const audioAvailable = ready && interview.audio_available;
  const currentAudio = audioAvailable && audioState?.interviewId === interviewId
    && audioState.attempt === audioLoadAttempt ? audioState : null;
  const audioUrl = currentAudio?.status === "ready" ? currentAudio.url : null;
  const audioLoadFailed = currentAudio?.status === "error";
  const retryAudio = () => setAudioLoadAttempt((attempt) => attempt + 1);
  const markAudioUnavailable = () => setAudioState((current) =>
    current?.interviewId === interviewId && current.attempt === audioLoadAttempt
      ? { ...current, status: "error", url: null }
      : current,
  );
  useEffect(() => {
    if (!audioAvailable) return;
    const controller = new AbortController();
    let objectUrl: string | null = null;
    let cancelled = false;
    const state = { interviewId, attempt: audioLoadAttempt };
    setAudioState({ ...state, status: "loading", url: null });
    fetchInterviewAudioUrl(interviewId, controller.signal)
      .then((url) => {
        if (cancelled) URL.revokeObjectURL(url);
        else {
          objectUrl = url;
          setAudioState({ ...state, status: "ready", url });
        }
      })
      .catch(() => {
        if (!cancelled) setAudioState({ ...state, status: "error", url: null });
      });
    return () => {
      cancelled = true;
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [interviewId, audioAvailable, audioLoadAttempt]);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [history, pendingQuestion]);

  const chat = useMutation({
    mutationFn: (question: string) => {
      const turnId = createSpecialistTurnId();
      return api.interviewChatStream(
        interviewId,
        question,
        model,
        recordAgentEvent,
        beginLocalTurn(turnId),
      );
    },
    onMutate: (question) => {
      setAgentWorking(true);
      startAgentTurn();
      setPendingQuestion(question);
    },
    onSuccess: async () => {
      try {
        await queryClient.invalidateQueries({ queryKey: ["interview-chat", interviewId] });
      } finally {
        handoffAgentTurn();
        finishTurn();
        setAgentWorking(false);
        setPendingQuestion(null);
      }
    },
    onError: async (error) => {
      await queryClient.refetchQueries({ queryKey: ["interview-chat", interviewId] });
      handoffAgentTurn();
      finishTurn();
      setAgentWorking(false);
      setPendingQuestion(null);
      if (!(error instanceof SpecialistStreamError && error.kind === "cancelled")) {
        toast.error(error instanceof Error ? error.message : "That didn't work.");
      }
    },
  });
  const reanalyze = useMutation({
    mutationFn: () => api.interviewAnalyze(interviewId, model),
    onSuccess: (updated) => {
      queryClient.setQueryData(["interview", interviewId], (current: Interview | undefined) =>
        current ? { ...current, analyzing: updated.analyzing, pipeline: updated.pipeline } : updated,
      );
      toast.success("Fresh analysis started.");
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Analysis failed to start."),
  });

  const saveSegment = useMutation({
    mutationFn: (input: { idx: number; text: string }) =>
      api.interviewSegmentUpdate(interviewId, input.idx, input.text),
    onSuccess: () => {
      setEditingIdx(null);
      void queryClient.invalidateQueries({ queryKey: ["interview", interviewId] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Save failed."),
  });

  const saveSpeakers = useMutation({
    mutationFn: () => api.interviewUpdate(interviewId, { speakers: speakerDraft }),
    onSuccess: (updated) => {
      queryClient.setQueryData(["interview", interviewId], updated);
      setSpeakersOpen(false);
      toast.success("Speakers named.");
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Save failed."),
  });

  const { data: projects } = useProjects();
  const saveMeta = useMutation({
    mutationFn: (body: { title?: string; project_id?: number | null }) =>
      api.interviewUpdate(interviewId, body),
    onSuccess: (updated, body) => {
      queryClient.setQueryData(["interview", interviewId], updated);
      void queryClient.invalidateQueries({ queryKey: ["interviews"] });
      void queryClient.invalidateQueries({ queryKey: ["project-workspace"] });
      if ("project_id" in body) {
        toast.success(
          body.project_id === null
            ? "Removed from its project."
            : `Moved to ${
                (projects ?? []).find((item) => item.id === body.project_id)?.name ??
                "the project"
              }.`,
        );
      }
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Save failed."),
  });
  const removeAudio = useMutation({
    mutationFn: (targetId: string) => api.interviewAudioRemove(targetId),
    onSuccess: (updated, targetId) => {
      queryClient.setQueryData(["interview", targetId], updated);
      setRemoveAudioTarget((current) =>
        current?.id === targetId ? null : current,
      );
      toast.success("Audio removed. The transcript stays.");
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "That didn't work."),
  });

  async function exportReport(format: "pdf" | "tex" | "txt") {
    setDownloading(true);
    try {
      if (format === "pdf") {
        if (!interview) throw new Error("The interview is still loading.");
        // branded client-side render, same design language as the run report
        const { downloadInterviewReportPdf } = await import(
          "@/lib/interview-report-doc"
        );
        await downloadInterviewReportPdf(interview);
      } else {
        await downloadInterviewReport(interviewId, format);
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Export failed.");
    } finally {
      setDownloading(false);
    }
  }

  function playFrom(ms: number) {
    const player = audioRef.current;
    if (!player) return;
    player.currentTime = ms / 1000;
    void player.play();
  }

  function jumpToSegment(idx: number) {
    setView("transcript");
    setHighlightIdx(idx);
    window.setTimeout(() => {
      document
        .getElementById(`segment-${idx}`)
        ?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 60);
    window.setTimeout(() => setHighlightIdx(null), 2_600);
  }

  const chatMessages: InterviewMessage[] = history ?? [];
  const persistedAgentTimelines = chatMessages
    .filter((message) => message.role === "assistant")
    .map((message) => message.payload.agent_events ?? []);
  const { checking: checkingAgentTurn, recovering: recoveringAgentTurn } =
    useDurableSpecialistTurn<unknown>({
      resourceKind: "interview",
      resourceId: interviewId,
      enabled: ready && history !== undefined,
      persistedTurnIds: agentTurnIdsFromTimelines(persistedAgentTimelines),
      onStarted: (turn) => {
        recoverTurn(turn.turn_id);
        startAgentTurn();
        setAgentWorking(true);
        setPendingQuestion(null);
      },
      onEvent: recordAgentEvent,
      onTerminal: async (_result, turn) => {
        await Promise.allSettled([
          queryClient.refetchQueries({ queryKey: ["interview-chat", interviewId] }),
          queryClient.invalidateQueries({ queryKey: ["interview", interviewId] }),
        ]);
        handoffAgentTurn();
        finishTurn(turn.turn_id);
        setAgentWorking(false);
        setPendingQuestion(null);
      },
      onError: (error) => {
        setAgentWorking(false);
        toast.error(
          error instanceof Error
            ? error.message
            : "The running interview analysis could not be recovered yet.",
        );
      },
    });
  const specialistBusy =
    agentWorking
    || Boolean(activeTurnId)
    || (ready && history === undefined)
    || checkingAgentTurn
    || recoveringAgentTurn;
  const queuedChat = useAgentTurnQueue<string>({
    working: specialistBusy,
    run: (question) => chat.mutate(question),
  });
  const {
    clearChat,
    clearing,
    confirmationOpen,
    setConfirmationOpen,
    confirmClearChat,
    clearChatTriggerRef,
  } = useSpecialistChatReset({
    resourceKind: "interview",
    resourceId: interviewId,
    queryKey: ["interview-chat", interviewId],
    hasHistory: chatMessages.length > 0,
    onCleared: () => {
      setChatInput("");
      setPendingQuestion(null);
      queuedChat.clear();
      resetAgentTimeline();
    },
  });
  const stopAgentTurn = async () => {
    try {
      await stopTurn();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "The agent could not be stopped.");
    }
  };

  if (loadError) {
    return (
      <DetailError
        title="Transcript unavailable"
        error={loadError}
        fallback="This transcript could not be loaded."
        backHref="/interviews"
        backLabel="Back to interviews"
      />
    );
  }
  if (isLoading || !interview) {
    return (
      <div className="grid min-h-0 flex-1 place-items-center">
        <Loader2 className="size-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const speakers = interview.speakers;
  const segments = interview.segments ?? [];
  const analysis = interview.analysis;
  const clientReportedSource =
    interview.source_integrity === "client_reported_unverified";

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-background md:rounded-t-2xl">
      <SpecialistChatResetDialog
        open={confirmationOpen}
        clearing={clearing}
        returnFocusRef={clearChatTriggerRef}
        onOpenChange={setConfirmationOpen}
        onConfirm={() => void confirmClearChat()}
      />
      <header className="flex min-h-14 shrink-0 items-center justify-between gap-2 border-b border-border px-3 py-2 sm:gap-4 sm:px-5 lg:px-7">
        <div className="flex min-w-0 flex-1 items-center gap-3">
          <Button asChild variant="ghost" size="icon" className="size-8 rounded-full">
            <Link href="/interviews"><ArrowLeft className="size-4" /></Link>
          </Button>
          <div className="min-w-0 flex-1">
            <InlineTitle
              value={interview.title}
              onCommit={(next) => saveMeta.mutate({ title: next })}
              ariaLabel="Interview title"
              placeholder="Untitled interview"
            />
            <p className="font-mono text-[0.59375rem] uppercase tracking-[0.18em] text-muted-foreground">
              {interview.kind === "live" ? "AI-led · " : ""}
              {formatClock(interview.duration_ms)} ·{" "}
              {Object.keys(speakers).length || "?"} speakers · {interview.language}
            </p>
          </div>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                aria-label="Move this interview to a project"
                className="size-7 shrink-0 rounded-full text-muted-foreground"
              >
                <FolderKanban className="size-3.5" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent
              align="start"
              className="max-h-72 w-52 overflow-y-auto rounded-2xl"
            >
              <DropdownMenuItem onSelect={() => saveMeta.mutate({ project_id: null })}>
                <span className="flex-1">No project</span>
                {interview.project_id === null && (
                  <Check className="size-3.5 text-moss" />
                )}
              </DropdownMenuItem>
              {(projects ?? []).length > 0 && <DropdownMenuSeparator />}
              {(projects ?? []).map((project) => (
                <DropdownMenuItem
                  key={project.id}
                  onSelect={() => saveMeta.mutate({ project_id: project.id })}
                >
                  <span className="flex-1 truncate">{project.name}</span>
                  {interview.project_id === project.id && (
                    <Check className="size-3.5 text-moss" />
                  )}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
        {ready && (
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              className="h-9 rounded-full px-3"
              disabled={interview.analyzing || reanalyze.isPending}
              onClick={() => reanalyze.mutate()}
              aria-label="Analyze interview again"
            >
              {(interview.analyzing || reanalyze.isPending) && (
                <Loader2 className="size-4 animate-spin" />
              )}
              <span className="hidden sm:inline">Analyze again</span>
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  className="h-9 rounded-full px-3"
                  disabled={downloading}
                  aria-label="Export interview"
                >
                  {downloading ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <Download className="size-4" />
                  )}
                  <span className="hidden sm:inline">Export</span>
                  <ChevronDown className="size-3.5" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-[15rem]">
                <DropdownMenuItem onSelect={() => void exportReport("pdf")}>
                  <FileText className="size-3.5" /> Report PDF
                </DropdownMenuItem>
                <DropdownMenuItem onSelect={() => void exportReport("tex")}>
                  <FileText className="size-3.5" /> LaTeX source
                </DropdownMenuItem>
                <DropdownMenuItem onSelect={() => void exportReport("txt")}>
                  <FileText className="size-3.5" /> Plain transcript
                </DropdownMenuItem>
                {interview.audio_available && (
                  <>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem
                      variant="destructive"
                      disabled={removeAudio.isPending}
                      onSelect={() =>
                        setRemoveAudioTarget({
                          id: interviewId,
                          title: interview.title || "this interview",
                        })
                      }
                    >
                      <Trash2 className="size-3.5" /> Remove audio, keep transcript
                    </DropdownMenuItem>
                  </>
                )}
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        )}
      </header>

      {interview.status === "pending" && <PipelineCard interview={interview} />}

      {interview.status === "error" && (
        <div className="grid min-h-0 flex-1 place-items-center px-6">
          <div className="w-full max-w-md rounded-3xl border border-destructive/30 bg-destructive/5 p-6 text-center">
            <AlertCircle className="mx-auto size-6 text-destructive" />
            <p className="mt-3 text-[0.875rem] font-medium text-foreground">
              This transcription failed
            </p>
            <p className="mt-1.5 text-[0.75rem] leading-relaxed text-muted-foreground">
              {userFacingStoredErrorMessage(
                interview.error,
                "We couldn't process this interview. Return to Interviews and try again.",
              )}
            </p>
            <Button asChild variant="outline" className="mt-4 rounded-full">
              <Link href="/interviews">Back to interviews</Link>
            </Button>
          </div>
        </div>
      )}

      {ready && (
        <ResizableWorkspaceSplit
          storageKey="six:interview-workspace-split"
          label="Resize interview assistant and transcript"
          mobileSwitch={(
            <MobileWorkspaceSwitch
              value={mobilePane}
              onChange={setMobilePane}
              workspaceLabel="Transcript"
            />
          )}
        >
          {/* chat rail: the agent answers only with quotes verified verbatim
              against the stored transcript */}
          <aside
            className={cn(
              "min-h-0 flex-col border-b border-border bg-card/45 group-data-[workspace-layout=split]/workspace:!flex group-data-[workspace-layout=split]/workspace:border-b-0",
              mobilePane === "agent" ? "flex" : "hidden",
            )}
          >
            <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-5">
              {chatMessages.length === 0 && !specialistBusy && (
                <div className="flex h-full flex-col items-center justify-center gap-2 px-4 text-center">
                  <p className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
                    Ask the interview
                  </p>
                  <p className="max-w-[17rem] text-[0.78125rem] leading-relaxed text-muted-foreground">
                    Every claim comes with verbatim quotes, anchored to segment
                    and timestamp. A quote that cannot be located is flagged,
                    never presented as evidence.
                  </p>
                </div>
              )}
              {chatMessages.map((message) => (
                <div
                  key={message.id}
                  className={cn("flex", message.role === "user" && "justify-end")}
                >
                  {message.role === "user" ? (
                    <div className="max-w-[85%] rounded-2xl rounded-br-md bg-moss-surface px-3.5 py-2.5 text-[0.875rem] leading-relaxed text-ivory">
                      <p className="whitespace-pre-wrap">{message.content}</p>
                    </div>
                  ) : (
                    <div className="min-w-0 max-w-full flex-1 text-[0.875rem] leading-relaxed text-foreground">
                      <SpecialistCompletedTurn
                        kind="interview"
                        events={message.payload.agent_events}
                        answer={message.content}
                        artifacts={message.payload.artifacts}
                      >
                        {(message.payload.quotes?.length ?? 0) > 0 ? (
                          <div className="space-y-2 rounded-2xl border border-border bg-secondary/35 px-3.5 py-3">
                            {message.payload.quotes?.map((quote, quoteIndex) => (
                              <QuoteCard
                                key={`${message.id}-${quoteIndex}`}
                                quote={quote}
                                speakers={speakers}
                                onJump={jumpToSegment}
                              />
                            ))}
                          </div>
                        ) : null}
                        <WorkspaceActionList
                          actions={message.payload.workspace_actions}
                          compact
                        />
                      </SpecialistCompletedTurn>
                    </div>
                  )}
                </div>
              ))}
              <AgentTimelineHandoffs
                handoffs={agentTimelineHandoffs}
                persistedTimelines={persistedAgentTimelines}
              />
              {pendingQuestion &&
                !chatMessages
                  .slice(-2)
                  .some(
                    (message) =>
                      message.role === "user" && message.content === pendingQuestion,
                  ) && (
                  <div className="flex justify-end">
                    <div className="max-w-[85%] rounded-2xl rounded-br-md bg-moss-surface px-3.5 py-2.5 text-[0.875rem] leading-relaxed text-ivory">
                      <p className="whitespace-pre-wrap">{pendingQuestion}</p>
                    </div>
                  </div>
              )}
            {specialistBusy && (
                <div className="min-w-0 max-w-full">
                  <AgentTurnElapsed className="mb-2" />
                  <AgentActivityTimeline events={agentEvents} running />
                </div>
            )}
              <div ref={chatEndRef} />
            </div>
<div className="shrink-0 border-t border-border/60 p-3">
            <AiInteractionNotice />
              {chatMessages.length === 0 && !specialistBusy && (
                <div className="mb-2 flex max-w-full gap-1.5 overflow-x-auto pb-0.5">
                  {STARTERS.map((starter) => (
                    <button
                      key={starter.label}
                      type="button"
                      onClick={() => chat.mutate(starter.prompt)}
                      title={starter.prompt}
                      className="shrink-0 cursor-pointer rounded-full border border-border bg-card px-2.5 py-1 text-[0.65625rem] text-muted-foreground transition-colors hover:border-moss/40 hover:text-moss"
                    >
                      {starter.label}
                    </button>
                  ))}
                </div>
              )}
            <AgentTurnQueue items={queuedChat.queue} onRemove={queuedChat.remove} />
            <form
                method="post"
                className="flex items-end gap-2"
                onSubmit={(event) => {
                  event.preventDefault();
                  const question = chatInput.trim();
                  if (!question) return;
                  if (isClearChatCommand(question)) {
                    void clearChat();
                    return;
                  }
                  if (specialistBusy) return;
                  setChatInput("");
                  queuedChat.submit(question, question);
                }}
              >
                <SpecialistChatResetButton
                  onClick={() => void clearChat()}
                  clearing={clearing}
                  triggerRef={clearChatTriggerRef}
                  disabled={specialistBusy}
                />
                <div className="flex shrink-0 items-center gap-1 self-end">
                  <ModelPicker value={model} onChange={pickModel} compact />
                </div>
                <Textarea
                  value={chatInput}
                  onChange={(event) => setChatInput(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault();
                      const question = chatInput.trim();
                      if (!question) return;
                      if (isClearChatCommand(question)) {
                        void clearChat();
                        return;
                      }
                      if (specialistBusy) return;
                      setChatInput("");
                      queuedChat.submit(question, question);
                    }
                  }}
                  placeholder="What was said about…"
                  rows={1}
                  className="min-h-9 max-h-28 min-w-0 flex-1 resize-none rounded-xl bg-transparent py-2 text-[0.8125rem] dark:bg-transparent"
                />
                <Button
                  type={activeTurnId ? "button" : "submit"}
                  size="icon"
                  className="size-9 shrink-0 rounded-full"
                  disabled={activeTurnId ? !turnAccepted || stopping : !chatInput.trim() || specialistBusy}
                  aria-label={activeTurnId ? "Stop agent turn" : "Send"}
                  onClick={activeTurnId ? () => void stopAgentTurn() : undefined}
                >
                  {stopping ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : activeTurnId ? (
                    <Square className="size-3.5 fill-current" />
                  ) : (
                    <SendHorizontal className="size-4" />
                  )}
                </Button>
              </form>
            </div>
          </aside>

          <main
            className={cn(
              "min-h-0 overflow-y-auto group-data-[workspace-layout=split]/workspace:!block",
              mobilePane === "workspace" ? "block" : "hidden",
            )}
          >
            <div className="sticky top-0 z-10 space-y-3 border-b border-border bg-background/95 px-5 py-3 backdrop-blur lg:px-7">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex w-fit items-center gap-1 rounded-full bg-secondary/65 p-1">
                  {(
                    [
                      ["transcript", AudioLines, "Transcript"],
                      ["analysis", MessageSquareQuote, "Analysis"],
                    ] as const
                  ).map(([value, Icon, label]) => (
                    <button
                      key={value}
                      type="button"
                      onClick={() => setView(value)}
                      className={cn(
                        "flex h-8 cursor-pointer items-center gap-1.5 rounded-full px-3 text-[0.71875rem] font-medium transition-colors",
                        view === value
                          ? "bg-card text-foreground shadow-sm"
                          : "text-muted-foreground hover:text-foreground",
                      )}
                    >
                      <Icon className="size-3.5" /> {label}
                    </button>
                  ))}
                </div>
                {view === "transcript" && (
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-8 rounded-full px-3 text-[0.71875rem]"
                    onClick={() => {
                      setSpeakerDraft({ ...speakers });
                      setSpeakersOpen(true);
                    }}
                  >
                    <Users className="size-3.5" /> Name speakers
                  </Button>
                )}
              </div>
              {view === "transcript" &&
                (audioLoadFailed ? (
                  <div role="alert" className="flex flex-wrap items-center gap-2 text-[0.6875rem] text-muted-foreground">
                    <p>The recording could not be loaded. Your transcript is still available.</p>
                    <Button variant="outline" size="sm" className="h-7 text-xs" onClick={retryAudio}>
                      <RefreshCw className="size-3" /> Try again
                    </Button>
                  </div>
                ) : audioUrl ? (
                  // eslint-disable-next-line jsx-a11y/media-has-caption
                  <audio
                    ref={audioRef}
                    controls
                    src={audioUrl}
                    onError={markAudioUnavailable}
                    preload="metadata"
                    className="h-9 w-full"
                  />
                ) : interview.audio_available ? (
                  <p role="status" className="flex items-center gap-2 text-[0.6875rem] text-muted-foreground">
                    <Loader2 className="size-3 animate-spin" /> Loading the recording…
                  </p>
                ) : (
                  <p className="text-[0.6875rem] text-muted-foreground">
                    The audio was removed; the transcript below is the record.
                  </p>
                ))}
            </div>

            <div className="mx-auto w-full max-w-3xl space-y-5 px-5 py-6 lg:px-7 lg:py-8">
              {view === "transcript" && (
                <div className="space-y-1.5">
                  {segments.map((segment) => {
                    const name = speakers[segment.speaker] ?? segment.speaker;
                    const isInterviewer = segment.speaker === "S1";
                    return (
                      <div
                        key={segment.idx}
                        id={`segment-${segment.idx}`}
                        className={cn(
                          "group rounded-2xl px-3.5 py-2.5 transition-colors",
                          highlightIdx === segment.idx
                            ? "bg-accent ring-2 ring-moss/30"
                            : "hover:bg-secondary/45",
                        )}
                      >
                        <div className="flex items-baseline gap-2">
                          <span className="shrink-0 font-mono text-[0.59375rem] text-muted-foreground/70">
                            [{segment.idx}
                            {segment.edited ? "*" : ""}]
                          </span>
                          <button
                            type="button"
                            title="Play from here"
                            disabled={!audioUrl}
                            onClick={() => playFrom(segment.start_ms)}
                            className={cn(
                              "flex shrink-0 items-center gap-1 font-mono text-[0.625rem]",
                              audioUrl
                                ? "cursor-pointer text-moss hover:underline"
                                : "text-muted-foreground/60",
                            )}
                          >
                            <Play className="size-2.5" />
                            {formatClock(segment.start_ms)}
                          </button>
                          <span
                            className={cn(
                              "shrink-0 text-[0.71875rem] font-semibold",
                              isInterviewer ? "text-muted-foreground" : "text-foreground",
                            )}
                          >
                            {name}
                          </span>
                          <button
                            type="button"
                            aria-label={`Edit segment ${segment.idx}`}
                            className="ml-auto shrink-0 cursor-pointer p-0.5 text-muted-foreground opacity-100 transition-opacity hover:text-moss sm:opacity-0 sm:group-hover:opacity-100"
                            onClick={() => {
                              setEditingIdx(segment.idx);
                              setEditText(segment.text);
                            }}
                          >
                            <Pencil className="size-3" />
                          </button>
                        </div>
                        {editingIdx === segment.idx ? (
                          <div className="mt-1.5 space-y-2">
                            <Textarea
                              value={editText}
                              onChange={(event) => setEditText(event.target.value)}
                              className="min-h-16 text-[0.8125rem]"
                            />
                            <div className="flex gap-2">
                              <Button
                                size="sm"
                                className="h-7 rounded-full px-3 text-[0.6875rem]"
                                disabled={!editText.trim() || saveSegment.isPending}
                                onClick={() =>
                                  saveSegment.mutate({
                                    idx: segment.idx,
                                    text: editText.trim(),
                                  })
                                }
                              >
                                {saveSegment.isPending ? (
                                  <Loader2 className="size-3 animate-spin" />
                                ) : (
                                  <Check className="size-3" />
                                )}
                                Save correction
                              </Button>
                              <Button
                                variant="ghost"
                                size="sm"
                                className="h-7 rounded-full px-3 text-[0.6875rem]"
                                onClick={() => setEditingIdx(null)}
                              >
                                <X className="size-3" /> Cancel
                              </Button>
                            </div>
                          </div>
                        ) : (
                          <p
                            className={cn(
                              "mt-1 text-[0.8125rem] leading-relaxed",
                              isInterviewer ? "text-muted-foreground" : "text-foreground",
                            )}
                          >
                            {segment.text}
                          </p>
                        )}
                      </div>
                    );
                  })}
                  {segments.some((segment) => segment.edited) && (
                    <p className="px-3.5 pt-2 text-[0.625rem] text-muted-foreground">
                      Segments marked with * were manually corrected; the report
                      discloses this.
                    </p>
                  )}
                </div>
              )}

              {view === "analysis" && (
                <>
                  {interview.analyzing && (
                    <div className="flex items-center gap-2">
                      <AgentLoadingOrb />
                      <span className="shimmer-text text-[0.78125rem] font-medium">
                        Re-reading the transcript…
                      </span>
                    </div>
                  )}
                  {!analysis.summary && !interview.analyzing ? (
                    <div className="rounded-3xl border border-dashed border-border bg-card/60 p-6 text-center">
                      <p className="text-[0.875rem] font-medium text-foreground">
                        No analysis yet
                      </p>
                      <p className="mx-auto mt-1.5 max-w-sm text-[0.75rem] leading-relaxed text-muted-foreground">
                        Run the evidence-bound analysis: themes, key findings and
                        tensions, each backed by verbatim quotes.
                      </p>
                      <Button
                        className="mt-4 rounded-full"
                        disabled={reanalyze.isPending}
                        onClick={() => reanalyze.mutate()}
                      >
                        Analyze the interview
                      </Button>
                    </div>
                  ) : (
                    <>
                      {(analysis.quotes_total ?? 0) > 0 && (
                        <p className="flex items-center gap-1.5 font-mono text-[0.625rem] uppercase tracking-[0.18em] text-moss">
                          <Check className="size-3.5" />
                          {analysis.quotes_verified}/{analysis.quotes_total} quotes
                          {clientReportedSource
                            ? " matched in client-reported transcript"
                            : " verified verbatim"}
                        </p>
                      )}
                      {analysis.summary && (
                        <div className="rounded-2xl border border-border bg-card p-4">
                          <p className="mb-2 text-[0.75rem] font-medium text-foreground">Summary</p>
                          <p className="text-[0.8125rem] leading-relaxed text-foreground">
                            {analysis.summary}
                          </p>
                        </div>
                      )}
                      {(analysis.themes ?? []).map((theme) => (
                        <div key={theme.name} className="rounded-2xl border border-border bg-card p-4">
                          <p className="text-[0.8125rem] font-medium text-foreground">{theme.name}</p>
                          {theme.description && (
                            <p className="mt-1 text-[0.75rem] leading-relaxed text-muted-foreground">
                              {theme.description}
                            </p>
                          )}
                          <div className="mt-3 space-y-2">
                            {theme.quotes.map((quote, quoteIndex) => (
                              <QuoteCard
                                key={`${theme.name}-${quoteIndex}`}
                                quote={quote}
                                speakers={speakers}
                                onJump={jumpToSegment}
                              />
                            ))}
                          </div>
                        </div>
                      ))}
                      {(
                        [
                          ["Key findings", analysis.key_findings],
                          ["Tensions and contradictions", analysis.tensions],
                          ["Hypotheses for future testing", analysis.hypotheses],
                          ["Follow-up questions for the next interview", analysis.followups],
                        ] as const
                      ).map(([label, items]) =>
                        (items ?? []).length > 0 ? (
                          <div key={label} className="rounded-2xl border border-border bg-card p-4">
                            <p className="mb-2 text-[0.75rem] font-medium text-foreground">{label}</p>
                            <ul className="space-y-1.5">
                              {(items ?? []).map((item) => (
                                <li
                                  key={item}
                                  className="flex items-start gap-2 text-[0.78125rem] leading-relaxed text-foreground"
                                >
                                  <span className="mt-[0.4375rem] size-1 shrink-0 rounded-full bg-moss-surface" />
                                  {item}
                                </li>
                              ))}
                            </ul>
                          </div>
                        ) : null,
                      )}
                      <Button
                        variant="outline"
                        className="rounded-full"
                        disabled={interview.analyzing || reanalyze.isPending}
                        onClick={() => reanalyze.mutate()}
                      >
                        <RefreshCw className="size-4" /> Run a fresh analysis
                      </Button>
                    </>
                  )}
                </>
              )}
            </div>
          </main>
        </ResizableWorkspaceSplit>
      )}

      {/* Speaker naming: the model's stable labels get human names. */}
      <Dialog open={speakersOpen} onOpenChange={setSpeakersOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="font-serif text-2xl">Name the speakers</DialogTitle>
            <DialogDescription>
              Labels stay stable; names appear in the transcript, chat and report.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            {Object.keys(speakerDraft).map((label) => (
              <div key={label} className="flex items-center gap-3">
                <span className="w-10 shrink-0 font-mono text-[0.75rem] text-muted-foreground">
                  {label}
                </span>
                <Input
                  value={speakerDraft[label]}
                  onChange={(event) =>
                    setSpeakerDraft((prev) => ({ ...prev, [label]: event.target.value }))
                  }
                  placeholder={label === "S1" ? "Interviewer" : "P1"}
                  className="h-9"
                />
              </div>
            ))}
          </div>
          <Button
            className="rounded-full"
            disabled={saveSpeakers.isPending}
            onClick={() => saveSpeakers.mutate()}
          >
            {saveSpeakers.isPending ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Check className="size-4" />
            )}
            Save names
          </Button>
        </DialogContent>
      </Dialog>

      <ConfirmDeleteDialog
        target={
          removeAudioTarget
            ? {
                title: "Remove the original audio?",
                description: `The recording for “${removeAudioTarget.title}” will be permanently removed. Its transcript and analysis stay available.`,
                action: "Remove audio",
                cancel: "Keep audio",
              }
            : null
        }
        pending={removeAudio.isPending}
        onCancel={() => {
          if (!removeAudio.isPending) setRemoveAudioTarget(null);
        }}
        onConfirm={() => {
          const target = removeAudioTarget;
          if (!target || removeAudio.isPending) return;
          removeAudio.mutate(target.id);
        }}
      />
    </div>
  );
}
