"use client";

import { ArrowRight } from "lucide-react";

import { formatNumber } from "@/lib/format";
import type { PrismaCounts } from "@/lib/types";
import { cn } from "@/lib/utils";

function FlowBox({
  value,
  label,
  tone = "main",
}: {
  value: number;
  label: string;
  tone?: "main" | "side" | "final";
}) {
  return (
    <div
      className={cn(
        "flex min-h-[4.25rem] min-w-0 flex-col justify-center rounded-xl border px-3 py-2.5",
        tone === "main" && "border-border bg-card",
        tone === "side" && "border-dashed border-border bg-secondary/50",
        tone === "final" && "border-moss/40 bg-accent",
      )}
    >
      <span
        className={cn(
          "font-mono text-[1.0625rem] font-medium tabular-nums leading-tight",
          tone === "final" ? "text-moss" : "text-foreground",
        )}
      >
        {formatNumber(value)}
      </span>
      <span className="line-clamp-2 text-[0.6875rem] leading-snug text-muted-foreground">
        {label}
      </span>
    </div>
  );
}

function FlowRow({
  section,
  main,
  side,
}: {
  section: string;
  main: React.ReactNode;
  side?: React.ReactNode;
}) {
  return (
    <div className="grid grid-cols-[100px_minmax(0,1fr)] items-center gap-2">
      <span className="min-w-0 whitespace-nowrap font-mono text-[0.53125rem] uppercase leading-tight tracking-[0.11em] text-muted-foreground/80">
        {section}
      </span>
      <div className="grid grid-cols-[minmax(0,1fr)_0.875rem_minmax(0,1fr)] items-center gap-1.5">
        {main}
        {side ? (
          <>
            <ArrowRight className="size-3.5 text-muted-foreground/50" />
            {side}
          </>
        ) : (
          <span className="col-span-2" />
        )}
      </div>
    </div>
  );
}

/**
 * The PRISMA 2020 flow as a compact, honest diagram — identification down to
 * studies included, exclusions branching right. Sections that didn't run
 * (no acquisition / no full-text pass) are simply absent, never zero-padded.
 */
export default function PrismaFlow({ prisma }: { prisma: PrismaCounts }) {
  const hasRetrieval = prisma.reports_sought_for_retrieval > 0;
  const hasEligibility = prisma.reports_assessed_for_eligibility > 0;
  const advanced = prisma.included + prisma.records_unsure;

  return (
    <div className="space-y-2.5">
      <FlowRow
        section="Identification"
        main={
          <FlowBox
            value={prisma.records_identified}
            label={
              prisma.citation_identified
                ? `records identified · ${formatNumber(prisma.citation_identified)} via citation search`
                : "records identified"
            }
          />
        }
        side={
          <FlowBox
            value={prisma.duplicates_removed}
            label={
              prisma.companion_reports_merged
                ? `duplicates removed · ${formatNumber(prisma.companion_reports_merged)} companion reports`
                : "duplicates removed"
            }
            tone="side"
          />
        }
      />
      {prisma.records_screened > 0 && (
        <FlowRow
          section="Screening"
          main={<FlowBox value={prisma.records_screened} label="records screened" />}
          side={
            <FlowBox
              value={prisma.records_excluded}
              label="records excluded"
              tone="side"
            />
          }
        />
      )}
      <FlowRow
        section="Advanced"
        main={
          <FlowBox
            value={advanced}
            label={
              prisma.records_unsure > 0
                ? `advanced after title/abstract · ${formatNumber(prisma.records_unsure)} unsure`
                : "advanced after title/abstract"
            }
            tone={hasRetrieval ? "main" : "final"}
          />
        }
        side={
          prisma.retracted_flagged > 0 ? (
            <FlowBox value={prisma.retracted_flagged} label="retracted, flagged" tone="side" />
          ) : undefined
        }
      />
      {hasRetrieval && (
        <FlowRow
          section="Retrieval"
          main={
            <FlowBox
              value={prisma.reports_sought_for_retrieval}
              label="reports sought (open access)"
            />
          }
          side={
            <FlowBox
              value={prisma.reports_not_retrieved}
              label="reports not retrieved"
              tone="side"
            />
          }
        />
      )}
      {hasEligibility && (
        <>
          <FlowRow
            section="Eligibility"
            main={
              <FlowBox
                value={prisma.reports_assessed_for_eligibility}
                label="assessed on full text"
              />
            }
            side={
              <FlowBox
                value={prisma.reports_excluded_fulltext}
                label="excluded on full text"
                tone="side"
              />
            }
          />
          <FlowRow
            section="Studies"
            main={<FlowBox value={prisma.studies_included} label="studies included" tone="final" />}
          />
        </>
      )}
    </div>
  );
}
