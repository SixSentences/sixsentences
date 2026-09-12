"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  AlertCircle,
  Copy,
  FileText,
  Gavel,
  Loader2,
  Table2,
  Globe,
  Info,
  Library,
  ListTodo,
  MessageSquareText,
  MoreHorizontal,
  Quote,
  Radio,
  RefreshCcw,
  ShieldCheck,
  Target,
} from "lucide-react";
import { toast } from "sonner";
import { coveragePresentation, recallPresentation } from "@/lib/review-quality-presentation";

import CalibrationDialog from "@/components/run/calibration-dialog";
import DecisionsPanel from "@/components/run/decisions-panel";
import EvidencePanel from "@/components/run/evidence-panel";
import ProbePanel from "@/components/run/probe-panel";
import DocumentsPanel from "@/components/run/documents-panel";
import ExportMenu from "@/components/run/export-menu";
import ReportButton, { generateAndDownloadReport } from "@/components/run/report-button";
import MethodsPanel from "@/components/run/methods-panel";
import PrismaFlow from "@/components/run/prisma-flow";
import QueuePanel from "@/components/run/queue-panel";
import WebSourcesPanel from "@/components/run/web-sources-panel";
import WorksTable from "@/components/run/works-table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useQueue, useWorks } from "@/hooks/queries";
import { api } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import type { QueryTranslations, RunDetail, RunEvent } from "@/lib/types";

type QualityWarning = NonNullable<RunDetail["config"]["quality_warnings"]>[number];

function QualityWarningsCard({ warnings }: { warnings: QualityWarning[] }) {
  if (warnings.length === 0) return null;
  return (
    <div className="rounded-2xl border border-amber-200/80 bg-amber-50/60 px-4 py-3.5 dark:border-amber-300/25 dark:bg-amber-300/10">
      <div className="flex items-start gap-2.5">
        <AlertCircle className="mt-0.5 size-4 shrink-0 text-amber-700 dark:text-amber-200" />
        <div className="min-w-0">
          <p className="text-[0.78125rem] font-medium text-foreground">
            Review limitations to resolve
          </p>
          <ul className="mt-2 space-y-2">
            {warnings.map((warning) => (
              <li key={warning.code}>
                <p className="text-[0.71875rem] font-medium text-foreground/85">
                  {warning.title}
                </p>
                <p className="mt-0.5 text-[0.6875rem] leading-relaxed text-muted-foreground">
                  {warning.detail}
                </p>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}

function ReviewHealthPanel({ runId }: { runId: number }) {
  const { data, isLoading } = useQuery({
    queryKey: ["review-health", runId],
    queryFn: () => api.reviewHealth(runId),
  });
  if (isLoading || !data) {
    return <Loader2 className="mx-auto my-16 size-5 animate-spin text-muted-foreground" />;
  }
  return (
    <div className="space-y-4 pb-3">
      <div className="flex flex-wrap items-center gap-5 rounded-3xl border border-border bg-card p-5">
        <div className="grid size-24 shrink-0 place-items-center rounded-full border-[7px] border-moss/20 bg-accent/35 text-center">
          <div><p className="font-mono text-2xl font-medium text-foreground">{data.score}%</p><p className="text-[0.59375rem] uppercase tracking-[0.18em] text-muted-foreground">ready</p></div>
        </div>
        <div className="min-w-0 flex-1">
          <p className="font-serif text-2xl text-foreground">Methodological readiness</p>
          <p className="mt-1 max-w-2xl text-[0.78125rem] leading-relaxed text-muted-foreground">{data.ready} of {data.total} safeguards have reconstructable evidence in this review. This is a working checklist, not a validity certificate.</p>
          {data.next_actions.length > 0 && <p className="mt-3 text-[0.71875rem] font-medium text-moss">Next: {data.next_actions[0].label}</p>}
        </div>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        {data.checks.map((check) => (
          <article key={check.id} className="flex gap-3 rounded-2xl border border-border bg-card p-4">
            <div className={check.status === "ready" ? "grid size-8 shrink-0 place-items-center rounded-full bg-accent text-moss" : "grid size-8 shrink-0 place-items-center rounded-full bg-amber-50 text-amber-700 dark:bg-amber-300/10 dark:text-amber-200"}>
              {check.status === "ready" ? <Check className="size-4" /> : <AlertCircle className="size-4" />}
            </div>
            <div className="min-w-0 flex-1"><div className="flex items-center justify-between gap-2"><p className="text-[0.8125rem] font-medium text-foreground">{check.label}</p><span className="shrink-0 rounded-full bg-secondary px-2 py-0.5 font-mono text-[0.5625rem] uppercase tracking-[0.18em] text-muted-foreground">{check.action}</span></div><p className="mt-1.5 text-[0.71875rem] leading-relaxed text-muted-foreground">{check.detail}</p></div>
          </article>
        ))}
      </div>
      <p className="px-2 text-[0.6875rem] text-muted-foreground">{data.note}</p>
    </div>
  );
}

/** The executed boolean search string — copyable, reusable in any database. */
function SearchStringCard({
  runId,
  searchString,
  synthesizedBy,
}: {
  runId: number;
  searchString: string;
  synthesizedBy: string | null;
}) {
  const [copied, setCopied] = useState(false);
  const [showTargets, setShowTargets] = useState(false);
  const [copiedTarget, setCopiedTarget] = useState("");
  const { data: translated, isLoading: translating } = useQuery({
    queryKey: ["query-translations", runId],
    queryFn: () => api.queryTranslations(runId),
    enabled: showTargets,
    staleTime: Number.POSITIVE_INFINITY,
    retry: false,
  });
  const targetRows: { key: keyof QueryTranslations["targets"]; label: string }[] = [
    { key: "pubmed", label: "PubMed" },
    { key: "scopus", label: "Scopus" },
    { key: "wos", label: "Web of Science" },
    { key: "ieee", label: "IEEE Xplore" },
  ];
  return (
    <div className="rounded-2xl border border-border bg-card p-5">
      <div className="mb-3 flex items-center justify-between gap-2">
        <p className="font-mono text-[0.625rem] uppercase tracking-[0.22em] text-muted-foreground">
          Search string
        </p>
        <Button
          variant="outline"
          size="sm"
          className="h-6.5 rounded-full px-2.5 text-[0.6875rem]"
          onClick={() => {
            void navigator.clipboard.writeText(searchString).then(() => {
              setCopied(true);
              setTimeout(() => setCopied(false), 1600);
            });
          }}
        >
          {copied ? <Check className="size-3 text-moss" /> : <Copy className="size-3" />}
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
      <p className="break-words rounded-xl bg-secondary/60 px-3.5 py-3 font-mono text-[0.75rem] leading-relaxed text-foreground/90">
        {searchString}
      </p>
      <p className="mt-2.5 text-[0.71875rem] leading-relaxed text-muted-foreground">
        Paste it into your review&apos;s search appendix.
        {synthesizedBy === "user"
          ? " You supplied this string yourself."
          : synthesizedBy === "heuristic"
            ? " Built from the review question and criteria."
            : ""}
      </p>
      <button
        type="button"
        onClick={() => setShowTargets((open) => !open)}
        className="mt-2 cursor-pointer font-mono text-[0.625rem] uppercase tracking-[0.18em] text-moss underline decoration-moss/30 underline-offset-4 hover:decoration-moss"
      >
        {showTargets ? "Hide other databases" : "For other databases"}
      </button>
      {showTargets ? (
        translating ? (
          <Loader2 className="mt-3 size-4 animate-spin text-muted-foreground" />
        ) : translated ? (
          <div className="mt-3 space-y-2">
            {targetRows.map(({ key, label }) => (
              <div key={key}>
                <div className="flex items-center justify-between">
                  <p className="font-mono text-[0.59375rem] uppercase tracking-[0.18em] text-muted-foreground">
                    {label}
                  </p>
                  <button
                    type="button"
                    className="cursor-pointer font-mono text-[0.59375rem] uppercase tracking-[0.18em] text-muted-foreground hover:text-moss"
                    onClick={() => {
                      void navigator.clipboard
                        .writeText(translated.targets[key])
                        .then(() => {
                          setCopiedTarget(key);
                          setTimeout(() => setCopiedTarget(""), 1600);
                        });
                    }}
                  >
                    {copiedTarget === key ? "copied" : "copy"}
                  </button>
                </div>
                <p className="mt-1 break-words rounded-lg bg-secondary/50 px-2.5 py-2 font-mono text-[0.6875rem] leading-relaxed text-foreground/85">
                  {translated.targets[key]}
                </p>
              </div>
            ))}
            <p className="text-[0.6875rem] leading-relaxed text-muted-foreground">
              Run it there, export RIS or BibTeX, and attach the file to your
              next search here. The records join identification as their own
              database arm.
            </p>
          </div>
        ) : null
      ) : null}
    </div>
  );
}

function extractStats(events: RunEvent[]) {
  let coverage = coveragePresentation({});
  let recall = recallPresentation({});
  for (const event of events) {
    if (event.event === "coverage_estimated") {
      coverage = coveragePresentation(event.payload ?? {});
    }
    if (event.event === "screening_recall_certified") {
      recall = recallPresentation(event.payload ?? {});
    }
  }
  return { coverage, recall };
}

function StatTile({ value, label, sub }: { value: string; label: string; sub?: string }) {
  return (
    <div className="min-w-0 rounded-xl border border-border bg-card px-2.5 py-2 sm:px-4 sm:py-2.5">
      <p className="font-mono text-[1.125rem] font-medium tabular-nums leading-tight text-foreground">
        {value}
      </p>
      <p className="mt-0.5 text-[0.625rem] leading-tight text-muted-foreground sm:text-[0.71875rem]">
        {label}
        {sub ? (
          <span className="block text-muted-foreground/70 sm:inline">
            <span className="hidden sm:inline"> · </span>
            {sub}
          </span>
        ) : null}
      </p>
    </div>
  );
}

/**
 * The results workspace: headline stats and actions on top, the PRISMA flow
 * pinned on the left, and the deep views (works, review queue, decisions,
 * ledger, sources, methods, audit log) using the full remaining width.
 */
export default function ResultsPanel({
  run,
  events,
  onAskResults,
}: {
  run: RunDetail;
  events: RunEvent[];
  onAskResults: () => void;
}) {
  const [calibrationOpen, setCalibrationOpen] = useState(false);
  const queryClient = useQueryClient();
  const { data: queuePage } = useQueue(run.id, true, 1, 0);
  const { data: worksPage } = useWorks(run.id, false);
  const stats = useMemo(() => extractStats(events), [events]);
  const prisma = run.prisma;

  const living = Boolean(run.config?.living);
  const screened = Boolean(run.config?.screen);
  const completed = run.status === "completed";
  // the library shows acquired full texts AND user uploads — always offered
  // once the run is done, even when acquisition was not part of the run
  const showLibrary = Boolean(run.config?.acquire || run.config?.full_text) || completed;
  const webSearched = Boolean(run.config?.web_search);

  const setLiving = useMutation({
    mutationFn: (enabled: boolean) => api.setLiving(run.id, enabled),
    onSuccess: (result) => {
      toast.success(
        result.living
          ? "Living review on: retraction re-checks watch this run."
          : "Living review off.",
      );
      void queryClient.invalidateQueries({ queryKey: ["run", run.id] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Living review could not be updated."),
  });

  const recheck = useMutation({
    mutationFn: () => api.recheck(run.id),
    onSuccess: (delta) => {
      if (delta.newly_retracted.length > 0) {
        toast.warning(
          `${delta.newly_retracted.length} of your included works ${
            delta.newly_retracted.length === 1 ? "was" : "were"
          } retracted since this run: ${delta.newly_retracted.join(", ")}`,
          { duration: 10_000 },
        );
      } else {
        toast.success(`Re-checked ${delta.checked} included works, no new retractions.`);
      }
      void queryClient.invalidateQueries({ queryKey: ["run-events", run.id] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Re-check failed."),
  });

  if (!prisma) return null;

  // Output selection is not an inclusion decision. Incomplete runs display
  // the full screening ledger, never an inferred final selection.
  const resultCounts = completed
    ? worksPage?.result_evidence_counts
    : worksPage?.evidence_counts;
  const identifiedCount = worksPage?.identified_total ??
    Math.max(0, prisma.records_identified - prisma.duplicates_removed);
  const includedCount = resultCounts
    ? resultCounts.confirmed_include + resultCounts.provisional_include
    : Math.max(0, prisma.included - prisma.reports_excluded_fulltext);
  const headlineCount = screened
    ? includedCount
    : completed ? (worksPage?.selected_total ?? identifiedCount) : identifiedCount;
  const unscreenedCount = worksPage?.evidence_counts?.unscreened ??
    Math.max(0, identifiedCount - prisma.records_screened);
  const uncertaintyHint = screened && (resultCounts?.unsure ?? prisma.records_unsure) > 0;
  const qualityWarnings = run.config.quality_warnings ?? [];

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden px-3 pb-3 pt-4 sm:px-6">
      {/* Headline: stats + actions */}
      <div
        data-tour="run-results"
        className="grid shrink-0 gap-3 lg:flex lg:flex-wrap lg:items-center"
      >
        <div className="grid w-full grid-cols-3 gap-2 sm:gap-3 lg:max-w-2xl lg:flex-1">
          <StatTile
            value={formatNumber(headlineCount)}
            label={screened
              ? completed ? "included in results" : "included so far"
              : completed ? "candidates selected" : "candidates found"}
            sub={
              screened && resultCounts
                ? `${formatNumber(resultCounts.confirmed_include)} confirmed · ${formatNumber(resultCounts.provisional_include)} provisional`
                : undefined
            }
          />
          <StatTile
            value={
              stats.coverage.value !== null
                ? `${(stats.coverage.value * 100).toFixed(1)}%`
                : "n/a"
            }
            label="est. search coverage"
            sub={stats.coverage.detail}
          />
          <StatTile
            value={stats.recall.value !== null ? `${(stats.recall.value * 100).toFixed(1)}%` : "n/a"}
            label="est. screening recall"
            sub={stats.recall.detail}
          />
        </div>
        <div className="flex items-center justify-end gap-1.5 lg:ml-auto">
          <Button
            variant="outline"
            className="h-8 rounded-full px-3 text-[0.75rem]"
            onClick={onAskResults}
          >
            <MessageSquareText className="size-3.5" />
            Ask results
          </Button>
          {run.status === "completed" && <ReportButton runId={run.id} />}
          <ExportMenu runId={run.id} />
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="outline"
                size="icon"
                className="size-8 rounded-full"
                aria-label="More actions"
              >
                <MoreHorizontal className="size-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-[15.625rem]">
              <DropdownMenuLabel className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
                Keep it alive
              </DropdownMenuLabel>
              <DropdownMenuCheckboxItem
                checked={living}
                onCheckedChange={(checked) => {
                  const enabled = checked === true;
                  setLiving.mutate(enabled);
                }}
              >
                <Radio className="size-4" /> Living review
              </DropdownMenuCheckboxItem>
              <DropdownMenuItem onSelect={() => recheck.mutate()}>
                <RefreshCcw className="size-4" /> Re-check retractions now
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem onSelect={() => setCalibrationOpen(true)}>
                <Target className="size-4" /> Calibrate the screener…
              </DropdownMenuItem>
              {run.status === "completed" && (
                <DropdownMenuItem
                  onSelect={() => {
                    toast.promise(generateAndDownloadReport(run.id, true), {
                      loading: "Writing a fresh report…",
                      success: "Fresh report downloaded.",
                      error: (error) =>
                        error instanceof Error
                          ? error.message
                          : "The report could not be written.",
                    });
                  }}
                >
                  <FileText className="size-4" /> Rewrite the report…
                </DropdownMenuItem>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      {(!completed || (screened && unscreenedCount > 0)) && (
        <p className="mt-3 shrink-0 rounded-xl border border-border bg-secondary/40 px-4 py-2.5 text-[0.78125rem] leading-relaxed text-muted-foreground">
          {!completed && (
            <><span className="font-medium text-foreground">Partial results. </span>
              {run.status === "cancelled" ? "This search was cancelled. "
                : run.status === "paused" ? "This search is paused. "
                : run.status === "failed" ? "This search did not finish. "
                : "This search has not finished yet. "}
              Found records and recorded decisions are preserved, but this is not a final review. </>
          )}
          {screened && unscreenedCount > 0 && (
            <>{formatNumber(unscreenedCount)} works have not been screened and are not counted as inclusions.</>
          )}
        </p>
      )}

      <div className="mt-3 xl:hidden">
        <QualityWarningsCard warnings={qualityWarnings} />
      </div>

      {/* Two-column workspace, each column scrolls inside itself */}
      <div className="mt-4 grid min-h-0 flex-1 gap-5 xl:grid-cols-[minmax(340px,420px)_minmax(0,1fr)] xl:grid-rows-[minmax(0,1fr)]">
        <aside className="hidden min-h-0 space-y-4 overflow-y-auto pb-1 xl:block">
          {worksPage?.search_string && (
            <SearchStringCard
              runId={run.id}
              searchString={worksPage.search_string}
              synthesizedBy={worksPage.search_synthesized_by ?? null}
            />
          )}

          <div
            data-tour="run-record"
            className="rounded-2xl border border-border bg-card p-5"
          >
            <p className="mb-4 font-mono text-[0.625rem] uppercase tracking-[0.22em] text-muted-foreground">
              PRISMA 2020 flow
            </p>
            <PrismaFlow prisma={prisma} />
          </div>

          <QualityWarningsCard warnings={qualityWarnings} />

          {uncertaintyHint && (
            <div className="flex gap-2.5 rounded-2xl border border-border bg-secondary/50 px-4 py-3.5">
              <Info className="mt-0.5 size-4 shrink-0 text-moss" />
              <p className="text-[0.78125rem] leading-relaxed text-muted-foreground">
                <span className="font-medium text-foreground">
                  Why do some works remain unsure?
                </span>{" "}
                An uncertain record is never treated as an exclusion. If the
                criteria cannot be verified from its title and abstract, or an
                exclusion lacks a verified passage, the paper advances to the
                full-text or human review queue.
              </p>
            </div>
          )}
        </aside>

        <section data-tour="run-papers" className="flex min-h-0 min-w-0 flex-col">
          <Tabs defaultValue="works" className="flex min-h-0 flex-1 flex-col gap-0">
            <TabsList className="h-auto w-full shrink-0 justify-start gap-1 overflow-x-auto rounded-full bg-secondary/70 p-1">
              <TabsTrigger value="works" className="rounded-full px-3.5 text-[0.78125rem]">
                <Library className="size-3.5" />
                Works
              </TabsTrigger>
              {screened && (
                <TabsTrigger value="queue" className="rounded-full px-3.5 text-[0.78125rem]">
                  <ListTodo className="size-3.5" />
                  Review queue
                  {queuePage && queuePage.total > 0 && (
                    <Badge className="ml-1 h-4 min-w-4 rounded-full bg-amber-100 px-1 font-mono text-[0.59375rem] text-amber-900">
                      {queuePage.total}
                    </Badge>
                  )}
                </TabsTrigger>
              )}
              {screened && (
                <TabsTrigger value="decisions" className="rounded-full px-3.5 text-[0.78125rem]">
                  <Gavel className="size-3.5" />
                  Decisions
                </TabsTrigger>
              )}
              {screened && (
                <TabsTrigger value="evidence" className="rounded-full px-3.5 text-[0.78125rem]">
                  <Table2 className="size-3.5" />
                  Evidence
                </TabsTrigger>
              )}
              {showLibrary && (
                <TabsTrigger value="documents" className="rounded-full px-3.5 text-[0.78125rem]">
                  <FileText className="size-3.5" />
                  Library
                </TabsTrigger>
              )}
              {webSearched && (
                <TabsTrigger value="web" className="rounded-full px-3.5 text-[0.78125rem]">
                  <Globe className="size-3.5" />
                  Web sources
                </TabsTrigger>
              )}
              <TabsTrigger value="methods" className="rounded-full px-3.5 text-[0.78125rem]">
                <Quote className="size-3.5" />
                Methods
              </TabsTrigger>
              <TabsTrigger value="health" className="rounded-full px-3.5 text-[0.78125rem]">
                <ShieldCheck className="size-3.5" />
                Review health
              </TabsTrigger>
            </TabsList>

            <div className="min-h-0 flex-1 pt-3">
              <TabsContent value="works" className="m-0 h-full">
                <WorksTable runId={run.id} completed={completed} fitHeight />
              </TabsContent>
              {screened && (
                <TabsContent value="queue" className="m-0 h-full overflow-y-auto pb-1">
                  <QueuePanel runId={run.id} />
                </TabsContent>
              )}
              {screened && (
                <TabsContent value="decisions" className="m-0 h-full overflow-y-auto pb-1">
                  <ProbePanel runId={run.id} />
                  <DecisionsPanel runId={run.id} />
                </TabsContent>
              )}
              {screened && (
                <TabsContent value="evidence" className="m-0 h-full overflow-y-auto pb-1">
                  <EvidencePanel runId={run.id} />
                </TabsContent>
              )}
              {showLibrary && (
                <TabsContent value="documents" className="m-0 h-full overflow-y-auto pb-1">
                  <DocumentsPanel runId={run.id} canCollect={completed} />
                </TabsContent>
              )}
              {webSearched && (
                <TabsContent value="web" className="m-0 h-full overflow-y-auto pb-1">
                  <WebSourcesPanel runId={run.id} />
                </TabsContent>
              )}
              <TabsContent value="methods" className="m-0 h-full overflow-y-auto pb-1">
                <MethodsPanel runId={run.id} />
              </TabsContent>
              <TabsContent value="health" className="m-0 h-full overflow-y-auto pb-1">
                <ReviewHealthPanel runId={run.id} />
              </TabsContent>
            </div>
          </Tabs>
        </section>
      </div>

      <CalibrationDialog
        runId={run.id}
        open={calibrationOpen}
        onOpenChange={setCalibrationOpen}
      />
    </div>
  );
}
