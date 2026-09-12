"use client";

import { type ReactNode, useEffect, useRef, useState } from "react";
import {
  CheckCircle2,
  ChevronDown,
  ExternalLink,
  FileQuestion,
  FileText,
  Loader2,
  Pencil,
  X,
} from "lucide-react";

import { PaperEnrichment } from "@/components/library/paper-enrichment";
import { PaperCitationSection } from "@/components/library/citation-tools";
import { SourceIdentityMark } from "@/components/library/source-identity-mark";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";
import { formatDate } from "@/lib/format";
import {
  librarySourceIdentity,
  safeLibrarySourceUrl,
} from "@/lib/library-source-identity";
import type {
  LibraryDocument,
  LibraryMetadataProvenance,
  LibraryPaperMetadata,
  LibraryWebSource,
} from "@/lib/types";

type DetailKind = "paper" | "web";

interface MetadataField {
  key: keyof LibraryPaperMetadata | "site_name" | "source_kind";
  de: string;
  en: string;
  wide?: boolean;
  url?: boolean;
}

const METADATA_GROUPS: Array<{
  de: string;
  en: string;
  fields: MetadataField[];
}> = [
  {
    de: "Inhalt",
    en: "Content",
    fields: [
      { key: "title", de: "Titel", en: "Title", wide: true },
      { key: "subtitle", de: "Untertitel", en: "Subtitle" },
      { key: "short_title", de: "Kurztitel", en: "Short title" },
      { key: "authors", de: "Autor:innen", en: "Authors", wide: true },
      { key: "abstract", de: "Abstract / Beschreibung", en: "Abstract / description", wide: true },
      { key: "selected_excerpt", de: "Markierte Passage", en: "Selected passage", wide: true },
      { key: "keywords", de: "Schlagwörter", en: "Keywords", wide: true },
    ],
  },
  {
    de: "Publikation",
    en: "Publication",
    fields: [
      { key: "published_at", de: "Veröffentlicht", en: "Published" },
      { key: "year", de: "Jahr", en: "Year" },
      { key: "container_title", de: "Publikation / Venue", en: "Publication / venue" },
      { key: "publisher", de: "Verlag", en: "Publisher" },
      { key: "publisher_place", de: "Verlagsort", en: "Publisher place" },
      { key: "volume", de: "Band", en: "Volume" },
      { key: "issue", de: "Ausgabe", en: "Issue" },
      { key: "pages", de: "Seiten / Artikelnummer", en: "Pages / article number" },
      { key: "series_title", de: "Reihe", en: "Series title" },
      { key: "series_number", de: "Reihennummer", en: "Series number" },
      { key: "edition", de: "Auflage", en: "Edition" },
      { key: "item_type", de: "Eintragstyp", en: "Item type" },
      { key: "source_kind", de: "Quellentyp", en: "Source kind" },
      { key: "site_name", de: "Website", en: "Site" },
      { key: "format", de: "Format", en: "Format" },
      { key: "language", de: "Sprache", en: "Language" },
      { key: "license", de: "Lizenz", en: "License" },
    ],
  },
  {
    de: "Identifikatoren",
    en: "Identifiers",
    fields: [
      { key: "doi", de: "DOI", en: "DOI" },
      { key: "arxiv_id", de: "arXiv-ID", en: "arXiv ID" },
      { key: "isbn", de: "ISBN", en: "ISBN" },
      { key: "issn", de: "ISSN", en: "ISSN" },
      { key: "pmid", de: "PMID", en: "PMID" },
      { key: "pmcid", de: "PMCID", en: "PMCID" },
      { key: "citation_key", de: "Zitierschlüssel", en: "Citation key" },
      { key: "call_number", de: "Signatur", en: "Call number" },
    ],
  },
  {
    de: "Links & Archiv",
    en: "Links & archive",
    fields: [
      { key: "source_url", de: "Quell-URL", en: "Source URL", wide: true, url: true },
      { key: "canonical_url", de: "Kanonische URL", en: "Canonical URL", wide: true, url: true },
      { key: "pdf_url", de: "PDF-URL", en: "PDF URL", wide: true, url: true },
      { key: "accessed_at", de: "Zugriffsdatum", en: "Accessed at" },
      { key: "archive", de: "Archiv", en: "Archive" },
      { key: "archive_location", de: "Archivstandort", en: "Archive location" },
      { key: "extra", de: "Zusatzangaben", en: "Extra details", wide: true },
    ],
  },
];

function formatBytes(bytes: number): string {
  if (bytes <= 0) return "–";
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function displayValue(value: unknown): string | null {
  if (Array.isArray(value)) {
    const values = value.filter((item): item is string => typeof item === "string" && Boolean(item.trim()));
    return values.length > 0 ? values.join(" · ") : null;
  }
  if (typeof value === "number") return String(value);
  if (typeof value === "string" && value.trim()) return value;
  return null;
}

function provenanceLabel(
  provenance: LibraryMetadataProvenance | null | undefined,
  isGerman: boolean,
): string | null {
  if (!provenance) return null;
  const labels: Record<LibraryMetadataProvenance["source"], readonly [string, string]> = {
    user: ["Von dir geprüft", "Reviewed by you"],
    user_fill: ["Von dir ergänzt", "Added by you"],
    browser_capture: ["Browser Capture", "Browser Capture"],
    work: ["Bibliografischer Datensatz", "Bibliographic record"],
    document: ["Gespeichertes Dokument", "Stored document"],
    paper_enrichment: ["Verifizierte Metadatensuche", "Verified metadata search"],
  };
  const label = labels[provenance.source]?.[isGerman ? 0 : 1] ?? provenance.source;
  return provenance.updated_at
    ? `${label} · ${formatDate(provenance.updated_at, isGerman ? "de" : "en")}`
    : label;
}

function MetadataValue({
  field,
  value,
  provenance,
  isGerman,
}: {
  field: MetadataField;
  value: unknown;
  provenance: LibraryMetadataProvenance | null | undefined;
  isGerman: boolean;
}) {
  const rendered = displayValue(value);
  if (!rendered) return null;
  const url = field.url ? safeLibrarySourceUrl(rendered) : null;
  const sourceLabel = provenanceLabel(provenance, isGerman);
  return (
    <div className={`min-w-0 border-b border-border/55 pb-3 ${field.wide ? "sm:col-span-2" : ""}`}>
      <dt className="font-mono text-[0.59375rem] uppercase tracking-[0.15em] text-muted-foreground">
        {isGerman ? field.de : field.en}
      </dt>
      <dd className="mt-1 whitespace-pre-wrap break-words text-[0.8125rem] leading-relaxed text-foreground">
        {url ? (
          <a className="inline-flex max-w-full items-start gap-1 text-moss underline-offset-4 hover:underline" href={url} target="_blank" rel="noopener noreferrer">
            <span className="min-w-0 break-all">{rendered}</span>
            <ExternalLink className="mt-0.5 size-3 shrink-0" aria-hidden="true" />
          </a>
        ) : rendered}
      </dd>
      {sourceLabel ? <dd className="mt-1 text-[0.625rem] text-muted-foreground">{sourceLabel}</dd> : null}
    </div>
  );
}

function Fact({ icon, label, value }: { icon?: ReactNode; label: string; value: ReactNode }) {
  return (
    <span className="inline-flex min-w-0 items-center gap-1.5 rounded-full border border-border/70 bg-muted/25 px-2.5 py-1 text-[0.6875rem] text-muted-foreground">
      {icon}
      <span className="sr-only">{label}: </span>
      <span className="truncate">{value}</span>
    </span>
  );
}

function effectiveMetadata(document?: LibraryDocument, source?: LibraryWebSource): Record<string, unknown> {
  const metadata: Record<string, unknown> = { ...(document?.metadata ?? source?.metadata ?? {}) };
  metadata.title ??= document?.title ?? source?.title ?? null;
  metadata.authors = Array.isArray(metadata.authors) && metadata.authors.length > 0
    ? metadata.authors
    : document?.authors ?? source?.authors ?? [];
  metadata.abstract ??= source?.description ?? null;
  metadata.published_at ??= source?.published_at ?? null;
  metadata.year ??= document?.year ?? null;
  metadata.doi ??= document?.doi ?? source?.doi ?? null;
  metadata.source_url ??= document?.url ?? source?.url ?? null;
  metadata.canonical_url ??= source?.canonical_url ?? null;
  metadata.selected_excerpt ??= source?.selected_excerpt ?? null;
  metadata.license ??= document?.license ?? null;
  metadata.site_name ??= source?.site_name ?? null;
  metadata.source_kind ??= source?.source_kind ?? null;
  return metadata;
}

export function LibraryEntryDetail({
  open,
  kind,
  document,
  source,
  loading = false,
  loadFailed = false,
  onOpenChange,
  onRetry,
  onEdit,
  actions,
}: {
  open: boolean;
  kind: DetailKind;
  document?: LibraryDocument;
  source?: LibraryWebSource;
  loading?: boolean;
  loadFailed?: boolean;
  onOpenChange: (open: boolean) => void;
  onRetry: () => void;
  onEdit: () => void;
  actions?: ReactNode;
}) {
  const panelRef = useRef<HTMLElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);
  const onOpenChangeRef = useRef(onOpenChange);
  const [mobileModal, setMobileModal] = useState(false);
  const [enrichmentActionHost, setEnrichmentActionHost] = useState<HTMLDivElement | null>(null);
  onOpenChangeRef.current = onOpenChange;
  const { me } = useAuth();
  const isGerman = me?.language === "de";
  const t = (de: string, en: string) => (isGerman ? de : en);
  const localizedDate = (value: string) => formatDate(value, isGerman ? "de" : "en");
  const entry = document ?? source;
  const metadata = effectiveMetadata(document, source);
  const provenance = document?.metadata_provenance ?? source?.metadata_provenance ?? {};
  const title = displayValue(metadata.title) ?? t("Bibliothekseintrag", "Library entry");
  const sourceIdentity = librarySourceIdentity({
    url: metadata.source_url,
    siteName: metadata.site_name,
    fallbackLabel: kind === "paper" ? t("Paper", "Paper") : t("Webquelle", "Web source"),
  });
  const entryIdentity = `${kind}:${document?.id ?? source?.id ?? "missing"}`;
  const capture = document?.browser_capture ?? source?.provenance;
  const captureFields = Array.isArray(capture?.metadata_fields)
    ? capture.metadata_fields.filter((field): field is string => typeof field === "string")
    : [];
  const capturedAt = typeof capture?.captured_at === "string" ? capture.captured_at : null;
  const extensionVersion = typeof capture?.extension_version === "string" ? capture.extension_version : null;
  const visibleGroups = METADATA_GROUPS.map((group) => ({
    ...group,
    fields: group.fields.filter((field) => (
      (kind === "web" || (field.key !== "site_name" && field.key !== "source_kind"))
      && displayValue(metadata[field.key]) !== null
    )),
  })).filter((group) => group.fields.length > 0);
  const isProtectedEmpty = (field: MetadataField) => {
    const sourceType = provenance[field.key]?.source;
    return displayValue(metadata[field.key]) === null
      && (sourceType === "user" || sourceType === "user_fill");
  };
  const protectedEmptyGroups = METADATA_GROUPS.map((group) => ({
    ...group,
    fields: group.fields.filter((field) => (
      (kind === "web" || (field.key !== "site_name" && field.key !== "source_kind"))
      && isProtectedEmpty(field)
    )),
  })).filter((group) => group.fields.length > 0);
  const missingGroups = METADATA_GROUPS.map((group) => ({
    ...group,
    fields: group.fields.filter((field) => (
      (kind === "web" || (field.key !== "site_name" && field.key !== "source_kind"))
      && displayValue(metadata[field.key]) === null
      && !isProtectedEmpty(field)
    )),
  })).filter((group) => group.fields.length > 0);
  const missingCount = missingGroups.reduce((total, group) => total + group.fields.length, 0);
  const protectedEmptyCount = protectedEmptyGroups.reduce((total, group) => total + group.fields.length, 0);

  useEffect(() => {
    if (!open) return;
    restoreFocusRef.current = globalThis.document.activeElement instanceof HTMLElement
      ? globalThis.document.activeElement
      : null;
    const mobileQuery = window.matchMedia("(max-width: 1279px)");
    const masterPanel = globalThis.document.querySelector<HTMLElement>("[data-library-master-panel]");
    const previousMasterInert = masterPanel?.inert ?? false;
    const previousMasterAriaHidden = masterPanel?.getAttribute("aria-hidden") ?? null;
    const syncModalState = () => {
      const mobile = mobileQuery.matches;
      setMobileModal(mobile);
      if (masterPanel) {
        masterPanel.inert = mobile;
        if (mobile) masterPanel.setAttribute("aria-hidden", "true");
        else if (previousMasterAriaHidden === null) masterPanel.removeAttribute("aria-hidden");
        else masterPanel.setAttribute("aria-hidden", previousMasterAriaHidden);
      }
    };
    const focusMobilePanel = () => {
      if (mobileQuery.matches) closeButtonRef.current?.focus();
    };
    syncModalState();
    const animationFrame = window.requestAnimationFrame(focusMobilePanel);
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented) return;
      const eventTarget = event.target instanceof Element ? event.target : null;
      const nestedDialog = eventTarget?.closest('[role="dialog"]');
      if (nestedDialog && nestedDialog !== panelRef.current) return;
      if (eventTarget?.closest('[role="alertdialog"]')) return;
      if (eventTarget?.closest("[data-radix-popper-content-wrapper]")) return;
      if (event.key === "Escape") {
        event.preventDefault();
        onOpenChangeRef.current(false);
        return;
      }
      if (event.key !== "Tab" || !mobileQuery.matches || !panelRef.current) return;
      const focusable = Array.from(
        panelRef.current.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), summary, input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      ).filter((element) => !element.hasAttribute("hidden"));
      if (focusable.length === 0) {
        event.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && globalThis.document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && globalThis.document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    mobileQuery.addEventListener("change", syncModalState);
    mobileQuery.addEventListener("change", focusMobilePanel);
    return () => {
      window.cancelAnimationFrame(animationFrame);
      window.removeEventListener("keydown", handleKeyDown);
      mobileQuery.removeEventListener("change", syncModalState);
      mobileQuery.removeEventListener("change", focusMobilePanel);
      if (masterPanel) {
        masterPanel.inert = previousMasterInert;
        if (previousMasterAriaHidden === null) masterPanel.removeAttribute("aria-hidden");
        else masterPanel.setAttribute("aria-hidden", previousMasterAriaHidden);
      }
      const restoreTarget = restoreFocusRef.current?.isConnected
        ? restoreFocusRef.current
        : globalThis.document.querySelector<HTMLElement>('[data-library-master-panel] [aria-current="true"]');
      restoreTarget?.focus();
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const activeElement = globalThis.document.activeElement;
    if (activeElement instanceof HTMLElement && !panelRef.current?.contains(activeElement)) {
      restoreFocusRef.current = activeElement;
    }
    scrollRef.current?.scrollTo({ top: 0 });
  }, [entryIdentity, open]);

  if (!open) return null;

  return (
    <aside
      ref={panelRef}
      className="fixed inset-x-0 bottom-0 top-12 z-40 flex min-h-0 flex-col overflow-hidden bg-background xl:static xl:z-auto xl:w-[48%] xl:min-w-[30rem] xl:max-w-[56rem] xl:rounded-r-2xl xl:border-l xl:border-border xl:bg-card/35"
      aria-label={kind === "paper" ? t("Paperdetails", "Paper details") : t("Details der Webquelle", "Web source details")}
      role={mobileModal ? "dialog" : "complementary"}
      aria-modal={mobileModal || undefined}
      data-library-detail-panel
    >
      <header className="shrink-0 border-b border-border bg-background/95 px-4 py-4 backdrop-blur sm:px-5">
        <div className="flex items-start gap-3">
          <SourceIdentityMark
            kind={kind}
            url={metadata.source_url}
            siteName={metadata.site_name}
            isGerman={isGerman}
            className="mt-0.5 size-8 rounded-xl"
          />
          <div className="min-w-0 flex-1">
            <p className="font-mono text-[0.59375rem] uppercase tracking-[0.18em] text-moss">
              {kind === "paper" ? t("Paper", "Paper") : t("Webquelle", "Web source")}
            </p>
            <h2 className="mt-1 line-clamp-2 break-words font-display text-xl font-normal leading-tight text-foreground">
              {loading ? t("Eintrag wird geladen …", "Loading entry …") : title}
            </h2>
          </div>
          <Button
            ref={closeButtonRef}
            type="button"
            variant="ghost"
            size="icon"
            className="size-8 shrink-0 rounded-full"
            onClick={() => onOpenChange(false)}
            aria-label={t("Detailansicht schließen", "Close detail view")}
          >
            <X className="size-4" />
          </Button>
        </div>

        {entry ? (
          <div className="mt-3 flex flex-wrap items-center gap-1.5">
            {document ? (
              <>
                <Fact
                  icon={document.has_file ? <CheckCircle2 className="size-3 text-moss" /> : <FileText className="size-3" />}
                  label={t("Datei", "File")}
                  value={document.has_file ? t("Originaldatei", "Original file") : t("Nur Literaturangabe", "Citation only")}
                />
                {document.has_file ? <Fact label={t("Dateityp und Größe", "File type and size")} value={`${document.content_type || t("Datei", "File")} · ${formatBytes(document.byte_size)}`} /> : null}
                {sourceIdentity.specific ? <Fact label={t("Quellseite", "Source site")} value={sourceIdentity.label} /> : null}
                {document.project_name || document.folder ? <Fact label={t("Ablage", "Filed in")} value={[document.project_name, document.folder].filter(Boolean).join(" / ")} /> : null}
                <Fact label={t("Hinzugefügt", "Added")} value={localizedDate(document.created_at)} />
              </>
            ) : source ? (
              <>
                {sourceIdentity.specific ? <Fact label={t("Quellseite", "Source site")} value={sourceIdentity.label} /> : null}
                {source.source_kind ? <Fact label={t("Quellentyp", "Source type")} value={source.source_kind} /> : null}
                <Fact label={t("Gespeichert", "Saved")} value={localizedDate(source.created_at)} />
              </>
            ) : null}
          </div>
        ) : null}
      </header>

      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-5 sm:px-5">
        {loading ? (
          <div className="grid min-h-48 place-items-center" role="status">
            <Loader2 className="size-5 animate-spin text-moss" />
            <span className="sr-only">{t("Bibliothekseintrag wird geladen", "Loading Library entry")}</span>
          </div>
        ) : loadFailed && !entry ? (
          <div className="grid min-h-48 place-items-center text-center">
            <div>
              <FileQuestion className="mx-auto size-6 text-muted-foreground" />
              <p className="mt-2 text-[0.8125rem] text-muted-foreground">
                {t("Der Eintrag konnte gerade nicht geladen werden.", "This entry could not be loaded right now.")}
              </p>
              <Button type="button" variant="outline" size="sm" className="mt-4 rounded-full" onClick={onRetry}>
                {t("Erneut versuchen", "Try again")}
              </Button>
            </div>
          </div>
        ) : !entry ? (
          <div className="grid min-h-48 place-items-center text-center">
            <div>
              <FileQuestion className="mx-auto size-6 text-muted-foreground" />
              <p className="mt-2 text-[0.8125rem] text-muted-foreground">
                    {t("Dieser Eintrag ist in deiner Library nicht mehr verfügbar.", "This entry is no longer available in your Library.")}
              </p>
            </div>
          </div>
        ) : (
          <div className="space-y-6">
            {visibleGroups.map((group) => (
              <section key={group.en} aria-labelledby={`metadata-${group.en.toLowerCase().replaceAll(" ", "-").replaceAll("&", "and")}`}>
                <h3 id={`metadata-${group.en.toLowerCase().replaceAll(" ", "-").replaceAll("&", "and")}`} className="mb-3 font-mono text-[0.625rem] uppercase tracking-[0.18em] text-moss">
                  {isGerman ? group.de : group.en}
                </h3>
                <dl className="grid gap-x-5 gap-y-3 sm:grid-cols-2">
                  {group.fields.map((field) => (
                    <MetadataValue
                      key={field.key}
                      field={field}
                      value={metadata[field.key]}
                      provenance={provenance[field.key]}
                      isGerman={isGerman}
                    />
                  ))}
                </dl>
              </section>
            ))}

            {document ? (
              <PaperCitationSection
                documentId={document.id}
                metadataRevision={document.metadata_revision}
              />
            ) : null}

            {document ? (
              <PaperEnrichment
                document={document}
                isGerman={isGerman}
                actionHost={enrichmentActionHost}
              />
            ) : null}

            {missingCount > 0 ? (
              <details className="group rounded-xl border border-border/70 bg-muted/20 px-3.5 py-3">
                <summary className="flex cursor-pointer list-none items-center gap-2 text-[0.75rem] font-medium text-foreground marker:content-none">
                  <ChevronDown className="size-3.5 text-muted-foreground transition-transform group-open:rotate-180" />
                  {t(`${missingCount} noch nicht gespeicherte Felder`, `${missingCount} fields not saved yet`)}
                  <span className="ml-auto text-[0.65625rem] font-normal text-muted-foreground">
                    {t("kompakt anzeigen", "show compactly")}
                  </span>
                </summary>
                <div className="mt-3 space-y-3 border-t border-border/60 pt-3">
                  {missingGroups.map((group) => (
                    <div key={group.en}>
                      <p className="font-mono text-[0.5625rem] uppercase tracking-[0.14em] text-muted-foreground">
                        {isGerman ? group.de : group.en}
                      </p>
                      <p className="mt-1 text-[0.6875rem] leading-relaxed text-muted-foreground">
                        {group.fields.map((field) => isGerman ? field.de : field.en).join(" · ")}
                      </p>
                    </div>
                  ))}
                </div>
              </details>
            ) : null}

            {protectedEmptyCount > 0 ? (
              <details className="group rounded-xl border border-border/60 px-3.5 py-3">
                <summary className="flex cursor-pointer list-none items-center gap-2 text-[0.71875rem] text-muted-foreground marker:content-none">
                  <ChevronDown className="size-3.5 transition-transform group-open:rotate-180" />
                  {t(
                    `${protectedEmptyCount} bewusst leer gelassene Felder`,
                    `${protectedEmptyCount} fields intentionally left blank`,
                  )}
                </summary>
                <div className="mt-3 space-y-2 border-t border-border/60 pt-3">
                  <p className="text-[0.65625rem] leading-relaxed text-muted-foreground">
                    {t(
                      "Diese von dir bestätigten Leerwerte sind geschützt und werden bei der Metadatensuche nicht automatisch ergänzt.",
                      "These user-confirmed empty values are protected and will not be filled automatically by metadata enrichment.",
                    )}
                  </p>
                  {protectedEmptyGroups.map((group) => (
                    <p key={group.en} className="text-[0.6875rem] text-muted-foreground">
                      <span className="font-medium text-foreground">{isGerman ? group.de : group.en}:</span>{" "}
                      {group.fields.map((field) => isGerman ? field.de : field.en).join(" · ")}
                    </p>
                  ))}
                </div>
              </details>
            ) : null}

            <details className="group border-t border-border/70 pt-3">
              <summary className="flex cursor-pointer list-none items-center gap-2 text-[0.71875rem] text-muted-foreground marker:content-none">
                <ChevronDown className="size-3.5 transition-transform group-open:rotate-180" />
                {t("Technische Eintragsdetails", "Technical record details")}
              </summary>
              <dl className="mt-3 grid gap-x-5 gap-y-2 text-[0.6875rem] sm:grid-cols-2">
                {document ? (
                  <>
                    <div><dt className="text-muted-foreground">{t("Library-ID / Work-ID", "Library ID / Work ID")}</dt><dd className="mt-0.5 break-all text-foreground">{document.id} / {document.work_id}</dd></div>
                    {document.text_status ? <div><dt className="text-muted-foreground">{t("Texterkennung", "Text extraction")}</dt><dd className="mt-0.5 text-foreground">{document.text_status}</dd></div> : null}
                    {document.source ? <div><dt className="text-muted-foreground">{t("Quelle", "Origin")}</dt><dd className="mt-0.5 text-foreground">{document.source}</dd></div> : null}
                    {document.legal_basis ? <div><dt className="text-muted-foreground">{t("Rechtsgrundlage", "Legal basis")}</dt><dd className="mt-0.5 text-foreground">{document.legal_basis}</dd></div> : null}
                    {document.run ? <div className="sm:col-span-2"><dt className="text-muted-foreground">{t("Ursprünglicher Chat", "Originating chat")}</dt><dd className="mt-0.5"><a className="text-moss hover:underline" href={`/r/${document.run.public_id}`}>{document.run.label}</a></dd></div> : null}
                  </>
                ) : source ? (
                  <>
                    <div><dt className="text-muted-foreground">{t("Quellen-ID", "Source ID")}</dt><dd className="mt-0.5 break-all text-foreground">{source.id}</dd></div>
                    {source.project_id ? <div><dt className="text-muted-foreground">{t("Projekt", "Project")}</dt><dd className="mt-0.5 text-foreground">#{source.project_id}</dd></div> : null}
                    <div><dt className="text-muted-foreground">{t("Zuletzt geändert", "Last changed")}</dt><dd className="mt-0.5 text-foreground">{localizedDate(source.updated_at)}</dd></div>
                  </>
                ) : null}
                {capturedAt ? <div><dt className="text-muted-foreground">{t("Erfasst am", "Captured at")}</dt><dd className="mt-0.5 text-foreground">{localizedDate(capturedAt)}</dd></div> : null}
                {extensionVersion ? <div><dt className="text-muted-foreground">{t("Browser-Capture-Version", "Browser Capture version")}</dt><dd className="mt-0.5 text-foreground">{extensionVersion}</dd></div> : null}
                {captureFields.length > 0 ? <div className="sm:col-span-2"><dt className="text-muted-foreground">{t("Erkannte Felder", "Detected fields")}</dt><dd className="mt-0.5 text-foreground">{captureFields.join(" · ")}</dd></div> : null}
              </dl>
            </details>
          </div>
        )}
      </div>

      {entry ? (
        <footer className="shrink-0 border-t border-border bg-background/95 px-4 py-3 backdrop-blur sm:px-5">
          <div className="flex flex-wrap items-center justify-end gap-2">
            {document ? (
              <div
                ref={setEnrichmentActionHost}
                className="mr-auto flex min-h-8 items-center"
                data-paper-enrichment-action
              />
            ) : null}
            <Button type="button" variant="outline" size="sm" className="h-8 rounded-full" onClick={onEdit}>
              <Pencil className="size-3.5" />
              {t("Metadaten bearbeiten", "Edit metadata")}
            </Button>
            {actions}
          </div>
        </footer>
      ) : null}
    </aside>
  );
}
