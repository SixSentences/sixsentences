"use client";
import { AiInteractionNotice } from "@/components/ai-interaction-notice";

import { ParticipantInformationEditor } from "@/components/participant-information";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowDown,
  ArrowLeft,
  ArrowUp,
  ArrowUpRight,
  AudioLines,
  Check,
  Copy,
  FolderKanban,
  Link2,
  ListChecks,
  Loader2,
  Mic,
  Plus,
  RotateCcw,
  SendHorizontal,
  ShieldCheck,
  SlidersHorizontal,
  Square,
  Trash2,
  Wand2,
  X,
} from "lucide-react";
import { toast } from "sonner";

import {
  AgentActivityTimeline,
  AgentChangeSequence,
  AgentProposalControls,
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
import LiveSession, {
  liveSessionInterruptionNotice,
  type LiveSessionResult,
} from "@/components/voice/live-session";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { useProjects } from "@/hooks/queries";
import {
  SpecialistStreamError,
  api,
  createSpecialistTurnId,
} from "@/lib/api";
import { formatClock } from "@/lib/format";
import type {
  LiveVoiceSessionConfig,
  VoiceGuideSection,
  VoiceStudy,
  VoiceStudyMessage,
} from "@/lib/types";
import { cn } from "@/lib/utils";

const TONE_LABELS: Record<string, string> = {
  warm: "Warm",
  neutral: "Neutral",
  formal: "Formal",
};

const STARTERS = [
  {
    label: "Draft the guide",
    prompt:
      "Draft a complete interview guide for this study: 4 topics from easy to sensitive, each with one open core question and one concrete-episode probe.",
  },
  {
    label: "Sharpen the probes",
    prompt:
      "Add one concrete-episode probe to every topic that asks for a specific recent situation.",
  },
  {
    label: "Study introduction",
    prompt:
      "Draft a concise introduction describing this study's purpose. Do not invent controller details, a legal basis, or consent approval.",
  },
  {
    label: "Tune the pace",
    prompt:
      "Make the interviewer noticeably more patient and cap sessions at 30 minutes.",
  },
] as const;

const SESSION_DURATION_OPTIONS = [30, 45, 60] as const;
const FIELDWORK_LIMIT_OPTIONS = [30, 60, 120, 300, 600, 1200] as const;
function participantInformationIsReady(study: VoiceStudy): boolean {
  return study.participant_information_ready === true;
}

function voiceStudyLimitsAreValid(study: VoiceStudy, minimumMinutes = 30): boolean {
  return (
    study.max_session_minutes >= minimumMinutes &&
    study.budget_minutes >= study.max_session_minutes
  );
}

type GuideSectionDeleteTarget = {
  studyId: string;
  section: VoiceGuideSection;
  position: number;
  label: string;
};

type GuideSectionDeleteRequest = GuideSectionDeleteTarget & {
  nextSections: VoiceGuideSection[];
  usableSections: VoiceGuideSection[];
};

export default function VoiceStudyBuilderPage() {
  const params = useParams<{ id: string }>();
  const studyId = params.id;
  const queryClient = useQueryClient();
  const chatEndRef = useRef<HTMLDivElement>(null);

  const { data: study, isLoading, error: loadError } = useQuery({
    queryKey: ["voice-study", studyId],
    queryFn: () => api.voiceStudy(studyId),
  });
  const { data: voiceConfig } = useQuery({
    queryKey: ["voice-config"],
    queryFn: api.voiceConfig,
  });
  const minLiveSessionMinutes = voiceConfig?.min_live_session_minutes ?? 30;
  const { data: chatHistory } = useQuery({
    queryKey: ["voice-study-chat", studyId],
    queryFn: () => api.voiceStudyChatHistory(studyId),
  });

  const [view, setView] = useState<"guide" | "setup">("guide");
  const [mobilePane, setMobilePane] = useState<MobileWorkspacePane>("workspace");
  const [sections, setSections] = useState<VoiceGuideSection[]>([]);
  const [sectionDeleteTarget, setSectionDeleteTarget] =
    useState<GuideSectionDeleteTarget | null>(null);
  const [saving, setSaving] = useState<"idle" | "saving" | "saved">("idle");
  const [session, setSession] = useState<LiveVoiceSessionConfig | null>(null);
  const [starting, setStarting] = useState(false);
  const pilotStartInFlight = useRef(false);
  const [settlingSession, setSettlingSession] = useState(false);
  const [pendingPilotFinalize, setPendingPilotFinalize] = useState<{
    session: LiveVoiceSessionConfig;
    result: LiveSessionResult;
  } | null>(null);
  const [lastInterviewId, setLastInterviewId] = useState<string | null>(null);
  const [pilotEndNotice, setPilotEndNotice] = useState<string | null>(null);
  const [inviteLabel, setInviteLabel] = useState("");
  const [invitePasscode, setInvitePasscode] = useState("");
  const [inviteMax, setInviteMax] = useState("25");
  const [chatInput, setChatInput] = useState("");
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
  const hydrated = useRef(false);
  const saveTimer = useRef<number | null>(null);
  const sectionDeleteLockRef = useRef<VoiceGuideSection | null>(null);
  const abandoningSessionIdsRef = useRef(new Set<string>());

  const [model, pickModel] = usePrivateModelPreference("six:study-model");
  const [autoApply, setAutoApply] = useState(true);
  useEffect(() => {
    setAutoApply(localStorage.getItem("six:study-autoapply") !== "0");
  }, []);
  const toggleAutoApply = () => {
    setAutoApply((current) => {
      localStorage.setItem("six:study-autoapply", current ? "0" : "1");
      return !current;
    });
  };

  useEffect(() => {
    if (study && !hydrated.current) {
      setSections(study.guide.sections.map((section) => ({ ...section })));
      hydrated.current = true;
    }
  }, [study]);
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chatHistory, pendingQuestion]);

  const persistSections = useCallback(
    (next: VoiceGuideSection[]) => {
      setSections(next);
      if (saveTimer.current) window.clearTimeout(saveTimer.current);
      setSaving("saving");
      saveTimer.current = window.setTimeout(async () => {
        const usable = next.filter((section) => section.question.trim().length >= 3);
        if (usable.length === 0) {
          setSaving("idle");
          return;
        }
        try {
          const updated = await api.voiceStudyUpdate(studyId, { sections: usable });
          queryClient.setQueryData(["voice-study", studyId], (current: VoiceStudy | undefined) =>
            current ? { ...current, guide: updated.guide, guide_version: updated.guide_version } : updated,
          );
          setSaving("saved");
          window.setTimeout(() => setSaving("idle"), 1_600);
        } catch (error) {
          setSaving("idle");
          toast.error(error instanceof Error ? error.message : "Save failed.");
        }
      }, 900);
    },
    [queryClient, studyId],
  );

  const removeSection = useMutation({
    mutationFn: (request: GuideSectionDeleteRequest) =>
      api.voiceStudyUpdate(request.studyId, { sections: request.usableSections }),
    onMutate: (request) => {
      if (saveTimer.current) {
        window.clearTimeout(saveTimer.current);
        saveTimer.current = null;
      }
      setSections(request.nextSections);
      setSaving("saving");
    },
    onSuccess: (updated, request) => {
      queryClient.setQueryData(
        ["voice-study", request.studyId],
        (current: VoiceStudy | undefined) =>
          current
            ? {
                ...current,
                guide: updated.guide,
                guide_version: updated.guide_version,
              }
            : updated,
      );
      setSectionDeleteTarget((current) =>
        current?.studyId === request.studyId && current.section === request.section
          ? null
          : current,
      );
      setSaving("saved");
      window.setTimeout(() => setSaving("idle"), 1_600);
    },
    onError: (error, request) => {
      setSections((current) => {
        const deletionIsStillCurrent =
          current.length === request.nextSections.length &&
          current.every((section, index) => section === request.nextSections[index]);
        if (!deletionIsStillCurrent) return current;
        const restored = [...current];
        restored.splice(request.position, 0, request.section);
        return restored;
      });
      setSaving("idle");
      toast.error(error instanceof Error ? error.message : "Removing the topic failed.");
    },
    onSettled: (_, __, request) => {
      if (sectionDeleteLockRef.current === request.section) {
        sectionDeleteLockRef.current = null;
      }
    },
  });

  const updateSetting = useMutation({
    mutationFn: (body: Parameters<typeof api.voiceStudyUpdate>[1]) =>
      api.voiceStudyUpdate(studyId, body),
    onSuccess: (updated) => {
      queryClient.setQueryData(["voice-study", studyId], (current: VoiceStudy | undefined) =>
        current
          ? { ...current, ...updated, sessions: current.sessions, invites: current.invites }
          : updated,
      );
      void queryClient.invalidateQueries({ queryKey: ["voice-studies"] });
      void queryClient.invalidateQueries({ queryKey: ["project-workspace"] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Save failed."),
  });

  const { data: projects } = useProjects();
  const adoptStudy = useCallback(
    (incoming: VoiceStudy) => {
      queryClient.setQueryData(["voice-study", studyId], (current: VoiceStudy | undefined) =>
        current
          ? { ...current, ...incoming, sessions: current.sessions, invites: current.invites }
          : current,
      );
      setSections(incoming.guide.sections.map((section) => ({ ...section })));
    },
    [queryClient, studyId],
  );

  const chat = useMutation({
    mutationFn: (question: string) => {
      const turnId = createSpecialistTurnId();
      return api.voiceStudyChatStream(
        studyId,
        question,
        model,
        autoApply,
        recordAgentEvent,
        beginLocalTurn(turnId),
      );
    },
    onMutate: (question) => {
      setAgentWorking(true);
      startAgentTurn();
      setPendingQuestion(question);
    },
    onSuccess: async (result) => {
      // auto-applied edits land immediately; staged proposals wait in the
      // chat for an explicit apply
      adoptStudy(result.study);
      try {
        await queryClient.invalidateQueries({ queryKey: ["voice-study-chat", studyId] });
      } finally {
        handoffAgentTurn();
        finishTurn();
        setAgentWorking(false);
        setPendingQuestion(null);
      }
    },
    onError: async (error) => {
      await queryClient.refetchQueries({ queryKey: ["voice-study-chat", studyId] });
      handoffAgentTurn();
      finishTurn();
      setAgentWorking(false);
      setPendingQuestion(null);
      if (!(error instanceof SpecialistStreamError && error.kind === "cancelled")) {
        toast.error(error instanceof Error ? error.message : "That didn't work.");
      }
    },
  });
  const applyProposal = useMutation({
    mutationFn: (messageId: number) => api.voiceStudyApplyProposal(studyId, messageId),
    onSuccess: (result) => {
      adoptStudy(result.study);
      void queryClient.invalidateQueries({ queryKey: ["voice-study-chat", studyId] });
      toast.success("Changes applied.");
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Applying failed."),
  });

  const createInvite = useMutation({
    mutationFn: () => {
      if (!study || !voiceStudyLimitsAreValid(study, minLiveSessionMinutes)) {
        throw new Error("Raise the fieldwork limit to at least the session cap first.");
      }
      return api.voiceInviteCreate(studyId, {
        label: inviteLabel.trim(),
        passcode: invitePasscode.trim(),
        max_sessions: Number(inviteMax),
      });
    },
    onSuccess: () => {
      setInviteLabel("");
      setInvitePasscode("");
      void queryClient.invalidateQueries({ queryKey: ["voice-study", studyId] });
      toast.success("Participation link created.");
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Could not create the link."),
  });
  const toggleInvite = useMutation({
    mutationFn: (input: { id: string; active: boolean }) => {
      if (
        input.active &&
        (!study || !voiceStudyLimitsAreValid(study, minLiveSessionMinutes))
      ) {
        throw new Error("Raise the fieldwork limit to at least the session cap first.");
      }
      return api.voiceInviteUpdate(input.id, input.active);
    },
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: ["voice-study", studyId] }),
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Updating the link failed."),
  });

  const createParticipationLink = () => createInvite.mutate();

  const setInviteActive = (id: string, active: boolean) => {
    toggleInvite.mutate({ id, active });
  };

  const copyInviteUrl = (inviteId: string) => {
    const url = `${window.location.origin}/talk/${inviteId}`;
    void navigator.clipboard.writeText(url);
    toast.success("Link copied. Share it with your participants.");
  };

  const startPilot = useCallback(async () => {
    // Block repeated activation before React commits the disabled button.
    if (pilotStartInFlight.current || session || settlingSession || pendingPilotFinalize) return;
    if (!study || !voiceStudyLimitsAreValid(study, minLiveSessionMinutes)) {
      toast.error("Raise the fieldwork limit to at least the session cap first.");
      return;
    }
    pilotStartInFlight.current = true;
    setStarting(true);
    try {
      const config = await api.voiceSessionStart(studyId);
      setSession(config);
      setLastInterviewId(null);
      setPilotEndNotice(null);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not start.");
    } finally {
      pilotStartInFlight.current = false;
      setStarting(false);
    }
  }, [
    minLiveSessionMinutes,
    pendingPilotFinalize,
    queryClient,
    session,
    settlingSession,
    study,
    studyId,
  ]);

  const finalizePilot = useCallback(
    async (active: LiveVoiceSessionConfig, result: LiveSessionResult) => {
      setPendingPilotFinalize({ session: active, result });
      setSettlingSession(true);
      try {
        const settled = await api.voiceSessionFinalize(active.id, {
          turns: result.turns,
          duration_ms: result.duration_ms,
          ...(result.audio_base64 ? { audio_base64: result.audio_base64 } : {}),
          aborted: result.aborted,
        });
        setPendingPilotFinalize(null);
        const saved = settled.status === "completed" && Boolean(settled.interview_id);
        const notice = liveSessionInterruptionNotice(result.endReason, saved, false);
        setPilotEndNotice(notice);
        if (saved && settled.interview_id) {
          setLastInterviewId(settled.interview_id);
          if (notice) toast.info(notice);
          else toast.success("Session saved. The analysis is running.");
        } else if (!result.aborted) {
          toast.info(notice ?? "No interview transcript was saved.");
        }
      } catch (error) {
        toast.error(
          error instanceof Error
            ? `${error.message} Retry saving the pilot before starting another one.`
            : "Saving the session failed. Retry before starting another pilot.",
        );
      } finally {
        setSettlingSession(false);
        void queryClient.invalidateQueries({ queryKey: ["voice-study", studyId] });
        void queryClient.invalidateQueries({ queryKey: ["interviews"] });
      }
    },
    [queryClient, studyId],
  );

  const handleFinished = useCallback(
    (result: LiveSessionResult) => {
      const active = session;
      setSession(null);
      if (!active) return;
      void finalizePilot(active, result);
    },
    [finalizePilot, session],
  );

  const handleLiveError = useCallback(
    (message: string) => {
      const active = session;
      setSession(null);
      toast.error(message);
      if (!active || abandoningSessionIdsRef.current.has(active.id)) return;
      abandoningSessionIdsRef.current.add(active.id);
      void finalizePilot(active, {
        turns: [],
        duration_ms: 0,
        aborted: true,
      });
    },
    [finalizePilot, session],
  );

  const messages: VoiceStudyMessage[] = chatHistory ?? [];
  const persistedAgentTimelines = messages
    .filter((message) => message.role === "assistant")
    .map((message) => message.payload.agent_events ?? []);
  const { checking: checkingAgentTurn, recovering: recoveringAgentTurn } =
    useDurableSpecialistTurn<{ study?: VoiceStudy }>({
      resourceKind: "interview-study",
      resourceId: studyId,
      enabled: chatHistory !== undefined,
      persistedTurnIds: agentTurnIdsFromTimelines(persistedAgentTimelines),
      onStarted: (turn) => {
        recoverTurn(turn.turn_id);
        startAgentTurn();
        setAgentWorking(true);
        setPendingQuestion(null);
      },
      onEvent: recordAgentEvent,
      onTerminal: async (result, turn) => {
        if (result?.study) adoptStudy(result.study);
        await Promise.allSettled([
          queryClient.refetchQueries({ queryKey: ["voice-study-chat", studyId] }),
          queryClient.invalidateQueries({ queryKey: ["voice-study", studyId] }),
          queryClient.invalidateQueries({ queryKey: ["voice-studies"] }),
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
            : "The running study update could not be recovered yet.",
        );
      },
    });
  const specialistBusy =
    agentWorking
    || Boolean(activeTurnId)
    || chatHistory === undefined
    || checkingAgentTurn
    || recoveringAgentTurn;
  const queuedChat = useAgentTurnQueue<string>({
    working: specialistBusy,
    run: (question) => chat.mutate(question),
  });
  const submitStudyChat = (question: string) => {
    queuedChat.submit(question, question);
    return true;
  };
  const {
    clearChat,
    clearing,
    confirmationOpen,
    setConfirmationOpen,
    confirmClearChat,
    clearChatTriggerRef,
  } = useSpecialistChatReset({
    resourceKind: "interview-study",
    resourceId: studyId,
    queryKey: ["voice-study-chat", studyId],
    hasHistory: messages.length > 0,
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
        title="Study unavailable"
        error={loadError}
        fallback="This study could not be loaded."
        backHref="/interviews"
        backLabel="Back to interviews"
      />
    );
  }
  if (isLoading || !study) {
    return (
      <div className="grid min-h-0 flex-1 place-items-center">
        <Loader2 className="size-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const configured = voiceConfig?.configured ?? true;
  const publicSpokenAvailable = voiceConfig?.public_spoken_available ?? false;
  const maxLiveSessionMinutes = voiceConfig?.max_live_session_minutes;
  const currentSessionCapExceedsLimit =
    typeof maxLiveSessionMinutes === "number" &&
    study.max_session_minutes > maxLiveSessionMinutes;
  const currentSessionCapExceedsFieldworkLimit =
    study.max_session_minutes > study.budget_minutes;
  const currentSessionCapBelowMinimum =
    study.max_session_minutes < minLiveSessionMinutes;
  const currentSessionCapIsUnavailable =
    currentSessionCapExceedsLimit ||
    currentSessionCapExceedsFieldworkLimit ||
    currentSessionCapBelowMinimum;
  const studyLimitsAreValid = voiceStudyLimitsAreValid(study, minLiveSessionMinutes);
  const sessionDurationOptions = Array.from(
    new Set([
      ...SESSION_DURATION_OPTIONS.filter(
        (minutes) =>
          minutes >= minLiveSessionMinutes &&
          minutes <= study.budget_minutes &&
          (maxLiveSessionMinutes === undefined || minutes <= maxLiveSessionMinutes),
      ),
      ...(currentSessionCapIsUnavailable ? [] : [study.max_session_minutes]),
    ]),
  ).sort((left, right) => left - right);
  const currentFieldworkLimitIsBelowSessionCap =
    study.budget_minutes < study.max_session_minutes;
  const fieldworkLimitOptions = Array.from(
    new Set([
      ...FIELDWORK_LIMIT_OPTIONS.filter(
        (minutes) => minutes >= study.max_session_minutes,
      ),
      ...(currentFieldworkLimitIsBelowSessionCap ? [] : [study.budget_minutes]),
    ]),
  ).sort((left, right) => left - right);
  const sessions = study.sessions ?? [];
  const completed = sessions.filter((item) => item.status === "completed");

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-background md:rounded-t-2xl">
      <SpecialistChatResetDialog
        open={confirmationOpen}
        clearing={clearing}
        returnFocusRef={clearChatTriggerRef}
        onOpenChange={setConfirmationOpen}
        onConfirm={() => void confirmClearChat()}
      />
      <ConfirmDeleteDialog
        target={
          sectionDeleteTarget
            ? {
                title: `Remove “${sectionDeleteTarget.label}” from the guide?`,
                description:
                  "This permanently removes the topic, its core question and all probes from the interview guide. Existing recorded sessions remain unchanged.",
                action: "Remove topic",
                cancel: "Keep topic",
              }
            : null
        }
        pending={removeSection.isPending || saving === "saving"}
        onCancel={() => {
          if (!removeSection.isPending && sectionDeleteLockRef.current === null) {
            setSectionDeleteTarget(null);
          }
        }}
        onConfirm={() => {
          const target = sectionDeleteTarget;
          if (
            !target ||
            removeSection.isPending ||
            saving === "saving" ||
            sectionDeleteLockRef.current !== null
          ) {
            return;
          }
          if (target.studyId !== studyId) {
            setSectionDeleteTarget(null);
            toast.error("This topic belongs to a study that is no longer open.");
            return;
          }
          const position = sections.findIndex((section) => section === target.section);
          if (position < 0 || sections.length <= 1) {
            setSectionDeleteTarget(null);
            toast.error("This topic changed before it could be removed. Review the guide and try again.");
            return;
          }
          const nextSections = sections.filter((_, index) => index !== position);
          const usableSections = nextSections.filter(
            (section) => section.question.trim().length >= 3,
          );
          if (usableSections.length === 0) {
            setSectionDeleteTarget(null);
            toast.error("Add a complete core question to another topic before removing this one.");
            return;
          }
          sectionDeleteLockRef.current = target.section;
          removeSection.mutate({
            ...target,
            position,
            nextSections,
            usableSections,
          });
        }}
      />
      {session && (
        <LiveSession
          config={session}
          onFinished={handleFinished}
          onError={handleLiveError}
        />
      )}

      <header className="flex min-h-14 shrink-0 items-center justify-between gap-2 border-b border-border px-3 py-2 sm:gap-4 sm:px-5 lg:px-7">
        <div className="flex min-w-0 flex-1 items-center gap-3">
          <Button asChild variant="ghost" size="icon" className="size-8 rounded-full">
            <Link href="/interviews"><ArrowLeft className="size-4" /></Link>
          </Button>
          <div className="min-w-0 flex-1">
            <InlineTitle
              value={study.title}
              onCommit={(next) => updateSetting.mutate({ title: next })}
              ariaLabel="Study title"
              placeholder="Untitled study"
            />
            <p className="font-mono text-[0.59375rem] uppercase tracking-[0.18em] text-muted-foreground">
              {study.language} · guide v{study.guide_version} · {completed.length}{" "}
              {completed.length === 1 ? "session" : "sessions"}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <span
            className={cn(
              "font-mono text-[0.625rem] uppercase tracking-[0.18em] transition-opacity",
              saving === "idle" ? "opacity-0" : "opacity-100",
              saving === "saved" ? "text-moss" : "text-muted-foreground",
            )}
          >
            {saving === "saving" ? "Saving…" : "Saved"}
          </span>
          <Button
            className="h-9 rounded-full px-3"
            disabled={
              starting ||
              Boolean(session) ||
              settlingSession ||
              currentSessionCapExceedsLimit ||
              !studyLimitsAreValid ||
              (!pendingPilotFinalize && !configured)
            }
            onClick={() => {
              if (pendingPilotFinalize) {
                void finalizePilot(
                  pendingPilotFinalize.session,
                  pendingPilotFinalize.result,
                );
                return;
              }
              void startPilot();
            }}
            aria-label={
              pendingPilotFinalize ? "Retry saving pilot interview" : "Start pilot interview"
            }
            title={
              currentSessionCapExceedsLimit
                ? `Choose a session cap of ${maxLiveSessionMinutes} minutes or less first.`
                : !studyLimitsAreValid
                  ? "Raise the fieldwork limit to at least the session cap first."
                : undefined
            }
          >
            {starting || settlingSession ? (
              <Loader2 className="size-4 animate-spin" />
            ) : pendingPilotFinalize ? (
              <RotateCcw className="size-4" />
            ) : (
              <Mic className="size-4" />
            )}
            <span className="hidden sm:inline">
              {settlingSession
                ? "Saving pilot…"
                : pendingPilotFinalize
                  ? "Retry saving"
                  : "Start pilot"}
            </span>
          </Button>
        </div>
      </header>

      {pilotEndNotice && (
        <p role="status" className="border-b border-border bg-secondary/40 px-5 py-3 text-sm text-foreground">
          {pilotEndNotice}
        </p>
      )}

      <ResizableWorkspaceSplit
        storageKey="six:interview-study-workspace-split"
        label="Resize study assistant and study"
        primaryMinPx={400}
        secondaryMinPx={620}
        mobileSwitch={(
          <MobileWorkspaceSwitch
            value={mobilePane}
            onChange={setMobilePane}
            workspaceLabel="Study"
          />
        )}
      >
        {/* the study designer: it edits through the same validated rules as
            the manual editor, so it can never overreach */}
        <aside
          className={cn(
            "min-h-0 flex-col border-b border-border bg-card/45 group-data-[workspace-layout=split]/workspace:!flex group-data-[workspace-layout=split]/workspace:border-b-0",
            mobilePane === "agent" ? "flex" : "hidden",
          )}
        >
          <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-5">
            {messages.length === 0 && !specialistBusy && (
              <div className="flex h-full flex-col items-center justify-center gap-2 px-4 text-center">
                <p className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
                  Your study designer
                </p>
                <p className="max-w-[17rem] text-[0.78125rem] leading-relaxed text-muted-foreground">
                  Describe your study and it drafts the guide, tunes the
                  interviewer and writes the consent texts. Then talk to your
                  own interviewer with Start pilot before anyone else does.
                </p>
              </div>
            )}
            {messages.map((message) => (
              <div key={message.id} className={cn("flex", message.role === "user" && "justify-end")}>
                <div
                  className={cn(
                    "text-[0.875rem] leading-relaxed",
                    message.role === "user"
                      ? "max-w-[85%] rounded-2xl rounded-br-md bg-moss-surface px-3.5 py-2.5 text-ivory"
                      : "max-w-full text-foreground",
                  )}
                >
                  {message.role === "assistant" ? (
                    <SpecialistCompletedTurn
                      kind="interview-study"
                      events={message.payload.agent_events}
                      answer={message.content}
                      artifacts={message.payload.artifacts}
                    >
                      {(message.payload.proposals?.length ?? 0) > 0 &&
                      !message.payload.resolved ? (
                        (message.payload.agent_events?.length ?? 0) > 0 ? (
                          <AgentProposalControls
                            count={message.payload.proposals?.length ?? 0}
                            applying={applyProposal.isPending}
                            onApply={() => applyProposal.mutate(message.id)}
                          />
                        ) : (
                          <AgentChangeSequence
                            changes={message.payload.proposals ?? []}
                            pending
                            applying={applyProposal.isPending}
                            onApply={() => applyProposal.mutate(message.id)}
                          />
                        )
                      ) : null}
                      {(message.payload.actions?.length ?? 0) > 0 &&
                      (message.payload.agent_events?.length ?? 0) === 0 ? (
                        <AgentChangeSequence changes={message.payload.actions ?? []} />
                      ) : null}
                      <WorkspaceActionList
                        actions={message.payload.workspace_actions}
                        compact
                      />
                    </SpecialistCompletedTurn>
                  ) : (
                    <div className="whitespace-pre-wrap">{message.content}</div>
                  )}
                </div>
              </div>
            ))}
            <AgentTimelineHandoffs
              handoffs={agentTimelineHandoffs}
              persistedTimelines={persistedAgentTimelines}
            />
            {pendingQuestion &&
              !messages
                .slice(-2)
                .some(
                  (message) => message.role === "user" && message.content === pendingQuestion,
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
            {messages.length === 0 && !specialistBusy && (
              <div className="mb-2 flex max-w-full gap-1.5 overflow-x-auto pb-0.5">
                {STARTERS.map((starter) => (
                  <button
                    key={starter.label}
                    type="button"
                    onClick={() => submitStudyChat(starter.prompt)}
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
                if (submitStudyChat(question)) setChatInput("");
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
                <button
                  type="button"
                  title={
                    autoApply
                      ? "Auto-apply is on: changes land immediately"
                      : "Auto-apply is off: changes stage as proposals"
                  }
                  onClick={toggleAutoApply}
                  className={cn(
                    "grid size-9 cursor-pointer place-items-center rounded-full border transition-colors",
                    autoApply
                      ? "border-moss/50 bg-accent text-moss"
                      : "border-border text-muted-foreground hover:text-foreground",
                  )}
                >
                  <Wand2 className="size-3.5" />
                </button>
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
                    if (submitStudyChat(question)) setChatInput("");
                  }
                }}
                placeholder="Draft, refine, configure…"
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
            "@container/study min-h-0 min-w-0 overflow-y-auto group-data-[workspace-layout=split]/workspace:!block",
            mobilePane === "workspace" ? "block" : "hidden",
          )}
        >
          <div className="sticky top-0 z-10 flex flex-wrap items-center justify-between gap-3 border-b border-border bg-background/95 px-5 py-3 backdrop-blur lg:px-7">
            <div className="flex w-fit items-center gap-1 rounded-full bg-secondary/65 p-1">
              {(
                [
                  ["guide", ListChecks, "Guide"],
                  ["setup", SlidersHorizontal, "Setup"],
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
            {lastInterviewId && (
              <Button
                asChild
                variant="outline"
                size="sm"
                className="h-8 rounded-full border-moss/40 px-3 text-[0.71875rem]"
              >
                <Link href={`/interviews/${lastInterviewId}`}>
                  Open the last session&apos;s interview <ArrowUpRight className="size-3.5" />
                </Link>
              </Button>
            )}
          </div>

          <div className="min-w-0 w-full px-4 py-6 @min-[44rem]/study:px-7">
            {view === "guide" && (
              <>
                <div className="mb-3 flex w-fit items-center gap-1 rounded-full bg-secondary/65 p-1">
                  {(
                    [
                      ["guided", "Guided"],
                      ["iterative", "Iterative"],
                    ] as const
                  ).map(([value, label]) => (
                    <button
                      key={value}
                      type="button"
                      onClick={() => {
                        if (study.mode !== value) updateSetting.mutate({ mode: value });
                      }}
                      className={cn(
                        "h-8 cursor-pointer rounded-full px-3.5 text-[0.71875rem] font-medium transition-colors",
                        study.mode === value
                          ? "bg-card text-foreground shadow-sm"
                          : "text-muted-foreground hover:text-foreground",
                      )}
                    >
                      {label}
                    </button>
                  ))}
                </div>
                <p className="mb-4 max-w-3xl text-[0.78125rem] leading-relaxed text-muted-foreground">
                  {study.mode === "iterative"
                    ? "Iterative mode: the interviewer asks exactly one opening question, then derives every follow-up from what the participant actually says. Probes become emergency hooks used only when the conversation stalls."
                    : "The agent works through these topics in a natural flow and probes with the participant's own words. Wordings are guardrails, not scripts, and the interviewer never sees your hypothesis."}
                </p>
                <div className="space-y-4">
                  {(study.mode === "iterative" ? sections.slice(0, 1) : sections).map((section, index) => (
                    <div key={index} className="rounded-2xl border border-border bg-card p-4">
                      <div className="flex items-center gap-2">
                        <Input
                          value={section.title}
                          onChange={(event) =>
                            persistSections(
                              sections.map((item, itemIndex) =>
                                itemIndex === index
                                  ? { ...item, title: event.target.value }
                                  : item,
                              ),
                            )
                          }
                          placeholder={`Topic ${index + 1}`}
                          className="h-8 max-w-56 border-transparent bg-secondary/50 text-[0.78125rem] font-medium"
                        />
                        <span className="ml-auto flex items-center gap-0.5">
                          <button
                            type="button"
                            aria-label="Move up"
                            disabled={index === 0}
                            className="cursor-pointer rounded p-1 text-muted-foreground hover:text-foreground disabled:opacity-30"
                            onClick={() => {
                              const next = [...sections];
                              [next[index - 1], next[index]] = [next[index], next[index - 1]];
                              persistSections(next);
                            }}
                          >
                            <ArrowUp className="size-3.5" />
                          </button>
                          <button
                            type="button"
                            aria-label="Move down"
                            disabled={index === sections.length - 1}
                            className="cursor-pointer rounded p-1 text-muted-foreground hover:text-foreground disabled:opacity-30"
                            onClick={() => {
                              const next = [...sections];
                              [next[index], next[index + 1]] = [next[index + 1], next[index]];
                              persistSections(next);
                            }}
                          >
                            <ArrowDown className="size-3.5" />
                          </button>
                          <button
                            type="button"
                            aria-label="Remove topic"
                            disabled={sections.length <= 1 || removeSection.isPending}
                            className="cursor-pointer rounded p-1 text-muted-foreground hover:text-destructive disabled:opacity-30"
                            onClick={() =>
                              setSectionDeleteTarget({
                                studyId,
                                section,
                                position: index,
                                label: section.title.trim() || `Topic ${index + 1}`,
                              })
                            }
                          >
                            <Trash2 className="size-3.5" />
                          </button>
                        </span>
                      </div>
                      <div className="mt-3 grid min-w-0 items-start gap-x-6 gap-y-3 @min-[44rem]/study:grid-cols-2">
                      <Textarea
                        value={section.question}
                        onChange={(event) =>
                          persistSections(
                            sections.map((item, itemIndex) =>
                              itemIndex === index
                                ? { ...item, question: event.target.value }
                                : item,
                            ),
                          )
                        }
                        placeholder="The core question for this topic…"
                        className="min-h-28 resize-none text-[0.8125rem]"
                      />
                      <div>
                      <div className="space-y-1.5">
                        {section.probes.map((probe, probeIndex) => (
                          <div key={probeIndex} className="flex items-center gap-2">
                            <span className="shrink-0 font-mono text-[0.59375rem] uppercase tracking-[0.18em] text-moss-soft">
                              probe
                            </span>
                            <Input
                              value={probe}
                              onChange={(event) =>
                                persistSections(
                                  sections.map((item, itemIndex) =>
                                    itemIndex === index
                                      ? {
                                          ...item,
                                          probes: item.probes.map((p, pIndex) =>
                                            pIndex === probeIndex ? event.target.value : p,
                                          ),
                                        }
                                      : item,
                                  ),
                                )
                              }
                              className="h-7 flex-1 text-[0.75rem]"
                            />
                            <button
                              type="button"
                              aria-label="Remove probe"
                              className="cursor-pointer p-1 text-muted-foreground hover:text-destructive"
                              onClick={() =>
                                persistSections(
                                  sections.map((item, itemIndex) =>
                                    itemIndex === index
                                      ? {
                                          ...item,
                                          probes: item.probes.filter(
                                            (_, pIndex) => pIndex !== probeIndex,
                                          ),
                                        }
                                      : item,
                                  ),
                                )
                              }
                            >
                              <X className="size-3" />
                            </button>
                          </div>
                        ))}
                      </div>
                      <div className="mt-3 flex items-center justify-between">
                        <button
                          type="button"
                          disabled={section.probes.length >= 5}
                          className="cursor-pointer text-[0.6875rem] text-moss hover:underline disabled:opacity-40"
                          onClick={() =>
                            persistSections(
                              sections.map((item, itemIndex) =>
                                itemIndex === index
                                  ? { ...item, probes: [...item.probes, ""] }
                                  : item,
                              ),
                            )
                          }
                        >
                          + Add probe
                        </button>
                        <label className="flex cursor-pointer items-center gap-2 text-[0.6875rem] text-muted-foreground">
                          Must cover
                          <Switch
                            checked={section.must_cover}
                            onCheckedChange={(checked) =>
                              persistSections(
                                sections.map((item, itemIndex) =>
                                  itemIndex === index
                                    ? { ...item, must_cover: checked }
                                    : item,
                                ),
                              )
                            }
                          />
                        </label>
                      </div>
                      </div>
                      </div>
                    </div>
                  ))}
                  {study.mode === "iterative" ? (
                    sections.length > 1 && (
                      <p className="rounded-2xl border border-dashed border-border px-4 py-3 text-[0.71875rem] leading-relaxed text-muted-foreground">
                        {sections.length - 1} more{" "}
                        {sections.length - 1 === 1 ? "topic is" : "topics are"}{" "}
                        parked. Iterative interviews use only the opening
                        question above; switch back to Guided to see and use
                        them again.
                      </p>
                    )
                  ) : (
                    <Button
                      variant="outline"
                      className="h-11 w-full rounded-2xl border-dashed"
                      disabled={sections.length >= 8}
                      onClick={() =>
                        persistSections([
                          ...sections,
                          { title: "", question: "", probes: [], must_cover: false },
                        ])
                      }
                    >
                      <Plus className="size-4" /> Add topic
                    </Button>
                  )}
                </div>
              </>
            )}

            {view === "setup" && (
              <div className="space-y-4">
                {!configured && (
                  <div className="rounded-2xl border border-amber-500/30 bg-amber-50 p-4 text-[0.75rem] leading-relaxed text-amber-800 dark:bg-amber-300/10 dark:text-amber-200">
                    Live interviews are not configured on this workspace yet.
                    The deployment operator must configure a compatible live-interview provider.
                  </div>
                )}
                <div className="grid min-w-0 items-start gap-4 @min-[56rem]/study:grid-cols-2 [&>*]:min-w-0">
                  <div className="min-w-0 space-y-4">
                  <div className="@container/study-controls min-w-0 rounded-2xl border border-border bg-card p-4">
                    <p className="mb-1.5 flex items-center gap-2 text-[0.8125rem] font-medium text-foreground">
                      <SlidersHorizontal className="size-4 text-moss" /> Interviewer
                    </p>
                    <p className="mb-3 text-[0.6875rem] leading-relaxed text-muted-foreground">
                      How she sounds, paces and structures the conversations.
                    </p>
                    {!publicSpokenAvailable && (
                      <p className="mb-3 rounded-xl border border-border bg-secondary/45 px-3 py-2 text-[0.6875rem] leading-relaxed text-muted-foreground">
                        Voice controls apply to your authenticated spoken pilot. Public
                        participation links currently open the written AI interview.
                      </p>
                    )}
                    <div className="space-y-3">
                      <div className="space-y-1.5">
                        <Label className="text-[0.6875rem]">Tone</Label>
                        <div className="flex flex-wrap gap-1.5">
                          {(voiceConfig?.tones ?? ["warm", "neutral", "formal"]).map((tone) => (
                            <button
                              key={tone}
                              type="button"
                              onClick={() => updateSetting.mutate({ tone })}
                              className={cn(
                                "cursor-pointer rounded-full border px-3 py-1.5 text-[0.71875rem] transition-colors",
                                study.tone === tone
                                  ? "border-moss bg-accent text-foreground"
                                  : "border-border text-muted-foreground hover:border-moss/40",
                              )}
                            >
                              {TONE_LABELS[tone] ?? tone}
                            </button>
                          ))}
                        </div>
                      </div>
                      <div className="grid min-w-0 grid-cols-1 gap-3 @min-[28rem]/study-controls:grid-cols-2 [&>*]:min-w-0">
                        <div className="space-y-1.5">
                          <Label className="text-[0.6875rem]">Voice</Label>
                          <Select
                            value={study.voice}
                            onValueChange={(voice) => updateSetting.mutate({ voice })}
                          >
                            <SelectTrigger className="h-9 w-full"><SelectValue /></SelectTrigger>
                            <SelectContent>
                              {(voiceConfig?.voices ?? [study.voice]).map((voice) => (
                                <SelectItem key={voice} value={voice}>{voice}</SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        </div>
                        <div className="space-y-1.5">
                          <Label className="text-[0.6875rem]">Language</Label>
                          <Select
                            value={study.language}
                            onValueChange={(language) =>
                              updateSetting.mutate({ language: language as "de" | "en" })
                            }
                          >
                            <SelectTrigger className="h-9 w-full"><SelectValue /></SelectTrigger>
                            <SelectContent>
                              <SelectItem value="de">Deutsch</SelectItem>
                              <SelectItem value="en">English</SelectItem>
                            </SelectContent>
                          </Select>
                        </div>
                      </div>
                      <div className="space-y-1.5">
                        <Label className="text-[0.6875rem]">
                          Patience · {(study.patience_ms / 1000).toFixed(1)}s of silence before she answers
                        </Label>
                        <input
                          type="range"
                          min={600}
                          max={3000}
                          step={100}
                          key={`patience-${study.patience_ms}`}
                          defaultValue={study.patience_ms}
                          onMouseUp={(event) =>
                            updateSetting.mutate({
                              patience_ms: Number((event.target as HTMLInputElement).value),
                            })
                          }
                          onTouchEnd={(event) =>
                            updateSetting.mutate({
                              patience_ms: Number((event.target as HTMLInputElement).value),
                            })
                          }
                          className="w-full accent-[#33544c]"
                        />
                      </div>
                      <div className="grid min-w-0 grid-cols-1 gap-3 @min-[28rem]/study-controls:grid-cols-2 [&>*]:min-w-0">
                        <div className="space-y-1.5">
                          <Label className="text-[0.6875rem]">Session cap</Label>
                          <Select
                            value={String(study.max_session_minutes)}
                            onValueChange={(value) =>
                              updateSetting.mutate({ max_session_minutes: Number(value) })
                            }
                          >
                            <SelectTrigger className="h-9 w-full"><SelectValue /></SelectTrigger>
                            <SelectContent>
                              {currentSessionCapIsUnavailable && (
                                <SelectItem
                                  value={String(study.max_session_minutes)}
                                  disabled
                                >
                                  {study.max_session_minutes} min · {currentSessionCapExceedsLimit
                                    ? "above current limit"
                                    : currentSessionCapExceedsFieldworkLimit
                                      ? "above fieldwork limit"
                                      : "below current minimum"}
                                </SelectItem>
                              )}
                              {sessionDurationOptions.map((minutes) => (
                                <SelectItem key={minutes} value={String(minutes)}>
                                  {minutes} min
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                          {typeof maxLiveSessionMinutes === "number" && (
                            <p
                              className={cn(
                                "text-[0.65625rem] leading-relaxed text-muted-foreground",
                                currentSessionCapExceedsLimit && "text-amber-700 dark:text-amber-300",
                              )}
                            >
                              {currentSessionCapExceedsLimit
                                ? `This study still has ${study.max_session_minutes} minutes saved. The current live-session limit is ${maxLiveSessionMinutes}; choose a lower cap before the next session.`
                                : `The connected API currently allows spoken sessions up to ${maxLiveSessionMinutes} minutes. Connection renewals happen automatically.`}
                            </p>
                          )}
                        </div>
                        <div className="space-y-1.5">
                          <Label className="text-[0.6875rem]">Keep audio</Label>
                          <div className="flex min-h-9 flex-wrap items-center gap-y-2">
                            <Switch
                              checked={study.retention === "keep"}
                              onCheckedChange={(checked) =>
                                updateSetting.mutate({
                                  retention: checked ? "keep" : "transcript_only",
                                })
                              }
                            />
                            <span className="ml-2 text-[0.6875rem] text-muted-foreground">
                              {study.retention === "keep" ? "recording stored" : "transcript only"}
                            </span>
                          </div>
                        </div>
                      </div>
                      <div className="space-y-1.5">
                        <Label className="text-[0.6875rem]">
                          Fieldwork limit
                        </Label>
                        <div className="flex items-center gap-3">
                          <Select
                            value={String(study.budget_minutes)}
                            onValueChange={(value) =>
                              updateSetting.mutate({ budget_minutes: Number(value) })
                            }
                          >
                            <SelectTrigger className="h-9 w-28"><SelectValue /></SelectTrigger>
                            <SelectContent>
                              {currentFieldworkLimitIsBelowSessionCap && (
                                <SelectItem value={String(study.budget_minutes)} disabled>
                                  {study.budget_minutes} min · below session cap
                                </SelectItem>
                              )}
                              {fieldworkLimitOptions.map((minutes) => (
                                <SelectItem key={minutes} value={String(minutes)}>
                                  {minutes} min
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        </div>
                        <p className="text-[0.65625rem] leading-relaxed text-muted-foreground">
                          This study-level limit bounds the interview minutes that may be
                          authorized. The connected API remains authoritative about whether a
                          new session can start. Written interviews use their recorded exchanges.
                        </p>
                        {!studyLimitsAreValid && (
                          <p className="text-[0.65625rem] leading-relaxed text-amber-700 dark:text-amber-300">
                            The fieldwork limit must cover at least one complete session.
                            Raise the limit or lower the session cap.
                          </p>
                        )}
                      </div>
                    </div>
                  </div>

                  <ParticipantInformationEditor
                    key={study.id}
                    value={{ language: study.language, ...study.participant_information }}
                    onSave={(participant_information) => updateSetting.mutateAsync({ participant_information })}
                  />
                  </div>

                  <div className="space-y-4">
                <div className="rounded-2xl border border-border bg-card p-4">
                  <p className="mb-1.5 flex items-center gap-2 text-[0.8125rem] font-medium text-foreground">
                    <Link2 className="size-4 text-moss" /> Participation links
                  </p>
                  <p className="mb-3 text-[0.6875rem] leading-relaxed text-muted-foreground">
                    {publicSpokenAvailable
                      ? "Share a link and participants talk to your interviewer, no account needed. Consent is collected and timestamped before every conversation."
                      : "Share a link for a written AI-led interview, no account needed. Consent is collected and timestamped before every conversation. Spoken public interviews remain in a protected pilot and are not offered on these links."}
                  </p>
                  <div className="space-y-1.5">
                    {(study.invites ?? []).map((invite) => (
                      <div
                        key={invite.id}
                        className={cn(
                          "flex items-center gap-2 rounded-xl bg-secondary/45 px-3 py-2 text-[0.71875rem]",
                          !invite.active && "opacity-55",
                        )}
                      >
                        <span className="min-w-0 truncate text-foreground">
                          {invite.label || "Field link"}
                        </span>
                        {invite.passcode_required && (
                          <span
                            title="An access code is required"
                            className="shrink-0 rounded-full bg-secondary px-2 py-0.5 font-mono text-[0.625rem] text-muted-foreground"
                          >
                            Access code set
                          </span>
                        )}
                        <span className="shrink-0 font-mono text-[0.625rem] text-muted-foreground">
                          {invite.used_sessions}/{invite.max_sessions}
                        </span>
                        <span className="ml-auto flex shrink-0 items-center gap-1.5">
                          <button
                            type="button"
                            aria-label="Copy participation link"
                            className="cursor-pointer p-1 text-moss hover:text-foreground"
                            onClick={() => copyInviteUrl(invite.id)}
                          >
                            <Copy className="size-3.5" />
                          </button>
                          <Switch
                            checked={invite.active}
                            disabled={
                              !invite.active &&
                              (!participantInformationIsReady(study) || !studyLimitsAreValid)
                            }
                            onCheckedChange={(checked) =>
                              setInviteActive(invite.id, checked)
                            }
                          />
                        </span>
                      </div>
                    ))}
                    {(study.invites ?? []).length === 0 && (
                      <p className="text-[0.6875rem] text-muted-foreground">
                        No links yet. Pilot the guide first, then open the door.
                      </p>
                    )}
                  </div>
                  <div className="mt-3 grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_4.5rem] gap-2">
                    <Input
                      value={inviteLabel}
                      onChange={(event) => setInviteLabel(event.target.value)}
                      placeholder="Label (P)"
                      className="h-8 text-[0.71875rem]"
                    />
                    <Input
                      value={invitePasscode}
                      onChange={(event) => setInvitePasscode(event.target.value)}
                      placeholder="Access code (optional)"
                      className="h-8 text-[0.71875rem]"
                    />
                    <Select value={inviteMax} onValueChange={setInviteMax}>
                      <SelectTrigger className="h-8 text-[0.71875rem]"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        {[5, 10, 25, 50, 100].map((count) => (
                          <SelectItem key={count} value={String(count)}>{count}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    className="mt-2 h-8 w-full rounded-full text-[0.71875rem]"
                    disabled={
                      createInvite.isPending ||
                      !participantInformationIsReady(study) ||
                      !studyLimitsAreValid
                    }
                    onClick={createParticipationLink}
                  >
                    {createInvite.isPending ? (
                      <Loader2 className="size-3.5 animate-spin" />
                    ) : (
                      <Plus className="size-3.5" />
                    )}
                    {publicSpokenAvailable
                      ? "Create participation link"
                      : "Create written participation link"}
                  </Button>
                  {!participantInformationIsReady(study) && (
                    <p className="mt-2 text-[0.65625rem] leading-relaxed text-amber-700 dark:text-amber-300">
                      Before opening a participant link, complete and review the controller,
                      purpose, legal basis, retention, recipients and participant-rights details above.
                    </p>
                  )}
                  {!studyLimitsAreValid && (
                    <p className="mt-2 text-[0.65625rem] leading-relaxed text-amber-700 dark:text-amber-300">
                      Raise the fieldwork limit to at least the session cap before opening
                      participation links.
                    </p>
                  )}
                </div>

                <div className="rounded-2xl border border-border bg-card p-4">
                  <p className="mb-1.5 flex items-center gap-2 text-[0.8125rem] font-medium text-foreground">
                    <FolderKanban className="size-4 text-moss" /> Project
                  </p>
                  <p className="mb-3 text-[0.6875rem] leading-relaxed text-muted-foreground">
                    Every session of this study lands as an interview inside the
                    chosen research project.
                  </p>
                  <Select
                    value={study.project_id === null ? "none" : String(study.project_id)}
                    onValueChange={(value) =>
                      updateSetting.mutate({
                        project_id: value === "none" ? null : Number(value),
                      })
                    }
                  >
                    <SelectTrigger className="h-9 w-full text-[0.78125rem]">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="none">No project</SelectItem>
                      {(projects ?? []).map((project) => (
                        <SelectItem key={project.id} value={String(project.id)}>
                          {project.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className="rounded-2xl border border-border bg-card p-4">
                  <p className="mb-1.5 flex items-center gap-2 text-[0.8125rem] font-medium text-foreground">
                    <AudioLines className="size-4 text-moss" /> Sessions
                  </p>
                  <p className="mb-3 text-[0.6875rem] leading-relaxed text-muted-foreground">
                    Each session shows its recorded duration and guide version. Whether a new
                    session can start is decided by the connected API.
                  </p>
                  {sessions.length === 0 ? (
                    <p className="text-[0.71875rem] leading-relaxed text-muted-foreground">
                      No conversations yet. Pilot sessions and field interviews
                      appear here, each with its transcript and analysis.
                    </p>
                  ) : (
                    <div className="space-y-1.5">
                      {sessions.slice(0, 12).map((item) => (
                        <div
                          key={item.id}
                          className="flex items-center gap-2 rounded-xl bg-secondary/45 px-3 py-2 text-[0.71875rem]"
                        >
                          {item.status === "completed" ? (
                            <Check className="size-3 shrink-0 text-moss" />
                          ) : item.status === "running" ? (
                            <Loader2 className="size-3 shrink-0 animate-spin text-moss" />
                          ) : (
                            <X className="size-3 shrink-0 text-muted-foreground" />
                          )}
                          <span className="text-foreground">{item.participant_label}</span>
                          <span className="font-mono text-[0.625rem] text-muted-foreground">
                            {formatClock(item.duration_ms)} · v{item.guide_version}
                          </span>
                          {item.interview_id && (
                            <Link
                              href={`/interviews/${item.interview_id}`}
                              className="ml-auto flex shrink-0 items-center gap-1 text-moss hover:underline"
                            >
                              <AudioLines className="size-3" /> Open
                            </Link>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        </main>
      </ResizableWorkspaceSplit>
    </div>
  );
}
