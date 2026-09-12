"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  AlertCircle,
  BookCheck,
  CheckCircle2,
  Database,
  FileCode2,
  Link2,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { api } from "@/lib/api";
import type { WriterAuditFinding } from "@/lib/types";
import { cn } from "@/lib/utils";

type ReviewTab = "findings" | "research";
type FindingFilter = "all" | WriterAuditFinding["category"];

const categoryLabels: Record<WriterAuditFinding["category"], string> = {
  claims: "Claims",
  citations: "Citations",
  consistency: "Consistency",
  research: "Live results",
};

export default function WriterReviewDialog({
  docId,
  open,
  onOpenChange,
  canEdit,
  onNavigate,
  onInsert,
  onInsertCitation,
}: {
  docId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  canEdit: boolean;
  onNavigate: (path: string, line: number) => void;
  onInsert: (latex: string) => void;
  onInsertCitation: (key: string, path: string, line: number) => void;
}) {
  const [tab, setTab] = useState<ReviewTab>("findings");
  const [filter, setFilter] = useState<FindingFilter>("all");
  const audit = useQuery({
    queryKey: ["writer-audit", docId],
    queryFn: () => api.writerAudit(docId),
    enabled: open,
  });
  const research = useQuery({
    queryKey: ["writer-research-objects", docId],
    queryFn: () => api.writerResearchObjects(docId),
    enabled: open,
  });
  const findings = useMemo(
    () =>
      (audit.data?.findings ?? []).filter(
        (finding) => filter === "all" || finding.category === filter,
      ),
    [audit.data?.findings, filter],
  );
  const refreshing = audit.isFetching || research.isFetching;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex h-[calc(100dvh-0.5rem)] flex-col gap-0 overflow-hidden p-0 sm:h-[min(47rem,88vh)] sm:max-w-5xl">
        <DialogHeader className="shrink-0 border-b border-border px-4 py-3 sm:px-5 sm:py-4">
          <div className="flex flex-wrap items-start justify-between gap-3 pr-8 sm:gap-4">
            <div>
              <DialogTitle className="flex items-center gap-2">
                <ShieldCheck className="size-4 text-moss" />
                Manuscript review
              </DialogTitle>
              <DialogDescription className="mt-1">
                Traceable checks across every project file, source and pinned result.
              </DialogDescription>
            </div>
            <Button
              variant="outline"
              size="sm"
              className="h-8 rounded-full"
              disabled={refreshing}
              onClick={() => {
                void audit.refetch();
                void research.refetch();
              }}
            >
              <RefreshCw className={cn("size-3.5", refreshing && "animate-spin")} />
              Run again
            </Button>
          </div>
        </DialogHeader>

        <div className="grid min-h-0 flex-1 grid-rows-[auto_minmax(0,1fr)] md:grid-cols-[15rem_minmax(0,1fr)] md:grid-rows-1">
          <aside className="border-b border-border bg-secondary/20 p-2.5 md:border-b-0 md:border-r md:p-3">
            <div className="rounded-xl border border-border bg-card px-3 py-2.5 md:rounded-2xl md:p-3.5">
              <p className="font-mono text-[0.59375rem] uppercase tracking-[0.18em] text-muted-foreground">
                Readiness
              </p>
              <div className="mt-1.5 flex items-end justify-between gap-3 md:mt-2">
                <span className="font-serif text-3xl leading-none text-foreground md:text-4xl">
                  {audit.data?.score ?? "—"}
                </span>
                <span className="pb-0.5 text-[0.6875rem] text-muted-foreground">
                  {audit.data?.files_checked ?? 0} files checked
                </span>
              </div>
              <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-secondary md:mt-3">
                <div
                  className="h-full rounded-full bg-moss-surface transition-[width]"
                  style={{ width: `${audit.data?.score ?? 0}%` }}
                />
              </div>
            </div>

            <nav className="mt-2 grid grid-cols-2 gap-1 md:mt-3 md:block md:space-y-1">
              <button
                type="button"
                onClick={() => setTab("findings")}
                className={cn(
                  "flex w-full cursor-pointer items-center justify-between rounded-xl px-3 py-2 text-left text-[0.75rem]",
                  tab === "findings"
                    ? "bg-accent font-medium text-foreground"
                    : "text-muted-foreground hover:bg-card hover:text-foreground",
                )}
              >
                <span className="flex items-center gap-2">
                  <BookCheck className="size-3.5" /> Findings
                </span>
                <span className="font-mono text-[0.625rem]">
                  {audit.data?.findings.length ?? 0}
                </span>
              </button>
              <button
                type="button"
                onClick={() => setTab("research")}
                className={cn(
                  "flex w-full cursor-pointer items-center justify-between rounded-xl px-3 py-2 text-left text-[0.75rem]",
                  tab === "research"
                    ? "bg-accent font-medium text-foreground"
                    : "text-muted-foreground hover:bg-card hover:text-foreground",
                )}
              >
                <span className="flex items-center gap-2">
                  <Database className="size-3.5" /> Research objects
                </span>
                <span className="font-mono text-[0.625rem]">
                  {research.data?.length ?? 0}
                </span>
              </button>
            </nav>
          </aside>

          <div className="min-h-0 overflow-y-auto p-4">
            {tab === "findings" ? (
              <>
                <div className="mb-3 flex flex-wrap gap-1.5">
                  {(["all", "claims", "citations", "consistency", "research"] as const).map(
                    (category) => (
                      <button
                        key={category}
                        type="button"
                        onClick={() => setFilter(category)}
                        className={cn(
                          "cursor-pointer rounded-full border px-3 py-1 text-[0.6875rem] transition-colors",
                          filter === category
                            ? "border-primary bg-primary text-primary-foreground"
                            : "border-border bg-card text-muted-foreground hover:border-moss/40 hover:text-foreground",
                        )}
                      >
                        {category === "all" ? "All" : categoryLabels[category]}
                        {category !== "all" ? ` · ${audit.data?.counts[category] ?? 0}` : ""}
                      </button>
                    ),
                  )}
                </div>
                {audit.isLoading ? (
                  <div className="grid h-52 place-items-center text-[0.75rem] text-muted-foreground">
                    Checking the manuscript project…
                  </div>
                ) : findings.length === 0 ? (
                  <div className="grid h-52 place-items-center rounded-2xl border border-dashed border-border text-center">
                    <div>
                      <CheckCircle2 className="mx-auto size-5 text-moss" />
                      <p className="mt-2 text-[0.8125rem] font-medium text-foreground">
                        No findings in this view
                      </p>
                    </div>
                  </div>
                ) : (
                  <ul className="space-y-2">
                    {findings.map((finding, index) => (
                      <li
                        key={`${finding.id}:${finding.category}:${finding.path}:${finding.line}:${index}`}
                        className="rounded-2xl border border-border bg-card p-3.5"
                      >
                        <div className="flex items-start gap-3">
                          <span
                            className={cn(
                              "mt-0.5 grid size-7 shrink-0 place-items-center rounded-full",
                              finding.severity === "error"
                                ? "bg-destructive/10 text-destructive"
                                : finding.severity === "warning"
                                  ? "bg-amber-500/10 text-amber-700"
                                  : "bg-accent text-moss",
                            )}
                          >
                            <AlertCircle className="size-3.5" />
                          </span>
                          <div className="min-w-0 flex-1">
                            <div className="flex flex-wrap items-center justify-between gap-2">
                              <p className="text-[0.8125rem] font-medium text-foreground">
                                {finding.title}
                              </p>
                              <button
                                type="button"
                                onClick={() => {
                                  onNavigate(finding.path, finding.line);
                                  onOpenChange(false);
                                }}
                                className="inline-flex cursor-pointer items-center gap-1 font-mono text-[0.59375rem] text-moss hover:underline"
                              >
                                <FileCode2 className="size-3" />
                                {finding.path}:{finding.line}
                              </button>
                            </div>
                            <p className="mt-1 text-[0.71875rem] leading-relaxed text-muted-foreground">
                              {finding.detail}
                            </p>
                            {finding.snippet ? (
                              <p className="mt-2 line-clamp-2 rounded-lg bg-secondary/50 px-2.5 py-2 font-mono text-[0.625rem] leading-relaxed text-muted-foreground">
                                {finding.snippet}
                              </p>
                            ) : null}
                            {canEdit && finding.suggestion_keys.length > 0 ? (
                              <div className="mt-2 flex flex-wrap items-center gap-1.5">
                                <span className="text-[0.65625rem] text-muted-foreground">Possible sources</span>
                                {finding.suggestion_keys.map((key) => (
                                  <button
                                    key={key}
                                    type="button"
                                    onClick={() => {
                                      onInsertCitation(key, finding.path, finding.line);
                                      onOpenChange(false);
                                    }}
                                    className="inline-flex cursor-pointer items-center gap-1 rounded-full border border-border px-2 py-1 font-mono text-[0.59375rem] text-moss hover:border-moss/40"
                                  >
                                    <Link2 className="size-2.5" /> {key}
                                  </button>
                                ))}
                              </div>
                            ) : null}
                          </div>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </>
            ) : research.data?.length ? (
              <ul className="grid gap-3 md:grid-cols-2">
                {research.data.map((object) => {
                  const stale = object.latest_dataset_version > object.dataset_version;
                  const values = Object.entries(object.result)
                    .filter(([, value]) => typeof value === "number")
                    .slice(0, 4);
                  return (
                    <li key={object.id} className="flex flex-col rounded-2xl border border-border bg-card p-4">
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <p className="text-[0.8125rem] font-medium text-foreground">{object.name}</p>
                          <p className="mt-0.5 font-mono text-[0.59375rem] uppercase tracking-[0.18em] text-muted-foreground">
                            {object.kind} · dataset v{object.dataset_version}
                          </p>
                        </div>
                        <span
                          className={cn(
                            "rounded-full px-2 py-1 font-mono text-[0.5625rem] uppercase tracking-[0.18em]",
                            stale ? "bg-amber-500/10 text-amber-700" : "bg-accent text-moss",
                          )}
                        >
                          {stale ? `v${object.latest_dataset_version} available` : "current"}
                        </span>
                      </div>
                      {values.length > 0 ? (
                        <dl className="mt-3 grid grid-cols-2 gap-2">
                          {values.map(([key, value]) => (
                            <div key={key} className="rounded-xl bg-secondary/50 px-2.5 py-2">
                              <dt className="truncate font-mono text-[0.5625rem] uppercase tracking-[0.18em] text-muted-foreground">{key}</dt>
                              <dd className="mt-0.5 text-[0.8125rem] font-medium text-foreground">
                                {Number(value).toLocaleString(undefined, { maximumSignificantDigits: 5 })}
                              </dd>
                            </div>
                          ))}
                        </dl>
                      ) : null}
                      <p className="mt-3 flex-1 text-[0.6875rem] leading-relaxed text-muted-foreground">
                        Inserts a pinned LaTeX block. The review catches it when a newer dataset version exists.
                      </p>
                      <Button
                        size="sm"
                        className="mt-3 h-8 rounded-full"
                        disabled={!canEdit}
                        onClick={() => {
                          onInsert(`\n${object.latex}\n`);
                          onOpenChange(false);
                        }}
                      >
                        <Database className="size-3.5" />
                        {canEdit ? "Insert live result" : "View only"}
                      </Button>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <div className="grid h-64 place-items-center rounded-2xl border border-dashed border-border px-8 text-center">
                <div>
                  <Database className="mx-auto size-5 text-moss" />
                  <p className="mt-2 text-[0.8125rem] font-medium text-foreground">No project analyses yet</p>
                  <p className="mt-1 max-w-sm text-[0.71875rem] leading-relaxed text-muted-foreground">
                    Create a reproducible analysis in the Data Hub and link this manuscript to the same project.
                  </p>
                </div>
              </div>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
