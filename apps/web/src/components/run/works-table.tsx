"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronLeft, ChevronRight, ExternalLink, FileText, Quote } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useWorks } from "@/hooks/queries";
import { authorLine, formatNumber } from "@/lib/format";
import type { RankedWork, Verdict, WorkVerdictFilter } from "@/lib/types";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 25;
const COLLAPSED_ROW_ESTIMATE_REM = 6.1;

const VERDICT_STYLE: Record<Verdict, string> = {
  include: "bg-accent text-moss border-moss/30",
  exclude: "bg-secondary text-muted-foreground border-border",
  unsure:
    "bg-amber-50 text-amber-800 border-amber-200 dark:border-amber-300/25 dark:bg-amber-300/10 dark:text-amber-200",
};

function SignalBars({ work }: { work: RankedWork }) {
  const signals = [
    { key: "relevance", value: work.signals.relevance, weight: 0.6 },
    { key: "impact", value: work.signals.impact, weight: 0.25 },
    { key: "recency", value: work.signals.recency, weight: 0.15 },
  ];
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <div className="flex w-[4.5rem] cursor-default flex-col gap-1">
          <span className="font-mono text-[0.75rem] font-medium tabular-nums text-foreground">
            {work.score.toFixed(3)}
          </span>
          <div className="flex flex-col gap-[3px]">
            {signals.map((signal) => (
              <div key={signal.key} className="h-[3px] overflow-hidden rounded-full bg-secondary">
                <div
                  className="h-full rounded-full bg-moss-soft"
                  style={{ width: `${Math.round(signal.value * 100)}%` }}
                />
              </div>
            ))}
          </div>
        </div>
      </TooltipTrigger>
      <TooltipContent side="left" className="max-w-[16.25rem]">
        <p className="font-mono text-[0.6875rem]">{work.explanation}</p>
      </TooltipContent>
    </Tooltip>
  );
}

function WorkRow({ work, completed }: { work: RankedWork; completed: boolean }) {
  const [open, setOpen] = useState(false);
  const doiUrl = work.doi
    ? work.doi.startsWith("http")
      ? work.doi
      : `https://doi.org/${work.doi.replace(/^doi:/, "")}`
    : null;

  return (
    <div
      className={cn(
        "group rounded-xl border border-transparent transition-colors",
        open ? "border-border bg-secondary/40" : "hover:bg-secondary/40",
      )}
    >
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full cursor-pointer items-start gap-3.5 px-3 py-3 text-left"
      >
        <span className="mt-0.5 w-7 shrink-0 text-right font-mono text-[0.75rem] tabular-nums text-muted-foreground/70">
          {work.rank}
        </span>

        <span className="min-w-0 flex-1">
          <span
            className={cn(
              "block text-[0.875rem] font-medium leading-snug",
              !open && "line-clamp-2",
              work.retracted && "text-destructive/90",
            )}
          >
            {work.title}
          </span>
          <span className="mt-1 block truncate text-[0.78125rem] text-muted-foreground">
            {authorLine(work.authors)}
            {work.venue ? ` · ${work.venue}` : ""}
            {work.year ? ` · ${work.year}` : ""}
            {` · ${formatNumber(work.cited_by_count)} citations`}
          </span>
          <span className="mt-1.5 flex flex-wrap items-center gap-1.5">
            {work.verdict && (
              <Badge
                variant="outline"
                className={cn("h-5 rounded-full px-2 text-[0.65625rem]", VERDICT_STYLE[work.verdict])}
              >
                {work.verdict}
                {work.verdict_by === "human" ? " · human" : ""}
              </Badge>
            )}
            {!work.verdict && (
              <Badge
                variant="outline"
                className="h-5 rounded-full border-border px-2 text-[0.65625rem] text-muted-foreground"
              >
                not screened
              </Badge>
            )}
            {completed && work.selected && work.verdict !== "exclude" && (
              <Badge
                variant="outline"
                className="h-5 rounded-full border-moss/25 bg-accent/60 px-2 text-[0.65625rem] text-moss"
              >
                {work.verdict ? "selected result" : "selected candidate"}
              </Badge>
            )}
            {work.retracted && (
              <Badge
                variant="outline"
                className="h-5 rounded-full border-destructive/30 bg-destructive/5 px-2 text-[0.65625rem] text-destructive"
              >
                retracted
              </Badge>
            )}
            {work.oa_status && work.oa_status !== "closed" && (
              <Badge
                variant="outline"
                className="h-5 rounded-full border-border bg-background px-2 text-[0.65625rem] text-muted-foreground"
              >
                open access · {work.oa_status}
              </Badge>
            )}
            {work.work_type && work.work_type !== "article" && (
              <Badge
                variant="outline"
                className="h-5 rounded-full border-border bg-background px-2 text-[0.65625rem] text-muted-foreground"
              >
                {work.work_type}
              </Badge>
            )}
          </span>
        </span>

        <SignalBars work={work} />
        <ChevronDown
          className={cn(
            "mt-1 size-4 shrink-0 text-muted-foreground/50 transition-transform",
            open && "rotate-180",
          )}
        />
      </button>

      {open && (
        <div className="space-y-3 px-3 pb-4 pl-[3.2rem] pr-6">
          {work.abstract && (
            <p className="text-[0.8125rem] leading-relaxed text-muted-foreground">{work.abstract}</p>
          )}
          {work.verdict_reason && (
            <div className="flex gap-2 rounded-lg border-l-2 border-moss/40 bg-accent/60 px-3 py-2">
              <Quote className="mt-0.5 size-3.5 shrink-0 text-moss" />
              <p className="text-[0.78125rem] leading-relaxed text-accent-foreground">
                {work.verdict_reason}
              </p>
            </div>
          )}
          <div className="flex flex-wrap items-center gap-3 text-[0.75rem]">
            <span className="font-mono text-muted-foreground">{work.id}</span>
            {doiUrl && (
              <a
                href={doiUrl}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-moss underline decoration-moss/30 underline-offset-2 hover:decoration-moss"
              >
                DOI <ExternalLink className="size-3" />
              </a>
            )}
            {work.oa_url && (
              <a
                href={work.oa_url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-moss underline decoration-moss/30 underline-offset-2 hover:decoration-moss"
              >
                Open access <ExternalLink className="size-3" />
              </a>
            )}
            {work.pdf_url && (
              <a
                href={work.pdf_url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-moss underline decoration-moss/30 underline-offset-2 hover:decoration-moss"
              >
                PDF <FileText className="size-3" />
              </a>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * The ranked literature base, paginated. With `fitHeight` the list fills its
 * container and the page size adapts to how many rows actually fit, so the
 * results pane itself never scrolls.
 */
export default function WorksTable({
  runId,
  completed,
  fitHeight = false,
}: {
  runId: number;
  completed: boolean;
  fitHeight?: boolean;
}) {
  const [verdictFilter, setVerdictFilter] = useState<WorkVerdictFilter>("all");
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(PAGE_SIZE);
  const listRef = useRef<HTMLDivElement>(null);
  const { data, isLoading } = useWorks(
    runId,
    verdictFilter,
    true,
    pageSize,
    page * pageSize,
  );

  useEffect(() => setPage(0), [verdictFilter, pageSize]);

  // measure how many collapsed rows fit into the available height
  useEffect(() => {
    if (!fitHeight) return;
    const element = listRef.current;
    if (!element) return;
    const compute = () => {
      // Rows use rem-based spacing and type. Keep the established conservative
      // estimate proportional when the workstation-only root scale changes.
      const rootFontSize = Number.parseFloat(
        window.getComputedStyle(document.documentElement).fontSize,
      );
      const rowEstimate = COLLAPSED_ROW_ESTIMATE_REM * (
        Number.isFinite(rootFontSize) ? rootFontSize : 16
      );
      setPageSize(Math.max(3, Math.min(Math.floor(element.clientHeight / rowEstimate), 50)));
    };
    compute();
    const observer = new ResizeObserver(compute);
    observer.observe(element);
    return () => observer.disconnect();
  }, [fitHeight]);

  const pageCount = data ? Math.max(1, Math.ceil(data.total / pageSize)) : 1;
  const pageWorks = data?.works ?? [];

  return (
    <div className={cn(fitHeight && "flex h-full min-h-0 flex-col")}>
      <div className="mb-3 flex shrink-0 flex-wrap items-center gap-2">
        <div
          className="flex items-center gap-0.5 rounded-full bg-secondary/70 p-1"
          aria-label="Filter works by review decision"
        >
          {(
            [
              ["all", "All"],
              ["include", "Include"],
              ["exclude", "Exclude"],
              ["unsure", "Unsure"],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              onClick={() => setVerdictFilter(value)}
              className={cn(
                "h-7 cursor-pointer rounded-full px-3 text-[0.71875rem] font-medium transition-colors",
                verdictFilter === value
                  ? "bg-card text-foreground shadow-sm"
                  : "text-muted-foreground hover:bg-card/60 hover:text-foreground",
              )}
            >
              {label}
            </button>
          ))}
        </div>
        <p className="ml-auto text-[0.78125rem] text-muted-foreground">
          {data ? (
            <>
              <span className="font-medium text-foreground">{formatNumber(data.total)}</span>{" "}
              {verdictFilter === "all" ? "" : `${verdictFilter} `}works
              {completed && data.selected_total && verdictFilter === "all"
                ? ` · ${formatNumber(data.selected_total)} selected results`
                : ""}
              {" · ranked by relevance · impact · recency"}
            </>
          ) : (
            "Loading works…"
          )}
        </p>
      </div>

      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 5 }).map((_, index) => (
            <Skeleton key={index} className="h-20 w-full rounded-xl" />
          ))}
        </div>
      ) : data && data.works.length > 0 ? (
        <>
          <div
            ref={listRef}
            className={cn("space-y-1", fitHeight && "min-h-0 flex-1 overflow-y-auto")}
          >
            {pageWorks.map((work) => (
              <WorkRow key={work.id} work={work} completed={completed} />
            ))}
          </div>
          {pageCount > 1 && (
            <div className="mt-3 flex shrink-0 items-center justify-between">
              <p className="font-mono text-[0.6875rem] tabular-nums text-muted-foreground">
                {page * pageSize + 1}-{Math.min((page + 1) * pageSize, data.total)} of{" "}
                {formatNumber(data.total)}
              </p>
              <div className="flex items-center gap-1.5">
                <Button
                  variant="outline"
                  size="icon"
                  className="size-8 rounded-full"
                  onClick={() => setPage((value) => Math.max(0, value - 1))}
                  disabled={page === 0}
                  aria-label="Previous page"
                >
                  <ChevronLeft className="size-4" />
                </Button>
                <span className="min-w-16 text-center font-mono text-[0.71875rem] tabular-nums text-muted-foreground">
                  {page + 1} / {pageCount}
                </span>
                <Button
                  variant="outline"
                  size="icon"
                  className="size-8 rounded-full"
                  onClick={() => setPage((value) => Math.min(pageCount - 1, value + 1))}
                  disabled={page >= pageCount - 1}
                  aria-label="Next page"
                >
                  <ChevronRight className="size-4" />
                </Button>
              </div>
            </div>
          )}
        </>
      ) : (
        <p className="rounded-xl border border-dashed border-border px-4 py-8 text-center text-[0.8125rem] text-muted-foreground">
          {verdictFilter !== "all"
            ? `No works have an ${verdictFilter} decision yet.`
            : "No works were retrieved for this run."}
        </p>
      )}
    </div>
  );
}
