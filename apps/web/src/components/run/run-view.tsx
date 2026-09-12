"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  ArrowRight,
  CircleAlert,
  FileUp,
  GitCompareArrows,
  LayoutGrid,
  Loader2,
  MessageSquareText,
  Network,
  PauseCircle,
  Radio,
  RotateCcw,
  Table2,
} from "lucide-react";
import { toast } from "sonner";

import {
  ChatComposer,
  ChatThread,
  type AskActivityPlan,
  useDocumentUpload,
  useRunChat,
} from "@/components/run/chat-panel";
import type { PaperPanelState } from "@/components/run/paper-panel";
import ResearchArtifactWorkspace, {
  claimArtifactFromPayload,
  isSubstantialTable,
  sourceArtifactFromPayload,
  tableArtifactFromPayload,
  type ResearchArtifact,
} from "@/components/run/research-artifact-workspace";
import {
  AgentLoadingOrb,
  AgentResponseAvatar,
} from "@/components/agent-work-status";
import { dropOverlayClass, usePdfDrop } from "@/hooks/use-pdf-drop";
import { DetailError } from "@/components/detail-error";
import ProtocolCard from "@/components/run/protocol-card";
import RefineDiff from "@/components/run/refine-diff";
import ReportButton from "@/components/run/report-button";
import ResultsPanel from "@/components/run/results-panel";
import RunControls from "@/components/run/run-controls";
import ChatReportDialog from "@/components/run/chat-report-dialog";
import ClaimEvidenceGraph from "@/components/run/claim-evidence-graph";
import ExtractionStudio from "@/components/run/extraction-studio";
import LivingResearchPanel from "@/components/run/living-research-panel";
import ResearchControlRoom from "@/components/run/research-control-room";
import RunShareDialog from "@/components/run/share-dialog";
import StageTimeline from "@/components/run/stage-timeline";
import { activeStage, STAGE_META } from "@/components/run/stage-meta";
import { useSidebarUi } from "@/components/shell/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { useQueue, useRun, useRunEvents } from "@/hooks/queries";
import { useRunStream } from "@/hooks/use-run-stream";
import { api, ApiError, retryTransientApiQuery, type RunRef } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatNumber } from "@/lib/format";
import { useActiveProject } from "@/lib/project-context";
import { isMoving, isTerminal, STATUS_META } from "@/lib/status";
import {
  userFacingErrorMessage,
  userFacingStoredErrorMessage,
} from "@/lib/user-facing-error";
import type {
  ChatMessage,
  RunConfig,
  RunDetail,
  RunEvent,
  ToolStepPayload,
} from "@/lib/types";
import { cn } from "@/lib/utils";

function configChips(config: Partial<RunConfig>, german = false): string[] {
  const chips: string[] = [];
  if (config.mode === "ask") return ["Quick answer"];
  if (config.screen) chips.push("Screen");
  if (config.screen && config.review_method && config.review_method !== "prisma") {
    const methodLabel = {
      cochrane: "Cochrane",
      jbi: "JBI",
      campbell: "Campbell",
      kitchenham: "Kitchenham",
      prisma: "PRISMA 2020",
    }[config.review_method];
    chips.push(methodLabel);
  }
  if (config.live) chips.push("Live index");
  if (config.acquire) chips.push("Full texts");
  if (config.full_text) chips.push("Deep screen");
  if (config.web_search) chips.push("Web sources");
  if (config.gate_protocol) chips.push("Protocol gate");
  const paperLimit = config.paper_limit || config.screen_limit;
  if (paperLimit) chips.push(german ? `Bis zu ${paperLimit} finale Paper` : `Up to ${paperLimit} final papers`);
  if (config.peer_reviewed_only) chips.push("Peer-reviewed only");
  if (config.year_from || config.year_to)
    chips.push(`${config.year_from ?? "…"} to ${config.year_to ?? "…"}`);
  if (config.exhaustive === false) chips.push(german ? "Ohne Sucherweiterung" : "No query expansion");
  if (config.query) chips.push("Custom query");
  if (config.living) chips.push("Living review");
  return chips;
}

function artifactsFromMessages(messages: ChatMessage[]): ResearchArtifact[] {
  const artifacts: ResearchArtifact[] = [];
  for (const [index, message] of messages.entries()) {
    if (message.role !== "tool") continue;
    const payload = message.payload as ToolStepPayload | null;
    if (!payload || payload.status === "running") continue;
    if (payload.kind === "paper" && payload.document_id) {
      const title = payload.title?.trim() || "Paper";
      const artifact: ResearchArtifact = {
        id: `paper:${payload.document_id}`,
        kind: "paper",
        title,
        paper: {
          documentId: payload.document_id,
          title,
          highlights: payload.highlights ?? [],
          legalBasis: payload.legal_basis,
          license: payload.license,
        },
      };
      const existing = artifacts.findIndex((entry) => entry.id === artifact.id);
      if (existing >= 0) artifacts[existing] = artifact;
      else artifacts.push(artifact);
      continue;
    }
    const table = tableArtifactFromPayload(payload);
    if (table) {
      artifacts.push({
        id: `table:${message.id ?? `${payload.resource?.uri ?? payload.query}:${index}`}`,
        kind: "table",
        title: table.title,
        table: { ...table, messageId: message.id },
      });
      continue;
    }
    const source = sourceArtifactFromPayload(payload);
    if (source) {
      const artifact: ResearchArtifact = {
        id: `source:${source.url}`,
        kind: "source",
        title: source.title,
        source,
      };
      const existing = artifacts.findIndex((entry) => entry.id === artifact.id);
      if (existing >= 0) artifacts[existing] = artifact;
      else artifacts.push(artifact);
      continue;
    }
    const claim = claimArtifactFromPayload(payload);
    if (claim) {
      const artifact: ResearchArtifact = {
        id: `claim:${claim.claim.toLocaleLowerCase()}`,
        kind: "claim",
        title: "Claim audit",
        claim,
      };
      const existing = artifacts.findIndex((entry) => entry.id === artifact.id);
      if (existing >= 0) artifacts[existing] = artifact;
      else artifacts.push(artifact);
    }
  }
  return artifacts;
}

function AssistantRow({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex gap-3">
      <AgentResponseAvatar className="mt-0.5" />
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

/** While the run works, the chat animation takes the avatar's place. */
function WorkingBubble({
  run,
  events,
}: {
  run: RunDetail;
  events: RunEvent[];
}) {
  const stage = activeStage(events);
  const label = stage ? STAGE_META[stage].label.toLowerCase() : "starting";
  return (
    <div className="flex items-center gap-2.5">
      <AgentLoadingOrb />
      <span className="shimmer-text text-[0.9375rem] font-medium">
        {run.status === "pending"
          ? "Getting the search ready…"
          : `Searching the literature: ${label}…`}
      </span>
    </div>
  );
}

function CompletionCard({
  run,
  queueCount,
  onShowResults,
  onRefine,
}: {
  run: RunDetail;
  queueCount: number;
  onShowResults: () => void;
  onRefine: () => void;
}) {
  const prisma = run.prisma;
  if (!prisma) return null;
  const included =
    prisma.studies_included > 0 ? prisma.studies_included : prisma.included;
  const unique = prisma.records_identified - prisma.duplicates_removed;

  return (
    <AssistantRow>
      <div className="rounded-2xl border border-moss/25 bg-accent/40 px-5 py-4">
        <p className="text-[0.90625rem] leading-relaxed">
          {run.status === "completed" && "The search is complete. "}
          {run.status === "cancelled" && "The search was cancelled, here is what it found so far. "}
          {run.status === "paused" && "The search is paused, here is the state so far. "}
          <span className="font-medium">
            {formatNumber(prisma.records_identified)} records identified
          </span>
          , {formatNumber(unique)} unique works
          {prisma.records_screened > 0 && (
            <>
              , <span className="font-medium">{formatNumber(included)} included</span> after
              screening
            </>
          )}
          .
          {queueCount > 0 && (
            <>
              {" "}
              {formatNumber(queueCount)} work{queueCount === 1 ? " is" : "s are"} waiting for
              your verdict in the review queue.
            </>
          )}
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button onClick={onShowResults} className="rounded-full">
            Show results
            <ArrowRight className="size-4" />
          </Button>
          {run.status === "completed" && (
            <ReportButton runId={run.id} variant="outline" className="h-9 rounded-full" />
          )}
          {run.status === "completed" && run.config?.mode !== "ask" && (
            <Button
              variant="outline"
              onClick={onRefine}
              className="h-9 rounded-full"
              title="Start a new version of this search with the query or filters adjusted; the result comes with a documented diff."
            >
              <GitCompareArrows className="size-4" />
              Refine
            </Button>
          )}
          <span className="text-[0.75rem] text-muted-foreground">
            PRISMA flow, ranked works, review queue, exports
          </span>
        </div>
      </div>
    </AssistantRow>
  );
}

export default function RunView({ runId }: { runId: RunRef }) {
  const { me } = useAuth();
  const isGerman = me?.language === "de";
  const router = useRouter();
  const queryClient = useQueryClient();
  const { setActiveProjectId } = useActiveProject();
  const { data: run, isLoading, error: runError, refetch: refetchRun, isFetching: fetchingRun } = useRun(runId);
  const { data: events = [] } = useRunEvents(runId);
  const { open: sidebarOpen } = useSidebarUi();
  useRunStream(runId, run?.status);
  useEffect(() => {
    if (run?.project_id) setActiveProjectId(run.project_id);
  }, [run?.project_id, setActiveProjectId]);

  const [tab, setTab] = useState<
    "search" | "control" | "results" | "extract" | "evidence" | "living"
  >("search");
  const chips = useMemo(() => configChips(run?.config ?? {}, isGerman), [run?.config, isGerman]);

  const hasResults = Boolean(run?.prisma);
  const screened = Boolean(run?.config?.screen);
  const { data: queuePage } = useQueue(runId, hasResults && screened, 1, 0);
  const askRun = run?.config?.mode === "ask";
  const chatEnabled = Boolean(
    askRun ||
      (run?.prisma &&
        run.prisma.records_identified - run.prisma.duplicates_removed > 0),
  );
  const chat = useRunChat(
    runId,
    chatEnabled,
    Boolean(askRun && run && isMoving(run.status)),
    events,
    Boolean(askRun),
    run?.config?.chat_model ?? run?.config?.model ?? "auto",
  );
  const runQueryKey = ["run", String(runId)] as const;
  const chatQueryKey = ["chat", String(runId)] as const;
  const [retryWebSearchConfirmed, setRetryWebSearchConfirmed] = useState(false);
  const retryNeedsWebSearchConfirmation = Boolean(
    askRun && run?.config?.web_search,
  );
  const retry = useMutation({
    mutationFn: () =>
      api.retryRun(runId, {
        webSearchPublicDataConfirmed:
          retryNeedsWebSearchConfirmation && retryWebSearchConfirmed,
      }),
    onMutate: async () => {
      await Promise.all([
        queryClient.cancelQueries({ queryKey: runQueryKey }),
        queryClient.cancelQueries({ queryKey: chatQueryKey }),
      ]);
      const previousRun = queryClient.getQueryData<RunDetail>(runQueryKey);
      const previousChat = queryClient.getQueryData(chatQueryKey);
      queryClient.setQueryData<RunDetail>(runQueryKey, (current) =>
        current
          ? { ...current, status: "pending", error: null, finished_at: null }
          : current,
      );
      queryClient.setQueryData(chatQueryKey, []);
      return { previousRun, previousChat };
    },
    onSuccess: () => {
      toast.success("Retry started. Your attachments and context are still here.");
      void queryClient.invalidateQueries({ queryKey: runQueryKey });
      void queryClient.invalidateQueries({ queryKey: ["run-events", String(runId)] });
      void queryClient.invalidateQueries({ queryKey: chatQueryKey });
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
    },
    onError: (error, _variables, context) => {
      if (context?.previousRun) {
        queryClient.setQueryData(runQueryKey, context.previousRun);
      }
      if (context?.previousChat !== undefined) {
        queryClient.setQueryData(chatQueryKey, context.previousChat);
      }
      toast.error(error instanceof Error ? error.message : "Retry failed.");
    },
    // Consent is an authorization for one concrete request, not durable UI
    // state. A failed network attempt therefore also requires a fresh check.
    onSettled: () => setRetryWebSearchConfirmed(false),
  });
  const askAnswerPersisted = chat.messages.some(
    (message) => message.role === "assistant",
  );
  const askActivityPlan = useMemo<AskActivityPlan | undefined>(() => {
    const event = [...events].reverse().find((entry) => entry.event === "ask_planned");
    if (!event) return undefined;
    return {
      academic_search: Boolean(event.payload.academic_search),
      web_search: Boolean(event.payload.web_search),
      show_paper: Boolean(event.payload.show_paper),
      chart: Boolean(event.payload.chart),
      cite: Boolean(event.payload.cite),
    };
  }, [events]);

  // a freshly opened, already-finished run jumps straight to the results
  useEffect(() => {
    if (run && isTerminal(run.status) && run.prisma) setTab("results");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run?.id]);

  // MCP-UI intents from embedded resources (host-level navigation)
  useEffect(() => {
    function onIntent(event: Event) {
      const detail = (event as CustomEvent).detail as { intent?: string } | undefined;
      if (detail?.intent === "open-results") setTab("results");
    }
    window.addEventListener("six:ui-intent", onIntent);
    return () => window.removeEventListener("six:ui-intent", onIntent);
  }, []);

  // Papers and substantial tables share one persistent split workspace. Tool
  // messages rebuild its tabs after a refresh; direct paper/table clicks can
  // also add or update a tab without mutating the conversation.
  const chatArtifacts = useMemo(
    () => artifactsFromMessages(chat.messages),
    [chat.messages],
  );
  const [manualArtifacts, setManualArtifacts] = useState<ResearchArtifact[]>([]);
  const artifacts = useMemo(() => {
    const merged = [...chatArtifacts];
    for (const artifact of manualArtifacts) {
      const existing = merged.findIndex((entry) => entry.id === artifact.id);
      if (existing < 0) {
        merged.push(artifact);
        continue;
      }
      // A table rebuilt from the persisted chat message is authoritative.
      // Keeping the manually opened snapshot here would make a saved or
      // agent-edited table look correct only until the next refresh/poll and
      // then silently replace it with stale rows. Paper tabs are different:
      // their manual state carries the active page/flash selection.
      if (artifact.kind !== "table") merged[existing] = artifact;
    }
    return merged;
  }, [chatArtifacts, manualArtifacts]);
  const artifactsRef = useRef(artifacts);
  artifactsRef.current = artifacts;
  const [activeArtifactId, setActiveArtifactId] = useState("");
  const [artifactWorkspaceOpen, setArtifactWorkspaceOpen] = useState(false);

  // the assistant's highlights per document (from show_paper messages):
  // page anchors and citation chips open the reader WITH them, so the
  // marked passages never disappear just because a chip re-opened the paper
  const highlightsByDoc = useMemo(() => {
    const map = new Map<number, PaperPanelState["highlights"]>();
    for (const message of chat.messages) {
      const payload = message.payload as ToolStepPayload | null;
      if (payload?.kind === "paper" && payload.document_id && payload.highlights?.length) {
        map.set(payload.document_id, payload.highlights);
      }
    }
    return map;
  }, [chat.messages]);
  const highlightsByDocRef = useRef(highlightsByDoc);
  highlightsByDocRef.current = highlightsByDoc;
  useEffect(() => {
    function onOpenPaper(event: Event) {
      const detail = (event as CustomEvent).detail as PaperPanelState | undefined;
      if (!detail?.documentId) return;
      const id = `paper:${detail.documentId}`;
      const existing = artifactsRef.current.find(
        (artifact) => artifact.id === id && artifact.kind === "paper",
      );
      const existingPaper = existing?.kind === "paper" ? existing.paper : null;
      let paper = detail;
      // A flash into an existing tab keeps its highlights and metadata alive.
      if (detail.flash && existingPaper && (detail.highlights?.length ?? 0) === 0) {
        paper = { ...existingPaper, page: detail.page, flash: detail.flash };
      } else {
        const known =
          (detail.highlights?.length ?? 0) === 0
            ? highlightsByDocRef.current.get(detail.documentId)
            : undefined;
        if (known) paper = { ...detail, highlights: known };
      }
      const artifact: ResearchArtifact = {
        id,
        kind: "paper",
        title: paper.title || "Paper",
        paper: { ...paper, highlights: paper.highlights ?? [] },
      };
      setManualArtifacts((current) => {
        const index = current.findIndex((entry) => entry.id === id);
        if (index < 0) return [...current, artifact];
        const next = [...current];
        next[index] = artifact;
        return next;
      });
      setActiveArtifactId(id);
      setArtifactWorkspaceOpen(true);
    }
    function onOpenTable(event: Event) {
      const detail = (event as CustomEvent).detail as ResearchArtifact | undefined;
      if (!detail || detail.kind !== "table" || !detail.id) return;
      setManualArtifacts((current) => {
        const index = current.findIndex((entry) => entry.id === detail.id);
        if (index < 0) return [...current, detail];
        const next = [...current];
        next[index] = detail;
        return next;
      });
      setActiveArtifactId(detail.id);
      setArtifactWorkspaceOpen(true);
    }
    function onOpenArtifact(event: Event) {
      const detail = (event as CustomEvent).detail as { id?: string } | undefined;
      if (!detail?.id || !artifactsRef.current.some((item) => item.id === detail.id)) {
        return;
      }
      setActiveArtifactId(detail.id);
      setArtifactWorkspaceOpen(true);
    }
    window.addEventListener("six:open-paper", onOpenPaper);
    window.addEventListener("six:open-table", onOpenTable);
    window.addEventListener("six:open-artifact", onOpenArtifact);
    return () => {
      window.removeEventListener("six:open-paper", onOpenPaper);
      window.removeEventListener("six:open-table", onOpenTable);
      window.removeEventListener("six:open-artifact", onOpenArtifact);
    };
  }, []);

  // drop a PDF anywhere on the run to attach it (same flow as the paperclip)
  const upload = useDocumentUpload(runId);
  const { dragging, dropProps } = usePdfDrop((files) => {
    for (const file of files) upload.mutate({ file });
  });

  const seenArtifactIds = useRef<Set<string> | null>(null);
  useEffect(() => {
    const currentIds = new Set(chatArtifacts.map((artifact) => artifact.id));
    if (seenArtifactIds.current === null) {
      seenArtifactIds.current = currentIds;
      const latest = chatArtifacts.at(-1);
      if (
        latest &&
        (latest.kind === "paper" ||
          (latest.kind === "table" && isSubstantialTable(latest.table)))
      ) {
        setActiveArtifactId(latest.id);
        setArtifactWorkspaceOpen(true);
      } else if (latest?.kind === "table") {
        setArtifactWorkspaceOpen(false);
      }
      return;
    }
    const fresh = chatArtifacts.filter(
      (artifact) => !seenArtifactIds.current?.has(artifact.id),
    );
    seenArtifactIds.current = currentIds;
    const latest = fresh.at(-1);
    if (
      latest &&
      (latest.kind === "paper" ||
        (latest.kind === "table" && isSubstantialTable(latest.table)))
    ) {
      setActiveArtifactId(latest.id);
      setArtifactWorkspaceOpen(true);
    } else if (latest?.kind === "table") {
      setArtifactWorkspaceOpen(false);
    }
  }, [chatArtifacts]);

  // the conversation column breathes: wide UI resources (charts, forms)
  // get more room than a plain text thread
  const hasWideUi = chat.messages.some(
    (message) => {
      const payload = message.payload as ToolStepPayload | null;
      return payload?.size === "wide" && !tableArtifactFromPayload(payload);
    },
  );
  const threadWidth = hasWideUi ? "max-w-[80rem]" : "max-w-5xl";

  if (isLoading) {
    return (
      <div className="mx-auto w-full max-w-4xl space-y-4 px-6 pt-20">
        <div className="flex justify-end">
          <Skeleton className="h-16 w-2/3 rounded-2xl" />
        </div>
        <Skeleton className="h-72 w-full rounded-2xl" />
      </div>
    );
  }

  if (runError || !run) {
    const notFound = runError instanceof ApiError && runError.status === 404;
    return (
      <DetailError
        title={notFound ? "Run not found" : "Research could not be loaded"}
        error={notFound ? null : runError}
        fallback={notFound
          ? "It may belong to another workspace, or it was removed."
          : "We could not load this research right now. Please try again."}
        onRetry={!runError || retryTransientApiQuery(0, runError) ? () => void refetchRun() : undefined}
        retrying={fetchingRun}
        backHref="/"
        backLabel="Back to research"
      />
    );
  }

  const effectiveStatus =
    run.config?.mode === "ask" && askAnswerPersisted && isMoving(run.status)
      ? "completed"
      : run.status;
  const meta = STATUS_META[effectiveStatus];
  const askMode = run.config?.mode === "ask";
  const uniqueWorks = run.prisma
    ? run.prisma.records_identified - run.prisma.duplicates_removed
    : 0;
  // Quick-answer conversations must keep their composer mounted while the
  // streamed answer hands off to the persisted chat row. Tying it to the
  // transient completion status caused the input to briefly disappear.
  const hasWorks = askMode || uniqueWorks > 0;
  const includedCount = run.prisma
    ? run.prisma.studies_included > 0
      ? run.prisma.studies_included
      : run.prisma.included
    : 0;
  const initialAskWorking =
    askMode && isMoving(run.status) && !askAnswerPersisted;
  const chatDisabledReason = !askMode && !hasWorks
    ? "Chat unlocks once the run has retrieved works."
    : null;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <h1 className="sr-only">{run.title ?? run.question}</h1>
      {/* Header */}
      <header
        className={cn(
          "flex h-13 shrink-0 items-center justify-between gap-3 pl-3 pr-3 pt-1 lg:pl-24 lg:pr-4",
          sidebarOpen && "lg:pl-6",
        )}
      >
        <div className="flex min-w-0 items-center gap-2.5">
          {!askMode ? (
            <span
              className={cn(
                "size-2 shrink-0 rounded-full",
                meta.dot,
                isMoving(effectiveStatus) && "status-pulse",
              )}
            />
          ) : null}
          <span className="truncate text-[0.84375rem] font-medium">{run.title ?? run.question}</span>
          {!askMode ? (
            <Badge
              variant="outline"
              className={cn("shrink-0 rounded-full border-0 text-[0.65625rem]", meta.badge)}
            >
              {meta.label}
            </Badge>
          ) : null}
        </div>
        <div className="flex items-center gap-1">
          <RunShareDialog run={run} />
          <ChatReportDialog runId={run.id} />
          {!askMode && <RunControls run={run} />}
        </div>
      </header>

      {/* Below the header: the run column, plus the paper reader when open */}
      <div className="flex min-h-0 flex-1 overflow-hidden">
        <div className="relative flex min-w-0 flex-1 flex-col" {...dropProps}>
          {dragging && (
            <div className={dropOverlayClass("rounded-none")}>
              <div className="flex flex-col items-center gap-2 text-moss">
                <FileUp className="size-7" />
                <p className="text-[0.9375rem] font-medium">Drop your PDF here</p>
                <p className="text-[0.75rem] text-moss/80">
                  It joins this conversation&apos;s sources.
                </p>
              </div>
            </div>
          )}

      {/* Tabs: the same pill segmented control as every other workspace (a
          quick answer is a pure conversation, so it hides them entirely) */}
      <div
        data-tour="run-tabs"
        className={cn(
          "flex shrink-0 items-center border-b border-border px-4 py-2",
          askMode && "hidden",
        )}
      >
        <div className="flex w-fit max-w-full items-center gap-1 overflow-x-auto rounded-full bg-secondary/65 p-1">
          <button
            type="button"
            onClick={() => setTab("search")}
            className={cn(
              "flex h-8 cursor-pointer items-center gap-1.5 rounded-full px-3 text-[0.71875rem] font-medium transition-colors",
              tab === "search"
                ? "bg-card text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            <MessageSquareText className="size-3.5" />
            Search
          </button>
          <button
            type="button"
            onClick={() => setTab("control")}
            className={cn(
              "flex h-8 cursor-pointer items-center gap-1.5 rounded-full px-3 text-[0.71875rem] font-medium transition-colors",
              tab === "control"
                ? "bg-card text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            <Activity className="size-3.5" />
            Control
            {isMoving(run.status) && (
              <span className="size-1.5 rounded-full bg-moss-surface status-pulse" />
            )}
          </button>
          <button
            type="button"
            onClick={() => hasResults && setTab("results")}
            disabled={!hasResults}
            className={cn(
              "flex h-8 items-center gap-1.5 rounded-full px-3 text-[0.71875rem] font-medium transition-colors",
              tab === "results"
                ? "bg-card text-foreground shadow-sm"
                : hasResults
                  ? "cursor-pointer text-muted-foreground hover:text-foreground"
                  : "cursor-not-allowed text-muted-foreground/40",
            )}
          >
            <LayoutGrid className="size-3.5" />
            Results
            {hasResults && includedCount > 0 && (
              <span className="rounded-full bg-accent px-1.5 py-0.5 font-mono text-[0.59375rem] text-moss">
                {formatNumber(includedCount)}
              </span>
            )}
          </button>
          <button
            type="button"
            onClick={() => hasResults && setTab("extract")}
            disabled={!hasResults}
            className={cn(
              "flex h-8 items-center gap-1.5 rounded-full px-3 text-[0.71875rem] font-medium transition-colors",
              tab === "extract"
                ? "bg-card text-foreground shadow-sm"
                : hasResults
                  ? "cursor-pointer text-muted-foreground hover:text-foreground"
                  : "cursor-not-allowed text-muted-foreground/40",
            )}
          >
            <Table2 className="size-3.5" />
            Extract
          </button>
          <button
            type="button"
            onClick={() => hasResults && setTab("evidence")}
            disabled={!hasResults}
            className={cn(
              "flex h-8 items-center gap-1.5 rounded-full px-3 text-[0.71875rem] font-medium transition-colors",
              tab === "evidence"
                ? "bg-card text-foreground shadow-sm"
                : hasResults
                  ? "cursor-pointer text-muted-foreground hover:text-foreground"
                  : "cursor-not-allowed text-muted-foreground/40",
            )}
          >
            <Network className="size-3.5" />
            Evidence
          </button>
          <button
            type="button"
            onClick={() => hasResults && setTab("living")}
            disabled={!hasResults}
            className={cn(
              "flex h-8 items-center gap-1.5 rounded-full px-3 text-[0.71875rem] font-medium transition-colors",
              tab === "living"
                ? "bg-card text-foreground shadow-sm"
                : hasResults
                  ? "cursor-pointer text-muted-foreground hover:text-foreground"
                  : "cursor-not-allowed text-muted-foreground/40",
            )}
          >
            <Radio className="size-3.5" />
            Living
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        {tab === "search" ? (
          <>
          <div className="min-h-0 flex-1 overflow-y-auto">
          <div
            className={cn(
              "mx-auto w-full px-3 pb-4 pt-5 transition-[max-width] duration-500 ease-out sm:px-6 sm:pt-6",
              threadWidth,
            )}
          >
            {/* The question */}
            <div className="mb-6 flex flex-col items-end gap-2">
              <div className="max-w-[85%] rounded-2xl rounded-br-md bg-moss-surface px-3.5 py-2.5 text-[0.875rem] leading-relaxed text-ivory">
                {run.question}
              </div>
              {chips.length > 0 && (
                <div className="flex max-w-[85%] flex-wrap justify-end gap-1.5">
                  {chips.map((chip) => (
                    <span
                      key={chip}
                      className="rounded-full bg-secondary px-2.5 py-1 font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground"
                    >
                      {chip}
                    </span>
                  ))}
                </div>
              )}
              {!askMode && Boolean(run.config?.paper_limit || run.config?.screen_limit || run.config?.exhaustive === false) && (
                <p className="max-w-[85%] text-right text-xs leading-relaxed text-muted-foreground">
                  {isGerman
                    ? "Die Paperzahl begrenzt das finale Ergebnis, nicht die Suche oder das Screening. Auch ohne Sucherweiterung können viele Treffer geprüft werden; das ist kein Zeit- oder Kostenlimit."
                    : "The paper limit caps final results, not retrieval or screening. Even without query expansion, many records may be screened; it does not limit the work required before selection."}
                </p>
              )}
            </div>

            {/* The system's turn */}
            <div className="space-y-4">
              {!askMode && run.status === "awaiting_protocol_approval" && (
                <ProtocolCard run={run} />
              )}

              {run.status === "failed" && run.error && (
                <div className="flex flex-wrap items-start gap-3 rounded-2xl border border-destructive/30 bg-destructive/5 px-5 py-4">
                  <CircleAlert className="mt-0.5 size-4 shrink-0 text-destructive" />
                  <div className="min-w-0 flex-1 text-[0.84375rem] leading-relaxed">
                    <p className="font-medium text-destructive">The run failed.</p>
                    <p className="mt-0.5 break-words font-mono text-[0.75rem] text-destructive/80">
                      {userFacingStoredErrorMessage(
                        run.error,
                        "We couldn't finish this request. Your saved work is unchanged. Please try again.",
                      )}
                    </p>
                  </div>
                  {askMode && (
                    <div className="flex min-w-0 flex-col items-end gap-2">
                      {retryNeedsWebSearchConfirmation ? (
                        <div className="flex max-w-xl items-start gap-2 rounded-xl border border-moss/30 bg-accent/45 px-3 py-2">
                          <Checkbox
                            id="retry-web-search-public-data-confirmed"
                            checked={retryWebSearchConfirmed}
                            onCheckedChange={(checked) =>
                              setRetryWebSearchConfirmed(checked === true)
                            }
                            aria-describedby="retry-web-search-public-data-description"
                            aria-required="true"
                            className="mt-0.5"
                          />
                          <Label
                            htmlFor="retry-web-search-public-data-confirmed"
                            id="retry-web-search-public-data-description"
                            className="cursor-pointer text-[0.6875rem] font-normal leading-[1.05rem] text-muted-foreground"
                          >
                            I confirm this retry uses only public search terms: no
                            personal, confidential or sensitive data and no content
                            from transcripts, manuscripts or uploads. Search terms go
                            to an external search service.
                          </Label>
                        </div>
                      ) : null}
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        className="h-8 shrink-0 rounded-full bg-background/80"
                        onClick={() => retry.mutate()}
                        disabled={
                          retry.isPending ||
                          (retryNeedsWebSearchConfirmation &&
                            !retryWebSearchConfirmed)
                        }
                      >
                        {retry.isPending ? (
                          <Loader2 className="size-3.5 animate-spin" />
                        ) : (
                          <RotateCcw className="size-3.5" />
                        )}
                        Retry answer
                      </Button>
                    </div>
                  )}
                </div>
              )}

              {!askMode && <StageTimeline run={run} events={events} />}

              {isMoving(run.status) && !askMode && (
                <WorkingBubble run={run} events={events} />
              )}

              {run.status === "paused" && (
                <div className="flex items-start gap-3 rounded-2xl border border-amber-200 bg-amber-50/50 px-5 py-4 dark:border-amber-300/25 dark:bg-amber-300/10">
                  <PauseCircle className="mt-0.5 size-4 shrink-0 text-amber-700" />
                  <div className="text-[0.84375rem] leading-relaxed text-amber-900">
                    <p className="font-medium">This run is paused. Nothing was lost.</p>
                    <p className="mt-0.5 text-amber-800/90">
                      Resume it from the controls above; works that are already
                      screened are skipped automatically.
                    </p>
                  </div>
                </div>
              )}

              {!askMode && run.status !== "awaiting_protocol_approval" && (
                <ProtocolCard run={run} />
              )}

              {hasResults && !isMoving(run.status) && (
                <CompletionCard
                  run={run}
                  queueCount={queuePage?.total ?? 0}
                  onShowResults={() => setTab("results")}
                  onRefine={() => {
                    // the composer on the home page consumes this and
                    // preloads the question, options and parent link
                    sessionStorage.setItem(
                      "six:refine",
                      JSON.stringify({
                        public_id: run.public_id,
                        label: run.title ?? run.question,
                        question: run.question,
                        config: run.config ?? {},
                      }),
                    );
                    window.dispatchEvent(new CustomEvent("six:refine-search"));
                    router.push("/");
                  }}
                />
              )}

              {/* a refined run shows what changed against its parent */}
              {hasResults && !isMoving(run.status) && run.config?.parent_run && (
                <AssistantRow>
                  <RefineDiff runId={runId} against={run.config.parent_run} />
                </AssistantRow>
              )}
            </div>

            {/* Grounded chat thread (scrolls with the conversation) */}
            {(hasWorks || askMode) && (
              <ChatThread
                runId={runId}
                chat={chat}
                heading={askMode ? null : "Ask the results"}
                askMode={askMode}
                initialQuestion={run.question}
                working={initialAskWorking}
                activityPlan={askActivityPlan}
              />
            )}
          </div>
          </div>

          {/* The chat input stays pinned below the scroll area */}
          {hasWorks && (
            <div className="shrink-0 border-t border-border/60 bg-background px-3 pb-3 pt-2.5 sm:px-6">
              <div
                className={cn(
                  "mx-auto w-full transition-[max-width] duration-500 ease-out",
                  threadWidth,
                )}
              >
                <ChatComposer
                  runId={runId}
                  chat={chat}
                  disabledReason={chatDisabledReason}
                  askMode={askMode}
                  initialQuestion={run.question}
                />
              </div>
            </div>
          )}
          </>
        ) : tab === "control" ? (
          <ResearchControlRoom run={run} />
        ) : tab === "results" ? (
          <ResultsPanel
            run={run}
            events={events}
            onAskResults={() => setTab("search")}
          />
        ) : tab === "extract" ? (
          <ExtractionStudio runId={run.id} />
        ) : tab === "evidence" ? (
          <ClaimEvidenceGraph runId={run.id} />
        ) : (
          <LivingResearchPanel runId={run.id} />
        )}
      </div>

        </div>

        {artifactWorkspaceOpen && artifacts.length > 0 && (
          <ResearchArtifactWorkspace
            artifacts={artifacts}
            activeId={activeArtifactId}
            runId={runId}
            onSelect={(id) => {
              setActiveArtifactId(id);
              setArtifactWorkspaceOpen(true);
            }}
            onClose={() => setArtifactWorkspaceOpen(false)}
            onDiscuss={(selection) => {
              chat.setSelection(selection);
              setTab("search");
            }}
            onPrompt={(prompt) => {
              setTab("search");
              void chat.send(prompt);
            }}
          />
        )}
      </div>
    </div>
  );
}
