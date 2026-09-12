"use client";

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Check, Clipboard, Download, Loader2, RefreshCw, TriangleAlert } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ApiError, api, downloadLibraryCitations } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { LibraryCitationFormat } from "@/lib/types";

const CITATION_FORMATS: Array<{
  value: LibraryCitationFormat;
  de: string;
  en: string;
  extension: string;
}> = [
  { value: "apa", de: "APA", en: "APA", extension: ".txt" },
  { value: "mla", de: "MLA", en: "MLA", extension: ".txt" },
  { value: "chicago", de: "Chicago (Autor–Jahr)", en: "Chicago Author-Date", extension: ".txt" },
  { value: "harvard", de: "Harvard (Autor–Jahr)", en: "Harvard Author-Date", extension: ".txt" },
  { value: "bibtex", de: "BibTeX", en: "BibTeX", extension: ".bib" },
  { value: "ris", de: "RIS", en: "RIS", extension: ".ris" },
];

const MISSING_FIELD_LABELS: Record<string, readonly [string, string]> = {
  title: ["Titel", "title"],
  authors: ["Autor:innen", "authors"],
  year: ["Jahr", "year"],
  published_at: ["Veröffentlichungsdatum", "publication date"],
  container_title: ["Publikation / Venue", "publication / venue"],
  publisher: ["Verlag", "publisher"],
  volume: ["Band", "volume"],
  issue: ["Ausgabe", "issue"],
  pages: ["Seiten", "pages"],
  doi: ["DOI", "DOI"],
  url: ["URL", "URL"],
  source_url: ["Quell-URL", "source URL"],
};

function missingFieldLabel(field: string, isGerman: boolean): string {
  return MISSING_FIELD_LABELS[field]?.[isGerman ? 0 : 1]
    ?? field.replaceAll("_", " ");
}

function downloadErrorMessage(error: unknown, isGerman: boolean): string {
  if (error instanceof ApiError && error.status === 0) {
    return isGerman
      ? "SixSentences ist gerade nicht erreichbar. Prüfe deine Verbindung und versuche es erneut."
      : "SixSentences is unreachable right now. Check your connection and try again.";
  }
  if (error instanceof ApiError && error.status === 404) {
    return isGerman
      ? "Mindestens ein ausgewähltes Paper ist nicht mehr verfügbar. Die Auswahl bleibt erhalten."
      : "At least one selected paper is no longer available. Your selection is preserved.";
  }
  if (error instanceof ApiError && error.status === 422) {
    return isGerman
      ? "Die Auswahl kann so nicht exportiert werden. Wähle 1 bis 100 Paper aus."
      : "This selection cannot be exported. Choose between 1 and 100 papers.";
  }
  return isGerman
    ? "Die Zitationsdatei konnte nicht heruntergeladen werden. Deine Auswahl bleibt erhalten."
    : "The citation file could not be downloaded. Your selection is preserved.";
}

export function LibraryCitationExportMenu({
  documentIds,
  isGerman,
}: {
  documentIds: number[];
  isGerman: boolean;
}) {
  const [downloading, setDownloading] = useState<LibraryCitationFormat | null>(null);
  const tooMany = documentIds.length > 100;

  const download = async (format: LibraryCitationFormat) => {
    if (documentIds.length < 1 || tooMany || downloading) return;
    setDownloading(format);
    try {
      await downloadLibraryCitations(documentIds, format);
      toast.success(
        isGerman
          ? "Zitationsdatei wurde heruntergeladen."
          : "Citation file downloaded.",
      );
    } catch (error) {
      toast.error(downloadErrorMessage(error, isGerman));
    } finally {
      setDownloading(null);
    }
  };

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-9 rounded-full px-2.5 sm:h-8"
          aria-busy={downloading !== null}
          aria-label={isGerman
            ? `${documentIds.length} ausgewählte Paper als Zitationen exportieren`
            : `Export citations for ${documentIds.length} selected papers`}
        >
          {downloading ? <Loader2 className="size-3.5 animate-spin" /> : <Download className="size-3.5" />}
          {isGerman ? "Exportieren" : "Export"}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-[min(16rem,calc(100vw-2rem))]">
        <DropdownMenuLabel className="font-mono text-[0.625rem] uppercase tracking-[0.16em] text-muted-foreground">
          {isGerman ? "Zitationsformat" : "Citation format"}
        </DropdownMenuLabel>
        {tooMany ? (
          <DropdownMenuLabel className="whitespace-normal text-[0.6875rem] font-normal leading-relaxed text-destructive">
            {isGerman
              ? "Wähle höchstens 100 Paper für einen Export aus. Deine Auswahl bleibt erhalten."
              : "Select no more than 100 papers for one export. Your selection is preserved."}
          </DropdownMenuLabel>
        ) : null}
        {CITATION_FORMATS.map((format) => (
          <DropdownMenuItem
            key={format.value}
            disabled={tooMany || downloading !== null}
            onSelect={() => void download(format.value)}
          >
            {isGerman ? format.de : format.en}
            <span className="ml-auto font-mono text-[0.6875rem] text-muted-foreground">
              {format.extension}
            </span>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function PaperCitationSection({
  documentId,
  metadataRevision,
}: {
  documentId: number;
  metadataRevision: string;
}) {
  const { me } = useAuth();
  const isGerman = me?.language === "de";
  const userId = me?.user_id ?? null;
  const [format, setFormat] = useState<LibraryCitationFormat>("apa");
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const citations = useQuery({
    queryKey: ["library-document-citations", userId, documentId, metadataRevision],
    queryFn: () => api.libraryDocumentCitations(documentId),
    enabled: userId !== null,
    retry: false,
  });
  const citation = citations.data?.citations[format].value ?? "";

  useEffect(() => {
    setCopyState("idle");
  }, [format, documentId, citation]);

  useEffect(() => {
    if (copyState !== "copied") return;
    const timer = window.setTimeout(() => setCopyState("idle"), 1_800);
    return () => window.clearTimeout(timer);
  }, [copyState]);

  const copy = async () => {
    if (!citation) return;
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(citation);
      setCopyState("copied");
      toast.success(isGerman ? "Zitation kopiert." : "Citation copied.");
    } catch {
      setCopyState("failed");
      toast.error(
        isGerman
          ? "Die Zitation konnte nicht kopiert werden. Prüfe die Browser-Berechtigung und versuche es erneut."
          : "The citation could not be copied. Check the browser permission and try again.",
      );
    }
  };

  return (
    <section
      aria-labelledby={`paper-citation-${documentId}`}
      className="rounded-2xl border border-border bg-muted/20 p-4"
      data-paper-citation-section
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h3 id={`paper-citation-${documentId}`} className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-moss">
            {isGerman ? "Zitation" : "Citation"}
          </h3>
          <p className="mt-1 text-[0.71875rem] leading-relaxed text-muted-foreground">
            {isGerman
              ? "Vom Server aus den aktuell gespeicherten Paper-Metadaten erzeugt."
              : "Rendered by the server from the paper metadata currently saved."}
          </p>
        </div>
        <div className="w-full sm:w-44">
          <label htmlFor={`paper-citation-format-${documentId}`} className="sr-only">
            {isGerman ? "Zitationsformat" : "Citation format"}
          </label>
          <Select value={format} onValueChange={(value) => setFormat(value as LibraryCitationFormat)}>
            <SelectTrigger
              id={`paper-citation-format-${documentId}`}
              size="sm"
              className="h-9 w-full rounded-full"
              aria-label={isGerman ? "Zitationsformat wählen" : "Choose citation format"}
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent align="end">
              {CITATION_FORMATS.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {isGerman ? item.de : item.en}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {citations.isLoading ? (
        <div role="status" className="mt-4 flex min-h-24 items-center justify-center gap-2 rounded-xl bg-background/65 text-[0.75rem] text-muted-foreground">
          <Loader2 className="size-3.5 animate-spin" />
          {isGerman ? "Zitationen werden erstellt …" : "Rendering citations …"}
        </div>
      ) : citations.isError ? (
        <div role="alert" className="mt-4 rounded-xl border border-destructive/20 bg-destructive/5 p-3">
          <p className="text-[0.75rem] leading-relaxed text-destructive">
            {isGerman
              ? "Die Zitationen konnten gerade nicht geladen werden."
              : "Citations could not be loaded right now."}
          </p>
          <Button type="button" variant="outline" size="sm" className="mt-3 h-8 rounded-full" onClick={() => void citations.refetch()}>
            <RefreshCw className="size-3.5" />
            {isGerman ? "Erneut versuchen" : "Try again"}
          </Button>
        </div>
      ) : (
        <>
          <div className="relative mt-4 rounded-xl border border-border bg-background/80 p-3 pr-12">
            <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words font-mono text-[0.71875rem] leading-relaxed text-foreground">
              {citation || (isGerman ? "Für dieses Format ist keine Zitation verfügbar." : "No citation is available for this format.")}
            </pre>
            <Button
              type="button"
              variant="outline"
              size="icon-sm"
              className="absolute right-2 top-2 rounded-full"
              disabled={!citation}
              onClick={() => void copy()}
              aria-label={isGerman ? `${format.toUpperCase()}-Zitation kopieren` : `Copy ${format.toUpperCase()} citation`}
            >
              {copyState === "copied" ? <Check className="size-3.5 text-moss" /> : <Clipboard className="size-3.5" />}
            </Button>
          </div>
          <p aria-live="polite" className="mt-2 min-h-4 text-[0.65625rem] text-muted-foreground">
            {copyState === "copied"
              ? isGerman ? "In die Zwischenablage kopiert." : "Copied to the clipboard."
              : copyState === "failed"
                ? isGerman ? "Kopieren fehlgeschlagen." : "Copy failed."
                : ""}
          </p>
          {(citations.data?.missing_fields.length ?? 0) > 0 ? (
            <div className="mt-2 flex items-start gap-2 rounded-xl border border-amber-400/20 bg-amber-400/5 p-3 text-[0.6875rem] leading-relaxed text-muted-foreground">
              <TriangleAlert className="mt-0.5 size-3.5 shrink-0 text-amber-500" />
              <p>
                {isGerman
                  ? "Mit den vorhandenen Metadaten erzeugt. Für eine vollständigere Zitation fehlen: "
                  : "Rendered with the available metadata. For a more complete citation, add: "}
                <span className="text-foreground">
                  {citations.data?.missing_fields.map((field) => missingFieldLabel(field, isGerman)).join(" · ")}
                </span>
              </p>
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}
