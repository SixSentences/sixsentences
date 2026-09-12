"use client";

import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, Loader2, RefreshCw, ShieldCheck } from "lucide-react";
import { toast } from "sonner";

import PublicWebSearchApproval from "@/components/search/public-web-search-approval";
import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useProtocol } from "@/hooks/queries";
import { api } from "@/lib/api";
import type { Protocol, RunDetail } from "@/lib/types";
import { cn } from "@/lib/utils";

function CriteriaList({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div>
      <p className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
        {title}
      </p>
      <ul className="mt-1.5 space-y-1">
        {items.map((item, index) => (
          <li key={index} className="flex gap-2 text-[0.84375rem] leading-relaxed">
            <span className="text-moss">·</span>
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ProtocolBody({ protocol }: { protocol: Protocol }) {
  return (
    <div className="space-y-4">
      <div>
        <p className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
          Boolean query
        </p>
        <pre className="mt-1.5 overflow-x-auto rounded-lg bg-secondary/70 px-3 py-2.5 font-mono text-[0.78125rem] leading-relaxed whitespace-pre-wrap break-words">
          {protocol.query_string}
        </pre>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <CriteriaList title="Include when" items={protocol.inclusion_criteria} />
        <CriteriaList title="Exclude when" items={protocol.exclusion_criteria} />
      </div>
      <div className="flex flex-wrap gap-x-5 gap-y-1 text-[0.75rem] text-muted-foreground">
        {(protocol.year_from || protocol.year_to) && (
          <span>
            Years {protocol.year_from ?? "…"} to {protocol.year_to ?? "…"}
          </span>
        )}
        {protocol.peer_reviewed_only && <span>Peer-reviewed only</span>}
        <span>Languages: {protocol.languages.join(", ")}</span>
        <span>Version {protocol.version}</span>
      </div>
    </div>
  );
}

/**
 * The run's review protocol. At the human gate it becomes editable and asks
 * for approval; afterwards it collapses into a quiet, expandable record.
 */
export default function ProtocolCard({ run }: { run: RunDetail }) {
  const gated = run.status === "awaiting_protocol_approval";
  const { data, isLoading } = useProtocol(run.id, run.status !== "pending");
  const queryClient = useQueryClient();
  const [inclusion, setInclusion] = useState("");
  const [exclusion, setExclusion] = useState("");
  const [query, setQuery] = useState("");
  const [webSearchPublicDataConfirmed, setWebSearchPublicDataConfirmed] =
    useState(false);
  const [open, setOpen] = useState(false);

  const protocol = data?.protocol;
  const webSearchConfirmationRequired = gated && run.config.web_search;
  const isGerman = run.config.language === "de";

  useEffect(() => {
    if (protocol && gated) {
      setInclusion(protocol.inclusion_criteria.join("\n"));
      setExclusion(protocol.exclusion_criteria.join("\n"));
      setQuery(protocol.query_string);
      setWebSearchPublicDataConfirmed(false);
    }
  }, [protocol, gated]);

  const regenerate = useMutation({
    mutationFn: () => api.regenerateProtocol(run.id),
    onMutate: () => setWebSearchPublicDataConfirmed(false),
    onSuccess: () => {
      toast.success("A fresh protocol draft is ready.");
      void queryClient.invalidateQueries({ queryKey: ["protocol", run.id] });
      void queryClient.invalidateQueries({ queryKey: ["run-events", run.id] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Regenerate failed."),
  });

  const approve = useMutation({
    mutationFn: () => {
      const edits: Parameters<typeof api.approveProtocol>[1] = {};
      if (!protocol) return api.approveProtocol(run.id, edits);
      const lines = (value: string) =>
        value
          .split("\n")
          .map((line) => line.trim())
          .filter(Boolean);
      const nextInclusion = lines(inclusion);
      const nextExclusion = lines(exclusion);
      if (nextInclusion.join("\n") !== protocol.inclusion_criteria.join("\n"))
        edits.inclusion_criteria = nextInclusion;
      if (nextExclusion.join("\n") !== protocol.exclusion_criteria.join("\n"))
        edits.exclusion_criteria = nextExclusion;
      if (query.trim() && query.trim() !== protocol.query_string)
        edits.query_string = query.trim();
      if (webSearchConfirmationRequired && webSearchPublicDataConfirmed)
        edits.web_search_public_data_confirmed = true;
      return api.approveProtocol(run.id, edits);
    },
    onSuccess: () => {
      toast.success("Protocol approved. The search continues.");
      void queryClient.invalidateQueries({ queryKey: ["run", run.id] });
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
      void queryClient.invalidateQueries({ queryKey: ["protocol", run.id] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Approval failed."),
  });

  if (isLoading || !protocol) return null;

  if (gated) {
    return (
      <div className="rounded-2xl border border-amber-200 bg-amber-50/40 dark:border-amber-300/25 dark:bg-amber-300/10">
        <div className="flex items-center gap-2.5 border-b border-amber-200/70 px-5 py-3">
          <ShieldCheck className="size-4 text-amber-700" />
          <span className="text-[0.84375rem] font-medium text-amber-900">
            Human gate: approve the protocol before retrieval starts
          </span>
          {protocol.synthesized_by === "heuristic" && (
            <span className="ml-auto rounded-full bg-amber-100 px-2 py-0.5 font-mono text-[0.59375rem] uppercase tracking-[0.18em] text-amber-900">
              review before continuing
            </span>
          )}
        </div>
        <div className="space-y-4 px-5 py-4">
          <div>
            <Label className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
              Boolean query
            </Label>
            <Input
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setWebSearchPublicDataConfirmed(false);
              }}
              className="mt-1.5 rounded-lg font-mono text-[0.78125rem]"
            />
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <Label className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
                Include when (one per line)
              </Label>
              <Textarea
                value={inclusion}
                onChange={(event) => {
                  setInclusion(event.target.value);
                  setWebSearchPublicDataConfirmed(false);
                }}
                rows={5}
                className="mt-1.5 rounded-lg text-[0.8125rem]"
              />
            </div>
            <div>
              <Label className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
                Exclude when (one per line)
              </Label>
              <Textarea
                value={exclusion}
                onChange={(event) => {
                  setExclusion(event.target.value);
                  setWebSearchPublicDataConfirmed(false);
                }}
                rows={5}
                className="mt-1.5 rounded-lg text-[0.8125rem]"
              />
            </div>
          </div>
          {webSearchConfirmationRequired && (
            <div
              data-testid="protocol-web-search-public-data-confirmation"
            >
              <PublicWebSearchApproval
                language={isGerman ? "de" : "en"}
                query={run.question}
                confirmed={webSearchPublicDataConfirmed}
                onConfirmedChange={setWebSearchPublicDataConfirmed}
              />
            </div>
          )}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-[0.75rem] text-muted-foreground">
              Every draft and edit is kept as its own protocol version in the
              audit trail.
            </p>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                onClick={() => regenerate.mutate()}
                disabled={regenerate.isPending || approve.isPending}
                className="rounded-full"
              >
                {regenerate.isPending ? (
                  <>
                    <Loader2 className="size-4 animate-spin" /> Regenerating…
                  </>
                ) : (
                  <>
                    <RefreshCw className="size-4" /> Regenerate
                  </>
                )}
              </Button>
              <Button
                onClick={() => approve.mutate()}
                disabled={
                  approve.isPending ||
                  regenerate.isPending ||
                  !query.trim() ||
                  (webSearchConfirmationRequired &&
                    !webSearchPublicDataConfirmed)
                }
                className="rounded-full"
              >
                {approve.isPending ? (
                  <>
                    <Loader2 className="size-4 animate-spin" /> Approving…
                  </>
                ) : (
                  "Approve & continue"
                )}
              </Button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <div className="rounded-2xl border border-border bg-card">
        <CollapsibleTrigger className="flex w-full cursor-pointer items-center justify-between px-5 py-3 text-left">
          <span className="flex items-center gap-2.5">
            <ShieldCheck className="size-4 text-moss" />
            <span className="text-[0.84375rem] font-medium">Review protocol</span>
            <span className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
              v{protocol.version}
            </span>
          </span>
          <ChevronDown
            className={cn(
              "size-4 text-muted-foreground transition-transform",
              open && "rotate-180",
            )}
          />
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="border-t border-border/70 px-5 py-4">
            <ProtocolBody protocol={protocol} />
          </div>
        </CollapsibleContent>
      </div>
    </Collapsible>
  );
}
