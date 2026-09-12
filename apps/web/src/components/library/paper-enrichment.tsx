"use client";

import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  CheckCircle2,
  ExternalLink,
  FileSearch,
  Loader2,
  RefreshCcw,
  SearchCheck,
  ShieldCheck,
  X,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { api, ApiError } from "@/lib/api";
import type {
  LibraryDocument,
  PaperEnrichmentJob,
  PaperEnrichmentSuggestion,
} from "@/lib/types";

function requestErrorMessage(
  error: unknown,
  isGerman: boolean,
  fallback: readonly [string, string],
): string {
  const messages: Record<string, readonly [string, string]> = {
    library_metadata_identity_conflict: [
      "Eine vorgeschlagene ID gehört bereits zu einem anderen Library-Paper.",
      "A proposed identifier already belongs to another Library paper.",
    ],
    library_metadata_revision_conflict: [
      "Das Paper wurde inzwischen geändert. Starte eine neue Prüfung.",
      "The paper changed in the meantime. Start a new check.",
    ],
    paper_enrichment_already_active: [
      "Für dieses Paper läuft bereits eine Prüfung.",
      "A metadata check is already running for this paper.",
    ],
    paper_enrichment_capacity_busy: [
      "Die Prüfkapazität ist gerade ausgelastet. Versuche es gleich noch einmal.",
      "Metadata-check capacity is busy. Try again shortly.",
    ],
    paper_enrichment_daily_limit: [
      "Dieses Paper wurde heute bereits zu oft geprüft.",
      "This paper has reached today's metadata-check limit.",
    ],
    paper_enrichment_rate_limited: [
      "Zu viele Prüfungen in kurzer Zeit. Warte bitte kurz.",
      "Too many checks in a short period. Please wait a moment.",
    ],
    paper_enrichment_request_conflict: [
      "Diese Prüfungsanfrage ist bereits an ein anderes Paper gebunden.",
      "This check request is already bound to another paper.",
    ],
    paper_enrichment_retry_limit: [
      "Diese Prüfung kann nicht erneut versucht werden. Starte eine neue Prüfung.",
      "This check cannot be retried again. Start a new check.",
    ],
    paper_enrichment_source_changed: [
      "Die Paper-Metadaten haben sich geändert. Starte eine neue Prüfung.",
      "The paper metadata changed. Start a new check.",
    ],
    paper_enrichment_strong_identity_required: [
      "Ergänze zuerst eine DOI, arXiv-ID, PMID oder PMCID.",
      "Add a DOI, arXiv ID, PMID or PMCID first.",
    ],
  };
  let code = "";
  if (error instanceof ApiError && error.detail && typeof error.detail === "object" && !Array.isArray(error.detail)) {
    const rawCode = (error.detail as Record<string, unknown>).code;
    code = typeof rawCode === "string" ? rawCode : "";
  }
  return (messages[code] ?? fallback)[isGerman ? 0 : 1];
}

function fieldLabel(field: string, isGerman: boolean): string {
  const labels: Record<string, readonly [string, string]> = {
    abstract: ["Abstract", "Abstract"],
    authors: ["Autor:innen", "Authors"],
    canonical_url: ["Kanonische URL", "Canonical URL"],
    container_title: ["Publikation / Venue", "Publication / venue"],
    doi: ["DOI", "DOI"],
    issue: ["Ausgabe", "Issue"],
    language: ["Sprache", "Language"],
    license: ["Lizenz", "License"],
    pages: ["Seiten", "Pages"],
    pdf_url: ["Öffentliches PDF", "Public PDF"],
    pmcid: ["PMCID", "PMCID"],
    pmid: ["PMID", "PMID"],
    published_at: ["Veröffentlichungsdatum", "Publication date"],
    publisher: ["Verlag", "Publisher"],
    source_url: ["Quell-URL", "Source URL"],
    subtitle: ["Untertitel", "Subtitle"],
    title: ["Titel", "Title"],
    volume: ["Band", "Volume"],
  };
  return labels[field]?.[isGerman ? 0 : 1] ?? field.replaceAll("_", " ");
}

function valueText(value: PaperEnrichmentSuggestion["value"]): string {
  return Array.isArray(value) ? value.join(" · ") : value;
}

function providerLabel(provider: string): string {
  return provider === "openalex" ? "OpenAlex + Unpaywall" : "Crossref";
}

function safeExternalUrl(value: string): string | null {
  try {
    const parsed = new URL(value);
    if (
      !["http:", "https:"].includes(parsed.protocol) ||
      !parsed.hostname ||
      parsed.username ||
      parsed.password
    ) {
      return null;
    }
    return parsed.href;
  } catch {
    return null;
  }
}

function jobFailureMessage(job: PaperEnrichmentJob, isGerman: boolean): string {
  if (job.status === "cancelled") {
    return isGerman ? "Die Suche wurde abgebrochen." : "The check was cancelled.";
  }
  const messages: Record<string, readonly [string, string]> = {
    paper_enrichment_identity_changed: [
      "Die Paper-ID hat sich geändert. Starte eine neue Prüfung.",
      "The paper identifier changed. Start a new check.",
    ],
    paper_enrichment_identity_mismatch: [
      "Die öffentliche Quelle passte nicht eindeutig zu diesem Paper. Es wurde nichts vorgeschlagen.",
      "The public record did not match this paper exactly. Nothing was proposed.",
    ],
    paper_enrichment_not_accessible: [
      "Für diese ID wurden keine öffentlich zugänglichen Metadaten gefunden.",
      "No publicly accessible metadata was found for this identifier.",
    ],
    paper_enrichment_paper_missing: [
      "Dieses Library-Paper ist nicht mehr verfügbar.",
      "This Library paper is no longer available.",
    ],
    paper_enrichment_provider_failed: [
      "Die öffentliche Metadatenquelle ist gerade nicht erreichbar.",
      "The public metadata source is temporarily unavailable.",
    ],
    paper_enrichment_source_changed: [
      "Die Paper-Metadaten haben sich geändert. Starte eine neue Prüfung.",
      "The paper metadata changed. Start a new check.",
    ],
    paper_enrichment_worker_failed: [
      "Die Prüfung konnte nicht abgeschlossen werden.",
      "The metadata check could not be completed.",
    ],
  };
  return messages[job.error?.code ?? ""]?.[isGerman ? 0 : 1] ??
    (isGerman
      ? "Die Prüfung konnte nicht abgeschlossen werden."
      : "The metadata check could not be completed.");
}

export function PaperEnrichment({
  document,
  isGerman,
  actionHost = null,
}: {
  document: LibraryDocument;
  isGerman: boolean;
  actionHost?: HTMLElement | null;
}) {
  const queryClient = useQueryClient();
  const startRequestId = useRef<string | null>(null);
  useEffect(() => {
    startRequestId.current = null;
  }, [document.id]);
  const t = (de: string, en: string) => (isGerman ? de : en);
  const queryKey = ["paper-enrichment", document.id] as const;
  const latest = useQuery({
    queryKey,
    queryFn: () => api.latestPaperEnrichment(document.id),
    refetchInterval: (query) => {
      const status = query.state.data?.job?.status;
      return status === "queued" || status === "running" ? 900 : false;
    },
  });
  const job = latest.data?.job ?? null;
  const setJob = (next: PaperEnrichmentJob) => {
    queryClient.setQueryData(queryKey, { job: next });
  };
  const start = useMutation({
    mutationFn: (requestId: string) =>
      api.startPaperEnrichment(document.id, requestId),
    onSuccess: (next) => {
      setJob(next);
      startRequestId.current = null;
    },
    onError: (error) =>
      toast.error(
        requestErrorMessage(
          error,
          isGerman,
          ["Die Metadatensuche konnte nicht gestartet werden.", "Could not start the metadata check."],
        ),
      ),
  });
  const cancel = useMutation({
    mutationFn: () => api.cancelPaperEnrichment(document.id, job!.id),
    onSuccess: setJob,
    onError: () => toast.error(t("Die Suche konnte nicht gestoppt werden.", "Could not stop the check.")),
  });
  const retry = useMutation({
    mutationFn: () => api.retryPaperEnrichment(document.id, job!.id),
    onSuccess: setJob,
    onError: (error) =>
      toast.error(
        requestErrorMessage(
          error,
          isGerman,
          ["Erneuter Versuch fehlgeschlagen.", "Could not retry the check."],
        ),
      ),
  });
  const apply = useMutation({
    mutationFn: () =>
      api.applyPaperEnrichment(document.id, job!.id, job!.source_revision),
    onSuccess: (result) => {
      setJob(result.job);
      void queryClient.invalidateQueries({ queryKey: ["library"] });
      toast.success(
        result.changed_fields.length > 0
          ? t(
              `${result.changed_fields.length} fehlende Angaben ergänzt.`,
              `${result.changed_fields.length} missing fields added.`,
            )
          : t("Es war nichts mehr zu ergänzen.", "There was nothing left to add."),
      );
    },
    onError: (error) =>
      toast.error(
        requestErrorMessage(
          error,
          isGerman,
          ["Die Vorschläge konnten nicht übernommen werden.", "Could not apply the proposals."],
        ),
      ),
  });
  const active = job?.status === "queued" || job?.status === "running";
  const startCheck = () => {
    startRequestId.current ??= `enrich:${crypto.randomUUID()}`;
    start.mutate(startRequestId.current);
  };
  const canStart = (
    !job
    || ["completed", "applied", "failed", "cancelled"].includes(job.status)
  ) && !latest.isError && !latest.isLoading;
  const primaryAction = active ? (
    <Button
      type="button"
      size="sm"
      variant="outline"
      className="h-8 rounded-full"
      disabled={cancel.isPending}
      onClick={() => cancel.mutate()}
      aria-controls="paper-enrichment"
    >
      {cancel.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <X className="size-3.5" />}
      {t("Stoppen", "Stop")}
    </Button>
  ) : canStart ? (
    <Button
      type="button"
      size="sm"
      className="h-8 rounded-full"
      disabled={start.isPending}
      onClick={startCheck}
      aria-controls="paper-enrichment"
    >
      {start.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <SearchCheck className="size-3.5" />}
      {t("Fehlende Metadaten suchen", "Find missing metadata")}
    </Button>
  ) : latest.isLoading ? (
    <Button type="button" size="sm" variant="outline" className="h-8 rounded-full" disabled>
      <Loader2 className="size-3.5 animate-spin" />
      {t("Metadatenstatus laden …", "Loading metadata status …")}
    </Button>
  ) : null;

  return (
    <section aria-labelledby="paper-enrichment" className="rounded-2xl border border-moss/25 bg-moss/[0.035] p-4 sm:p-5">
      {actionHost && primaryAction ? createPortal(primaryAction, actionHost) : null}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="max-w-2xl">
          <div className="flex items-center gap-2 text-moss">
            <FileSearch className="size-4" aria-hidden="true" />
            <h3 id="paper-enrichment" className="font-mono text-[0.625rem] uppercase tracking-[0.18em]">
              {t("Metadatenprüfung", "Metadata check")}
            </h3>
          </div>
          <p className="mt-2 text-[0.75rem] leading-relaxed text-muted-foreground">
            {t(
              "Sucht über die bereits gespeicherte DOI, arXiv-ID, PMID, PMCID oder OpenAlex-ID nach fehlenden Angaben. Titel allein werden nie zum Zuordnen verwendet.",
              "Looks for missing fields using the saved DOI, arXiv ID, PMID, PMCID or OpenAlex ID. A title alone is never used to identify the paper.",
            )}
          </p>
        </div>
        {!actionHost ? primaryAction : null}
      </div>

      {latest.isLoading ? (
        <div className="mt-4 flex items-center gap-2 text-[0.75rem] text-muted-foreground" role="status">
          <Loader2 className="size-3.5 animate-spin" />
          {t("Letzten Prüfstand laden …", "Loading the latest check …")}
        </div>
      ) : null}

      {latest.isError ? (
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-500/30 bg-amber-500/5 p-3">
          <p className="text-[0.75rem] text-foreground">
            {t("Der letzte Prüfstand konnte nicht geladen werden.", "Could not load the latest metadata check.")}
          </p>
          <Button type="button" size="sm" variant="outline" className="h-7 rounded-full" onClick={() => void latest.refetch()}>
            <RefreshCcw className="size-3.5" /> {t("Neu laden", "Reload")}
          </Button>
        </div>
      ) : null}

      {active && job ? (
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-card p-3" role="status">
          <div className="flex items-center gap-2 text-[0.75rem]">
            <Loader2 className="size-3.5 animate-spin text-moss" />
            <span>{t("Öffentliche Metadaten werden geprüft …", "Checking public metadata …")}</span>
          </div>
          {!actionHost ? (
            <Button type="button" size="sm" variant="ghost" className="h-7 rounded-full" disabled={cancel.isPending} onClick={() => cancel.mutate()}>
              <X className="size-3.5" /> {t("Stoppen", "Stop")}
            </Button>
          ) : null}
        </div>
      ) : null}

      {job?.status === "failed" || job?.status === "cancelled" ? (
        <div className="mt-4 rounded-xl border border-amber-500/30 bg-amber-500/5 p-3">
          <p className="text-[0.75rem] text-foreground">
            {jobFailureMessage(job, isGerman)}
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            {job.retry_count < 3 ? (
              <Button type="button" size="sm" variant="outline" className="h-7 rounded-full" disabled={retry.isPending || start.isPending} onClick={() => retry.mutate()}>
                {retry.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <RefreshCcw className="size-3.5" />}
                {t("Erneut versuchen", "Retry")}
              </Button>
            ) : null}
            {!actionHost ? (
              <Button type="button" size="sm" variant="ghost" className="h-7 rounded-full" disabled={retry.isPending || start.isPending} onClick={startCheck}>
                {start.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <SearchCheck className="size-3.5" />}
                {t("Neue Prüfung starten", "Start a new check")}
              </Button>
            ) : null}
          </div>
        </div>
      ) : null}

      {job?.status === "completed" ? (
        <div className="mt-4 space-y-3">
          {job.suggestions.length > 0 ? (
            <>
              <div className="flex items-center justify-between gap-3">
                <p className="text-[0.75rem] font-medium">
                  {t(
                    `${job.suggestions.length} geprüfte Vorschläge`,
                    `${job.suggestions.length} verified proposals`,
                  )}
                </p>
                <span className="inline-flex items-center gap-1 text-[0.65625rem] text-muted-foreground">
                  <ShieldCheck className="size-3.5 text-moss" />
                  {job.identity.kind.toUpperCase()}: {job.identity.value}
                </span>
              </div>
              <ul className="space-y-2" aria-label={t("Vorschau der Änderungen", "Change preview")}>
                {job.suggestions.map((suggestion) => {
                  const sourceUrl = safeExternalUrl(suggestion.source.url);
                  return (
                    <li key={suggestion.field} className="rounded-xl border border-border bg-card p-3">
                    <div className="flex flex-wrap items-center gap-2 text-[0.6875rem]">
                      <span className="font-mono uppercase tracking-[0.12em] text-muted-foreground">{fieldLabel(suggestion.field, isGerman)}</span>
                      <span className="text-muted-foreground">{t("Fehlt", "Missing")}</span>
                      <ArrowRight className="size-3 text-moss" aria-hidden="true" />
                      <span className="font-medium text-foreground">{Math.round(suggestion.confidence * 100)}%</span>
                    </div>
                    <p className="mt-1.5 max-h-28 overflow-y-auto whitespace-pre-wrap break-words text-[0.75rem] leading-relaxed text-foreground">
                      {valueText(suggestion.value)}
                    </p>
                      {sourceUrl ? (
                        <a href={sourceUrl} target="_blank" rel="noopener noreferrer" className="mt-2 inline-flex items-center gap-1 text-[0.65625rem] text-moss hover:underline">
                          {providerLabel(suggestion.source.provider)} <ExternalLink className="size-3" />
                        </a>
                      ) : (
                        <span className="mt-2 inline-flex text-[0.65625rem] text-muted-foreground">
                          {providerLabel(suggestion.source.provider)}
                        </span>
                      )}
                    </li>
                  );
                })}
              </ul>
              <div className="flex justify-end">
                <Button type="button" size="sm" className="rounded-full" disabled={apply.isPending} onClick={() => apply.mutate()}>
                  {apply.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <CheckCircle2 className="size-3.5" />}
                  {t("Vorschläge übernehmen", "Apply proposals")}
                </Button>
              </div>
            </>
          ) : (
            <div className="flex items-center gap-2 rounded-xl border border-border bg-card p-3 text-[0.75rem]">
              <CheckCircle2 className="size-4 text-moss" />
              {t("Keine weiteren verifizierbaren Angaben gefunden.", "No additional verifiable fields were found.")}
            </div>
          )}
        </div>
      ) : null}

      {job?.status === "applied" ? (
        <div className="mt-4 flex items-center gap-2 rounded-xl border border-moss/25 bg-card p-3 text-[0.75rem]">
          <CheckCircle2 className="size-4 text-moss" />
          {t(
            `${job.applied_fields.length} Angaben wurden sicher ergänzt.`,
            `${job.applied_fields.length} fields were safely added.`,
          )}
        </div>
      ) : null}

      <p className="mt-3 text-[0.65625rem] leading-relaxed text-muted-foreground">
        {t(
          "Quellen: Crossref sowie OpenAlex mit Unpaywall-OA-Hinweisen. Vorhandene oder manuell geleerte Felder werden nie überschrieben; Paywalls werden nicht umgangen.",
          "Sources: Crossref and OpenAlex with Unpaywall OA signals. Existing or manually cleared fields are never overwritten, and paywalls are never bypassed.",
        )}
      </p>
    </section>
  );
}
