"use client";

/**
 * Split-view PDF reader: the chat stays left, the paper opens right.
 *
 * The paper scrolls CONTINUOUSLY (all pages stacked, like a native viewer) —
 * no page-flip/scroll hybrid. Navigation, the notes rail and highlight chips
 * jump by scrolling to the page; an IntersectionObserver keeps the page
 * counter honest. Highlights come from the backend's show_paper tool and are
 * SERVER-VERIFIED quotes; matching against pdf.js's text layer is
 * fragment-based per page.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bot,
  BookmarkPlus,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronsDown,
  ChevronsUp,
  CircleAlert,
  Download,
  Highlighter,
  Languages,
  Loader2,
  MessageSquareText,
  Minus,
  Pencil,
  Plus,
  Scan,
  TextQuote,
  StickyNote,
  Trash2,
  UserRound,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/TextLayer.css";

import { Button } from "@/components/ui/button";
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
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { api, fetchDocumentBytes, fetchTranslatedDocumentBytes } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { downloadPdfBytes, paperPdfFilename } from "@/lib/paper-download";
import type { DocumentAnnotation } from "@/lib/types";
import { userFacingStoredErrorMessage } from "@/lib/user-facing-error";
import { cn } from "@/lib/utils";

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

export interface PaperPanelState {
  documentId: number;
  title: string;
  highlights: Array<{ page: number; quote: string; note: string }>;
  legalBasis?: string | null;
  license?: string | null;
  page?: number;
  /** An ephemeral passage to light up (clicked evidence chip in the chat). */
  flash?: { page: number; quote: string };
}

const LEGAL_LABEL: Record<string, string> = {
  user_upload: "Your upload",
  oa_gold: "Open access (gold)",
  oa_green: "Open access (green)",
  oa_hybrid: "Open access (hybrid)",
  oa_bronze: "Open access (bronze)",
  oa_diamond: "Open access (diamond)",
};

const COMMENT_COLORS = [
  { id: "moss", label: "Moss", className: "bg-moss" },
  { id: "amber", label: "Amber", className: "bg-amber-500" },
  { id: "rose", label: "Rose", className: "bg-rose-500" },
  { id: "blue", label: "Blue", className: "bg-blue-500" },
] as const;

const ZOOM_STEPS = [0.75, 0.9, 1, 1.15, 1.3, 1.5, 1.75, 2];

// Keep the complete translation implementation available for a later release,
// but do not expose or start it in the current product.
const PAPER_TRANSLATION_AVAILABLE = false;

const TRANSLATION_LANGUAGES = [
  { id: "de", label: "Deutsch" },
  { id: "en", label: "English" },
  { id: "es", label: "Español" },
  { id: "fr", label: "Français" },
  { id: "it", label: "Italiano" },
  { id: "nl", label: "Nederlands" },
  { id: "pl", label: "Polski" },
  { id: "pt", label: "Português" },
  { id: "sv", label: "Svenska" },
  { id: "tr", label: "Türkçe" },
] as const;

// mirror of the backend's quote normalisation: NFKC folds PDF ligatures
// (ﬁ → fi), curly quotes flatten, and hyphens drop entirely — so a quote
// saying "information" still matches a page carrying hyphenated
// "informa-tion", and "multi-document" matches "multidocument"
function norm(text: string): string {
  return text
    .normalize("NFKC")
    .replace(/[‘’]/g, "'")
    .replace(/[“”]/g, '"')
    .replace(/[–—­-]/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export default function PaperPanel({
  paper,
  onClose,
  onDiscuss,
  showClose = true,
}: {
  paper: PaperPanelState;
  onClose: () => void;
  showClose?: boolean;
  /** The user marked a passage and wants to discuss it in the chat. */
  onDiscuss?: (selection: {
    document_id: number;
    page: number;
    quote: string;
    title: string;
  }) => void;
}) {
  const { documentId, title } = paper;
  const { me } = useAuth();
  const isGerman = me?.language === "de";
  const queryClient = useQueryClient();
  const { data: annotations } = useQuery({
    queryKey: ["document-annotations", documentId],
    queryFn: () => api.documentAnnotations(documentId),
  });
  const highlights = useMemo(() => {
    const saved = (annotations ?? [])
      .filter((annotation: DocumentAnnotation) => annotation.quote.trim().length > 0)
      .map((annotation: DocumentAnnotation) => ({
        ...annotation,
        saved: true,
      }));
    const seen = new Set(saved.map((item) => `${item.page}:${norm(item.quote)}`));
    const suggested = paper.highlights
      .filter((item) => !seen.has(`${item.page}:${norm(item.quote)}`))
      .map((item) => ({
        ...item,
        id: null,
        source: "assistant" as const,
        author: "SixSentences AI",
        color: "amber" as const,
        created_at: "",
        document_id: documentId,
        saved: false,
      }));
    return [...saved, ...suggested];
  }, [annotations, paper.highlights, documentId]);
  const [currentPage, setCurrentPage] = useState(1);
  // the window-level mouseup listener reads the page without re-binding
  const currentPageRef = useRef(1);
  currentPageRef.current = currentPage;
  const [pageCount, setPageCount] = useState(0);
  const [width, setWidth] = useState(0);
  const [zoom, setZoom] = useState(1);
  const [controlsExpanded, setControlsExpanded] = useState(true);
  const [translationLanguage, setTranslationLanguage] = useState("");
  const bodyRef = useRef<HTMLDivElement>(null);
  const pageRefs = useRef(new Map<number, HTMLDivElement>());
  // the loaded pdf.js document: figure captures re-render the page at high
  // resolution instead of cropping the on-screen canvas
  const pdfDocRef = useRef<Parameters<
    NonNullable<React.ComponentProps<typeof Document>["onLoadSuccess"]>
  >[0] | null>(null);
  const captureBusy = useRef(false);
  // a marked passage pops a "discuss" pill right above the selection
  const [marked, setMarked] = useState<{
    top: number;
    left: number;
    page: number;
    quote: string;
  } | null>(null);
  const [annotating, setAnnotating] = useState<{
    top: number;
    left: number;
    page: number;
    quote: string;
  } | null>(null);
  const [noteDraft, setNoteDraft] = useState("");
  const [commentsOpen, setCommentsOpen] = useState(false);
  const [pageCommentDraft, setPageCommentDraft] = useState("");
  const [commentColor, setCommentColor] = useState<
    "moss" | "amber" | "rose" | "blue"
  >("moss");
  const [editingComment, setEditingComment] = useState<{
    id: number;
    note: string;
    color: "moss" | "amber" | "rose" | "blue";
  } | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<{
    id: number;
    page: number;
  } | null>(null);
  const [captureMode, setCaptureMode] = useState(false);
  const [figureSelection, setFigureSelection] = useState<{
    page: number;
    startX: number;
    startY: number;
    endX: number;
    endY: number;
  } | null>(null);
  const saveFigure = useMutation({
    mutationFn: (input: { page: number; imageBase64: string }) =>
      api.saveDocumentFigure(documentId, {
        page: input.page,
        image_base64: input.imageBase64,
        caption: `Figure captured from ${title}, page ${input.page}`,
        source_label: title,
      }),
    onSuccess: () => {
      setFigureSelection(null);
      setCaptureMode(false);
      void queryClient.invalidateQueries({ queryKey: ["figures"] });
      toast.success("Figure saved to Visual Lab with its source.");
    },
    onError: (error) => {
      setFigureSelection(null);
      toast.error(error instanceof Error ? error.message : "Could not save the figure.");
    },
  });
  const saveAnnotation = useMutation({
    mutationFn: (input: {
      page: number;
      quote: string;
      note: string;
      source: "user" | "assistant";
      color: "moss" | "amber" | "rose" | "blue";
    }) => api.createDocumentAnnotation(documentId, input),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["document-annotations", documentId],
      });
      setAnnotating(null);
      setNoteDraft("");
      setPageCommentDraft("");
      window.getSelection()?.removeAllRanges();
      toast.success("Saved to this paper.");
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Could not save the comment."),
  });
  const updateAnnotation = useMutation({
    mutationFn: (input: {
      id: number;
      note: string;
      color: "moss" | "amber" | "rose" | "blue";
    }) =>
      api.updateDocumentAnnotation(documentId, input.id, {
        note: input.note,
        color: input.color,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["document-annotations", documentId],
      });
      setEditingComment(null);
      toast.success("Comment updated.");
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Could not update the comment."),
  });
  const deleteAnnotation = useMutation({
    mutationFn: (annotationId: number) =>
      api.deleteDocumentAnnotation(documentId, annotationId),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["document-annotations", documentId],
      });
      setDeleteTarget(null);
      setHoverNote(null);
      toast.success("Comment deleted.");
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Could not delete the comment."),
  });
  // hovering a highlight in the paper shows the assistant's note in place
  const [hoverNote, setHoverNote] = useState<{
    top: number;
    left: number;
    index: number;
    note: string;
  } | null>(null);

  const goToPage = useCallback((page: number, smooth = true) => {
    setCurrentPage(page);
    pageRefs.current.get(page)?.scrollIntoView({
      behavior: smooth ? "smooth" : "auto",
      block: "start",
    });
  }, []);

  // pdf.js takes ownership of the buffer it is handed (worker transfer), so
  // the bytes are fetched per open and copied before hand-off — never cached
  const { data: bytes, isLoading, isError } = useQuery({
    queryKey: ["document-file", documentId],
    queryFn: () => fetchDocumentBytes(documentId),
    gcTime: 0,
    staleTime: 0,
    refetchOnWindowFocus: false,
  });
  const translationStatus = useQuery({
    queryKey: ["document-full-translation", documentId, translationLanguage],
    queryFn: () => api.documentTranslationStatus(documentId, translationLanguage),
    enabled: PAPER_TRANSLATION_AVAILABLE && Boolean(translationLanguage),
    retry: false,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "queued" || status === "running" || status === "rendering"
        ? 1500
        : false;
    },
  });
  const startTranslation = useMutation({
    mutationFn: (language: string) => api.startDocumentTranslation(documentId, language),
    onSuccess: (data, language) => {
      queryClient.setQueryData(
        ["document-full-translation", documentId, language],
        data,
      );
      void queryClient.invalidateQueries({
        queryKey: ["document-full-translation", documentId, language],
      });
    },
    onError: (error) =>
      toast.error(
        error instanceof Error ? error.message : "Could not start the translation.",
      ),
  });
  const {
    data: translatedBytes,
    isLoading: translatedFileLoading,
    isError: translatedFileError,
  } = useQuery({
    queryKey: ["document-translated-file", documentId, translationLanguage],
    queryFn: () => fetchTranslatedDocumentBytes(documentId, translationLanguage),
    enabled:
      PAPER_TRANSLATION_AVAILABLE &&
      Boolean(translationLanguage && translationStatus.data?.ready),
    gcTime: 0,
    staleTime: Number.POSITIVE_INFINITY,
    refetchOnWindowFocus: false,
  });
  const usingTranslatedPdf = Boolean(
    translationLanguage && translationStatus.data?.ready && translatedBytes,
  );

  // react-pdf treats an equal-but-new `file` object as a possible reload.
  // Keep a stable owned buffer for each immutable original/translated edition.
  const pdfFileRef = useRef<{
    key: string;
    file: { data: Uint8Array<ArrayBuffer> };
  } | null>(null);
  const activeBytes = usingTranslatedPdf ? translatedBytes : bytes;
  const activeFileKey = usingTranslatedPdf
    ? `${documentId}:${translationLanguage}:translated`
    : `${documentId}:original`;
  const file = (() => {
    if (!activeBytes) return null;
    if (pdfFileRef.current?.key !== activeFileKey) {
      pdfFileRef.current = {
        key: activeFileKey,
        file: { data: new Uint8Array(activeBytes.slice(0)) },
      };
    }
    return pdfFileRef.current.file;
  })();

  useEffect(() => {
    setTranslationLanguage("");
    setControlsExpanded(true);
  }, [documentId]);

  useEffect(() => {
    if (!translationLanguage) return;
    setCaptureMode(false);
    setFigureSelection(null);
    setMarked(null);
    setAnnotating(null);
  }, [translationLanguage]);

  useEffect(() => {
    pdfDocRef.current = null;
    pageRefs.current.clear();
    setPageCount(0);
    setCurrentPage(1);
  }, [documentId, translationLanguage, usingTranslatedPdf]);

  useEffect(() => {
    const node = bodyRef.current;
    if (!node) return;
    const observer = new ResizeObserver(() => {
      setWidth(Math.max(280, node.clientWidth - 32));
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  // the page counter follows the scroll position
  useEffect(() => {
    if (pageCount === 0) return;
    const root = bodyRef.current;
    if (!root) return;
    const visible = new Map<number, number>();
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          const page = Number((entry.target as HTMLElement).dataset.page);
          if (entry.isIntersecting) visible.set(page, entry.intersectionRatio);
          else visible.delete(page);
        }
        if (visible.size > 0) {
          const [best] = [...visible.entries()].sort((a, b) => b[1] - a[1]);
          setCurrentPage(best[0]);
        }
      },
      { root, threshold: [0, 0.2, 0.5, 0.8] },
    );
    for (const node of pageRefs.current.values()) observer.observe(node);
    return () => observer.disconnect();
  }, [pageCount]);

  // a fresh paper (or fresh highlights / a clicked evidence chip) starts at
  // its first anchor
  const initialTarget =
    paper.flash?.page ?? paper.page ?? highlights[0]?.page ?? 1;
  useEffect(() => {
    setMarked(null);
    setHoverNote(null);
    if (pageCount === 0 || initialTarget <= 1) return;
    // placeholders keep page heights stable, so jumping early is accurate
    const frame = requestAnimationFrame(() => goToPage(initialTarget, false));
    return () => cancelAnimationFrame(frame);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paper.documentId, paper.highlights, paper.flash, pageCount]);

  // index -1 is the ephemeral flash passage (an evidence chip in the chat)
  const quotesByPage = useMemo(() => {
    const map = new Map<number, Array<{ index: number; value: string }>>();
    highlights.forEach((highlight, index) => {
      // the server extractor and pdf.js occasionally disagree about page
      // numbering, so each quote is also tried on the neighboring pages; a
      // page that does not contain it simply never matches
      const value = norm(highlight.quote);
      for (const page of [highlight.page - 1, highlight.page, highlight.page + 1]) {
        if (page < 1) continue;
        const list = map.get(page) ?? [];
        list.push({ index, value });
        map.set(page, list);
      }
    });
    if (paper.flash) {
      const list = map.get(paper.flash.page) ?? [];
      list.push({ index: -1, value: norm(paper.flash.quote) });
      map.set(paper.flash.page, list);
    }
    return map;
  }, [highlights, paper.flash]);

  // POSITIONAL quote matching: a quote usually starts and ends mid-line, so
  // fragment-containment misses it. Instead the page's text-layer items are
  // joined into one string, the quote located in it, and every item whose
  // span overlaps the quote's span gets marked (with its highlight index for
  // the hover note). Computed once per page when pdf.js delivers the text.
  const markedItems = useRef(new Map<number, Map<number, number>>());
  // raw text items per rendered page: quote changes (a clicked evidence
  // chip adds a flash) recompute marks WITHOUT waiting for pdf.js to load
  // the text layer again — it never re-fires for already-rendered pages
  const pageItems = useRef(new Map<number, string[]>());
  const [markVersion, setMarkVersion] = useState(0);
  useEffect(() => {
    markedItems.current.clear();
    pageItems.current.clear();
    setMarkVersion((value) => value + 1);
  }, [paper.documentId]);

  const computeMarks = useCallback(
    (page: number, rawStrs: string[]) => {
      const quotes = quotesByPage.get(page);
      markedItems.current.delete(page);
      if (!quotes?.length) return;
      const norms = rawStrs.map(norm);
      // empty items (pdf.js emits many) must NOT join — each would add a
      // double space and break indexOf. An item ending in a hyphen is a
      // line-break hyphenation: its continuation joins WITHOUT a space.
      let joined = "";
      const spans: Array<[number, number]> = [];
      let previousRaw = "";
      for (let index = 0; index < norms.length; index += 1) {
        const value = norms[index];
        if (!value) {
          spans.push([joined.length, joined.length]); // zero-width: never marked
          continue;
        }
        const glue =
          joined.length === 0 || /[-­–]\s*$/.test(previousRaw) ? "" : " ";
        const start = joined.length + glue.length;
        joined += glue + value;
        spans.push([start, start + value.length]);
        previousRaw = rawStrs[index];
      }
      // digit-blind view with offsets back into `joined`: preprints carry
      // margin line numbers that extractors weave into the text, so a clean
      // quote only matches once digits vanish from both sides
      const buildLoose = (source: string): { loose: string; map: number[] } => {
        let loose = "";
        const map: number[] = [];
        let pendingSpace = false;
        for (let index = 0; index < source.length; index += 1) {
          const ch = source[index];
          if (ch >= "0" && ch <= "9") continue;
          if (ch === " ") {
            pendingSpace = loose.length > 0;
            continue;
          }
          if (pendingSpace) {
            loose += " ";
            map.push(index);
            pendingSpace = false;
          }
          loose += ch;
          map.push(index);
        }
        return { loose, map };
      };
      const looseView = buildLoose(joined);
      // digit-blind AND space-blind: stripping woven-in line numbers leaves
      // the two sides disagreeing about the spaces around them ("(29%" from
      // the server extractor vs "( 29 %" in the text layer), so the last
      // resort compares with spaces gone entirely
      const buildBare = (source: string): { bare: string; map: number[] } => {
        let bare = "";
        const map: number[] = [];
        for (let index = 0; index < source.length; index += 1) {
          const ch = source[index];
          if ((ch >= "0" && ch <= "9") || ch === " ") continue;
          bare += ch;
          map.push(index);
        }
        return { bare, map };
      };
      const bareView = buildBare(joined);
      // exact match first; long quotes fall back to head+tail anchors, then
      // to the digit-blind view, then to the space-blind view (each mapped
      // back to real offsets)
      const locate = (value: string): [number, number] | null => {
        const at = joined.indexOf(value);
        if (at >= 0) return [at, at + value.length];
        if (value.length >= 90) {
          const head = value.slice(0, 60);
          const tail = value.slice(-60);
          const start = joined.indexOf(head);
          if (start >= 0) {
            const tailAt = joined.indexOf(tail, start + head.length);
            if (tailAt >= 0 && tailAt + tail.length - start <= value.length * 1.6) {
              return [start, tailAt + tail.length];
            }
          }
        }
        const quoteLoose = buildLoose(value).loose;
        if (quoteLoose.length >= 40) {
          const looseAt = looseView.loose.indexOf(quoteLoose);
          if (looseAt >= 0) {
            const looseEnd = looseAt + quoteLoose.length;
            return [looseView.map[looseAt], looseView.map[looseEnd - 1] + 1];
          }
        }
        const quoteBare = buildBare(value).bare;
        if (quoteBare.length >= 40) {
          const bareAt = bareView.bare.indexOf(quoteBare);
          if (bareAt >= 0) {
            const bareEnd = bareAt + quoteBare.length;
            return [bareView.map[bareAt], bareView.map[bareEnd - 1] + 1];
          }
        }
        return null;
      };
      const marks = new Map<number, number>();
      for (const quote of quotes) {
        const span = locate(quote.value);
        if (!span) continue;
        const [at, end] = span;
        spans.forEach(([spanStart, spanEnd], itemIndex) => {
          const overlap = Math.min(spanEnd, end) - Math.max(spanStart, at);
          if (overlap >= 3 && norms[itemIndex].length > 0) {
            marks.set(itemIndex, quote.index);
          }
        });
      }
      if (marks.size > 0) markedItems.current.set(page, marks);
    },
    [quotesByPage],
  );

  // fresh quotes (new highlights, a flash) re-mark every cached page
  useEffect(() => {
    for (const [page, rawStrs] of pageItems.current) computeMarks(page, rawStrs);
    setMarkVersion((value) => value + 1);
  }, [computeMarks]);

  const handleTextSuccess = useCallback(
    (page: number) => (textContent: { items: unknown[] }) => {
      const rawStrs = textContent.items.map(
        (item) => (item as { str?: string }).str ?? "",
      );
      pageItems.current.set(page, rawStrs);
      computeMarks(page, rawStrs);
      if (markedItems.current.has(page)) {
        setMarkVersion((value) => value + 1); // re-render the text layer
      }
    },
    [computeMarks],
  );

  // per-page text renderer: wraps overlapping items in a mark that carries
  // its highlight index so hovering can surface the matching note
  const renderers = useMemo(() => {
    void markVersion; // new renderer identities force the text layer to redraw
    const map = new Map<
      number,
      (item: { str: string; itemIndex: number }) => string
    >();
    for (let page = 1; page <= pageCount; page += 1) {
      map.set(page, ({ str, itemIndex }) => {
        const highlightIndex = markedItems.current.get(page)?.get(itemIndex);
        if (highlightIndex !== undefined) {
          const flash = highlightIndex < 0 ? " paper-hl-flash" : "";
          const origin =
            highlightIndex >= 0 && highlights[highlightIndex]?.source === "assistant"
              ? " paper-hl-ai"
              : " paper-hl-user";
          return `<mark class="paper-hl${origin}${flash}" data-hl="${highlightIndex}">${escapeHtml(str)}</mark>`;
        }
        return escapeHtml(str);
      });
    }
    return map;
  }, [pageCount, markVersion]);

  // event delegation: the marks are raw HTML inside pdf.js's text layer, so
  // the hover note is tracked on the scroll container
  function handleMouseOver(event: React.MouseEvent) {
    const mark = (event.target as HTMLElement).closest?.(
      "mark.paper-hl",
    ) as HTMLElement | null;
    const host = bodyRef.current;
    if (!mark || !host) {
      if (hoverNote) setHoverNote(null);
      return;
    }
    const index = Number(mark.dataset.hl);
    const highlight = index >= 0 ? highlights[index] : undefined;
    if (!highlight?.note) {
      if (hoverNote) setHoverNote(null); // flash marks carry no note
      return;
    }
    const rect = mark.getBoundingClientRect();
    const hostRect = host.getBoundingClientRect();
    setHoverNote({
      top: Math.max(6, rect.top - hostRect.top + host.scrollTop - 8),
      left: Math.min(
        Math.max(120, rect.left - hostRect.left + rect.width / 2),
        Math.max(120, host.clientWidth - 130),
      ),
      index,
      note: `${highlight.author}: ${highlight.note}`,
    });
  }

  // the discuss pill listens on window: releasing the mouse outside the
  // scroll container (common after a long multi-line or cross-column drag)
  // must still pop it
  useEffect(() => {
    function onMouseUp() {
      if (captureMode || translationLanguage) return;
      const selection = window.getSelection();
      const raw = selection?.toString().replace(/\s+/g, " ").trim() ?? "";
      if (!selection || selection.isCollapsed || raw.length < 8) {
        setMarked(null);
        return;
      }
      // EITHER end of the selection may sit in the text layer — dragging
      // often starts or ends in the gutter between columns or lines
      const toElement = (node: Node | null | undefined) =>
        node instanceof Element ? node : (node?.parentElement ?? null);
      const inText = (el: Element | null) =>
        Boolean(el?.closest(".react-pdf__Page__textContent"));
      const anchorEl = toElement(selection.anchorNode);
      const focusEl = toElement(selection.focusNode);
      const textEl = inText(anchorEl) ? anchorEl : inText(focusEl) ? focusEl : null;
      const host = bodyRef.current;
      if (!textEl || !host || !host.contains(textEl)) {
        setMarked(null);
        return;
      }
      // a huge cross-column grab is trimmed to the backend cap, never
      // silently dropped — the popup must always appear
      const quote = raw.length > 1100 ? raw.slice(0, 1100).trimEnd() : raw;
      const pageNode = textEl.closest("[data-page]") as HTMLElement | null;
      const page =
        Number(pageNode?.dataset.page ?? currentPageRef.current) ||
        currentPageRef.current;
      // anchor the pill to the END of the selection (its last client rect)
      const range = selection.getRangeAt(selection.rangeCount - 1);
      const rects = range.getClientRects();
      const rect =
        rects.length > 0 ? rects[rects.length - 1] : range.getBoundingClientRect();
      const hostRect = host.getBoundingClientRect();
      setMarked({
        top: Math.max(4, rect.top - hostRect.top + host.scrollTop - 42),
        left: Math.min(
          Math.max(8, rect.left - hostRect.left + rect.width / 2 - 92),
          Math.max(8, host.clientWidth - 200),
        ),
        page,
        quote,
      });
    }
    window.addEventListener("mouseup", onMouseUp);
    return () => window.removeEventListener("mouseup", onMouseUp);
  }, [captureMode, translationLanguage]);

  function pointerPosition(
    event: React.PointerEvent<HTMLDivElement>,
    node: HTMLDivElement,
  ) {
    const rect = node.getBoundingClientRect();
    return {
      x: Math.max(0, Math.min(rect.width, event.clientX - rect.left)),
      y: Math.max(0, Math.min(rect.height, event.clientY - rect.top)),
    };
  }

  function beginFigureCapture(
    event: React.PointerEvent<HTMLDivElement>,
    page: number,
  ) {
    if (!captureMode || saveFigure.isPending) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    const point = pointerPosition(event, event.currentTarget);
    setMarked(null);
    setAnnotating(null);
    window.getSelection()?.removeAllRanges();
    setFigureSelection({
      page,
      startX: point.x,
      startY: point.y,
      endX: point.x,
      endY: point.y,
    });
  }

  function moveFigureCapture(
    event: React.PointerEvent<HTMLDivElement>,
    page: number,
  ) {
    if (!captureMode || figureSelection?.page !== page) return;
    const point = pointerPosition(event, event.currentTarget);
    setFigureSelection((current) =>
      current ? { ...current, endX: point.x, endY: point.y } : null,
    );
  }

  /** Re-render the selected region straight from the PDF at print
   * resolution (~2600 px on the crop's longer edge), so captures stop
   * being screenshots of the on-screen canvas. */
  async function renderHdCrop(
    pageNumber: number,
    box: { x: number; y: number; w: number; h: number },
  ): Promise<string> {
    const pdf = pdfDocRef.current;
    if (!pdf) throw new Error("document not ready");
    const pdfPage = await pdf.getPage(pageNumber);
    const base = pdfPage.getViewport({ scale: 1 });
    const cropEdge = Math.max(box.w * base.width, box.h * base.height);
    let scale = Math.min(8, Math.max(2, 2600 / Math.max(1, cropEdge)));
    // whole-page cap keeps memory and the server's 8000 px limit safe
    scale = Math.min(scale, 6000 / Math.max(base.width, base.height));
    const viewport = pdfPage.getViewport({ scale });
    const full = document.createElement("canvas");
    full.width = Math.round(viewport.width);
    full.height = Math.round(viewport.height);
    await pdfPage.render({ canvas: full, viewport }).promise;
    const crop = document.createElement("canvas");
    crop.width = Math.max(1, Math.round(box.w * full.width));
    crop.height = Math.max(1, Math.round(box.h * full.height));
    const cropContext = crop.getContext("2d");
    if (!cropContext) throw new Error("canvas unavailable");
    cropContext.drawImage(
      full,
      Math.round(box.x * full.width),
      Math.round(box.y * full.height),
      crop.width,
      crop.height,
      0,
      0,
      crop.width,
      crop.height,
    );
    return crop.toDataURL("image/png").replace(/^data:image\/png;base64,/, "");
  }

  function finishFigureCapture(
    event: React.PointerEvent<HTMLDivElement>,
    page: number,
  ) {
    if (!captureMode || figureSelection?.page !== page) return;
    if (captureBusy.current) return;
    const point = pointerPosition(event, event.currentTarget);
    const selection = { ...figureSelection, endX: point.x, endY: point.y };
    const left = Math.min(selection.startX, selection.endX);
    const top = Math.min(selection.startY, selection.endY);
    const selectedWidth = Math.abs(selection.endX - selection.startX);
    const selectedHeight = Math.abs(selection.endY - selection.startY);
    if (selectedWidth < 40 || selectedHeight < 40) {
      setFigureSelection(null);
      toast.error("Drag a larger area around the figure.");
      return;
    }
    const source = event.currentTarget.querySelector("canvas") as HTMLCanvasElement | null;
    const pageRect = event.currentTarget.getBoundingClientRect();
    if (!source || pageRect.width <= 0 || pageRect.height <= 0) {
      setFigureSelection(null);
      toast.error("This page is still rendering. Try again in a moment.");
      return;
    }
    const box = {
      x: left / pageRect.width,
      y: top / pageRect.height,
      w: selectedWidth / pageRect.width,
      h: selectedHeight / pageRect.height,
    };
    const screenCrop = (): string => {
      const scaleX = source.width / pageRect.width;
      const scaleY = source.height / pageRect.height;
      const crop = document.createElement("canvas");
      crop.width = Math.max(1, Math.round(selectedWidth * scaleX));
      crop.height = Math.max(1, Math.round(selectedHeight * scaleY));
      const context = crop.getContext("2d");
      if (!context) throw new Error("canvas unavailable");
      context.drawImage(
        source,
        Math.round(left * scaleX),
        Math.round(top * scaleY),
        crop.width,
        crop.height,
        0,
        0,
        crop.width,
        crop.height,
      );
      return crop.toDataURL("image/png").replace(/^data:image\/png;base64,/, "");
    };
    captureBusy.current = true;
    void (async () => {
      try {
        let imageBase64: string;
        try {
          imageBase64 = await renderHdCrop(page, box);
        } catch {
          imageBase64 = screenCrop();
        }
        saveFigure.mutate({ page, imageBase64 });
      } catch {
        setFigureSelection(null);
        toast.error("Could not read this page. Try again in a moment.");
      } finally {
        captureBusy.current = false;
      }
    })();
  }

  const legal = paper.legalBasis ? LEGAL_LABEL[paper.legalBasis] : null;
  const effectiveWidth = Math.round(width * zoom);
  const zoomIndex = ZOOM_STEPS.indexOf(zoom);

  return (
    <div className="relative flex h-full min-h-0 flex-col">
      {/* Header */}
      <div
        className={cn(
          "relative flex shrink-0 flex-wrap items-center gap-2 border-b border-border px-3 py-2.5 sm:px-4",
          showClose ? "pr-20 sm:pr-20" : "pr-12 sm:pr-12",
        )}
      >
        <div className="min-w-0 flex-1">
          <p className="truncate text-[0.8125rem] font-medium" title={title}>
            {title}
          </p>
          <p className="mt-0.5 flex items-center gap-2 font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
            {legal && <span className="text-moss">{legal}</span>}
            {paper.license && <span>{paper.license}</span>}
          </p>
        </div>
        <div
          className={cn(
            "flex basis-full flex-wrap items-center gap-2 overflow-hidden transition-[max-height,opacity] duration-200 ease-out sm:flex-nowrap",
            controlsExpanded
              ? "max-h-28 opacity-100 sm:max-h-10 sm:overflow-x-auto"
              : "pointer-events-none max-h-0 opacity-0",
          )}
          aria-hidden={!controlsExpanded}
          inert={!controlsExpanded ? true : undefined}
        >
          <div className="flex shrink-0 items-center gap-0.5 rounded-full border border-border bg-secondary/50 px-1 py-0.5">
            <Button
              variant="ghost"
              size="icon"
              className="size-6 rounded-full"
              onClick={() => goToPage(Math.max(1, currentPage - 1))}
              disabled={currentPage <= 1}
              aria-label="Previous page"
            >
              <ChevronLeft className="size-3.5" />
            </Button>
            <span className="min-w-[3.25rem] text-center font-mono text-[0.65625rem] text-muted-foreground">
              {currentPage} / {pageCount || "…"}
            </span>
            <Button
              variant="ghost"
              size="icon"
              className="size-6 rounded-full"
              onClick={() =>
                goToPage(Math.min(pageCount || currentPage + 1, currentPage + 1))
              }
              disabled={pageCount > 0 && currentPage >= pageCount}
              aria-label="Next page"
            >
              <ChevronRight className="size-3.5" />
            </Button>
          </div>
          <div className="flex shrink-0 items-center gap-0.5 rounded-full border border-border bg-secondary/50 px-1 py-0.5">
              <Button
                variant="ghost"
                size="icon"
                className="size-6 rounded-full"
                onClick={() => setZoom(ZOOM_STEPS[Math.max(0, zoomIndex - 1)])}
                disabled={zoomIndex <= 0}
                aria-label="Zoom out"
              >
                <Minus className="size-3" />
              </Button>
              <button
                type="button"
                onClick={() => setZoom(1)}
                className="min-w-[2.6rem] cursor-pointer text-center font-mono text-[0.625rem] text-muted-foreground hover:text-foreground"
                aria-label="Reset zoom"
              >
                {Math.round(zoom * 100)}%
              </button>
              <Button
                variant="ghost"
                size="icon"
                className="size-6 rounded-full"
                onClick={() =>
                  setZoom(ZOOM_STEPS[Math.min(ZOOM_STEPS.length - 1, zoomIndex + 1)])
                }
                disabled={zoomIndex >= ZOOM_STEPS.length - 1}
                aria-label="Zoom in"
              >
                <Plus className="size-3" />
              </Button>
          </div>
          {PAPER_TRANSLATION_AVAILABLE ? (
            <label className="flex h-7 shrink-0 items-center gap-1.5 rounded-full border border-border bg-secondary/50 px-2 text-[0.6875rem] text-muted-foreground transition-colors focus-within:border-moss/60 focus-within:text-foreground">
              <Languages className="size-3.5" />
              <span className="relative block">
                <select
                  value={translationLanguage}
                  onChange={(event) => setTranslationLanguage(event.target.value)}
                  className="peer cursor-pointer appearance-none bg-transparent pl-0 pr-5 text-[0.6875rem] text-foreground outline-none disabled:cursor-not-allowed disabled:opacity-50 forced-colors:appearance-auto"
                  aria-label="Translate paper"
                >
                  <option value="">Original</option>
                  {TRANSLATION_LANGUAGES.map((language) => (
                    <option key={language.id} value={language.id}>
                      {language.label}
                    </option>
                  ))}
                </select>
                <ChevronDown
                  aria-hidden="true"
                  className="pointer-events-none absolute right-0 top-1/2 size-3 -translate-y-1/2 text-muted-foreground peer-disabled:opacity-50 forced-colors:hidden"
                />
              </span>
            </label>
          ) : null}
          {PAPER_TRANSLATION_AVAILABLE &&
          translationLanguage &&
          translationStatus.data?.ready &&
          translatedBytes ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-7 shrink-0 rounded-full"
                  aria-label="Download translated PDF"
                  onClick={() => {
                    downloadPdfBytes(
                      translatedBytes,
                      paperPdfFilename(title, `${translationLanguage}-translated`),
                    );
                  }}
                >
                  <Download className="size-3.5" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Download translated PDF</TooltipContent>
            </Tooltip>
          ) : null}
          {!translationLanguage ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="h-7 shrink-0 rounded-full px-2.5 text-[0.6875rem]"
                  aria-label={`${isGerman ? "Original-PDF herunterladen" : "Download original PDF"}: ${title}`}
                  aria-busy={isLoading}
                  disabled={!bytes || isLoading || isError}
                  onClick={() => {
                    if (!bytes) return;
                    try {
                      downloadPdfBytes(bytes, paperPdfFilename(title));
                    } catch {
                      toast.error(
                        isGerman
                          ? "Die PDF konnte nicht heruntergeladen werden."
                          : "The PDF could not be downloaded.",
                      );
                    }
                  }}
                >
                  {isLoading ? (
                    <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
                  ) : (
                    <Download className="size-3.5" aria-hidden="true" />
                  )}
                  {isGerman ? "Original-PDF" : "Original PDF"}
                </Button>
              </TooltipTrigger>
              <TooltipContent>
                {isError
                  ? isGerman ? "PDF nicht verfügbar" : "PDF unavailable"
                  : isGerman ? "Original-PDF herunterladen" : "Download original PDF"}
              </TooltipContent>
            </Tooltip>
          ) : null}
          {!translationLanguage ? (
            <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant={commentsOpen ? "secondary" : "ghost"}
                size="sm"
                className={cn(
                  "h-7 shrink-0 rounded-full px-2.5 text-[0.6875rem]",
                  commentsOpen && "text-moss ring-1 ring-moss/25",
                )}
                onClick={() => setCommentsOpen((open) => !open)}
                aria-label="Open paper comments"
                aria-expanded={commentsOpen}
              >
                <MessageSquareText className="size-3.5" />
                <span className="hidden xl:inline">Comments</span>
                {(annotations?.length ?? 0) > 0 ? (
                  <span className="rounded-full bg-accent px-1.5 font-mono text-[0.5625rem] text-moss">
                    {annotations?.length}
                  </span>
                ) : null}
              </Button>
            </TooltipTrigger>
            <TooltipContent>Comments and saved highlights</TooltipContent>
            </Tooltip>
          ) : null}
          {!translationLanguage ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant={captureMode ? "secondary" : "ghost"}
                  size="icon"
                  className={cn(
                    "size-7 shrink-0 rounded-full",
                    captureMode && "text-moss ring-1 ring-moss/30",
                  )}
                  onClick={() => {
                    setCaptureMode((current) => !current);
                    setFigureSelection(null);
                  }}
                  disabled={saveFigure.isPending}
                  aria-label="Capture a figure"
                >
                  {saveFigure.isPending ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <Scan className="size-4" />
                  )}
                </Button>
              </TooltipTrigger>
              <TooltipContent>Capture a figure with its paper source</TooltipContent>
            </Tooltip>
          ) : null}
        </div>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className={cn(
                "absolute top-2.5 z-10 size-7 rounded-full bg-card/80 text-muted-foreground backdrop-blur-sm hover:bg-secondary sm:top-2.5",
                showClose ? "right-12 sm:right-12" : "right-3 sm:right-4",
              )}
              onClick={() => setControlsExpanded((expanded) => !expanded)}
              aria-label={controlsExpanded ? "Hide reader controls" : "Show reader controls"}
              aria-expanded={controlsExpanded}
            >
              {controlsExpanded ? (
                <ChevronsUp className="size-4" />
              ) : (
                <ChevronsDown className="size-4" />
              )}
            </Button>
          </TooltipTrigger>
          <TooltipContent>
            {controlsExpanded ? "Hide reader controls" : "Show reader controls"}
          </TooltipContent>
        </Tooltip>
        {showClose && (
          <Button
            variant="ghost"
            size="icon"
            className="absolute right-3 top-2.5 z-10 size-7 rounded-full bg-card/80 text-muted-foreground backdrop-blur-sm hover:bg-secondary sm:right-4"
            onClick={onClose}
            aria-label="Close reader"
          >
            <X className="size-4" />
          </Button>
        )}
      </div>

      {/* One slim rail: page chips jump, hovering (chip or highlight) explains */}
      {controlsExpanded && !translationLanguage && highlights.length > 0 && (
        <div className="flex shrink-0 items-center gap-1.5 overflow-x-auto border-b border-border bg-accent/30 px-3 py-1.5">
          <span className="flex shrink-0 items-center gap-1 font-mono text-[0.625rem] uppercase tracking-[0.18em] text-moss">
            <Highlighter className="size-3" />
            {highlights.length} note{highlights.length === 1 ? "" : "s"}
          </span>
          {highlights.map((highlight, index) => (
            <Tooltip key={highlight.id ?? `suggested-${index}`}>
              <TooltipTrigger asChild>
                <span className="flex shrink-0 items-center overflow-hidden rounded-full border border-border bg-card">
                  <button
                    type="button"
                    onClick={() => goToPage(highlight.page)}
                    className={cn(
                      "flex cursor-pointer items-center gap-1 px-2 py-0.5 font-mono text-[0.625rem] font-medium transition-colors",
                      highlight.source === "assistant"
                        ? "text-amber-700 hover:bg-amber-50 dark:text-amber-200 dark:hover:bg-amber-300/10"
                        : "text-moss hover:bg-accent",
                    )}
                  >
                    {highlight.source === "assistant" ? (
                      <Bot className="size-2.5" />
                    ) : (
                      <UserRound className="size-2.5" />
                    )}
                    {highlight.source === "assistant" ? "AI" : "You"} · p.{highlight.page}
                  </button>
                  {!highlight.saved ? (
                    <button
                      type="button"
                      onClick={() =>
                        saveAnnotation.mutate({
                          page: highlight.page,
                          quote: highlight.quote,
                          note: highlight.note,
                          source: "assistant",
                          color: "amber",
                        })
                      }
                      className="cursor-pointer border-l border-border px-1.5 py-1 text-amber-700 hover:bg-amber-50 dark:text-amber-200 dark:hover:bg-amber-300/10"
                      aria-label="Save AI highlight"
                    >
                      <BookmarkPlus className="size-3" />
                    </button>
                  ) : typeof highlight.id === "number" ? (
                    <button
                      type="button"
                      onClick={() =>
                        setDeleteTarget({ id: highlight.id as number, page: highlight.page })
                      }
                      className="cursor-pointer border-l border-border px-1.5 py-1 text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
                      aria-label={`Delete note on page ${highlight.page}`}
                    >
                      <Trash2 className="size-3" />
                    </button>
                  ) : null}
                </span>
              </TooltipTrigger>
              <TooltipContent side="bottom" className="max-w-[20rem]">
                <p className="font-medium">{highlight.author}</p>
                <p className="mt-0.5">{highlight.note || "Highlighted passage"}</p>
                <p className="mt-1 italic opacity-75">
                  “{highlight.quote.slice(0, 110)}
                  {highlight.quote.length > 110 ? "…" : ""}”
                </p>
              </TooltipContent>
            </Tooltip>
          ))}
          <span className="ml-auto hidden shrink-0 text-[0.625rem] text-muted-foreground/70 lg:block">
            Hover a highlight for its note
          </span>
        </div>
      )}
      {captureMode && (
        <div className="flex shrink-0 items-center justify-between gap-3 border-b border-moss/20 bg-moss-surface/7 px-4 py-2 text-[0.71875rem] text-moss">
          <span>
            Drag around a figure. It will be saved to Visual Lab with paper and page provenance.
          </span>
          <button
            type="button"
            className="shrink-0 cursor-pointer font-medium hover:underline"
            onClick={() => {
              setCaptureMode(false);
              setFigureSelection(null);
            }}
          >
            Cancel
          </button>
        </div>
      )}

      {commentsOpen && (
        <aside
          aria-label="Paper comments"
          className="absolute bottom-0 right-0 top-20 z-30 flex w-full flex-col border-l border-border bg-card shadow-[-12px_0_36px_rgba(12,29,25,0.12)] sm:top-[3.25rem] sm:w-[21rem]"
        >
          <div className="flex shrink-0 items-center justify-between border-b border-border px-4 py-3">
            <div>
              <p className="text-[0.8125rem] font-medium">Paper comments</p>
              <p className="mt-0.5 text-[0.65625rem] text-muted-foreground">
                Add a page note or manage a highlighted passage.
              </p>
            </div>
            <Button
              variant="ghost"
              size="icon"
              className="size-8 rounded-full"
              onClick={() => setCommentsOpen(false)}
              aria-label="Close comments"
            >
              <X className="size-4" />
            </Button>
          </div>
          <div className="shrink-0 border-b border-border bg-secondary/25 p-3">
            <p className="font-mono text-[0.59375rem] uppercase tracking-[0.14em] text-muted-foreground">
              Comment on page {currentPage}
            </p>
            <Textarea
              value={pageCommentDraft}
              maxLength={4000}
              onChange={(event) => setPageCommentDraft(event.target.value)}
              placeholder="Add an observation, question or review note…"
              aria-label={`Comment on page ${currentPage}`}
              className="mt-2 min-h-20 resize-none rounded-xl bg-card text-[0.75rem]"
            />
            <div className="mt-2 flex items-center gap-1.5">
              {COMMENT_COLORS.map((color) => (
                <button
                  key={color.id}
                  type="button"
                  title={color.label}
                  aria-label={`Use ${color.label} for this comment`}
                  aria-pressed={commentColor === color.id}
                  onClick={() => setCommentColor(color.id)}
                  className={cn(
                    "size-5 cursor-pointer rounded-full border-2 border-card ring-1 ring-border transition-transform",
                    color.className,
                    commentColor === color.id && "scale-110 ring-2 ring-moss/40",
                  )}
                />
              ))}
              <Button
                size="sm"
                className="ml-auto h-8 rounded-full"
                disabled={!pageCommentDraft.trim() || saveAnnotation.isPending}
                onClick={() =>
                  saveAnnotation.mutate({
                    page: currentPage,
                    quote: "",
                    note: pageCommentDraft,
                    source: "user",
                    color: commentColor,
                  })
                }
              >
                {saveAnnotation.isPending ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <Plus className="size-3.5" />
                )}
                Add
              </Button>
            </div>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-3">
            {(annotations?.length ?? 0) === 0 ? (
              <div className="rounded-2xl border border-dashed border-border p-6 text-center">
                <MessageSquareText className="mx-auto size-5 text-muted-foreground" />
                <p className="mt-2 text-[0.75rem] font-medium">No comments yet</p>
                <p className="mt-1 text-[0.6875rem] leading-relaxed text-muted-foreground">
                  Comment on the page here, or select text in the PDF to anchor
                  a note to an exact passage.
                </p>
              </div>
            ) : (
              <div className="space-y-2">
                {annotations?.map((annotation) => {
                  const editing = editingComment?.id === annotation.id;
                  const color =
                    COMMENT_COLORS.find((item) => item.id === annotation.color) ??
                    COMMENT_COLORS[0];
                  return (
                    <article
                      key={annotation.id}
                      className="rounded-2xl border border-border bg-background p-3"
                    >
                      <div className="flex items-center gap-2">
                        <span className={cn("size-2.5 shrink-0 rounded-full", color.className)} />
                        <button
                          type="button"
                          className="cursor-pointer font-mono text-[0.59375rem] uppercase tracking-[0.12em] text-moss hover:underline"
                          onClick={() => goToPage(annotation.page)}
                        >
                          Page {annotation.page}
                        </button>
                        <span className="truncate text-[0.625rem] text-muted-foreground">
                          {annotation.author}
                        </span>
                        <span className="ml-auto font-mono text-[0.53125rem] uppercase tracking-[0.1em] text-muted-foreground/70">
                          {annotation.quote ? "Passage" : "Page note"}
                        </span>
                      </div>
                      {annotation.quote ? (
                        <p className="mt-2 line-clamp-3 border-l-2 border-border pl-2.5 text-[0.6875rem] italic leading-relaxed text-muted-foreground">
                          “{annotation.quote}”
                        </p>
                      ) : null}
                      {editing ? (
                        <>
                          <Textarea
                            autoFocus
                            value={editingComment.note}
                            maxLength={4000}
                            onChange={(event) =>
                              setEditingComment((current) =>
                                current ? { ...current, note: event.target.value } : null,
                              )
                            }
                            className="mt-2 min-h-20 resize-none rounded-xl text-[0.71875rem]"
                          />
                          <div className="mt-2 flex items-center gap-1.5">
                            {COMMENT_COLORS.map((item) => (
                              <button
                                key={item.id}
                                type="button"
                                title={item.label}
                                aria-label={`Change comment to ${item.label}`}
                                onClick={() =>
                                  setEditingComment((current) =>
                                    current ? { ...current, color: item.id } : null,
                                  )
                                }
                                className={cn(
                                  "size-5 cursor-pointer rounded-full border-2 border-card ring-1 ring-border",
                                  item.className,
                                  editingComment.color === item.id &&
                                    "scale-110 ring-2 ring-moss/40",
                                )}
                              />
                            ))}
                            <Button
                              variant="ghost"
                              size="sm"
                              className="ml-auto h-7 rounded-full"
                              onClick={() => setEditingComment(null)}
                            >
                              Cancel
                            </Button>
                            <Button
                              size="sm"
                              className="h-7 rounded-full"
                              disabled={
                                updateAnnotation.isPending ||
                                (!annotation.quote && !editingComment.note.trim())
                              }
                              onClick={() => updateAnnotation.mutate(editingComment)}
                            >
                              {updateAnnotation.isPending ? (
                                <Loader2 className="size-3 animate-spin" />
                              ) : (
                                <Check className="size-3" />
                              )}
                              Save
                            </Button>
                          </div>
                        </>
                      ) : (
                        <>
                          <p className="mt-2 whitespace-pre-wrap text-[0.75rem] leading-relaxed">
                            {annotation.note || "Highlighted passage"}
                          </p>
                          <div className="mt-2 flex justify-end gap-1">
                            <Button
                              variant="ghost"
                              size="icon"
                              className="size-7 rounded-full text-muted-foreground"
                              onClick={() =>
                                setEditingComment({
                                  id: annotation.id,
                                  note: annotation.note,
                                  color: annotation.color,
                                })
                              }
                              aria-label={`Edit comment on page ${annotation.page}`}
                            >
                              <Pencil className="size-3" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="icon"
                              className="size-7 rounded-full text-muted-foreground hover:text-destructive"
                              onClick={() =>
                                setDeleteTarget({
                                  id: annotation.id,
                                  page: annotation.page,
                                })
                              }
                              aria-label={`Delete comment on page ${annotation.page}`}
                            >
                              <Trash2 className="size-3" />
                            </Button>
                          </div>
                        </>
                      )}
                    </article>
                  );
                })}
              </div>
            )}
          </div>
        </aside>
      )}

      {/* The paper: continuous scroll — mark any passage to discuss it */}
      <div
        ref={bodyRef}
        onMouseOver={handleMouseOver}
        onMouseLeave={() => setHoverNote(null)}
        className="relative min-h-0 flex-1 overflow-y-auto bg-secondary/40 p-4"
      >
        {PAPER_TRANSLATION_AVAILABLE && translationLanguage && !usingTranslatedPdf ? (
          <section className="absolute inset-0 z-20 grid place-items-center overflow-y-auto bg-secondary/95 p-4 backdrop-blur-sm sm:p-6">
            <article className="w-full max-w-xl rounded-3xl border border-border bg-card p-6 shadow-[0_14px_50px_rgba(12,29,25,0.14)] sm:p-8">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <p className="flex items-center gap-2 font-mono text-[0.625rem] uppercase tracking-[0.18em] text-moss">
                    <Languages className="size-3.5" /> Full paper translation
                  </p>
                  <h3 className="mt-2 font-serif text-2xl leading-tight">
                    {TRANSLATION_LANGUAGES.find(
                      (language) => language.id === translationLanguage,
                    )?.label ?? translationLanguage} reading edition
                  </h3>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-8 shrink-0 rounded-full"
                  onClick={() => setTranslationLanguage("")}
                >
                  Show original
                </Button>
              </div>

              {translationStatus.isPending ? (
                <div className="mt-8 flex items-center gap-3 rounded-2xl border border-border bg-secondary/50 p-4 text-[0.8125rem] text-muted-foreground">
                  <Loader2 className="size-4 animate-spin" /> Checking for an existing edition…
                </div>
              ) : translationStatus.isError ? (
                <div className="mt-8 rounded-2xl border border-destructive/25 bg-destructive/5 p-5">
                  <CircleAlert className="size-5 text-destructive" />
                  <p className="mt-2 text-[0.8125rem] font-medium">
                    Translation status could not be loaded.
                  </p>
                  <Button
                    variant="outline"
                    size="sm"
                    className="mt-4 rounded-full"
                    onClick={() => void translationStatus.refetch()}
                  >
                    Try again
                  </Button>
                </div>
              ) : translationStatus.data?.status === "not_started" ? (
                <div className="mt-7">
                  <p className="text-[0.8125rem] leading-6 text-muted-foreground">
                    Translate the complete extractable text of all{" "}
                    {translationStatus.data.page_count || pageCount || "…"} source pages and
                    typeset it as a new, searchable PDF. Citations, numbers, equations and
                    page provenance stay visible. You can leave while it runs.
                  </p>
                  <div className="mt-5 rounded-2xl border border-border bg-secondary/40 p-4 text-[0.71875rem] leading-5 text-muted-foreground">
                    The original publication remains unchanged. Completed pages are cached,
                    so an interrupted job resumes in place.
                  </div>
                  <Button
                    className="mt-5 w-full rounded-full"
                    disabled={startTranslation.isPending}
                    onClick={() => startTranslation.mutate(translationLanguage)}
                  >
                    {startTranslation.isPending ? (
                      <Loader2 className="size-4 animate-spin" />
                    ) : (
                      <Languages className="size-4" />
                    )}
                    Translate full paper
                  </Button>
                </div>
              ) : translationStatus.data?.status === "failed" ? (
                <div className="mt-8 rounded-2xl border border-destructive/25 bg-destructive/5 p-5">
                  <CircleAlert className="size-5 text-destructive" />
                  <p className="mt-2 text-[0.8125rem] font-medium">Translation paused</p>
                  <p className="mt-1 text-[0.71875rem] leading-5 text-muted-foreground">
                    {userFacingStoredErrorMessage(
                      translationStatus.data.error,
                      "Retry to continue from the last completed page.",
                    )}
                  </p>
                  <Button
                    variant="outline"
                    size="sm"
                    className="mt-4 rounded-full"
                    disabled={startTranslation.isPending}
                    onClick={() => startTranslation.mutate(translationLanguage)}
                  >
                    {startTranslation.isPending ? (
                      <Loader2 className="size-3.5 animate-spin" />
                    ) : null}
                    Resume translation
                  </Button>
                </div>
              ) : translationStatus.data?.status === "completed" ? (
                <div className="mt-8 flex items-center gap-3 rounded-2xl border border-border bg-secondary/50 p-4 text-[0.8125rem] text-muted-foreground">
                  {translatedFileLoading ? (
                    <>
                      <Loader2 className="size-4 animate-spin" /> Loading the translated PDF…
                    </>
                  ) : translatedFileError ? (
                    <>
                      <CircleAlert className="size-4 text-destructive" />
                      The translated PDF could not be loaded. Switch to the original and try
                      again.
                    </>
                  ) : (
                    <>
                      <Loader2 className="size-4 animate-spin" /> Loading the translated PDF…
                    </>
                  )}
                </div>
              ) : (
                <div className="mt-8">
                  <div className="flex items-end justify-between gap-4">
                    <div>
                      <p className="text-[0.8125rem] font-medium">
                        {translationStatus.data?.status === "rendering"
                          ? "Typesetting the translated PDF"
                          : "Translating every page"}
                      </p>
                      <p className="mt-1 text-[0.71875rem] text-muted-foreground">
                        {translationStatus.data?.completed_pages ?? 0} of{" "}
                        {translationStatus.data?.page_count ?? pageCount} source pages complete
                      </p>
                    </div>
                    <span className="font-mono text-sm text-moss">
                      {translationStatus.data?.percent ?? 0}%
                    </span>
                  </div>
                  <div className="mt-4 h-2 overflow-hidden rounded-full bg-secondary">
                    <div
                      className="h-full rounded-full bg-moss transition-[width] duration-500"
                      style={{ width: `${translationStatus.data?.percent ?? 0}%` }}
                    />
                  </div>
                  <p className="mt-5 text-[0.71875rem] leading-5 text-muted-foreground">
                    This runs as a resumable background job. You can close the paper or leave the
                    app and return later without losing completed pages.
                  </p>
                </div>
              )}
            </article>
          </section>
        ) : null}
        {hoverNote && (
          <div
            style={{ top: hoverNote.top, left: hoverNote.left }}
            className="pointer-events-none absolute z-20 w-max max-w-[21rem] -translate-x-1/2 -translate-y-full rounded-xl bg-pine px-3 py-2 shadow-[0_6px_20px_rgba(12,29,25,0.35)]"
          >
            <p className="text-[0.71875rem] leading-snug text-ivory">
              {hoverNote.note}
            </p>
          </div>
        )}
        {marked && (
          <div
            style={{ top: marked.top, left: marked.left }}
            onMouseDown={(event) => event.preventDefault()} // keep the selection
            className="absolute z-10 flex overflow-hidden rounded-full bg-pine text-[0.75rem] font-medium text-ivory shadow-[0_4px_16px_rgba(12,29,25,0.3)]"
          >
            <button
              type="button"
              onClick={() => {
                onDiscuss?.({
                  document_id: documentId,
                  page: marked.page,
                  quote: marked.quote,
                  title,
                });
                setMarked(null);
                window.getSelection()?.removeAllRanges();
              }}
              className="flex cursor-pointer items-center gap-1.5 px-3.5 py-2 hover:bg-white/10"
            >
              <TextQuote className="size-3.5" /> Ask AI
            </button>
            <button
              type="button"
              onClick={() => {
                setAnnotating(marked);
                setMarked(null);
              }}
              className="flex cursor-pointer items-center gap-1.5 border-l border-white/20 px-3.5 py-2 hover:bg-white/10"
            >
              <StickyNote className="size-3.5" /> Add note
            </button>
          </div>
        )}
        {annotating && (
          <div
            style={{ top: annotating.top, left: annotating.left }}
            className="absolute z-20 w-80 max-w-[calc(100%-1rem)] rounded-2xl border border-border bg-card p-3 shadow-xl"
          >
            <p className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-moss">
              Your note · page {annotating.page}
            </p>
            <p className="mt-1 line-clamp-2 text-[0.6875rem] italic text-muted-foreground">
              “{annotating.quote}”
            </p>
            <Textarea
              autoFocus
              value={noteDraft}
              onChange={(event) => setNoteDraft(event.target.value)}
              placeholder="Why does this matter? Add your interpretation…"
              className="mt-2 min-h-20 resize-none rounded-xl text-[0.75rem]"
            />
            <div className="mt-2 flex justify-end gap-1.5">
              <Button variant="ghost" size="sm" onClick={() => setAnnotating(null)}>
                Cancel
              </Button>
              <Button
                size="sm"
                disabled={saveAnnotation.isPending}
                onClick={() =>
                  saveAnnotation.mutate({
                    page: annotating.page,
                    quote: annotating.quote,
                    note: noteDraft,
                    source: "user",
                    color: "moss",
                  })
                }
              >
                {saveAnnotation.isPending ? <Loader2 className="size-3 animate-spin" /> : null}
                Save highlight
              </Button>
            </div>
          </div>
        )}
        {isLoading && (
          <div className="grid h-full place-items-center text-muted-foreground">
            <span className="flex items-center gap-2 text-[0.8125rem]">
              <Loader2 className="size-4 animate-spin" /> Loading the PDF…
            </span>
          </div>
        )}
        {isError && (
          <div className="grid h-full place-items-center">
            <span className="flex items-center gap-2 text-[0.8125rem] text-muted-foreground">
              <CircleAlert className="size-4" /> The file could not be loaded.
            </span>
          </div>
        )}
        {file && width > 0 && (
          <Document
            file={file}
            onLoadSuccess={(pdf) => {
              setPageCount(pdf.numPages);
              pdfDocRef.current = pdf;
            }}
            loading={null}
            error={
              <p className="p-6 text-center text-[0.8125rem] text-muted-foreground">
                This file could not be rendered as a PDF.
              </p>
            }
            className="mx-auto flex w-fit flex-col gap-4"
          >
            {Array.from({ length: pageCount }, (_, index) => index + 1).map(
              (pageNumber) => (
                <div
                  key={pageNumber}
                  data-page={pageNumber}
                  ref={(node) => {
                    if (node) pageRefs.current.set(pageNumber, node);
                    else pageRefs.current.delete(pageNumber);
                  }}
                  className={cn("relative", captureMode && "cursor-crosshair select-none")}
                  onPointerDown={(event) => beginFigureCapture(event, pageNumber)}
                  onPointerMove={(event) => moveFigureCapture(event, pageNumber)}
                  onPointerUp={(event) => finishFigureCapture(event, pageNumber)}
                >
                  <Page
                    pageNumber={pageNumber}
                    width={effectiveWidth}
                    customTextRenderer={
                      translationLanguage ? undefined : renderers.get(pageNumber)
                    }
                    onGetTextSuccess={
                      translationLanguage ? undefined : handleTextSuccess(pageNumber)
                    }
                    renderAnnotationLayer={false}
                    className="overflow-hidden rounded-xl shadow-[0_2px_18px_rgba(12,29,25,0.10)] ring-1 ring-border"
                    loading={
                      <div
                        style={{
                          width: effectiveWidth,
                          height: Math.round(effectiveWidth * 1.35),
                        }}
                        className="rounded-xl bg-card"
                      />
                    }
                  />
                  {!translationLanguage
                    ? (annotations ?? [])
                    .filter(
                      (annotation) =>
                        annotation.page === pageNumber && !annotation.quote.trim(),
                    )
                    .map((annotation, markerIndex) => {
                      const color =
                        COMMENT_COLORS.find((item) => item.id === annotation.color) ??
                        COMMENT_COLORS[0];
                      return (
                        <button
                          key={annotation.id}
                          type="button"
                          title={annotation.note}
                          aria-label={`Open comment on page ${pageNumber}`}
                          onPointerDown={(event) => event.stopPropagation()}
                          onClick={(event) => {
                            event.stopPropagation();
                            setCommentsOpen(true);
                          }}
                          className={cn(
                            "absolute left-2 z-10 grid size-7 cursor-pointer place-items-center rounded-full border-2 border-card text-white shadow-md transition-transform hover:scale-110",
                            color.className,
                          )}
                          style={{ top: 12 + markerIndex * 32 }}
                        >
                          <MessageSquareText className="size-3.5" />
                        </button>
                      );
                    })
                    : null}
                  {figureSelection?.page === pageNumber && (
                    <div
                      className="pointer-events-none absolute border-2 border-moss bg-moss-surface/10 shadow-[0_0_0_9999px_rgba(12,29,25,0.22)]"
                      style={{
                        left: Math.min(figureSelection.startX, figureSelection.endX),
                        top: Math.min(figureSelection.startY, figureSelection.endY),
                        width: Math.abs(figureSelection.endX - figureSelection.startX),
                        height: Math.abs(figureSelection.endY - figureSelection.startY),
                      }}
                    />
                  )}
                  <span className="pointer-events-none absolute bottom-1.5 right-2.5 rounded-full bg-pine/70 px-1.5 py-0.5 font-mono text-[0.5625rem] text-ivory/90">
                    {pageNumber}
                  </span>
                </div>
              ),
            )}
          </Document>
        )}
      </div>
      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
      >
        <AlertDialogContent size="sm">
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this comment?</AlertDialogTitle>
            <AlertDialogDescription>
              The saved comment or highlighted note on page {deleteTarget?.page} will be
              removed.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep comment</AlertDialogCancel>
            <AlertDialogAction
              disabled={deleteAnnotation.isPending}
              onClick={() => {
                if (deleteTarget) deleteAnnotation.mutate(deleteTarget.id);
              }}
              variant="destructive"
            >
              {deleteAnnotation.isPending ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : null}
              Delete comment
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
