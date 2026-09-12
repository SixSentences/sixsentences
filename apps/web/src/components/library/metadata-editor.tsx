"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { BookOpenText, ExternalLink, Loader2, Pencil, Save } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type {
  LibraryDocument,
  LibraryPaperMetadata,
  LibraryWebSource,
} from "@/lib/types";

type MetadataKind = "paper" | "web";

const METADATA_FIELD_LABELS: Record<string, readonly [string, string]> = {
  title: ["Titel", "title"],
  subtitle: ["Untertitel", "subtitle"],
  short_title: ["Kurztitel", "short title"],
  authors: ["Autor:innen", "authors"],
  abstract: ["Abstract/Beschreibung", "abstract/description"],
  selected_excerpt: ["markierte Passage", "selected passage"],
  published_at: ["Veröffentlichungsdatum", "published date"],
  container_title: ["Publikation/Venue", "publication/venue"],
  publisher: ["Verlag", "publisher"],
  publisher_place: ["Verlagsort", "publisher place"],
  volume: ["Band", "volume"],
  issue: ["Ausgabe", "issue"],
  pages: ["Seiten/Artikelnummer", "pages/article number"],
  series_title: ["Reihe", "series title"],
  series_number: ["Reihennummer", "series number"],
  edition: ["Auflage", "edition"],
  item_type: ["Eintragstyp", "item type"],
  format: ["Format", "format"],
  language: ["Sprache", "language"],
  license: ["Lizenz", "license"],
  doi: ["DOI", "DOI"],
  arxiv_id: ["arXiv-ID", "arXiv ID"],
  isbn: ["ISBN", "ISBN"],
  issn: ["ISSN", "ISSN"],
  pmid: ["PMID", "PMID"],
  pmcid: ["PMCID", "PMCID"],
  citation_key: ["Zitierschlüssel", "citation key"],
  call_number: ["Signatur", "call number"],
  keywords: ["Schlagwörter", "keywords"],
  source_url: ["Quell-URL", "source URL"],
  canonical_url: ["kanonische URL", "canonical URL"],
  pdf_url: ["PDF-URL", "PDF URL"],
  accessed_at: ["Zugriffsdatum", "accessed at"],
  archive: ["Archiv", "archive"],
  archive_location: ["Archivstandort", "archive location"],
  extra: ["Zusatzangaben", "extra details"],
  site_name: ["Website", "site"],
  source_kind: ["Quellentyp", "source kind"],
};

function metadataFieldLabel(field: string, isGerman: boolean): string {
  const labels = METADATA_FIELD_LABELS[field];
  return labels ? labels[isGerman ? 0 : 1] : field.replaceAll("_", " ");
}

interface MetadataDraft {
  title: string;
  authors: string;
  abstract: string;
  published_at: string;
  doi: string;
  container_title: string;
  volume: string;
  issue: string;
  pages: string;
  publisher: string;
  language: string;
  license: string;
  isbn: string;
  issn: string;
  arxiv_id: string;
  keywords: string;
  item_type: string;
  subtitle: string;
  short_title: string;
  series_title: string;
  series_number: string;
  edition: string;
  publisher_place: string;
  accessed_at: string;
  archive: string;
  archive_location: string;
  citation_key: string;
  format: string;
  call_number: string;
  pmid: string;
  pmcid: string;
  extra: string;
  source_url: string;
  canonical_url: string;
  pdf_url: string;
  site_name: string;
  selected_excerpt: string;
}

const EMPTY_METADATA: LibraryPaperMetadata = {
  title: null,
  authors: [],
  abstract: null,
  published_at: null,
  year: null,
  doi: null,
  source_url: null,
  canonical_url: null,
  pdf_url: null,
  container_title: null,
  volume: null,
  issue: null,
  pages: null,
  publisher: null,
  language: null,
  license: null,
  isbn: null,
  issn: null,
  arxiv_id: null,
  keywords: null,
  item_type: null,
  subtitle: null,
  short_title: null,
  series_title: null,
  series_number: null,
  edition: null,
  publisher_place: null,
  accessed_at: null,
  archive: null,
  archive_location: null,
  citation_key: null,
  format: null,
  call_number: null,
  pmid: null,
  pmcid: null,
  extra: null,
  selected_excerpt: null,
};

function draftFromMetadata(
  metadata: LibraryPaperMetadata,
  webSource?: LibraryWebSource,
): MetadataDraft {
  return {
    title: metadata.title ?? "",
    authors: metadata.authors.join("\n"),
    abstract: metadata.abstract ?? "",
    published_at: metadata.published_at ?? "",
    doi: metadata.doi ?? "",
    container_title: metadata.container_title ?? "",
    volume: metadata.volume ?? "",
    issue: metadata.issue ?? "",
    pages: metadata.pages ?? "",
    publisher: metadata.publisher ?? "",
    language: metadata.language ?? "",
    license: metadata.license ?? "",
    isbn: metadata.isbn ?? "",
    issn: metadata.issn ?? "",
    arxiv_id: metadata.arxiv_id ?? "",
    keywords: (metadata.keywords ?? []).join("\n"),
    item_type: metadata.item_type ?? "",
    subtitle: metadata.subtitle ?? "",
    short_title: metadata.short_title ?? "",
    series_title: metadata.series_title ?? "",
    series_number: metadata.series_number ?? "",
    edition: metadata.edition ?? "",
    publisher_place: metadata.publisher_place ?? "",
    accessed_at: metadata.accessed_at ?? "",
    archive: metadata.archive ?? "",
    archive_location: metadata.archive_location ?? "",
    citation_key: metadata.citation_key ?? "",
    format: metadata.format ?? "",
    call_number: metadata.call_number ?? "",
    pmid: metadata.pmid ?? "",
    pmcid: metadata.pmcid ?? "",
    extra: metadata.extra ?? "",
    source_url: metadata.source_url ?? "",
    canonical_url: metadata.canonical_url ?? "",
    pdf_url: metadata.pdf_url ?? "",
    site_name: webSource?.site_name ?? "",
    selected_excerpt: webSource?.selected_excerpt ?? metadata.selected_excerpt ?? "",
  };
}

function cleanedLines(value: string): string[] {
  const values: string[] = [];
  const seen = new Set<string>();
  for (const line of value.split("\n")) {
    const cleaned = line.trim();
    if (cleaned && !seen.has(cleaned.toLocaleLowerCase())) {
      values.push(cleaned);
      seen.add(cleaned.toLocaleLowerCase());
    }
  }
  return values;
}

function apiErrorDetail(error: ApiError): Record<string, unknown> | null {
  return error.detail !== null && typeof error.detail === "object" && !Array.isArray(error.detail)
    ? (error.detail as Record<string, unknown>)
    : null;
}

function metadataChanges(
  original: LibraryPaperMetadata,
  draft: MetadataDraft,
): Partial<Omit<LibraryPaperMetadata, "year">> {
  const changes: Partial<Omit<LibraryPaperMetadata, "year">> = {};
  const put = (field: string, value: unknown, previous: unknown) => {
    if (JSON.stringify(value) !== JSON.stringify(previous)) {
      Object.assign(changes, { [field]: value });
    }
  };
  put("title", draft.title.trim() || null, original.title);
  put("authors", cleanedLines(draft.authors), original.authors);
  put("abstract", draft.abstract.trim() || null, original.abstract);
  put("published_at", draft.published_at.trim() || null, original.published_at);
  put("doi", draft.doi.trim() || null, original.doi);
  put("container_title", draft.container_title.trim() || null, original.container_title);
  put("volume", draft.volume.trim() || null, original.volume);
  put("issue", draft.issue.trim() || null, original.issue);
  put("pages", draft.pages.trim() || null, original.pages);
  put("publisher", draft.publisher.trim() || null, original.publisher);
  put("language", draft.language.trim() || null, original.language);
  put("license", draft.license.trim() || null, original.license);
  put("isbn", draft.isbn.trim() || null, original.isbn);
  put("issn", draft.issn.trim() || null, original.issn);
  put("arxiv_id", draft.arxiv_id.trim() || null, original.arxiv_id);
  put("keywords", cleanedLines(draft.keywords), original.keywords ?? []);
  put("item_type", draft.item_type.trim() || null, original.item_type);
  put("subtitle", draft.subtitle.trim() || null, original.subtitle);
  put("short_title", draft.short_title.trim() || null, original.short_title);
  put("series_title", draft.series_title.trim() || null, original.series_title);
  put("series_number", draft.series_number.trim() || null, original.series_number);
  put("edition", draft.edition.trim() || null, original.edition);
  put("publisher_place", draft.publisher_place.trim() || null, original.publisher_place);
  put("accessed_at", draft.accessed_at.trim() || null, original.accessed_at);
  put("archive", draft.archive.trim() || null, original.archive);
  put("archive_location", draft.archive_location.trim() || null, original.archive_location);
  put("citation_key", draft.citation_key.trim() || null, original.citation_key);
  put("format", draft.format.trim() || null, original.format);
  put("call_number", draft.call_number.trim() || null, original.call_number);
  put("pmid", draft.pmid.trim() || null, original.pmid);
  put("pmcid", draft.pmcid.trim() || null, original.pmcid);
  put("extra", draft.extra.trim() || null, original.extra);
  put(
    "selected_excerpt",
    draft.selected_excerpt.trim() || null,
    original.selected_excerpt,
  );
  put("source_url", draft.source_url.trim() || null, original.source_url);
  put("canonical_url", draft.canonical_url.trim() || null, original.canonical_url);
  put("pdf_url", draft.pdf_url.trim() || null, original.pdf_url);
  return changes;
}

function Field({
  label,
  value,
  onChange,
  placeholder,
  className = "",
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
}) {
  return (
    <label className={`grid gap-1.5 ${className}`}>
      <span className="font-mono text-[0.625rem] uppercase tracking-[0.16em] text-muted-foreground">
        {label}
      </span>
      <Input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        className="h-9 rounded-xl text-[0.8125rem]"
      />
    </label>
  );
}

function MetadataEditor({
  kind,
  document,
  webSource,
  controlledOpen,
  onControlledOpenChange,
  hideTrigger = false,
}: {
  kind: MetadataKind;
  document?: LibraryDocument;
  webSource?: LibraryWebSource;
  controlledOpen?: boolean;
  onControlledOpenChange?: (open: boolean) => void;
  hideTrigger?: boolean;
}) {
  const { me } = useAuth();
  const isGerman = me?.language === "de";
  const t = (de: string, en: string) => (isGerman ? de : en);
  const queryClient = useQueryClient();
  const [internalOpen, setInternalOpen] = useState(false);
  const open = controlledOpen ?? internalOpen;
  const setOpen = (next: boolean) => {
    if (controlledOpen === undefined) setInternalOpen(next);
    onControlledOpenChange?.(next);
  };
  const metadata = document?.metadata ?? webSource?.metadata ?? EMPTY_METADATA;
  const revision = document?.metadata_revision ?? webSource?.metadata_revision ?? "";
  const provenance = document?.metadata_provenance ?? webSource?.metadata_provenance ?? {};
  const reviewedFields = Object.entries(provenance)
    .filter(([, value]) => value?.source === "user" || value?.source === "user_fill")
    .map(([field]) => metadataFieldLabel(field, isGerman));
  const detectedFields = Object.entries(provenance)
    .filter(([, value]) => value && value.source !== "user" && value.source !== "user_fill")
    .map(([field]) => metadataFieldLabel(field, isGerman));
  const [draft, setDraft] = useState(() => draftFromMetadata(metadata, webSource));

  useEffect(() => {
    if (open) setDraft(draftFromMetadata(metadata, webSource));
  }, [metadata, open, revision, webSource]);

  const changes = useMemo(() => {
    const next: Record<string, unknown> = { ...metadataChanges(metadata, draft) };
    if (kind === "web") {
      const previousSite = webSource?.site_name || null;
      const siteName = draft.site_name.trim() || null;
      if (siteName !== previousSite) next.site_name = siteName;
    }
    return next;
  }, [draft, kind, metadata, webSource]);

  const save = useMutation({
    mutationFn: async () => {
      if (kind === "paper" && document) {
        return api.updateLibraryDocumentMetadata(document.id, {
          expected_revision: revision,
          mode: "edit",
          metadata: changes,
        });
      }
      if (kind === "web" && webSource) {
        return api.updateLibraryWebSourceMetadata(webSource.id, {
          expected_revision: revision,
          mode: "edit",
          metadata: changes,
        });
      }
      throw new Error(t("Der Bibliothekseintrag ist nicht mehr verfügbar.", "The Library record is no longer available."));
    },
    onSuccess: () => {
      setOpen(false);
      toast.success(
        kind === "paper"
          ? t("Paper-Metadaten gespeichert.", "Paper details saved.")
          : t("Quellen-Metadaten gespeichert.", "Source details saved."),
      );
      void queryClient.invalidateQueries({
        queryKey: kind === "paper" ? ["library"] : ["library-web-sources"],
      });
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) {
        const detail = apiErrorDetail(error);
        if (detail?.code === "library_metadata_revision_conflict") {
          toast.error(
            t(
              "Dieser Eintrag wurde an anderer Stelle geändert. Öffne ihn erneut, um den aktuellen Stand zu prüfen.",
              "This entry changed elsewhere. Reopen it to review the latest details.",
            ),
          );
          void queryClient.invalidateQueries({
            queryKey: kind === "paper" ? ["library"] : ["library-web-sources"],
          });
          setOpen(false);
          return;
        }
        if (detail?.code === "library_metadata_identity_conflict") {
          const field = detail.field === "doi" ? "DOI" : t("kanonische URL", "canonical URL");
          toast.error(
            t(
              `Diese ${field} gehört bereits zu einem anderen Bibliothekseintrag. Prüfe den vorhandenen Eintrag oder verwende einen eindeutigen Identifikator.`,
              `This ${field} already belongs to another Library entry. Review the existing entry or use a unique identifier.`,
            ),
          );
          return;
        }
        toast.error(
          t(
            "Die Änderung steht im Konflikt mit einem anderen Bibliothekseintrag. Prüfe die Identifikatoren und versuche es erneut.",
            "This change conflicts with another Library entry. Review the identifiers and try again.",
          ),
        );
        return;
      }
      toast.error(
        error instanceof Error
          ? error.message
          : t("Die Metadaten konnten nicht gespeichert werden.", "Could not save the metadata."),
      );
    },
  });

  const update = (field: keyof MetadataDraft, value: string) => {
    setDraft((current) => ({ ...current, [field]: value }));
  };
  const label = metadata.title || webSource?.title || document?.work_id || t("Bibliothekseintrag", "Library entry");

  return (
    <>
      {!hideTrigger ? (
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="size-7 rounded-full"
              onClick={() => setOpen(true)}
              aria-label={`${t("Metadaten bearbeiten für", "Edit metadata for")} ${label}`}
            >
              <Pencil className="size-3.5" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="top">
            {t("Metadaten ansehen und bearbeiten", "View and edit metadata")}
          </TooltipContent>
        </Tooltip>
      ) : null}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="flex max-h-[90dvh] flex-col gap-0 overflow-hidden p-0 sm:max-w-3xl">
          <DialogHeader className="shrink-0 border-b border-border px-6 pb-4 pt-6">
            <div className="flex items-center gap-2 text-moss">
              <BookOpenText className="size-4" />
              <span className="font-mono text-[0.625rem] uppercase tracking-[0.2em]">
                SixSentences Library
              </span>
            </div>
            <DialogTitle className="pt-1">
              {t("Bibliografische Angaben", "Bibliographic details")}
            </DialogTitle>
            <DialogDescription>
              {kind === "paper"
                ? t(
                    "Ergänze oder korrigiere dieses Paper. Nur geänderte Felder werden gespeichert; neuere Metadaten werden nie unbemerkt überschrieben.",
                    "Complete or correct this paper. Only changed fields are saved, and newer metadata can never be overwritten silently.",
                  )
                : t(
                    "Ergänze oder korrigiere diese Webquelle. Nur geänderte Felder werden gespeichert; neuere Metadaten werden nie unbemerkt überschrieben.",
                    "Complete or correct this web source. Only changed fields are saved, and newer metadata can never be overwritten silently.",
                  )}
            </DialogDescription>
          </DialogHeader>
          <div className="min-h-0 flex-1 space-y-6 overflow-y-auto overscroll-contain px-6 py-5">
            <section className="rounded-xl border border-moss/20 bg-accent/35 px-3 py-2.5">
              <div className="flex flex-wrap gap-2 text-[0.6875rem]">
                <span className="rounded-full bg-moss/10 px-2 py-1 font-medium text-moss">
                  {t("Von dir geprüft", "Reviewed by you")}: {reviewedFields.length}
                </span>
                <span className="rounded-full bg-muted px-2 py-1 text-muted-foreground">
                  {t("Automatisch erkannt", "Detected automatically")}: {detectedFields.length}
                </span>
              </div>
              <p className="mt-2 text-[0.6875rem] leading-relaxed text-muted-foreground">
                {t(
                  "Gespeicherte Änderungen gelten als von dir geprüft. Unveränderte Angaben behalten ihre automatische Quelle.",
                  "Saved changes are marked as reviewed by you. Untouched details keep their automatic source.",
                )}
              </p>
              {reviewedFields.length > 0 ? (
                <p className="mt-1 text-[0.6875rem] text-muted-foreground">
                  {t("Geprüfte Felder", "Reviewed fields")}: {reviewedFields.join(", ")}
                </p>
              ) : null}
              {detectedFields.length > 0 ? (
                <p className="mt-1 text-[0.6875rem] text-muted-foreground">
                  {t("Automatische Felder", "Automatically sourced fields")}: {detectedFields.join(", ")}
                </p>
              ) : null}
            </section>
            <section className="grid gap-3">
              <Field label={t("Titel", "Title")} value={draft.title} onChange={(value) => update("title", value)} />
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label={t("Untertitel", "Subtitle")} value={draft.subtitle} onChange={(value) => update("subtitle", value)} />
                <Field label={t("Kurztitel", "Short title")} value={draft.short_title} onChange={(value) => update("short_title", value)} />
              </div>
              <label className="grid gap-1.5">
                <span className="font-mono text-[0.625rem] uppercase tracking-[0.16em] text-muted-foreground">
                  {t("Autor:innen · eine Person pro Zeile", "Authors · one per line")}
                </span>
                <Textarea
                  value={draft.authors}
                  onChange={(event) => update("authors", event.target.value)}
                  className="min-h-20 rounded-xl text-[0.8125rem]"
                />
              </label>
              <label className="grid gap-1.5">
                <span className="font-mono text-[0.625rem] uppercase tracking-[0.16em] text-muted-foreground">
                  {t("Abstract oder Beschreibung", "Abstract or description")}
                </span>
                <Textarea
                  value={draft.abstract}
                  onChange={(event) => update("abstract", event.target.value)}
                  className="min-h-28 rounded-xl text-[0.8125rem]"
                />
              </label>
              <label className="grid gap-1.5">
                <span className="font-mono text-[0.625rem] uppercase tracking-[0.16em] text-muted-foreground">
                  {t("Markierte Passage", "Selected passage")}
                </span>
                <Textarea
                  value={draft.selected_excerpt}
                  onChange={(event) => update("selected_excerpt", event.target.value)}
                  maxLength={4000}
                  className="min-h-24 rounded-xl text-[0.8125rem]"
                />
              </label>
            </section>

            <section>
              <p className="mb-3 font-mono text-[0.625rem] uppercase tracking-[0.18em] text-moss">
                {t("Publikation", "Publication")}
              </p>
              <div className="grid gap-3 sm:grid-cols-2">
                {kind === "web" ? (
                  <Field label={t("Website", "Site")} value={draft.site_name} onChange={(value) => update("site_name", value)} />
                ) : null}
                <Field label={t("Veröffentlichungsdatum", "Published date")} value={draft.published_at} onChange={(value) => update("published_at", value)} placeholder={t("JJJJ, JJJJ-MM oder JJJJ-MM-TT", "YYYY, YYYY-MM or YYYY-MM-DD")} />
                <Field label={t("Publikation / Venue", "Publication / venue")} value={draft.container_title} onChange={(value) => update("container_title", value)} />
                <Field label={t("Verlag", "Publisher")} value={draft.publisher} onChange={(value) => update("publisher", value)} />
                <Field label={t("Verlagsort", "Publisher place")} value={draft.publisher_place} onChange={(value) => update("publisher_place", value)} />
                <Field label={t("Band", "Volume")} value={draft.volume} onChange={(value) => update("volume", value)} />
                <Field label={t("Ausgabe", "Issue")} value={draft.issue} onChange={(value) => update("issue", value)} />
                <Field label={t("Seiten / Artikelnummer", "Pages / article number")} value={draft.pages} onChange={(value) => update("pages", value)} />
                <Field label={t("Reihe", "Series title")} value={draft.series_title} onChange={(value) => update("series_title", value)} />
                <Field label={t("Reihennummer", "Series number")} value={draft.series_number} onChange={(value) => update("series_number", value)} />
                <Field label={t("Auflage", "Edition")} value={draft.edition} onChange={(value) => update("edition", value)} />
                <Field label={t("Eintragstyp", "Item type")} value={draft.item_type} onChange={(value) => update("item_type", value)} placeholder="journalArticle, preprint, webpage…" />
                <Field label={t("Format", "Format")} value={draft.format} onChange={(value) => update("format", value)} />
                <Field label={t("Sprache", "Language")} value={draft.language} onChange={(value) => update("language", value)} />
                <Field label={t("Lizenz", "License")} value={draft.license} onChange={(value) => update("license", value)} />
              </div>
            </section>

            <section>
              <p className="mb-3 font-mono text-[0.625rem] uppercase tracking-[0.18em] text-moss">
                {t("Identifikatoren", "Identifiers")}
              </p>
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="DOI" value={draft.doi} onChange={(value) => update("doi", value)} />
                <Field label="arXiv ID" value={draft.arxiv_id} onChange={(value) => update("arxiv_id", value)} />
                <Field label="ISBN" value={draft.isbn} onChange={(value) => update("isbn", value)} />
                <Field label="ISSN" value={draft.issn} onChange={(value) => update("issn", value)} />
                <Field label="PMID" value={draft.pmid} onChange={(value) => update("pmid", value)} />
                <Field label="PMCID" value={draft.pmcid} onChange={(value) => update("pmcid", value)} />
                <Field label={t("Zitierschlüssel", "Citation key")} value={draft.citation_key} onChange={(value) => update("citation_key", value)} />
                <Field label={t("Signatur", "Call number")} value={draft.call_number} onChange={(value) => update("call_number", value)} />
              </div>
              <label className="mt-3 grid gap-1.5">
                <span className="font-mono text-[0.625rem] uppercase tracking-[0.16em] text-muted-foreground">
                  {t("Schlagwörter · eines pro Zeile", "Keywords · one per line")}
                </span>
                <Textarea
                  value={draft.keywords}
                  onChange={(event) => update("keywords", event.target.value)}
                  className="min-h-20 rounded-xl text-[0.8125rem]"
                />
              </label>
            </section>

            <section>
              <p className="mb-3 font-mono text-[0.625rem] uppercase tracking-[0.18em] text-moss">
                {t("Links", "Links")}
              </p>
              <div className="grid gap-3">
                <Field label={t("Quell-URL", "Source URL")} value={draft.source_url} onChange={(value) => update("source_url", value)} />
                <Field label={t("Kanonische URL", "Canonical URL")} value={draft.canonical_url} onChange={(value) => update("canonical_url", value)} />
                <Field label="PDF URL" value={draft.pdf_url} onChange={(value) => update("pdf_url", value)} />
              </div>
              {draft.source_url ? (
                <Button asChild variant="ghost" size="sm" className="mt-2 rounded-full text-moss">
                  <a href={draft.source_url} target="_blank" rel="noopener noreferrer">
                    <ExternalLink className="size-3.5" /> {t("Quelle öffnen", "Open source")}
                  </a>
                </Button>
              ) : null}
            </section>

            <section>
              <p className="mb-3 font-mono text-[0.625rem] uppercase tracking-[0.18em] text-moss">
                {t("Archiv & Ablage", "Archive & filing")}
              </p>
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label={t("Zugriff am", "Accessed at")} value={draft.accessed_at} onChange={(value) => update("accessed_at", value)} placeholder={t("JJJJ-MM-TT", "YYYY-MM-DD")} />
                <Field label={t("Archiv", "Archive")} value={draft.archive} onChange={(value) => update("archive", value)} />
                <Field label={t("Archivstandort", "Archive location")} value={draft.archive_location} onChange={(value) => update("archive_location", value)} />
              </div>
              <label className="mt-3 grid gap-1.5">
                <span className="font-mono text-[0.625rem] uppercase tracking-[0.16em] text-muted-foreground">
                  {t("Zusätzliche bibliografische Angaben", "Extra bibliographic details")}
                </span>
                <Textarea
                  value={draft.extra}
                  onChange={(event) => update("extra", event.target.value)}
                  maxLength={2000}
                  className="min-h-24 rounded-xl text-[0.8125rem]"
                />
              </label>
            </section>
          </div>
          <DialogFooter className="mx-0 mb-0 shrink-0 border-t border-border px-6 py-4">
            <Button type="button" variant="outline" className="rounded-full" onClick={() => setOpen(false)}>
              {t("Abbrechen", "Cancel")}
            </Button>
            <Button
              type="button"
              className="rounded-full"
              disabled={save.isPending || Object.keys(changes).length === 0}
              onClick={() => save.mutate()}
            >
              {save.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <Save className="size-3.5" />}
              {t("Angaben speichern", "Save details")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

interface ControlledMetadataEditorProps {
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  hideTrigger?: boolean;
}

export function PaperMetadataEditor({
  document,
  open,
  onOpenChange,
  hideTrigger,
}: { document: LibraryDocument } & ControlledMetadataEditorProps) {
  return (
    <MetadataEditor
      kind="paper"
      document={document}
      controlledOpen={open}
      onControlledOpenChange={onOpenChange}
      hideTrigger={hideTrigger}
    />
  );
}

export function WebSourceMetadataEditor({
  source,
  open,
  onOpenChange,
  hideTrigger,
}: { source: LibraryWebSource } & ControlledMetadataEditorProps) {
  return (
    <MetadataEditor
      kind="web"
      webSource={source}
      controlledOpen={open}
      onControlledOpenChange={onOpenChange}
      hideTrigger={hideTrigger}
    />
  );
}
