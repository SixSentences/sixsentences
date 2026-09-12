"use client";

import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useDecisions, useWorks } from "@/hooks/queries";
import type { Verdict } from "@/lib/types";
import { cn } from "@/lib/utils";

const VERDICT_STYLE: Record<Verdict, string> = {
  include: "bg-accent text-moss border-moss/30",
  exclude: "bg-secondary text-muted-foreground border-border",
  unsure:
    "bg-amber-50 text-amber-800 border-amber-200 dark:border-amber-300/25 dark:bg-amber-300/10 dark:text-amber-200",
};

/**
 * The effective decision per work — human verdicts supersede the model's,
 * full-text passes supersede title/abstract. The audit-grade reviewed set.
 */
export default function DecisionsPanel({ runId }: { runId: number }) {
  const { data: decisions, isLoading } = useDecisions(runId);
  const { data: worksPage } = useWorks(runId, false);
  const titles = new Map(
    (worksPage?.works ?? []).map((work) => [work.id, work.title]),
  );

  if (isLoading) {
    return <Skeleton className="h-48 w-full rounded-xl" />;
  }

  if (!decisions || decisions.length === 0) {
    return (
      <p className="rounded-xl border border-dashed border-border px-4 py-8 text-center text-[0.8125rem] text-muted-foreground">
        No screening decisions yet. Enable screening when starting a search.
      </p>
    );
  }

  const ordered = [...decisions].sort((a, b) =>
    a.by === b.by ? 0 : a.by === "human" ? -1 : 1,
  );

  return (
    <div className="overflow-hidden rounded-xl border border-border">
      <Table>
        <TableHeader>
          <TableRow className="bg-secondary/50 hover:bg-secondary/50">
            <TableHead className="w-[45%]">Work</TableHead>
            <TableHead>Verdict</TableHead>
            <TableHead>Decided by</TableHead>
            <TableHead>Reason</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {ordered.map((decision) => (
            <TableRow key={decision.work_id}>
              <TableCell className="max-w-0">
                <p className="truncate text-[0.8125rem] font-medium">
                  {titles.get(decision.work_id) ?? decision.work_id}
                </p>
                <p className="font-mono text-[0.65625rem] text-muted-foreground">
                  {decision.work_id}
                </p>
              </TableCell>
              <TableCell>
                <Badge
                  variant="outline"
                  className={cn(
                    "rounded-full text-[0.65625rem]",
                    VERDICT_STYLE[decision.verdict],
                  )}
                >
                  {decision.verdict}
                </Badge>
              </TableCell>
              <TableCell className="text-[0.78125rem] text-muted-foreground">
                {decision.by === "human" ? (
                  <span className="font-medium text-foreground">
                    {decision.reviewer.replace("human:", "")}
                  </span>
                ) : (
                  <span>
                    {decision.stage === "full_text" ? "model · full text" : "model ensemble"}
                  </span>
                )}
              </TableCell>
              <TableCell className="max-w-0">
                <p className="truncate text-[0.78125rem] text-muted-foreground" title={decision.reason}>
                  {decision.reason || "—"}
                </p>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
