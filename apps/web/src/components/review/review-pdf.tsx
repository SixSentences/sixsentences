"use client";

/**
 * The reviewer-facing PDF reader: continuous vertical scroll of every page
 * (no page flip), reviewer comments with a quote light up as moss marks in
 * the pdf.js text layer, and marking a passage pops a "Comment" pill that
 * opens the small composer. Highlight matching mirrors paper-panel.tsx:
 * the page's text items are joined, the NFKC-normalised quote located in
 * the joined string, and every item overlapping the quote's span is marked.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { MessageSquarePlus, ZoomIn, ZoomOut } from "lucide-react";
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

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

export default function ReviewPdf({
  file,
  comments,
  focusPage,
  name,
  onNameChange,
  onComment,
  submitting,
  revision,
}: {
  file: { data: Uint8Array };
  comments: WriterComment[];
  /** Clicking a comment in the rail asks the reader to scroll to its page. */
  focusPage: { page: number; commentId?: number; key: number } | null;
  name: string;
  onNameChange: (value: string) => void;
  onComment: (input: ReviewCommentInput) => Promise<void>;
  submitting: boolean;
  /** Manuscript revision recorded when the PDF was compiled/shared. */
  revision: string;
}) {
  const [currentPage, setCurrentPage] = useState(1);
  // the window-level mouseup listener reads the page without re-binding
  const currentPageRef = useRef(1);
  currentPageRef.current = currentPage;
  const [pageCount, setPageCount] = useState(0);
  const [width, setWidth] = useState(0);
  // fit-width is often too close for reading; start calmer and let the
  // reviewer zoom between 40% and 200%
  const [zoom, setZoom] = useState(0.75);
  const pageWidth = Math.round(width * zoom);
  const bodyRef = useRef<HTMLDivElement>(null);
  const pageRefs = useRef(new Map<number, HTMLDivElement>());
  // a marked passage pops the "Comment" pill right above the selection
  const [marked, setMarked] = useState<{
    top: number;
    left: number;
    page: number;
    quote: string;
    anchorPrefix: string;
    anchorSuffix: string;
  } | null>(null);
  const [composing, setComposing] = useState<{
    top: number;
    left: number;
    page: number;
    quote: string;
    anchorPrefix: string;
    anchorSuffix: string;
  } | null>(null);

  const goToPage = useCallback((page: number, smooth = true) => {
    pageRefs.current.get(page)?.scrollIntoView({
      behavior: smooth ? "smooth" : "auto",
      block: "start",
    });
  }, []);

  useEffect(() => {
    const node = bodyRef.current;
    if (!node) return;
    const observer = new ResizeObserver(() => {
      setWidth(Math.max(280, node.clientWidth - 32));
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  // the page counter follows the scroll position (fallback anchor for a
  // selection whose endpoints both sit outside the text layer)
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

  // the rail asks for a page; placeholders keep heights stable so the jump
  // lands even while later pages are still rendering
  useEffect(() => {
    if (!focusPage) return;
    const resolvedPage =
      (focusPage.commentId
        ? resolvedPages.current.get(focusPage.commentId)
        : undefined) ?? focusPage.page;
    const frame = requestAnimationFrame(() => goToPage(resolvedPage));
    return () => cancelAnimationFrame(frame);
  }, [focusPage, goToPage]);

  const markedItems = useRef(new Map<number, Map<number, number>>());
  const resolvedPages = useRef(new Map<number, number>());
  const pageItems = useRef(new Map<number, string[]>());
  const [markVersion, setMarkVersion] = useState(0);

  const recomputeMarks = useCallback(() => {
    const resolution = resolvePdfComments(pageItems.current, comments);
    markedItems.current = resolution.marks;
    resolvedPages.current = resolution.pagesByComment;
    setMarkVersion((value) => value + 1);
  }, [comments]);

  useEffect(() => {
    recomputeMarks();
  }, [recomputeMarks]);

  const handleTextSuccess = useCallback(
    (page: number) => (textContent: { items: unknown[] }) => {
      const rawStrs = textContent.items.map(
        (item) => (item as { str?: string }).str ?? "",
      );
      const previous = pageItems.current.get(page);
      if (
        previous
        && previous.length === rawStrs.length
        && previous.every((value, index) => value === rawStrs[index])
      ) {
        return;
      }
      pageItems.current.set(page, rawStrs);
      recomputeMarks();
    },
    [recomputeMarks],
  );

  // per-page text renderer: wraps overlapping items in a moss mark
  const renderers = useMemo(() => {
    void markVersion; // new renderer identities force the text layer to redraw
    const map = new Map<
      number,
      (item: { str: string; itemIndex: number }) => string
    >();
    for (let page = 1; page <= pageCount; page += 1) {
      map.set(page, ({ str, itemIndex }) => {
        const color = markedItems.current.get(page)?.get(itemIndex);
        if (color !== undefined) {
          return `<mark class="paper-hl paper-hl-review-${color}">${escapePdfText(str)}</mark>`;
        }
        return escapePdfText(str);
      });
    }
    return map;
  }, [pageCount, markVersion]);

  // the comment pill listens on window: releasing the mouse outside the
  // scroll container (common after a long multi-line or cross-column drag)
  // must still pop it
  useEffect(() => {
    function onMouseUp() {
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
      // a huge cross-column grab is trimmed, never silently dropped
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
      const context = pdfSelectionContext(pageItems.current.get(page), quote);
      setMarked({
        top: Math.max(4, rect.top - hostRect.top + host.scrollTop - 42),
        left: Math.min(
          Math.max(8, rect.left - hostRect.left + rect.width / 2 - 92),
          Math.max(8, host.clientWidth - 200),
        ),
        page,
        quote,
        anchorPrefix: context.prefix,
        anchorSuffix: context.suffix,
      });
    }
    window.addEventListener("mouseup", onMouseUp);
    return () => window.removeEventListener("mouseup", onMouseUp);
  }, []);

  return (
    <div
      ref={bodyRef}
      className="relative h-full min-h-0 overflow-y-auto bg-secondary/40 p-4"
    >
      {marked && (
        <div
          style={{ top: marked.top, left: marked.left }}
          onMouseDown={(event) => event.preventDefault()} // keep the selection
          className="absolute z-10 flex overflow-hidden rounded-full bg-pine text-[0.75rem] font-medium text-ivory shadow-[0_4px_16px_rgba(12,29,25,0.3)]"
        >
          <button
            type="button"
            onClick={() => {
              setComposing(marked);
              setMarked(null);
            }}
            className="flex cursor-pointer items-center gap-1.5 px-3.5 py-2 hover:bg-white/10"
          >
            <MessageSquarePlus className="size-3.5" /> Comment
          </button>
        </div>
      )}
      {composing && (
        <div
          style={{ top: composing.top, left: composing.left }}
          className="absolute z-20 w-80 max-w-[calc(100%-1rem)] rounded-2xl border border-border bg-card p-3 shadow-xl"
        >
          <CommentForm
            name={name}
            onNameChange={onNameChange}
            quote={composing.quote}
            page={composing.page}
            anchorPrefix={composing.anchorPrefix}
            anchorSuffix={composing.anchorSuffix}
            anchorRevision={revision}
            submitting={submitting}
            onCancel={() => {
              setComposing(null);
              window.getSelection()?.removeAllRanges();
            }}
            onSubmit={async (input) => {
              await onComment(input);
              setComposing(null);
              window.getSelection()?.removeAllRanges();
            }}
          />
        </div>
      )}
      {width > 0 && (
        <div className="sticky top-2 z-30 mx-auto mb-2 flex w-fit items-center gap-0.5 rounded-full border border-border bg-card/95 px-1.5 py-1 shadow-sm backdrop-blur">
          <button
            type="button"
            aria-label="Zoom out"
            disabled={zoom <= 0.4}
            onClick={() => setZoom((value) => Math.max(0.4, Math.round((value - 0.1) * 10) / 10))}
            className="grid size-7 cursor-pointer place-items-center rounded-full text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground disabled:opacity-40"
          >
            <ZoomOut className="size-3.5" />
          </button>
          <button
            type="button"
            aria-label="Reset zoom"
            title="Reset zoom"
            onClick={() => setZoom(0.75)}
            className="min-w-11 cursor-pointer rounded-full px-1 py-1 text-center font-mono text-[0.65625rem] text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
          >
            {Math.round(zoom * 100)}%
          </button>
          <button
            type="button"
            aria-label="Zoom in"
            disabled={zoom >= 2}
            onClick={() => setZoom((value) => Math.min(2, Math.round((value + 0.1) * 10) / 10))}
            className="grid size-7 cursor-pointer place-items-center rounded-full text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground disabled:opacity-40"
          >
            <ZoomIn className="size-3.5" />
          </button>
        </div>
      )}
      {width > 0 && (
        <Document
          file={file}
          onLoadSuccess={({ numPages }) => setPageCount(numPages)}
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
                className="relative"
              >
                <Page
                  pageNumber={pageNumber}
                  width={pageWidth}
                  customTextRenderer={renderers.get(pageNumber)}
                  onGetTextSuccess={handleTextSuccess(pageNumber)}
                  renderAnnotationLayer={false}
                  className="overflow-hidden rounded-xl shadow-[0_2px_18px_rgba(12,29,25,0.10)] ring-1 ring-border"
                  loading={
                    <div
                      style={{
                        width: pageWidth,
                        height: Math.round(pageWidth * 1.35),
                      }}
                      className="rounded-xl bg-card"
                    />
                  }
                />
                <span className="pointer-events-none absolute bottom-1.5 right-2.5 rounded-full bg-pine/70 px-1.5 py-0.5 font-mono text-[0.5625rem] text-ivory/90">
                  {pageNumber}
                </span>
              </div>
            ),
          )}
        </Document>
      )}
    </div>
  );
}
