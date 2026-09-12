"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowRight, GitCompareArrows, Minus, Plus } from "lucide-react";

import { api, type RunRef } from "@/lib/api";
import type { RunDiffWork } from "@/lib/types";
import { cn } from "@/lib/utils";

const LIST_CAP = 6;

function WorkList({
  works,
  tone,
}: {
  works: RunDiffWork[];
  tone: "added" | "removed";
}) {
  const Icon = tone === "added" ? Plus : Minus;
  return (
    <ul className="space-y-1">
      {works.slice(0, LIST_CAP).map((work) => (
        <li key={work.work_id} className="flex items-start gap-1.5">
          <Icon
            className={cn(
              "mt-0.5 size-3 shrink-0",
              tone === "added" ? "text-moss" : "text-destructive/70",
            )}
          />
          <span className="min-w-0 text-[0.75rem] leading-snug">
            {work.title}
            {work.year ? (
              <span className="text-muted-foreground"> ({work.year})</span>
            ) : null}
          </span>
        </li>
      ))}
      {works.length > LIST_CAP ? (
        <li className="pl-4.5 font-mono text-[0.625rem] text-muted-foreground">
          +{works.length - LIST_CAP} more
        </li>
      ) : null}
    </ul>
  );
}

/** What this refined run changed against its parent version: identified
 * counts, includes gained and lost, verdicts that flipped, and the query
 * rewrite itself — search-strategy iteration, documented. */
export default function RefineDiff({
  runId,
  against,
}: {
  runId: RunRef;
  against: string;
}) {
  const { data: diff } = useQuery({
    queryKey: ["run-diff", String(runId), against],
    queryFn: () => api.runDiff(runId, against),
  });
  if (!diff) return null;

  const queryChanged =
    diff.query.run && diff.query.against && diff.query.run !== diff.query.against;
  const quiet =
    diff.identified.added === 0 &&
    diff.identified.removed === 0 &&
    diff.added_includes.length === 0 &&
    diff.removed_includes.length === 0 &&
    diff.changed_verdicts.length === 0;

  return (
    <div className="rounded-2xl border border-border bg-card px-5 py-4">
      <p className="flex items-center gap-2 font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
        <GitCompareArrows className="size-3.5 text-moss" />
        versus the previous version · {diff.against}
      </p>

      <p className="mt-2.5 text-[0.8125rem] leading-relaxed">
        <span className="font-medium">
          {diff.identified.run.toLocaleString()} records identified
        </span>{" "}
        ({diff.identified.against.toLocaleString()} before
        {diff.identified.added > 0 && (
          <span className="text-moss">
            , +{diff.identified.added.toLocaleString()} new
          </span>
        )}
        {diff.identified.removed > 0 && (
          <span className="text-destructive/80">
            , -{diff.identified.removed.toLocaleString()} no longer found
          </span>
        )}
        ).
      </p>

      {queryChanged && (
        <div className="mt-3 space-y-1 overflow-x-auto rounded-xl bg-secondary/50 px-3 py-2 font-mono text-[0.6875rem] leading-relaxed">
          <p className="text-muted-foreground line-through decoration-destructive/50">
            {diff.query.against}
          </p>
          <p className="text-foreground">{diff.query.run}</p>
        </div>
      )}

      {quiet ? (
        <p className="mt-3 text-[0.78125rem] text-muted-foreground">
          The refinement left the identified and included sets unchanged.
        </p>
      ) : (
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          {diff.added_includes.length > 0 && (
            <div>
              <p className="mb-1.5 font-mono text-[0.625rem] uppercase tracking-[0.18em] text-moss">
                includes gained ({diff.added_includes.length})
              </p>
              <WorkList works={diff.added_includes} tone="added" />
            </div>
          )}
          {diff.removed_includes.length > 0 && (
            <div>
              <p className="mb-1.5 font-mono text-[0.625rem] uppercase tracking-[0.18em] text-destructive/80">
                includes lost ({diff.removed_includes.length})
              </p>
              <WorkList works={diff.removed_includes} tone="removed" />
            </div>
          )}
        </div>
      )}

      {diff.changed_verdicts.length > 0 && (
        <div className="mt-3">
          <p className="mb-1.5 font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
            verdicts that flipped ({diff.changed_verdicts.length})
          </p>
          <ul className="space-y-1">
            {diff.changed_verdicts.slice(0, LIST_CAP).map((work) => (
              <li
                key={work.work_id}
                className="flex items-start gap-1.5 text-[0.75rem] leading-snug"
              >
                <ArrowRight className="mt-0.5 size-3 shrink-0 text-muted-foreground" />
                <span className="min-w-0">
                  {work.title}{" "}
                  <span className="font-mono text-[0.625rem] text-muted-foreground">
                    {work.from} → {work.to}
                  </span>
                </span>
              </li>
            ))}
            {diff.changed_verdicts.length > LIST_CAP ? (
              <li className="pl-4.5 font-mono text-[0.625rem] text-muted-foreground">
                +{diff.changed_verdicts.length - LIST_CAP} more
              </li>
            ) : null}
          </ul>
        </div>
      )}
    </div>
  );
}
