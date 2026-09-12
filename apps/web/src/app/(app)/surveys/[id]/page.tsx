"use client";
import { AiInteractionNotice } from "@/components/ai-interaction-notice";

import { ParticipantInformationEditor } from "@/components/participant-information";

import { type DragEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  BarChart3,
  BookMarked,
  Check,
  ChevronDown,
  Clipboard,
  Database,
  Download,
  ExternalLink,
  GripVertical,
  Link2,
  ListChecks,
  LockKeyhole,
  Loader2,
  MessageSquareText,
  Plus,
  SendHorizontal,
  Settings2,
  Share2,
  Square,
  Trash2,
  Wand2,
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
import { DetailError } from "@/components/detail-error";
import { InlineTitle } from "@/components/inline-title";
import {
  MobileWorkspaceSwitch,
  type MobileWorkspacePane,
} from "@/components/mobile-workspace-switch";
import { ResizableWorkspaceSplit } from "@/components/workspace/resizable-workspace-split";

import { Button } from "@/components/ui/button";
import ModelPicker from "@/components/search/model-picker";
import { usePrivateModelPreference } from "@/hooks/use-private-model-preference";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import {
  ApiError,
  SpecialistStreamError,
  api,
  createSpecialistTurnId,
  downloadSurveyCsv,
  retryTransientApiQuery,
} from "@/lib/api";
import {
  useDurableSpecialistTurn,
  useSpecialistTurnControl,
} from "@/hooks/use-durable-specialist-turn";
import { useActiveProject } from "@/lib/project-context";
import { projectConfirmedSurveyChange } from "@/lib/survey-agent-sync";
import { isParticipantInformationRequired, SURVEY_PARTICIPANT_INFORMATION_REQUIRED } from "@/lib/participant-information-feedback";
import { userFacingErrorMessage } from "@/lib/user-facing-error";
import type {
  SpecialistAgentEvent,
  Survey,
  SurveyAgentReply,
  SurveyMessage,
  SurveyQuestion,
  SurveyQuestionType,
} from "@/lib/types";
import { cn } from "@/lib/utils";

type WorkspaceView = "build" | "responses" | "share";

const QUESTION_TYPES: Array<{ value: SurveyQuestionType; label: string }> = [
  { value: "short_text", label: "Short answer" },
  { value: "long_text", label: "Long answer" },
  { value: "single_choice", label: "Single choice" },
  { value: "multiple_choice", label: "Multiple choice" },
  { value: "rating", label: "Rating" },
  { value: "scale", label: "Numeric scale" },
];

const ANALYSIS_STARTERS = [
  {
    eyebrow: "Draft the questionnaire",
    prompt: "Create a balanced six-question questionnaire from this survey's title and introduction. If the topic is not clear yet, ask me for it before editing.",
    detail: "Build a complete first version from the survey context.",
  },
  {
    eyebrow: "Improve wording & flow",
    prompt: "Improve the wording and order of the current questions. Keep their meaning and explain your changes.",
    detail: "Tighten language and arrange questions naturally.",
  },
  {
    eyebrow: "Check for bias",
    prompt: "Check the questionnaire for leading wording, overlap and missing response options, then fix clear issues.",
    detail: "Find methodological issues before participants see them.",
  },
  {
    eyebrow: "Analyse responses",
    prompt: "Summarise the strongest themes and support them with exact counts or response excerpts.",
    detail: "Turn collected responses into a grounded overview.",
  },
] as const;

function newQuestion(): SurveyQuestion {
  return {
    id: crypto.randomUUID(),
    title: "New question",
    description: "",
    type: "short_text",
    required: false,
    options: [],
    min: null,
    max: null,
  };
}

function choiceOptionsError(question: SurveyQuestion): string | null {
  if (question.type !== "single_choice" && question.type !== "multiple_choice") {
    return null;
  }
  const normalized = question.options.map((option) => option.trim()).filter(Boolean);
  if (normalized.length < 2) return "Add at least two answer options.";
  if (new Set(normalized.map((option) => option.toLocaleLowerCase())).size !== normalized.length) {
    return "Each answer option must be unique.";
  }
  return null;
}

function surveyQuestionsError(questions: SurveyQuestion[]): string | null {
  for (let index = 0; index < questions.length; index += 1) {
    const error = choiceOptionsError(questions[index]);
    if (error) return `Question ${index + 1}: ${error}`;
  }
  return null;
}

function questionsForSave(questions: SurveyQuestion[]): SurveyQuestion[] {
  const error = surveyQuestionsError(questions);
  if (error) throw new Error(error);
  return questions.map((question) => ({
    ...question,
    title: question.title.trim(),
    description: question.description.trim(),
    options:
      question.type === "single_choice" || question.type === "multiple_choice"
        ? question.options.map((option) => option.trim()).filter(Boolean)
        : question.options,
  }));
}

function surveyDraftPayload(survey: Survey) {
  return {
    title: survey.title,
    description: survey.description,
    questions: questionsForSave(survey.questions),
    settings: survey.settings,
  };
}

function ChoiceOptionsEditor({
  questionId,
  options,
  onChange,
}: {
  questionId: string;
  options: string[];
  onChange: (options: string[]) => void;
}) {
  const [draggedIndex, setDraggedIndex] = useState<number | null>(null);
  const inputRefs = useRef<Array<HTMLInputElement | null>>([]);
  const error =
    choiceOptionsError({
      id: questionId,
      title: "",
      description: "",
      type: "single_choice",
      required: false,
      options,
      min: null,
      max: null,
    });

  const focusOption = (index: number) => {
    requestAnimationFrame(() => inputRefs.current[index]?.focus());
  };

  const addOption = (afterIndex = options.length - 1) => {
    if (options.length >= 30) return;
    const next = [...options];
    const insertAt = Math.min(Math.max(afterIndex + 1, 0), next.length);
    next.splice(insertAt, 0, "");
    onChange(next);
    focusOption(insertAt);
  };

  const removeOption = (index: number) => {
    if (options.length <= 2) return;
    onChange(options.filter((_, optionIndex) => optionIndex !== index));
    focusOption(Math.max(0, index - 1));
  };

  const moveOption = (sourceIndex: number, targetIndex: number) => {
    if (sourceIndex === targetIndex || sourceIndex < 0 || targetIndex < 0) return;
    const next = [...options];
    const [moved] = next.splice(sourceIndex, 1);
    next.splice(targetIndex, 0, moved);
    onChange(next);
    setDraggedIndex(null);
    focusOption(targetIndex);
  };

  return (
    <div className="rounded-xl border border-border/75 bg-secondary/20 p-2.5">
      <div className="mb-2 flex items-center justify-between gap-3 px-1">
        <p className="font-mono text-[0.5625rem] uppercase tracking-[0.16em] text-muted-foreground">
          Answer options
        </p>
        <span className="text-[0.625rem] tabular-nums text-muted-foreground">
          {options.length}/30
        </span>
      </div>
      <div className="space-y-1.5">
        {options.map((option, optionIndex) => (
          <div
            key={`${questionId}-option-${optionIndex}`}
            onDragOver={(event) => {
              if (draggedIndex === null) return;
              event.preventDefault();
              event.stopPropagation();
              event.dataTransfer.dropEffect = "move";
            }}
            onDrop={(event) => {
              event.preventDefault();
              event.stopPropagation();
              if (draggedIndex !== null) moveOption(draggedIndex, optionIndex);
            }}
            className={cn(
              "group/option flex items-center gap-1.5 rounded-lg border border-border/70 bg-background px-1.5 transition-[border-color,opacity]",
              draggedIndex === optionIndex && "opacity-45",
            )}
          >
            <button
              type="button"
              draggable
              onDragStart={(event) => {
                event.stopPropagation();
                event.dataTransfer.effectAllowed = "move";
                event.dataTransfer.setData("text/plain", `${questionId}:${optionIndex}`);
                setDraggedIndex(optionIndex);
              }}
              onDragEnd={(event) => {
                event.stopPropagation();
                setDraggedIndex(null);
              }}
              onKeyDown={(event) => {
                if (event.key === "ArrowUp" && optionIndex > 0) {
                  event.preventDefault();
                  moveOption(optionIndex, optionIndex - 1);
                }
                if (event.key === "ArrowDown" && optionIndex < options.length - 1) {
                  event.preventDefault();
                  moveOption(optionIndex, optionIndex + 1);
                }
              }}
              className="grid size-7 shrink-0 cursor-grab place-items-center rounded-md text-muted-foreground/55 hover:bg-secondary hover:text-moss active:cursor-grabbing"
              aria-label={`Move option ${optionIndex + 1}. Drag or use arrow keys.`}
            >
              <GripVertical className="size-3.5" />
            </button>
            <span className="grid size-5 shrink-0 place-items-center rounded-full bg-secondary font-mono text-[0.5625rem] text-moss">
              {optionIndex + 1}
            </span>
            <Input
              ref={(node) => {
                inputRefs.current[optionIndex] = node;
              }}
              value={option}
              maxLength={180}
              onChange={(event) =>
                onChange(
                  options.map((current, currentIndex) =>
                    currentIndex === optionIndex ? event.target.value : current,
                  ),
                )
              }
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  addOption(optionIndex);
                }
                if (event.key === "Backspace" && !option && options.length > 2) {
                  event.preventDefault();
                  removeOption(optionIndex);
                }
              }}
              className="h-8 min-w-0 border-0 bg-transparent px-1 text-[0.75rem] shadow-none focus-visible:ring-0 dark:bg-transparent"
              placeholder={`Option ${optionIndex + 1}`}
              aria-label={`Option ${optionIndex + 1}`}
            />
            <button
              type="button"
              onClick={() => removeOption(optionIndex)}
              disabled={options.length <= 2}
              className="grid size-7 shrink-0 cursor-pointer place-items-center rounded-md text-muted-foreground opacity-0 transition-opacity hover:bg-destructive/8 hover:text-destructive focus-visible:opacity-100 disabled:cursor-not-allowed disabled:opacity-20 group-hover/option:opacity-100"
              aria-label={`Delete option ${optionIndex + 1}`}
              title={options.length <= 2 ? "A choice question needs at least two options" : "Delete option"}
            >
              <Trash2 className="size-3.5" />
            </button>
          </div>
        ))}
      </div>
      <div className="mt-2 flex flex-wrap items-center justify-between gap-2 px-1">
        <button
          type="button"
          onClick={() => addOption()}
          disabled={options.length >= 30}
          className="inline-flex cursor-pointer items-center gap-1.5 text-[0.6875rem] font-medium text-moss hover:underline disabled:cursor-not-allowed disabled:text-muted-foreground disabled:no-underline"
        >
          <Plus className="size-3.5" />
          Add option
        </button>
        <p className="text-[0.625rem] text-muted-foreground">
          Enter adds another · drag to reorder
        </p>
      </div>
      {error && (
        <p className="mt-2 px-1 text-[0.6875rem] text-destructive" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

function QuestionEditor({
  question,
  index,
  onChange,
  onDelete,
  dragging,
  dropTarget,
  onDragStart,
  onDragOver,
  onDrop,
  onDragEnd,
  onKeyboardMove,
}: {
  question: SurveyQuestion;
  index: number;
  onChange: (next: SurveyQuestion) => void;
  onDelete: () => void;
  dragging: boolean;
  dropTarget: boolean;
  onDragStart: (event: DragEvent<HTMLButtonElement>) => void;
  onDragOver: (event: DragEvent<HTMLElement>) => void;
  onDrop: (event: DragEvent<HTMLElement>) => void;
  onDragEnd: () => void;
  onKeyboardMove: (direction: -1 | 1) => void;
}) {
  const usesOptions = question.type === "single_choice" || question.type === "multiple_choice";
  const usesRange = question.type === "rating" || question.type === "scale";
  return (
    <article
      onDragOver={onDragOver}
      onDrop={onDrop}
      className={cn(
        "relative rounded-2xl border border-border bg-card p-4 shadow-sm transition-[border-color,opacity,transform]",
        dragging && "scale-[0.99] opacity-45",
        dropTarget && !dragging && "border-moss/60 ring-2 ring-moss/10",
      )}
    >
      <div className="flex items-center gap-2">
        <button
          type="button"
          draggable
          onDragStart={onDragStart}
          onDragEnd={onDragEnd}
          onKeyDown={(event) => {
            if (event.key === "ArrowUp") {
              event.preventDefault();
              onKeyboardMove(-1);
            }
            if (event.key === "ArrowDown") {
              event.preventDefault();
              onKeyboardMove(1);
            }
          }}
          className="grid size-7 shrink-0 cursor-grab place-items-center rounded-lg text-muted-foreground/55 hover:bg-secondary hover:text-moss active:cursor-grabbing"
          aria-label={`Move question ${index + 1}. Drag or use arrow keys.`}
        >
          <GripVertical className="size-4" />
        </button>
        <span className="grid size-6 shrink-0 place-items-center rounded-full bg-secondary font-mono text-[0.625rem] text-moss">
          {index + 1}
        </span>
        <Input
          value={question.title}
          onChange={(event) => onChange({ ...question, title: event.target.value })}
          className="h-9 border-0 bg-transparent px-1 text-[0.875rem] font-medium shadow-none focus-visible:ring-0"
          placeholder="Question"
          aria-label={`Question ${index + 1} text`}
        />
        <button
          type="button"
          onClick={onDelete}
          className="grid size-8 shrink-0 cursor-pointer place-items-center rounded-full text-muted-foreground hover:bg-destructive/8 hover:text-destructive"
          aria-label="Delete question"
        >
          <Trash2 className="size-3.5" />
        </button>
      </div>
      <Input
        value={question.description}
        onChange={(event) => onChange({ ...question, description: event.target.value })}
        className="mt-1 h-8 border-0 bg-transparent pl-10 text-[0.71875rem] text-muted-foreground shadow-none focus-visible:ring-0"
        placeholder="Optional context for participants"
        aria-label={`Question ${index + 1} participant context`}
      />
      <div className="mt-3 border-t border-border/70 pt-3">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
          <Select
            value={question.type}
            onValueChange={(value) =>
              onChange({
                ...question,
                type: value as SurveyQuestionType,
                options:
                  value === "single_choice" || value === "multiple_choice"
                    ? question.options.length
                      ? question.options
                      : ["Option 1", "Option 2"]
                    : [],
                min: value === "rating" || value === "scale" ? question.min ?? 1 : null,
                max: value === "rating" || value === "scale" ? question.max ?? 5 : null,
              })
            }
          >
            <SelectTrigger className="h-9 w-full rounded-full bg-secondary/50 sm:w-44">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {QUESTION_TYPES.map((type) => (
                <SelectItem key={type.value} value={type.value}>{type.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          {usesRange && (
            <div className="flex min-w-0 flex-1 items-center gap-2 sm:max-w-72">
              <Input
                type="number"
                value={question.min ?? 1}
                onChange={(event) => onChange({ ...question, min: Number(event.target.value) })}
                className="h-9"
                aria-label="Minimum"
              />
              <span className="text-xs text-muted-foreground">to</span>
              <Input
                type="number"
                value={question.max ?? 5}
                onChange={(event) => onChange({ ...question, max: Number(event.target.value) })}
                className="h-9"
                aria-label="Maximum"
              />
            </div>
          )}
          {!usesOptions && !usesRange && (
            <p className="min-w-0 flex-1 px-2 text-[0.6875rem] text-muted-foreground">
              {question.type === "long_text" ? "Multi-line response" : "Single-line response"}
            </p>
          )}
          <label className="flex cursor-pointer items-center gap-2 text-[0.71875rem] text-muted-foreground sm:ml-auto">
            <Switch
              checked={question.required}
              onCheckedChange={(required) => onChange({ ...question, required })}
              size="sm"
            />
            Required
          </label>
        </div>
        {usesOptions && (
          <div className="mt-3">
            <ChoiceOptionsEditor
              questionId={question.id}
              options={question.options}
              onChange={(options) => onChange({ ...question, options })}
            />
          </div>
        )}
      </div>
    </article>
  );
}

function ResultsView({ survey }: { survey: Survey }) {
  return (
    <div className="space-y-5">
      <div className="grid gap-3 sm:grid-cols-3">
        {[
          [survey.summary.responses, "responses"],
          [survey.summary.complete, "complete"],
          [`${survey.summary.completion_percent}%`, "completion"],
        ].map(([value, label]) => (
          <div key={label} className="rounded-2xl border border-border bg-card p-4">
            <p className="font-mono text-xl text-foreground">{value}</p>
            <p className="mt-1 text-[0.6875rem] text-muted-foreground">{label}</p>
          </div>
        ))}
      </div>
      {survey.summary.questions.map((summary, index) => (
        <article key={summary.id} className="rounded-2xl border border-border bg-card p-5">
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-moss">
                Question {index + 1}
              </p>
              <h3 className="mt-1 text-[0.875rem] font-medium text-foreground">{summary.title}</h3>
            </div>
            <span className="font-mono text-[0.6875rem] text-muted-foreground">
              n={summary.answered}
            </span>
          </div>
          {summary.counts && (
            <div className="mt-4 space-y-2.5">
              {summary.counts.map((item) => (
                <div key={item.option}>
                  <div className="mb-1 flex justify-between gap-3 text-[0.71875rem]">
                    <span className="truncate">{item.option}</span>
                    <span className="font-mono text-muted-foreground">{item.count} · {item.percent}%</span>
                  </div>
                  <div className="h-1.5 overflow-hidden rounded-full bg-secondary">
                    <div className="h-full rounded-full bg-moss-surface" style={{ width: `${item.percent}%` }} />
                  </div>
                </div>
              ))}
            </div>
          )}
          {summary.mean !== undefined && (
            <div className="mt-4 flex items-end gap-3">
              <span className="font-display text-4xl text-foreground">{summary.mean ?? "—"}</span>
              <span className="pb-1 text-[0.6875rem] text-muted-foreground">
                mean · range {summary.min ?? "—"}–{summary.max ?? "—"}
              </span>
            </div>
          )}
          {summary.responses && (
            <div className="mt-4 space-y-2">
              {summary.responses.slice(0, 8).map((response, responseIndex) => (
                <p key={responseIndex} className="rounded-xl bg-secondary/45 px-3 py-2 text-[0.75rem] leading-relaxed">
                  {response}
                </p>
              ))}
            </div>
          )}
        </article>
      ))}
    </div>
  );
}

export default function SurveyWorkspacePage() {
  const params = useParams<{ id: string }>();
  const surveyId = params.id;
  const queryClient = useQueryClient();
  const { setActiveProjectId } = useActiveProject();
  const chatEndRef = useRef<HTMLDivElement>(null);
  const [view, setView] = useState<WorkspaceView>("build");
  const [mobilePane, setMobilePane] = useState<MobileWorkspacePane>("workspace");
  const [draft, setDraft] = useState<Survey | null>(null);
  const [publicationError, setPublicationError] = useState<string | null>(null);
  const [publicationNeedsInfo, setPublicationNeedsInfo] = useState(false);
  const [participantInfoFocusRequest, setParticipantInfoFocusRequest] = useState(0);
  const participantInformationRef = useRef<HTMLDivElement>(null);
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
  const [surveyPassword, setSurveyPassword] = useState("");
  const [passwordSetupOpen, setPasswordSetupOpen] = useState(false);
  const [draggedQuestionId, setDraggedQuestionId] = useState<string | null>(null);
  const [dropQuestionId, setDropQuestionId] = useState<string | null>(null);

  const handleSurveyAgentEvent = useCallback((event: SpecialistAgentEvent) => {
    recordAgentEvent(event);
    if (event.event !== "change.completed" || event.applied !== true) return;
    setDraft((current) => current
      ? projectConfirmedSurveyChange(current, event)
      : current,
    );
    queryClient.setQueryData<Survey>(["survey", surveyId], (current) => current
      ? projectConfirmedSurveyChange(current, event)
      : current,
    );
  }, [queryClient, recordAgentEvent, surveyId]);

  // which model answers; persisted like the writer's composer choice
  const [model, pickModel] = usePrivateModelPreference("six:survey-model");

  // auto apply: proposals land in the form without an Apply click
  const [autoApply, setAutoApply] = useState(false);
  useEffect(() => {
    setAutoApply(localStorage.getItem("six:survey-autoapply") === "1");
  }, []);

  // Build and Share both contain unsaved edits. Poll only the responses view,
  // so directing an incomplete publication to Share cannot erase its draft.
  const { data: survey, isLoading, error: loadError, refetch: refetchSurvey, isFetching: fetchingSurvey } = useQuery({
    queryKey: ["survey", surveyId],
    queryFn: () => api.survey(surveyId),
    refetchInterval: view === "responses" ? 8_000 : false,
    refetchOnReconnect: view === "responses",
  });
  const { data: history } = useQuery({
    queryKey: ["survey-chat", surveyId],
    queryFn: () => api.surveyChatHistory(surveyId),
  });
  useEffect(() => {
    if (survey?.project_id) setActiveProjectId(survey.project_id);
  }, [survey?.project_id, setActiveProjectId]);
  const { data: writerDocs } = useQuery({
    queryKey: ["writer-docs"],
    queryFn: api.writerList,
  });
  useEffect(() => {
    if (survey) setDraft(survey);
  }, [survey]);
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [history]);
  useEffect(() => {
    if (view !== "share" || mobilePane !== "workspace" || participantInfoFocusRequest === 0) return;
    participantInformationRef.current?.focus({ preventScroll: true });
    participantInformationRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [view, mobilePane, participantInfoFocusRequest]);

  function showParticipantInformation() {
    setView("share");
    setMobilePane("workspace");
    setParticipantInfoFocusRequest((request) => request + 1);
  }

  const save = useMutation({
    mutationFn: () => {
      if (!draft) throw new Error("Survey is not ready.");
      return api.surveyUpdate(surveyId, surveyDraftPayload(draft));
    },
    onSuccess: (updated) => {
      setDraft(updated);
      void queryClient.invalidateQueries({ queryKey: ["survey", surveyId] });
      void queryClient.invalidateQueries({ queryKey: ["surveys"] });
      toast.success("Survey saved.");
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "Save failed."),
  });
  // header rename patches only the title so unsaved build edits survive
  const rename = useMutation({
    mutationFn: (title: string) => api.surveyUpdate(surveyId, { title }),
    onSuccess: (updated) => {
      setDraft((prev) => (prev ? { ...prev, title: updated.title } : updated));
      void queryClient.invalidateQueries({ queryKey: ["surveys"] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Save failed."),
  });
  const statusUpdate = useMutation({
    mutationFn: (status: Survey["status"]) => {
      if (!draft) throw new Error("Survey is not ready.");
      return api.surveyUpdate(surveyId, { ...surveyDraftPayload(draft), status });
    },
    onSuccess: (updated) => {
      setPublicationError(null);
      setPublicationNeedsInfo(false);
      setDraft(updated);
      void queryClient.invalidateQueries({ queryKey: ["survey", surveyId] });
      void queryClient.invalidateQueries({ queryKey: ["surveys"] });
      toast.success(updated.status === "live" ? "Survey is live." : "Survey status updated.");
    },
    onError: (error, status) => {
      const needsInfo = status === "live" && error instanceof ApiError
        && isParticipantInformationRequired(error.detail);
      const message = needsInfo
        ? SURVEY_PARTICIPANT_INFORMATION_REQUIRED
        : userFacingErrorMessage(error, "The survey status could not be changed. Please try again.");
      setPublicationError(message);
      setPublicationNeedsInfo(needsInfo);
      if (needsInfo) showParticipantInformation();
      toast.error(message);
    },
  });
  function requestPublication() {
    if (!draft || statusUpdate.isPending) return;
    const questionError = surveyQuestionsError(draft.questions);
    if (questionError) {
      setPublicationError(questionError);
      setPublicationNeedsInfo(false);
      setView("build");
      setMobilePane("workspace");
      return;
    }
    if (draft.participant_information_ready !== true) {
      setPublicationError(SURVEY_PARTICIPANT_INFORMATION_REQUIRED);
      setPublicationNeedsInfo(true);
      showParticipantInformation();
      return;
    }
    setPublicationError(null);
    setPublicationNeedsInfo(false);
    statusUpdate.mutate("live");
  }
  const passwordUpdate = useMutation({
    mutationFn: async (password: string) => {
      if (draft) {
        await api.surveyUpdate(surveyId, surveyDraftPayload(draft));
      }
      return api.surveySetPassword(surveyId, password);
    },
    onSuccess: (updated) => {
      setDraft(updated);
      setSurveyPassword("");
      setPasswordSetupOpen(false);
      void queryClient.invalidateQueries({ queryKey: ["survey", surveyId] });
      void queryClient.invalidateQueries({ queryKey: ["surveys"] });
      toast.success("Survey password saved.");
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "Password update failed."),
  });
  const passwordRemove = useMutation({
    mutationFn: async () => {
      if (draft) {
        await api.surveyUpdate(surveyId, surveyDraftPayload(draft));
      }
      return api.surveyRemovePassword(surveyId);
    },
    onSuccess: (updated) => {
      setDraft(updated);
      setSurveyPassword("");
      setPasswordSetupOpen(false);
      void queryClient.invalidateQueries({ queryKey: ["survey", surveyId] });
      void queryClient.invalidateQueries({ queryKey: ["surveys"] });
      toast.success("Survey password removed.");
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "Password removal failed."),
  });
  const chat = useMutation({
    mutationFn: async (question: string) => {
      if (draft) {
        await api.surveyUpdate(surveyId, surveyDraftPayload(draft));
      }
      const turnId = createSpecialistTurnId();
      return api.surveyChatStream(
        surveyId,
        question,
        model,
        autoApply,
        handleSurveyAgentEvent,
        beginLocalTurn(turnId),
      );
    },
    onMutate: (question) => {
      setAgentWorking(true);
      startAgentTurn();
      // show the user's turn immediately; the server turn replaces it on refetch
      setPendingQuestion(question);
    },
    onSuccess: async (result) => {
      setDraft(result.survey);
      queryClient.setQueryData(["survey", surveyId], result.survey);
      await Promise.allSettled([
        queryClient.invalidateQueries({ queryKey: ["survey-chat", surveyId] }),
        queryClient.invalidateQueries({ queryKey: ["surveys"] }),
      ]);
      handoffAgentTurn();
      finishTurn();
      setAgentWorking(false);
      setPendingQuestion(null);
      if (result.actions.some((action) => action.applied)) {
        toast.success("Survey updated by the agent.");
      }
    },
    onError: async (error) => {
      await Promise.allSettled([
        queryClient.refetchQueries({ queryKey: ["survey-chat", surveyId] }),
        queryClient.refetchQueries({ queryKey: ["survey", surveyId] }),
      ]);
      handoffAgentTurn();
      finishTurn();
      setAgentWorking(false);
      setPendingQuestion(null);
      if (!(error instanceof SpecialistStreamError && error.kind === "cancelled")) {
        toast.error(error instanceof Error ? error.message : "Analysis failed.");
      }
    },
  });
  const applyProposal = useMutation({
    mutationFn: (messageId: number) => api.surveyChatApply(surveyId, messageId),
    onSuccess: (result) => {
      setDraft(result.survey);
      queryClient.setQueryData(["survey", surveyId], result.survey);
      void queryClient.invalidateQueries({ queryKey: ["survey-chat", surveyId] });
      void queryClient.invalidateQueries({ queryKey: ["surveys"] });
      toast.success("Proposal applied to the survey.");
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Apply failed."),
  });
  const publish = useMutation({
    mutationFn: async (writerId?: string) => {
      if (draft) {
        await api.surveyUpdate(surveyId, surveyDraftPayload(draft));
      }
      return api.surveyImportDataset(surveyId, writerId);
    },
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ["survey", surveyId] });
      void queryClient.invalidateQueries({ queryKey: ["surveys"] });
      void queryClient.invalidateQueries({ queryKey: ["datasets"] });
      if (result.writer_linked) {
        void queryClient.invalidateQueries({ queryKey: ["writer-docs"] });
        toast.success("Responses synced and linked to the manuscript.");
      } else {
        toast.success(`Responses synced to the Data Hub (version ${result.version}).`);
      }
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Dataset export failed."),
  });

  function moveQuestion(sourceId: string, targetId: string) {
    if (!draft || sourceId === targetId) return;
    const sourceIndex = draft.questions.findIndex((question) => question.id === sourceId);
    const targetIndex = draft.questions.findIndex((question) => question.id === targetId);
    if (sourceIndex < 0 || targetIndex < 0) return;
    const next = [...draft.questions];
    const [moved] = next.splice(sourceIndex, 1);
    next.splice(targetIndex, 0, moved);
    setDraft({ ...draft, questions: next });
  }

  function moveQuestionBy(questionId: string, direction: -1 | 1) {
    if (!draft) return;
    const sourceIndex = draft.questions.findIndex((question) => question.id === questionId);
    const target = draft.questions[sourceIndex + direction];
    if (sourceIndex < 0 || !target) return;
    moveQuestion(questionId, target.id);
  }

  const publicUrl = useMemo(
    () => (typeof window === "undefined" ? `/s/${surveyId}` : `${window.location.origin}/s/${surveyId}`),
    [surveyId],
  );
  const chatMessages: SurveyMessage[] = history ?? [];
  const persistedAgentTimelines = chatMessages
    .filter((message) => message.role === "assistant")
    .map((message) => message.payload.agent_events ?? []);
  const { checking: checkingAgentTurn, recovering: recoveringAgentTurn } =
    useDurableSpecialistTurn<SurveyAgentReply>({
    resourceKind: "survey",
    resourceId: surveyId,
    enabled: history !== undefined,
    persistedTurnIds: agentTurnIdsFromTimelines(persistedAgentTimelines),
    onStarted: (turn) => {
      recoverTurn(turn.turn_id);
      startAgentTurn();
      setAgentWorking(true);
      setPendingQuestion(null);
    },
    onEvent: handleSurveyAgentEvent,
    onTerminal: async (result, turn) => {
      if (result?.survey) {
        setDraft(result.survey);
        queryClient.setQueryData(["survey", surveyId], result.survey);
      }
      await Promise.allSettled([
        queryClient.refetchQueries({ queryKey: ["survey-chat", surveyId] }),
        queryClient.invalidateQueries({ queryKey: ["survey", surveyId] }),
        queryClient.invalidateQueries({ queryKey: ["surveys"] }),
      ]);
      handoffAgentTurn();
      finishTurn(turn.turn_id);
      setAgentWorking(false);
      setPendingQuestion(null);
    },
    onError: (error) => {
      setAgentWorking(false);
      void queryClient.refetchQueries({ queryKey: ["survey", surveyId] });
      toast.error(
        error instanceof Error
          ? error.message
          : "The running survey task could not be recovered yet.",
      );
    },
    });
  const specialistBusy =
    agentWorking
    || Boolean(activeTurnId)
    || history === undefined
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
    resourceKind: "survey",
    resourceId: surveyId,
    queryKey: ["survey-chat", surveyId],
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
        title="Survey unavailable"
        error={loadError}
        fallback="This survey could not be loaded."
        backHref="/surveys"
        backLabel="Back to surveys"
        onRetry={retryTransientApiQuery(0, loadError) ? () => void refetchSurvey() : undefined}
        retrying={fetchingSurvey}
      />
    );
  }
  if (isLoading || !draft) {
    return <div className="grid min-h-0 flex-1 place-items-center"><Loader2 className="size-5 animate-spin text-muted-foreground" /></div>;
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-background md:rounded-t-2xl">
      <SpecialistChatResetDialog
        open={confirmationOpen}
        clearing={clearing}
        returnFocusRef={clearChatTriggerRef}
        onOpenChange={setConfirmationOpen}
        onConfirm={() => void confirmClearChat()}
      />
      <h1 className="sr-only">Survey editor: {draft.title || "Untitled survey"}</h1>
      <header className="flex min-h-14 shrink-0 items-center justify-between gap-2 border-b border-border px-3 py-2 sm:gap-4 sm:px-5 lg:px-7">
        <div className="flex min-w-0 flex-1 items-center gap-3">
          <Button asChild variant="ghost" size="icon" className="size-8 rounded-full">
            <Link href="/surveys" aria-label="Back to surveys"><ArrowLeft className="size-4" /></Link>
          </Button>
          <div className="min-w-0 flex-1">
            <InlineTitle
              value={draft.title}
              onCommit={(next) => rename.mutate(next)}
              ariaLabel="Survey title"
              placeholder="Untitled survey"
            />
            <p className="font-mono text-[0.59375rem] uppercase tracking-[0.18em] text-muted-foreground">
              {draft.status} · {draft.response_count} responses
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            className="h-9 rounded-full px-3"
            disabled={save.isPending}
            onClick={() => save.mutate()}
            aria-label="Save survey"
          >
            {save.isPending ? <Loader2 className="size-4 animate-spin" /> : <Check className="size-4" />}
            <span className="hidden sm:inline">Save</span>
          </Button>
          {draft.status !== "live" ? (
            <Button
              className="h-9 rounded-full px-3"
              onClick={requestPublication}
              disabled={statusUpdate.isPending}
              aria-label="Publish survey"
            >
              {statusUpdate.isPending ? <Loader2 className="size-4 animate-spin" /> : <Share2 className="size-4" />}
              <span className="hidden sm:inline">Publish</span>
            </Button>
          ) : (
            <Button
              variant="outline"
              className="h-9 rounded-full px-3"
              onClick={() => statusUpdate.mutate("closed")}
              disabled={statusUpdate.isPending}
              aria-label="Close survey collection"
            >
              <span className="hidden sm:inline">Close collection</span>
              <span className="sm:hidden">Close</span>
            </Button>
          )}
        </div>
      </header>

      {publicationError && (
        <div role="alert" className="flex shrink-0 flex-wrap items-center gap-3 border-b border-border bg-secondary/45 px-5 py-3 text-sm">
          <p className="min-w-0 flex-1">{publicationError}</p>
          {publicationNeedsInfo && <Button variant="outline" size="sm" className="rounded-full" onClick={showParticipantInformation}>Review participant information</Button>}
        </div>
      )}

      <ResizableWorkspaceSplit
        storageKey="six:survey-workspace-split"
        label="Resize survey assistant and survey"
        mobileSwitch={(
          <MobileWorkspaceSwitch
            value={mobilePane}
            onChange={setMobilePane}
            workspaceLabel="Survey"
          />
        )}
      >
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
                  Your methodologist, on call
                </p>
                <p className="max-w-[17rem] text-[0.78125rem] leading-relaxed text-muted-foreground">
                  Ask for a first draft, a wording pass, a bias check or a
                  grounded read of the responses. Questionnaire edits come back
                  as proposals you apply with one click, or land directly when
                  auto apply is on.
                </p>
              </div>
            )}
            {chatMessages.map((message) => (
              <div key={message.id} className={cn("flex", message.role === "user" && "justify-end")}>
                {/* like every other chat: plain assistant prose, framed cards
                    only for actions, the user keeps the pine bubble */}
                <div className={cn("min-w-0 max-w-[85%] overflow-hidden text-[0.875rem] leading-relaxed", message.role === "user" ? "rounded-2xl rounded-br-md bg-moss-surface px-3.5 py-2.5 text-ivory" : "max-w-full flex-1 text-foreground")}>
                  {message.role === "assistant" ? (
                    <SpecialistCompletedTurn
                      kind="survey"
                      events={message.payload.agent_events}
                      answer={message.content}
                      artifacts={message.payload.artifacts}
                    >
                      {message.payload.proposal_status === "pending" &&
                      (message.payload.actions?.length ?? 0) > 0 &&
                      (message.payload.agent_events?.length ?? 0) > 0 ? (
                        <AgentProposalControls
                          count={message.payload.actions?.length ?? 0}
                          applying={applyProposal.isPending}
                          onApply={() => applyProposal.mutate(message.id)}
                        />
                      ) : null}
                      {(message.payload.actions?.length ?? 0) > 0 &&
                      (message.payload.agent_events?.length ?? 0) === 0 ? (
                        <AgentChangeSequence
                          changes={message.payload.actions ?? []}
                          pending={message.payload.proposal_status === "pending"}
                          applying={applyProposal.isPending}
                          onApply={() => applyProposal.mutate(message.id)}
                        />
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
              !chatMessages
                .slice(-2)
                .some((message) => message.role === "user" && message.content === pendingQuestion) && (
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
                {ANALYSIS_STARTERS.map((starter) => (
                  <button
                    key={starter.prompt}
                    type="button"
                    onClick={() => chat.mutate(starter.prompt)}
                    title={starter.detail}
                    className="shrink-0 cursor-pointer rounded-full border border-border bg-card px-2.5 py-1 text-[0.65625rem] text-muted-foreground transition-colors hover:border-moss/40 hover:text-moss"
                  >
                    {starter.eyebrow}
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
              <button
                type="button"
                onClick={() => {
                  const next = !autoApply;
                  setAutoApply(next);
                  localStorage.setItem("six:survey-autoapply", next ? "1" : "0");
                  if (next) {
                    toast.success("Auto apply on: edits land in the form immediately.");
                  }
                }}
                title={
                  autoApply
                    ? "Auto apply is on: the agent's edits land without asking"
                    : "Auto apply is off: every edit waits for your Apply"
                }
                aria-pressed={autoApply}
                className={cn(
                  "grid size-9 shrink-0 cursor-pointer place-items-center rounded-full border transition-colors",
                  autoApply
                    ? "border-moss/50 bg-accent text-moss"
                    : "border-border text-muted-foreground hover:border-moss/40 hover:text-foreground",
                )}
              >
                <Wand2 className="size-4" />
              </button>
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
                placeholder="Create questions, improve the flow or analyse responses…"
                aria-label="Survey assistant message"
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
            "@container min-h-0 overflow-y-auto group-data-[workspace-layout=split]/workspace:!block",
            mobilePane === "workspace" ? "block" : "hidden",
          )}
        >
          <div className="sticky top-0 z-10 flex items-center justify-between gap-3 border-b border-border bg-background/95 px-5 py-3 backdrop-blur lg:px-7">
            <div className="flex items-center gap-1 rounded-full bg-secondary/65 p-1">
              {([
                ["build", Settings2, "Build"],
                ["responses", BarChart3, "Responses"],
                ["share", Share2, "Share"],
              ] as const).map(([value, Icon, label]) => (
                <button key={value} type="button" onClick={() => setView(value)} className={cn("flex h-8 cursor-pointer items-center gap-1.5 rounded-full px-3 text-[0.71875rem] font-medium transition-colors", view === value ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground")}>
                  <Icon className="size-3.5" /> {label}
                </button>
              ))}
            </div>
            <Button variant="ghost" size="sm" className="rounded-full" onClick={() => downloadSurveyCsv(surveyId)} disabled={draft.response_count === 0}>
              <Download className="size-3.5" /> CSV
            </Button>
          </div>

          <div className="mx-auto w-full max-w-5xl px-5 py-6 lg:px-7 lg:py-8">
            {view === "build" && (
              <div className="space-y-5">
                <section className="rounded-3xl border border-border bg-card p-5">
                  <Input value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} aria-label="Survey title" className="h-auto border-0 bg-transparent px-0 font-display text-3xl text-foreground shadow-none focus-visible:ring-0" />
                  <Textarea value={draft.description} onChange={(event) => setDraft({ ...draft, description: event.target.value })} placeholder="Tell participants what this research is about and how their answers will be used." aria-label="Survey description and participant information" className="mt-2 min-h-20 resize-none border-0 bg-transparent px-0 text-[0.8125rem] leading-relaxed shadow-none focus-visible:ring-0" />
                </section>
                {draft.questions.map((question, index) => (
                  <QuestionEditor
                    key={question.id}
                    question={question}
                    index={index}
                    dragging={draggedQuestionId === question.id}
                    dropTarget={dropQuestionId === question.id}
                    onDragStart={(event) => {
                      event.dataTransfer.effectAllowed = "move";
                      event.dataTransfer.setData("text/plain", question.id);
                      setDraggedQuestionId(question.id);
                      setDropQuestionId(question.id);
                    }}
                    onDragOver={(event) => {
                      event.preventDefault();
                      event.dataTransfer.dropEffect = "move";
                      if (draggedQuestionId) setDropQuestionId(question.id);
                    }}
                    onDrop={(event) => {
                      event.preventDefault();
                      const sourceId = draggedQuestionId || event.dataTransfer.getData("text/plain");
                      if (sourceId) moveQuestion(sourceId, question.id);
                      setDraggedQuestionId(null);
                      setDropQuestionId(null);
                    }}
                    onDragEnd={() => {
                      setDraggedQuestionId(null);
                      setDropQuestionId(null);
                    }}
                    onKeyboardMove={(direction) => moveQuestionBy(question.id, direction)}
                    onChange={(next) => setDraft({ ...draft, questions: draft.questions.map((item) => item.id === question.id ? next : item) })}
                    onDelete={() => draft.questions.length > 1 && setDraft({ ...draft, questions: draft.questions.filter((item) => item.id !== question.id) })}
                  />
                ))}
                <Button variant="outline" className="w-full rounded-2xl border-dashed py-6" onClick={() => setDraft({ ...draft, questions: [...draft.questions, newQuestion()] })}>
                  <Plus className="size-4" /> Add question
                </Button>
              </div>
            )}

            {view === "responses" && (
              draft.response_count === 0 ? (
                <div className="rounded-3xl border border-dashed border-border py-20 text-center">
                  <ListChecks className="mx-auto size-6 text-moss" />
                  <h2 className="mt-4 font-serif text-2xl text-foreground">The analysis grows with every response</h2>
                  <p className="mx-auto mt-2 max-w-md text-[0.8125rem] leading-relaxed text-muted-foreground">Publish the survey and share its link. Exact distributions, open-text answers and AI-assisted synthesis will appear here.</p>
                  <Button className="mt-5 rounded-full" onClick={() => setView("share")}><Share2 className="size-4" /> Open sharing</Button>
                </div>
              ) : (
                <div className="space-y-5">
                  <section className="rounded-3xl border border-border bg-card p-5">
                    <div className="flex flex-wrap items-center justify-between gap-4">
                      <div className="min-w-0">
                        <p className="flex items-center gap-2 text-[0.8125rem] font-medium text-foreground">
                          <Database className="size-4 text-moss" /> Results in the Data Hub
                        </p>
                        <p className="mt-1 max-w-xl text-[0.71875rem] leading-relaxed text-muted-foreground">
                          {draft.settings.result_dataset_id
                            ? "This response set lives in a versioned Data Hub dataset — the Writer and Visual Lab work from its exact values."
                            : "Export the response set as a versioned dataset: exact counts for the manuscript, charts in Visual Lab and a provenance trail back to this survey."}
                        </p>
                      </div>
                      <div className="flex shrink-0 flex-wrap items-center gap-2">
                        {draft.settings.result_dataset_id && (
                          <Button asChild variant="outline" size="sm" className="rounded-full">
                            <Link href="/data">Open Data Hub</Link>
                          </Button>
                        )}
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button variant="outline" size="sm" className="rounded-full" disabled={publish.isPending}>
                              <BookMarked className="size-3.5" /> To manuscript <ChevronDown className="size-3" />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end" className="w-[17rem]">
                            <DropdownMenuLabel className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
                              Link responses to
                            </DropdownMenuLabel>
                            {(writerDocs ?? []).length === 0 ? (
                              <p className="px-2 py-3 text-[0.75rem] leading-relaxed text-muted-foreground">
                                No manuscripts yet. Create one in the Writer, then the dataset attaches here.
                              </p>
                            ) : (
                              (writerDocs ?? []).slice(0, 10).map((doc) => (
                                <DropdownMenuItem
                                  key={doc.id}
                                  onSelect={() => publish.mutate(doc.public_id ?? String(doc.id))}
                                >
                                  <span className="truncate">{doc.title}</span>
                                </DropdownMenuItem>
                              ))
                            )}
                          </DropdownMenuContent>
                        </DropdownMenu>
                        <Button size="sm" className="rounded-full" disabled={publish.isPending} onClick={() => publish.mutate(undefined)}>
                          {publish.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <Database className="size-3.5" />}
                          {draft.settings.result_dataset_id ? "Sync responses" : "Export dataset"}
                        </Button>
                      </div>
                    </div>
                  </section>
                  <ResultsView survey={draft} />
                </div>
              )
            )}

            {view === "share" && (
              <div className="grid min-w-0 gap-5 @min-[50rem]:grid-cols-[minmax(0,1.1fr)_minmax(0,.9fr)]">
                <div ref={participantInformationRef} tabIndex={-1} aria-label="Participant information" className="scroll-mt-4 outline-none focus-visible:ring-2 focus-visible:ring-moss @min-[50rem]:col-span-2">
                  <ParticipantInformationEditor
                    key={draft.public_id}
                    value={draft.participant_information ?? {}}
                    gaps={draft.participant_information_gaps ?? []}
                    onSave={async (participant_information) => {
                      const updated = await api.surveyUpdate(surveyId, { participant_information });
                      setDraft((current) => current ? { ...current, participant_information: updated.participant_information, participant_information_ready: updated.participant_information_ready, participant_information_gaps: updated.participant_information_gaps } : updated);
                      if (publicationNeedsInfo) {
                        setPublicationError(updated.participant_information_ready
                          ? null
                          : SURVEY_PARTICIPANT_INFORMATION_REQUIRED);
                        setPublicationNeedsInfo(!updated.participant_information_ready);
                      }
                    }}
                  />
                </div>
                <section className="min-w-0 rounded-3xl border border-border bg-card p-6">
                  <p className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-moss">Participant link</p>
                  <h2 className="mt-2 font-serif text-2xl text-foreground">Collect responses anywhere</h2>
                  <p className="mt-2 text-[0.78125rem] leading-relaxed text-muted-foreground">The public form works without a SixSentences account. Participant identity is optional and off by default.</p>
                  <div className="mt-5 flex items-center gap-2 rounded-2xl border border-border bg-secondary/40 p-2 pl-4">
                    <span className="min-w-0 flex-1 truncate font-mono text-[0.6875rem] text-muted-foreground">{publicUrl}</span>
                    <Button size="sm" className="rounded-full" onClick={() => { void navigator.clipboard.writeText(publicUrl); toast.success("Survey link copied."); }}><Clipboard className="size-3.5" /> Copy</Button>
                  </div>
                  <Button asChild variant="outline" className="mt-3 rounded-full" disabled={draft.status !== "live"}>
                    <a href={`/s/${surveyId}`} target="_blank" rel="noreferrer">Preview form <ExternalLink className="size-3.5" /></a>
                  </Button>
                </section>
                <section className="min-w-0 rounded-3xl border border-border bg-card p-6">
                  <p className="flex items-center gap-2 text-[0.8125rem] font-medium text-foreground"><MessageSquareText className="size-4 text-moss" /> Participant experience</p>
                  <label className="mt-5 flex items-center justify-between gap-4 rounded-2xl bg-secondary/45 p-3 text-[0.75rem]">
                    <span><span className="block font-medium text-foreground">Ask for a respondent label</span><span className="text-muted-foreground">Useful for invited panels; still optional.</span></span>
                    <Switch checked={Boolean(draft.settings.collect_identity)} onCheckedChange={(checked) => setDraft({ ...draft, settings: { ...draft.settings, collect_identity: checked } })} />
                  </label>
                  <div className="mt-3 rounded-2xl border border-border p-3">
                    <label className="flex items-center justify-between gap-4 text-[0.75rem]">
                      <span className="flex min-w-0 items-start gap-2.5">
                        <LockKeyhole className="mt-0.5 size-4 shrink-0 text-moss" />
                        <span><span className="block font-medium text-foreground">Require a password</span><span className="text-muted-foreground">Only participants with the password can open or submit the form.</span></span>
                      </span>
                      <Switch
                        checked={Boolean(draft.settings.password_protected) || passwordSetupOpen}
                        disabled={passwordRemove.isPending}
                        onCheckedChange={(checked) => {
                          if (checked) setPasswordSetupOpen(true);
                          else if (draft.settings.password_protected) passwordRemove.mutate();
                          else setPasswordSetupOpen(false);
                        }}
                      />
                    </label>
                    {(draft.settings.password_protected || passwordSetupOpen) && (
                      <div className="mt-3 flex gap-2 border-t border-border pt-3">
                        <Input
                          type="password"
                          autoComplete="new-password"
                          value={surveyPassword}
                          onChange={(event) => setSurveyPassword(event.target.value)}
                          placeholder={draft.settings.password_protected ? "Set a new password" : "At least 8 characters"}
                          className="h-9"
                        />
                        <Button
                          size="sm"
                          variant="outline"
                          className="h-9 shrink-0 rounded-full"
                          disabled={surveyPassword.length < 8 || passwordUpdate.isPending}
                          onClick={() => passwordUpdate.mutate(surveyPassword)}
                        >
                          {passwordUpdate.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <Check className="size-3.5" />}
                          {draft.settings.password_protected ? "Change" : "Protect"}
                        </Button>
                      </div>
                    )}
                  </div>
                  <p className="mt-4 text-[0.6875rem] font-medium text-muted-foreground">Confirmation message</p>
                  <Textarea value={draft.settings.confirmation ?? ""} onChange={(event) => setDraft({ ...draft, settings: { ...draft.settings, confirmation: event.target.value } })} className="mt-2 min-h-24" />
                  <p className="mt-4 flex items-start gap-2 text-[0.6875rem] leading-relaxed text-muted-foreground"><Link2 className="mt-0.5 size-3.5 shrink-0 text-moss" /> Responses remain connected to this survey and can be exported into the wider Data Hub workflow.</p>
                </section>
              </div>
            )}
          </div>
        </main>
      </ResizableWorkspaceSplit>
    </div>
  );
}
