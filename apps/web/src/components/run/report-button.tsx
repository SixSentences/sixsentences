"use client";

import { useState } from "react";
import { FileDown, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { api } from "@/lib/api";

type Phase = "idle" | "writing" | "rendering";

/** Synthesize (or fetch the cached) report and download the branded PDF. */
export async function generateAndDownloadReport(
  runId: number,
  force = false,
): Promise<void> {
  const response = await api.generateReport(runId, force);
  const { downloadReportPdf } = await import("@/lib/report-doc");
  await downloadReportPdf(response.report);
}

export default function ReportButton({
  runId,
  variant = "outline",
  className,
}: {
  runId: number;
  variant?: "outline" | "default" | "secondary";
  className?: string;
}) {
  const [phase, setPhase] = useState<Phase>("idle");

  async function handleClick() {
    if (phase !== "idle") return;
    try {
      setPhase("writing");
      const response = await api.generateReport(runId);
      setPhase("rendering");
      const { downloadReportPdf } = await import("@/lib/report-doc");
      await downloadReportPdf(response.report);
      toast.success("Report downloaded.");
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "The report could not be written.",
      );
    } finally {
      setPhase("idle");
    }
  }

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant={variant}
          size="sm"
          onClick={handleClick}
          disabled={phase !== "idle"}
          className={className ?? "h-8 rounded-full"}
        >
          {phase === "idle" ? (
            <FileDown className="size-4" />
          ) : (
            <Loader2 className="size-4 animate-spin" />
          )}
          {phase === "idle"
            ? "Report (PDF)"
            : phase === "writing"
              ? "Writing the report…"
              : "Rendering the PDF…"}
        </Button>
      </TooltipTrigger>
      <TooltipContent side="top" className="max-w-[17.5rem]">
        A branded PDF that sums up the whole search: synthesis, PRISMA flow,
        protocol and the included studies. Written once, then cached.
      </TooltipContent>
    </Tooltip>
  );
}
