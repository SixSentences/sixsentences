"use client";
import { AiInteractionNotice } from "@/components/ai-interaction-notice";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  type CSSProperties,
  type DragEvent,
  type FormEvent,
  Fragment,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ReactCodeMirrorRef } from "@uiw/react-codemirror";
import {
  ArrowLeft,
  ArrowUpRight,
  AlertTriangle,
  BookMarked,
  Check,
  ChevronDown,
  CircleHelp,
  ClipboardList,
  Code2,
  Copy,
  Crosshair,
  Database,
  Download,
  Ellipsis,
  FileClock,
  FilePlus2,
  FileText,
  FileWarning,
  History,
  ImageIcon,
  FolderTree,
  GripVertical,
  ListPlus,
  ListTree,
  Loader2,
  Mic2,
  Pencil,
  PanelLeftClose,
  PanelLeftOpen,
  Pilcrow,
  Play,
  Quote,
  Repeat2,
  ScrollText,
  Search,
  SendHorizontal,
  ShieldCheck,
  Shapes,
  Square,
  Table2,
  Terminal,
  Trash2,
  Users,
  Wand2,
  Wrench,
  Workflow,
  X,
  Zap,
} from "lucide-react";
import { toast } from "sonner";

import {
  AgentActivityTimeline,
  AgentTimelineHandoffs,
  AgentTurnElapsed,
  SpecialistCompletedTurn,
  agentTurnIdsFromTimelines,
  useAgentTimelineLedger,
} from "@/components/agent-work-status";
import {
  MobileWorkspaceSwitch,
  type MobileWorkspacePane,
} from "@/components/mobile-workspace-switch";

import FollowUpChips from "@/components/ai/follow-up-chips";
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
import type { ReviewCommentInput } from "@/components/review/comment-form";
import ModelPicker from "@/components/search/model-picker";
import { usePrivateModelPreference } from "@/hooks/use-private-model-preference";
import { useSidebarUi } from "@/components/shell/app-shell";
import WriterReviewDialog from "@/components/writer/review-dialog";
import { RepositoryProseProvenance } from "@/components/writer/repository-prose-provenance";
import WriterSharePopover from "@/components/writer/share-popover";
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
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Textarea } from "@/components/ui/textarea";
import { useRuns } from "@/hooks/queries";
import { useDurableSpecialistTurn } from "@/hooks/use-durable-specialist-turn";
import {
  ApiError,
  SpecialistStreamError,
  api,
  createSpecialistTurnId,
  downloadWriterBib,
  downloadWriterProject,
  fetchWriterAssetImageUrl,
  fetchWriterPdfUrl,
  fileToBase64,
  retryTransientApiQuery,
  transientApiRetryDelay,
} from "@/lib/api";
import { buildWriterFollowUps } from "@/lib/follow-ups";
import { formatDateTime } from "@/lib/format";
import { useActiveProject } from "@/lib/project-context";
import {
  clampWorkspaceSplitPercent,
  isWorkspaceSplitFeasible,
} from "@/lib/workspace-split-layout.mjs";
import { countLatexWords } from "@/lib/writer-word-count";
import {
  buildWriterSourceSelection,
  writerPdfPageLabel,
  type WriterSelection,
} from "@/lib/writer-selection";
import {
  activeWriterEditReviewSet,
  collectWriterEditReviews,
  nextWriterEditReview,
  type WriterEditReview,
} from "@/lib/writer-edit-review";
import type {
  WriterCitation,
  WriterCollaboratorCatalog,
  WriterComment,
  WriterDocument,
  WriterEdit,
  WriterInterviewContextCatalog,
  WriterInterviewContextItem,
  WriterInterviewContextMode,
  WriterMessage,
  WriterProjectFile,
  WriterPresence,
  WriterRetargetPreview,
  WriterSource,
  WriterSurveyContextCatalog,
  WriterSurveyContextItem,
  WriterSurveyContextMode,
  WriterVisualRequest,
} from "@/lib/types";
import { cn } from "@/lib/utils";

// pdf.js / CodeMirror touch browser globals at import time
const WriterPdfPreview = dynamic(
  () => import("@/components/writer/pdf-preview"),
  { ssr: false },
);
const CodeEditor = dynamic(() => import("@/components/writer/code-editor"), {
  ssr: false,
  loading: () => (
    <Loader2 className="mx-auto my-16 size-5 animate-spin text-muted-foreground" />
  ),
});

const RETARGET_TEMPLATES = [
  ["article", "Article"],
  ["review", "Literature review"],
  ["thesis", "Thesis"],
  ["ieee", "IEEE conference"],
  ["report", "Seminar report"],
  ["proposal", "Research proposal"],
  ["abstract", "Extended abstract"],
  ["poster", "Conference poster"],
] as const;

const WRITER_CHAT_MIN_PX = 420;
const WRITER_EDITOR_MIN_PX = 560;
const WRITER_PROJECT_RAIL_PX = 208;
const WRITER_DIVIDER_PX = 6;
const WRITER_DEFAULT_CHAT_PERCENT = 50;
const WRITER_CHAT_MIN_PERCENT = 38;
const WRITER_CHAT_MAX_PERCENT = 58;

type WriterToolbarPanel =
  | "cite"
  | "sources"
  | "data"
  | "study-data"
  | "visuals"
  | "versions";

const WRITER_TOOLBAR_PANEL_COPY: Record<
  WriterToolbarPanel,
  { title: string; description: string }
> = {
  cite: {
    title: "Cite included papers",
    description: "Insert a verified BibTeX key at the current source cursor.",
  },
  sources: {
    title: "Manuscript sources",
    description: "Manage uploaded PDF, BibTeX and RIS evidence for this manuscript.",
  },
  data: {
    title: "Linked datasets",
    description: "Choose the research datasets available to the manuscript agent.",
  },
  "study-data": {
    title: "Interview and survey evidence",
    description: "Control the first-party research context available to the manuscript.",
  },
  visuals: {
    title: "Manuscript visuals",
    description: "Upload figures or insert an existing visual at the source cursor.",
  },
  versions: {
    title: "Version history",
    description: "Compare snapshots or restore an earlier manuscript state.",
  },
};

/* ---------- cite popover ---------- */

function CiteList({
  docId,
  onInsert,
}: {
  docId: string;
  onInsert: (key: string) => void;
}) {
  const [query, setQuery] = useState("");
  const { data: citations } = useQuery({
    queryKey: ["writer-citations", docId],
    queryFn: () => api.writerCitations(docId),
  });
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return citations ?? [];
    return (citations ?? []).filter(
      (c: WriterCitation) =>
        c.title.toLowerCase().includes(needle) ||
        c.key.toLowerCase().includes(needle) ||
        c.authors.some((a) => a.toLowerCase().includes(needle)),
    );
  }, [citations, query]);

  return (
    <div className="flex max-h-[26rem] flex-col">
      <div className="relative shrink-0">
        <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search your includes…"
          className="h-9 rounded-lg pl-8 text-[0.8125rem]"
        />
      </div>
      <div className="mt-2 min-h-0 flex-1 overflow-y-auto">
        {(citations ?? []).length === 0 ? (
          <p className="rounded-xl border border-dashed border-border px-3 py-6 text-center text-[0.75rem] leading-relaxed text-muted-foreground">
            Link a finished search and its included papers appear here, ready
            to cite.
          </p>
        ) : (
          <ul className="space-y-1.5">
            {filtered.map((citation: WriterCitation) => (
              <li key={citation.key}>
                <button
                  type="button"
                  onClick={() => onInsert(citation.key)}
                  className="group w-full cursor-pointer rounded-xl border border-border bg-card px-3 py-2 text-left transition-colors hover:border-moss/50"
                  title={`Insert \\citep{${citation.key}}`}
                >
                  <p className="line-clamp-2 text-[0.78125rem] font-medium leading-snug text-foreground">
                    {citation.title}
                  </p>
                  <p className="mt-1 flex items-center justify-between font-mono text-[0.625rem] text-muted-foreground">
                    <span className="truncate">
                      {citation.authors[0] ?? ""} · {citation.year ?? "n.d."}
                    </span>
                    <span className="ml-2 shrink-0 rounded bg-secondary px-1.5 py-0.5 text-moss opacity-100 transition-opacity sm:opacity-0 sm:group-hover:opacity-100">
                      insert
                    </span>
                  </p>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

/* ---------- owned sources + research data ---------- */

function SourceList({ docId, onInsert }: { docId: string; onInsert: (key: string) => void }) {
  const queryClient = useQueryClient();
  const [deleteTarget, setDeleteTarget] = useState<{ id: number; title: string } | null>(null);
  const deleteInFlightRef = useRef(false);
  useEffect(() => {
    deleteInFlightRef.current = false;
    setDeleteTarget(null);
  }, [docId]);
  const inputRef = useRef<HTMLInputElement>(null);
  const { data: sources } = useQuery({
    queryKey: ["writer-sources", docId],
    queryFn: () => api.writerSources(docId),
  });
  const upload = useMutation({
    mutationFn: async (file: File) =>
      api.writerSourceUpload(docId, file.name, await fileToBase64(file)),
    onSuccess: (created) => {
      toast.success(
        created.length === 1
          ? `${created[0].title} is ready to cite.`
          : `${created.length} references imported.`,
      );
      void queryClient.invalidateQueries({ queryKey: ["writer-sources", docId] });
      void queryClient.invalidateQueries({ queryKey: ["writer-citations", docId] });
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "Import failed."),
  });
  const remove = useMutation({
    mutationFn: (id: number) => api.writerSourceDelete(docId, id),
    onSuccess: () => {
      deleteInFlightRef.current = false;
      setDeleteTarget(null);
      void queryClient.invalidateQueries({ queryKey: ["writer-sources", docId] });
      void queryClient.invalidateQueries({ queryKey: ["writer-citations", docId] });
    },
    onError: (error) => {
      deleteInFlightRef.current = false;
      toast.error(error instanceof Error ? error.message : "Could not delete the source.");
    },
  });
  return (
    <div className="space-y-3">
      <input
        ref={inputRef}
        type="file"
        accept=".pdf,.bib,.bibtex,.ris"
        className="hidden"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) upload.mutate(file);
          event.target.value = "";
        }}
      />
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        disabled={upload.isPending}
        className="flex w-full cursor-pointer items-center gap-3 rounded-2xl border border-dashed border-border bg-secondary/35 p-3 text-left transition-colors hover:border-moss/50 disabled:cursor-wait"
      >
        <div className="grid size-9 shrink-0 place-items-center rounded-xl bg-card">
          {upload.isPending ? (
            <Loader2 className="size-4 animate-spin text-moss" />
          ) : (
            <FilePlus2 className="size-4 text-moss" />
          )}
        </div>
        <div>
          <p className="text-[0.78125rem] font-medium text-foreground">
            Add your own sources
          </p>
          <p className="mt-0.5 text-[0.65625rem] text-muted-foreground">PDF, BibTeX or RIS</p>
        </div>
      </button>
      {(sources ?? []).length === 0 ? (
        <p className="px-2 py-3 text-center text-[0.71875rem] leading-relaxed text-muted-foreground">
          Uploaded sources sit beside papers from your searches and receive stable citation keys.
        </p>
      ) : (
        <ul className="max-h-64 space-y-1.5 overflow-y-auto">
          {(sources ?? []).map((source: WriterSource) => (
            <li key={source.id} className="flex items-center gap-2 rounded-xl border border-border bg-card p-2.5">
              <FileText className="size-3.5 shrink-0 text-moss" />
              <button type="button" onClick={() => onInsert(source.cite_key)} className="min-w-0 flex-1 cursor-pointer text-left">
                <p className="truncate text-[0.75rem] font-medium text-foreground">{source.title}</p>
                <p className="mt-0.5 font-mono text-[0.59375rem] text-muted-foreground">{source.cite_key} · {source.readable ? "AI readable" : "metadata"}</p>
              </button>
              <button type="button" onClick={() => setDeleteTarget({ id: source.id, title: source.title })} aria-label={`Delete ${source.title}`} className="cursor-pointer text-muted-foreground hover:text-destructive"><Trash2 className="size-3" /></button>
            </li>
          ))}
        </ul>
      )}
      <ConfirmDeleteDialog
        target={
          deleteTarget
            ? {
                title: "Delete this manuscript source?",
                description: `“${deleteTarget.title}” and its imported source file will be removed from this manuscript. This cannot be undone.`,
                action: "Delete source",
                cancel: "Keep source",
              }
            : null
        }
        pending={remove.isPending}
        onCancel={() => {
          if (!remove.isPending && !deleteInFlightRef.current) setDeleteTarget(null);
        }}
        onConfirm={() => {
          if (!deleteTarget || remove.isPending || deleteInFlightRef.current) return;
          const current = (sources ?? []).find((source) => source.id === deleteTarget.id);
          if (!current || current.title !== deleteTarget.title) {
            setDeleteTarget(null);
            toast.error("The manuscript source changed. Nothing was deleted.");
            return;
          }
          deleteInFlightRef.current = true;
          remove.mutate(current.id);
        }}
      />
    </div>
  );
}

function DatasetList({
  docId,
  linked,
}: {
  docId: string;
  linked: string[];
}) {
  const headingId = useId();
  const descriptionId = useId();
  const queryClient = useQueryClient();
  const {
    data: datasets,
    isLoading: datasetsLoading,
    isError: datasetsFailed,
    refetch: refetchDatasets,
  } = useQuery({ queryKey: ["datasets"], queryFn: api.datasets });
  const { data: surveys } = useQuery({ queryKey: ["surveys"], queryFn: api.surveys });
  const toggle = useMutation({
    mutationFn: (publicId: string) => {
      const next = linked.includes(publicId)
        ? linked.filter((id) => id !== publicId)
        : [...linked, publicId];
      return api.writerPatch(docId, { dataset_ids: next });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["writer-doc", docId] });
      void queryClient.invalidateQueries({ queryKey: ["writer-docs"] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Dataset link could not be updated."),
  });
  const importSurvey = useMutation({
    mutationFn: (surveyId: string) => api.surveyImportDataset(surveyId, docId),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ["datasets"] });
      void queryClient.invalidateQueries({ queryKey: ["surveys"] });
      void queryClient.invalidateQueries({ queryKey: ["writer-doc", docId] });
      void queryClient.invalidateQueries({ queryKey: ["writer-docs"] });
      toast.success(`Survey results linked as data version ${result.version}.`);
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "Survey import failed."),
  });
  const surveysWithResponses = (surveys ?? []).filter((survey) => survey.response_count > 0);
  const datasetItems = datasets ?? [];
  const datasetsUnavailable = datasetsFailed && datasets === undefined;

  return (
    <section
      aria-labelledby={headingId}
      aria-describedby={descriptionId}
      className="min-w-0 overflow-x-hidden"
    >
      <div className="flex min-w-0 items-start justify-between gap-3 border-b border-border px-4 py-3.5">
        <div className="min-w-0">
          <h2 id={headingId} className="text-[0.8125rem] font-medium text-foreground">
            Workspace data
          </h2>
          <p
            id={descriptionId}
            className="mt-1 max-w-[30rem] text-[0.6875rem] leading-relaxed text-muted-foreground"
          >
            Linked data is available to the writing assistant with exact values,
            missingness and provenance.
          </p>
        </div>
        <span className="shrink-0 rounded-full bg-secondary px-2 py-1 font-mono text-[0.5625rem] uppercase tracking-[0.08em] text-muted-foreground">
          {linked.length} linked
        </span>
      </div>

      <div className="min-w-0 space-y-4 p-3.5">
        <Button
          asChild
          variant="outline"
          size="sm"
          className="h-9 w-full min-w-0 justify-between rounded-lg px-3 text-[0.75rem]"
        >
          <Link href="/data">
            <span className="inline-flex min-w-0 items-center gap-2">
              <Database className="size-3.5" />
              <span>Open Data Hub</span>
            </span>
            <ArrowUpRight className="size-3.5 text-muted-foreground" />
          </Link>
        </Button>

        {surveysWithResponses.length > 0 ? (
          <div className="min-w-0">
            <div className="mb-2 flex items-center justify-between gap-3 px-1">
              <h3 className="font-mono text-[0.5625rem] uppercase tracking-[0.18em] text-muted-foreground">
                Survey results
              </h3>
              <span className="shrink-0 text-[0.625rem] text-muted-foreground">
                {surveysWithResponses.length} available
              </span>
            </div>
            <ul className="max-h-44 min-w-0 space-y-1.5 overflow-x-hidden overflow-y-auto pr-0.5">
              {surveysWithResponses.map((survey) => {
                const importedId = survey.settings.result_dataset_id;
                const active = Boolean(importedId && linked.includes(importedId));
                const pending =
                  importSurvey.isPending && importSurvey.variables === survey.public_id;
                return (
                  <li
                    key={survey.public_id}
                    className="flex min-w-0 items-center gap-2 rounded-xl border border-border bg-card p-2.5"
                  >
                    <span
                      aria-hidden="true"
                      className={cn(
                        "grid size-7 shrink-0 place-items-center rounded-lg",
                        active
                          ? "bg-moss-surface text-ivory"
                          : "bg-secondary text-moss",
                      )}
                    >
                      {active ? (
                        <Check className="size-3.5" />
                      ) : (
                        <ClipboardList className="size-3.5" />
                      )}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block [overflow-wrap:anywhere] text-[0.71875rem] font-medium leading-snug text-foreground">
                        {survey.title}
                      </span>
                      <span className="block font-mono text-[0.5625rem] leading-relaxed text-muted-foreground">
                        {survey.response_count} responses{active ? " · linked" : ""}
                      </span>
                    </span>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-7 shrink-0 rounded-full px-2.5 text-[0.65625rem]"
                      disabled={importSurvey.isPending}
                      aria-label={`${active ? "Sync" : "Import"} survey results for ${survey.title}`}
                      onClick={() => importSurvey.mutate(survey.public_id)}
                    >
                      {pending ? <Loader2 className="size-3 animate-spin" /> : null}
                      {active ? "Sync" : "Import"}
                    </Button>
                  </li>
                );
              })}
            </ul>
          </div>
        ) : null}

        <div className="min-w-0">
          <div className="mb-2 flex items-center justify-between gap-3 px-1">
            <h3 className="font-mono text-[0.5625rem] uppercase tracking-[0.18em] text-muted-foreground">
              Available datasets
            </h3>
            {!datasetsLoading && !datasetsUnavailable ? (
              <span className="shrink-0 text-[0.625rem] text-muted-foreground">
                {datasetItems.length} available
              </span>
            ) : null}
          </div>

          {datasetsLoading ? (
            <div
              role="status"
              className="flex min-h-24 items-center justify-center gap-2 rounded-xl border border-dashed border-border px-3 py-5 text-[0.71875rem] text-muted-foreground"
            >
              <Loader2 className="size-3.5 animate-spin" />
              Loading workspace data…
            </div>
          ) : datasetsUnavailable ? (
            <div
              role="alert"
              className="rounded-xl border border-border bg-secondary/30 px-3 py-4 text-center"
            >
              <p className="text-[0.71875rem] leading-relaxed text-muted-foreground">
                Workspace data could not be loaded.
              </p>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="mt-2 h-7 rounded-full text-[0.6875rem]"
                onClick={() => void refetchDatasets()}
              >
                Try again
              </Button>
            </div>
          ) : datasetItems.length === 0 ? (
            <div className="rounded-xl border border-dashed border-border px-3 py-5 text-center">
              <p className="text-[0.75rem] font-medium text-foreground">
                No workspace datasets yet
              </p>
              <p className="mt-1 text-[0.6875rem] leading-relaxed text-muted-foreground">
                Import results in Data Hub, then connect them to this manuscript.
              </p>
            </div>
          ) : (
            <ul className="max-h-64 min-w-0 space-y-1.5 overflow-x-hidden overflow-y-auto pr-0.5">
              {datasetItems.map((dataset) => {
                const active = linked.includes(dataset.public_id);
                const pending =
                  toggle.isPending && toggle.variables === dataset.public_id;
                return (
                  <li key={dataset.public_id}>
                    <button
                      type="button"
                      aria-pressed={active}
                      aria-label={`${active ? "Unlink" : "Link"} dataset ${dataset.name}`}
                      disabled={toggle.isPending}
                      onClick={() => toggle.mutate(dataset.public_id)}
                      className={cn(
                        "flex w-full min-w-0 items-start gap-3 rounded-xl border px-3 py-2.5 text-left transition-colors outline-none focus-visible:border-moss/60 focus-visible:ring-2 focus-visible:ring-moss/35 disabled:cursor-wait disabled:opacity-70",
                        active
                          ? "border-moss/45 bg-accent"
                          : "border-border bg-card hover:border-moss/35",
                      )}
                    >
                      <span
                        aria-hidden="true"
                        className={cn(
                          "mt-0.5 grid size-5 shrink-0 place-items-center rounded-full border",
                          active
                            ? "border-moss bg-moss-surface text-ivory"
                            : "border-border",
                        )}
                      >
                        {pending ? (
                          <Loader2 className="size-3 animate-spin" />
                        ) : active ? (
                          <Check className="size-3" />
                        ) : null}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block [overflow-wrap:anywhere] text-[0.75rem] font-medium leading-snug text-foreground">
                          {dataset.name}
                        </span>
                        <span className="mt-1 flex flex-wrap gap-x-2 gap-y-0.5 font-mono text-[0.59375rem] leading-relaxed text-muted-foreground">
                          <span>{dataset.row_count.toLocaleString()} rows</span>
                          <span aria-hidden="true">·</span>
                          <span>{dataset.column_count} columns</span>
                        </span>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </div>
    </section>
  );
}

/* ---------- linked interview evidence ---------- */

function interviewDuration(milliseconds: number): string {
  const minutes = Math.max(1, Math.round(milliseconds / 60_000));
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return `${hours}h${remainder ? ` ${remainder}m` : ""}`;
}

function InterviewContextList({ docId }: { docId: string }) {
  const queryClient = useQueryClient();
  const [query, setQuery] = useState("");
  const {
    data: catalog,
    isLoading,
    isError,
    error,
    refetch,
  } = useQuery({
    queryKey: ["writer-interview-contexts", docId],
    queryFn: () => api.writerInterviewContexts(docId),
  });
  const save = useMutation({
    mutationFn: (
      sources: Array<{
        interview_id: string;
        mode: WriterInterviewContextMode;
        include_methodology: boolean;
      }>,
    ) => api.writerInterviewContextsSet(docId, sources),
    onSuccess: (updated) => {
      queryClient.setQueryData<WriterInterviewContextCatalog>(
        ["writer-interview-contexts", docId],
        updated,
      );
    },
    onError: (error) =>
      toast.error(
        error instanceof Error ? error.message : "Interview context could not be updated.",
      ),
  });

  const currentSources = () =>
    (catalog?.items ?? [])
      .filter(
        (
          item,
        ): item is WriterInterviewContextItem & {
          mode: WriterInterviewContextMode;
        } => item.linked && item.mode !== null,
      )
      .map((item) => ({
        interview_id: item.interview_id,
        mode: item.mode,
        include_methodology: item.include_methodology,
      }));

  const setMode = (
    interviewId: string,
    mode: WriterInterviewContextMode | null,
  ) => {
    const current = currentSources().filter(
      (source) => source.interview_id !== interviewId,
    );
    save.mutate(
      mode
        ? [
            ...current,
            {
              interview_id: interviewId,
              mode,
              include_methodology:
                catalog?.items.find((item) => item.interview_id === interviewId)
                  ?.include_methodology ?? false,
            },
          ]
        : current,
    );
  };

  const setMethodology = (interviewId: string, includeMethodology: boolean) => {
    save.mutate(
      currentSources().map((source) =>
        source.interview_id === interviewId
          ? { ...source, include_methodology: includeMethodology }
          : source,
      ),
    );
  };

  const needle = query.trim().toLocaleLowerCase();
  const visible = (catalog?.items ?? []).filter((item) =>
    needle ? item.title.toLocaleLowerCase().includes(needle) : true,
  );

  return (
    <div className="space-y-3">
      <div className="rounded-xl border border-border bg-secondary/35 px-3 py-2.5">
        <p className="text-[0.75rem] font-medium text-foreground">
          Interview evidence for this manuscript
        </p>
        <p className="mt-1 text-[0.65625rem] leading-relaxed text-muted-foreground">
          Analyses are always available. Transcript mode retrieves only
          relevant timestamped passages when you ask the agent a question.
        </p>
      </div>
      {(catalog?.items.length ?? 0) > 5 ? (
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Find an interview…"
            className="h-8 rounded-full pl-8 text-[0.75rem]"
          />
        </div>
      ) : null}
      {isLoading ? (
        <Loader2 className="mx-auto my-8 size-4 animate-spin text-muted-foreground" />
      ) : isError ? (
        <div className="rounded-xl border border-coral/30 bg-coral/5 px-3 py-5 text-center">
          <p className="text-[0.71875rem] font-medium text-foreground">
            Interview evidence could not be loaded.
          </p>
          <p className="mt-1 text-[0.65625rem] leading-relaxed text-muted-foreground">
            {error instanceof Error
              ? error.message
              : "The research API did not return an interview catalog."}
          </p>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="mt-3 h-7 rounded-full"
            onClick={() => void refetch()}
          >
            Try again
          </Button>
        </div>
      ) : visible.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border px-3 py-6 text-center">
          <p className="text-[0.71875rem] text-muted-foreground">
            {catalog?.items.length
              ? "No interview matches this search."
              : "No completed interview analysis is available yet."}
          </p>
          {!catalog?.items.length ? (
            <Button asChild variant="ghost" size="sm" className="mt-2 h-7 rounded-full">
              <Link href="/interviews">Open Interviews</Link>
            </Button>
          ) : null}
        </div>
      ) : (
        <ul className="max-h-[22rem] space-y-1.5 overflow-y-auto pr-0.5">
          {visible.map((item) => {
            const available = item.status === "ready" && item.analysis_ready;
            return (
              <li
                key={item.interview_id}
                className={cn(
                  "rounded-xl border p-2.5 transition-colors",
                  item.linked
                    ? "border-moss/45 bg-accent/60"
                    : "border-border bg-card",
                  !available && "opacity-60",
                )}
              >
                <div className="flex items-start gap-2.5">
                  <button
                    type="button"
                    disabled={!available || save.isPending}
                    onClick={() =>
                      setMode(
                        item.interview_id,
                        item.linked ? null : "analysis",
                      )
                    }
                    aria-label={
                      item.linked
                        ? `Remove ${item.title} from context`
                        : `Add ${item.title} to context`
                    }
                    className={cn(
                      "mt-0.5 grid size-5 shrink-0 place-items-center rounded-full border transition-colors",
                      item.linked
                        ? "border-moss bg-moss-surface text-ivory"
                        : "border-border bg-card",
                      available && "cursor-pointer",
                    )}
                  >
                    {item.linked ? <Check className="size-3" /> : null}
                  </button>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      <p className="truncate text-[0.75rem] font-medium text-foreground">
                        {item.title}
                      </p>
                      {item.project_match ? (
                        <span className="shrink-0 rounded-full bg-secondary px-1.5 py-0.5 font-mono text-[0.5rem] uppercase tracking-[0.12em] text-moss">
                          project
                        </span>
                      ) : null}
                    </div>
                    <p className="mt-0.5 font-mono text-[0.5625rem] text-muted-foreground">
                      {item.status === "ready"
                        ? `${interviewDuration(item.duration_ms)} · ${item.segment_count} turns`
                        : item.status === "pending"
                          ? "Processing"
                          : "Needs attention"}
                    </p>
                  </div>
                </div>
                {item.linked ? (
                  <div className="mt-2 space-y-1.5">
                    <div className="grid grid-cols-2 gap-1 rounded-lg bg-secondary/65 p-1">
                      <button
                        type="button"
                        disabled={save.isPending}
                        onClick={() => setMode(item.interview_id, "analysis")}
                        className={cn(
                          "h-7 cursor-pointer rounded-md px-2 text-[0.65625rem] transition-colors",
                          item.mode === "analysis"
                            ? "bg-card font-medium text-foreground shadow-sm"
                            : "text-muted-foreground hover:text-foreground",
                        )}
                      >
                        Analysis only
                      </button>
                      <button
                        type="button"
                        disabled={save.isPending}
                        onClick={() => setMode(item.interview_id, "transcript")}
                        className={cn(
                          "h-7 cursor-pointer rounded-md px-2 text-[0.65625rem] transition-colors",
                          item.mode === "transcript"
                            ? "bg-card font-medium text-foreground shadow-sm"
                            : "text-muted-foreground hover:text-foreground",
                        )}
                      >
                        + transcript
                      </button>
                    </div>
                    <button
                      type="button"
                      disabled={save.isPending || !item.methodology_ready}
                      onClick={() =>
                        setMethodology(
                          item.interview_id,
                          !item.include_methodology,
                        )
                      }
                      className={cn(
                        "flex h-8 w-full cursor-pointer items-center gap-2 rounded-lg border px-2.5 text-left text-[0.65625rem] transition-colors",
                        item.include_methodology
                          ? "border-moss/45 bg-accent text-foreground"
                          : "border-border bg-card text-muted-foreground hover:text-foreground",
                      )}
                    >
                      <span
                        className={cn(
                          "grid size-4 shrink-0 place-items-center rounded-full border",
                          item.include_methodology
                            ? "border-moss bg-moss-surface text-ivory"
                            : "border-border",
                        )}
                      >
                        {item.include_methodology ? (
                          <Check className="size-2.5" />
                        ) : null}
                      </span>
                      Include interview method and guide
                    </button>
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
      {(catalog?.linked_count ?? 0) > 0 ? (
        <p className="px-1 text-[0.625rem] leading-relaxed text-muted-foreground">
          {catalog?.linked_count} linked · {catalog?.analysis_count} analysis
          only · {catalog?.transcript_count} with transcript retrieval
          {(catalog?.methodology_count ?? 0) > 0
            ? ` · ${catalog?.methodology_count} with method`
            : ""}
        </p>
      ) : null}
    </div>
  );
}

function SurveyContextList({ docId }: { docId: string }) {
  const queryClient = useQueryClient();
  const [query, setQuery] = useState("");
  const {
    data: catalog,
    isLoading,
    isError,
    error,
    refetch,
  } = useQuery({
    queryKey: ["writer-survey-contexts", docId],
    queryFn: () => api.writerSurveyContexts(docId),
  });
  const save = useMutation({
    mutationFn: (
      sources: Array<{
        survey_id: string;
        mode: WriterSurveyContextMode;
      }>,
    ) => api.writerSurveyContextsSet(docId, sources),
    onSuccess: (updated) => {
      queryClient.setQueryData<WriterSurveyContextCatalog>(
        ["writer-survey-contexts", docId],
        updated,
      );
    },
    onError: (error) =>
      toast.error(
        error instanceof Error
          ? error.message
          : "Survey context could not be updated.",
      ),
  });

  const currentSources = () =>
    (catalog?.items ?? [])
      .filter(
        (
          item,
        ): item is WriterSurveyContextItem & {
          mode: WriterSurveyContextMode;
        } => item.linked && item.mode !== null,
      )
      .map((item) => ({
        survey_id: item.survey_id,
        mode: item.mode,
      }));

  const setMode = (surveyId: string, mode: WriterSurveyContextMode | null) => {
    const current = currentSources().filter(
      (source) => source.survey_id !== surveyId,
    );
    save.mutate(
      mode ? [...current, { survey_id: surveyId, mode }] : current,
    );
  };

  const needle = query.trim().toLocaleLowerCase();
  const visible = (catalog?.items ?? []).filter((item) =>
    needle ? item.title.toLocaleLowerCase().includes(needle) : true,
  );

  return (
    <div className="space-y-3">
      <div className="rounded-xl border border-border bg-secondary/35 px-3 py-2.5">
        <p className="text-[0.75rem] font-medium text-foreground">
          Survey evidence for this manuscript
        </p>
        <p className="mt-1 text-[0.65625rem] leading-relaxed text-muted-foreground">
          The exact questionnaire, order, scales and missingness are always
          included. Add de-identified response rows only when the agent needs
          respondent-level patterns or exact open text.
        </p>
      </div>
      {(catalog?.items.length ?? 0) > 5 ? (
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Find a survey…"
            className="h-8 rounded-full pl-8 text-[0.75rem]"
          />
        </div>
      ) : null}
      {isLoading ? (
        <Loader2 className="mx-auto my-8 size-4 animate-spin text-muted-foreground" />
      ) : isError ? (
        <div className="rounded-xl border border-coral/30 bg-coral/5 px-3 py-5 text-center">
          <p className="text-[0.71875rem] font-medium text-foreground">
            Survey evidence could not be loaded.
          </p>
          <p className="mt-1 text-[0.65625rem] leading-relaxed text-muted-foreground">
            {error instanceof Error
              ? error.message
              : "The research API did not return a survey catalog."}
          </p>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="mt-3 h-7 rounded-full"
            onClick={() => void refetch()}
          >
            Try again
          </Button>
        </div>
      ) : visible.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border px-3 py-6 text-center">
          <p className="text-[0.71875rem] text-muted-foreground">
            {catalog?.items.length
              ? "No survey matches this search."
              : "No survey is available in this workspace yet."}
          </p>
          {!catalog?.items.length ? (
            <Button
              asChild
              variant="ghost"
              size="sm"
              className="mt-2 h-7 rounded-full"
            >
              <Link href="/surveys">Open Surveys</Link>
            </Button>
          ) : null}
        </div>
      ) : (
        <ul className="max-h-[22rem] space-y-1.5 overflow-y-auto pr-0.5">
          {visible.map((item) => (
            <li
              key={item.survey_id}
              className={cn(
                "rounded-xl border p-2.5 transition-colors",
                item.linked
                  ? "border-moss/45 bg-accent/60"
                  : "border-border bg-card",
              )}
            >
              <div className="flex items-start gap-2.5">
                <button
                  type="button"
                  disabled={save.isPending}
                  onClick={() =>
                    setMode(item.survey_id, item.linked ? null : "summary")
                  }
                  aria-label={
                    item.linked
                      ? `Remove ${item.title} from context`
                      : `Add ${item.title} to context`
                  }
                  className={cn(
                    "mt-0.5 grid size-5 shrink-0 cursor-pointer place-items-center rounded-full border transition-colors",
                    item.linked
                      ? "border-moss bg-moss-surface text-ivory"
                      : "border-border bg-card",
                  )}
                >
                  {item.linked ? <Check className="size-3" /> : null}
                </button>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <p className="truncate text-[0.75rem] font-medium text-foreground">
                      {item.title}
                    </p>
                    {item.project_match ? (
                      <span className="shrink-0 rounded-full bg-secondary px-1.5 py-0.5 font-mono text-[0.5rem] uppercase tracking-[0.12em] text-moss">
                        project
                      </span>
                    ) : null}
                  </div>
                  <p className="mt-0.5 font-mono text-[0.5625rem] text-muted-foreground">
                    {item.question_count} questions · {item.response_count}{" "}
                    responses · {item.status}
                  </p>
                </div>
              </div>
              {item.linked ? (
                <div className="mt-2 grid grid-cols-2 gap-1 rounded-lg bg-secondary/65 p-1">
                  <button
                    type="button"
                    disabled={save.isPending}
                    onClick={() => setMode(item.survey_id, "summary")}
                    className={cn(
                      "h-7 cursor-pointer rounded-md px-2 text-[0.65625rem] transition-colors",
                      item.mode === "summary"
                        ? "bg-card font-medium text-foreground shadow-sm"
                        : "text-muted-foreground hover:text-foreground",
                    )}
                  >
                    Results summary
                  </button>
                  <button
                    type="button"
                    disabled={save.isPending}
                    onClick={() => setMode(item.survey_id, "responses")}
                    className={cn(
                      "h-7 cursor-pointer rounded-md px-2 text-[0.65625rem] transition-colors",
                      item.mode === "responses"
                        ? "bg-card font-medium text-foreground shadow-sm"
                        : "text-muted-foreground hover:text-foreground",
                    )}
                  >
                    + response rows
                  </button>
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      )}
      {(catalog?.linked_count ?? 0) > 0 ? (
        <p className="px-1 text-[0.625rem] leading-relaxed text-muted-foreground">
          {catalog?.linked_count} linked · {catalog?.summary_count} summarized
          · {catalog?.responses_count} with response retrieval
        </p>
      ) : null}
    </div>
  );
}

function PrimaryResearchContext({ docId }: { docId: string }) {
  const [section, setSection] = useState<"interviews" | "surveys">(
    "interviews",
  );
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-1 rounded-xl bg-secondary/65 p-1">
        <button
          type="button"
          onClick={() => setSection("interviews")}
          className={cn(
            "flex h-8 cursor-pointer items-center justify-center gap-1.5 rounded-lg text-[0.6875rem] transition-colors",
            section === "interviews"
              ? "bg-card font-medium text-foreground shadow-sm"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          <Mic2 className="size-3.5" />
          Interviews
        </button>
        <button
          type="button"
          onClick={() => setSection("surveys")}
          className={cn(
            "flex h-8 cursor-pointer items-center justify-center gap-1.5 rounded-lg text-[0.6875rem] transition-colors",
            section === "surveys"
              ? "bg-card font-medium text-foreground shadow-sm"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          <ClipboardList className="size-3.5" />
          Surveys
        </button>
      </div>
      {section === "interviews" ? (
        <InterviewContextList docId={docId} />
      ) : (
        <SurveyContextList docId={docId} />
      )}
    </div>
  );
}

/* ---------- figures popover ---------- */

const IMAGE_ASSET = /\.(png|jpe?g|webp|gif)$/i;

/** A small preview so the Visuals menu shows WHICH figure a file is. */
function AssetThumb({
  docId,
  assetId,
  byteSize,
}: {
  docId: string;
  assetId: number;
  byteSize: number;
}) {
  // byteSize busts the cache when a re-attach refreshes the pixels
  const { data: url } = useQuery({
    queryKey: ["writer-asset-image", docId, assetId, byteSize],
    queryFn: () => fetchWriterAssetImageUrl(docId, assetId),
    staleTime: Infinity,
  });
  useEffect(() => {
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, [url]);
  if (!url) {
    return <span className="size-9 shrink-0 rounded-lg bg-secondary" />;
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={url}
      alt=""
      className="size-9 shrink-0 rounded-lg border border-border bg-white object-contain"
    />
  );
}

function FigureList({
  docId,
  onInsert,
}: {
  docId: string;
  onInsert: (filename: string) => void;
}) {
  const queryClient = useQueryClient();
  const [deleteTarget, setDeleteTarget] = useState<{ id: number; filename: string } | null>(null);
  const deleteInFlightRef = useRef(false);
  useEffect(() => {
    deleteInFlightRef.current = false;
    setDeleteTarget(null);
  }, [docId]);
  const fileRef = useRef<HTMLInputElement>(null);
  const { data: assets, isPending, error: assetsError, isFetching, refetch } = useQuery({
    queryKey: ["writer-assets", docId],
    queryFn: () => api.writerAssets(docId),
  });
  const upload = useMutation({
    mutationFn: async (file: File) =>
      api.writerAssetUpload(docId, file.name, await fileToBase64(file)),
    onSuccess: (asset) => {
      toast.success(`${asset.filename} uploaded.`);
      void queryClient.invalidateQueries({
        queryKey: ["writer-assets", docId],
      });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Upload failed."),
  });
  const remove = useMutation({
    mutationFn: (assetId: number) => api.writerAssetDelete(docId, assetId),
    onSuccess: () => {
      deleteInFlightRef.current = false;
      setDeleteTarget(null);
      void queryClient.invalidateQueries({
        queryKey: ["writer-assets", docId],
      });
      toast.success("Figure asset deleted.");
    },
    onError: (error) => {
      deleteInFlightRef.current = false;
      toast.error(
        error instanceof Error ? error.message : "That didn't work.",
      );
    },
  });

  return (
    <div className="space-y-2">
      <input
        ref={fileRef}
        type="file"
        accept=".png,.jpg,.jpeg,.pdf"
        className="hidden"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) upload.mutate(file);
          event.target.value = "";
        }}
      />
      <Button
        type="button"
        size="sm"
        variant="outline"
        disabled={upload.isPending}
        onClick={() => fileRef.current?.click()}
        className="h-8 w-full rounded-full text-[0.75rem]"
      >
        {upload.isPending ? (
          <Loader2 className="size-3 animate-spin" />
        ) : (
          "Upload figure (.png, .jpg, .pdf)"
        )}
      </Button>
      <Button asChild type="button" size="sm" variant="outline" className="h-8 w-full rounded-full text-[0.75rem]">
        <a href={`/figures?writer=${encodeURIComponent(docId)}`}>
          <Shapes className="size-3" /> Open Visual Lab
        </a>
      </Button>
      {assetsError && (
        <div role="alert" className="space-y-2 rounded-xl border border-border px-3 py-4 text-[0.71875rem]">
          <p>Figures could not be loaded. Your existing attachments have not been removed.</p>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={isFetching}
            onClick={() => void refetch()}
          >
            {isFetching ? "Retrying…" : "Try again"}
          </Button>
        </div>
      )}
      {isPending ? (
        <p role="status" className="flex items-center justify-center gap-2 px-3 py-4 text-[0.71875rem] text-muted-foreground">
          <Loader2 aria-hidden="true" className="size-3.5 animate-spin" />
          Loading figures…
        </p>
      ) : !assets ? null : assets.length === 0 ? (
        !assetsError &&
        <p className="rounded-xl border border-dashed border-border px-3 py-4 text-center text-[0.71875rem] leading-relaxed text-muted-foreground">
          Uploaded figures join every compile, and the assistant can embed
          them for you.
        </p>
      ) : (
        <ul className="max-h-56 space-y-1.5 overflow-y-auto">
          {(assets ?? []).map((asset) => (
            <li
              key={asset.id}
              className="flex items-center gap-2 rounded-xl border border-border bg-card px-3 py-2"
            >
              {IMAGE_ASSET.test(asset.filename) ? (
                <AssetThumb
                  docId={docId}
                  assetId={asset.id}
                  byteSize={asset.byte_size}
                />
              ) : (
                <ImageIcon className="size-3.5 shrink-0 text-moss" />
              )}
              <span className="min-w-0 flex-1 truncate font-mono text-[0.71875rem]">
                {asset.filename}
              </span>
              <button
                type="button"
                onClick={() => onInsert(asset.filename)}
                className="cursor-pointer font-mono text-[0.625rem] uppercase tracking-[0.18em] text-moss hover:underline"
              >
                insert
              </button>
              <button
                type="button"
                onClick={() => setDeleteTarget({ id: asset.id, filename: asset.filename })}
                aria-label={`Delete ${asset.filename}`}
                className="cursor-pointer text-muted-foreground hover:text-destructive"
              >
                <Trash2 className="size-3" />
              </button>
            </li>
          ))}
        </ul>
      )}
      <ConfirmDeleteDialog
        target={
          deleteTarget
            ? {
                title: "Delete this figure asset?",
                description: `“${deleteTarget.filename}” will be removed from this manuscript project and may no longer compile where it is referenced. This cannot be undone.`,
                action: "Delete figure",
                cancel: "Keep figure",
              }
            : null
        }
        pending={remove.isPending}
        onCancel={() => {
          if (!remove.isPending && !deleteInFlightRef.current) setDeleteTarget(null);
        }}
        onConfirm={() => {
          if (!deleteTarget || remove.isPending || deleteInFlightRef.current) return;
          const current = (assets ?? []).find((asset) => asset.id === deleteTarget.id);
          if (!current || current.filename !== deleteTarget.filename) {
            setDeleteTarget(null);
            toast.error("The figure asset changed. Nothing was deleted.");
            return;
          }
          deleteInFlightRef.current = true;
          remove.mutate(current.id);
        }}
      />
    </div>
  );
}

/* ---------- version history ---------- */

function SnapshotList({
  docId,
  onRestored,
  canRestore,
}: {
  docId: string;
  onRestored: (content: string) => void;
  canRestore: boolean;
}) {
  const queryClient = useQueryClient();
  const [compareId, setCompareId] = useState<number | null>(null);
  const { data: snapshots, isLoading, error: snapshotsError, refetch, isFetching } = useQuery({
    queryKey: ["writer-snapshots", docId],
    queryFn: () => api.writerSnapshots(docId),
  });
  const restore = useMutation({
    mutationFn: (snapshotId: number) => api.writerRestore(docId, snapshotId),
    onSuccess: (restored) => {
      onRestored(restored.content);
      toast.success("Restored. The previous state was snapshotted first.");
      void queryClient.invalidateQueries({ queryKey: ["writer-doc", docId] });
      void queryClient.invalidateQueries({ queryKey: ["writer-files", docId] });
      void queryClient.invalidateQueries({
        queryKey: ["writer-snapshots", docId],
      });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Restore failed."),
  });
  const comparison = useQuery({
    queryKey: ["writer-snapshot-diff", docId, compareId],
    queryFn: () => api.writerSnapshotDiff(docId, compareId as number),
    enabled: compareId !== null,
  });

  if (isLoading) {
    return <p role="status" className="px-3 py-5 text-sm text-muted-foreground">Loading version history…</p>;
  }
  if (snapshotsError) {
    return (
      <div role="alert" className="space-y-3 rounded-xl border border-border px-3 py-5 text-sm">
        <p>Version history could not be loaded. Your manuscript has not been changed.</p>
        {retryTransientApiQuery(0, snapshotsError) && <Button variant="outline" size="sm" disabled={isFetching} onClick={() => void refetch()}>{isFetching ? "Retrying…" : "Try again"}</Button>}
      </div>
    );
  }
  if ((snapshots ?? []).length === 0) {
    return (
      <p className="rounded-xl border border-dashed border-border px-3 py-5 text-center text-[0.71875rem] leading-relaxed text-muted-foreground">
        Every compile and manual save lands here as a snapshot you can roll
        back to.
      </p>
    );
  }
  return (
    <>
    <ul className="max-h-[26rem] space-y-1.5 overflow-y-auto">
      {(snapshots ?? []).slice(0, 40).map((snapshot) => (
        <li
          key={snapshot.id}
          className="flex items-center gap-2 rounded-xl border border-border bg-card px-3 py-2"
        >
          <div className="min-w-0 flex-1">
            <p className="truncate text-[0.75rem] font-medium text-foreground">
              {snapshot.note}
            </p>
            <p className="font-mono text-[0.625rem] text-muted-foreground">
              {formatDateTime(snapshot.created_at)}{" "}
              · {snapshot.files} file{snapshot.files === 1 ? "" : "s"}
            </p>
            <p className="mt-1 line-clamp-2 text-[0.65625rem] leading-relaxed text-muted-foreground">
              {snapshot.summary}
            </p>
          </div>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setCompareId(snapshot.id)}
            className="h-6 shrink-0 rounded-full px-2.5 text-[0.65625rem]"
          >
            Compare
          </Button>
          {canRestore ? (
            <Button
              size="sm"
              variant="outline"
              disabled={restore.isPending}
              onClick={() => restore.mutate(snapshot.id)}
              className="h-6 shrink-0 rounded-full px-2.5 text-[0.65625rem]"
            >
              Restore
            </Button>
          ) : null}
        </li>
      ))}
    </ul>
    <Dialog open={compareId !== null} onOpenChange={(open) => !open && setCompareId(null)}>
      <DialogContent className="flex h-[min(54rem,92vh)] flex-col overflow-hidden sm:max-w-[min(72rem,calc(100vw-2rem))]">
        <DialogHeader>
          <DialogTitle>Version comparison</DialogTitle>
          <DialogDescription>
            {comparison.data?.summary ?? "Loading the project diff…"}
          </DialogDescription>
        </DialogHeader>
        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto">
          {(comparison.data?.files ?? []).map((file) => (
            <section key={file.path} className="overflow-hidden rounded-xl border border-border">
              <p className="border-b border-border bg-secondary/40 px-3 py-2 font-mono text-[0.65625rem] text-foreground">
                {file.path}
              </p>
              <div className="min-h-0 overflow-auto py-2 font-mono text-[0.6875rem] leading-relaxed">
                {file.diff.split("\n").map((line, index) => {
                  const added = line.startsWith("+") && !line.startsWith("+++");
                  const removed = line.startsWith("-") && !line.startsWith("---");
                  const hunk = line.startsWith("@@");
                  return (
                    <span
                      key={`${index}-${line.slice(0, 18)}`}
                      className={cn(
                        "block min-w-max whitespace-pre px-3",
                        added && "bg-moss-surface/10 text-moss",
                        removed && "bg-destructive/10 text-destructive",
                        hunk && "my-1 bg-secondary/70 py-0.5 text-moss",
                        !added && !removed && !hunk && "text-muted-foreground",
                      )}
                    >
                      {line || " "}
                    </span>
                  );
                })}
              </div>
            </section>
          ))}
          {comparison.data && comparison.data.files.length === 0 ? (
            <p className="rounded-xl border border-dashed border-border px-4 py-10 text-center text-[0.75rem] text-muted-foreground">
              This version has no source changes relative to the preceding version.
            </p>
          ) : null}
        </div>
      </DialogContent>
    </Dialog>
    </>
  );
}

/* ---------- AI contribution log ---------- */

function ContributionDialog({
  docId,
  open,
  onOpenChange,
  onInsert,
}: {
  docId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onInsert: (latex: string) => void;
}) {
  const { data: log } = useQuery({
    queryKey: ["writer-contribution-log", docId],
    queryFn: () => api.writerContributionLog(docId),
    enabled: open,
  });
  const summary = log?.summary;
  const stats: [string, string][] = summary
    ? [
        ["Assistant requests", String(summary.assistant_turns)],
        ["Edits proposed", String(summary.edits_proposed)],
        [
          "Edits applied",
          summary.edits_applied_auto > 0
            ? `${summary.edits_applied} (${summary.edits_applied_auto} auto)`
            : String(summary.edits_applied),
        ],
        [
          "AI characters",
          `+${summary.ai_chars_added.toLocaleString()} / -${summary.ai_chars_removed.toLocaleString()}`,
        ],
        ["Compiles", String(summary.compiles)],
        [
          "Figures",
          summary.readable_pdfs > 0
            ? `${summary.figures} (${summary.readable_pdfs} readable PDFs)`
            : String(summary.figures),
        ],
      ]
    : [];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <FileClock className="size-4 text-moss" />
            AI contribution log
          </DialogTitle>
          <DialogDescription>
            Counted from recorded events only: what the assistant proposed,
            and what actually landed in the text.
          </DialogDescription>
        </DialogHeader>
        {log ? (
          <div className="space-y-4">
            <dl className="grid grid-cols-2 gap-x-4 gap-y-2">
              {stats.map(([label, value]) => (
                <div
                  key={label}
                  className="rounded-xl border border-border bg-card px-3 py-2"
                >
                  <dt className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
                    {label}
                  </dt>
                  <dd className="mt-0.5 text-[0.875rem] font-medium text-foreground">
                    {value}
                  </dd>
                </div>
              ))}
            </dl>
            <div className="rounded-xl border border-moss/30 bg-accent/40 px-3.5 py-3">
              <p className="text-[0.78125rem] italic leading-relaxed text-foreground">
                {log.disclosure}
              </p>
              <div className="mt-2.5 flex items-center gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7 rounded-full px-3 text-[0.71875rem]"
                  onClick={() => {
                    void navigator.clipboard.writeText(log.disclosure);
                    toast.success("Disclosure copied.");
                  }}
                >
                  <Copy className="size-3" />
                  Copy
                </Button>
                <Button
                  size="sm"
                  className="h-7 rounded-full px-3 text-[0.71875rem]"
                  onClick={() => {
                    onInsert(`\n${log.disclosure_tex}\n`);
                    onOpenChange(false);
                  }}
                >
                  <ListPlus className="size-3" />
                  Insert into document
                </Button>
              </div>
            </div>
          </div>
        ) : (
          <Loader2 className="mx-auto my-8 size-5 animate-spin text-muted-foreground" />
        )}
      </DialogContent>
    </Dialog>
  );
}

/* ---------- the editing chat ---------- */

function EditCard({
  edit,
  onReview,
  decision,
  active,
}: {
  edit: WriterEdit;
  onReview: () => void;
  decision: "pending" | "applied" | "rejected" | "superseded" | "blocked";
  active: boolean;
}) {
  return (
    <div className="mt-2 overflow-hidden rounded-xl border border-border bg-secondary/30">
      <div className="max-h-40 overflow-auto px-3 py-2 font-mono text-[0.6875rem] leading-relaxed">
        <pre className="whitespace-pre-wrap break-words rounded bg-destructive/10 px-2 py-1 text-destructive/90">
          {edit.find}
        </pre>
        {edit.replace ? (
          <pre className="mt-1 whitespace-pre-wrap break-words rounded bg-moss-surface/10 px-2 py-1 text-foreground">
            {edit.replace}
          </pre>
        ) : (
          <p className="mt-1 font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
            (delete)
          </p>
        )}
      </div>
      <div className="flex items-center justify-between border-t border-border/60 bg-card px-3 py-1.5">
        <span className="font-mono text-[0.59375rem] uppercase tracking-[0.18em] text-muted-foreground">
          {decision === "applied"
            ? "applied"
            : decision === "rejected"
              ? "rejected · source unchanged"
              : decision === "superseded"
                ? "needs regeneration after an earlier rejection"
              : edit.applicable
                ? active
                  ? "next proposed edit"
                  : "queued for review"
                : (edit.integrity_errors?.[0] ?? "")
                  ? `blocked: ${edit.integrity_errors?.[0]}`
                  : `anchor not found (${edit.occurrences} matches)`}
        </span>
        <span className="mx-2 min-w-0 flex-1 truncate text-right font-mono text-[0.5625rem] text-muted-foreground">
          {edit.path}
        </span>
        {decision === "pending" ? (
          <Button
            size="sm"
            variant="outline"
            disabled={!active}
            onClick={onReview}
            className="h-6 rounded-full px-3 text-[0.6875rem]"
          >
            {active ? "Review in source" : "Queued"}
          </Button>
        ) : decision === "applied" ? (
          <Check className="size-3.5 text-moss" aria-label="Applied" />
        ) : decision === "rejected" || decision === "superseded" ? (
          <X
            className="size-3.5 text-muted-foreground"
            aria-label={decision === "rejected" ? "Rejected" : "Superseded"}
          />
        ) : null}
      </div>
    </div>
  );
}

const FIX_PROMPT =
  "The document fails to compile. Diagnose the errors and propose edits "
  + "that fix them.";

function VisualRequestCard({
  docId,
  messageId,
  request,
  onInsert,
}: {
  docId: string;
  messageId: number;
  request: WriterVisualRequest;
  onInsert: (filename: string) => void;
}) {
  const queryClient = useQueryClient();
  const { data: figures } = useQuery({
    queryKey: ["figures"],
    queryFn: api.figures,
    refetchInterval: (query) =>
      (query.state.data ?? []).some((figure) => figure.status === "pending")
        ? 2_500
        : false,
  });
  const figure = (figures ?? []).find(
    (candidate) =>
      candidate.config.writer_document_id === docId
      && candidate.config.writer_message_id === messageId,
  );
  const attachedFilename = figure?.config.writer_attachments?.[docId];
  const autoInsertKey = `six:writer-visual-auto-insert:${docId}:${messageId}`;
  const [autoInsertRequested, setAutoInsertRequested] = useState(false);
  const autoInsertGuard = useRef(false);

  useEffect(() => {
    try {
      setAutoInsertRequested(window.localStorage.getItem(autoInsertKey) === "1");
    } catch {
      // Rendering still works when storage is unavailable.
    }
  }, [autoInsertKey]);

  const render = useMutation({
    mutationFn: () =>
      api.figureCreate(request.prompt, null, "auto", {
        kind: request.kind,
        resolution: request.resolution,
        aspect_ratio: request.aspect_ratio,
        review_passes: request.review_passes,
        writer_document_id: docId,
        writer_message_id: messageId,
      }),
    onMutate: () => {
      autoInsertGuard.current = false;
      setAutoInsertRequested(true);
      try {
        window.localStorage.setItem(autoInsertKey, "1");
      } catch {
        // The in-memory request still completes in this tab.
      }
    },
    onSuccess: () => {
      toast.success("Confirmed. The figure will be inserted when rendering finishes.");
      void queryClient.invalidateQueries({ queryKey: ["figures"] });
    },
    onError: (error) => {
      setAutoInsertRequested(false);
      try {
        window.localStorage.removeItem(autoInsertKey);
      } catch {
        // No persistent receipt to clear.
      }
      toast.error(error instanceof Error ? error.message : "Rendering could not start.");
    },
  });
  const attach = useMutation({
    mutationFn: () => {
      if (!figure) throw new Error("The visual is not ready yet.");
      return api.figureAttach(figure.public_id, docId);
    },
    onSuccess: (result) => {
      setAutoInsertRequested(false);
      try {
        window.localStorage.removeItem(autoInsertKey);
      } catch {
        // No persistent receipt to clear.
      }
      void queryClient.invalidateQueries({ queryKey: ["figures"] });
      void queryClient.invalidateQueries({ queryKey: ["writer-assets", docId] });
      onInsert(result.filename);
      toast.success("Visual attached and inserted at the cursor.");
    },
    onError: (error) => {
      autoInsertGuard.current = false;
      setAutoInsertRequested(false);
      try {
        window.localStorage.removeItem(autoInsertKey);
      } catch {
        // No persistent receipt to clear.
      }
      toast.error(error instanceof Error ? error.message : "The visual could not be attached.");
    },
  });

  useEffect(() => {
    if (
      !autoInsertRequested
      || figure?.status !== "ok"
      || attach.isPending
      || autoInsertGuard.current
    ) {
      return;
    }
    autoInsertGuard.current = true;
    if (attachedFilename) {
      setAutoInsertRequested(false);
      try {
        window.localStorage.removeItem(autoInsertKey);
      } catch {
        // No persistent receipt to clear.
      }
      onInsert(attachedFilename);
      toast.success("Visual inserted at the cursor.");
      return;
    }
    attach.mutate();
  }, [
    attach,
    attachedFilename,
    autoInsertKey,
    autoInsertRequested,
    figure?.status,
    onInsert,
  ]);

  return (
    <div className="mt-3 rounded-2xl border border-moss/30 bg-accent/35 p-3">
      <div className="flex items-start gap-2">
        <span className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-xl bg-card text-moss">
          <Shapes className="size-3.5" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-[0.75rem] font-medium text-foreground">
            Proposed Visual Lab render
          </p>
          <p className="mt-1 line-clamp-3 text-[0.6875rem] leading-relaxed text-muted-foreground">
            {request.prompt}
          </p>
          <p className="mt-1.5 font-mono text-[0.59375rem] uppercase tracking-[0.15em] text-muted-foreground">
            {request.kind} · {request.resolution.toUpperCase()} · {request.aspect_ratio}
            {" · "}{request.review_passes === 0 ? "fast" : `${request.review_passes} review pass${request.review_passes === 1 ? "" : "es"}`}
          </p>
        </div>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {!figure || figure.status === "error" ? (
          <Button
            type="button"
            size="sm"
            className="h-7 rounded-full px-3 text-[0.6875rem]"
            disabled={render.isPending}
            onClick={() => render.mutate()}
          >
            {render.isPending ? (
              <Loader2 className="size-3 animate-spin" />
            ) : (
              <Check className="size-3" />
            )}
            {figure?.status === "error" ? "Confirm retry and insert" : "Confirm, render and insert"}
          </Button>
        ) : figure.status === "pending" ? (
          <span className="inline-flex h-7 items-center gap-1.5 rounded-full border border-border bg-card px-3 text-[0.6875rem] text-muted-foreground">
            <Loader2 className="size-3 animate-spin text-moss" />
            Rendering, then inserting here
          </span>
        ) : (
          <Button
            type="button"
            size="sm"
            className="h-7 rounded-full px-3 text-[0.6875rem]"
            disabled={attach.isPending}
            onClick={() => {
              if (attachedFilename) {
                onInsert(attachedFilename);
                toast.success("Visual inserted at the cursor.");
              } else {
                attach.mutate();
              }
            }}
          >
            {attach.isPending ? <Loader2 className="size-3 animate-spin" /> : <ImageIcon className="size-3" />}
            {attachedFilename ? "Insert visual" : "Attach and insert"}
          </Button>
        )}
        {figure ? (
          <Button asChild variant="ghost" size="sm" className="h-7 rounded-full px-2.5 text-[0.6875rem]">
            <Link href={`/figures?figure=${encodeURIComponent(figure.public_id)}`}>
              Open in Visual Lab
            </Link>
          </Button>
        ) : null}
      </div>
      {!figure ? (
        <p className="mt-2 text-[0.625rem] leading-relaxed text-muted-foreground">
          One confirmation renders, attaches and inserts the figure at your cursor. Auto apply never confirms Visual Lab actions.
        </p>
      ) : null}
    </div>
  );
}

type WriterAgentReply = Awaited<ReturnType<typeof api.writerChatSend>>;

function WriterChat({
  docId,
  activePath,
  runIds,
  interviewCount,
  surveyCount,
  selection,
  onClearSelection,
  editReviewsById,
  nextReviewId,
  onOpenReview,
  onApplyEdits,
  compileFailed,
  onBeforeSend,
  onInsertFigure,
  externalTask,
}: {
  docId: string;
  activePath: string;
  runIds: number[];
  interviewCount: number;
  surveyCount: number;
  selection: WriterSelection | null;
  onClearSelection: () => void;
  editReviewsById: ReadonlyMap<string, WriterEditReview>;
  nextReviewId: string | null;
  onOpenReview: (review: WriterEditReview) => void;
  /** Batch apply for auto mode; returns the indices that landed. */
  onApplyEdits: (
    edits: WriterEdit[],
    opts?: { auto?: boolean; messageId?: number | null },
  ) => Promise<number[]>;
  /** The latest build broke; a chip above the composer offers the fix. */
  compileFailed: boolean;
  onBeforeSend: () => Promise<void>;
  onInsertFigure: (filename: string) => void;
  externalTask?: {
    key: number;
    text: string;
    selection: WriterSelection | null;
  } | null;
}) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState("");
  // the just-sent turn, shown immediately while the assistant works
  const [pending, setPending] = useState<{
    text: string;
    selection: WriterSelection | null;
  } | null>(null);
  const {
    events: agentEvents,
    handoffs: agentTimelineHandoffs,
    recordAgentEvent,
    startAgentTurn,
    handoffAgentTurn,
    resetAgentTimeline,
  } = useAgentTimelineLedger();
  const activeStreamControllerRef = useRef<AbortController | null>(null);
  const activeTurnIdRef = useRef<string | null>(null);
  const turnAcceptedRef = useRef(false);
  const stopRequestedTurnIdRef = useRef<string | null>(null);
  const cancellationConfirmedRef = useRef(false);
  const stopFallbackTimerRef = useRef<number | null>(null);
  const [streamActive, setStreamActive] = useState(false);
  const [turnAccepted, setTurnAccepted] = useState(false);
  const [stopping, setStopping] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  // which model answers; persisted like the composer's choice
  const [model, pickModel] = usePrivateModelPreference("six:writer-model");

  // auto mode: proposals land in the source without an Apply click
  const [autoApply, setAutoApply] = useState(false);
  useEffect(() => {
    setAutoApply(localStorage.getItem("six:writer-autoapply") === "1");
  }, []);
  const autoApplyRef = useRef(autoApply);
  useEffect(() => {
    autoApplyRef.current = autoApply;
  }, [autoApply]);

  // drag a PDF or figure anywhere onto the chat to attach it
  const [dragging, setDragging] = useState(false);
  const dragDepth = useRef(0);
  const uploadFiles = useMutation({
    mutationFn: async (files: File[]) => {
      const uploaded: { filename: string; readable: boolean; source: boolean }[] = [];
      for (const file of files) {
        const encoded = await fileToBase64(file);
        if (/\.pdf$/i.test(file.name)) {
          const sources = await api.writerSourceUpload(docId, file.name, encoded);
          uploaded.push({ filename: file.name, readable: sources.some((item) => item.readable), source: true });
        } else {
          const asset = await api.writerAssetUpload(docId, file.name, encoded);
          uploaded.push({ filename: asset.filename, readable: false, source: false });
        }
      }
      return uploaded;
    },
    onSuccess: (assets) => {
      const readable = assets.filter((a) => a.readable).length;
      toast.success(
        assets.length === 1
          ? readable
            ? `${assets[0].filename} attached. The assistant can read it now.`
            : `${assets[0].filename} attached.`
          : `${assets.length} files attached.`,
      );
      void queryClient.invalidateQueries({ queryKey: ["writer-assets", docId] });
      void queryClient.invalidateQueries({ queryKey: ["writer-sources", docId] });
      void queryClient.invalidateQueries({ queryKey: ["writer-citations", docId] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Upload failed."),
  });
  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    dragDepth.current = 0;
    setDragging(false);
    const files = Array.from(event.dataTransfer.files).filter((file) =>
      /\.(pdf|png|jpe?g)$/i.test(file.name),
    );
    if (files.length === 0) {
      toast.error("Drop a .pdf, .png or .jpg file.");
      return;
    }
    uploadFiles.mutate(files);
  };

  const { data: messages } = useQuery({
    queryKey: ["writer-chat", docId],
    queryFn: () => api.writerChatHistory(docId),
  });

  const applyAgentResult = useCallback(
    async (response: WriterAgentReply) => {
      if (!autoApplyRef.current || (response.edits ?? []).length === 0) return;
      try {
        const landed = await onApplyEdits(response.edits ?? [], {
          auto: true,
          messageId: response.id,
        });
        if (landed.length > 0) {
          toast.success(
            landed.length === 1
              ? "Edit applied automatically."
              : `${landed.length} edits applied automatically.`,
          );
          return;
        }
        toast.error(
          "The manuscript changed before the verified patch could be applied. No part of the patch was applied; ask the agent to regenerate it.",
        );
      } catch (error) {
        toast.error(
          error instanceof Error
            ? error.message
            : "The verified patch could not be applied. No manuscript source was changed.",
        );
      }
    },
    [onApplyEdits],
  );

  useEffect(
    () => () => {
      if (stopFallbackTimerRef.current) {
        clearTimeout(stopFallbackTimerRef.current);
      }
      activeStreamControllerRef.current?.abort();
    },
    [],
  );

  const send = useMutation({
    mutationFn: async (turn: { text: string; selection: WriterSelection | null }) => {
      const controller = new AbortController();
      const turnId = createSpecialistTurnId();
      activeStreamControllerRef.current = controller;
      activeTurnIdRef.current = turnId;
      setStreamActive(true);
      try {
        await onBeforeSend();
        return await api.writerChatStream(
          docId,
          turn.text,
          turn.selection,
          model,
          activePath,
          (event) => {
            if (event.event === "turn.cancelled") {
              cancellationConfirmedRef.current = true;
            }
            recordAgentEvent(event);
          },
          {
            turnId,
            signal: controller.signal,
            onAccepted: () => {
              turnAcceptedRef.current = true;
              setTurnAccepted(true);
            },
          },
        );
      } finally {
        if (stopFallbackTimerRef.current) {
          clearTimeout(stopFallbackTimerRef.current);
          stopFallbackTimerRef.current = null;
        }
        if (activeStreamControllerRef.current === controller) {
          activeStreamControllerRef.current = null;
        }
        if (activeTurnIdRef.current === turnId) {
          activeTurnIdRef.current = null;
        }
        turnAcceptedRef.current = false;
        setTurnAccepted(false);
        setStreamActive(false);
      }
    },
    onMutate: (turn) => {
      setStopping(false);
      turnAcceptedRef.current = false;
      setTurnAccepted(false);
      stopRequestedTurnIdRef.current = null;
      cancellationConfirmedRef.current = false;
      startAgentTurn();
      setPending(turn);
    },
    onSuccess: applyAgentResult,
    onError: (error, turn) => {
      const streamError =
        error instanceof SpecialistStreamError ? error : null;
      const shouldRestoreDraft =
        !streamError || !streamError.accepted || streamError.kind === "terminal";
      // Never offer an accepted, potentially still-running turn as if it were
      // safe to submit again. Terminal failures may be retried deliberately.
      if (shouldRestoreDraft) {
        setDraft((current) => current.trim() ? current : turn.text);
      }
      if (streamError?.kind === "cancelled") {
        const stopWasRequested = stopRequestedTurnIdRef.current !== null;
        toast.message(
          stopWasRequested && cancellationConfirmedRef.current
            ? "Agent stopped at a safe checkpoint."
            : stopWasRequested
              ? "Stop was requested, but the live channel closed before final confirmation."
              : streamError.message,
        );
        return;
      }
      toast.error(
        error instanceof Error ? error.message : "That didn't work.",
      );
    },
    onSettled: async () => {
      // keep the optimistic bubble until the history actually holds it
      try {
        await queryClient.invalidateQueries({
          queryKey: ["writer-chat", docId],
        });
      } finally {
        handoffAgentTurn();
        setStopping(false);
        stopRequestedTurnIdRef.current = null;
        cancellationConfirmedRef.current = false;
        setPending(null);
      }
    },
  });
  const persistedAgentTimelines = (messages ?? [])
    .filter((message) => message.role === "assistant")
    .map((message) => message.payload.agent_events ?? []);
  const { checking: checkingAgentTurn, recovering: recoveringAgentTurn } =
    useDurableSpecialistTurn<WriterAgentReply>({
      resourceKind: "manuscript",
      resourceId: docId,
      enabled: messages !== undefined,
      persistedTurnIds: agentTurnIdsFromTimelines(persistedAgentTimelines),
      onStarted: (turn) => {
        startAgentTurn();
        activeTurnIdRef.current = turn.turn_id;
        turnAcceptedRef.current = true;
        setTurnAccepted(true);
        setStreamActive(true);
        setPending(null);
      },
      onEvent: (event) => {
        if (event.event === "turn.cancelled") {
          cancellationConfirmedRef.current = true;
        }
        recordAgentEvent(event);
      },
      onTerminal: async (result, turn) => {
        await queryClient.invalidateQueries({ queryKey: ["writer-chat", docId] });
        const refreshed = queryClient.getQueryData<WriterMessage[]>([
          "writer-chat",
          docId,
        ]) ?? [];
        const message = result
          ? refreshed.find((entry) => entry.id === result.id)
          : undefined;
        const alreadyApplied = (message?.payload.agent_events ?? []).some(
          (event) => event.event === "change.completed" && event.applied !== false,
        );
        if (result && turn.status === "completed" && !alreadyApplied) {
          await applyAgentResult(result);
        }
        await queryClient.invalidateQueries({ queryKey: ["writer-chat", docId] });
        handoffAgentTurn();
        activeTurnIdRef.current = null;
        turnAcceptedRef.current = false;
        setTurnAccepted(false);
        setStreamActive(false);
        setStopping(false);
        setPending(null);
      },
      onError: (error) => {
        activeTurnIdRef.current = null;
        turnAcceptedRef.current = false;
        setTurnAccepted(false);
        setStreamActive(false);
        setStopping(false);
        toast.error(
          error instanceof Error
            ? error.message
            : "The running manuscript task could not be recovered yet.",
        );
      },
    });
  const sendPending = send.isPending;
  const agentBusy =
    sendPending || messages === undefined || checkingAgentTurn || recoveringAgentTurn;
  const stopLiveTurn = async () => {
    const controller = activeStreamControllerRef.current;
    const turnId = activeTurnIdRef.current;
    if (
      !turnId
      || !turnAcceptedRef.current
      || controller?.signal.aborted
      || stopping
    ) return;
    setStopping(true);
    stopRequestedTurnIdRef.current = turnId;
    try {
      const stopped = await api.agentTurnStop(turnId);
      if (activeTurnIdRef.current !== turnId) return;
      if (stopped.status === "completed" || stopped.status === "failed") {
        stopRequestedTurnIdRef.current = null;
        setStopping(false);
        return;
      }
      // Cooperative cancellation should arrive as a durable turn.cancelled
      // event. Abort only if that terminal frame never reaches this browser.
      if (controller) {
        stopFallbackTimerRef.current = window.setTimeout(() => {
          if (
            activeTurnIdRef.current === turnId
            && activeStreamControllerRef.current === controller
            && !controller.signal.aborted
          ) {
            controller.abort();
          }
        }, 12_000);
      }
    } catch (error) {
      if (activeTurnIdRef.current === turnId) {
        stopRequestedTurnIdRef.current = null;
        setStopping(false);
      }
      toast.error(
        error instanceof Error ? error.message : "The agent could not be stopped.",
      );
    }
  };
  const queuedChat = useAgentTurnQueue<{
    text: string;
    selection: WriterSelection | null;
  }>({
    working: agentBusy,
    run: (turn) => send.mutate(turn),
  });
  const {
    clearChat,
    clearing,
    confirmationOpen,
    setConfirmationOpen,
    confirmClearChat,
    clearChatTriggerRef,
  } = useSpecialistChatReset({
    resourceKind: "manuscript",
    resourceId: docId,
    queryKey: ["writer-chat", docId],
    hasHistory: (messages ?? []).length > 0,
    onCleared: () => {
      setDraft("");
      setPending(null);
      queuedChat.clear();
      resetAgentTimeline();
      onClearSelection();
    },
  });

  const handledExternalTask = useRef<number | null>(null);
  useEffect(() => {
    if (
      !externalTask
      || handledExternalTask.current === externalTask.key
    ) {
      return;
    }
    handledExternalTask.current = externalTask.key;
    queuedChat.submit(externalTask.text, {
      text: externalTask.text,
      selection: externalTask.selection,
    });
  }, [externalTask, queuedChat]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, pending]);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = draft.trim();
    if (!text) return;
    if (isClearChatCommand(text)) {
      void clearChat();
      return;
    }
    if (agentBusy) return;
    const turnSelection = selection;
    setDraft("");
    onClearSelection();
    queuedChat.submit(text, { text, selection: turnSelection });
  }

  const latestWriterFollowUps = useMemo(() => {
    const thread = messages ?? [];
    let assistantIndex = -1;
    for (let index = thread.length - 1; index >= 0; index -= 1) {
      if (thread[index].role === "assistant") {
        assistantIndex = index;
        break;
      }
    }
    if (assistantIndex < 0) return { messageId: null, suggestions: [] };

    let question = "";
    for (let index = assistantIndex - 1; index >= 0; index -= 1) {
      if (thread[index].role === "user") {
        question = thread[index].content;
        break;
      }
    }
    const assistant = thread[assistantIndex];
    return {
      messageId: assistant.id,
      suggestions: buildWriterFollowUps({
        question,
        answer: assistant.content,
        editCount: assistant.payload.edits?.length ?? 0,
        hasLinkedSources: runIds.length > 0,
      }),
    };
  }, [messages, runIds.length]);

  const quickAnswerHandoffs = useMemo(() => {
    const handoffs = new Map<number, string>();
    let latestQuestion = "";
    for (const message of messages ?? []) {
      if (message.role === "user") {
        latestQuestion = message.content.trim();
        continue;
      }
      const answer = message.content.toLowerCase();
      const question = latestQuestion.toLowerCase();
      const declinesExternalResearch =
        (/\b(cannot|can't|unable to|do not have access|don't have access|only assist)\b/.test(
          answer,
        ) &&
          /\b(browse|internet|web|external|online|search)\b/.test(answer)) ||
        (/\b(kann|können|habe|haben)\b/.test(answer) &&
          /\b(nicht|kein|keinen|keine)\b/.test(answer) &&
          /\b(internet|web|extern|online|such|recherch)\w*/.test(answer));
      const asksForExternalResearch =
        /\b(web|internet|online|browse|search|paper|papers|literature|source|sources|latest|recent|current)\b/.test(
          question,
        ) ||
        /\b(internet|recherch|suche|such|paper|literatur|quelle|aktuell|neueste)\w*/.test(
          question,
        );
      if (latestQuestion && declinesExternalResearch && asksForExternalResearch) {
        handoffs.set(message.id, latestQuestion);
      }
    }
    return handoffs;
  }, [messages]);

  const openInQuickAnswer = (question: string) => {
    router.push(`/?q=${encodeURIComponent(question)}`);
  };

  return (
    <div
      data-tour="writer-agent"
      className="relative flex min-h-0 flex-1 flex-col"
      onDragEnter={(event) => {
        event.preventDefault();
        dragDepth.current += 1;
        setDragging(true);
      }}
      onDragLeave={() => {
        dragDepth.current = Math.max(0, dragDepth.current - 1);
        if (dragDepth.current === 0) setDragging(false);
      }}
      onDragOver={(event) => event.preventDefault()}
      onDrop={handleDrop}
    >
      <SpecialistChatResetDialog
        open={confirmationOpen}
        clearing={clearing}
        returnFocusRef={clearChatTriggerRef}
        onOpenChange={setConfirmationOpen}
        onConfirm={() => void confirmClearChat()}
      />
      {dragging ? (
        <div className="pointer-events-none absolute inset-0 z-30 grid place-items-center rounded-lg border-2 border-dashed border-moss/60 bg-accent/80">
          <div className="text-center">
            <FileText className="mx-auto size-6 text-moss" />
            <p className="mt-2 font-mono text-[0.6875rem] uppercase tracking-[0.18em] text-moss">
              Drop to attach
            </p>
            <p className="mt-1 max-w-[16rem] text-[0.71875rem] leading-relaxed text-muted-foreground">
              PDFs become citable sources; images join the figures.
            </p>
          </div>
        </div>
      ) : null}
      <div className="flex shrink-0 items-center gap-2 border-b border-border/60 px-3 py-2">
        <span className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
          {runIds.length > 0 || interviewCount > 0 || surveyCount > 0
            ? [
                runIds.length > 0
                  ? `${runIds.length} search${runIds.length === 1 ? "" : "es"}`
                  : "",
                interviewCount > 0
                  ? `${interviewCount} interview${interviewCount === 1 ? "" : "s"}`
                  : "",
                surveyCount > 0
                  ? `${surveyCount} survey${surveyCount === 1 ? "" : "s"}`
                  : "",
              ]
                .filter(Boolean)
                .join(" · ")
            : "writing assistant"}
        </span>
      </div>

      <div
        className={cn(
          "min-h-0 flex-1 overflow-y-auto px-3 py-3",
          // the floating debug chip hovers over the tail of the thread
          compileFailed && "pb-14",
        )}
      >
        {(messages ?? []).length === 0 &&
        !pending &&
        !streamActive &&
        !recoveringAgentTurn &&
        !checkingAgentTurn ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
            <p className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
              Your editor, on call
            </p>
            <p className="max-w-[17rem] text-[0.78125rem] leading-relaxed text-muted-foreground">
              Ask for text, rewrites, citations or figures. Every change comes
              back as a proposal you apply with one click. Mark a passage in
              the compiled PDF, select code in the source, or drop a PDF here
              for the assistant to read.
            </p>
          </div>
        ) : (
          <ul className="space-y-3">
            {(messages ?? []).map((message: WriterMessage) => (
              <li
                key={message.id}
                className={cn(message.role === "user" && "ml-auto max-w-[85%]")}
              >
                {message.payload.selection?.quote ? (
                  <p className="mb-1 rounded-xl border-l-2 border-moss/50 bg-secondary/50 px-3 py-1.5 text-[0.6875rem] italic leading-relaxed text-muted-foreground">
                    “{message.payload.selection.quote.slice(0, 160)}”
                    {message.payload.selection.page
                      ? ` (${writerPdfPageLabel({
                          page: message.payload.selection.page,
                          ...(message.payload.selection.page_end
                            ? { page_end: message.payload.selection.page_end }
                            : {}),
                        })})`
                      : message.payload.selection.line
                        ? ` (source, line ${message.payload.selection.line})`
                        : ""}
                  </p>
                ) : null}
                <div
                  className={cn(
                    "text-[0.875rem] leading-relaxed",
                    message.role === "user"
                      ? "rounded-2xl rounded-br-md bg-moss-surface px-3.5 py-2.5 text-ivory"
                      : "min-w-0 max-w-full text-foreground",
                  )}
                >
                  <div className={cn(message.role === "assistant" && "min-w-0 flex-1")}>
                    {message.role === "assistant" ? (
                      <SpecialistCompletedTurn
                        kind="manuscript"
                        events={message.payload.agent_events}
                        answer={message.content}
                        artifacts={message.payload.artifacts}
                      />
                    ) : (
                      <p className="whitespace-pre-wrap">{message.content}</p>
                    )}
                    {message.role === "assistant" &&
                    quickAnswerHandoffs.has(message.id) ? (
                      <button
                        type="button"
                        onClick={() =>
                          openInQuickAnswer(quickAnswerHandoffs.get(message.id) ?? "")
                        }
                        className="group mt-3 flex w-full max-w-md cursor-pointer items-center gap-3 rounded-2xl border border-moss/25 bg-accent/35 px-3.5 py-3 text-left transition-colors hover:border-moss/45 hover:bg-accent/60"
                      >
                        <span className="grid size-8 shrink-0 place-items-center rounded-full bg-moss-surface text-ivory">
                          <Search className="size-3.5" />
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block text-[0.75rem] font-medium text-foreground">
                            Continue in Quick Answer
                          </span>
                          <span className="mt-0.5 block text-[0.65625rem] leading-relaxed text-muted-foreground">
                            Open this question with web and scholarly search tools.
                          </span>
                        </span>
                        <ArrowUpRight className="size-3.5 shrink-0 text-muted-foreground transition-transform group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-moss" />
                      </button>
                    ) : null}
                    {message.role === "assistant" &&
                    (message.payload.interview_context?.length ?? 0) > 0 ? (
                      <div className="mt-2 inline-flex max-w-full items-center gap-1.5 rounded-full border border-border bg-secondary/45 px-2.5 py-1 font-mono text-[0.5625rem] uppercase tracking-[0.12em] text-muted-foreground">
                        <Mic2 className="size-3 shrink-0 text-moss" />
                        <span className="truncate">
                          {message.payload.interview_context?.length} interview
                          {message.payload.interview_context?.length === 1 ? "" : "s"}
                          {" · "}
                          {message.payload.interview_context?.reduce(
                            (total, source) => total + source.passages.length,
                            0,
                          )}{" "}
                          transcript passage
                          {message.payload.interview_context?.reduce(
                            (total, source) => total + source.passages.length,
                            0,
                          ) === 1
                            ? ""
                            : "s"}
                        </span>
                      </div>
                    ) : null}
                    {message.role === "assistant" &&
                    (message.payload.survey_context?.length ?? 0) > 0 ? (
                      <div className="mt-2 inline-flex max-w-full items-center gap-1.5 rounded-full border border-border bg-secondary/45 px-2.5 py-1 font-mono text-[0.5625rem] uppercase tracking-[0.12em] text-muted-foreground">
                        <ClipboardList className="size-3 shrink-0 text-moss" />
                        <span className="truncate">
                          {message.payload.survey_context?.length} survey
                          {message.payload.survey_context?.length === 1 ? "" : "s"}
                          {" · "}
                          {message.payload.survey_context?.reduce(
                            (total, source) => total + source.response_count,
                            0,
                          )}{" "}
                          submitted responses
                        </span>
                      </div>
                    ) : null}
                    {message.role === "assistant" ? (
                      <RepositoryProseProvenance
                        provenance={message.payload.repository_prose}
                        sourceKind={message.payload.source_kind}
                        requiresManualReview={message.payload.requires_manual_review}
                      />
                    ) : null}
                    {(message.payload.edits ?? []).map((edit, index) => {
                      const key = `${message.id}:${index}`;
                      const review = editReviewsById.get(key);
                      const decision = review?.decision
                        ?? (edit.applicable ? "pending" : "blocked");
                      return (
                        <EditCard
                          key={key}
                          edit={edit}
                          decision={decision}
                          active={key === nextReviewId}
                          onReview={() => {
                            if (review) onOpenReview(review);
                          }}
                        />
                      );
                    })}
                    {message.payload.visual_request ? (
                      <VisualRequestCard
                        docId={docId}
                        messageId={message.id}
                        request={message.payload.visual_request}
                        onInsert={onInsertFigure}
                      />
                    ) : null}
                    <WorkspaceActionList
                      actions={message.payload.workspace_actions}
                      compact
                    />
                    {message.payload.verification ? (
                      <div
                        className={cn(
                          "mt-2 flex items-start gap-2 rounded-xl border px-3 py-2 text-[0.6875rem]",
                          message.payload.verification.status === "passed"
                            ? "border-moss/30 bg-accent/50 text-moss"
                            : "border-destructive/25 bg-destructive/5 text-destructive",
                        )}
                      >
                        <ShieldCheck className="mt-0.5 size-3.5 shrink-0" />
                        <span>
                          {message.payload.verification.status === "passed"
                            ? "Proposal passed its compile check when prepared."
                            : `Compile check found ${message.payload.verification.errors.length || "remaining"} issue${message.payload.verification.errors.length === 1 ? "" : "s"}. Nothing was applied.`}
                        </span>
                      </div>
                    ) : null}
                  </div>
                </div>
                {message.role === "assistant"
                && message.id === latestWriterFollowUps.messageId
                && !pending ? (
                  <FollowUpChips
                    suggestions={latestWriterFollowUps.suggestions}
                    onSelect={(prompt) =>
                      queuedChat.submit(prompt, { text: prompt, selection: null })
                    }
                    className="mt-2 px-1"
                  />
                ) : null}
              </li>
            ))}
            {agentTimelineHandoffs.length > 0 ? (
              <li>
                <AgentTimelineHandoffs
                  handoffs={agentTimelineHandoffs}
                  persistedTimelines={persistedAgentTimelines}
                />
              </li>
            ) : null}
            {pending ? (
                <li className="ml-auto max-w-[85%]">
                  {pending.selection ? (
                    <p className="mb-1 rounded-xl border-l-2 border-moss/50 bg-secondary/50 px-3 py-1.5 text-[0.6875rem] italic leading-relaxed text-muted-foreground">
                      “{pending.selection.quote.slice(0, 160)}”
                      {pending.selection.kind === "pdf"
                        ? ` (${writerPdfPageLabel(pending.selection)})`
                        : ` (source, line ${pending.selection.line})`}
                    </p>
                  ) : null}
                  <div className="rounded-2xl rounded-br-md bg-moss-surface px-3.5 py-2.5 text-[0.875rem] leading-relaxed text-ivory">
                    <p className="whitespace-pre-wrap">{pending.text}</p>
                  </div>
                </li>
            ) : null}
            {pending || streamActive || recoveringAgentTurn || checkingAgentTurn ? (
              <li className="mr-auto">
                <AgentTurnElapsed className="mb-2" />
                <AgentActivityTimeline events={agentEvents} running />
              </li>
            ) : null}
          </ul>
        )}
        <div ref={endRef} />
      </div>

      <div
        data-tour="writer-agent-composer"
        className="relative shrink-0 border-t border-border/60 p-3"
      >
            <AiInteractionNotice />
        {compileFailed && !agentBusy ? (
          <button
            type="button"
            onClick={() =>
              queuedChat.submit(FIX_PROMPT, {
                text: FIX_PROMPT,
                selection: null,
              })
            }
            title="Hand the compile log to the assistant and get fixes proposed"
            className="absolute -top-12 left-1/2 z-10 inline-flex -translate-x-1/2 cursor-pointer items-center gap-2 whitespace-nowrap rounded-full border border-destructive/30 bg-card px-4 py-2 text-[0.8125rem] font-medium text-destructive shadow-lg transition-colors hover:bg-destructive/10"
          >
            <Wrench className="size-3.5" />
            Debug the failing build
          </button>
        ) : null}
        {selection ? (
          <div className="mb-2 flex items-start gap-2 rounded-xl border border-moss/40 bg-accent/60 px-3 py-2">
            {selection.kind === "source" ? (
              <Code2 className="mt-0.5 size-3 shrink-0 text-moss" />
            ) : (
              <Quote className="mt-0.5 size-3 shrink-0 text-moss" />
            )}
            <p className="min-w-0 flex-1 text-[0.71875rem] italic leading-relaxed text-foreground">
              “{selection.quote.slice(0, 140)}”{" "}
              {selection.kind === "pdf"
                ? `(${writerPdfPageLabel(selection)})`
                : `(source, line ${selection.line})`}
            </p>
            <button
              type="button"
              onClick={onClearSelection}
              aria-label="Clear the marked passage"
              className="cursor-pointer text-muted-foreground hover:text-foreground"
            >
              <X className="size-3.5" />
            </button>
          </div>
        ) : null}
        <AgentTurnQueue
          items={queuedChat.queue}
          onRemove={queuedChat.remove}
        />
        {!selection && !agentBusy && (messages ?? []).length === 0 ? (
          <div className="mb-2 flex max-w-full gap-1.5 overflow-x-auto pb-0.5">
            {[
              ["Strengthen structure", "Review the manuscript structure and propose the highest-impact reorganization."],
              ["Find citation gaps", "Find claims that need citations and suggest sources from my linked searches."],
              ["Draft next section", "Based on the current outline, draft the most useful next section with citation placeholders."],
              ["Reviewer pass", "Act as a critical reviewer: identify the three most important weaknesses and propose precise edits."],
            ].map(([label, prompt]) => (
              <button
                key={label}
                type="button"
                onClick={() => setDraft(prompt)}
                className="shrink-0 cursor-pointer rounded-full border border-border bg-card px-2.5 py-1 text-[0.65625rem] text-muted-foreground transition-colors hover:border-moss/40 hover:text-moss"
              >
                {label}
              </button>
            ))}
          </div>
        ) : null}
        <form
          method="post"
          onSubmit={submit}
          className="flex min-w-0 flex-wrap items-end gap-2"
        >
          <Textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                const text = draft.trim();
                if (!text) return;
                if (isClearChatCommand(text)) {
                  void clearChat();
                  return;
                }
                if (agentBusy) return;
                const turnSelection = selection;
                setDraft("");
                onClearSelection();
                queuedChat.submit(text, {
                  text,
                  selection: turnSelection,
                });
              }
            }}
            placeholder={
              selection
                ? selection.kind === "source"
                  ? "What should change about this code?"
                  : "What should change about this passage?"
                : "Write, rewrite, cite, embed a figure…"
            }
            rows={2}
            aria-label="Message to the manuscript assistant"
            className="min-h-16 min-w-0 w-full basis-full resize-none rounded-xl bg-transparent py-2 text-[0.8125rem] dark:bg-transparent"
          />
          <SpecialistChatResetButton
            onClick={() => void clearChat()}
            clearing={clearing}
            triggerRef={clearChatTriggerRef}
            disabled={agentBusy}
          />
          <div className="flex shrink-0 items-center gap-1 self-end pb-0.5">
            <ModelPicker value={model} onChange={pickModel} compact />
          </div>
          <button
            type="button"
            onClick={() => {
              const next = !autoApply;
              setAutoApply(next);
              localStorage.setItem("six:writer-autoapply", next ? "1" : "0");
              if (next) {
                toast.success(
                  "Auto apply on: proposals land in the source immediately.",
                );
              }
            }}
            title={
              autoApply
                ? "Auto apply is on: the assistant's edits land without asking"
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
          {streamActive ? (
            <Button
              type="button"
              size="icon"
              variant="outline"
              onClick={() => void stopLiveTurn()}
              disabled={!turnAccepted || stopping}
              className="ml-auto size-9 shrink-0 rounded-full"
              aria-label="Stop agent turn"
              title={
                turnAccepted
                  ? "Stop the agent at its next safe checkpoint"
                  : "Waiting for the server to accept this turn"
              }
            >
              {stopping ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Square className="size-3.5 fill-current" />
              )}
            </Button>
          ) : null}
          <Button
            type="submit"
            size="icon"
            disabled={!draft.trim() || agentBusy}
            className={cn("size-9 shrink-0 rounded-full", !streamActive && "ml-auto")}
            aria-label="Send"
          >
            <SendHorizontal className="size-4" />
          </Button>
        </form>
      </div>
    </div>
  );
}

/* ---------- the page ---------- */

function WriterChatReadOnly({ role }: { role: string }) {
  return (
    <div className="flex min-h-0 flex-1 items-center justify-center p-8">
      <div className="max-w-sm rounded-3xl border border-border bg-secondary/30 p-6 text-center">
        <span className="mx-auto grid size-10 place-items-center rounded-2xl bg-card text-moss shadow-sm">
          <Users className="size-4.5" />
        </span>
        <h2 className="font-display mt-4 text-[1.35rem] font-normal text-foreground">
          {role === "reviewer" ? "Review without rewriting" : "Read-only manuscript"}
        </h2>
        <p className="mt-2 text-[0.78125rem] leading-relaxed text-muted-foreground">
          {role === "reviewer"
            ? "Add general or PDF-anchored comments. An editor can ask the agent to address them as reviewable changes."
            : "You can inspect the source, PDF, evidence and version history. Ask the owner for edit access to change the manuscript."}
        </p>
      </div>
    </div>
  );
}

function initials(name: string, email: string) {
  const source = name.trim() || email.split("@")[0] || "?";
  return source
    .split(/[\s._-]+/)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
}

function WriterCollaborationPopover({
  docId,
  collaborators,
  presence,
}: {
  docId: string;
  collaborators: WriterCollaboratorCatalog | undefined;
  presence: WriterPresence[];
}) {
  const queryClient = useQueryClient();
  const updateRole = useMutation({
    mutationFn: ({
      userId,
      role,
    }: {
      userId: number;
      role: "editor" | "reviewer" | "viewer" | "none";
    }) => api.writerCollaboratorSet(docId, userId, role),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["writer-collaborators", docId],
      });
      toast.success("Manuscript access updated.");
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Access could not be updated."),
  });
  const active = presence.filter((person) => !person.self);

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className="h-8 rounded-full px-2.5 text-[0.78125rem]"
          title="Coauthors and live presence"
        >
          <Users className="size-3.5" />
          <span className="hidden min-[1700px]:inline">Coauthors</span>
          {active.length > 0 ? (
            <span className="grid min-w-4 place-items-center rounded-full bg-moss-surface px-1 font-mono text-[0.5625rem] text-ivory">
              {active.length}
            </span>
          ) : null}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[23rem] max-w-[calc(100vw-1rem)] p-3">
        <div className="rounded-2xl border border-border bg-secondary/35 p-3">
          <p className="text-[0.8125rem] font-medium text-foreground">
            Work on this manuscript together
          </p>
          <p className="mt-1 text-[0.6875rem] leading-relaxed text-muted-foreground">
            Live cursors show where people are working; overlapping edits are held for
            review instead of overwritten.
          </p>
        </div>
        <ul className="mt-3 max-h-[22rem] space-y-1.5 overflow-y-auto">
          {(collaborators?.members ?? []).map((member) => {
            const live = presence.find((person) => person.user_id === member.user_id);
            return (
              <li
                key={member.user_id}
                className="flex items-center gap-2 rounded-xl border border-border bg-card px-2.5 py-2"
              >
                <span className="relative grid size-8 shrink-0 place-items-center rounded-full bg-pine text-[0.625rem] font-semibold text-ivory">
                  {initials(member.name, member.email)}
                  {live ? (
                    <span className="absolute -bottom-0.5 -right-0.5 size-2.5 rounded-full border-2 border-card bg-moss-soft" />
                  ) : null}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[0.75rem] font-medium text-foreground">
                    {member.name || member.email}
                  </p>
                  <p className="truncate text-[0.625rem] text-muted-foreground">
                    {live ? `${live.path} · line ${live.line}` : member.email}
                  </p>
                </div>
                {collaborators?.can_manage && !member.fixed ? (
                  <span className="relative shrink-0">
                    <select
                      value={member.role}
                      disabled={updateRole.isPending}
                      onChange={(event) =>
                        updateRole.mutate({
                          userId: member.user_id,
                          role: event.target.value as
                            | "editor"
                            | "reviewer"
                            | "viewer"
                            | "none",
                        })
                      }
                      className="peer h-8 appearance-none rounded-full border border-border bg-background pl-2.5 pr-8 text-[0.65625rem] text-foreground outline-none focus-visible:ring-2 focus-visible:ring-moss/45 disabled:cursor-not-allowed disabled:opacity-50 forced-colors:appearance-auto"
                      aria-label={`Access for ${member.email}`}
                    >
                      <option value="editor">Can edit</option>
                      <option value="reviewer">Can review</option>
                      <option value="viewer">Can view</option>
                      <option value="none">No access</option>
                    </select>
                    <ChevronDown
                      aria-hidden="true"
                      className="pointer-events-none absolute right-2.5 top-1/2 size-3 -translate-y-1/2 text-muted-foreground peer-disabled:opacity-50 forced-colors:hidden"
                    />
                  </span>
                ) : (
                  <span className="rounded-full bg-secondary px-2 py-1 font-mono text-[0.5625rem] uppercase tracking-[0.12em] text-muted-foreground">
                    {member.role}
                  </span>
                )}
              </li>
            );
          })}
        </ul>
        {(collaborators?.members.length ?? 0) <= 1 ? (
          <p className="mt-3 rounded-xl border border-dashed border-border px-3 py-3 text-[0.6875rem] leading-relaxed text-muted-foreground">
            Add coauthors in Settings → Team. They appear here immediately and can
            receive a manuscript-specific role.
          </p>
        ) : null}
      </PopoverContent>
    </Popover>
  );
}

function WriterRetargetDialog({
  doc,
  open,
  onOpenChange,
}: {
  doc: WriterDocument;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const router = useRouter();
  const [selection, setSelection] = useState("builtin:ieee");
  const [title, setTitle] = useState("");
  const [preview, setPreview] = useState<WriterRetargetPreview | null>(null);
  const { data: customTemplates } = useQuery({
    queryKey: ["writer-templates"],
    queryFn: api.writerTemplates,
    enabled: open,
  });
  const requestBody = useCallback(() => {
    const [kind, value] = selection.split(":");
    return {
      template: kind === "builtin" ? value : "blank",
      ...(kind === "custom" ? { template_id: Number(value) } : {}),
      ...(title.trim() ? { title: title.trim() } : {}),
      expected_revision: doc.revision,
    };
  }, [doc.revision, selection, title]);
  const inspect = useMutation({
    mutationFn: () => api.writerRetargetPreview(doc.public_id, requestBody()),
    onSuccess: (result) => {
      setPreview(result);
      setTitle((current) => current || result.suggested_title);
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Transfer preview failed."),
  });
  const create = useMutation({
    mutationFn: () => api.writerRetarget(doc.public_id, requestBody()),
    onSuccess: (created) => {
      toast.success("New submission version created. The original is unchanged.");
      onOpenChange(false);
      router.push(`/writer/${created.public_id}`);
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Transfer failed."),
  });
  useEffect(() => {
    if (!open) {
      setPreview(null);
      setTitle("");
    }
  }, [open]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[calc(100dvh-1rem)] flex-col gap-0 overflow-hidden rounded-[1.75rem] p-0 sm:max-w-4xl">
        <DialogHeader className="shrink-0 border-b border-border px-5 py-4 text-left sm:px-7">
          <DialogTitle className="pr-10 font-display text-2xl font-normal sm:text-[1.75rem]">
            Retarget for another venue
          </DialogTitle>
          <DialogDescription className="max-w-3xl text-[0.8125rem] leading-relaxed sm:text-sm">
            Map this manuscript into a fresh conference or journal template. The
            current submission, versions and compiled PDF stay untouched.
          </DialogDescription>
        </DialogHeader>
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4 sm:p-5">
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block">
              <span className="mb-1.5 block font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
                Destination template
              </span>
              <span className="relative block">
                <select
                  value={selection}
                  onChange={(event) => {
                    setSelection(event.target.value);
                    setPreview(null);
                  }}
                  className="peer h-11 w-full appearance-none rounded-xl border border-border bg-background pl-3 pr-10 text-[0.8125rem] text-foreground outline-none focus:border-moss disabled:cursor-not-allowed disabled:opacity-50 forced-colors:appearance-auto"
                >
                  <optgroup label="SixSentences templates">
                    {RETARGET_TEMPLATES.map(([id, label]) => (
                      <option key={id} value={`builtin:${id}`}>
                        {label}
                      </option>
                    ))}
                  </optgroup>
                  {(customTemplates ?? []).length > 0 ? (
                    <optgroup label="Your templates">
                      {(customTemplates ?? []).map((template) => (
                        <option key={template.id} value={`custom:${template.id}`}>
                          {template.name}
                        </option>
                      ))}
                    </optgroup>
                  ) : null}
                </select>
                <ChevronDown aria-hidden="true" className="pointer-events-none absolute right-3.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground peer-disabled:opacity-50 forced-colors:hidden" />
              </span>
            </label>
            <label className="block">
              <span className="mb-1.5 block font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
                New submission name
              </span>
              <Input
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                placeholder={`${doc.title} · new venue`}
                className="h-11 rounded-xl"
              />
            </label>
            <div className="flex items-start gap-3 rounded-2xl border border-moss/20 bg-accent/35 px-4 py-3 text-[0.6875rem] leading-relaxed text-muted-foreground sm:col-span-2">
              <span className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-full bg-moss-surface/10 text-moss">
                <ShieldCheck className="size-3.5" />
              </span>
              <div>
                <p className="font-medium text-foreground">Your current submission stays untouched</p>
                <p className="mt-0.5">
                  Title, authors, abstract, sections, citations, figures, owned sources
                  and linked research context move into a separate project.
                </p>
              </div>
            </div>
          </div>
          <div className="min-h-[10rem] rounded-2xl border border-border bg-secondary/25 p-4 sm:p-5">
            {!preview ? (
              <div className="flex min-h-[8rem] items-center justify-center">
                <div className="flex max-w-lg items-start gap-3 text-left">
                  <Repeat2 className="mt-0.5 size-4 shrink-0 text-moss" />
                  <div>
                    <p className="text-[0.8125rem] font-medium text-foreground">
                      Inspect the transfer before creating it
                    </p>
                    <p className="mt-1 max-w-md text-[0.6875rem] leading-relaxed text-muted-foreground">
                      Preview what maps cleanly, which assets move with the manuscript,
                      and what still needs a human check for the destination template.
                    </p>
                  </div>
                </div>
              </div>
            ) : (
              <div className="space-y-4">
                <div>
                  <p className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
                    Transfer map
                  </p>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {preview.report.mapped.map((item) => (
                      <span
                        key={item}
                        className="rounded-full border border-moss/25 bg-accent px-2.5 py-1 text-[0.65625rem] text-moss"
                      >
                        <Check className="mr-1 inline size-3" />
                        {item}
                      </span>
                    ))}
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  {[
                    ["Sections", preview.report.section_count],
                    ["Citations", preview.report.citation_count],
                    ["Project files", preview.report.extra_files + 1],
                    ["Figures", preview.report.assets],
                  ].map(([label, value]) => (
                    <div key={label} className="rounded-xl border border-border bg-card p-2.5">
                      <p className="font-display text-xl text-foreground">{value}</p>
                      <p className="text-[0.625rem] text-muted-foreground">{label}</p>
                    </div>
                  ))}
                </div>
                {preview.report.warnings.length > 0 ? (
                  <div className="rounded-xl border border-amber-500/25 bg-amber-500/8 p-3">
                    <p className="flex items-center gap-1.5 text-[0.6875rem] font-medium text-foreground">
                      <AlertTriangle className="size-3.5 text-amber-600" />
                      Review after transfer
                    </p>
                    <ul className="mt-1.5 space-y-1 text-[0.65625rem] leading-relaxed text-muted-foreground">
                      {preview.report.warnings.map((warning) => (
                        <li key={warning}>{warning}</li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </div>
            )}
          </div>
        </div>
        <div className="flex shrink-0 flex-wrap items-center justify-end gap-2 border-t border-border bg-card px-4 py-3 sm:px-6">
          <Button
            type="button"
            variant="ghost"
            onClick={() => onOpenChange(false)}
            className="rounded-full"
          >
            Cancel
          </Button>
          {!preview ? (
            <Button
              type="button"
              onClick={() => inspect.mutate()}
              disabled={inspect.isPending}
              className="rounded-full"
            >
              {inspect.isPending ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Repeat2 className="size-4" />
              )}
              Preview transfer
            </Button>
          ) : (
            <Button
              type="button"
              onClick={() => create.mutate()}
              disabled={create.isPending}
              className="rounded-full"
            >
              {create.isPending ? <Loader2 className="size-4 animate-spin" /> : null}
              Create new submission version
            </Button>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

export default function WriterEditorPage() {
  const params = useParams<{ id: string }>();
  // the opaque public_id from the URL (older numeric links still resolve)
  const docId = params.id;
  const queryClient = useQueryClient();
  const { setActiveProjectId } = useActiveProject();
  const editorRef = useRef<ReactCodeMirrorRef>(null);
  const { open: sidebarOpen } = useSidebarUi();

  const {
    data: doc,
    isLoading,
    isFetching: docIsFetching,
    error: loadError,
    refetch: retryDocument,
  } = useQuery({
    queryKey: ["writer-doc", docId],
    queryFn: () => api.writerGet(docId),
    enabled: Boolean(docId),
    retry: retryTransientApiQuery,
    retryDelay: transientApiRetryDelay,
    refetchInterval: (query) =>
      query.state.data?.compile_status === "running" ? 1500 : false,
  });
  const { data: runs } = useRuns();
  useEffect(() => {
    if (doc?.project_id) setActiveProjectId(doc.project_id);
  }, [doc?.project_id, setActiveProjectId]);
  const { data: interviewContexts } = useQuery({
    queryKey: ["writer-interview-contexts", docId],
    queryFn: () => api.writerInterviewContexts(docId),
    enabled: Boolean(docId),
  });
  const { data: surveyContexts } = useQuery({
    queryKey: ["writer-survey-contexts", docId],
    queryFn: () => api.writerSurveyContexts(docId),
    enabled: Boolean(docId),
  });
  const { data: projectFiles } = useQuery({
    queryKey: ["writer-files", docId],
    queryFn: () => api.writerFiles(docId),
    enabled: Boolean(docId),
    refetchInterval: 3_000,
    refetchIntervalInBackground: true,
  });
  const { data: writerReviewMessages = [] } = useQuery({
    queryKey: ["writer-chat", docId],
    queryFn: () => api.writerChatHistory(docId),
    enabled: Boolean(docId),
  });
  const { data: collaborators } = useQuery({
    queryKey: ["writer-collaborators", docId],
    queryFn: () => api.writerCollaborators(docId),
    enabled: Boolean(docId),
  });
  const { data: presence = [] } = useQuery({
    queryKey: ["writer-presence", docId],
    queryFn: () => api.writerPresence(docId),
    enabled: Boolean(docId),
    refetchInterval: 4_000,
    refetchIntervalInBackground: true,
  });
  const { data: guardAudit } = useQuery({
    queryKey: ["writer-audit", docId],
    queryFn: () => api.writerAudit(docId),
    enabled: Boolean(docId) && doc?.compile_status === "ok",
    staleTime: 60_000,
  });
  const { data: writerComments = [] } = useQuery({
    queryKey: ["writer-comments", docId],
    queryFn: () => api.writerComments(docId),
    enabled: Boolean(docId),
    refetchInterval: 1_500,
    refetchIntervalInBackground: true,
  });

  const [content, setContent] = useState<string | null>(null);
  const [optimisticAppliedReviewIds, setOptimisticAppliedReviewIds] = useState<
    Set<string>
  >(new Set());
  const [reviewBusyId, setReviewBusyId] = useState<string | null>(null);
  const reviewBusyRef = useRef<string | null>(null);
  const writerEditReviews = useMemo(
    () =>
      collectWriterEditReviews(
        writerReviewMessages,
        optimisticAppliedReviewIds,
      ),
    [optimisticAppliedReviewIds, writerReviewMessages],
  );
  const pendingWriterEditReviews = useMemo(
    () =>
      activeWriterEditReviewSet(writerEditReviews).filter(
        (review) => review.decision === "pending",
      ),
    [writerEditReviews],
  );
  const writerEditReviewsById = useMemo(
    () => new Map(writerEditReviews.map((review) => [review.id, review])),
    [writerEditReviews],
  );
  const nextPendingWriterEditReview = useMemo(
    () => nextWriterEditReview(writerEditReviews),
    [writerEditReviews],
  );
  const pendingReviewTargetMissing = Boolean(
    nextPendingWriterEditReview
      && projectFiles
      && !projectFiles.some(
        (file) => file.path === nextPendingWriterEditReview.edit.path,
      ),
  );
  const [activeFileId, setActiveFileId] = useState(0);
  const activeProjectFile = projectFiles?.find(
    (file) => file.id === activeFileId,
  );
  const [title, setTitle] = useState("");
  const wordCounts = useMemo(() => {
    const files =
      projectFiles?.map((file) => ({
        ...file,
        content:
          file.id === activeFileId && content !== null ? content : file.content,
      })) ??
      (content === null
        ? []
        : [
            {
              id: 0,
              path: "main.tex",
              content,
              main: true,
              revision: 1,
              updated_by: null,
            },
          ]);
    const texFiles = files.filter((file) => file.path.toLowerCase().endsWith(".tex"));
    const byFile = texFiles.map((file) => ({
      id: file.id,
      path: file.path,
      words: countLatexWords(file.content),
    }));
    return {
      total: byFile.reduce((sum, file) => sum + file.words, 0),
      active: byFile.find((file) => file.id === activeFileId)?.words ?? 0,
      fileCount: byFile.length,
    };
  }, [activeFileId, content, projectFiles]);
  const [view, setView] = useState<"source" | "preview" | "log">("source");
  const [mobilePane, setMobilePane] = useState<MobileWorkspacePane>("workspace");
  const [projectRailOpen, setProjectRailOpen] = useState(true);
  const [chatSplitPercent, setChatSplitPercent] = useState(
    WRITER_DEFAULT_CHAT_PERCENT,
  );
  const [writerLayoutWidth, setWriterLayoutWidth] = useState(0);
  const [projectRailViewportVisible, setProjectRailViewportVisible] = useState(false);
  const [layoutResizing, setLayoutResizing] = useState(false);
  const writerLayoutRef = useRef<HTMLDivElement>(null);
  const writerPreferredSplitRef = useRef(WRITER_DEFAULT_CHAT_PERCENT);
  const writerSplitRestoredRef = useRef(false);
  const writerLayoutReady = !isLoading && Boolean(doc) && content !== null;

  useEffect(() => {
    setProjectRailOpen(localStorage.getItem("six:writer-project-rail") !== "closed");
  }, []);

  useEffect(() => {
    const projectRailViewport = window.matchMedia("(min-width: 1536px)");
    const syncProjectRailViewport = () => {
      setProjectRailViewportVisible(projectRailViewport.matches);
    };
    syncProjectRailViewport();
    projectRailViewport.addEventListener("change", syncProjectRailViewport);
    return () => projectRailViewport.removeEventListener("change", syncProjectRailViewport);
  }, []);

  const writerWorkspaceMinimum =
    WRITER_EDITOR_MIN_PX
    + (view === "source" && projectRailOpen && projectRailViewportVisible
      ? WRITER_PROJECT_RAIL_PX
      : 0);

  const constrainChatSplit = useCallback(
    (candidate: number, measuredWidth?: number): number => {
      const width = measuredWidth
        ?? writerLayoutRef.current?.getBoundingClientRect().width
        ?? 0;
      return clampWorkspaceSplitPercent(candidate, {
        containerWidth: width,
        primaryMinPx: WRITER_CHAT_MIN_PX,
        secondaryMinPx: writerWorkspaceMinimum,
        dividerPx: WRITER_DIVIDER_PX,
        minPercent: WRITER_CHAT_MIN_PERCENT,
        maxPercent: WRITER_CHAT_MAX_PERCENT,
      });
    },
    [writerWorkspaceMinimum],
  );

  useEffect(() => {
    if (!writerLayoutReady) return;
    const layout = writerLayoutRef.current;
    if (!layout) return;
    if (!writerSplitRestoredRef.current) {
      const stored = Number.parseFloat(
        localStorage.getItem("six:writer-chat-split") ?? "",
      );
      if (Number.isFinite(stored)) {
        writerPreferredSplitRef.current = stored;
      }
      writerSplitRestoredRef.current = true;
    }
    const renderPreferredSplit = () => {
      const measuredWidth = layout.getBoundingClientRect().width;
      setWriterLayoutWidth(measuredWidth);
      setChatSplitPercent(
        constrainChatSplit(writerPreferredSplitRef.current, measuredWidth),
      );
    };
    renderPreferredSplit();
    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", renderPreferredSplit);
      return () => window.removeEventListener("resize", renderPreferredSplit);
    }
    const observer = new ResizeObserver(renderPreferredSplit);
    observer.observe(layout);
    return () => observer.disconnect();
  }, [constrainChatSplit, writerLayoutReady]);

  const writerSplitLayout = isWorkspaceSplitFeasible({
    containerWidth: writerLayoutWidth,
    primaryMinPx: WRITER_CHAT_MIN_PX,
    secondaryMinPx: writerWorkspaceMinimum,
    dividerPx: WRITER_DIVIDER_PX,
    minPercent: WRITER_CHAT_MIN_PERCENT,
    maxPercent: WRITER_CHAT_MAX_PERCENT,
  });

  const commitChatSplit = useCallback(
    (candidate: number) => {
      const next = constrainChatSplit(candidate);
      writerPreferredSplitRef.current = next;
      setChatSplitPercent(next);
      localStorage.setItem("six:writer-chat-split", String(next));
    },
    [constrainChatSplit],
  );

  const updateChatSplitFromPointer = useCallback(
    (clientX: number) => {
      const bounds = writerLayoutRef.current?.getBoundingClientRect();
      if (!bounds || bounds.width <= 0) return;
      commitChatSplit(((clientX - bounds.left) / bounds.width) * 100);
    },
    [commitChatSplit],
  );

  const beginLayoutResize = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      if (!writerSplitLayout) return;
      event.preventDefault();
      const pointerId = event.pointerId;
      const divider = event.currentTarget;
      divider.setPointerCapture(pointerId);
      setLayoutResizing(true);
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";

      const move = (moveEvent: PointerEvent) => {
        updateChatSplitFromPointer(moveEvent.clientX);
      };
      const finish = () => {
        if (divider.hasPointerCapture(pointerId)) {
          divider.releasePointerCapture(pointerId);
        }
        setLayoutResizing(false);
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", finish);
        window.removeEventListener("pointercancel", finish);
      };

      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", finish);
      window.addEventListener("pointercancel", finish);
    },
    [updateChatSplitFromPointer, writerSplitLayout],
  );

  useEffect(
    () => () => {
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    },
    [],
  );

  const adjustChatSplitFromKeyboard = useCallback(
    (event: ReactKeyboardEvent<HTMLDivElement>) => {
      if (
        event.key !== "ArrowLeft"
        && event.key !== "ArrowRight"
        && event.key !== "Home"
        && event.key !== "End"
      ) return;
      event.preventDefault();
      const candidate = event.key === "Home"
        ? 0
        : event.key === "End"
          ? 100
          : chatSplitPercent
            + (event.key === "ArrowLeft" ? -1 : 1) * (event.shiftKey ? 5 : 2);
      commitChatSplit(candidate);
    },
    [chatSplitPercent, commitChatSplit],
  );

  useEffect(() => {
    const onTourView = (event: Event) => {
      const detail = (
        event as CustomEvent<{ view?: "source" | "preview" | "log" }>
      ).detail;
      if (!detail?.view) return;
      setMobilePane("workspace");
      setView(detail.view);
    };
    const onTourPane = (event: Event) => {
      const detail = (
        event as CustomEvent<{ pane?: MobileWorkspacePane }>
      ).detail;
      if (detail?.pane === "agent" || detail?.pane === "workspace") {
        setMobilePane(detail.pane);
      }
    };
    window.addEventListener("six:tour-writer-view", onTourView);
    window.addEventListener("six:tour-writer-pane", onTourPane);
    return () => {
      window.removeEventListener("six:tour-writer-view", onTourView);
      window.removeEventListener("six:tour-writer-pane", onTourPane);
    };
  }, []);
  const [saveState, setSaveState] = useState<"saved" | "saving" | "unsaved">(
    "saved",
  );
  const [sourceConflict, setSourceConflict] = useState<{
    fileId: number;
    path: string;
    local: string;
    remote: string;
    revision: number;
    reviewed: string;
  } | null>(null);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [retargetOpen, setRetargetOpen] = useState(false);
  const [linkMenuOpen, setLinkMenuOpen] = useState(false);
  const [toolbarPanel, setToolbarPanel] = useState<WriterToolbarPanel | null>(null);
  const [selection, setSelection] = useState<WriterSelection | null>(null);
  const [pdfCommentsVisible, setPdfCommentsVisible] = useState(false);
  const [commentFocusPage, setCommentFocusPage] = useState<{
    page: number;
    commentId?: number;
    key: number;
  } | null>(null);
  const [commentAgentTask, setCommentAgentTask] = useState<{
    key: number;
    text: string;
    selection: WriterSelection | null;
  } | null>(null);
  const [cursor, setCursor] = useState({ line: 1, column: 1 });
  const [pdfFocus, setPdfFocus] = useState<{
    page: number;
    x: number;
    y: number;
    width: number;
    height: number;
  } | null>(null);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const previousStatus = useRef<string | null>(null);
  const activeFileIdRef = useRef(0);
  const baseContentRef = useRef(new Map<number, string>());
  const revisionRef = useRef(new Map<number, number>());
  useEffect(() => {
    activeFileIdRef.current = activeFileId;
  }, [activeFileId]);
  const accessRole = collaborators?.my_role ?? doc?.access_role ?? "viewer";
  const canEdit = accessRole === "owner" || accessRole === "editor";
  const canManage = accessRole === "owner";

  const createComment = useMutation({
    mutationFn: (input: ReviewCommentInput) =>
      api.writerCommentCreate(docId, {
        quote: input.quote,
        page: input.page,
        anchor_prefix: input.anchor_prefix,
        anchor_suffix: input.anchor_suffix,
        anchor_revision: input.anchor_revision,
        content: input.content,
      }),
    onSuccess: (created) => {
      queryClient.setQueryData<WriterComment[]>(
        ["writer-comments", docId],
        (current) =>
          current?.some((comment) => comment.id === created.id)
            ? current
            : [...(current ?? []), created],
      );
      setPdfCommentsVisible(true);
      toast.success("Comment added to the manuscript.");
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Comment could not be added."),
  });
  const submitAuthorComment = useCallback(
    async (input: ReviewCommentInput) => {
      await createComment.mutateAsync(input);
    },
    [createComment],
  );
  const focusComment = useCallback((comment: WriterComment) => {
    if (!comment.page) return;
    setView("preview");
    setMobilePane("workspace");
    setPdfCommentsVisible(true);
    setCommentFocusPage({
      page: comment.page,
      commentId: comment.id,
      key: Date.now(),
    });
  }, []);
  const askAiAboutComment = useCallback(
    (comment: WriterComment) => {
      const quoteContext = comment.quote
        ? ` It is anchored to this PDF passage: "${comment.quote}".`
        : "";
      setCommentAgentTask({
        key: Date.now(),
        text:
          `Address this manuscript review comment: "${comment.content}".`
          + quoteContext
          + " Propose precise, reviewable source edits that resolve it. Do not claim the comment is resolved.",
        selection:
          comment.quote && comment.page
            ? { kind: "pdf", page: comment.page, quote: comment.quote }
            : null,
      });
      setMobilePane("agent");
    },
    [],
  );

  // auto compile: every settled autosave triggers a build, so the preview
  // keeps itself fresh without touching the Compile button
  const [autoCompile, setAutoCompile] = useState(false);
  useEffect(() => {
    setAutoCompile(localStorage.getItem("six:writer-autocompile") === "1");
  }, []);
  const autoRef = useRef(autoCompile);
  useEffect(() => {
    autoRef.current = autoCompile;
  }, [autoCompile]);
  // a save that lands mid-build queues exactly one follow-up build
  const queuedCompile = useRef(false);
  const compileNowRef = useRef<() => void>(() => {});
  // auto compile waits for real writing rest, not for every save debounce:
  // each keystroke pushes the build out again
  const compileIdleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const scheduleAutoCompile = useCallback(() => {
    if (compileIdleTimer.current) clearTimeout(compileIdleTimer.current);
    if (!autoRef.current) return;
    compileIdleTimer.current = setTimeout(() => {
      compileNowRef.current();
    }, 5000);
  }, []);
  useEffect(
    () => () => {
      if (compileIdleTimer.current) clearTimeout(compileIdleTimer.current);
    },
    [],
  );

  useEffect(() => {
    if (doc && content === null) {
      setContent(doc.content);
      setTitle(doc.title);
      baseContentRef.current.set(0, doc.content);
      revisionRef.current.set(0, doc.revision);
      previousStatus.current = doc.compile_status;
      if (doc.compile_status === "ok") {
        void fetchWriterPdfUrl(docId).then(setPdfUrl).catch(() => {});
      }
    }
  }, [doc, content, docId]);

  useEffect(() => {
    if (!projectFiles?.length) return;
    for (const file of projectFiles) {
      const knownRevision = revisionRef.current.get(file.id);
      if (knownRevision === undefined) {
        revisionRef.current.set(file.id, file.revision);
        baseContentRef.current.set(file.id, file.content);
        continue;
      }
      if (file.revision <= knownRevision) continue;
      revisionRef.current.set(file.id, file.revision);
      baseContentRef.current.set(file.id, file.content);
      if (file.id === activeFileIdRef.current && saveState === "saved") {
        setContent(file.content);
        toast.message("A coauthor updated this file.", {
          description: "The latest saved revision is now in the editor.",
        });
      }
    }
  }, [projectFiles, saveState]);

  useEffect(() => {
    const path = activeProjectFile?.path ?? "main.tex";
    const mode = view === "source" ? "source" : view === "preview" ? "preview" : "log";
    const heartbeat = () => {
      void api
        .writerPresenceUpdate(docId, {
          path,
          line: Math.max(1, cursor.line),
          mode,
        })
        .catch(() => {});
    };
    heartbeat();
    const interval = window.setInterval(heartbeat, 10_000);
    return () => window.clearInterval(interval);
  }, [activeProjectFile?.path, cursor.line, docId, view]);

  // when a background compile settles, fetch the PDF or surface the errors;
  // the view NEVER switches on its own — the author picks the tab
  useEffect(() => {
    if (!doc) return;
    const previous = previousStatus.current;
    previousStatus.current = doc.compile_status;
    if (previous !== "running") return;
    if (doc.compile_status === "ok") {
      toast.success("Compiled.");
      void queryClient.invalidateQueries({ queryKey: ["writer-audit", docId] });
      void fetchWriterPdfUrl(docId)
        .then((url) => {
          setPdfUrl((old) => {
            if (old) URL.revokeObjectURL(old);
            return url;
          });
        })
        .catch(() => {});
    } else if (doc.compile_status === "error") {
      toast.error("Compilation failed. See the error list.");
    }
    // a save that arrived during the build starts the next one
    if (queuedCompile.current && autoRef.current) {
      queuedCompile.current = false;
      compileNowRef.current();
    }
  }, [doc, docId]);
  useEffect(
    () => () => {
      if (pdfUrl) URL.revokeObjectURL(pdfUrl);
    },
    [pdfUrl],
  );

  const persist = useMutation({
    mutationFn: (body: {
      title?: string;
      content?: string;
      run_ids?: number[];
      dataset_ids?: string[];
    }) => api.writerPatch(docId, body),
    onSuccess: () => {
      setSaveState("saved");
      void queryClient.invalidateQueries({ queryKey: ["writer-docs"] });
    },
    onError: (error) => {
      setSaveState("unsaved");
      toast.error(error instanceof Error ? error.message : "Save failed.");
    },
  });

  const persistSource = useMutation({
    mutationFn: async ({
      fileId,
      source,
      expectedRevision,
      baseContent,
    }: {
      fileId: number;
      source: string;
      expectedRevision: number;
      baseContent: string;
    }) => {
      if (fileId === 0) {
        const saved = await api.writerPatch(docId, {
          content: source,
          expected_revision: expectedRevision,
          base_content: baseContent,
        });
        return {
          content: saved.content,
          revision: saved.revision,
          updated_by: saved.updated_by,
          merged: Boolean((saved as WriterDocument & { merged?: boolean }).merged),
        };
      }
      const saved = await api.writerFilePatch(
        docId,
        fileId,
        source,
        expectedRevision,
        baseContent,
      );
      return { ...saved, merged: Boolean((saved as WriterProjectFile & { merged?: boolean }).merged) };
    },
    onSuccess: (saved, variables) => {
      setSaveState("saved");
      revisionRef.current.set(variables.fileId, saved.revision);
      baseContentRef.current.set(variables.fileId, saved.content);
      if (activeFileIdRef.current === variables.fileId) {
        setContent(saved.content);
      }
      queryClient.setQueryData<WriterProjectFile[]>(
        ["writer-files", docId],
        (old) =>
          old?.map((file) =>
            file.id === variables.fileId
              ? {
                  ...file,
                  content: saved.content,
                  revision: saved.revision,
                  updated_by: saved.updated_by,
                }
              : file,
          ),
      );
      if (variables.fileId === 0) {
        queryClient.setQueryData<WriterDocument>(
          ["writer-doc", docId],
          (old) =>
            old
              ? {
                  ...old,
                  content: saved.content,
                  revision: saved.revision,
                  updated_by: saved.updated_by,
                }
              : old,
        );
        void queryClient.invalidateQueries({ queryKey: ["writer-docs"] });
      }
      if (saved.merged) {
        toast.success("Saved with your coauthor's independent changes.");
      }
    },
    onError: (error, variables) => {
      setSaveState("unsaved");
      if (error instanceof ApiError && error.status === 409) {
        const detail = error.detail as {
          code?: string;
          path?: string;
          revision?: number;
          content?: string;
        } | null;
        if (
          detail?.code === "writer_revision_conflict" &&
          typeof detail.revision === "number" &&
          typeof detail.content === "string"
        ) {
          setSourceConflict({
            fileId: variables.fileId,
            path: detail.path ?? "main.tex",
            local: variables.source,
            remote: detail.content,
            revision: detail.revision,
            reviewed: variables.source,
          });
          return;
        }
      }
      toast.error(error instanceof Error ? error.message : "Save failed.");
    },
  });

  const saveVariables = useCallback((fileId: number, source: string) => {
    return {
      fileId,
      source,
      expectedRevision: revisionRef.current.get(fileId) ?? 1,
      baseContent: baseContentRef.current.get(fileId) ?? source,
    };
  }, []);

  const flushActiveSource = useCallback(async (): Promise<void> => {
    if (saveTimer.current) {
      clearTimeout(saveTimer.current);
      saveTimer.current = null;
    }
    if (content === null || saveState === "saved") return;
    setSaveState("saving");
    await persistSource.mutateAsync(saveVariables(activeFileIdRef.current, content));
    setSaveState("saved");
  }, [content, persistSource, saveState, saveVariables]);

  const scheduleSave = useCallback(
    (next: string) => {
      if (!canEdit) return;
      setContent(next);
      setSaveState("unsaved");
      scheduleAutoCompile();
      if (saveTimer.current) clearTimeout(saveTimer.current);
      saveTimer.current = setTimeout(() => {
        setSaveState("saving");
        persistSource.mutate(saveVariables(activeFileIdRef.current, next));
      }, 1200);
    },
    [canEdit, persistSource, saveVariables, scheduleAutoCompile],
  );

  const openProjectFile = useCallback(
    (file: WriterProjectFile) => {
      if (file.id === activeFileIdRef.current) return;
      if (saveTimer.current && content !== null) {
        clearTimeout(saveTimer.current);
        saveTimer.current = null;
        persistSource.mutate(saveVariables(activeFileIdRef.current, content));
      }
      setActiveFileId(file.id);
      activeFileIdRef.current = file.id;
      revisionRef.current.set(file.id, file.revision);
      baseContentRef.current.set(file.id, file.content);
      setContent(file.content);
      setSaveState("saved");
      setView("source");
    },
    [content, persistSource, saveVariables],
  );

  const openEditReview = useCallback(
    (review: WriterEditReview) => {
      setView("source");
      setMobilePane("workspace");
      const file = projectFiles?.find(
        (candidate) => candidate.path === review.edit.path,
      );
      if (file) openProjectFile(file);
    },
    [openProjectFile, projectFiles],
  );

  const compiling = doc?.compile_status === "running";
  const compile = useMutation({
    mutationFn: async () => {
      if (saveTimer.current) {
        clearTimeout(saveTimer.current);
        saveTimer.current = null;
      }
      if (content !== null) {
        await persistSource.mutateAsync(
          saveVariables(activeFileIdRef.current, content),
        );
      }
      setSaveState("saved");
      return api.writerCompile(docId);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["writer-doc", docId] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Compile failed."),
  });
  // latest-ref: the autosave timeout always reaches the current build state
  compileNowRef.current = () => {
    if (compiling || compile.isPending) queuedCompile.current = true;
    else compile.mutate();
  };

  const insertSnippet = useCallback((snippet: string) => {
    setView("source");
    const view = editorRef.current?.view;
    if (!view) {
      void navigator.clipboard.writeText(snippet);
      toast.success("Copied. Paste it where it belongs.");
      return;
    }
    view.dispatch(view.state.replaceSelection(snippet));
    view.focus();
  }, []);

  const insertCite = useCallback(
    (key: string) => insertSnippet(`\\citep{${key}}`),
    [insertSnippet],
  );

  const insertFigure = useCallback(
    (filename: string) => {
      setView("source");
      const view = editorRef.current?.view;
      const figure =
        "\\begin{figure}[t]\n  \\centering\n  "
        + `\\includegraphics[width=\\linewidth]{${filename}}\n`
        + "  \\caption{…}\n  \\label{fig:"
        + filename.replace(/\.[^.]+$/, "").toLowerCase()
        + "}\n\\end{figure}\n";
      if (!view) {
        void navigator.clipboard.writeText(figure);
        toast.success("Copied. Paste it inside the document body.");
        return;
      }

      const source = view.state.doc.toString();
      const beginToken = "\\begin{document}";
      const beginDocument = source.indexOf(beginToken);
      const bodyStart =
        beginDocument >= 0 ? beginDocument + beginToken.length : -1;
      const selection = view.state.selection.main;
      const selectionStartsInPreamble =
        bodyStart >= 0 && selection.from <= bodyStart;
      const hasGraphicx =
        /\\usepackage(?:\s*\[[^\]]*\])?\s*\{[^}]*\bgraphicx\b[^}]*\}/.test(
          source,
        );
      const changes: Array<{
        from: number;
        to?: number;
        insert: string;
      }> = [];

      if (!hasGraphicx && beginDocument >= 0) {
        changes.push({
          from: beginDocument,
          insert: "\\usepackage{graphicx}\n",
        });
      }
      if (selectionStartsInPreamble) {
        changes.push({
          from: bodyStart,
          insert: `\n${figure}`,
        });
      } else {
        changes.push({
          from: selection.from,
          to: selection.to,
          insert: figure,
        });
      }

      view.dispatch({ changes });
      view.focus();
    },
    [],
  );

  // accepted proposals land in the live editor content, never silently;
  // the batch form applies sequentially so edit two sees edit one's result.
  // Every apply is reported to the document's transparency ledger.
  const applyEdits = useCallback(
    async (
      edits: WriterEdit[],
      opts?: { auto?: boolean; messageId?: number | null },
    ): Promise<number[]> => {
      await flushActiveSource();
      const result = await api.writerApplyEdits(docId, edits, opts);
      void queryClient.invalidateQueries({ queryKey: ["writer-chat", docId] });
      if (result.applied.length === 0) return [];
      const activePath = activeProjectFile?.path ?? "main.tex";
      const activeSource = result.files.find((file) => file.path === activePath);
      if (activeSource) {
        revisionRef.current.set(activeFileIdRef.current, activeSource.revision);
        baseContentRef.current.set(activeFileIdRef.current, activeSource.content);
        setContent(activeSource.content);
      }
      queryClient.setQueryData<WriterProjectFile[]>(
        ["writer-files", docId],
        (old) =>
          old?.map((file) => {
            const landed = result.files.find(
              (candidate) => candidate.path === file.path,
            );
            if (!landed) return file;
            revisionRef.current.set(file.id, landed.revision);
            baseContentRef.current.set(file.id, landed.content);
            return {
              ...file,
              content: landed.content,
              revision: landed.revision,
            };
          }),
      );
      const main = result.files.find((file) => file.path === "main.tex");
      if (main) {
        queryClient.setQueryData<WriterDocument>(
          ["writer-doc", docId],
          (old) =>
            old
              ? {
                  ...old,
                  content: main.content,
                  revision: main.revision,
                  compile_status: "none",
                  compile_log: "",
                  compile_errors: [],
                }
              : old,
        );
      }
      setSaveState("saved");
      setPdfUrl((previous) => {
        if (previous) URL.revokeObjectURL(previous);
        return null;
      });
      scheduleAutoCompile();
      if (!opts?.auto) setView("source");
      void queryClient.invalidateQueries({ queryKey: ["writer-files", docId] });
      void queryClient.invalidateQueries({ queryKey: ["writer-doc", docId] });
      void queryClient.invalidateQueries({ queryKey: ["writer-snapshots", docId] });
      void queryClient.invalidateQueries({ queryKey: ["writer-contribution-log", docId] });
      void queryClient.invalidateQueries({ queryKey: ["writer-audit", docId] });
      return result.applied;
    },
    [
      activeProjectFile?.path,
      docId,
      flushActiveSource,
      queryClient,
      scheduleAutoCompile,
    ],
  );

  const applyEdit = useCallback(
    async (edit: WriterEdit, messageId: number): Promise<boolean> => {
      try {
        const landed = await applyEdits([edit], { auto: false, messageId });
        if (landed.length > 0) {
          toast.success(`Edit applied to ${edit.path}.`);
          return true;
        }
        toast.error("The project changed since this proposal; it no longer applies.");
        return false;
      } catch (error) {
        toast.error(error instanceof Error ? error.message : "Edit could not be applied.");
        return false;
      }
    },
    [applyEdits],
  );

  const approveEditReview = useCallback(
    async (review: WriterEditReview): Promise<boolean> => {
      if (reviewBusyRef.current) return false;
      reviewBusyRef.current = review.id;
      setReviewBusyId(review.id);
      try {
        const landed = await applyEdit(review.edit, review.messageId);
        if (!landed) return false;
        setOptimisticAppliedReviewIds((current) => {
          const next = new Set(current);
          next.add(review.id);
          return next;
        });
        return true;
      } finally {
        reviewBusyRef.current = null;
        setReviewBusyId(null);
      }
    },
    [applyEdit],
  );

  const rejectEditReview = useCallback(
    async (review: WriterEditReview): Promise<boolean> => {
      if (reviewBusyRef.current) return false;
      reviewBusyRef.current = review.id;
      setReviewBusyId(review.id);
      try {
        const result = await api.writerRejectEdit(
          docId,
          review.messageId,
          review.index,
        );
        queryClient.setQueryData<WriterMessage[]>(
          ["writer-chat", docId],
          (current) =>
            current?.map((message) =>
              message.id === review.messageId
                ? {
                    ...message,
                    payload: {
                      ...message.payload,
                      edit_decisions: {
                        ...(message.payload.edit_decisions ?? {}),
                        [String(review.index)]: "rejected",
                        ...Object.fromEntries(
                          result.superseded.map((index) => [
                            String(index),
                            "superseded" as const,
                          ]),
                        ),
                      },
                    },
                  }
                : message,
            ),
        );
        void queryClient.invalidateQueries({ queryKey: ["writer-chat", docId] });
        toast.success("Proposed edit rejected. The manuscript was not changed.");
        return true;
      } catch (error) {
        toast.error(
          error instanceof Error ? error.message : "Edit could not be rejected.",
        );
        return false;
      } finally {
        reviewBusyRef.current = null;
        setReviewBusyId(null);
      }
    },
    [docId, queryClient],
  );

  // downloads: the PDF straight from its object URL, the source as a blob
  const fileSlug = (title.trim() || "document")
    .replace(/[^\w\d-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .toLowerCase();
  const saveUrl = useCallback((name: string, url: string) => {
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = name;
    anchor.click();
  }, []);
  const downloadSource = useCallback(() => {
    if (content === null) return;
    const url = URL.createObjectURL(
      new Blob([content], { type: "application/x-tex" }),
    );
    saveUrl(activeProjectFile?.path.split("/").pop() ?? `${fileSlug}.tex`, url);
    URL.revokeObjectURL(url);
  }, [activeProjectFile?.path, content, fileSlug, saveUrl]);

  const jumpToLine = useCallback((line: number | null) => {
    setView("source");
    const view = editorRef.current?.view;
    if (!view || !line) return;
    const target = view.state.doc.line(Math.min(line, view.state.doc.lines));
    view.dispatch({
      selection: { anchor: target.from },
      scrollIntoView: true,
    });
    view.focus();
  }, []);

  const navigateToSource = useCallback(
    (path: string, line: number) => {
      const target = projectFiles?.find((file) => file.path === path);
      if (target && target.id !== activeFileIdRef.current) openProjectFile(target);
      setView("source");
      requestAnimationFrame(() => {
        requestAnimationFrame(() => jumpToLine(line));
      });
    },
    [jumpToLine, openProjectFile, projectFiles],
  );

  const locateSourceInPdf = useCallback(async () => {
    try {
      await flushActiveSource();
      const position = await api.writerSyncSource(
        docId,
        activeProjectFile?.path ?? "main.tex",
        cursor.line,
        cursor.column,
      );
      setPdfFocus(position);
      setView("preview");
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : "Compile the latest manuscript before syncing it.",
      );
    }
  }, [activeProjectFile?.path, cursor, docId, flushActiveSource]);

  const insertCitationAtFinding = useCallback(
    (key: string, path: string, line: number) => {
      navigateToSource(path, line);
      window.setTimeout(() => {
        const editor = editorRef.current?.view;
        if (!editor) return;
        const target = editor.state.doc.line(
          Math.min(Math.max(line, 1), editor.state.doc.lines),
        );
        const citation = ` \\citep{${key}}`;
        editor.dispatch({
          changes: { from: target.to, insert: citation },
          selection: { anchor: target.to + citation.length },
          scrollIntoView: true,
        });
        editor.focus();
        toast.success(`Inserted ${key} at ${path}:${line}.`);
      }, 120);
    },
    [navigateToSource],
  );

  const linkedRuns = doc?.run_ids ?? [];
  const completedRuns = (runs ?? []).filter(
    (run) => run.status === "completed" && run.prisma,
  );
  const linkedRunObjs = (runs ?? []).filter((run) =>
    linkedRuns.includes(run.id),
  );
  const compileErrors = doc?.compile_errors ?? [];
  const outline = useMemo(() => {
    const entries: Array<{ title: string; line: number; depth: number }> = [];
    (content ?? "").split("\n").forEach((line, index) => {
      const match = line.match(
        /^\\(part|chapter|section|subsection|subsubsection)\*?\{([^}]*)\}/,
      );
      if (!match) return;
      entries.push({
        title: match[2].trim() || "Untitled section",
        line: index + 1,
        depth: ["part", "chapter", "section"].includes(match[1])
          ? 0
          : match[1] === "subsection"
            ? 1
            : 2,
      });
    });
    return entries;
  }, [content]);

  // include-set keys feed the editor's \cite autocomplete
  const { data: editorCitations } = useQuery({
    queryKey: ["writer-citations", docId],
    queryFn: () => api.writerCitations(docId),
    enabled: Boolean(docId),
  });

  const [contribOpen, setContribOpen] = useState(false);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [newFileName, setNewFileName] = useState("");
  const [renameFile, setRenameFile] = useState<WriterProjectFile | null>(null);
  const [renameFilePath, setRenameFilePath] = useState("");
  const [deleteFileTarget, setDeleteFileTarget] = useState<WriterProjectFile | null>(null);
  const deleteFileInFlightRef = useRef(false);
  useEffect(() => {
    deleteFileInFlightRef.current = false;
    setDeleteFileTarget(null);
  }, [docId]);
  const createProjectFile = useMutation({
    mutationFn: () => api.writerFileCreate(docId, newFileName.trim()),
    onSuccess: (file) => {
      setNewFileName("");
      void queryClient.invalidateQueries({ queryKey: ["writer-files", docId] });
      openProjectFile(file);
      toast.success(`${file.path} created.`);
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Could not create file."),
  });
  const createExtraFile = () => {
    createProjectFile.mutate();
  };
  const deleteProjectFile = useMutation({
    mutationFn: (fileId: number) => api.writerFileDelete(docId, fileId),
    onSuccess: () => {
      deleteFileInFlightRef.current = false;
      setDeleteFileTarget(null);
      const main = projectFiles?.find((file) => file.main);
      if (main) openProjectFile(main);
      void queryClient.invalidateQueries({ queryKey: ["writer-files", docId] });
      toast.success("File removed.");
    },
    onError: (error) => {
      deleteFileInFlightRef.current = false;
      toast.error(error instanceof Error ? error.message : "Could not remove file.");
    },
  });
  const renameProjectFile = useMutation({
    mutationFn: async ({
      fileId,
      path,
    }: {
      fileId: number;
      path: string;
    }) => {
      if (fileId === activeFileIdRef.current) await flushActiveSource();
      return api.writerFileRename(docId, fileId, path);
    },
    onSuccess: (renamed) => {
      revisionRef.current.set(renamed.id, renamed.revision);
      baseContentRef.current.set(renamed.id, renamed.content);
      queryClient.setQueryData<WriterProjectFile[]>(
        ["writer-files", docId],
        (current) =>
          current
            ?.map((file) => (file.id === renamed.id ? renamed : file))
            .sort((left, right) => left.path.localeCompare(right.path)),
      );
      setRenameFile(null);
      setRenameFilePath("");
      toast.success(`Renamed to ${renamed.path}.`);
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Could not rename file."),
  });
  const beginFileRename = (file: WriterProjectFile) => {
    setRenameFile(file);
    setRenameFilePath(file.path);
  };

  // Download > Save as template: the current source becomes an org template
  const [templateOpen, setTemplateOpen] = useState(false);
  const [templateName, setTemplateName] = useState("");
  const saveTemplate = useMutation({
    mutationFn: () =>
      api.writerTemplateCreate(templateName.trim(), content ?? ""),
    onSuccess: (saved) => {
      toast.success(`Template "${saved.name}" saved.`);
      setTemplateOpen(false);
      void queryClient.invalidateQueries({ queryKey: ["writer-templates"] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "That didn't work."),
  });

  // the TikZ flow needs tikz; documents from older templates may not load
  // it, so the insert quietly completes the preamble instead of failing
  const ensureTikzPreamble = useCallback((): void => {
    const view = editorRef.current?.view;
    if (!view) return;
    const source = view.state.doc.toString();
    if (/\\usepackage(\[[^\]]*\])?\{(tikz|pgfplots)\}/.test(source)) return;
    const anchor = source.indexOf("\\begin{document}");
    if (anchor < 0) return;
    view.dispatch({
      changes: { from: anchor, insert: "\\usepackage{tikz}\n" },
    });
    toast.success("Added \\usepackage{tikz} to the preamble.");
  }, []);

  const insertArtifact = useCallback(
    async (kind: "evidence" | "methods" | "prisma", runId: number) => {
      try {
        const artifact = await api.writerArtifact(docId, kind, runId);
        if (kind === "prisma") ensureTikzPreamble();
        insertSnippet(`\n${artifact.latex}\n`);
        toast.success("Inserted at the cursor.");
      } catch (error) {
        toast.error(
          error instanceof Error ? error.message : "That didn't work.",
        );
      }
    },
    [docId, insertSnippet, ensureTikzPreamble],
  );

  if (loadError && !doc) {
    return (
      <section role="status" className="grid min-h-0 flex-1 place-content-center gap-4 p-8 text-center">
        <h1 className="font-display text-3xl">Manuscript unavailable</h1>
        <p className="text-sm text-muted-foreground">This manuscript could not be loaded. Try again, or return to your manuscripts.</p>
        <div className="flex flex-wrap justify-center gap-2">
          <Button type="button" size="sm" disabled={docIsFetching} onClick={() => void retryDocument()}>
            {docIsFetching ? "Loading…" : "Try again"}
          </Button>
          <Button asChild variant="outline" size="sm"><Link href="/writer">Back to manuscripts</Link></Button>
        </div>
      </section>
    );
  }
  if (isLoading || !doc || content === null) {
    return (
      <Loader2 className="mx-auto my-24 size-5 animate-spin text-muted-foreground" />
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <h1 className="sr-only">Manuscript editor: {title || "Untitled"}</h1>
      {Boolean(loadError) && (
        <div role="status" className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-2 text-sm">
          <p>The latest manuscript update could not be loaded. Your current text is still here; recent changes may not be saved yet.</p>
          <Button type="button" variant="outline" size="sm" disabled={docIsFetching} onClick={() => void retryDocument()}>
            {docIsFetching ? "Loading…" : "Try again"}
          </Button>
        </div>
      )}
      <header
        className={cn(
          "flex min-h-14 shrink-0 flex-nowrap items-center gap-2 border-b border-border px-3 py-2.5 sm:px-5 lg:px-7",
          !sidebarOpen && "lg:pl-[7.5rem]", // room for the floating controls
        )}
      >
        <div
          data-tour="writer-document-header"
          className="flex min-w-8 max-w-[18rem] flex-1 items-center gap-2 overflow-hidden sm:min-w-[6.5rem] 2xl:w-[clamp(8rem,18vw,18rem)] 2xl:flex-none"
        >
          <Button asChild variant="ghost" size="icon" className="size-8 shrink-0 rounded-full">
            <Link href="/writer" aria-label="Back to manuscripts">
              <ArrowLeft className="size-4" />
            </Link>
          </Button>
          <input
            value={title}
            maxLength={240}
            readOnly={!canEdit}
            onChange={(event) => setTitle(event.target.value)}
            onBlur={() => {
              if (canEdit) persist.mutate({ title });
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter") event.currentTarget.blur();
              if (event.key === "Escape") {
                setTitle(doc.title);
                event.currentTarget.blur();
              }
            }}
            aria-label="Document title"
            className="hidden min-w-0 flex-1 bg-transparent text-[1.0625rem] font-medium text-foreground outline-none placeholder:text-muted-foreground/50 sm:block"
            placeholder="Untitled"
          />
        </div>
        <div className="hidden min-w-0 flex-1 items-center justify-end overflow-hidden 2xl:flex">
        <div className="hidden min-w-0 items-center justify-end gap-x-3 min-[2350px]:flex">
        <div
          data-tour="writer-context-tools"
          className={cn(
            "flex shrink-0 items-center gap-1.5 border-l border-border/70 pl-3",
            !canEdit && "pointer-events-none opacity-45",
          )}
          aria-disabled={!canEdit}
        >
        <Popover>
          <PopoverTrigger asChild>
            <Button
              variant="outline"
              size="sm"
              className="h-8 rounded-full text-[0.78125rem]"
            >
              <Quote className="size-3.5" />
              Cite
            </Button>
          </PopoverTrigger>
          <PopoverContent align="end" className="w-[22rem] p-3">
            <CiteList docId={docId} onInsert={insertCite} />
          </PopoverContent>
        </Popover>
        <Popover>
          <PopoverTrigger asChild>
            <Button variant="outline" size="sm" className="h-8 rounded-full text-[0.78125rem]">
              <BookMarked className="size-3.5" />
              Sources
            </Button>
          </PopoverTrigger>
          <PopoverContent align="end" className="w-[22rem] p-3">
            <SourceList docId={docId} onInsert={insertCite} />
          </PopoverContent>
        </Popover>
        <Popover>
          <PopoverTrigger asChild>
            <Button variant="outline" size="sm" className="h-8 rounded-full text-[0.78125rem]">
              <Database className="size-3.5" />
              Data
              {(doc.dataset_ids ?? []).length > 0 && (
                <span className="ml-0.5 rounded-full bg-moss-surface px-1.5 font-mono text-[0.59375rem] text-ivory">{doc.dataset_ids.length}</span>
              )}
            </Button>
          </PopoverTrigger>
          <PopoverContent
            data-writer-data-popover
            align="end"
            sideOffset={8}
            collisionPadding={16}
            aria-label="Workspace data"
            className="w-[calc(100vw-2rem)] max-w-[24rem] overflow-x-hidden overscroll-contain p-0 sm:w-[24rem]"
          >
            <DatasetList docId={docId} linked={doc.dataset_ids ?? []} />
          </PopoverContent>
        </Popover>
        <Popover>
          <PopoverTrigger asChild>
            <Button
              data-tour="writer-study-data"
              variant="outline"
              size="sm"
              className="h-8 rounded-full text-[0.78125rem]"
              title="Interview and survey evidence"
            >
              <ListTree className="size-3.5" />
              <span>Study data</span>
              {(interviewContexts?.linked_count ?? 0) +
                (surveyContexts?.linked_count ?? 0) >
              0 ? (
                <span className="ml-0.5 rounded-full bg-moss-surface px-1.5 font-mono text-[0.59375rem] text-ivory">
                  {(interviewContexts?.linked_count ?? 0) +
                    (surveyContexts?.linked_count ?? 0)}
                </span>
              ) : null}
            </Button>
          </PopoverTrigger>
          <PopoverContent align="end" className="w-[24rem] max-w-[calc(100vw-1rem)] p-3">
            <PrimaryResearchContext docId={docId} />
          </PopoverContent>
        </Popover>
        <Popover>
          <PopoverTrigger asChild>
            <Button
              variant="outline"
              size="sm"
              className="h-8 rounded-full text-[0.78125rem]"
            >
              <ImageIcon className="size-3.5" />
              Visuals
            </Button>
          </PopoverTrigger>
          <PopoverContent align="end" className="w-[20rem] p-3">
            <FigureList docId={docId} onInsert={insertFigure} />
          </PopoverContent>
        </Popover>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="outline"
              size="sm"
              className="h-8 rounded-full text-[0.78125rem]"
            >
              <ListPlus className="size-3.5" />
              Insert
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-[19rem]">
            <DropdownMenuLabel className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
              Paper-ready artifacts
            </DropdownMenuLabel>
            {linkedRunObjs.length === 0 ? (
              <p className="px-2 py-3 text-[0.75rem] text-muted-foreground">
                Link a finished search and its evidence table, methods
                paragraph and PRISMA flow become one-click inserts.
              </p>
            ) : (
              linkedRunObjs.map((run) => (
                <Fragment key={run.id}>
                  {linkedRunObjs.length > 1 ? (
                    <DropdownMenuLabel className="truncate text-[0.6875rem] font-normal text-muted-foreground">
                      {run.title ?? run.question}
                    </DropdownMenuLabel>
                  ) : null}
                  <DropdownMenuItem
                    onSelect={() => void insertArtifact("evidence", run.id)}
                  >
                    <Table2 className="size-4" /> Evidence table
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    onSelect={() => void insertArtifact("methods", run.id)}
                  >
                    <ScrollText className="size-4" /> Methods paragraph
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    onSelect={() => void insertArtifact("prisma", run.id)}
                  >
                    <Workflow className="size-4" /> PRISMA flow (TikZ)
                  </DropdownMenuItem>
                </Fragment>
              ))
            )}
          </DropdownMenuContent>
        </DropdownMenu>
        </div>
        <div
          className="hidden shrink-0 items-center gap-1.5 min-[2600px]:flex"
        >
          <Button
            variant="outline"
            size="sm"
            onClick={() => setReviewOpen(true)}
            className="h-8 rounded-full text-[0.78125rem]"
            title="Review manuscript"
          >
            <ShieldCheck className="size-3.5" />
            <span>Review</span>
            {(guardAudit?.severity_counts.error ?? 0) > 0 ? (
              <span className="grid size-4 place-items-center rounded-full bg-destructive font-mono text-[0.5625rem] text-white">
                {guardAudit?.severity_counts.error}
              </span>
            ) : null}
          </Button>
          <Popover>
            <PopoverTrigger asChild>
              <Button
                variant="outline"
                size="sm"
                aria-label="Version history"
                title="Version history"
                className="h-8 rounded-full px-2.5 text-[0.78125rem]"
              >
                <History className="size-3.5" />
                <span>Versions</span>
              </Button>
            </PopoverTrigger>
            <PopoverContent align="end" className="w-[22rem] p-3">
              <SnapshotList
                docId={docId}
                canRestore={canEdit}
                onRestored={(restoredContent) => {
                  setActiveFileId(0);
                  activeFileIdRef.current = 0;
                  setContent(restoredContent);
                  setSaveState("saved");
                  if (autoRef.current) compileNowRef.current();
                }}
              />
            </PopoverContent>
          </Popover>
        </div>
        <div className="hidden shrink-0 items-center gap-1.5 border-l border-border/70 pl-3 min-[2920px]:flex">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              data-tour="writer-export-tools"
              variant="outline"
              size="sm"
              className="h-8 rounded-full text-[0.78125rem]"
              title="Download and retarget manuscript"
            >
              <Download className="size-3.5" />
              <span>Download</span>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-[14rem]">
            <DropdownMenuItem
              disabled={!pdfUrl}
              onSelect={() => pdfUrl && saveUrl(`${fileSlug}.pdf`, pdfUrl)}
            >
              <FileText className="size-4" /> Compiled PDF
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={downloadSource}>
              <Code2 className="size-4" /> Current source file
            </DropdownMenuItem>
            <DropdownMenuItem
              onSelect={() =>
                void downloadWriterProject(docId).catch((error: unknown) =>
                  toast.error(error instanceof Error ? error.message : "Download failed."),
                )
              }
            >
              <FolderTree className="size-4" /> Complete LaTeX project (.zip)
            </DropdownMenuItem>
            <DropdownMenuItem
              onSelect={() =>
                void downloadWriterBib(docId).catch((error: unknown) =>
                  toast.error(
                    error instanceof Error
                      ? error.message
                      : "Download failed.",
                  ),
                )
              }
            >
              <BookMarked className="size-4" /> references.bib
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            {canEdit ? (
              <DropdownMenuItem onSelect={() => setRetargetOpen(true)}>
                <Repeat2 className="size-4" /> Retarget for another venue…
              </DropdownMenuItem>
            ) : null}
            {canEdit ? (
              <DropdownMenuItem
                onSelect={() => {
                  setTemplateName(title.trim() || "My template");
                  setTemplateOpen(true);
                }}
              >
                <Code2 className="size-4" /> Save as template…
              </DropdownMenuItem>
            ) : null}
            <DropdownMenuItem onSelect={() => setContribOpen(true)}>
              <FileClock className="size-4" /> AI contribution log
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        <DropdownMenu open={linkMenuOpen} onOpenChange={setLinkMenuOpen}>
          <DropdownMenuTrigger asChild>
            <Button
              data-tour="writer-search-links"
              variant="outline"
              size="sm"
              className="h-8 rounded-full text-[0.78125rem]"
              title={
                linkedRuns.length > 0
                  ? `${linkedRuns.length} linked search${linkedRuns.length === 1 ? "" : "es"}`
                  : "Link searches"
              }
            >
              <BookMarked className="size-3.5" />
              <span>
                {linkedRuns.length > 0
                  ? `${linkedRuns.length} search${linkedRuns.length === 1 ? "" : "es"}`
                  : "Searches"}
              </span>
              <ChevronDown className="size-3" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-[19rem]">
            <DropdownMenuLabel className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
              Feed the bibliography from
            </DropdownMenuLabel>
            {completedRuns.length === 0 ? (
              <p className="px-2 py-3 text-[0.75rem] text-muted-foreground">
                No completed searches yet. The Writer works without one; link
                later for citations and grounding.
              </p>
            ) : (
              completedRuns.slice(0, 12).map((run) => (
                <DropdownMenuCheckboxItem
                  key={run.id}
                  checked={linkedRuns.includes(run.id)}
                  onCheckedChange={(checked) => {
                    const next = checked
                      ? [...linkedRuns, run.id]
                      : linkedRuns.filter((id) => id !== run.id);
                    // optimistic: refetching before the PATCH lands used to
                    // revert the checkbox, which read as "click twice"
                    queryClient.setQueryData<WriterDocument>(
                      ["writer-doc", docId],
                      (old) => (old ? { ...old, run_ids: next } : old),
                    );
                    persist.mutate(
                      { run_ids: next },
                      {
                        onSettled: () => {
                          void queryClient.invalidateQueries({
                            queryKey: ["writer-doc", docId],
                          });
                          void queryClient.invalidateQueries({
                            queryKey: ["writer-citations", docId],
                          });
                        },
                      },
                    );
                  }}
                  onSelect={(event) => event.preventDefault()}
                >
                  <span className="truncate">{run.title ?? run.question}</span>
                </DropdownMenuCheckboxItem>
              ))
            )}
            <DropdownMenuSeparator />
            <DropdownMenuItem
              onSelect={() =>
                void downloadWriterBib(docId).catch((error: unknown) =>
                  toast.error(
                    error instanceof Error
                      ? error.message
                      : "Download failed.",
                  ),
                )
              }
            >
              <Download className="size-4" /> references.bib
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        </div>
        <Button
          type="button"
          data-tour="writer-tour-trigger"
          variant="ghost"
          size="sm"
          onClick={() =>
            window.dispatchEvent(new CustomEvent("six:start-manuscript-tour"))
          }
          className="hidden h-8 shrink-0 rounded-full border border-moss/20 bg-accent/35 px-2.5 text-[0.75rem] text-moss hover:bg-accent min-[3100px]:inline-flex"
          title="Open manuscript guide"
        >
          <CircleHelp className="size-3.5" />
          <span>Guide</span>
        </Button>
        </div>
        </div>
        <div
          data-tour="writer-collaboration"
          className="flex shrink-0 items-center gap-1"
        >
          <WriterCollaborationPopover
            docId={docId}
            collaborators={collaborators}
            presence={presence}
          />
          <WriterSharePopover
            docId={docId}
            comments={writerComments}
            onCreateComment={submitAuthorComment}
            commentSubmitting={createComment.isPending}
            onFocusComment={focusComment}
            onAskAi={askAiAboutComment}
            canManageShare={canManage}
            canUseAi={canEdit}
          />
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              data-tour="writer-toolbar-more"
              variant="outline"
              size="sm"
              className="h-8 shrink-0 rounded-full px-2.5 text-[0.78125rem] lg:px-3"
              aria-label="More manuscript actions"
              title="More manuscript actions"
            >
              <Ellipsis className="size-4" />
              <span className="hidden lg:inline">More</span>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-[17rem]">
            <DropdownMenuLabel className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
              Context and evidence
            </DropdownMenuLabel>
            <DropdownMenuItem
              disabled={!canEdit}
              onSelect={() => setToolbarPanel("cite")}
            >
              <Quote className="size-4" /> Cite included paper
            </DropdownMenuItem>
            <DropdownMenuItem
              disabled={!canEdit}
              onSelect={() => setToolbarPanel("sources")}
            >
              <BookMarked className="size-4" /> Sources
            </DropdownMenuItem>
            <DropdownMenuItem
              disabled={!canEdit}
              onSelect={() => setToolbarPanel("data")}
            >
              <Database className="size-4" /> Data
              {(doc.dataset_ids ?? []).length > 0 ? (
                <span className="ml-auto font-mono text-[0.625rem] text-muted-foreground">
                  {doc.dataset_ids.length}
                </span>
              ) : null}
            </DropdownMenuItem>
            <DropdownMenuItem
              disabled={!canEdit}
              onSelect={() => setToolbarPanel("study-data")}
            >
              <ListTree className="size-4" /> Study data
              {(interviewContexts?.linked_count ?? 0) +
                (surveyContexts?.linked_count ?? 0) >
              0 ? (
                <span className="ml-auto font-mono text-[0.625rem] text-muted-foreground">
                  {(interviewContexts?.linked_count ?? 0) +
                    (surveyContexts?.linked_count ?? 0)}
                </span>
              ) : null}
            </DropdownMenuItem>
            <DropdownMenuItem
              disabled={!canEdit}
              onSelect={() => setToolbarPanel("visuals")}
            >
              <ImageIcon className="size-4" /> Visuals
            </DropdownMenuItem>
            <DropdownMenuSub>
              <DropdownMenuSubTrigger disabled={!canEdit}>
                <ListPlus className="size-4" /> Insert artifact
              </DropdownMenuSubTrigger>
              <DropdownMenuSubContent className="w-[19rem]">
                <DropdownMenuLabel className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
                  Paper-ready artifacts
                </DropdownMenuLabel>
                {linkedRunObjs.length === 0 ? (
                  <DropdownMenuItem disabled>
                    Link a finished search first
                  </DropdownMenuItem>
                ) : (
                  linkedRunObjs.map((run) => (
                    <Fragment key={run.id}>
                      {linkedRunObjs.length > 1 ? (
                        <DropdownMenuLabel className="truncate text-[0.6875rem] font-normal text-muted-foreground">
                          {run.title ?? run.question}
                        </DropdownMenuLabel>
                      ) : null}
                      <DropdownMenuItem
                        onSelect={() => void insertArtifact("evidence", run.id)}
                      >
                        <Table2 className="size-4" /> Evidence table
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        onSelect={() => void insertArtifact("methods", run.id)}
                      >
                        <ScrollText className="size-4" /> Methods paragraph
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        onSelect={() => void insertArtifact("prisma", run.id)}
                      >
                        <Workflow className="size-4" /> PRISMA flow (TikZ)
                      </DropdownMenuItem>
                    </Fragment>
                  ))
                )}
              </DropdownMenuSubContent>
            </DropdownMenuSub>
            <DropdownMenuSeparator />
            <DropdownMenuLabel className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
              Review and history
            </DropdownMenuLabel>
            <DropdownMenuItem onSelect={() => setReviewOpen(true)}>
              <ShieldCheck className="size-4" /> Review manuscript
              {(guardAudit?.severity_counts.error ?? 0) > 0 ? (
                <span className="ml-auto grid size-4 place-items-center rounded-full bg-destructive font-mono text-[0.5625rem] text-white">
                  {guardAudit?.severity_counts.error}
                </span>
              ) : null}
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => setToolbarPanel("versions")}>
              <History className="size-4" /> Version history
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuSub>
              <DropdownMenuSubTrigger>
                <Download className="size-4" /> Download and transfer
              </DropdownMenuSubTrigger>
              <DropdownMenuSubContent className="w-[16rem]">
                <DropdownMenuItem
                  disabled={!pdfUrl}
                  onSelect={() => pdfUrl && saveUrl(`${fileSlug}.pdf`, pdfUrl)}
                >
                  <FileText className="size-4" /> Compiled PDF
                </DropdownMenuItem>
                <DropdownMenuItem onSelect={downloadSource}>
                  <Code2 className="size-4" /> Current source file
                </DropdownMenuItem>
                <DropdownMenuItem
                  onSelect={() =>
                    void downloadWriterProject(docId).catch((error: unknown) =>
                      toast.error(
                        error instanceof Error ? error.message : "Download failed.",
                      ),
                    )
                  }
                >
                  <FolderTree className="size-4" /> Complete project (.zip)
                </DropdownMenuItem>
                <DropdownMenuItem
                  onSelect={() =>
                    void downloadWriterBib(docId).catch((error: unknown) =>
                      toast.error(
                        error instanceof Error ? error.message : "Download failed.",
                      ),
                    )
                  }
                >
                  <BookMarked className="size-4" /> references.bib
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                {canEdit ? (
                  <DropdownMenuItem onSelect={() => setRetargetOpen(true)}>
                    <Repeat2 className="size-4" /> Retarget for another venue…
                  </DropdownMenuItem>
                ) : null}
                {canEdit ? (
                  <DropdownMenuItem
                    onSelect={() => {
                      setTemplateName(title.trim() || "My template");
                      setTemplateOpen(true);
                    }}
                  >
                    <Code2 className="size-4" /> Save as template…
                  </DropdownMenuItem>
                ) : null}
                <DropdownMenuItem onSelect={() => setContribOpen(true)}>
                  <FileClock className="size-4" /> AI contribution log
                </DropdownMenuItem>
              </DropdownMenuSubContent>
            </DropdownMenuSub>
            <DropdownMenuSub>
              <DropdownMenuSubTrigger>
                <BookMarked className="size-4" /> Linked searches
                {linkedRuns.length > 0 ? (
                  <span className="ml-auto mr-1 font-mono text-[0.625rem] text-muted-foreground">
                    {linkedRuns.length}
                  </span>
                ) : null}
              </DropdownMenuSubTrigger>
              <DropdownMenuSubContent className="w-[19rem]">
                <DropdownMenuLabel className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
                  Feed the bibliography from
                </DropdownMenuLabel>
                {completedRuns.length === 0 ? (
                  <DropdownMenuItem disabled>
                    No completed searches yet
                  </DropdownMenuItem>
                ) : (
                  completedRuns.slice(0, 12).map((run) => (
                    <DropdownMenuCheckboxItem
                      key={run.id}
                      checked={linkedRuns.includes(run.id)}
                      onCheckedChange={(checked) => {
                        const next = checked
                          ? [...linkedRuns, run.id]
                          : linkedRuns.filter((id) => id !== run.id);
                        queryClient.setQueryData<WriterDocument>(
                          ["writer-doc", docId],
                          (old) => (old ? { ...old, run_ids: next } : old),
                        );
                        persist.mutate(
                          { run_ids: next },
                          {
                            onSettled: () => {
                              void queryClient.invalidateQueries({
                                queryKey: ["writer-doc", docId],
                              });
                              void queryClient.invalidateQueries({
                                queryKey: ["writer-citations", docId],
                              });
                            },
                          },
                        );
                      }}
                      onSelect={(event) => event.preventDefault()}
                    >
                      <span className="truncate">{run.title ?? run.question}</span>
                    </DropdownMenuCheckboxItem>
                  ))
                )}
              </DropdownMenuSubContent>
            </DropdownMenuSub>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              onSelect={() =>
                window.dispatchEvent(
                  new CustomEvent("six:start-manuscript-tour"),
                )
              }
            >
              <CircleHelp className="size-4" /> Open manuscript guide
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        <div
          data-tour="writer-compile-controls"
          className="flex shrink-0 items-center gap-1 border-l border-border/70 pl-2 sm:gap-1.5 sm:pl-3"
        >
          <Button
            size="sm"
            onClick={() => compile.mutate()}
            disabled={!canEdit || compile.isPending || compiling}
            className="h-8 rounded-full px-2.5 text-[0.8125rem] sm:px-4"
            title={compiling ? "Compiling manuscript" : "Compile manuscript"}
            aria-label={compiling ? "Compiling manuscript" : "Compile manuscript"}
          >
            {compile.isPending || compiling ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <Play className="size-3.5" />
            )}
            <span className="hidden sm:inline">
              {compiling ? "Compiling…" : "Compile"}
            </span>
          </Button>
          <button
            type="button"
            disabled={!canEdit}
            aria-pressed={autoCompile}
            aria-label="Compile automatically once you stop writing"
            onClick={() => {
              const next = !autoCompile;
              setAutoCompile(next);
              localStorage.setItem("six:writer-autocompile", next ? "1" : "0");
              if (next) {
                toast.success(
                  "Auto compile on: rebuilds a few seconds after you stop writing.",
                );
                compileNowRef.current();
              } else if (compileIdleTimer.current) {
                clearTimeout(compileIdleTimer.current);
              }
            }}
            title="Compile automatically once you stop writing"
            className={cn(
              "inline-flex h-8 cursor-pointer items-center gap-1 rounded-full border px-2.5 font-mono text-[0.625rem] uppercase tracking-[0.18em] transition-colors md:px-3",
              !canEdit && "cursor-not-allowed opacity-45",
              autoCompile
                ? "border-moss/50 bg-accent text-moss"
                : "border-border text-muted-foreground hover:border-moss/40 hover:text-foreground",
            )}
          >
            <Zap className={cn("size-3", autoCompile && "fill-current")} />
            <span className="hidden md:inline">Auto</span>
          </button>
        </div>
        <div className="hidden w-[4.25rem] shrink-0 grid-cols-1 items-center md:grid 2xl:w-[9.75rem] 2xl:grid-cols-[minmax(0,1fr)_4.25rem] 2xl:gap-2">
          <span
            className="hidden min-w-0 items-center justify-end gap-1 whitespace-nowrap font-mono text-[0.625rem] tabular-nums tracking-[0.08em] text-muted-foreground 2xl:inline-flex"
            title={`Approximate prose count across ${wordCounts.fileCount || 1} TeX file${wordCounts.fileCount === 1 ? "" : "s"}. Current file: ${wordCounts.active.toLocaleString()} words.`}
            aria-label={`${wordCounts.total.toLocaleString()} words in manuscript`}
          >
            <Pilcrow className="size-3" />
            {wordCounts.total.toLocaleString()} words
          </span>
          <span className="block w-full text-right font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
            {!canEdit
              ? accessRole
              : saveState === "saved"
              ? "saved"
              : saveState === "saving"
                ? "saving…"
                : "editing…"}
          </span>
        </div>
      </header>

      <Dialog
        open={toolbarPanel !== null}
        onOpenChange={(open) => {
          if (!open) setToolbarPanel(null);
        }}
      >
        <DialogContent className="flex max-h-[min(44rem,calc(100dvh-1rem))] flex-col overflow-hidden sm:max-w-[min(32rem,calc(100vw-2rem))]">
          {toolbarPanel ? (
            <>
              <DialogHeader>
                <DialogTitle>
                  {WRITER_TOOLBAR_PANEL_COPY[toolbarPanel].title}
                </DialogTitle>
                <DialogDescription>
                  {WRITER_TOOLBAR_PANEL_COPY[toolbarPanel].description}
                </DialogDescription>
              </DialogHeader>
              <div
                data-toolbar-panel={toolbarPanel}
                className="min-h-0 flex-1 overflow-y-auto pr-1"
              >
                {toolbarPanel === "cite" ? (
                  <CiteList
                    docId={docId}
                    onInsert={(key) => {
                      insertCite(key);
                      setToolbarPanel(null);
                    }}
                  />
                ) : toolbarPanel === "sources" ? (
                  <SourceList
                    docId={docId}
                    onInsert={(key) => {
                      insertCite(key);
                      setToolbarPanel(null);
                    }}
                  />
                ) : toolbarPanel === "data" ? (
                  <DatasetList docId={docId} linked={doc.dataset_ids ?? []} />
                ) : toolbarPanel === "study-data" ? (
                  <PrimaryResearchContext docId={docId} />
                ) : toolbarPanel === "visuals" ? (
                  <FigureList
                    docId={docId}
                    onInsert={(filename) => {
                      insertFigure(filename);
                      setToolbarPanel(null);
                    }}
                  />
                ) : (
                  <SnapshotList
                    docId={docId}
                    canRestore={canEdit}
                    onRestored={(restoredContent) => {
                      setActiveFileId(0);
                      activeFileIdRef.current = 0;
                      setContent(restoredContent);
                      setSaveState("saved");
                      setToolbarPanel(null);
                      if (autoRef.current) compileNowRef.current();
                    }}
                  />
                )}
              </div>
            </>
          ) : null}
        </DialogContent>
      </Dialog>

      {!writerSplitLayout ? (
        <MobileWorkspaceSwitch
          value={mobilePane}
          onChange={setMobilePane}
          workspaceLabel="Manuscript"
        />
      ) : null}

      <div
        ref={writerLayoutRef}
        data-workspace-layout={writerSplitLayout ? "split" : "single"}
        className={cn(
          "grid min-h-0 min-w-0 flex-1 grid-cols-1 overflow-hidden [&>*]:min-w-0",
          writerSplitLayout
            && "grid-cols-[minmax(0,var(--writer-chat-width))_6px_minmax(0,1fr)]",
          layoutResizing && "select-none",
        )}
        style={
          {
            "--writer-chat-width": `${chatSplitPercent}%`,
          } as CSSProperties
        }
      >
        <aside
          data-tour="writer-chat"
          className={cn(
            "min-h-0 min-w-0 flex-col",
            writerSplitLayout
              ? "flex"
              : mobilePane === "agent" ? "flex" : "hidden",
          )}
        >
          {!canEdit ? (
            <WriterChatReadOnly role={accessRole} />
          ) : (
            <WriterChat
              docId={docId}
              activePath={activeProjectFile?.path ?? "main.tex"}
              runIds={linkedRuns}
              interviewCount={interviewContexts?.linked_count ?? 0}
              surveyCount={surveyContexts?.linked_count ?? 0}
              selection={selection}
              onClearSelection={() => setSelection(null)}
              editReviewsById={writerEditReviewsById}
              nextReviewId={nextPendingWriterEditReview?.id ?? null}
              onOpenReview={openEditReview}
              onApplyEdits={applyEdits}
              compileFailed={doc.compile_status === "error"}
              onBeforeSend={flushActiveSource}
              onInsertFigure={insertFigure}
              externalTask={commentAgentTask}
            />
          )}
        </aside>

        {writerSplitLayout ? (
          <div
            role="separator"
            aria-label="Resize writing assistant and manuscript workspace"
            aria-orientation="vertical"
            aria-valuemin={Math.round(constrainChatSplit(0, writerLayoutWidth))}
            aria-valuemax={Math.round(constrainChatSplit(100, writerLayoutWidth))}
            aria-valuenow={Math.round(chatSplitPercent)}
            tabIndex={0}
            onPointerDown={beginLayoutResize}
            onKeyDown={adjustChatSplitFromKeyboard}
            onDoubleClick={() => commitChatSplit(WRITER_DEFAULT_CHAT_PERCENT)}
            className={cn(
              "group relative min-h-0 cursor-col-resize touch-none outline-none",
              "after:absolute after:inset-y-0 after:left-1/2 after:w-px after:-translate-x-1/2 after:bg-border",
              "focus-visible:after:w-0.5 focus-visible:after:bg-moss hover:after:w-0.5 hover:after:bg-moss/65",
              layoutResizing && "after:w-0.5 after:bg-moss",
            )}
            title="Drag to resize. Double-click to reset."
          >
            <span
              className={cn(
                "absolute left-1/2 top-1/2 z-10 grid h-12 w-4 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full border border-border bg-card text-muted-foreground shadow-sm transition-colors",
                "group-hover:border-moss/40 group-hover:text-moss group-focus-visible:border-moss group-focus-visible:text-moss",
                layoutResizing && "border-moss/50 text-moss",
              )}
            >
              <GripVertical className="size-3" />
            </span>
          </div>
        ) : null}

        <section
          className={cn(
            "min-h-0 min-w-0 flex-col",
            writerSplitLayout
              ? "flex"
              : mobilePane === "workspace" ? "flex" : "hidden",
          )}
        >
          <div className="flex min-w-0 shrink-0 items-center gap-1 overflow-x-auto border-b border-border/60 px-3 py-2 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
            <div
              data-tour="writer-views"
              className="inline-flex items-center gap-1 rounded-full bg-secondary/65 p-1"
            >
              <button
                type="button"
                onClick={() => setView("source")}
                className={cn(
                  "inline-flex h-8 cursor-pointer items-center gap-1.5 rounded-full px-3 text-[0.71875rem] font-medium transition-colors",
                  view === "source"
                    ? "bg-card text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                <Code2 className="size-3.5" />
                Source
              </button>
              <button
                type="button"
                onClick={() => setView("preview")}
                className={cn(
                  "inline-flex h-8 cursor-pointer items-center gap-1.5 rounded-full px-3 text-[0.71875rem] font-medium transition-colors",
                  view === "preview"
                    ? "bg-card text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                <FileText className="size-3.5" />
                Preview
              </button>
              <button
                type="button"
                onClick={() => setView("log")}
                className={cn(
                  "inline-flex h-8 cursor-pointer items-center gap-1.5 rounded-full px-3 text-[0.71875rem] font-medium transition-colors",
                  view === "log"
                    ? "bg-card text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                <Terminal className="size-3.5" />
                Log
                {compileErrors.length > 0 ? (
                  <span
                    className="size-1.5 rounded-full bg-destructive"
                    aria-label={`${compileErrors.length} compile errors`}
                  />
                ) : null}
              </button>
            </div>
            {view === "source" ? (
              <Button
                type="button"
                variant="ghost"
                size="icon"
                onClick={() =>
                  setProjectRailOpen((open) => {
                    const next = !open;
                    localStorage.setItem(
                      "six:writer-project-rail",
                      next ? "open" : "closed",
                    );
                    return next;
                  })
                }
                className="hidden size-8 shrink-0 rounded-full text-muted-foreground hover:text-foreground 2xl:inline-flex"
                aria-label={
                  projectRailOpen
                    ? "Hide project files and outline"
                    : "Show project files and outline"
                }
                aria-controls="writer-project-rail"
                aria-expanded={projectRailOpen}
                title={
                  projectRailOpen
                    ? "Hide project files and outline"
                    : "Show project files and outline"
                }
              >
                {projectRailOpen ? (
                  <PanelLeftClose className="size-3.5" />
                ) : (
                  <PanelLeftOpen className="size-3.5" />
                )}
              </Button>
            ) : null}
            {view === "source" ? (
              <Popover>
                <PopoverTrigger asChild>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 max-w-44 rounded-full text-[0.71875rem] 2xl:hidden"
                  >
                    <FolderTree className="size-3.5" />
                    <span className="truncate">
                      {activeProjectFile?.path.split("/").pop() ?? "main.tex"}
                    </span>
                    <ChevronDown className="size-3" />
                  </Button>
                </PopoverTrigger>
                <PopoverContent align="start" className="w-72 p-2">
                  <p className="px-2 py-1 font-mono text-[0.59375rem] uppercase tracking-[0.18em] text-muted-foreground">
                    Project files
                  </p>
                  <nav className="mt-1 max-h-64 space-y-0.5 overflow-y-auto">
                    {(projectFiles ?? []).map((file) => (
                      <div
                        key={file.id}
                        className="group/file flex items-center gap-1"
                      >
                        <button
                          type="button"
                          onClick={() => openProjectFile(file)}
                          className={cn(
                            "flex min-w-0 flex-1 cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 text-left font-mono text-[0.71875rem]",
                            file.id === activeFileId
                              ? "bg-accent text-moss"
                              : "text-muted-foreground hover:bg-secondary hover:text-foreground",
                          )}
                        >
                          <FileText className="size-3.5 shrink-0" />
                          <span className="truncate">{file.path}</span>
                        </button>
                        {!file.main && canEdit ? (
                          <button
                            type="button"
                            onClick={() => beginFileRename(file)}
                            className="grid size-7 shrink-0 cursor-pointer place-items-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground"
                            aria-label={`Rename ${file.path}`}
                          >
                            <Pencil className="size-3" />
                          </button>
                        ) : null}
                      </div>
                    ))}
                  </nav>
                  {canEdit ? (
                    <div className="mt-2 flex gap-1.5 border-t border-border pt-2">
                      <Input
                        value={newFileName}
                        onChange={(event) => setNewFileName(event.target.value)}
                        placeholder="sections/method.tex"
                        className="h-8 font-mono text-[0.6875rem]"
                      />
                      <Button
                        size="icon"
                        className="size-8 shrink-0 rounded-lg"
                        disabled={!newFileName.trim() || createProjectFile.isPending}
                        onClick={createExtraFile}
                        aria-label="Create project file"
                      >
                        <FilePlus2 className="size-3.5" />
                      </Button>
                    </div>
                  ) : null}
                </PopoverContent>
              </Popover>
            ) : null}
            {view === "source" && doc.compile_status === "ok" ? (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => void locateSourceInPdf()}
                className="ml-auto h-7 rounded-full text-[0.71875rem]"
                title="Locate the current source line in the compiled PDF"
              >
                <Crosshair className="size-3.5" /> PDF
              </Button>
            ) : null}
            {view === "source" && outline.length > 0 ? (
              <Popover>
                <PopoverTrigger asChild>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 shrink-0 rounded-full text-[0.71875rem] 2xl:hidden"
                  >
                    <ListTree className="size-3.5" /> Outline · {outline.length}
                  </Button>
                </PopoverTrigger>
                <PopoverContent align="end" className="w-64 p-2">
                  <p className="px-2 py-1 font-mono text-[0.59375rem] uppercase tracking-[0.18em] text-muted-foreground">
                    Manuscript outline
                  </p>
                  <nav className="mt-1 max-h-72 space-y-0.5 overflow-y-auto">
                    {outline.map((entry) => (
                      <button
                        key={`${entry.line}-${entry.title}-mobile`}
                        type="button"
                        onClick={() => jumpToLine(entry.line)}
                        style={{ paddingLeft: `${0.5 + entry.depth * 0.65}rem` }}
                        className="block w-full cursor-pointer truncate rounded-lg py-1.5 pr-2 text-left text-[0.75rem] text-muted-foreground hover:bg-accent hover:text-foreground"
                      >
                        {entry.title}
                      </button>
                    ))}
                  </nav>
                </PopoverContent>
              </Popover>
            ) : null}
          </div>

          <div
            data-tour="writer-source"
            className={cn("min-h-0 flex-1", view !== "source" && "hidden")}
          >
            <div className="flex h-full min-h-0">
              <aside
                id="writer-project-rail"
                aria-hidden={!projectRailOpen}
                className={cn(
                  "hidden shrink-0 overflow-hidden border-r border-border/60 bg-secondary/20 transition-[width,opacity,border-color] duration-200 ease-out 2xl:flex 2xl:flex-col",
                  projectRailOpen
                    ? "2xl:w-[208px] 2xl:opacity-100"
                    : "pointer-events-none 2xl:w-0 2xl:border-r-transparent 2xl:opacity-0",
                )}
              >
                <div className="w-[208px] flex-1 overflow-y-auto p-2">
                  <div className="mb-2 border-b border-border/60 pb-2">
                    <p className="flex items-center justify-between gap-1.5 px-2 py-1.5 font-mono text-[0.59375rem] uppercase tracking-[0.18em] text-muted-foreground">
                      <span className="flex items-center gap-1.5">
                        <FolderTree className="size-3" /> Project
                      </span>
                      <span>{projectFiles?.length ?? 1}</span>
                    </p>
                    <nav className="mt-0.5 space-y-0.5">
                      {(projectFiles ?? []).map((file) => (
                        <div
                          key={file.id}
                          className="group/file flex items-center gap-1"
                        >
                          <button
                            type="button"
                            onClick={() => openProjectFile(file)}
                            title={file.path}
                            className={cn(
                              "flex min-w-0 flex-1 cursor-pointer items-center gap-1.5 rounded-lg px-2 py-1.5 text-left font-mono text-[0.6875rem] transition-colors",
                              file.id === activeFileId
                                ? "bg-accent text-moss"
                                : "text-muted-foreground hover:bg-card hover:text-foreground",
                            )}
                          >
                            <FileText className="size-3 shrink-0" />
                            <span className="truncate">{file.path}</span>
                          </button>
                          {!file.main && canEdit ? (
                            <button
                              type="button"
                              onClick={() => beginFileRename(file)}
                              className="grid size-6 shrink-0 cursor-pointer place-items-center rounded-md text-muted-foreground opacity-100 hover:bg-accent hover:text-foreground sm:opacity-0 sm:group-hover/file:opacity-100"
                              aria-label={`Rename ${file.path}`}
                            >
                              <Pencil className="size-3" />
                            </button>
                          ) : null}
                          {!file.main && canEdit ? (
                            <button
                              type="button"
                              onClick={() => setDeleteFileTarget(file)}
                              disabled={deleteProjectFile.isPending}
                              className="grid size-6 shrink-0 cursor-pointer place-items-center rounded-md text-muted-foreground opacity-100 hover:bg-destructive/10 hover:text-destructive sm:opacity-0 sm:group-hover/file:opacity-100"
                              aria-label={`Delete ${file.path}`}
                            >
                              <Trash2 className="size-3" />
                            </button>
                          ) : null}
                        </div>
                      ))}
                    </nav>
                    {canEdit ? (
                      <div className="mt-2 flex gap-1.5 px-1">
                        <Input
                          value={newFileName}
                          onChange={(event) => setNewFileName(event.target.value)}
                          onKeyDown={(event) => {
                            if (event.key === "Enter" && newFileName.trim()) {
                              createExtraFile();
                            }
                          }}
                          placeholder="sections/method.tex"
                          className="h-7 min-w-0 font-mono text-[0.625rem]"
                        />
                        <Button
                          size="icon"
                          variant="outline"
                          className="size-7 shrink-0 rounded-lg"
                          disabled={
                            !newFileName.trim() || createProjectFile.isPending
                          }
                          onClick={createExtraFile}
                          aria-label="Create project file"
                        >
                          <FilePlus2 className="size-3" />
                        </Button>
                      </div>
                    ) : null}
                  </div>
                  <p className="flex items-center gap-1.5 px-2 py-1.5 font-mono text-[0.59375rem] uppercase tracking-[0.18em] text-muted-foreground">
                    <ListTree className="size-3" /> Outline
                  </p>
                  {outline.length === 0 ? (
                    <p className="px-2 py-3 text-[0.6875rem] leading-relaxed text-muted-foreground">
                      Sections appear here as the manuscript grows.
                    </p>
                  ) : (
                    <nav className="mt-1 space-y-0.5">
                      {outline.map((entry) => (
                        <button
                          key={`${entry.line}-${entry.title}`}
                          type="button"
                          onClick={() => jumpToLine(entry.line)}
                          style={{
                            paddingLeft: `${0.5 + entry.depth * 0.65}rem`,
                          }}
                          className="block w-full cursor-pointer truncate rounded-lg py-1.5 pr-2 text-left text-[0.71875rem] text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                          title={entry.title}
                        >
                          {entry.title}
                        </button>
                      ))}
                    </nav>
                  )}
                </div>
              </aside>
              <ConfirmDeleteDialog
                target={
                  deleteFileTarget
                    ? {
                        title: "Delete this project file?",
                        description: `“${deleteFileTarget.path}” will be permanently removed from the manuscript project. References to it may stop the manuscript from compiling.`,
                        action: "Delete file",
                        cancel: "Keep file",
                      }
                    : null
                }
                pending={deleteProjectFile.isPending}
                onCancel={() => {
                  if (!deleteProjectFile.isPending && !deleteFileInFlightRef.current) {
                    setDeleteFileTarget(null);
                  }
                }}
                onConfirm={() => {
                  if (
                    !deleteFileTarget
                    || deleteProjectFile.isPending
                    || deleteFileInFlightRef.current
                  ) return;
                  const current = projectFiles?.find(
                    (file) => file.id === deleteFileTarget.id,
                  );
                  if (!current || current.main || current.path !== deleteFileTarget.path) {
                    setDeleteFileTarget(null);
                    toast.error("The project file changed. Nothing was deleted.");
                    return;
                  }
                  deleteFileInFlightRef.current = true;
                  deleteProjectFile.mutate(current.id);
                }}
              />
              <div className="flex min-h-0 min-w-0 flex-1 flex-col">
                {canEdit
                && nextPendingWriterEditReview
                && (pendingReviewTargetMissing
                  || nextPendingWriterEditReview.edit.path
                    !== (activeProjectFile?.path ?? "main.tex")) ? (
                  <div className="flex items-center justify-between gap-3 border-b border-border bg-card px-3 py-2">
                    <div className="min-w-0">
                      <p className="text-[0.75rem] font-medium text-foreground">
                        {pendingReviewTargetMissing
                          ? "The proposed source file no longer exists"
                          : "A source change is ready for review"}
                      </p>
                      <p className="truncate font-mono text-[0.59375rem] uppercase tracking-[0.13em] text-muted-foreground">
                        {nextPendingWriterEditReview.edit.path}
                      </p>
                    </div>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      className="h-7 rounded-full px-3 text-[0.6875rem]"
                      disabled={reviewBusyId === nextPendingWriterEditReview.id}
                      onClick={() => {
                        if (pendingReviewTargetMissing) {
                          void rejectEditReview(nextPendingWriterEditReview);
                        } else {
                          openEditReview(nextPendingWriterEditReview);
                        }
                      }}
                    >
                      {pendingReviewTargetMissing
                        ? "Reject stale proposal"
                        : "Open proposal"}
                    </Button>
                  </div>
                ) : null}
                <CodeEditor
                  ref={editorRef}
                  value={content}
                  onChange={scheduleSave}
                  readOnly={!canEdit || Boolean(reviewBusyId)}
                  onCursor={setCursor}
                  citations={editorCitations ?? []}
                  remoteCursors={presence
                    .filter(
                      (person) =>
                        !person.self &&
                        person.mode === "source" &&
                        person.path === (activeProjectFile?.path ?? "main.tex"),
                    )
                    .map((person) => ({
                      userId: person.user_id,
                      name: person.name,
                      email: person.email,
                      line: person.line,
                    }))}
                  review={
                    canEdit
                    && nextPendingWriterEditReview?.edit.path
                      === (activeProjectFile?.path ?? "main.tex")
                      ? nextPendingWriterEditReview
                      : null
                  }
                  reviewPendingCount={pendingWriterEditReviews.length}
                  reviewBusy={reviewBusyId === nextPendingWriterEditReview?.id}
                  onApproveReview={approveEditReview}
                  onRejectReview={rejectEditReview}
                  onDiscuss={(picked) => {
                    const built = buildWriterSourceSelection({
                      ...picked,
                      path: activeProjectFile?.path ?? "main.tex",
                    });
                    if (!built.selection) {
                      toast.error(built.error);
                      return;
                    }
                    setSelection(built.selection);
                    toast.success("Code attached to the chat.");
                  }}
                />
              </div>
            </div>
          </div>
          <div
            data-tour="writer-preview-workspace"
            className={cn(
              "min-h-0 flex-1 bg-secondary/30",
              view !== "preview" && "hidden",
            )}
          >
            {compiling ? (
              <div className="flex h-full flex-col items-center justify-center gap-3 px-8 text-center">
                <Loader2 className="size-5 animate-spin text-moss" />
                <p className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
                  compiling
                </p>
                <p className="max-w-xs text-[0.78125rem] leading-relaxed text-muted-foreground">
                  The first compile downloads LaTeX packages; that can take a
                  minute or two. After that it is seconds.
                </p>
              </div>
            ) : pdfUrl ? (
              <WriterPdfPreview
                url={pdfUrl}
                focus={pdfFocus}
                onNavigateSource={(position) => {
                  void api
                    .writerSyncPdf(docId, position.page, position.x, position.y)
                    .then((source) => navigateToSource(source.path, source.line))
                    .catch((error: unknown) =>
                      toast.error(
                        error instanceof Error
                          ? error.message
                          : "No source position was found there.",
                      ),
                    );
                }}
                onDiscuss={({ x, y, ...picked }) => {
                  setSelection({
                    kind: "pdf",
                    ...picked,
                    ...(typeof x === "number" && typeof y === "number"
                      ? { pdf_x: x, pdf_y: y }
                      : {}),
                  });
                  toast.success("Passage and its PDF position attached to the chat.");
                }}
                comments={writerComments}
                commentsVisible={pdfCommentsVisible}
                onCommentsVisibleChange={setPdfCommentsVisible}
                onComment={submitAuthorComment}
                commentSubmitting={createComment.isPending}
                commentFocusPage={commentFocusPage}
                commentRevision={doc.updated_at}
              />
            ) : (
              <div className="flex h-full flex-col items-center justify-center gap-3 px-8 text-center">
                <span className="grid size-11 place-items-center rounded-full bg-accent">
                  <Play className="size-4 text-moss" />
                </span>
                <p className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
                  Compile to preview
                </p>
                <p className="max-w-xs text-[0.78125rem] leading-relaxed text-muted-foreground">
                  Your PDF renders here, right next to the source. Mark any
                  passage in it to discuss that exact spot.
                </p>
              </div>
            )}
          </div>
          <div
            data-tour="writer-log-workspace"
            className={cn(
              "min-h-0 flex-1 overflow-y-auto p-4",
              view !== "log" && "hidden",
            )}
          >
            {compileErrors.length > 0 ? (
              <div className="mb-3">
                <p className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-destructive">
                  {compileErrors.length} error
                  {compileErrors.length === 1 ? "" : "s"}
                </p>
                <ul className="mt-2 space-y-1.5">
                  {compileErrors.map((error, index) => (
                    <li key={index}>
                      <button
                        type="button"
                        onClick={() =>
                          error.path && error.line
                            ? navigateToSource(error.path, error.line)
                            : jumpToLine(error.line)
                        }
                        className="flex w-full cursor-pointer items-start gap-2 rounded-xl border border-destructive/20 bg-destructive/5 px-3 py-2 text-left text-[0.75rem] leading-relaxed text-destructive hover:border-destructive/40"
                      >
                        <FileWarning className="mt-0.5 size-3 shrink-0" />
                        <span>
                          {error.path ? `${error.path}` : ""}
                          {error.line ? `:${error.line}: ` : error.path ? ": " : ""}
                          {error.message}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
            <p className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
              Engine output
            </p>
            {doc.compile_log ? (
              <pre className="mt-2 whitespace-pre-wrap break-words rounded-xl border border-border bg-secondary/40 p-3 font-mono text-[0.6875rem] leading-relaxed">
                {doc.compile_log}
              </pre>
            ) : (
              <p className="mt-2 rounded-xl border border-dashed border-border px-4 py-8 text-center text-[0.78125rem] text-muted-foreground">
                No compile log yet. Hit Compile and the engine output lands
                here.
              </p>
            )}
          </div>
        </section>
      </div>

      <ContributionDialog
        docId={docId}
        open={contribOpen}
        onOpenChange={setContribOpen}
        onInsert={insertSnippet}
      />

      <WriterReviewDialog
        docId={docId}
        open={reviewOpen}
        onOpenChange={setReviewOpen}
        canEdit={canEdit}
        onNavigate={navigateToSource}
        onInsert={insertSnippet}
        onInsertCitation={insertCitationAtFinding}
      />

      <Dialog
        open={renameFile !== null}
        onOpenChange={(open) => {
          if (!open) {
            setRenameFile(null);
            setRenameFilePath("");
          }
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Pencil className="size-4 text-moss" />
              Rename project file
            </DialogTitle>
            <DialogDescription>
              Keep the extension and any folder path the manuscript expects,
              for example sections/methods.tex.
            </DialogDescription>
          </DialogHeader>
          <form
            method="post"
            className="space-y-3"
            onSubmit={(event) => {
              event.preventDefault();
              if (!renameFile || !renameFilePath.trim()) return;
              renameProjectFile.mutate({
                fileId: renameFile.id,
                path: renameFilePath.trim(),
              });
            }}
          >
            <Input
              autoFocus
              value={renameFilePath}
              onChange={(event) => setRenameFilePath(event.target.value)}
              aria-label="Project file path"
              className="h-9 rounded-lg font-mono text-[0.75rem]"
            />
            <div className="flex justify-end gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="rounded-full"
                onClick={() => {
                  setRenameFile(null);
                  setRenameFilePath("");
                }}
              >
                Cancel
              </Button>
              <Button
                type="submit"
                size="sm"
                className="rounded-full"
                disabled={
                  !renameFilePath.trim()
                  || renameFilePath.trim() === renameFile?.path
                  || renameProjectFile.isPending
                }
              >
                {renameProjectFile.isPending ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  "Rename"
                )}
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog open={templateOpen} onOpenChange={setTemplateOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Code2 className="size-4 text-moss" />
              Save as template
            </DialogTitle>
            <DialogDescription>
              The current LaTeX source becomes a template your whole
              workspace can start new documents from.
            </DialogDescription>
          </DialogHeader>
          <div className="flex items-center gap-2">
            <Input
              autoFocus
              value={templateName}
              onChange={(event) => setTemplateName(event.target.value)}
              placeholder="Template name"
              className="h-9 rounded-lg text-[0.8125rem]"
              onKeyDown={(event) => {
                if (event.key === "Enter" && templateName.trim()) {
                  saveTemplate.mutate();
                }
              }}
            />
            <Button
              size="sm"
              disabled={!templateName.trim() || saveTemplate.isPending}
              onClick={() => saveTemplate.mutate()}
              className="h-9 shrink-0 rounded-full px-4 text-[0.78125rem]"
            >
              {saveTemplate.isPending ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                "Save"
              )}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      <WriterRetargetDialog
        doc={doc}
        open={retargetOpen}
        onOpenChange={setRetargetOpen}
      />

      <Dialog
        open={sourceConflict !== null}
        onOpenChange={(open) => {
          if (!open) setSourceConflict(null);
        }}
      >
        <DialogContent className="max-h-[92vh] max-w-5xl overflow-y-auto rounded-[1.75rem]">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 font-display text-2xl font-normal">
              <AlertTriangle className="size-5 text-amber-600" />
              Both authors changed the same passage
            </DialogTitle>
            <DialogDescription>
              Nothing was overwritten. Compare the latest saved source with your draft,
              then choose the text that should become the next revision.
            </DialogDescription>
          </DialogHeader>
          {sourceConflict ? (
            <>
              <div className="grid gap-3 md:grid-cols-2">
                <label className="block">
                  <span className="mb-1.5 block font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
                    Latest saved · {sourceConflict.path}
                  </span>
                  <Textarea
                    readOnly
                    value={sourceConflict.remote}
                    className="min-h-[20rem] resize-y rounded-xl bg-secondary/35 font-mono text-[0.6875rem] leading-relaxed"
                  />
                </label>
                <label className="block">
                  <span className="mb-1.5 block font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
                    Reviewed next revision
                  </span>
                  <Textarea
                    value={sourceConflict.reviewed}
                    onChange={(event) =>
                      setSourceConflict((current) =>
                        current
                          ? { ...current, reviewed: event.target.value }
                          : current,
                      )
                    }
                    className="min-h-[20rem] resize-y rounded-xl font-mono text-[0.6875rem] leading-relaxed"
                  />
                </label>
              </div>
              <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
                <Button
                  type="button"
                  variant="outline"
                  className="rounded-full"
                  onClick={() => {
                    revisionRef.current.set(
                      sourceConflict.fileId,
                      sourceConflict.revision,
                    );
                    baseContentRef.current.set(
                      sourceConflict.fileId,
                      sourceConflict.remote,
                    );
                    if (activeFileIdRef.current === sourceConflict.fileId) {
                      setContent(sourceConflict.remote);
                    }
                    setSaveState("saved");
                    setSourceConflict(null);
                    void queryClient.invalidateQueries({
                      queryKey: ["writer-files", docId],
                    });
                  }}
                >
                  Use latest saved
                </Button>
                <Button
                  type="button"
                  className="rounded-full"
                  disabled={persistSource.isPending}
                  onClick={() => {
                    const conflict = sourceConflict;
                    setSourceConflict(null);
                    setSaveState("saving");
                    persistSource.mutate({
                      fileId: conflict.fileId,
                      source: conflict.reviewed,
                      expectedRevision: conflict.revision,
                      baseContent: conflict.remote,
                    });
                  }}
                >
                  Save reviewed revision
                </Button>
              </div>
            </>
          ) : null}
        </DialogContent>
      </Dialog>

    </div>
  );
}
