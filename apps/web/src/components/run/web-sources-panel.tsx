"use client";

import { ExternalLink, Globe } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useWebSources } from "@/hooks/queries";

/** Grey-literature web sources — deliberately separate from the academic flow. */
export default function WebSourcesPanel({ runId }: { runId: number }) {
  const { data: sources, isLoading } = useWebSources(runId);

  if (isLoading) return <Skeleton className="h-48 w-full rounded-xl" />;

  if (!sources || sources.length === 0) {
    return (
      <p className="rounded-xl border border-dashed border-border px-4 py-8 text-center text-[0.8125rem] text-muted-foreground">
        No web sources yet. Enable “Web sources” when starting a search to collect
        quality grey literature (reports, standards, documentation).
      </p>
    );
  }

  return (
    <div>
      <p className="mb-3 text-[0.78125rem] text-muted-foreground">
        <span className="font-medium text-foreground">{sources.length}</span>{" "}
        grey-literature sources, ranked by authority, never mixed into the
        PRISMA academic counts.
      </p>
      <div className="space-y-2">
        {sources.map((source) => (
          <a
            key={source.url}
            href={source.url}
            target="_blank"
            rel="noreferrer"
            className="group block rounded-xl border border-border bg-card px-4 py-3 transition-colors hover:border-moss/40 hover:bg-secondary/40"
          >
            <div className="flex items-start justify-between gap-3">
              <p className="min-w-0 text-[0.875rem] font-medium leading-snug group-hover:text-moss">
                {source.title || source.url}
                <ExternalLink className="mb-0.5 ml-1.5 inline size-3 text-muted-foreground/60" />
              </p>
              <Badge
                variant="outline"
                className="shrink-0 rounded-full border-border bg-secondary/60 text-[0.65625rem] text-muted-foreground"
              >
                {source.category}
              </Badge>
            </div>
            <p className="mt-1 flex items-center gap-1.5 text-[0.75rem] text-muted-foreground">
              <Globe className="size-3" />
              {source.domain}
              <span className="text-muted-foreground/50">·</span>
              quality {source.quality.toFixed(2)}
            </p>
            {source.snippet && (
              <p className="mt-1.5 line-clamp-2 text-[0.78125rem] leading-relaxed text-muted-foreground">
                {source.snippet}
              </p>
            )}
          </a>
        ))}
      </div>
    </div>
  );
}
