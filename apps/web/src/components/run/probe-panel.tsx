"use client";

import { type FormEvent, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Loader2, SearchCheck } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

const STATUS_BADGE: Record<string, { label: string; className: string }> = {
  include: { label: "Included", className: "bg-moss-surface/10 text-moss" },
  exclude: { label: "Excluded", className: "bg-destructive/10 text-destructive" },
  unsure: { label: "Unsure", className: "bg-amber-100 text-amber-900" },
  identified_not_screened: {
    label: "Found, not screened",
    className: "bg-secondary text-muted-foreground",
  },
  in_index_not_retrieved: {
    label: "In the index, query missed it",
    className: "bg-amber-100 text-amber-900",
  },
  outside_index: {
    label: "Outside this search",
    className: "bg-secondary text-muted-foreground",
  },
  not_found: { label: "Not found", className: "bg-secondary text-muted-foreground" },
};

/** "Why is paper X (not) in the results?" — deterministic ledger forensics. */
export default function ProbePanel({ runId }: { runId: number }) {
  const [query, setQuery] = useState("");
  const probe = useMutation({
    mutationFn: () => api.probeRun(runId, query.trim()),
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "That didn't work."),
  });
  const result = probe.data;
  const badge = result ? STATUS_BADGE[result.status] : undefined;

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (query.trim().length >= 3 && !probe.isPending) probe.mutate();
  }

  return (
    <div className="mb-4 rounded-2xl border border-border bg-card p-4">
      <p className="flex items-center gap-2 font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
        <SearchCheck className="size-3.5 text-moss" /> Check a paper
      </p>
      <form method="post" onSubmit={submit} className="mt-2.5 flex items-center gap-2">
        <Input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Paste a DOI, OpenAlex id, or title to trace it through this search"
          aria-label="Paper to trace"
          className="h-9 rounded-lg text-[0.8125rem]"
        />
        <Button
          type="submit"
          size="sm"
          disabled={query.trim().length < 3 || probe.isPending}
          className="h-9 shrink-0 rounded-full px-4"
        >
          {probe.isPending ? <Loader2 className="size-3.5 animate-spin" /> : "Trace"}
        </Button>
      </form>

      {result ? (
        <div className="mt-3 space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            {badge ? (
              <span
                className={cn(
                  "rounded-full px-2 py-0.5 font-mono text-[0.625rem] uppercase tracking-[0.18em]",
                  badge.className,
                )}
              >
                {badge.label}
              </span>
            ) : null}
            {result.resolved ? (
              <p className="min-w-0 text-[0.8125rem] font-medium leading-snug">
                {result.resolved.title}
                <span className="ml-1.5 font-mono text-[0.6875rem] font-normal text-muted-foreground">
                  {result.resolved.year ?? ""}
                </span>
              </p>
            ) : null}
          </div>
          <ul className="space-y-1.5">
            {result.steps.map((step, index) => (
              <li key={index} className="flex items-start gap-2 text-[0.78125rem] leading-relaxed">
                <span className="mt-0.5 w-24 shrink-0 font-mono text-[0.59375rem] uppercase tracking-[0.18em] text-muted-foreground/70">
                  {step.stage.replace(/_/g, " ")}
                </span>
                <span className="min-w-0">{step.detail}</span>
              </li>
            ))}
          </ul>
          {result.suggestion ? (
            <p className="rounded-xl bg-accent/60 px-3 py-2 text-[0.75rem] leading-relaxed text-foreground">
              {result.suggestion}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
