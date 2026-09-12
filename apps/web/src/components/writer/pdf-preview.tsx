"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Crosshair,
  MessageSquarePlus,
  MessageSquareText,
  Minus,
  Plus,
} from "lucide-react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/TextLayer.css";

import CommentForm, {
  type ReviewCommentInput,
} from "@/components/review/comment-form";
import {
  escapePdfText,
  pdfSelectionContext,
  resolvePdfComments,
} from "@/lib/pdf-comment-anchors";
import type { WriterComment } from "@/lib/types";
import {
  buildWriterPdfSelection,
  WRITER_SELECTION_MIN_CHARACTERS,
  type WriterPdfSelectionSegment,
} from "@/lib/writer-selection";

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

const WRITER_COMMENT_MAX_CHARACTERS = 4_000;

type PdfDiscussSelection = {
  quote: string;
  page: number;
  page_end?: number;
  segments?: WriterPdfSelectionSegment[];
  /** PDF-space anchor used for a best-effort SyncTeX source lookup. */
  x?: number;
  y?: number;
};

function writerPageElement(node: Node | null): HTMLElement | null {
  const element = node instanceof Element ? node : node?.parentElement;
  return element?.closest<HTMLElement>("[data-writer-page]") ?? null;
}

function writerPageNumber(node: Node | null): number | null {
  const value = writerPageElement(node)?.dataset.writerPage;
  const page = value ? Number(value) : Number.NaN;
  return Number.isInteger(page) && page > 0 ? page : null;
}

function rangeEdgeRect(range: Range, edge: "start" | "end"): DOMRect {
  const rects = Array.from(range.getClientRects()).filter(
    (rect) => rect.width > 0 || rect.height > 0,
  );
  return (
    (edge === "start" ? rects[0] : rects.at(-1))
    ?? range.getBoundingClientRect()
  );
}

/** The Writer's compiled-PDF view, in the same quiet reader style as the
 * app's paper reader: continuous pages on a soft backdrop, no browser
 * chrome. Mark any passage and a discuss pill appears, exactly like the
 * paper reader — the assistant then traces it back to the LaTeX source. */
export default function WriterPdfPreview({
  url,
  onDiscuss,
  onNavigateSource,
  focus,
  comments = [],
  commentsVisible = false,
  onCommentsVisibleChange,
  onComment,
  commentSubmitting = false,
  commentFocusPage,
  commentRevision = "",
}: {
  url: string;
  onDiscuss?: (selection: PdfDiscussSelection) => void | Promise<void>;
  onNavigateSource?: (position: { page: number; x: number; y: number }) => void;
  focus?: {
    page: number;
    x: number;
    y: number;
    width: number;
    height: number;
  } | null;
  comments?: WriterComment[];
  commentsVisible?: boolean;
  onCommentsVisibleChange?: (visible: boolean) => void;
  onComment?: (input: ReviewCommentInput) => Promise<void>;
  commentSubmitting?: boolean;
  commentFocusPage?: { page: number; commentId?: number; key: number } | null;
  commentRevision?: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [containerWidth, setContainerWidth] = useState(0);
  const [pageCount, setPageCount] = useState(0);
  const [zoom, setZoom] = useState(1);
  const [pageSizes, setPageSizes] = useState<Record<number, { width: number; height: number }>>({});
  const pageRefs = useRef<Record<number, HTMLDivElement | null>>({});
  const [pill, setPill] = useState<{
    x: number;
    y: number;
    quote: string;
    page: number;
    pageEnd?: number;
    segments?: WriterPdfSelectionSegment[];
    sourceX?: number;
    sourceY?: number;
    anchorPrefix: string;
    anchorSuffix: string;
    error: string | null;
  } | null>(null);
  const [discussing, setDiscussing] = useState(false);
  const [commenting, setCommenting] = useState<typeof pill>(null);
  const pageItems = useRef(new Map<number, string[]>());
  const markedItems = useRef(new Map<number, Map<number, number>>());
  const resolvedPages = useRef(new Map<number, number>());
  const [markVersion, setMarkVersion] = useState(0);

  const recomputeMarks = useCallback(() => {
    const resolution = resolvePdfComments(
      pageItems.current,
      commentsVisible ? comments : [],
    );
    markedItems.current = resolution.marks;
    resolvedPages.current = resolution.pagesByComment;
    setMarkVersion((value) => value + 1);
  }, [comments, commentsVisible]);

  useEffect(() => {
    recomputeMarks();
  }, [recomputeMarks]);

  const renderers = useMemo(() => {
    void markVersion;
    const result = new Map<
      number,
      (item: { str: string; itemIndex: number }) => string
    >();
    for (let page = 1; page <= pageCount; page += 1) {
      result.set(page, ({ str, itemIndex }) => {
        const color = markedItems.current.get(page)?.get(itemIndex);
        return color === undefined
          ? escapePdfText(str)
          : `<mark class="paper-hl paper-hl-review-${color}">${escapePdfText(str)}</mark>`;
      });
    }
    return result;
  }, [pageCount, markVersion]);

  const handleMouseUp = () => {
    if (!onDiscuss && !onComment) return;
    const selection = window.getSelection();
    if (
      !selection
      || selection.isCollapsed
      || selection.toString().trim().length < WRITER_SELECTION_MIN_CHARACTERS
    ) {
      setPill(null);
      return;
    }
    const ranges = Array.from({ length: selection.rangeCount }, (_, index) =>
      selection.getRangeAt(index),
    );
    const rawSegments = ranges.flatMap((range) => {
      const quote = range.toString().trim();
      const startPage = writerPageNumber(range.startContainer);
      const endPage = writerPageNumber(range.endContainer);
      const page = startPage ?? endPage;
      if (!quote || page === null) return [];
      return [{
        quote,
        page,
        ...(endPage && endPage !== page ? { page_end: endPage } : {}),
      }];
    });
    const built = buildWriterPdfSelection(rawSegments);
    const firstRange = ranges[0];
    const lastRange = ranges.at(-1);
    const firstPage = firstRange
      ? writerPageElement(firstRange.startContainer)
      : null;
    if (!firstRange || !lastRange || !firstPage || rawSegments.length === 0) {
      setPill(null);
      return;
    }
    const rect = rangeEdgeRect(lastRange, "end");
    const sourceRect = rangeEdgeRect(firstRange, "start");
    const host = containerRef.current?.getBoundingClientRect();
    if (!host) return;
    const page = rawSegments[0].page;
    const size = pageSizes[page];
    const pageRect = firstPage.getBoundingClientRect();
    const sourceX = size && pageRect.width > 0
      ? Math.max(0, Math.min(size.width, ((sourceRect.left - pageRect.left) / pageRect.width) * size.width))
      : undefined;
    const sourceY = size && pageRect.height > 0
      ? Math.max(0, Math.min(size.height, ((sourceRect.top - pageRect.top) / pageRect.height) * size.height))
      : undefined;
    const context = pdfSelectionContext(
      pageItems.current.get(page),
      rawSegments[0].quote,
    );
    const pdfSelection = built.selection?.kind === "pdf" ? built.selection : null;
    setPill({
      x: rect.left - host.left + rect.width / 2,
      y: rect.top - host.top - 10 + (containerRef.current?.scrollTop ?? 0),
      quote: pdfSelection?.quote ?? rawSegments.map((segment) => segment.quote).join("\n\n"),
      page,
      ...(pdfSelection?.page_end ? { pageEnd: pdfSelection.page_end } : {}),
      ...(pdfSelection?.segments ? { segments: pdfSelection.segments } : {}),
      ...(sourceX !== undefined ? { sourceX } : {}),
      ...(sourceY !== undefined ? { sourceY } : {}),
      anchorPrefix: context.prefix,
      anchorSuffix: context.suffix,
      error: built.error,
    });
  };

  useEffect(() => {
    const node = containerRef.current;
    if (!node) return;
    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width ?? 0;
      if (width > 0) setContainerWidth(width);
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!focus) return;
    pageRefs.current[focus.page]?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [focus]);

  useEffect(() => {
    if (!commentFocusPage) return;
    const page =
      (commentFocusPage.commentId
        ? resolvedPages.current.get(commentFocusPage.commentId)
        : undefined) ?? commentFocusPage.page;
    pageRefs.current[page]?.scrollIntoView({
      behavior: "smooth",
      block: "center",
    });
  }, [commentFocusPage]);

  const pageWidth = Math.max(
    280,
    Math.min((containerWidth - 48) * zoom, 1400),
  );

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center justify-between border-b border-border/60 px-3 py-1.5">
        <span className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-muted-foreground">
          {pageCount > 0 ? `${pageCount} page${pageCount === 1 ? "" : "s"}` : "pdf"}
        </span>
        {onNavigateSource ? (
          <span className="hidden items-center gap-1 font-mono text-[0.5625rem] text-muted-foreground sm:flex">
            <Crosshair className="size-3" /> Double-click to source
          </span>
        ) : null}
        {onCommentsVisibleChange ? (
          <button
            type="button"
            aria-pressed={commentsVisible}
            onClick={() => onCommentsVisibleChange(!commentsVisible)}
            className={`inline-flex h-7 cursor-pointer items-center gap-1.5 rounded-full px-2.5 text-[0.65625rem] transition-colors ${
              commentsVisible
                ? "bg-accent text-moss"
                : "text-muted-foreground hover:bg-secondary hover:text-foreground"
            }`}
          >
            <MessageSquareText className="size-3.5" />
            {comments.filter((comment) => comment.status === "open").length} comments
          </button>
        ) : null}
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => setZoom((z) => Math.max(0.6, Math.round((z - 0.1) * 10) / 10))}
            className="grid size-6 cursor-pointer place-items-center rounded-md text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
            aria-label="Zoom out"
          >
            <Minus className="size-3.5" />
          </button>
          <span className="w-10 text-center font-mono text-[0.625rem] tabular-nums text-muted-foreground">
            {Math.round(zoom * 100)}%
          </span>
          <button
            type="button"
            onClick={() => setZoom((z) => Math.min(2, Math.round((z + 0.1) * 10) / 10))}
            className="grid size-6 cursor-pointer place-items-center rounded-md text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
            aria-label="Zoom in"
          >
            <Plus className="size-3.5" />
          </button>
        </div>
      </div>
      <div
        ref={containerRef}
        onMouseUp={handleMouseUp}
        className="relative min-h-0 flex-1 overflow-auto px-6 py-5"
      >
        {pill?.error ? (
          <div
            role="status"
            style={{ left: pill.x, top: pill.y }}
            className="absolute z-20 max-w-sm -translate-x-1/2 -translate-y-full rounded-xl border border-destructive/30 bg-card px-3 py-2 text-center text-[0.6875rem] leading-relaxed text-destructive shadow-lg"
          >
            {pill.error}
          </div>
        ) : pill ? (
          <div
            style={{ left: pill.x, top: pill.y }}
            className="absolute z-20 flex -translate-x-1/2 -translate-y-full overflow-hidden rounded-full bg-primary text-primary-foreground shadow-lg"
          >
            {onDiscuss ? (
              <button
                type="button"
                disabled={discussing}
                onMouseDown={(event) => event.preventDefault()}
                onClick={async () => {
                  setDiscussing(true);
                  try {
                    await onDiscuss({
                      quote: pill.quote,
                      page: pill.page,
                      ...(pill.pageEnd ? { page_end: pill.pageEnd } : {}),
                      ...(pill.segments ? { segments: pill.segments } : {}),
                      ...(pill.sourceX !== undefined ? { x: pill.sourceX } : {}),
                      ...(pill.sourceY !== undefined ? { y: pill.sourceY } : {}),
                    });
                    setPill(null);
                    window.getSelection()?.removeAllRanges();
                  } finally {
                    setDiscussing(false);
                  }
                }}
                className="cursor-pointer px-3 py-1.5 font-mono text-[0.625rem] uppercase tracking-[0.16em] hover:bg-white/10 disabled:cursor-wait disabled:opacity-60"
              >
                {discussing ? "Attaching\u2026" : "Discuss"}
              </button>
            ) : null}
            {onComment ? (
              <button
                type="button"
                disabled={
                  pill.quote.length > WRITER_COMMENT_MAX_CHARACTERS
                  || (pill.pageEnd !== undefined && pill.pageEnd !== pill.page)
                }
                title={
                  pill.pageEnd !== undefined && pill.pageEnd !== pill.page
                    ? "Comments must be anchored within one PDF page. Discuss supports the complete multi-page selection."
                    : pill.quote.length > WRITER_COMMENT_MAX_CHARACTERS
                    ? `Comments support at most ${WRITER_COMMENT_MAX_CHARACTERS.toLocaleString()} selected characters.`
                    : undefined
                }
                onClick={() => {
                  setCommenting(pill);
                  setPill(null);
                }}
                className="flex cursor-pointer items-center gap-1.5 border-l border-white/15 px-3 py-1.5 font-mono text-[0.625rem] uppercase tracking-[0.16em] hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <MessageSquarePlus className="size-3" /> Comment
              </button>
            ) : null}
          </div>
        ) : null}
        {commenting && onComment ? (
          <div
            style={{ left: commenting.x, top: commenting.y }}
            className="absolute z-30 w-80 max-w-[calc(100%-1rem)] -translate-x-1/2 rounded-2xl border border-border bg-card p-3 shadow-xl"
          >
            <CommentForm
              name="Author"
              onNameChange={() => {}}
              showName={false}
              quote={commenting.quote}
              page={commenting.page}
              anchorPrefix={commenting.anchorPrefix}
              anchorSuffix={commenting.anchorSuffix}
              anchorRevision={commentRevision}
              submitting={commentSubmitting}
              onCancel={() => setCommenting(null)}
              onSubmit={async (input) => {
                await onComment(input);
                setCommenting(null);
                window.getSelection()?.removeAllRanges();
              }}
            />
          </div>
        ) : null}
        {containerWidth > 0 ? (
          <Document
            file={url}
            onLoadSuccess={({ numPages }) => setPageCount(numPages)}
            loading={null}
            error={
              <p className="p-6 text-center text-[0.8125rem] text-muted-foreground">
                The compiled PDF could not be rendered.
              </p>
            }
            className="mx-auto flex w-fit flex-col gap-5"
          >
            {Array.from({ length: pageCount }, (_, index) => index + 1).map(
              (pageNumber) => (
                <div
                  key={pageNumber}
                  ref={(node) => {
                    pageRefs.current[pageNumber] = node;
                  }}
                  data-writer-page={pageNumber}
                  onDoubleClick={(event) => {
                    const size = pageSizes[pageNumber];
                    if (!size || !onNavigateSource) return;
                    const rect = event.currentTarget.getBoundingClientRect();
                    onNavigateSource({
                      page: pageNumber,
                      x: ((event.clientX - rect.left) / rect.width) * size.width,
                      y: ((event.clientY - rect.top) / rect.height) * size.height,
                    });
                  }}
                  className="relative overflow-hidden rounded-md shadow-[0_1px_10px_rgba(12,29,25,0.10)] ring-1 ring-border/60"
                >
                  <Page
                    pageNumber={pageNumber}
                    width={pageWidth}
                    renderAnnotationLayer={false}
                    customTextRenderer={renderers.get(pageNumber)}
                    onGetTextSuccess={(content) => {
                      const values = content.items.map(
                        (item) => (item as { str?: string }).str ?? "",
                      );
                      const previous = pageItems.current.get(pageNumber);
                      if (
                        previous
                        && previous.length === values.length
                        && previous.every((value, index) => value === values[index])
                      ) {
                        return;
                      }
                      pageItems.current.set(pageNumber, values);
                      recomputeMarks();
                    }}
                    onLoadSuccess={(page) => {
                      const [, , width, height] = page.view;
                      setPageSizes((current) => ({
                        ...current,
                        [pageNumber]: { width, height },
                      }));
                    }}
                  />
                  {focus?.page === pageNumber && pageSizes[pageNumber] ? (
                    <span
                      className="pointer-events-none absolute z-10 rounded-sm bg-moss-surface/10 ring-1 ring-moss/45 shadow-[0_0_0_2px_rgba(51,84,76,0.06)]"
                      style={{
                        left: `${Math.max(0, focus.x / pageSizes[pageNumber].width) * 100}%`,
                        top: `${Math.max(0, focus.y / pageSizes[pageNumber].height) * 100}%`,
                        width: `${Math.max(1.5, focus.width / pageSizes[pageNumber].width * 100)}%`,
                        height: `${Math.max(0.8, focus.height / pageSizes[pageNumber].height * 100)}%`,
                      }}
                    />
                  ) : null}
                </div>
              ),
            )}
          </Document>
        ) : null}
      </div>
    </div>
  );
}
