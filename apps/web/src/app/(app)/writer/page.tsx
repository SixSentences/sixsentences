"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ExternalLink,
  Check,
  Copy,
  FileCode2,
  FileUp,
  Link2,
  Loader2,
  PenLine,
  Plus,
  Presentation,
  ShieldCheck,
  Share2,
  Trash2,
  TriangleAlert,
} from "lucide-react";
import { toast } from "sonner";

import { ConfirmDeleteDialog } from "@/components/confirm-delete-dialog";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { track } from "@/lib/analytics";
import { api, fileToBase64 } from "@/lib/api";
import { formatBytes, formatDate } from "@/lib/format";
import { useAuth } from "@/lib/auth";
import { useActiveProject } from "@/lib/project-context";
import type { WriterSummary, WriterTemplate } from "@/lib/types";
import { cn } from "@/lib/utils";

/* Stylized page miniatures: each template card shows what the layout
   actually produces instead of a random icon. */

function Bar({ w = "w-full", tone = "bg-pine/12" }: { w?: string; tone?: string }) {
  return <span className={cn("block h-[3px] rounded-full", w, tone)} />;
}

function TwoCol() {
  return (
    <div className="grid grid-cols-2 gap-1.5">
      {[0, 1].map((col) => (
        <div key={col} className="space-y-1">
          {Array.from({ length: 6 }, (_, i) => (
            <Bar key={i} w={i === 5 ? "w-2/3" : "w-full"} />
          ))}
        </div>
      ))}
    </div>
  );
}

function OneCol({ lines = 6 }: { lines?: number }) {
  return (
    <div className="space-y-1">
      {Array.from({ length: lines }, (_, i) => (
        <Bar key={i} w={i % 3 === 2 ? "w-3/4" : "w-full"} />
      ))}
    </div>
  );
}

function OwnTemplateMiniature({ template }: { template: WriterTemplate }) {
  const isProject = (template.origin?.file_count ?? 0) > 1;
  return (
    <div className="relative aspect-[16/10] overflow-hidden rounded-2xl border border-border/70 bg-[radial-gradient(circle_at_18%_12%,hsl(var(--accent)),transparent_58%)] p-3 dark:bg-[radial-gradient(circle_at_18%_12%,hsl(var(--secondary)),transparent_62%)]">
      {isProject ? (
        <div className="absolute inset-x-[25%] bottom-2 top-4 rotate-[4deg] rounded-sm border border-pine/10 bg-[#eeeae0] shadow-sm" />
      ) : null}
      <div className="relative mx-auto flex h-full w-[64%] flex-col overflow-hidden rounded-[0.2rem] border border-pine/10 bg-[#fbfaf6] px-3 py-2.5 shadow-[0_8px_24px_rgba(12,29,25,0.16)] transition-transform duration-300 group-hover:-translate-y-0.5 group-hover:shadow-[0_12px_30px_rgba(12,29,25,0.2)]">
        <div className="border-b border-pine/10 pb-1 text-center font-mono text-[0.34rem] uppercase tracking-[0.14em] text-pine/40">
          {template.origin?.provider ?? "own format"}
        </div>
        <div className="flex flex-1 flex-col pt-1.5">
          <Bar w="w-4/5 mx-auto" tone="bg-moss-surface/65" />
          <Bar w="mt-1 w-2/5 mx-auto" tone="bg-pine/20" />
          <div className="mt-2 grid flex-1 grid-cols-2 gap-1.5">
            <OneCol lines={5} />
            <div className="space-y-1">
              <OneCol lines={2} />
              <div className="my-1 h-4 rounded-sm border border-moss/25 bg-accent/70" />
              <OneCol lines={2} />
            </div>
          </div>
        </div>
      </div>
      <span className="absolute bottom-2 left-2.5 rounded-full border border-border/70 bg-card/90 px-2 py-0.5 font-mono text-[0.42rem] uppercase tracking-[0.14em] text-muted-foreground backdrop-blur">
        {isProject ? `${template.origin?.file_count} files` : "LaTeX"}
      </span>
    </div>
  );
}

const MINIS: Record<string, React.ReactNode> = {
  article: (
    <>
      <Bar w="w-2/3 mx-auto" tone="bg-moss-surface/70" />
      <Bar w="w-1/3 mx-auto" tone="bg-pine/20" />
      <div className="pt-1.5">
        <TwoCol />
      </div>
    </>
  ),
  review: (
    <>
      <Bar w="w-3/4 mx-auto" tone="bg-moss-surface/70" />
      <div className="pt-1.5">
        <OneCol lines={3} />
      </div>
      <div className="mx-auto grid w-2/3 gap-1 pt-1">
        <span className="block h-2.5 rounded-sm border border-moss/40 bg-accent" />
        <span className="mx-auto block h-1.5 w-px bg-pine/30" />
        <span className="block h-2.5 rounded-sm border border-moss/40 bg-accent" />
      </div>
      <div className="pt-1">
        <OneCol lines={2} />
      </div>
    </>
  ),
  thesis: (
    <>
      <div className="flex h-full flex-col items-center justify-center gap-1.5 py-3">
        <Bar w="w-3/4" tone="bg-moss-surface/70" />
        <Bar w="w-1/2" tone="bg-pine/25" />
        <Bar w="w-1/3" tone="bg-pine/15" />
      </div>
    </>
  ),
  ieee: (
    <>
      <Bar w="w-full" tone="bg-pine/30" />
      <Bar w="w-1/2 mx-auto" tone="bg-moss-surface/60" />
      <div className="pt-1.5">
        <TwoCol />
      </div>
    </>
  ),
  report: (
    <>
      <Bar w="w-1/2" tone="bg-moss-surface/70" />
      <Bar w="w-1/4" tone="bg-pine/20" />
      <div className="pt-1.5">
        <OneCol lines={7} />
      </div>
    </>
  ),
  proposal: (
    <>
      <Bar w="w-2/3" tone="bg-moss-surface/70" />
      <div className="space-y-1.5 pt-1.5">
        {[0, 1, 2].map((i) => (
          <div key={i} className="flex items-center gap-1">
            <span className="block size-1.5 shrink-0 rounded-full bg-moss-surface/50" />
            <Bar w={i === 1 ? "w-3/4" : "w-full"} />
          </div>
        ))}
      </div>
      <div className="pt-1">
        <OneCol lines={3} />
      </div>
    </>
  ),
  beamer: (
    <div className="flex h-full items-center justify-center">
      <div className="aspect-video w-full rounded-sm border border-pine/15 bg-pine/[0.03] p-1.5">
        <Bar w="w-1/2" tone="bg-moss-surface/70" />
        <div className="space-y-1 pt-1.5">
          <Bar w="w-3/4" />
          <Bar w="w-2/3" />
          <Bar w="w-3/4" />
        </div>
      </div>
    </div>
  ),
  abstract: (
    <>
      <Bar w="w-1/2 mx-auto" tone="bg-moss-surface/70" />
      <div className="pt-1.5">
        <OneCol lines={9} />
      </div>
    </>
  ),
  blank: (
    <div className="flex h-full items-center justify-center">
      <span className="font-mono text-[0.5rem] uppercase tracking-[0.18em] text-foreground/25">
        tabula rasa
      </span>
    </div>
  ),
};

const TEMPLATES = [
  {
    id: "article",
    label: "Article",
    labelDe: "Fachartikel",
    hint: "Twocolumn paper with abstract and natbib. The conference shape.",
    hintDe: "Zweispaltiges Paper mit Abstract und natbib für Konferenzen.",
  },
  {
    id: "review",
    label: "Literature review",
    labelDe: "Literaturreview",
    hint: "Method, PRISMA, evidence table: built for your search exports.",
    hintDe: "Methode, PRISMA und Evidenztabelle für deine Recherche-Exporte.",
  },
  {
    id: "thesis",
    label: "Thesis",
    labelDe: "Abschlussarbeit",
    hint: "Report class, chapters, table of contents. The long form.",
    hintDe: "Report-Klasse, Kapitel und Inhaltsverzeichnis für längere Arbeiten.",
  },
  {
    id: "ieee",
    label: "IEEE conference",
    labelDe: "IEEE-Konferenz",
    hint: "The official IEEEtran skeleton: author blocks, keywords, [1] cites.",
    hintDe: "Offizielles IEEEtran-Gerüst mit Autorenblöcken, Keywords und Zitaten.",
  },
  {
    id: "report",
    label: "Seminar report",
    labelDe: "Seminararbeit",
    hint: "Clean onecolumn article for coursework and short reports.",
    hintDe: "Klare einspaltige Vorlage für Coursework und kurze Reports.",
  },
  {
    id: "proposal",
    label: "Research proposal",
    labelDe: "Forschungsexposé",
    hint: "Problem, gap, research questions, plan. The exposé shape.",
    hintDe: "Problem, Forschungslücke, Forschungsfragen und Arbeitsplan.",
  },
  {
    id: "beamer",
    label: "Beamer slides",
    labelDe: "Beamer-Folien",
    hint: "16:9 metropolis deck: agenda, blocks, results table, standout.",
    hintDe: "16:9-Metropolis-Deck mit Agenda, Blöcken und Ergebnistabelle.",
  },
  {
    id: "abstract",
    label: "Extended abstract",
    labelDe: "Extended Abstract",
    hint: "One dense page: motivation, method, result, significance.",
    hintDe: "Eine kompakte Seite für Motivation, Methode, Ergebnis und Relevanz.",
  },
  {
    id: "blank",
    label: "Blank",
    labelDe: "Leeres Dokument",
    hint: "A document class and your bibliography. Start from zero.",
    hintDe: "Nur Dokumentklasse und Bibliografie. Starte bei null.",
  },
] as const;

const STATUS_DOT: Record<WriterSummary["compile_status"], string> = {
  none: "bg-border",
  running: "bg-amber-400",
  ok: "bg-moss-surface",
  error: "bg-destructive",
} as Record<WriterSummary["compile_status"], string>;

export default function WriterListPage() {
  const { me } = useAuth();
  const isGerman = me?.language === "de";
  const router = useRouter();
  const queryClient = useQueryClient();
  const { activeProjectId } = useActiveProject();
  const { data: docs, isLoading } = useQuery({
    queryKey: ["writer-docs"],
    queryFn: api.writerList,
  });
  const { data: ownTemplates } = useQuery({
    queryKey: ["writer-templates"],
    queryFn: api.writerTemplates,
  });
  const openImport = (input: HTMLInputElement | null) => input?.click();

  const create = useMutation({
    mutationFn: (source: { template?: string; templateId?: number }) =>
      api.writerCreate(
        "Untitled",
        source.template ?? "blank",
        [],
        source.templateId,
        activeProjectId ?? undefined,
      ),
    onSuccess: (doc, source) => {
      track("doc_created", {
        source: source.templateId ? "own_template" : (source.template ?? "blank"),
      });
      void queryClient.invalidateQueries({ queryKey: ["writer-docs"] });
      router.push(`/writer/${doc.public_id ?? doc.id}`);
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "That didn't work."),
  });

  // own templates: create via paste or .tex upload, delete, start from
  const [tplOpen, setTplOpen] = useState(false);
  const [tplName, setTplName] = useState("");
  const [tplContent, setTplContent] = useState("");
  const [linkOpen, setLinkOpen] = useState(false);
  const [linkUrl, setLinkUrl] = useState("");
  const [rightsConfirmed, setRightsConfirmed] = useState(false);
  const [sharedToken, setSharedToken] = useState("");
  const [shareTarget, setShareTarget] = useState<WriterTemplate | null>(null);
  const [shareCopied, setShareCopied] = useState(false);
  const texFileRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    setSharedToken(new URLSearchParams(window.location.search).get("template") ?? "");
  }, []);
  const sharedPreview = useQuery({
    queryKey: ["writer-template-shared", sharedToken],
    queryFn: () => api.writerTemplateSharedPreview(sharedToken),
    enabled: Boolean(sharedToken),
    retry: false,
  });
  const saveTemplate = useMutation({
    mutationFn: () => api.writerTemplateCreate(tplName.trim(), tplContent),
    onSuccess: (saved) => {
      toast.success(`Template "${saved.name}" saved.`);
      setTplOpen(false);
      setTplName("");
      setTplContent("");
      void queryClient.invalidateQueries({ queryKey: ["writer-templates"] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "That didn't work."),
  });
  const removeTemplate = useMutation({
    mutationFn: (id: number) => api.writerTemplateDelete(id),
    onSuccess: () => {
      setConfirmDelete(null);
      toast.success("Template deleted.");
      void queryClient.invalidateQueries({ queryKey: ["writer-templates"] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "That didn't work."),
  });
  const createTemplateShare = useMutation({
    mutationFn: (id: number) => api.writerTemplateShare(id),
    onError: (error) => {
      setShareTarget(null);
      toast.error(error instanceof Error ? error.message : "Share link failed.");
    },
  });
  const revokeTemplateShare = useMutation({
    mutationFn: (id: number) => api.writerTemplateShareRevoke(id),
    onSuccess: () => {
      setShareTarget(null);
      createTemplateShare.reset();
      setShareCopied(false);
      toast.success(isGerman ? "Freigabelink widerrufen." : "Share link revoked.");
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Revoke failed."),
  });
  const importSharedTemplate = useMutation({
    mutationFn: () => api.writerTemplateSharedImport(sharedToken),
    onSuccess: (saved) => {
      toast.success(
        isGerman
          ? `Vorlage „${saved.name}“ wurde in deinen Workspace kopiert.`
          : `Template “${saved.name}” was copied into your workspace.`,
      );
      setSharedToken("");
      void queryClient.invalidateQueries({ queryKey: ["writer-templates"] });
      router.replace("/writer");
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Template import failed."),
  });
  const inspectLinkedTemplate = useMutation({
    mutationFn: () => api.writerTemplateLinkPreview(linkUrl.trim()),
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "The link could not be inspected."),
  });
  const importLinkedTemplate = useMutation({
    mutationFn: () =>
      api.writerTemplateLinkImport(linkUrl.trim(), rightsConfirmed),
    onSuccess: (saved) => {
      toast.success(`Template "${saved.name}" imported with its project files.`);
      setLinkOpen(false);
      setLinkUrl("");
      setRightsConfirmed(false);
      inspectLinkedTemplate.reset();
      void queryClient.invalidateQueries({ queryKey: ["writer-templates"] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Template import failed."),
  });

  // imports: semantic Word conversion or a complete multi-file LaTeX project
  const docxFileRef = useRef<HTMLInputElement>(null);
  const latexFileRef = useRef<HTMLInputElement>(null);
  const importDocument = useMutation({
    mutationFn: async (file: File) =>
      api.writerImport(
        file.name,
        await fileToBase64(file),
        activeProjectId ?? undefined,
      ),
    onSuccess: (doc) => {
      track("doc_created", { source: "import" });
      toast.success("Imported as an editable manuscript project.");
      void queryClient.invalidateQueries({ queryKey: ["writer-docs"] });
      router.push(`/writer/${doc.public_id ?? doc.id}`);
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Import failed."),
  });
  const [confirmDelete, setConfirmDelete] = useState<
    { kind: "doc" | "template"; id: number } | null
  >(null);
  const remove = useMutation({
    mutationFn: (id: number) => api.writerDelete(id),
    onSuccess: () => {
      setConfirmDelete(null);
      toast.success("Document deleted.");
      void queryClient.invalidateQueries({ queryKey: ["writer-docs"] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "That didn't work."),
  });
  const slides = useMutation({
    mutationFn: (id: string) => api.writerCreateSlides(id),
    onSuccess: (created) => {
      void queryClient.invalidateQueries({ queryKey: ["writer-docs"] });
      toast.success(isGerman ? "Folien erstellt." : "Slides drafted.");
      router.push(`/writer/${created.public_id ?? created.id}`);
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "That didn't work."),
  });
  const shareUrl =
    createTemplateShare.data && typeof window !== "undefined"
      ? `${window.location.origin}/writer?template=${createTemplateShare.data.token}`
      : "";

  return (
    <div className="h-full overflow-y-auto">
      <div className="w-full px-4 pb-20 pt-6 sm:px-6 sm:pt-10 lg:px-10 2xl:px-14">
        <p className="flex items-center gap-2 font-mono text-[0.625rem] uppercase tracking-[0.22em] text-moss">
          <PenLine className="size-3.5 text-moss" /> Manuscript Studio
        </p>
        <h1 className="mt-2 font-display text-[clamp(2.1rem,10vw,2.4rem)] font-normal leading-none text-foreground">
          {isGerman
            ? "Baue ein Manuskript, nicht nur ein Dokument."
            : "Build a manuscript, not just a document."}
        </h1>
        <p className="mt-1 max-w-xl text-[0.875rem] text-muted-foreground">
          {isGerman
            ? "LaTeX-Dokumente, die mit deinen Recherchen verbunden sind: Die Bibliografie bleibt mit deinem Include-Set synchron, jede Zitation ist einen Klick entfernt und das PDF rendert direkt neben dem Text."
            : "LaTeX documents wired to your searches: the bibliography stays in sync with your include set, every citation is one click away, and the PDF renders right next to your text."}
        </p>
        <div className="mt-8 grid items-start gap-8 2xl:grid-cols-[minmax(0,1fr)_21rem] min-[1850px]:grid-cols-[minmax(0,1fr)_23rem]">
          <section data-tour="writer-templates">
            <p className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
              {isGerman ? "Mit einer Vorlage starten" : "Start from a template"}
            </p>
            <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3 min-[1850px]:grid-cols-4">
              <button
                type="button"
                onClick={() => setTplOpen(true)}
                className="group cursor-pointer rounded-3xl border border-moss/30 bg-accent/25 p-3.5 text-left transition-all hover:-translate-y-0.5 hover:border-moss/60 hover:shadow-sm"
              >
                <div className="grid aspect-[16/10] place-items-center rounded-lg border border-dashed border-moss/35 bg-background transition-colors group-hover:border-moss/60 dark:bg-secondary/60">
                  <Plus className="size-5 text-moss-surface" />
                </div>
                <p className="mt-3 text-[0.9375rem] font-medium text-foreground">
                  {isGerman ? "Eigene Vorlage erstellen" : "Create your own template"}
                </p>
                <p className="mt-0.5 text-[0.75rem] leading-relaxed text-muted-foreground">
                  {isGerman
                    ? "LaTeX einfügen oder eine .tex-Datei laden und im Workspace wiederverwenden."
                    : "Paste LaTeX or load a .tex file. Reuse it across the workspace."}
                </p>
              </button>
              <button
                type="button"
                disabled={importDocument.isPending}
                onClick={() => openImport(docxFileRef.current)}
                className={cn(
                  "group cursor-pointer rounded-3xl border border-moss/30 bg-accent/25 p-3.5 text-left transition-all hover:-translate-y-0.5 hover:border-moss/60 hover:shadow-sm disabled:opacity-60",
                )}
              >
                <div className="grid aspect-[16/10] place-items-center rounded-lg border border-dashed border-moss/35 bg-background transition-colors group-hover:border-moss/60 dark:bg-secondary/60">
                  {importDocument.isPending ? (
                    <Loader2 className="size-5 animate-spin text-moss-surface" />
                  ) : (
                    <FileUp className="size-5 text-moss-surface" />
                  )}
                </div>
                <p className="mt-3 text-[0.9375rem] font-medium text-foreground">
                  <span className="flex items-center justify-between gap-2">
                    {isGerman ? "Word importieren (.docx)" : "Import Word (.docx)"}
                  </span>
                </p>
                <p className="mt-0.5 text-[0.75rem] leading-relaxed text-muted-foreground">
                  {isGerman
                    ? "Überschriften, Listen und Tabellen in ein strukturiertes Manuskript übernehmen."
                    : "Bring headings, lists and tables into a structured manuscript."}
                </p>
              </button>
              <button
                type="button"
                onClick={() => setLinkOpen(true)}
                className={cn(
                  "group cursor-pointer rounded-3xl border border-moss/30 bg-accent/25 p-3.5 text-left transition-all hover:-translate-y-0.5 hover:border-moss/60 hover:shadow-sm",
                )}
              >
                <div className="grid aspect-[16/10] place-items-center rounded-lg border border-dashed border-moss/35 bg-background transition-colors group-hover:border-moss/60 dark:bg-secondary/60">
                  <Link2 className="size-5 text-moss-surface" />
                </div>
                <p className="mt-3 text-[0.9375rem] font-medium text-foreground">
                  <span className="flex items-center justify-between gap-2">
                    {isGerman
                      ? "Vorlage per Link importieren"
                      : "Import template from link"}
                  </span>
                </p>
                <p className="mt-0.5 text-[0.75rem] leading-relaxed text-muted-foreground">
                  {isGerman
                    ? "Overleaf, GitHub oder einen öffentlichen ZIP-/TeX-Link mit Quelle und Lizenz übernehmen."
                    : "Bring in Overleaf, GitHub or a public ZIP/TeX link with source and license provenance."}
                </p>
              </button>
              <button
                type="button"
                disabled={importDocument.isPending}
                onClick={() => openImport(latexFileRef.current)}
                className={cn(
                  "group cursor-pointer rounded-3xl border border-moss/30 bg-accent/25 p-3.5 text-left transition-all hover:-translate-y-0.5 hover:border-moss/60 hover:shadow-sm disabled:opacity-60",
                )}
              >
                <div className="grid aspect-[16/10] place-items-center rounded-lg border border-dashed border-moss/35 bg-background transition-colors group-hover:border-moss/60 dark:bg-secondary/60">
                  {importDocument.isPending ? (
                    <Loader2 className="size-5 animate-spin text-moss-surface" />
                  ) : (
                    <FileCode2 className="size-5 text-moss-surface" />
                  )}
                </div>
                <p className="mt-3 text-[0.9375rem] font-medium text-foreground">
                  <span className="flex items-center justify-between gap-2">
                    {isGerman ? "LaTeX-Projekt importieren" : "Import LaTeX project"}
                  </span>
                </p>
                <p className="mt-0.5 text-[0.75rem] leading-relaxed text-muted-foreground">
                  {isGerman
                    ? "Eine .tex-Datei oder ein vollständiges .zip-Projekt mit Ordnern und Assets öffnen."
                    : "Open a .tex file or a full .zip project with folders and assets."}
                </p>
              </button>
              {TEMPLATES.map((template) => (
                <button
                  key={template.id}
                  type="button"
                  disabled={create.isPending}
                  onClick={() => create.mutate({ template: template.id })}
                  className="group cursor-pointer rounded-3xl border border-border bg-card p-3.5 text-left transition-all hover:-translate-y-0.5 hover:border-moss/50 hover:shadow-sm disabled:opacity-60"
                >
                  <div className="relative aspect-[16/10] overflow-hidden rounded-2xl border border-border/70 bg-[radial-gradient(circle_at_top_left,hsl(var(--accent)),transparent_58%)] p-3 transition-colors group-hover:border-moss/35 dark:bg-[radial-gradient(circle_at_top_left,hsl(var(--secondary)),transparent_62%)]">
                    <span className="absolute left-3 top-2.5 font-mono text-[0.42rem] uppercase tracking-[0.2em] text-muted-foreground/70">
                      {template.id === "beamer" ? "slide" : "page 1"}
                    </span>
                    <div className="mx-auto flex h-full w-[62%] flex-col gap-1 overflow-hidden rounded-[0.2rem] border border-pine/10 bg-[#fbfaf6] px-3 py-2.5 shadow-[0_8px_24px_rgba(12,29,25,0.14)] transition-transform duration-300 group-hover:-translate-y-0.5 group-hover:shadow-[0_12px_30px_rgba(12,29,25,0.18)]">
                      <div className="mb-0.5 flex items-center justify-between border-b border-pine/10 pb-1 font-mono text-[0.35rem] uppercase tracking-[0.14em] text-pine/35">
                        <span>SixSentences_</span>
                        <span>{template.id}</span>
                      </div>
                      <div className="flex min-h-0 flex-1 flex-col gap-1">
                        {MINIS[template.id]}
                      </div>
                    </div>
                  </div>
                  <p className="mt-3 text-[0.9375rem] font-medium text-foreground">
                    {isGerman ? template.labelDe : template.label}
                  </p>
                  <p className="mt-0.5 text-[0.75rem] leading-relaxed text-muted-foreground">
                    {isGerman ? template.hintDe : template.hint}
                  </p>
                </button>
              ))}
            </div>
            <input
              ref={docxFileRef}
              type="file"
              accept=".docx"
              className="hidden"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) importDocument.mutate(file);
                event.target.value = "";
              }}
            />
            <input
              ref={latexFileRef}
              type="file"
              accept=".tex,.latex,.zip,application/zip"
              className="hidden"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) importDocument.mutate(file);
                event.target.value = "";
              }}
            />

            {(ownTemplates ?? []).length > 0 ? (
              <p className="mt-8 font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
                {isGerman ? "Deine Vorlagen" : "Your templates"}
              </p>
            ) : null}
            {(ownTemplates ?? []).length === 0 ? null : (
              <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3 min-[1850px]:grid-cols-4">
                {(ownTemplates ?? []).map((template) => (
                  <div
                    key={template.id}
                    className="group relative rounded-3xl border border-border bg-card p-4 transition-all hover:-translate-y-0.5 hover:border-moss/50 hover:shadow-sm"
                  >
                    <button
                      type="button"
                      disabled={create.isPending}
                      onClick={() => create.mutate({ templateId: template.id })}
                      className="w-full cursor-pointer text-left"
                    >
                      <OwnTemplateMiniature template={template} />
                      <p className="mt-3 truncate text-[0.9375rem] font-medium text-foreground">
                        {template.name}
                      </p>
                      <p className="mt-0.5 text-[0.75rem] text-muted-foreground">
                        {formatDate(template.updated_at, isGerman ? "de" : "en")} ·{" "}
                        {template.chars.toLocaleString()} chars
                      </p>
                      {template.origin ? (
                        <p className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[0.6875rem] text-muted-foreground">
                          <span className="rounded-full bg-secondary px-2 py-0.5 font-mono uppercase tracking-[0.12em]">
                            {template.origin.provider}
                          </span>
                          <span>
                            {template.origin.file_count} files ·{" "}
                            {template.origin.license_name}
                          </span>
                        </p>
                      ) : null}
                    </button>
                    {template.origin ? (
                      <a
                        href={template.origin.source_url}
                        target="_blank"
                        rel="noreferrer"
                        className="mt-2 inline-flex items-center gap-1 text-[0.6875rem] text-moss hover:underline"
                      >
                        {isGerman ? "Quelle und Lizenz" : "Source and license"}
                        <ExternalLink className="size-3" />
                      </a>
                    ) : null}
                    <div className="absolute right-2.5 top-2.5 flex items-center gap-1 rounded-xl border border-border/70 bg-card/90 p-0.5 opacity-100 shadow-sm backdrop-blur transition-opacity sm:opacity-0 sm:group-hover:opacity-100">
                      <Button
                        variant="ghost"
                        size="icon"
                        className="size-7 rounded-lg text-muted-foreground hover:text-moss"
                        onClick={() => {
                          setShareTarget(template);
                          setShareCopied(false);
                          createTemplateShare.reset();
                          createTemplateShare.mutate(template.id);
                        }}
                        aria-label={`Share template ${template.name}`}
                      >
                        <Share2 className="size-3.5" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="size-7 rounded-lg text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                        onClick={() =>
                          setConfirmDelete({ kind: "template", id: template.id })
                        }
                        aria-label={`Delete template ${template.name}`}
                      >
                        <Trash2 className="size-3.5" />
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section
            data-tour="writer-projects"
            className="order-first rounded-2xl border border-border bg-secondary/20 p-3.5 2xl:order-none 2xl:sticky 2xl:top-6 2xl:mt-[1.625rem] 2xl:max-h-[calc(100vh-5rem)] 2xl:overflow-y-auto"
          >
            <p className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
              {isGerman ? "Manuskriptprojekte" : "Manuscript projects"}
            </p>
            {isLoading ? (
              <Loader2 className="mx-auto my-16 size-5 animate-spin text-muted-foreground" />
            ) : (docs ?? []).length === 0 ? (
              <p className="mt-3 rounded-2xl border border-dashed border-border px-4 py-10 text-center text-[0.8125rem] leading-relaxed text-muted-foreground">
                {isGerman
                  ? "Noch keine Projekte. Wähle eine Vorlage, dann erscheint das Projekt in dieser Liste."
                  : "Nothing here yet. Pick a template and the project will appear in this workspace list."}
              </p>
            ) : (
              <ul className="mt-3 space-y-2">
                {(docs ?? []).map((doc) => (
                  <li key={doc.id}>
                    <div className="group flex items-center gap-3 rounded-2xl border border-border bg-card px-4 py-3 transition-colors hover:border-moss/40">
                      <span
                        className={cn(
                          "size-1.5 shrink-0 rounded-full",
                          STATUS_DOT[doc.compile_status] ?? "bg-border",
                        )}
                        title={`Last compile: ${doc.compile_status}`}
                      />
                      <button
                        type="button"
                        onClick={() => router.push(`/writer/${doc.public_id ?? doc.id}`)}
                        className="min-w-0 flex-1 cursor-pointer text-left"
                      >
                        <p className="truncate text-[0.9375rem] font-medium text-foreground">
                          {doc.title}
                        </p>
                        <p className="mt-0.5 font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
                          {isGerman ? "Bearbeitet" : "Edited"}{" "}
                          {formatDate(doc.updated_at, isGerman ? "de" : "en")}
                          {doc.run_ids.length > 0
                            ? isGerman
                              ? ` · ${doc.run_ids.length} verknüpfte Recherche${doc.run_ids.length === 1 ? "" : "n"}`
                              : ` · ${doc.run_ids.length} linked search${doc.run_ids.length === 1 ? "" : "es"}`
                            : ""}
                        </p>
                      </button>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="size-7 rounded-lg text-muted-foreground opacity-100 transition-opacity hover:text-moss sm:opacity-0 sm:group-hover:opacity-100"
                        disabled={slides.isPending}
                        onClick={() => slides.mutate(doc.public_id ?? String(doc.id))}
                        aria-label={`Draft slides from ${doc.title}`}
                        title={isGerman ? "Folien aus dem Manuskript erstellen" : "Draft slides from the manuscript"}
                      >
                        {slides.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <Presentation className="size-3.5" />}
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="size-7 rounded-lg text-muted-foreground opacity-100 transition-opacity hover:bg-destructive/10 hover:text-destructive sm:opacity-0 sm:group-hover:opacity-100"
                        onClick={() => setConfirmDelete({ kind: "doc", id: doc.id })}
                        aria-label={`Delete ${doc.title}`}
                      >
                        <Trash2 className="size-3.5" />
                      </Button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      </div>

      <ConfirmDeleteDialog
        target={
          confirmDelete === null
            ? null
            : confirmDelete.kind === "doc"
              ? {
                  title: "Delete this manuscript?",
                  description:
                    "The manuscript project and its compiled files will be removed. This cannot be undone.",
                  action: "Delete manuscript",
                  cancel: "Keep manuscript",
                }
              : {
                  title: "Delete this template?",
                  description:
                    "The template will be removed from your gallery. This cannot be undone.",
                  action: "Delete template",
                  cancel: "Keep template",
                }
        }
        pending={remove.isPending || removeTemplate.isPending}
        onCancel={() => setConfirmDelete(null)}
        onConfirm={() => {
          if (!confirmDelete) return;
          if (confirmDelete.kind === "doc") remove.mutate(confirmDelete.id);
          else removeTemplate.mutate(confirmDelete.id);
        }}
      />

      <Dialog
        open={shareTarget !== null}
        onOpenChange={(open) => {
          if (!open) {
            setShareTarget(null);
            setShareCopied(false);
            createTemplateShare.reset();
          }
        }}
      >
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Share2 className="size-4 text-moss" />
              {isGerman ? "Vorlage teilen" : "Share template"}
            </DialogTitle>
            <DialogDescription>
              {isGerman
                ? "Wer den Link besitzt, kann eine eigene, unabhängige Kopie dieser Vorlage importieren. Dein Original bleibt unverändert."
                : "Anyone with the link can import an independent copy of this template. Your original remains unchanged."}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="rounded-2xl border border-border bg-secondary/20 p-4">
              <p className="font-medium text-foreground">{shareTarget?.name}</p>
              <p className="mt-1 text-[0.75rem] leading-relaxed text-muted-foreground">
                {isGerman
                  ? "Projektdateien, Lizenzinformationen und Herkunft werden mit der Kopie übernommen. Der LaTeX Quelltext ist in der öffentlichen Vorschau nicht sichtbar."
                  : "Project files, license metadata and provenance travel with the copy. The public preview never exposes the LaTeX source."}
              </p>
            </div>
            {createTemplateShare.isPending ? (
              <div className="flex h-11 items-center justify-center rounded-xl border border-border">
                <Loader2 className="size-4 animate-spin text-moss" />
              </div>
            ) : shareUrl ? (
              <div className="flex gap-2">
                <Input
                  readOnly
                  value={shareUrl}
                  className="h-10 min-w-0 rounded-xl font-mono text-[0.6875rem]"
                  aria-label={isGerman ? "Freigabelink" : "Share link"}
                />
                <Button
                  type="button"
                  variant="outline"
                  className="h-10 shrink-0 rounded-xl"
                  onClick={() => {
                    void navigator.clipboard.writeText(shareUrl).then(() => {
                      setShareCopied(true);
                      toast.success(isGerman ? "Link kopiert." : "Link copied.");
                    });
                  }}
                >
                  {shareCopied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
                  {shareCopied
                    ? isGerman
                      ? "Kopiert"
                      : "Copied"
                    : isGerman
                      ? "Kopieren"
                      : "Copy"}
                </Button>
              </div>
            ) : null}
            <div className="flex items-center justify-between gap-3 border-t border-border pt-4">
              <p className="max-w-xs text-[0.6875rem] leading-relaxed text-muted-foreground">
                {isGerman
                  ? "Du kannst den Link jederzeit widerrufen. Bereits importierte Kopien bleiben erhalten."
                  : "You can revoke this link at any time. Existing imported copies remain available."}
              </p>
              <Button
                type="button"
                variant="ghost"
                className="shrink-0 rounded-full text-destructive hover:bg-destructive/10 hover:text-destructive"
                disabled={!shareTarget || revokeTemplateShare.isPending}
                onClick={() => shareTarget && revokeTemplateShare.mutate(shareTarget.id)}
              >
                {revokeTemplateShare.isPending ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <Trash2 className="size-3.5" />
                )}
                {isGerman ? "Link widerrufen" : "Revoke link"}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog
        open={Boolean(sharedToken)}
        onOpenChange={(open) => {
          if (!open) {
            setSharedToken("");
            router.replace("/writer");
          }
        }}
      >
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <FileCode2 className="size-4 text-moss" />
              {isGerman ? "Geteilte Vorlage importieren" : "Import shared template"}
            </DialogTitle>
            <DialogDescription>
              {isGerman
                ? "Die Vorlage wird als unabhängige Kopie in deinem Workspace gespeichert. Änderungen wirken sich nicht auf das Original aus."
                : "The template is saved as an independent copy in your workspace. Your edits never affect the original."}
            </DialogDescription>
          </DialogHeader>
          {sharedPreview.isPending ? (
            <div className="grid h-40 place-items-center rounded-2xl border border-border">
              <Loader2 className="size-5 animate-spin text-moss" />
            </div>
          ) : sharedPreview.isError ? (
            <div className="rounded-2xl border border-destructive/30 bg-destructive/5 p-4">
              <p className="font-medium text-foreground">
                {isGerman ? "Dieser Link ist nicht mehr aktiv." : "This link is no longer active."}
              </p>
              <p className="mt-1 text-[0.75rem] text-muted-foreground">
                {isGerman
                  ? "Bitte die Person, die die Vorlage geteilt hat, um einen neuen Link."
                  : "Ask the template owner for a new link."}
              </p>
            </div>
          ) : sharedPreview.data ? (
            <div className="space-y-4">
              <OwnTemplateMiniature
                template={{
                  id: 0,
                  name: sharedPreview.data.name,
                  chars: sharedPreview.data.chars,
                  updated_at: sharedPreview.data.created_at,
                  origin: sharedPreview.data.origin,
                }}
              />
              <div>
                <p className="text-[1rem] font-medium text-foreground">
                  {sharedPreview.data.name}
                </p>
                <p className="mt-1 text-[0.75rem] text-muted-foreground">
                  {sharedPreview.data.chars.toLocaleString()} {isGerman ? "Zeichen" : "characters"}
                  {sharedPreview.data.origin
                    ? ` · ${sharedPreview.data.origin.file_count} ${isGerman ? "Dateien" : "files"} · ${sharedPreview.data.origin.license_name}`
                    : " · LaTeX"}
                </p>
              </div>
              <div className="flex justify-end">
                <Button
                  type="button"
                  className="rounded-full px-5"
                  disabled={importSharedTemplate.isPending}
                  onClick={() => importSharedTemplate.mutate()}
                >
                  {importSharedTemplate.isPending ? (
                    <Loader2 className="size-3.5 animate-spin" />
                  ) : (
                    <FileCode2 className="size-3.5" />
                  )}
                  {isGerman
                    ? "In meinen Workspace importieren"
                    : "Import into my workspace"}
                </Button>
              </div>
            </div>
          ) : null}
        </DialogContent>
      </Dialog>

      <Dialog
        open={linkOpen}
        onOpenChange={(open) => {
          setLinkOpen(open);
          if (!open) {
            inspectLinkedTemplate.reset();
            setRightsConfirmed(false);
          }
        }}
      >
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Link2 className="size-4 text-moss" />
              {isGerman ? "Vorlage aus einem Link" : "Template from a link"}
            </DialogTitle>
            <DialogDescription>
              {isGerman
                ? "Öffentliche Overleaf-Vorlagen, GitHub-Projekte sowie direkte ZIP- und TeX-Links. Vor dem Speichern prüfen wir Projektdateien, Quelle und Lizenz."
                : "Public Overleaf templates, GitHub projects and direct ZIP or TeX links. Project files, provenance and license are checked before saving."}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="flex flex-col gap-2 sm:flex-row">
              <Input
                value={linkUrl}
                onChange={(event) => {
                  setLinkUrl(event.target.value);
                  setRightsConfirmed(false);
                  inspectLinkedTemplate.reset();
                }}
                placeholder="https://www.overleaf.com/latex/templates/…"
                className="h-10 flex-1 rounded-xl text-[0.8125rem]"
                autoComplete="url"
              />
              <Button
                type="button"
                variant="outline"
                className="h-10 rounded-xl px-4"
                disabled={!linkUrl.trim() || inspectLinkedTemplate.isPending}
                onClick={() => inspectLinkedTemplate.mutate()}
              >
                {inspectLinkedTemplate.isPending ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <ShieldCheck className="size-3.5" />
                )}
                {isGerman ? "Prüfen" : "Inspect"}
              </Button>
            </div>

            {inspectLinkedTemplate.data ? (
              <div className="rounded-2xl border border-border bg-secondary/20 p-4">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                  <div className="min-w-0">
                    <p className="font-medium text-foreground">
                      {inspectLinkedTemplate.data.title}
                    </p>
                    <p className="mt-1 text-[0.75rem] text-muted-foreground">
                      {[
                        inspectLinkedTemplate.data.provider,
                        inspectLinkedTemplate.data.author,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </p>
                  </div>
                  <span
                    className={cn(
                      "w-fit shrink-0 rounded-full px-2.5 py-1 font-mono text-[0.625rem] uppercase tracking-[0.12em]",
                      inspectLinkedTemplate.data.license_status === "open"
                        ? "bg-moss/10 text-moss"
                        : "bg-amber-500/10 text-amber-700 dark:text-amber-300",
                    )}
                  >
                    {inspectLinkedTemplate.data.license_name}
                  </span>
                </div>
                <div className="mt-4 grid grid-cols-3 gap-2">
                  {[
                    [
                      isGerman ? "Dateien" : "Files",
                      inspectLinkedTemplate.data.file_count.toLocaleString(),
                    ],
                    [
                      isGerman ? "Assets" : "Assets",
                      inspectLinkedTemplate.data.asset_count.toLocaleString(),
                    ],
                    [
                      isGerman ? "Paket" : "Package",
                      formatBytes(inspectLinkedTemplate.data.package_bytes),
                    ],
                  ].map(([label, value]) => (
                    <div
                      key={label}
                      className="rounded-xl border border-border bg-card px-3 py-2"
                    >
                      <p className="font-mono text-[0.5625rem] uppercase tracking-[0.14em] text-muted-foreground">
                        {label}
                      </p>
                      <p className="mt-0.5 text-[0.8125rem] font-medium text-foreground">
                        {value}
                      </p>
                    </div>
                  ))}
                </div>
                <div className="mt-3 flex flex-wrap gap-3 text-[0.6875rem]">
                  <a
                    href={inspectLinkedTemplate.data.source_url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 text-moss hover:underline"
                  >
                    {isGerman ? "Vorlagenseite" : "Template page"}
                    <ExternalLink className="size-3" />
                  </a>
                  {inspectLinkedTemplate.data.upstream_url !==
                  inspectLinkedTemplate.data.source_url ? (
                    <a
                      href={inspectLinkedTemplate.data.upstream_url}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center gap-1 text-moss hover:underline"
                    >
                      {isGerman ? "Offizielle Projektquelle" : "Official project source"}
                      <ExternalLink className="size-3" />
                    </a>
                  ) : null}
                  {inspectLinkedTemplate.data.license_url ? (
                    <a
                      href={inspectLinkedTemplate.data.license_url}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center gap-1 text-moss hover:underline"
                    >
                      {isGerman ? "Lizenz lesen" : "Read license"}
                      <ExternalLink className="size-3" />
                    </a>
                  ) : null}
                </div>
                <div className="mt-4 border-t border-border pt-3">
                  {inspectLinkedTemplate.data.rights_confirmation_required ? (
                    <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-amber-500/30 bg-amber-500/5 p-3">
                      <Checkbox
                        checked={rightsConfirmed}
                        onCheckedChange={(checked) =>
                          setRightsConfirmed(checked === true)
                        }
                        className="mt-0.5"
                      />
                      <span className="text-[0.75rem] leading-relaxed text-foreground">
                        {isGerman
                          ? "Die Quelle nennt keine eindeutig wiederverwendbare Lizenz. Ich besitze diese Vorlage oder habe die Erlaubnis, sie in diesem privaten Workspace zu verwenden."
                          : "The source does not state a clearly reusable license. I own this template or have permission to use it in this private workspace."}
                      </span>
                    </label>
                  ) : (
                    <p className="flex items-center gap-2 text-[0.75rem] text-moss">
                      <ShieldCheck className="size-4" />
                      {isGerman
                        ? "Die angegebene Lizenz erlaubt die Wiederverwendung mit Attribution."
                        : "The stated license permits reuse with attribution."}
                    </p>
                  )}
                  <p className="mt-2 flex items-start gap-2 text-[0.6875rem] leading-relaxed text-muted-foreground">
                    <TriangleAlert className="mt-0.5 size-3.5 shrink-0" />
                    {isGerman
                      ? "Prüfe vor der Einreichung immer die aktuellen Vorgaben des Journals. Quelle, Lizenz, Prüfsumme und Importzeit bleiben an der Vorlage gespeichert."
                      : "Always verify the venue's current instructions before submission. Source, license, checksum and import time remain attached to the template."}
                  </p>
                </div>
              </div>
            ) : null}

            <div className="flex justify-end">
              <Button
                type="button"
                className="h-9 rounded-full px-5"
                disabled={
                  !inspectLinkedTemplate.data ||
                  importLinkedTemplate.isPending ||
                  (inspectLinkedTemplate.data.rights_confirmation_required &&
                    !rightsConfirmed)
                }
                onClick={() => importLinkedTemplate.mutate()}
              >
                {importLinkedTemplate.isPending ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <Link2 className="size-3.5" />
                )}
                {isGerman ? "Als Vorlage speichern" : "Save as template"}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={tplOpen} onOpenChange={setTplOpen}>
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <FileCode2 className="size-4 text-moss" />
              {isGerman ? "Neue Vorlage" : "New template"}
            </DialogTitle>
            <DialogDescription>
              {isGerman
                ? "Dein eigener Ausgangspunkt für neue Dokumente: Füge unten LaTeX ein oder lade eine .tex-Datei. Im Editor speichert Download > Als Vorlage speichern ein fertiges Setup."
                : "Your own starting point for new documents: paste LaTeX below or load a .tex file. In the editor, Download > Save as template captures a finished setup."}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <Input
              value={tplName}
              onChange={(event) => setTplName(event.target.value)}
              placeholder={isGerman ? "Name der Vorlage" : "Template name"}
              className="h-9 rounded-lg text-[0.8125rem]"
            />
            <Textarea
              value={tplContent}
              onChange={(event) => setTplContent(event.target.value)}
              placeholder={"\\documentclass{article}\n…"}
              className="h-56 resize-none rounded-lg font-mono text-[0.71875rem] leading-relaxed"
            />
            <div className="flex items-center justify-between">
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="h-8 rounded-full text-[0.75rem]"
                onClick={() => texFileRef.current?.click()}
              >
                <FileUp className="size-3" />
                {isGerman ? ".tex-Datei laden" : "Load .tex file"}
              </Button>
              <Button
                size="sm"
                disabled={
                  !tplName.trim() || !tplContent.trim() || saveTemplate.isPending
                }
                onClick={() => saveTemplate.mutate()}
                className="h-8 rounded-full px-4 text-[0.75rem]"
              >
                {saveTemplate.isPending ? (
                  <Loader2 className="size-3 animate-spin" />
                ) : (
                  isGerman ? "Vorlage speichern" : "Save template"
                )}
              </Button>
            </div>
            <input
              ref={texFileRef}
              type="file"
              accept=".tex,.latex,text/x-tex"
              className="hidden"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) {
                  void file.text().then((text) => {
                    setTplContent(text);
                    if (!tplName.trim()) {
                      setTplName(file.name.replace(/\.(tex|latex)$/i, ""));
                    }
                  });
                }
                event.target.value = "";
              }}
            />
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
