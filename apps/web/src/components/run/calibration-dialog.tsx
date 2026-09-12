"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Loader2, Target } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useWorks } from "@/hooks/queries";
import { api } from "@/lib/api";
import type { CalibrationReport } from "@/lib/types";
import { cn } from "@/lib/utils";

type SeedState = Record<string, boolean>; // work_id -> included (ground truth)

/**
 * Screener calibration (ASReview pattern): hand-label a handful of works you
 * know, and check whether the model screener clears the target recall on them.
 */
export default function CalibrationDialog({
  runId,
  open,
  onOpenChange,
}: {
  runId: number;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { data: worksPage } = useWorks(runId, false, open);
  const [seeds, setSeeds] = useState<SeedState>({});
  const [report, setReport] = useState<CalibrationReport | null>(null);

  const calibrate = useMutation({
    mutationFn: () =>
      api.calibrate(
        runId,
        Object.entries(seeds).map(([work_id, included]) => ({ work_id, included })),
      ),
    onSuccess: setReport,
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Calibration failed."),
  });

  const seedCount = Object.keys(seeds).length;

  function setSeed(workId: string, included: boolean) {
    setSeeds((current) => {
      const next = { ...current };
      if (current[workId] === included) delete next[workId];
      else next[workId] = included;
      return next;
    });
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) setReport(null);
      }}
    >
      <DialogContent className="flex max-h-[80vh] flex-col overflow-hidden sm:max-w-[35rem]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Target className="size-4 text-moss" /> Calibrate the screener
          </DialogTitle>
          <DialogDescription>
            Label works you know as relevant or not. The report shows whether
            the model screener clears the target recall on your ground truth.
          </DialogDescription>
        </DialogHeader>

        {report ? (
          <div className="min-h-0 space-y-4 overflow-y-auto py-2">
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 sm:gap-3">
              {[
                { label: "Recall", value: `${(report.recall * 100).toFixed(0)}%` },
                { label: "Agreement (κ)", value: report.agreement.toFixed(2) },
                { label: "Verdict", value: report.verdict.replaceAll("_", " ") },
              ].map((stat) => (
                <div
                  key={stat.label}
                  className="rounded-xl border border-border px-3 py-3 last:col-span-2 sm:px-4 sm:last:col-span-1"
                >
                  <p className="break-words font-mono text-[0.875rem] font-medium sm:text-[1rem]">
                    {stat.value}
                  </p>
                  <p className="text-[0.71875rem] text-muted-foreground">{stat.label}</p>
                </div>
              ))}
            </div>
            <p className="text-[0.8125rem] leading-relaxed text-muted-foreground">{report.note}</p>
            {report.missed.length > 0 && (
              <div className="rounded-xl border border-destructive/30 bg-destructive/5 px-4 py-3">
                <p className="text-[0.78125rem] font-medium text-destructive">
                  Missed by the screener (human-include, model-exclude):
                </p>
                <p className="mt-1 font-mono text-[0.71875rem] text-muted-foreground">
                  {report.missed.join(", ")}
                </p>
              </div>
            )}
            <Button
              variant="outline"
              className="rounded-full"
              onClick={() => setReport(null)}
            >
              Label more seeds
            </Button>
          </div>
        ) : (
          <>
            {/* a plain scroll div: radix ScrollArea's table-layout viewport
                breaks truncation and let rows overflow the dialog */}
            <div className="-mx-1 min-h-0 flex-1 overflow-y-auto px-1">
              <div className="space-y-1.5 py-1">
                {(worksPage?.works ?? []).slice(0, 60).map((work) => (
                  <div
                    key={work.id}
                    className="flex items-center gap-3 rounded-lg border border-border/70 px-3 py-2"
                  >
                    <p className="min-w-0 flex-1 truncate text-[0.8125rem]" title={work.title}>
                      {work.title}
                    </p>
                    <div className="flex shrink-0 gap-1">
                      <button
                        type="button"
                        onClick={() => setSeed(work.id, true)}
                        className={cn(
                          "cursor-pointer rounded-full border px-2.5 py-1 text-[0.71875rem] font-medium transition-colors",
                          seeds[work.id] === true
                            ? "border-moss bg-accent text-moss"
                            : "border-border text-muted-foreground hover:text-foreground",
                        )}
                      >
                        Relevant
                      </button>
                      <button
                        type="button"
                        onClick={() => setSeed(work.id, false)}
                        className={cn(
                          "cursor-pointer rounded-full border px-2.5 py-1 text-[0.71875rem] font-medium transition-colors",
                          seeds[work.id] === false
                            ? "border-destructive/50 bg-destructive/5 text-destructive"
                            : "border-border text-muted-foreground hover:text-foreground",
                        )}
                      >
                        Not relevant
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
            <div className="flex shrink-0 items-center justify-between border-t border-border pt-3">
              <p className="text-[0.78125rem] text-muted-foreground">
                {seedCount} seed{seedCount === 1 ? "" : "s"} labelled
              </p>
              <Button
                onClick={() => calibrate.mutate()}
                disabled={seedCount === 0 || calibrate.isPending}
                className="rounded-full"
              >
                {calibrate.isPending ? (
                  <>
                    <Loader2 className="size-4 animate-spin" /> Calibrating…
                  </>
                ) : (
                  "Run calibration"
                )}
              </Button>
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
