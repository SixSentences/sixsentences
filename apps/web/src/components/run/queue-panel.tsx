"use client";

import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  HelpCircle,
  Loader2,
  Quote,
  XCircle,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useQueue } from "@/hooks/queries";
import { api } from "@/lib/api";
import type { QueueItem, QueuePage, Verdict } from "@/lib/types";
import { cn } from "@/lib/utils";

type Draft = { verdict: Verdict; reason: string };

const PAGE_SIZE = 25;

const VERDICT_OPTIONS: {
  verdict: Verdict;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  activeClass: string;
}[] = [
  {
    verdict: "include",
    label: "Include",
    icon: CheckCircle2,
    activeClass: "border-moss bg-accent text-moss",
  },
  {
    verdict: "unsure",
    label: "Still unsure",
    icon: HelpCircle,
    activeClass:
      "border-amber-400 bg-amber-50 text-amber-800 dark:border-amber-300/35 dark:bg-amber-300/10 dark:text-amber-200",
  },
  {
    verdict: "exclude",
    label: "Exclude",
    icon: XCircle,
    activeClass: "border-destructive/50 bg-destructive/5 text-destructive",
  },
];

function QueueRow({
  item,
  draft,
  onDraft,
}: {
  item: QueueItem;
  draft: Draft | undefined;
  onDraft: (workId: string, draft: Draft | undefined) => void;
}) {
  return (
    <div className="rounded-xl border border-border bg-card px-4 py-3.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[0.875rem] font-medium leading-snug">
            {item.title ?? item.work_id}
          </p>
          <p className="mt-0.5 font-mono text-[0.6875rem] text-muted-foreground">
            {item.work_id} · {item.reviewer}
          </p>
        </div>
        <Badge
          variant="outline"
          className="shrink-0 rounded-full border-border bg-secondary/60 text-[0.65625rem] text-muted-foreground"
        >
          {item.stage === "full_text" ? "full text" : "title/abstract"}
        </Badge>
      </div>

      {item.model_reason && (
        <p className="mt-2.5 text-[0.8125rem] leading-relaxed text-muted-foreground">
          <span className="font-medium text-foreground/80">Automated review: </span>
          {item.model_reason}
        </p>
      )}
      {item.evidence && (
        <div className="mt-2 flex gap-2 rounded-lg border-l-2 border-moss/40 bg-accent/50 px-3 py-2">
          <Quote className="mt-0.5 size-3.5 shrink-0 text-moss" />
          <p className="text-[0.78125rem] italic leading-relaxed text-accent-foreground">
            “{item.evidence}”
          </p>
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        {VERDICT_OPTIONS.map(({ verdict, label, icon: Icon, activeClass }) => (
          <button
            key={verdict}
            type="button"
            onClick={() =>
              onDraft(
                item.work_id,
                draft?.verdict === verdict ? undefined : { verdict, reason: draft?.reason ?? "" },
              )
            }
            className={cn(
              "inline-flex h-8 cursor-pointer items-center gap-1.5 rounded-full border px-3 text-[0.78125rem] font-medium transition-colors",
              draft?.verdict === verdict
                ? activeClass
                : "border-border bg-background text-muted-foreground hover:text-foreground",
            )}
          >
            <Icon className="size-3.5" />
            {label}
          </button>
        ))}
        {draft && (
          <Input
            value={draft.reason}
            onChange={(event) =>
              onDraft(item.work_id, { ...draft, reason: event.target.value })
            }
            placeholder="Reason (goes into the audit log)…"
            className="h-8 min-w-[12.5rem] flex-1 rounded-full text-[0.78125rem]"
          />
        )}
      </div>
    </div>
  );
}

/**
 * The human screening queue: every work the ensemble is UNSURE about,
 * with the model's reasoning and evidence quote. Verdicts are batched and
 * recorded as `human:<email>` decisions that supersede the model's.
 */
export default function QueuePanel({ runId }: { runId: number }) {
  const [pageIndex, setPageIndex] = useState(0);
  const offset = pageIndex * PAGE_SIZE;
  const { data: queuePage, isLoading, isFetching } = useQueue(
    runId,
    true,
    PAGE_SIZE,
    offset,
  );
  const queue = queuePage?.items ?? [];
  const total = queuePage?.total ?? 0;
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const queryClient = useQueryClient();

  useEffect(() => {
    if (total > 0 && offset >= total) {
      setPageIndex(Math.max(0, Math.ceil(total / PAGE_SIZE) - 1));
    }
  }, [offset, total]);

  const submit = useMutation({
    mutationFn: () =>
      api.submitDecisions(
        runId,
        Object.entries(drafts).map(([workId, draft]) => ({
          work_id: workId,
          verdict: draft.verdict,
          reason: draft.reason,
        })),
      ),
    onMutate: async () => {
      const queryKey = ["queue", String(runId), PAGE_SIZE, offset] as const;
      const submittedIds = new Set(Object.keys(drafts));
      await queryClient.cancelQueries({ queryKey: ["queue", String(runId)] });
      const previous = queryClient.getQueryData<QueuePage>(queryKey);
      queryClient.setQueryData<QueuePage>(queryKey, (current) => {
        if (!current) return current;
        return {
          ...current,
          items: current.items.filter((item) => !submittedIds.has(item.work_id)),
          total: Math.max(0, current.total - submittedIds.size),
        };
      });
      return { previous, queryKey };
    },
    onSuccess: (result) => {
      toast.success(
        `${result.recorded} decision${result.recorded === 1 ? "" : "s"} recorded.`,
      );
      setDrafts({});
      void queryClient.invalidateQueries({ queryKey: ["queue", String(runId)] });
      void queryClient.invalidateQueries({ queryKey: ["decisions", String(runId)] });
      void queryClient.invalidateQueries({ queryKey: ["works", String(runId)] });
      void queryClient.invalidateQueries({ queryKey: ["run-events", String(runId)] });
      void queryClient.invalidateQueries({ queryKey: ["run", String(runId)] });
    },
    onError: (error, _variables, context) => {
      if (context?.previous !== undefined) {
        queryClient.setQueryData(context.queryKey, context.previous);
      }
      toast.error(error instanceof Error ? error.message : "Could not record decisions.");
    },
  });

  const draftCount = Object.keys(drafts).length;

  if (isLoading) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 3 }).map((_, index) => (
          <Skeleton key={index} className="h-28 w-full rounded-xl" />
        ))}
      </div>
    );
  }

  if (total === 0) {
    return (
      <p className="rounded-xl border border-dashed border-border px-4 py-8 text-center text-[0.8125rem] text-muted-foreground">
        Nothing awaits your review. The queue holds works the screening
        ensemble is unsure about.
      </p>
    );
  }

  return (
    <div>
      <div className="mb-3 flex items-center justify-between gap-3">
        <p className="text-[0.78125rem] text-muted-foreground">
          <span className="font-medium text-foreground">{total}</span> work
          {total === 1 ? "" : "s"}{" "}
          awaiting your verdict. Your decision
          supersedes the automated review.
        </p>
        {draftCount > 0 && (
          <Button
            size="sm"
            onClick={() => submit.mutate()}
            disabled={submit.isPending}
            className="rounded-full"
          >
            {submit.isPending ? (
              <>
                <Loader2 className="size-3.5 animate-spin" /> Recording…
              </>
            ) : (
              `Submit ${draftCount} decision${draftCount === 1 ? "" : "s"}`
            )}
          </Button>
        )}
      </div>
      <div className={cn("space-y-2 transition-opacity", isFetching && "opacity-60")}>
        {queue.map((item) => (
          <QueueRow
            key={item.work_id}
            item={item}
            draft={drafts[item.work_id]}
            onDraft={(workId, draft) =>
              setDrafts((current) => {
                const next = { ...current };
                if (draft) next[workId] = draft;
                else delete next[workId];
                return next;
              })
            }
          />
        ))}
      </div>
      {total > PAGE_SIZE && (
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-border pt-3">
          <p className="font-mono text-[0.6875rem] tabular-nums text-muted-foreground">
            {offset + 1}–{Math.min(offset + queue.length, total)} of {total}
          </p>
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="rounded-full"
              onClick={() => setPageIndex((current) => Math.max(0, current - 1))}
              disabled={pageIndex === 0 || isFetching}
              aria-label="Previous review queue page"
            >
              <ChevronLeft className="size-3.5" />
              Previous
            </Button>
            <span className="min-w-16 text-center font-mono text-[0.6875rem] tabular-nums text-muted-foreground">
              {pageIndex + 1} / {Math.ceil(total / PAGE_SIZE)}
            </span>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="rounded-full"
              onClick={() => setPageIndex((current) => current + 1)}
              disabled={offset + queue.length >= total || isFetching}
              aria-label="Next review queue page"
            >
              Next
              <ChevronRight className="size-3.5" />
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
