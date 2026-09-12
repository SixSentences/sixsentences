"use client";

import { type DragEvent, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowUp,
  ArrowUpDown,
  BookOpen,
  Check,
  CheckSquare2,
  FileText,
  FileUp,
  FolderInput,
  FolderOpen,
  Library as LibraryIcon,
  Loader2,
  MessageSquareText,
  ExternalLink,
  Globe2,
  Search,
  SlidersHorizontal,
  Trash2,
  Users,
  X,
} from "lucide-react";
import { toast } from "sonner";

import type { PaperPanelState } from "@/components/run/paper-panel";
import { BrowserCaptureConnect } from "@/components/library/browser-capture-connect";
import { BrowserCaptureDevices } from "@/components/library/browser-capture-devices";
import { LibraryCitationExportMenu } from "@/components/library/citation-tools";
import { LibraryEntryDetail } from "@/components/library/library-entry-detail";
import {
  LibraryShareDialog,
  SharedWithMeView,
} from "@/components/library/library-share-dialog";
import { PaperPdfDownloadButton } from "@/components/library/paper-pdf-download-button";
import { SourceIdentityMark } from "@/components/library/source-identity-mark";
import {
  PaperMetadataEditor,
  WebSourceMetadataEditor,
} from "@/components/library/metadata-editor";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useLibrary, useLibraryWebSources, useProjects } from "@/hooks/queries";
import { api, fileToBase64 } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatDate } from "@/lib/format";
import { isPdfMimeType, isStoredLibraryPdf } from "@/lib/library-document";
import {
  librarySourceIdentity,
  safeLibrarySourceUrl,
} from "@/lib/library-source-identity";
import { useActiveProject } from "@/lib/project-context";
import type { LibraryDocument, LibraryWebSource, Project, WriterSummary } from "@/lib/types";

// pdf.js touches browser globals at import time
const PaperPanel = dynamic(() => import("@/components/run/paper-panel"), {
  ssr: false,
});

function formatBytes(bytes: number): string {
  if (bytes <= 0) return "–";
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

type LibraryTypeFilter = "all" | "pdf" | "image" | "other";
type LibraryOriginFilter = "all" | "uploaded" | "search";
type LibrarySort =
  | "added-desc"
  | "added-asc"
  | "title-asc"
  | "title-desc"
  | "year-desc"
  | "year-asc"
  | "size-desc";

type LibraryDetailTarget =
  | { kind: "paper"; id: number }
  | { kind: "web"; id: string };

function parseLibraryDetailTarget(value: string | null): LibraryDetailTarget | null {
  if (!value) return null;
  const separator = value.indexOf(":");
  if (separator < 1) return null;
  const kind = value.slice(0, separator);
  const id = value.slice(separator + 1);
  if (kind === "paper") {
    const documentId = Number(id);
    return Number.isSafeInteger(documentId) && documentId > 0
      ? { kind: "paper", id: documentId }
      : null;
  }
  return kind === "web" && id.trim() ? { kind: "web", id } : null;
}

function libraryDocumentType(document: LibraryDocument): Exclude<LibraryTypeFilter, "all"> {
  const contentType = (document.content_type ?? "").toLowerCase();
  if (isPdfMimeType(document.content_type)) return "pdf";
  if (contentType.startsWith("image/")) return "image";
  return "other";
}

function compareNullableYears(
  left: LibraryDocument,
  right: LibraryDocument,
  direction: "asc" | "desc",
): number {
  if (left.year === null && right.year === null) return 0;
  if (left.year === null) return 1;
  if (right.year === null) return -1;
  return direction === "asc" ? left.year - right.year : right.year - left.year;
}

/** "Ask about this paper": one typed question starts a chat with the PDF attached. */
function AskButton({ doc }: { doc: LibraryDocument }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [question, setQuestion] = useState("");

  const ask = useMutation({
    mutationFn: () =>
      api.createRun(doc.project_id, {
        question: question.trim(),
        mode: "ask",
        document_ids: [doc.id],
      }),
    onSuccess: (created) => {
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
      router.push(`/r/${created.public_id}`);
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "That didn't work."),
  });

  return (
    <Popover modal open={open} onOpenChange={setOpen}>
      <Tooltip>
        <TooltipTrigger asChild>
          <PopoverTrigger asChild>
            <Button
              variant="outline"
              size="sm"
              className="h-7 rounded-full text-[0.75rem]"
            >
              <MessageSquareText className="size-3" />
              Ask
            </Button>
          </PopoverTrigger>
        </TooltipTrigger>
        <TooltipContent side="top">Start a chat about this paper</TooltipContent>
      </Tooltip>
      <PopoverContent align="end" className="w-80 p-3">
        <p className="mb-2 text-[0.78125rem] font-medium">
          Ask about this paper
        </p>
        <div className="flex items-center gap-1.5">
          <Input
            autoFocus
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && question.trim().length > 2) {
                ask.mutate();
              }
            }}
            placeholder="What do you want to know?"
            className="h-8 rounded-lg text-[0.8125rem]"
          />
          <Button
            size="icon"
            disabled={question.trim().length < 3 || ask.isPending}
            onClick={() => ask.mutate()}
            className="size-8 shrink-0 rounded-lg"
            aria-label="Ask about this paper"
          >
            {ask.isPending ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <ArrowUp className="size-3.5" />
            )}
          </Button>
        </div>
        <p className="mt-1.5 text-[0.6875rem] text-muted-foreground">
          Opens a new chat with the paper attached.
        </p>
      </PopoverContent>
    </Popover>
  );
}

function FilePaperButton({ doc, projects }: { doc: LibraryDocument; projects: Project[] }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [projectId, setProjectId] = useState<number | null>(doc.project_id);
  const [folder, setFolder] = useState(doc.folder ?? "");
  const move = useMutation({
    mutationFn: () =>
      api.updateLibraryDocument(doc.id, {
        project_id: projectId,
        folder: folder.trim() || null,
      }),
    onSuccess: () => {
      setOpen(false);
      toast.success("Paper filed.");
      void queryClient.invalidateQueries({ queryKey: ["library"] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Could not file the paper."),
  });

  return (
    <Popover modal open={open} onOpenChange={setOpen}>
      <Tooltip>
        <TooltipTrigger asChild>
          <PopoverTrigger asChild>
            <Button variant="ghost" size="icon" className="size-7 rounded-full" aria-label="File paper">
              <FolderInput className="size-3.5" />
            </Button>
          </PopoverTrigger>
        </TooltipTrigger>
        <TooltipContent side="top">Move to project or folder</TooltipContent>
      </Tooltip>
      <PopoverContent align="end" className="w-72 p-3">
        <p className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
          File this paper
        </p>
        <div className="mt-2 grid max-h-40 gap-1 overflow-y-auto">
          <button
            type="button"
            onClick={() => setProjectId(null)}
            className={`rounded-lg px-2.5 py-1.5 text-left text-[0.75rem] ${projectId === null ? "bg-accent text-moss" : "hover:bg-secondary"}`}
          >
            Unfiled
          </button>
          {projects.map((project) => (
            <button
              key={project.id}
              type="button"
              onClick={() => setProjectId(project.id)}
              className={`rounded-lg px-2.5 py-1.5 text-left text-[0.75rem] ${projectId === project.id ? "bg-accent text-moss" : "hover:bg-secondary"}`}
            >
              {project.name}
            </button>
          ))}
        </div>
        <Input
          value={folder}
          onChange={(event) => setFolder(event.target.value)}
          placeholder="Optional folder, e.g. Methods"
          className="mt-2 h-8 rounded-lg text-[0.75rem]"
        />
        <Button size="sm" className="mt-2 w-full rounded-full" onClick={() => move.mutate()} disabled={move.isPending}>
          {move.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <FolderInput className="size-3.5" />}
          Save location
        </Button>
      </PopoverContent>
    </Popover>
  );
}

function DeletePaperButton({
  doc,
  onDeleted,
}: {
  doc: LibraryDocument;
  onDeleted: () => void;
}) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const remove = useMutation({
    mutationFn: () => api.deleteLibraryDocument(doc.id),
    onSuccess: () => {
      setOpen(false);
      onDeleted();
      toast.success("Paper deleted from the library.");
      void queryClient.invalidateQueries({ queryKey: ["library"] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Could not delete the paper."),
  });

  return (
    <AlertDialog open={open} onOpenChange={setOpen}>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            className="size-7 rounded-full text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
            onClick={() => setOpen(true)}
            aria-label={`Delete ${doc.title ?? doc.work_id}`}
          >
            <Trash2 className="size-3.5" />
          </Button>
        </TooltipTrigger>
        <TooltipContent side="top">Delete paper</TooltipContent>
      </Tooltip>
      <AlertDialogContent size="sm">
        <AlertDialogHeader>
          <AlertDialogTitle>Delete this paper?</AlertDialogTitle>
          <AlertDialogDescription>
            The PDF, its saved notes and every library reference to this file
            will be removed from the workspace. This cannot be undone.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Keep paper</AlertDialogCancel>
          <AlertDialogAction
            disabled={remove.isPending}
            onClick={() => remove.mutate()}
            variant="destructive"
          >
            {remove.isPending ? <Loader2 className="size-3.5 animate-spin" /> : null}
            Delete paper
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function DeleteWebSourceButton({
  source,
  onDeleted,
}: {
  source: LibraryWebSource;
  onDeleted?: () => void;
}) {
  const queryClient = useQueryClient();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const remove = useMutation({
    mutationFn: () => api.deleteLibraryWebSource(source.id),
    onSuccess: () => {
      setConfirmOpen(false);
      onDeleted?.();
      toast.success("Web source removed from the Library.");
      void queryClient.invalidateQueries({ queryKey: ["library-web-sources"] });
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Could not remove the source."),
  });

  return (
    <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
      <Button
        variant="ghost"
        size="icon"
        className="size-8 rounded-full text-muted-foreground hover:text-destructive"
        onClick={() => setConfirmOpen(true)}
        aria-label={`Delete ${source.title}`}
      >
        <Trash2 className="size-3.5" />
      </Button>
      <AlertDialogContent size="sm">
        <AlertDialogHeader>
          <AlertDialogTitle>Remove this web source?</AlertDialogTitle>
          <AlertDialogDescription>
            Its saved metadata and selected excerpt will be removed. The original website is unchanged.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Keep source</AlertDialogCancel>
          <AlertDialogAction variant="destructive" disabled={remove.isPending} onClick={() => remove.mutate()}>
            {remove.isPending ? <Loader2 className="size-3.5 animate-spin" /> : null}
            Remove source
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function WebSourceCard({
  source,
  onOpenDetails,
  isGerman,
  active = false,
}: {
  source: LibraryWebSource;
  onOpenDetails: () => void;
  isGerman: boolean;
  active?: boolean;
}) {
  const sourceIdentity = librarySourceIdentity({
    url: source.url,
    siteName: source.site_name,
    fallbackLabel: isGerman ? "Gespeicherte Quelle" : "Saved source",
  });
  return (
    <div className={`rounded-2xl border px-4 py-3 transition-colors ${active ? "border-moss/45 bg-accent/35" : "border-border bg-card hover:border-moss/40"}`}>
      <div className="flex min-w-0 flex-wrap items-start gap-3 @min-[52rem]/library:flex-nowrap">
        <button
          type="button"
          onClick={onOpenDetails}
          className="flex min-w-0 flex-1 items-start gap-3 rounded-xl text-left outline-none focus-visible:ring-2 focus-visible:ring-moss focus-visible:ring-offset-2"
          aria-label={`${isGerman ? "Alle Details anzeigen für" : "View all details for"} ${source.title}`}
          aria-current={active ? "true" : undefined}
        >
          <SourceIdentityMark
            kind="web"
            url={source.url}
            siteName={source.site_name}
            isGerman={isGerman}
          />
          <span className="min-w-0 flex-1">
            <span className="block truncate text-[0.875rem] font-medium">{source.title}</span>
            <span className="mt-0.5 flex flex-wrap gap-x-2 text-[0.71875rem] text-muted-foreground">
              <span>{sourceIdentity.label}</span>
              {source.published_at ? <span>{source.published_at}</span> : null}
              {source.metadata.container_title ? <span>{source.metadata.container_title}</span> : null}
              {source.doi ? <span>DOI {source.doi}</span> : null}
              <span>{formatDate(source.created_at)}</span>
              {source.provenance.captured_at ? <span>Captured {formatDate(source.provenance.captured_at)}</span> : null}
            </span>
            {source.authors.length > 0 ? (
              <span className="mt-1 block truncate text-[0.75rem] text-foreground/70">
                {source.authors.join(", ")}
              </span>
            ) : null}
            {source.selected_excerpt ? (
              <span className="mt-2 line-clamp-2 text-[0.75rem] leading-relaxed text-muted-foreground">
                “{source.selected_excerpt}”
              </span>
            ) : source.description ? (
              <span className="mt-2 line-clamp-2 text-[0.75rem] leading-relaxed text-muted-foreground">
                {source.description}
              </span>
            ) : null}
          </span>
        </button>
        <div
          className="flex w-full shrink-0 flex-wrap items-center justify-end gap-1 @min-[52rem]/library:w-auto"
          onClick={(event) => event.stopPropagation()}
        >
          <WebSourceMetadataEditor source={source} />
          {sourceIdentity.safeUrl ? (
            <Button asChild variant="ghost" size="sm" className="h-8 rounded-full">
              <a href={sourceIdentity.safeUrl} target="_blank" rel="noopener noreferrer">
                <ExternalLink className="size-3.5" /> {isGerman ? "Öffnen" : "Open"}
              </a>
            </Button>
          ) : null}
          <DeleteWebSourceButton source={source} />
        </div>
      </div>
    </div>
  );
}

export default function LibraryPage() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();
  const { me } = useAuth();
  const isGerman = me?.language === "de";
  const userId = me?.user_id ?? null;
  const captureRevisionBaselineRef = useRef<{
    userId: number;
    revision: number;
  } | null>(null);
  const captureRevision = useQuery({
    queryKey: ["browser-capture-revision", userId],
    queryFn: api.browserCaptureRevision,
    enabled: userId !== null,
    refetchInterval: 5_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: "always",
    retry: 1,
  });
  useEffect(() => {
    if (userId === null) {
      captureRevisionBaselineRef.current = null;
      return;
    }
    const revision = captureRevision.data?.revision;
    if (revision === undefined) return;
    const previous = captureRevisionBaselineRef.current;
    captureRevisionBaselineRef.current = { userId, revision };
    if (!previous || previous.userId !== userId || revision <= previous.revision) return;
    void queryClient.invalidateQueries({ queryKey: ["library"] });
    void queryClient.invalidateQueries({ queryKey: ["library-web-sources"] });
  }, [captureRevision.data?.revision, queryClient, userId]);
  const { activeProjectId, setActiveProjectId } = useActiveProject();
  const [query, setQuery] = useState("");
  const [libraryView, setLibraryView] = useState<"papers" | "web" | "shared">("papers");
  const detailTarget = useMemo(
    () => parseLibraryDetailTarget(searchParams.get("entry")),
    [searchParams],
  );
  const [metadataEditorTarget, setMetadataEditorTarget] = useState<LibraryDetailTarget | null>(null);
  const {
    data: documentPages,
    isLoading,
    hasNextPage: hasMoreDocuments,
    fetchNextPage: fetchMoreDocuments,
    isFetchingNextPage: loadingMoreDocuments,
  } = useLibrary(libraryView === "papers" ? query.trim() : "");
  const {
    data: webSourcePages,
    isLoading: webSourcesLoading,
    hasNextPage: hasMoreWebSources,
    fetchNextPage: fetchMoreWebSources,
    isFetchingNextPage: loadingMoreWebSources,
  } = useLibraryWebSources(
    libraryView === "web" ? query.trim() : "",
  );
  const docs = useMemo(
    () => documentPages?.pages.flatMap((page) => page) ?? [],
    [documentPages],
  );
  const webSources = useMemo(
    () => webSourcePages?.pages.flatMap((page) => page) ?? [],
    [webSourcePages],
  );
  const detailDocumentId = detailTarget?.kind === "paper" ? detailTarget.id : null;
  const detailWebSourceId = detailTarget?.kind === "web" ? detailTarget.id : null;
  const cachedDetailDocument = detailDocumentId !== null
    ? docs.find((document) => document.id === detailDocumentId)
    : undefined;
  const cachedDetailWebSource = detailWebSourceId !== null
    ? webSources.find((source) => source.id === detailWebSourceId)
    : undefined;
  const detailDocumentQuery = useQuery({
    queryKey: ["library", "document", detailDocumentId],
    queryFn: () => detailDocumentId === null ? Promise.resolve(null) : api.libraryDocument(detailDocumentId),
    enabled: detailDocumentId !== null,
    placeholderData: cachedDetailDocument,
  });
  const detailWebSourceQuery = useQuery({
    queryKey: ["library-web-sources", "entry", detailWebSourceId],
    queryFn: () => detailWebSourceId === null ? Promise.resolve(null) : api.libraryWebSource(detailWebSourceId),
    enabled: detailWebSourceId !== null,
    placeholderData: cachedDetailWebSource,
  });
  const detailDocument = detailTarget?.kind === "paper"
    ? detailDocumentQuery.data ?? undefined
    : undefined;
  const detailWebSource = detailTarget?.kind === "web"
    ? detailWebSourceQuery.data ?? undefined
    : undefined;
  const detailDocumentSourceUrl = safeLibrarySourceUrl(detailDocument?.url);
  const detailWebSourceUrl = safeLibrarySourceUrl(detailWebSource?.url);
  const editorDocument = metadataEditorTarget?.kind === "paper"
    ? (detailDocument?.id === metadataEditorTarget.id
        ? detailDocument
        : docs.find((document) => document.id === metadataEditorTarget.id))
    : undefined;
  const editorWebSource = metadataEditorTarget?.kind === "web"
    ? (detailWebSource?.id === metadataEditorTarget.id
        ? detailWebSource
        : webSources.find((source) => source.id === metadataEditorTarget.id))
    : undefined;
  const { data: projects } = useProjects();
  const { data: writerDocuments } = useQuery({
    queryKey: ["writer-docs"],
    queryFn: api.writerList,
  });
  const editableManuscripts = useMemo(
    () =>
      (writerDocuments ?? []).filter(
        (document) =>
          document.access_role === "owner" || document.access_role === "editor",
      ),
    [writerDocuments],
  );
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [selectionMode, setSelectionMode] = useState(false);
  const [bulkDeleteOpen, setBulkDeleteOpen] = useState(false);
  const [manuscriptDialogOpen, setManuscriptDialogOpen] = useState(false);
  const [targetManuscript, setTargetManuscript] = useState<string | null>(null);
  const openedDocumentRef = useRef<number | null>(null);
  const [location, setLocation] = useState<"all" | "unfiled" | number>("all");
  const [typeFilter, setTypeFilter] = useState<LibraryTypeFilter>("all");
  const [originFilter, setOriginFilter] = useState<LibraryOriginFilter>("all");
  const [sort, setSort] = useState<LibrarySort>("added-desc");
  const [paper, setPaper] = useState<PaperPanelState | null>(null);
  const [compactSecondarySurface, setCompactSecondarySurface] = useState(false);
  const libraryLayoutRef = useRef<HTMLDivElement>(null);
  const readerSurfaceRef = useRef<HTMLElement>(null);
  const readerRestoreFocusRef = useRef<HTMLElement | null>(null);
  const handoffQueryLoadedRef = useRef(false);
  const resolvedMetadataEditorTargetRef = useRef<string | null>(null);

  const setDetailTarget = (target: LibraryDetailTarget | null) => {
    const params = new URLSearchParams(searchParams.toString());
    if (target) params.set("entry", `${target.kind}:${target.id}`);
    else params.delete("entry");
    const nextQuery = params.toString();
    const nextUrl = nextQuery ? `${pathname}?${nextQuery}` : pathname;
    if (target && !detailTarget) router.push(nextUrl, { scroll: false });
    else router.replace(nextUrl, { scroll: false });
  };

  const openDetail = (target: LibraryDetailTarget) => {
    setPaper(null);
    setMetadataEditorTarget(null);
    setSelectionMode(false);
    setSelectedIds(new Set());
    setDetailTarget(target);
  };

  const closeDetail = () => {
    setPaper(null);
    setMetadataEditorTarget(null);
    setDetailTarget(null);
  };

  const openReader = (document: LibraryDocument) => {
    if (!isStoredLibraryPdf(document)) return;
    setMetadataEditorTarget(null);
    setSelectionMode(false);
    setSelectedIds(new Set());
    if (document.project_id) setActiveProjectId(document.project_id);
    setDetailTarget({ kind: "paper", id: document.id });
    setPaper({
      documentId: document.id,
      title: document.title ?? document.work_id,
      highlights: [],
      legalBasis: document.legal_basis,
      license: document.license,
    });
  };

  useEffect(() => {
    if (!detailTarget) return;
    setLibraryView(detailTarget.kind === "paper" ? "papers" : "web");
    setSelectionMode(false);
    setSelectedIds(new Set());
  }, [detailTarget]);

  const activePaper = paper
    && detailTarget?.kind === "paper"
    && detailTarget.id === paper.documentId
    ? paper
    : null;

  useEffect(() => {
    const layout = libraryLayoutRef.current;
    if (!layout) return;
    // Account for the app sidebar: both list and reader need usable actual widths.
    const sync = () => setCompactSecondarySurface(layout.getBoundingClientRect().width < 1100);
    sync();
    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", sync);
      return () => window.removeEventListener("resize", sync);
    }
    const observer = new ResizeObserver(sync);
    observer.observe(layout);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!activePaper) return;
    const readerSurface = readerSurfaceRef.current;
    if (!readerSurface) return;
    readerRestoreFocusRef.current = globalThis.document.activeElement instanceof HTMLElement
      ? globalThis.document.activeElement
      : null;
    const masterPanel = globalThis.document.querySelector<HTMLElement>("[data-library-master-panel]");
    const previousMasterInert = masterPanel?.inert ?? false;
    const previousMasterAriaHidden = masterPanel?.getAttribute("aria-hidden") ?? null;
    if (masterPanel && compactSecondarySurface) {
      masterPanel.inert = true;
      masterPanel.setAttribute("aria-hidden", "true");
    }
    const managedHiddenRegions = new Set<HTMLElement>();
    const syncHiddenReaderRegions = () => {
      for (const region of managedHiddenRegions) {
        if (region.getAttribute("aria-hidden") !== "true") {
          region.inert = false;
          managedHiddenRegions.delete(region);
        }
      }
      for (const region of readerSurface.querySelectorAll<HTMLElement>('[aria-hidden="true"]')) {
        if (!region.querySelector("a[href], button, input, select, textarea, [tabindex]")) continue;
        region.inert = true;
        managedHiddenRegions.add(region);
      }
    };
    syncHiddenReaderRegions();
    const hiddenRegionObserver = new MutationObserver(syncHiddenReaderRegions);
    hiddenRegionObserver.observe(readerSurface, {
      attributes: true,
      attributeFilter: ["aria-hidden"],
      subtree: true,
    });
    const animationFrame = compactSecondarySurface
      ? window.requestAnimationFrame(() => readerSurface.focus())
      : null;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented) return;
      const eventTarget = event.target instanceof Element ? event.target : null;
      const nestedDialog = eventTarget?.closest('[role="dialog"]');
      if (nestedDialog && nestedDialog !== readerSurface) return;
      if (eventTarget?.closest('[role="alertdialog"], [data-radix-popper-content-wrapper]')) return;
      if (event.key === "Escape") {
        event.preventDefault();
        setPaper(null);
        return;
      }
      if (event.key !== "Tab" || !compactSecondarySurface) return;
      const focusable = Array.from(
        readerSurface.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      ).filter((element) => (
        !element.hasAttribute("hidden")
        && !element.closest('[inert], [aria-hidden="true"]')
      ));
      if (focusable.length === 0) {
        event.preventDefault();
        readerSurface.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && (globalThis.document.activeElement === first || globalThis.document.activeElement === readerSurface)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && globalThis.document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      if (animationFrame !== null) window.cancelAnimationFrame(animationFrame);
      window.removeEventListener("keydown", handleKeyDown);
      hiddenRegionObserver.disconnect();
      for (const region of managedHiddenRegions) region.inert = false;
      if (masterPanel) {
        masterPanel.inert = previousMasterInert;
        if (previousMasterAriaHidden === null) masterPanel.removeAttribute("aria-hidden");
        else masterPanel.setAttribute("aria-hidden", previousMasterAriaHidden);
      }
      const restoreTarget = readerRestoreFocusRef.current?.isConnected
        ? readerRestoreFocusRef.current
        : globalThis.document.querySelector<HTMLElement>('[data-library-master-panel] [aria-current="true"]');
      restoreTarget?.focus();
    };
  }, [activePaper, compactSecondarySurface]);

  useEffect(() => {
    if (!metadataEditorTarget) {
      resolvedMetadataEditorTargetRef.current = null;
      return;
    }
    const targetKey = `${metadataEditorTarget.kind}:${metadataEditorTarget.id}`;
    const resolved = metadataEditorTarget.kind === "paper" ? editorDocument : editorWebSource;
    if (resolved) {
      resolvedMetadataEditorTargetRef.current = targetKey;
    } else if (resolvedMetadataEditorTargetRef.current === targetKey) {
      resolvedMetadataEditorTargetRef.current = null;
      setMetadataEditorTarget(null);
    }
  }, [
    editorDocument,
    editorWebSource,
    metadataEditorTarget,
  ]);
  useEffect(() => {
    if (!metadataEditorTarget) return;
    if (
      !detailTarget
      || metadataEditorTarget.kind !== detailTarget.kind
      || metadataEditorTarget.id !== detailTarget.id
    ) {
      setMetadataEditorTarget(null);
    }
  }, [detailTarget, metadataEditorTarget]);
  useEffect(() => {
    const available = new Set((docs ?? []).map((document) => document.id));
    setSelectedIds((current) => {
      const next = new Set(
        Array.from(current).filter((documentId) => available.has(documentId)),
      );
      return next.size === current.size ? current : next;
    });
  }, [docs]);
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (!handoffQueryLoadedRef.current) {
      const handoffQuery = params.get("query")?.trim();
      if (handoffQuery) setQuery(handoffQuery);
      handoffQueryLoadedRef.current = true;
    }
    const requestedParam = params.get("document");
    const requested = Number(requestedParam);
    if (
      !requestedParam
      || !Number.isFinite(requested)
      || !docs
      || openedDocumentRef.current === requested
    ) {
      return;
    }
    const document = docs.find((item) => item.id === requested);
    if (!document) return;
    if (!isStoredLibraryPdf(document)) return;
    openedDocumentRef.current = requested;
    openReader(document);
  }, [docs, setActiveProjectId]);

  // "Discuss this passage" has no chat on this page, so it opens one: an
  // ask conversation with the PDF attached and the marked passage quoted
  const discuss = useMutation({
    mutationFn: (selection: {
      document_id: number;
      page: number;
      quote: string;
      title: string;
    }) =>
      api.createRun(
        (docs ?? []).find((doc) => doc.id === selection.document_id)?.project_id
          ?? activeProjectId
          ?? null,
        {
          question:
            `Discuss this passage from "${selection.title}" ` +
            `(page ${selection.page}): "${selection.quote.slice(0, 400)}"`,
          mode: "ask",
          document_ids: [selection.document_id],
        },
      ),
    onSuccess: (created) => {
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
      router.push(`/r/${created.public_id ?? created.id}`);
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "That didn't work."),
  });

  // Instant client-side filtering keeps selection and the open reader stable.
  // The server-side q parameter remains available when this collection grows.
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const visible = (docs ?? []).filter((doc) => {
      const matchesLocation =
        location === "all"
          ? true
          : location === "unfiled"
            ? doc.project_id === null
            : doc.project_id === location;
      const matchesType =
        typeFilter === "all" || libraryDocumentType(doc) === typeFilter;
      const matchesOrigin =
        originFilter === "all"
        || (originFilter === "search" ? doc.run !== null : doc.run === null);
      const matchesQuery =
        !needle
        || (doc.title ?? "").toLowerCase().includes(needle)
        || doc.work_id.toLowerCase().includes(needle)
        || (doc.run?.label ?? "").toLowerCase().includes(needle)
        || (doc.project_name ?? "").toLowerCase().includes(needle)
        || (doc.folder ?? "").toLowerCase().includes(needle);
      return matchesLocation && matchesType && matchesOrigin && matchesQuery;
    });
    return visible.toSorted((left, right) => {
      switch (sort) {
        case "added-asc":
          return Date.parse(left.created_at) - Date.parse(right.created_at);
        case "title-asc":
          return (left.title ?? left.work_id).localeCompare(
            right.title ?? right.work_id,
          );
        case "title-desc":
          return (right.title ?? right.work_id).localeCompare(
            left.title ?? left.work_id,
          );
        case "year-desc":
          return compareNullableYears(left, right, "desc");
        case "year-asc":
          return compareNullableYears(left, right, "asc");
        case "size-desc":
          return right.byte_size - left.byte_size;
        case "added-desc":
        default:
          return Date.parse(right.created_at) - Date.parse(left.created_at);
      }
    });
  }, [docs, query, location, originFilter, sort, typeFilter]);
  const filteredWebSources = webSources ?? [];
  const hasCollectionFilters =
    typeFilter !== "all" || originFilter !== "all" || location !== "all";
  const activeFilterCount =
    Number(typeFilter !== "all")
    + Number(originFilter !== "all")
    + Number(location !== "all");
  const activeLocationLabel =
    location === "unfiled"
      ? "Unfiled"
      : typeof location === "number"
        ? (projects ?? []).find((project) => project.id === location)?.name
          ?? "Project"
        : null;
  const selectedDocuments = useMemo(
    () => (docs ?? []).filter((document) => selectedIds.has(document.id)),
    [docs, selectedIds],
  );
  const allVisibleSelected =
    filtered.length > 0
    && filtered.every((document) => selectedIds.has(document.id));
  const toggleDocument = (documentId: number) => {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(documentId)) next.delete(documentId);
      else next.add(documentId);
      return next;
    });
  };
  const toggleAllVisible = () => {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (allVisibleSelected) {
        filtered.forEach((document) => next.delete(document.id));
      } else {
        filtered.forEach((document) => next.add(document.id));
      }
      return next;
    });
  };

  const bulkDelete = useMutation({
    mutationFn: () =>
      api.bulkDeleteLibraryDocuments(Array.from(selectedIds)),
    onSuccess: (result) => {
      if (paper && selectedIds.has(paper.documentId)) setPaper(null);
      setSelectedIds(new Set());
      setSelectionMode(false);
      setBulkDeleteOpen(false);
      toast.success(
        result.deleted === 1
          ? "Paper deleted from the library."
          : `${result.deleted} papers deleted from the library.`,
      );
      void queryClient.invalidateQueries({ queryKey: ["library"] });
    },
    onError: (error) =>
      toast.error(
        error instanceof Error ? error.message : "Could not delete the selected papers.",
      ),
  });

  const assignToManuscript = useMutation({
    mutationFn: () => {
      if (!targetManuscript) throw new Error("Choose a manuscript.");
      return api.writerLibrarySourcesAdd(
        targetManuscript,
        Array.from(selectedIds),
      );
    },
    onSuccess: (result) => {
      const manuscript = editableManuscripts.find(
        (document) => document.public_id === targetManuscript,
      );
      setManuscriptDialogOpen(false);
      setSelectedIds(new Set());
      setSelectionMode(false);
      toast.success(
        result.created > 0
          ? `${result.created} ${result.created === 1 ? "paper is" : "papers are"} ready to cite in ${manuscript?.title ?? "the manuscript"}.`
          : "Those papers are already linked to this manuscript.",
      );
      void queryClient.invalidateQueries({
        queryKey: ["writer-sources", targetManuscript],
      });
      void queryClient.invalidateQueries({
        queryKey: ["writer-citations", targetManuscript],
      });
    },
    onError: (error) =>
      toast.error(
        error instanceof Error ? error.message : "Could not link the selected papers.",
      ),
  });

  // drop PDFs anywhere on the page to add them to the library
  const [dragging, setDragging] = useState(false);
  const dragDepth = useRef(0);
  const uploadInputRef = useRef<HTMLInputElement>(null);
  const uploadPdfs = useMutation({
    mutationFn: async (files: File[]) => {
      const uploaded = [];
      for (const file of files) {
        uploaded.push(
          await api.uploadDocument({
            filename: file.name,
            content_base64: await fileToBase64(file),
            ...(activeProjectId ? { project_id: activeProjectId } : {}),
          }),
        );
      }
      return uploaded;
    },
    onSuccess: (uploaded) => {
      toast.success(
        uploaded.length === 1
          ? "Paper added to your library."
          : `${uploaded.length} papers added to your library.`,
      );
      void queryClient.invalidateQueries({ queryKey: ["library"] });
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "Upload failed.");
      void queryClient.invalidateQueries({ queryKey: ["library"] });
    },
  });
  const queueUploads = (incoming: File[]) => {
    const files = incoming.filter((file) =>
      /\.(pdf|png|jpe?g|webp|gif)$/i.test(file.name),
    );
    if (files.length === 0) {
      toast.error("Choose PDF files or photos of sources.");
      return;
    }
    uploadPdfs.mutate(files);
  };
  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    dragDepth.current = 0;
    setDragging(false);
    if (libraryView === "shared") return;
    queueUploads(Array.from(event.dataTransfer.files));
  };

  return (
    <div
      ref={libraryLayoutRef}
      className="relative flex min-h-0 min-w-0 flex-1 overflow-hidden rounded-2xl"
      onDragEnter={(event) => {
        event.preventDefault();
        if (libraryView === "shared") return;
        dragDepth.current += 1;
        setDragging(true);
      }}
      onDragLeave={() => {
        dragDepth.current = Math.max(0, dragDepth.current - 1);
        if (dragDepth.current === 0) setDragging(false);
      }}
      onDragOver={(event) => event.preventDefault()}
      onDrop={handleDrop}
    >
      {libraryView !== "shared" ? <BrowserCaptureConnect /> : null}
      {libraryView !== "shared" && dragging ? (
        <div className="pointer-events-none absolute inset-0 z-30 grid place-items-center border-2 border-dashed border-moss/60 bg-accent/80">
          <div className="text-center">
            <FileUp className="mx-auto size-7 text-moss" />
            <p className="mt-2 font-mono text-[0.75rem] uppercase tracking-[0.18em] text-moss">
              Drop to add to your library
            </p>
            <p className="mt-1 text-[0.78125rem] text-muted-foreground">
              PDFs are stored, parsed and ready to read or discuss.
            </p>
          </div>
        </div>
      ) : null}
      {libraryView !== "shared" && uploadPdfs.isPending ? (
        <div className="absolute right-6 top-6 z-20 flex items-center gap-2 rounded-full border border-border bg-card px-3 py-1.5 shadow-sm">
          <Loader2 className="size-3.5 animate-spin text-moss" />
          <span className="text-[0.75rem] text-muted-foreground">
            Uploading…
          </span>
        </div>
      ) : null}
      <div className="@container/library min-h-0 min-w-0 flex-1 overflow-y-auto" data-library-master-panel>
        <div className="w-full px-4 pb-16 pt-6 @min-[40rem]/library:px-6 @min-[40rem]/library:pt-10 @min-[60rem]/library:px-10 @min-[80rem]/library:px-14">
          <div data-tour="library-page" className="flex max-w-5xl flex-col items-start justify-between gap-4 @min-[52rem]/library:flex-row @min-[52rem]/library:items-end">
            <div className="min-w-0 max-w-xl">
            <p className="flex items-center gap-2 font-mono text-[0.625rem] uppercase tracking-[0.22em] text-moss">
              <LibraryIcon className="size-3.5 text-moss" /> Library
            </p>
            <h1 className="mt-2 font-display text-[2.4rem] font-normal leading-none text-foreground">
              {libraryView === "shared"
                ? isGerman ? "Mit mir geteilt" : "Shared with me"
                : isGerman ? "Deine Quellen" : "Your sources"}
            </h1>
            <p className="mt-1 text-[0.875rem] text-muted-foreground">
              {libraryView === "shared"
                ? isGerman
                  ? "Accountgebundener Lesezugriff auf Libraries und Projekt-Collections, die andere Personen mit dir geteilt haben."
                  : "Account-bound, read-only access to Libraries and project collections other people shared with you."
                : isGerman
                  ? "Paper und bewusst gespeicherte Webquellen samt Herkunft. Browser Capture speichert die aktive Quelle mit einem Klick."
                  : "Papers and explicitly saved web sources, together with their provenance. Nothing is captured passively; clicking Browser Capture saves the active source in one step."}
            </p>
            </div>
            {libraryView !== "shared" ? (
              <div className="flex flex-wrap items-center gap-2">
                {me?.role === "owner" ? (
                  <LibraryShareDialog
                    projects={projects ?? []}
                    defaultProjectId={activeProjectId}
                    isGerman={isGerman}
                  />
                ) : null}
                <BrowserCaptureDevices />
              </div>
            ) : null}
          </div>

          <div className="mt-6 max-w-5xl">
            <div className="mb-3 flex max-w-full w-fit overflow-x-auto rounded-full border border-border bg-muted/35 p-1" role="tablist" aria-label="Library source type">
              <button
                type="button"
                id="library-tab-papers"
                role="tab"
                aria-controls="library-panel-papers"
                aria-selected={libraryView === "papers"}
                onClick={() => {
                  if (libraryView !== "papers") closeDetail();
                  setLibraryView("papers");
                }}
                className={`shrink-0 rounded-full px-4 py-1.5 text-[0.78125rem] transition-colors ${libraryView === "papers" ? "bg-background font-medium text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
              >
                Papers <span className="ml-1 font-mono text-[0.625rem]">{docs.length}{hasMoreDocuments ? "+" : ""}</span>
              </button>
              <button
                type="button"
                id="library-tab-web"
                role="tab"
                aria-controls="library-panel-web"
                aria-selected={libraryView === "web"}
                onClick={() => {
                  if (libraryView !== "web") closeDetail();
                  setLibraryView("web");
                  setSelectionMode(false);
                  setSelectedIds(new Set());
                }}
                className={`shrink-0 rounded-full px-4 py-1.5 text-[0.78125rem] transition-colors ${libraryView === "web" ? "bg-background font-medium text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
              >
                Web sources <span className="ml-1 font-mono text-[0.625rem]">{webSources.length}{hasMoreWebSources ? "+" : ""}</span>
              </button>
              <button
                type="button"
                id="library-tab-shared"
                role="tab"
                aria-controls="library-panel-shared"
                aria-selected={libraryView === "shared"}
                onClick={() => {
                  closeDetail();
                  setPaper(null);
                  setSelectionMode(false);
                  setSelectedIds(new Set());
                  setDragging(false);
                  setLibraryView("shared");
                }}
                className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-4 py-1.5 text-[0.78125rem] transition-colors ${libraryView === "shared" ? "bg-background font-medium text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
              >
                <Users className="size-3.5" aria-hidden="true" />
                {isGerman ? "Mit mir geteilt" : "Shared with me"}
              </button>
            </div>
            {libraryView !== "shared" ? (
            <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2">
              <div className="relative min-w-0">
                <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder={libraryView === "papers" ? "Search papers…" : "Search web sources…"}
                  aria-label={`Search Library ${libraryView}`}
                  className="h-9 rounded-full pl-9 text-[0.8125rem]"
                />
              </div>
              <input
                ref={uploadInputRef}
                type="file"
                accept=".pdf,.png,.jpg,.jpeg,.webp,.gif,application/pdf,image/*"
                multiple
                className="sr-only"
                aria-label="Add papers to the library"
                onChange={(event) => {
                  queueUploads(Array.from(event.target.files ?? []));
                  event.target.value = "";
                }}
              />
              {libraryView === "papers" ? <Button
                type="button"
                className="h-9 shrink-0 rounded-full"
                disabled={uploadPdfs.isPending}
                onClick={() => uploadInputRef.current?.click()}
              >
                {uploadPdfs.isPending ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <FileUp className="size-3.5" />
                )}
                Add papers
              </Button> : null}
            </div>
            ) : null}

            {libraryView === "papers" && selectionMode ? (
              <div
                className="mt-2 flex min-h-10 flex-wrap items-center gap-2 border-y border-border/60 py-1.5"
                aria-label="Paper selection actions"
              >
                <div className="flex items-center gap-2 px-1 text-[0.75rem] font-medium text-foreground">
                  <Checkbox
                    checked={allVisibleSelected}
                    onCheckedChange={toggleAllVisible}
                    aria-label={
                      allVisibleSelected
                        ? "Clear all visible papers"
                        : "Select all visible papers"
                    }
                  />
                  <button type="button" onClick={toggleAllVisible}>
                    {allVisibleSelected ? "Clear visible" : "Select visible"}
                  </button>
                </div>
                {selectedIds.size > 0 ? (
                  <span className="rounded-full bg-moss px-2.5 py-1 font-mono text-[0.625rem] uppercase tracking-[0.12em] text-white">
                    {selectedIds.size} selected
                  </span>
                ) : (
                  <span className="text-[0.71875rem] text-muted-foreground">
                    Choose papers for a bulk action.
                  </span>
                )}
                <div className="ml-auto flex flex-wrap items-center justify-end gap-1">
                  {selectedIds.size > 0 ? (
                    <>
                      <LibraryCitationExportMenu
                        documentIds={Array.from(selectedIds)}
                        isGerman={isGerman}
                      />
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-9 rounded-full px-2.5 sm:h-8"
                        onClick={() => {
                          setTargetManuscript(
                            editableManuscripts[0]?.public_id ?? null,
                          );
                          setManuscriptDialogOpen(true);
                        }}
                      >
                        <FileText className="size-3.5" />
                        Add to manuscript
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-9 rounded-full px-2.5 text-destructive hover:bg-destructive/10 hover:text-destructive sm:h-8"
                        onClick={() => setBulkDeleteOpen(true)}
                      >
                        <Trash2 className="size-3.5" />
                        Delete
                      </Button>
                    </>
                  ) : null}
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="h-9 rounded-full px-2.5 sm:h-8"
                    aria-pressed={selectionMode}
                    onClick={() => {
                      closeDetail();
                      setSelectedIds(new Set());
                      setSelectionMode(false);
                    }}
                  >
                    <Check className="size-3.5" />
                    Done
                  </Button>
                </div>
              </div>
            ) : libraryView === "papers" ? (
              <div className="mt-2 flex min-h-10 flex-wrap items-center gap-1 border-y border-border/60 py-1.5">
                <span
                  className="w-full whitespace-nowrap px-1 text-[0.71875rem] text-muted-foreground sm:mr-auto sm:w-auto"
                  aria-live="polite"
                >
                  {hasCollectionFilters || query.trim()
                    ? `${filtered.length} of ${(docs ?? []).length} papers`
                    : `${(docs ?? []).length} ${(docs ?? []).length === 1 ? "paper" : "papers"}`}
                </span>
                <Popover>
                  <PopoverTrigger asChild>
                    <Button
                      type="button"
                      variant={activeFilterCount > 0 ? "secondary" : "ghost"}
                      size="sm"
                      className="h-9 shrink-0 rounded-full px-2.5 sm:h-8"
                      aria-label={
                        activeFilterCount > 0
                          ? `Filters, ${activeFilterCount} active`
                          : "Filter library papers"
                      }
                    >
                      <SlidersHorizontal className="size-3.5" />
                      Filter
                      {activeFilterCount > 0 ? (
                        <span className="grid size-4 place-items-center rounded-full bg-moss font-mono text-[0.5625rem] leading-none text-white">
                          {activeFilterCount}
                        </span>
                      ) : null}
                    </Button>
                  </PopoverTrigger>
                  <PopoverContent
                    align="end"
                    className="w-[min(18rem,calc(100vw-2rem))] gap-3 p-3"
                  >
                    <div className="flex items-center justify-between gap-3">
                      <div>
                        <p className="text-[0.8125rem] font-medium">
                          Filter papers
                        </p>
                        <p className="text-[0.6875rem] text-muted-foreground">
                          Narrow this library view.
                        </p>
                      </div>
                      {hasCollectionFilters ? (
                        <Button
                          type="button"
                          variant="ghost"
                          size="xs"
                          className="rounded-full text-muted-foreground"
                          onClick={() => {
                            setTypeFilter("all");
                            setOriginFilter("all");
                            setLocation("all");
                          }}
                        >
                          Clear filters
                        </Button>
                      ) : null}
                    </div>
                    <div className="grid gap-3">
                      <div className="grid gap-1.5">
                        <p className="text-[0.6875rem] font-medium text-muted-foreground">
                          File type
                        </p>
                        <Select
                          value={typeFilter}
                          onValueChange={(value) =>
                            setTypeFilter(value as LibraryTypeFilter)
                          }
                        >
                          <SelectTrigger
                            size="sm"
                            aria-label="Filter library by file type"
                            className="h-8 w-full justify-between rounded-lg bg-muted/45 px-2.5 text-foreground shadow-none"
                          >
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent align="start">
                            <SelectItem value="all">All file types</SelectItem>
                            <SelectItem value="pdf">PDFs</SelectItem>
                            <SelectItem value="image">Source images</SelectItem>
                            <SelectItem value="other">Other files</SelectItem>
                          </SelectContent>
                        </Select>
                      </div>
                      <div className="grid gap-1.5">
                        <p className="text-[0.6875rem] font-medium text-muted-foreground">
                          Origin
                        </p>
                        <Select
                          value={originFilter}
                          onValueChange={(value) =>
                            setOriginFilter(value as LibraryOriginFilter)
                          }
                        >
                          <SelectTrigger
                            size="sm"
                            aria-label="Filter library by origin"
                            className="h-8 w-full justify-between rounded-lg bg-muted/45 px-2.5 text-foreground shadow-none"
                          >
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent align="start">
                            <SelectItem value="all">All origins</SelectItem>
                            <SelectItem value="uploaded">
                              Uploaded by you
                            </SelectItem>
                            <SelectItem value="search">
                              Collected by searches
                            </SelectItem>
                          </SelectContent>
                        </Select>
                      </div>
                      <div className="grid gap-1.5">
                        <p className="text-[0.6875rem] font-medium text-muted-foreground">
                          Location
                        </p>
                        <Select
                          value={String(location)}
                          onValueChange={(value) =>
                            setLocation(
                              value === "all" || value === "unfiled"
                                ? value
                                : Number(value),
                            )
                          }
                        >
                          <SelectTrigger
                            size="sm"
                            aria-label="Filter library by location"
                            className="h-8 w-full justify-between rounded-lg bg-muted/45 px-2.5 text-foreground shadow-none"
                          >
                            <FolderOpen className="size-3.5 text-moss" />
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent align="start">
                            <SelectItem value="all">All papers</SelectItem>
                            <SelectItem value="unfiled">Unfiled</SelectItem>
                            {(projects ?? []).map((project) => (
                              <SelectItem
                                key={project.id}
                                value={String(project.id)}
                              >
                                {project.name}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </div>
                    </div>
                  </PopoverContent>
                </Popover>
                <Select
                  value={sort}
                  onValueChange={(value) => setSort(value as LibrarySort)}
                >
                  <SelectTrigger
                    size="sm"
                    aria-label="Sort library papers"
                    className="h-9 w-[10.25rem] rounded-full border-transparent px-2.5 shadow-none sm:h-8"
                  >
                    <ArrowUpDown className="size-3.5 text-moss" />
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent align="end">
                    <SelectItem value="added-desc">Recently added</SelectItem>
                    <SelectItem value="added-asc">Oldest added</SelectItem>
                    <SelectItem value="title-asc">Title A to Z</SelectItem>
                    <SelectItem value="title-desc">Title Z to A</SelectItem>
                    <SelectItem value="year-desc">
                      Newest publication
                    </SelectItem>
                    <SelectItem value="year-asc">
                      Oldest publication
                    </SelectItem>
                    <SelectItem value="size-desc">Largest files</SelectItem>
                  </SelectContent>
                </Select>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="h-9 shrink-0 rounded-full px-2.5 sm:h-8"
                  aria-pressed={selectionMode}
                  onClick={() => {
                    closeDetail();
                    setSelectionMode(true);
                  }}
                >
                  <CheckSquare2 className="size-3.5" />
                  Select papers
                </Button>
              </div>
            ) : libraryView === "web" ? (
              <div className="mt-2 flex min-h-10 items-center border-y border-border/60 px-1 py-1.5 text-[0.71875rem] text-muted-foreground">
                {query.trim()
                  ? `${filteredWebSources.length} of ${(webSources ?? []).length} web sources`
                  : `${(webSources ?? []).length} ${(webSources ?? []).length === 1 ? "web source" : "web sources"}`}
                <span className="ml-auto">Saved in one click with Browser Capture</span>
              </div>
            ) : null}

            {libraryView === "papers" && hasCollectionFilters && !selectionMode ? (
              <div className="mt-2 flex min-h-7 flex-wrap items-center gap-1.5 px-1">
                {typeFilter !== "all" ? (
                  <button
                    type="button"
                    onClick={() => setTypeFilter("all")}
                    className="inline-flex h-7 items-center gap-1.5 rounded-full bg-accent/70 px-2.5 text-[0.71875rem] text-moss transition-colors hover:bg-accent"
                    aria-label="Remove file type filter"
                  >
                    {typeFilter === "pdf"
                      ? "PDFs"
                      : typeFilter === "image"
                        ? "Source images"
                        : "Other files"}
                    <X className="size-3" />
                  </button>
                ) : null}
                {originFilter !== "all" ? (
                  <button
                    type="button"
                    onClick={() => setOriginFilter("all")}
                    className="inline-flex h-7 items-center gap-1.5 rounded-full bg-accent/70 px-2.5 text-[0.71875rem] text-moss transition-colors hover:bg-accent"
                    aria-label="Remove origin filter"
                  >
                    {originFilter === "uploaded"
                      ? "Uploaded by you"
                      : "Collected by searches"}
                    <X className="size-3" />
                  </button>
                ) : null}
                {activeLocationLabel ? (
                  <button
                    type="button"
                    onClick={() => setLocation("all")}
                    className="inline-flex h-7 max-w-56 items-center gap-1.5 rounded-full bg-accent/70 px-2.5 text-[0.71875rem] text-moss transition-colors hover:bg-accent"
                    aria-label="Remove location filter"
                  >
                    <FolderOpen className="size-3" />
                    <span className="truncate">{activeLocationLabel}</span>
                    <X className="size-3" />
                  </button>
                ) : null}
              </div>
            ) : null}
          </div>

          <div
            id={`library-panel-${libraryView}`}
            role="tabpanel"
            aria-labelledby={`library-tab-${libraryView}`}
          >
          {libraryView === "shared" ? (
            <SharedWithMeView isGerman={isGerman} />
          ) : libraryView === "web" ? (
            webSourcesLoading ? (
              <Loader2 className="mx-auto my-16 size-5 animate-spin text-muted-foreground" />
            ) : filteredWebSources.length === 0 ? (
              <div className="mt-10 rounded-2xl border border-dashed border-border p-8 text-center">
                <Globe2 className="mx-auto size-5 text-moss" />
                <p className="mt-2 text-[0.875rem] text-muted-foreground">
                  {query ? "No web sources match that search." : "No web sources saved yet. Open a page and click Browser Capture once."}
                </p>
              </div>
            ) : (
              <div className="mt-6 space-y-2">
                {filteredWebSources.map((source) => (
                  <WebSourceCard
                    key={source.id}
                    source={source}
                    isGerman={isGerman}
                    active={detailTarget?.kind === "web" && detailTarget.id === source.id}
                    onOpenDetails={() => openDetail({ kind: "web", id: source.id })}
                  />
                ))}
                {hasMoreWebSources ? (
                  <div className="flex justify-center pt-3">
                    <Button
                      type="button"
                      variant="outline"
                      className="rounded-full"
                      disabled={loadingMoreWebSources}
                      onClick={() => void fetchMoreWebSources()}
                    >
                      {loadingMoreWebSources ? <Loader2 className="size-3.5 animate-spin" /> : null}
                      Load more sources
                    </Button>
                  </div>
                ) : null}
              </div>
            )
          ) : isLoading ? (
            <Loader2 className="mx-auto my-16 size-5 animate-spin text-muted-foreground" />
          ) : filtered.length === 0 ? (
            <div className="mt-10 rounded-2xl border border-dashed border-border p-8 text-center">
              <p className="text-[0.875rem] text-muted-foreground">
                {query
                  ? "No papers match that search and the active filters."
                  : hasCollectionFilters
                    ? "No papers match the active filters. Clear them to see the complete Library."
                  : "Nothing collected yet. Run a search with full texts, or attach a PDF to a chat. It lands here."}
              </p>
              {hasMoreDocuments ? (
                <Button
                  type="button"
                  variant="outline"
                  className="mt-4 rounded-full"
                  disabled={loadingMoreDocuments}
                  onClick={() => void fetchMoreDocuments()}
                >
                  {loadingMoreDocuments ? <Loader2 className="size-3.5 animate-spin" /> : null}
                  Search more papers
                </Button>
              ) : null}
            </div>
          ) : (
            <div className="mt-6 space-y-2">
              {filtered.map((doc) => {
                const isPdf = isStoredLibraryPdf(doc);
                const selected = selectedIds.has(doc.id);
                const detailActive = detailTarget?.kind === "paper" && detailTarget.id === doc.id;
                const sourceIdentity = librarySourceIdentity({
                  url: doc.url,
                  fallbackLabel: "Paper",
                });
                return (
                  <div
                    key={doc.id}
                    className={`flex min-w-0 flex-wrap items-center gap-3 rounded-2xl border px-3 py-3 transition-colors @min-[52rem]/library:flex-nowrap @min-[40rem]/library:px-4 ${
                      selected || detailActive
                        ? "border-moss/45 bg-accent/45"
                        : "border-border bg-card hover:border-moss/40"
                    }`}
                  >
                    {selectionMode ? (
                      <Checkbox
                        checked={selected}
                        onCheckedChange={() => toggleDocument(doc.id)}
                        aria-label={`Select ${doc.title ?? doc.work_id}`}
                        className="ml-0.5"
                      />
                    ) : null}
                    <button
                      type="button"
                      className="flex min-w-0 flex-1 items-center gap-3 rounded-xl text-left outline-none focus-visible:ring-2 focus-visible:ring-moss focus-visible:ring-offset-2"
                      onClick={() => {
                        if (selectionMode) toggleDocument(doc.id);
                        else openDetail({ kind: "paper", id: doc.id });
                      }}
                      aria-label={selectionMode
                        ? `${isGerman
                          ? selected ? "Auswahl aufheben" : "Auswählen"
                          : selected ? "Deselect" : "Select"} ${doc.title ?? doc.work_id}`
                        : `${isGerman ? "Alle Details anzeigen für" : "View all details for"} ${doc.title ?? doc.work_id}`}
                      aria-pressed={selectionMode ? selected : undefined}
                      aria-current={!selectionMode && detailActive ? "true" : undefined}
                    >
                      <SourceIdentityMark
                        kind="paper"
                        url={doc.url}
                        isGerman={isGerman}
                      />
                      <span className="min-w-0 flex-1">
                      <span className="block truncate text-[0.875rem] font-medium">
                        {doc.title ?? doc.work_id}
                      </span>
                      {doc.authors.length > 0 ? (
                        <span className="mt-0.5 block truncate text-[0.75rem] text-foreground/70">
                          {doc.authors.join(", ")}
                        </span>
                      ) : null}
                      <span className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[0.71875rem] text-muted-foreground [&>span]:max-w-full [&>span]:truncate">
                        {doc.year && <span>{doc.year}</span>}
                        {doc.metadata.container_title ? (
                          <span className="max-w-64 truncate">{doc.metadata.container_title}</span>
                        ) : null}
                        {doc.metadata.volume ? (
                          <span>
                            vol. {doc.metadata.volume}
                            {doc.metadata.issue ? `(${doc.metadata.issue})` : ""}
                          </span>
                        ) : null}
                        {doc.doi ? <span className="max-w-52 truncate">DOI {doc.doi}</span> : null}
                        {sourceIdentity.hostname ? <span>{sourceIdentity.hostname}</span> : null}
                        <span>{formatBytes(doc.byte_size)}</span>
                        <span>{formatDate(doc.created_at)}</span>
                        {doc.run && (
                          <span className="inline-flex max-w-[16rem] items-center gap-0.5 truncate text-moss">
                            from “{doc.run.label}”
                          </span>
                        )}
                        {doc.project_name && (
                          <span className="inline-flex items-center gap-1 text-moss">
                            <FolderOpen className="size-2.5" />
                            {doc.project_name}{doc.folder ? ` / ${doc.folder}` : ""}
                          </span>
                        )}
                      </span>
                      </span>
                    </button>
                    {!selectionMode ? (
                      <div
                        className="flex w-full shrink-0 flex-wrap items-center justify-end gap-1.5 @min-[52rem]/library:w-auto"
                        onClick={(event) => event.stopPropagation()}
                        role="group"
                        aria-label="Paper actions"
                      >
                        <PaperMetadataEditor document={doc} />
                        <FilePaperButton doc={doc} projects={projects ?? []} />
                        <DeletePaperButton
                          doc={doc}
                          onDeleted={() => {
                            if (paper?.documentId === doc.id) setPaper(null);
                          }}
                        />
                        {doc.has_file ? <AskButton doc={doc} /> : sourceIdentity.safeUrl ? (
                          <Button asChild size="sm" variant="outline" className="h-7 rounded-full text-[0.75rem]">
                            <a href={sourceIdentity.safeUrl} target="_blank" rel="noopener noreferrer">
                              <ExternalLink className="size-3" /> Open source
                            </a>
                          </Button>
                        ) : null}
                        {isPdf ? (
                          <>
                            <PaperPdfDownloadButton
                              documentId={doc.id}
                              title={doc.title ?? doc.metadata.title ?? doc.work_id}
                              isGerman={isGerman}
                              className="h-7"
                            />
                            <Button
                              size="sm"
                              className="h-7 rounded-full text-[0.75rem]"
                              onClick={() => openReader(doc)}
                            >
                              <BookOpen className="size-3" />
                              {isGerman ? "Lesen" : "Read"}
                            </Button>
                          </>
                        ) : null}
                      </div>
                    ) : null}
                  </div>
                );
              })}
              {hasMoreDocuments ? (
                <div className="flex justify-center pt-3">
                  <Button
                    type="button"
                    variant="outline"
                    className="rounded-full"
                    disabled={loadingMoreDocuments}
                    onClick={() => void fetchMoreDocuments()}
                  >
                    {loadingMoreDocuments ? <Loader2 className="size-3.5 animate-spin" /> : null}
                    Load more papers
                  </Button>
                </div>
              ) : null}
            </div>
          )}
          </div>
        </div>
      </div>

      {detailTarget && !activePaper ? (
        <LibraryEntryDetail
          open={!metadataEditorTarget}
          kind={detailTarget.kind}
          document={detailDocument}
          source={detailWebSource}
          loading={detailTarget.kind === "paper"
            ? detailDocumentQuery.isLoading
            : detailWebSourceQuery.isLoading}
          loadFailed={detailTarget.kind === "paper"
            ? detailDocumentQuery.isError
            : detailWebSourceQuery.isError}
          onRetry={() => {
            if (detailTarget.kind === "paper") void detailDocumentQuery.refetch();
            else void detailWebSourceQuery.refetch();
          }}
          onOpenChange={(open) => {
            if (!open) closeDetail();
          }}
          onEdit={() => {
            setPaper(null);
            setMetadataEditorTarget(detailTarget);
          }}
          actions={detailDocument ? (
            <>
              <FilePaperButton doc={detailDocument} projects={projects ?? []} />
              <DeletePaperButton
                doc={detailDocument}
                onDeleted={() => {
                  if (paper?.documentId === detailDocument.id) setPaper(null);
                  closeDetail();
                }}
              />
              {detailDocument.has_file ? <AskButton doc={detailDocument} /> : null}
              {detailDocumentSourceUrl ? (
                <Button asChild size="sm" variant="outline" className="h-8 rounded-full text-[0.75rem]">
                  <a href={detailDocumentSourceUrl} target="_blank" rel="noopener noreferrer">
                    <ExternalLink className="size-3" /> Open source
                  </a>
                </Button>
              ) : null}
              {isStoredLibraryPdf(detailDocument) ? (
                <>
                  <PaperPdfDownloadButton
                    documentId={detailDocument.id}
                    title={detailDocument.title ?? detailDocument.metadata.title ?? detailDocument.work_id}
                    isGerman={isGerman}
                    className="h-8"
                  />
                  <Button
                    size="sm"
                    className="h-8 rounded-full text-[0.75rem]"
                    onClick={() => {
                      openReader(detailDocument);
                    }}
                  >
                    <BookOpen className="size-3" /> {isGerman ? "Lesen" : "Read"}
                  </Button>
                </>
              ) : null}
            </>
          ) : detailWebSource ? (
            <>
              {detailWebSourceUrl ? (
                <Button asChild variant="outline" size="sm" className="h-8 rounded-full">
                  <a href={detailWebSourceUrl} target="_blank" rel="noopener noreferrer">
                    <ExternalLink className="size-3.5" /> {isGerman ? "Öffnen" : "Open"}
                  </a>
                </Button>
              ) : null}
              <DeleteWebSourceButton source={detailWebSource} onDeleted={closeDetail} />
            </>
          ) : null}
        />
      ) : null}

      {editorDocument ? (
        <PaperMetadataEditor
          document={editorDocument}
          open
          hideTrigger
          onOpenChange={(open) => {
            if (!open) setMetadataEditorTarget(null);
          }}
        />
      ) : null}
      {editorWebSource ? (
        <WebSourceMetadataEditor
          source={editorWebSource}
          open
          hideTrigger
          onOpenChange={(open) => {
            if (!open) setMetadataEditorTarget(null);
          }}
        />
      ) : null}

      {activePaper && (
        <aside
          ref={readerSurfaceRef}
          className={compactSecondarySurface
            ? "fixed inset-x-0 bottom-0 top-12 z-40 overflow-hidden border-l border-border bg-card outline-none"
            : "static z-auto block w-[48%] min-w-[35rem] max-w-[56rem] shrink-0 overflow-hidden rounded-r-2xl border-l border-border bg-card outline-none"}
          role={compactSecondarySurface ? "dialog" : "complementary"}
          aria-modal={compactSecondarySurface || undefined}
          aria-label={`${isGerman ? "PDF lesen" : "Read PDF"}: ${activePaper.title}`}
          tabIndex={compactSecondarySurface ? -1 : undefined}
          data-library-reader-panel
        >
          <PaperPanel
            paper={activePaper}
            onClose={() => setPaper(null)}
            onDiscuss={(selection) => discuss.mutate(selection)}
          />
        </aside>
      )}

      <Dialog
        open={manuscriptDialogOpen}
        onOpenChange={setManuscriptDialogOpen}
      >
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Add papers to a manuscript</DialogTitle>
            <DialogDescription>
              The selected papers become citable sources. Their extracted text
              is available to the manuscript agent, while the original Library
              files stay in place.
            </DialogDescription>
          </DialogHeader>
          {editableManuscripts.length > 0 ? (
            <div className="grid max-h-72 gap-2 overflow-y-auto py-1">
              {editableManuscripts.map((manuscript) => {
                const active = targetManuscript === manuscript.public_id;
                return (
                  <button
                    key={manuscript.public_id}
                    type="button"
                    onClick={() => setTargetManuscript(manuscript.public_id)}
                    className={`flex items-center gap-3 rounded-xl border px-3 py-3 text-left transition-colors ${
                      active
                        ? "border-moss/45 bg-accent"
                        : "border-border bg-card hover:border-moss/30"
                    }`}
                  >
                    <span
                      className={`grid size-8 shrink-0 place-items-center rounded-lg ${
                        active
                          ? "bg-moss text-white"
                          : "bg-secondary text-muted-foreground"
                      }`}
                    >
                      {active ? (
                        <Check className="size-3.5" />
                      ) : (
                        <FileText className="size-3.5" />
                      )}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[0.8125rem] font-medium">
                        {manuscript.title}
                      </span>
                      <span className="mt-0.5 block text-[0.6875rem] text-muted-foreground">
                        {manuscript.project_id
                          ? "Project manuscript"
                          : "Independent manuscript"}
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>
          ) : (
            <div className="rounded-xl border border-dashed border-border p-5 text-center">
              <FileText className="mx-auto size-5 text-moss" />
              <p className="mt-2 text-[0.8125rem] font-medium">
                No editable manuscript yet
              </p>
              <p className="mt-1 text-[0.75rem] text-muted-foreground">
                Create or import a manuscript first, then return to this
                selection.
              </p>
              <Button asChild variant="outline" size="sm" className="mt-3 rounded-full">
                <Link href="/writer">Open Manuscripts</Link>
              </Button>
            </div>
          )}
          <div className="rounded-xl bg-secondary/60 px-3 py-2 text-[0.71875rem] text-muted-foreground">
            <CheckSquare2 className="mr-1.5 inline size-3.5 text-moss" />
            {selectedIds.size} {selectedIds.size === 1 ? "paper" : "papers"} selected
          </div>
          <DialogFooter>
            <Button
              variant="ghost"
              onClick={() => setManuscriptDialogOpen(false)}
            >
              Cancel
            </Button>
            <Button
              disabled={
                !targetManuscript
                || selectedIds.size === 0
                || assignToManuscript.isPending
              }
              onClick={() => assignToManuscript.mutate()}
            >
              {assignToManuscript.isPending ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <FileText className="size-3.5" />
              )}
              Add sources
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog open={bulkDeleteOpen} onOpenChange={setBulkDeleteOpen}>
        <AlertDialogContent
          size="sm"
          className="isolate !w-[calc(100vw-2rem)] !max-w-lg !gap-0 overflow-hidden border border-border bg-popover !p-0 shadow-2xl"
        >
          <AlertDialogHeader className="px-5 pt-5 pb-4 sm:place-items-start sm:text-left">
            <AlertDialogTitle>
              Delete {selectedIds.size} selected{" "}
              {selectedIds.size === 1 ? "paper" : "papers"}?
            </AlertDialogTitle>
            <AlertDialogDescription>
              The Library files, saved PDF notes and Library references will be
              removed. Sources already added to a manuscript remain available
              there. This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          {selectedDocuments.length > 0 ? (
            <div className="mx-5 mb-5 space-y-1 rounded-xl border border-border/70 bg-secondary px-3 py-2.5">
              {selectedDocuments.slice(0, 3).map((document) => (
                <p
                  key={document.id}
                  className="truncate text-[0.75rem] text-foreground"
                >
                  {document.title ?? document.work_id}
                </p>
              ))}
              {selectedDocuments.length > 3 ? (
                <p className="text-[0.6875rem] text-muted-foreground">
                  and {selectedDocuments.length - 3} more
                </p>
              ) : null}
            </div>
          ) : null}
          <AlertDialogFooter className="!mx-0 !mb-0 grid grid-cols-1 gap-2 rounded-none rounded-b-2xl border-t border-border bg-muted/70 px-5 py-4 sm:grid-cols-2">
            <AlertDialogCancel className="w-full">Keep papers</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              className="w-full"
              disabled={bulkDelete.isPending || selectedIds.size === 0}
              onClick={(event) => {
                event.preventDefault();
                bulkDelete.mutate();
              }}
            >
              {bulkDelete.isPending ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <Trash2 className="size-3.5" />
              )}
              Delete papers
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
