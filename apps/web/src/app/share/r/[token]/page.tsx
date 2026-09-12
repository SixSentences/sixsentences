"use client";

import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import {
  BookOpen,
  Check,
  FileText,
  Loader2,
  ShieldCheck,
  X,
} from "lucide-react";

import SixMark from "@/components/brand/six-mark";
import PublicLegalFooter from "@/components/public-legal-footer";
import { API_URL, api } from "@/lib/api";
import type { PublicSharedRun } from "@/lib/types";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";

const VERDICT_STYLE: Record<string, string> = {
  include: "bg-moss/10 text-moss",
  exclude: "bg-secondary text-muted-foreground",
  unsure: "bg-amber-100 text-amber-800",
};

function WorkRow({ work }: { work: PublicSharedRun["works"][number] }) {
  return (
    <article className="rounded-2xl border border-border bg-card p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[0.875rem] font-medium leading-snug text-pine">
            {work.title}
          </p>
          <p className="mt-1 text-[0.71875rem] text-muted-foreground">
            {[work.authors.slice(0, 3).join(", ") + (work.authors.length > 3 ? " et al." : ""), work.year, work.venue]
              .filter(Boolean)
              .join(" · ")}
          </p>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1.5">
          {work.verdict && (
            <span className={cn("rounded-full px-2.5 py-1 font-mono text-[0.59375rem] uppercase tracking-[0.18em]", VERDICT_STYLE[work.verdict])}>
              {work.verdict}
            </span>
          )}
          {work.retracted && (
            <span className="rounded-full bg-destructive/10 px-2.5 py-1 font-mono text-[0.59375rem] uppercase tracking-[0.18em] text-destructive">
              retracted
            </span>
          )}
        </div>
      </div>
      {work.verdict_reason && (
        <p className="mt-2 border-t border-border/70 pt-2 text-[0.71875rem] leading-relaxed text-muted-foreground">
          {work.verdict_reason}
        </p>
      )}
      <div className="mt-2 flex items-center gap-3 text-[0.65625rem] text-muted-foreground">
        <span>rank {work.rank}</span>
        <span>{work.cited_by_count.toLocaleString()} citations</span>
        {work.doi && (
          <a
            href={`https://doi.org/${work.doi}`}
            target="_blank"
            rel="noreferrer"
            className="truncate text-moss hover:underline"
          >
            doi.org/{work.doi}
          </a>
        )}
      </div>
    </article>
  );
}

export default function SharedRunPage() {
  const params = useParams<{ token: string }>();
  const token = params.token;
  const { data: record, isLoading, isError } = useQuery({
    queryKey: ["shared-run", token],
    queryFn: () => api.publicSharedRun(token),
    retry: false,
  });

  return (
    <div className="flex min-h-dvh flex-col bg-background">
      <header className="border-b border-border bg-card/60">
        <div className="mx-auto flex max-w-4xl flex-wrap items-center justify-between gap-2 px-4 py-3 sm:px-5 sm:py-4">
          <span className="flex min-w-0 items-center gap-2.5">
            <SixMark title="SixSentences_" className="h-6 w-6 text-pine" />
            <span className="truncate font-mono text-[0.625rem] tracking-[0.2em] text-pine/90 sm:text-[0.6875rem] sm:tracking-[0.24em]">
              SIXSENTENCES_
            </span>
          </span>
          <span className="flex shrink-0 items-center gap-1.5 rounded-full bg-moss/10 px-2.5 py-1 text-[0.625rem] font-medium text-moss sm:px-3 sm:text-[0.6875rem]">
            <ShieldCheck className="size-3.5" /> Shared research record
          </span>
        </div>
      </header>

      <main className="mx-auto w-full max-w-4xl flex-1 px-4 pb-16 pt-6 sm:px-5 sm:pb-20 sm:pt-8">
        {isLoading ? (
          <Loader2 className="mx-auto mt-24 size-5 animate-spin text-muted-foreground" />
        ) : isError || !record ? (
          <div className="mt-16 text-center">
            <p className="font-display text-2xl text-pine">This share link is not active</p>
            <p className="mx-auto mt-2 max-w-md text-[0.8125rem] leading-relaxed text-muted-foreground">
              The workspace revoked it or it never existed. Ask the author for a
              fresh link.
            </p>
          </div>
        ) : (
          <div className="space-y-5">
            <div>
              <p className="font-mono text-[0.625rem] uppercase tracking-[0.22em] text-moss">
                Systematic literature search
              </p>
              <h1 className="mt-2 font-display text-[2rem] leading-tight text-pine">
                {record.title}
              </h1>
              <p className="mt-2 max-w-2xl text-[0.875rem] leading-relaxed text-muted-foreground">
                {record.question}
              </p>
              <p className="mt-2 text-[0.6875rem] text-muted-foreground">
                Shared {formatDate(record.created_at)} · read-only record
              </p>
            </div>

            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {[
                [record.prisma.records_identified ?? 0, "identified"],
                [record.prisma.records_screened ?? 0, "screened"],
                [record.prisma.records_excluded ?? 0, "excluded"],
                [record.prisma.included ?? 0, "included"],
              ].map(([value, label]) => (
                <div key={label} className="rounded-2xl border border-border bg-card p-4">
                  <p className="font-mono text-xl text-pine">{Number(value).toLocaleString()}</p>
                  <p className="mt-1 text-[0.6875rem] text-muted-foreground">{label}</p>
                </div>
              ))}
            </div>

            <section className="overflow-hidden rounded-3xl border border-border bg-card">
              <div className="border-b border-border bg-secondary/40 px-5 py-3">
                <p className="flex items-center gap-2 text-[0.75rem] font-medium text-pine">
                  <FileText className="size-3.5 text-moss" /> PRISMA 2020 flow
                </p>
              </div>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={`${API_URL}/public/runs/${token}/prisma.svg`}
                alt="PRISMA 2020 flow diagram of the shared search"
                className="w-full bg-white"
              />
            </section>

            <section className="rounded-3xl border border-border bg-card p-5">
              <p className="flex items-center gap-2 text-[0.8125rem] font-medium text-pine">
                <BookOpen className="size-4 text-moss" /> Frozen protocol
              </p>
              <p className="mt-3 rounded-xl bg-secondary/50 px-3 py-2 font-mono text-[0.75rem] leading-relaxed text-pine">
                {record.query_string}
              </p>
              <div className="mt-4 grid gap-4 sm:grid-cols-2">
                <div>
                  <p className="mb-1.5 font-mono text-[0.625rem] uppercase tracking-[0.18em] text-moss">
                    Inclusion
                  </p>
                  <ul className="space-y-1">
                    {record.inclusion_criteria.map((criterion) => (
                      <li key={criterion} className="flex items-start gap-2 text-[0.75rem] leading-relaxed">
                        <Check className="mt-0.5 size-3.5 shrink-0 text-moss" /> {criterion}
                      </li>
                    ))}
                  </ul>
                </div>
                <div>
                  <p className="mb-1.5 font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
                    Exclusion
                  </p>
                  <ul className="space-y-1">
                    {record.exclusion_criteria.map((criterion) => (
                      <li key={criterion} className="flex items-start gap-2 text-[0.75rem] leading-relaxed text-muted-foreground">
                        <X className="mt-0.5 size-3.5 shrink-0" /> {criterion}
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            </section>

            <section className="rounded-3xl border border-border bg-card p-5">
              <p className="text-[0.8125rem] font-medium text-pine">Methods, as citable text</p>
              <p className="mt-2 whitespace-pre-wrap text-[0.8125rem] leading-relaxed text-muted-foreground">
                {record.methods}
              </p>
            </section>

            <section>
              <p className="mb-3 font-mono text-[0.625rem] uppercase tracking-[0.22em] text-moss">
                Every work, every decision, every reason
              </p>
              <div className="space-y-2.5">
                {record.works.map((work) => (
                  <WorkRow key={work.id} work={work} />
                ))}
              </div>
            </section>

            <p className="pt-4 text-center text-[0.6875rem] text-muted-foreground">
              Read-only record shared from a SixSentences_ workspace.
            </p>
          </div>
        )}
      </main>
      <PublicLegalFooter className="shrink-0 border-t border-border px-4 py-4" />
    </div>
  );
}
