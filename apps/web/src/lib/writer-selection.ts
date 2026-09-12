export const WRITER_SELECTION_MIN_CHARACTERS = 8;
export const WRITER_SELECTION_MAX_CHARACTERS = 12_000;
export const WRITER_SELECTION_MAX_SEGMENTS = 32;
export const WRITER_SELECTION_MAX_PAGE = 2_000;
export const WRITER_SELECTION_MAX_PAGE_SPAN = 32;

export type WriterPdfSelectionSegment = {
  quote: string;
  page: number;
  page_end?: number;
};

export type WriterSelection =
  | {
      kind: "pdf";
      quote: string;
      page: number;
      page_end?: number;
      segments?: WriterPdfSelectionSegment[];
      pdf_x?: number;
      pdf_y?: number;
    }
  | {
      kind: "source";
      quote: string;
      line: number;
      line_end?: number;
      path: string;
    };

export type WriterSelectionResult =
  | { selection: WriterSelection; error: null }
  | { selection: null; error: string };

/**
 * Remove layout-only PDF copy artifacts while preserving ordinary in-line
 * hyphens. react-pdf exposes visual line wrapping through the browser
 * selection, so a word split at a line end otherwise cannot be matched back
 * to the LaTeX source.
 */
export function normalizeWriterPdfText(value: string): string {
  return value
    .replace(/\u00ad/g, "")
    .replace(/(\p{L})-[\t ]*(?:\r?\n)+[\t ]*(\p{L})/gu, "$1$2")
    .replace(/\s+/g, " ")
    .trim();
}

export function writerSelectionError(length: number): string | null {
  if (length < WRITER_SELECTION_MIN_CHARACTERS) return "Select a longer passage.";
  if (length > WRITER_SELECTION_MAX_CHARACTERS) {
    return `Selection has ${length.toLocaleString()} characters. Select at most ${WRITER_SELECTION_MAX_CHARACTERS.toLocaleString()} characters so the full passage can be sent.`;
  }
  return null;
}

export function writerSourceSelectionError(quote: string): string | null {
  if (quote.trim().length < WRITER_SELECTION_MIN_CHARACTERS) {
    return "Select a longer passage.";
  }
  return writerSelectionError(quote.length);
}

export function buildWriterPdfSelection(
  rawSegments: Array<{ quote: string; page: number; page_end?: number }>,
): WriterSelectionResult {
  if (rawSegments.length > WRITER_SELECTION_MAX_SEGMENTS) {
    return {
      selection: null,
      error: `A selection can contain at most ${WRITER_SELECTION_MAX_SEGMENTS} separate ranges. Select fewer passages.`,
    };
  }
  if (
    rawSegments.some((segment) => {
      const end = segment.page_end ?? segment.page;
      return !Number.isInteger(segment.page)
        || segment.page < 1
        || segment.page > WRITER_SELECTION_MAX_PAGE
        || !Number.isInteger(end)
        || end < segment.page
        || end > WRITER_SELECTION_MAX_PAGE
        || end - segment.page + 1 > WRITER_SELECTION_MAX_PAGE_SPAN;
    })
  ) {
    return {
      selection: null,
      error: `Select text from at most ${WRITER_SELECTION_MAX_PAGE_SPAN} consecutive PDF pages.`,
    };
  }
  const segments = rawSegments
    .map((segment) => ({
      // Keep the user's verbatim browser selection as provenance. The server
      // derives a normalized matching copy without rewriting this visible text.
      quote: segment.quote.trim(),
      page: segment.page,
      ...(segment.page_end && segment.page_end !== segment.page
        ? { page_end: segment.page_end }
        : {}),
    }))
    .filter((segment) => segment.quote.length > 0);
  if (segments.length === 0) {
    return { selection: null, error: "Select a longer passage." };
  }
  const quote = segments.map((segment) => segment.quote).join("\n\n");
  const error = writerSelectionError(quote.length);
  if (error) return { selection: null, error };

  const page = Math.min(...segments.map((segment) => segment.page));
  const pageEnd = Math.max(
    ...segments.map((segment) => segment.page_end ?? segment.page),
  );
  if (pageEnd - page + 1 > WRITER_SELECTION_MAX_PAGE_SPAN) {
    return {
      selection: null,
      error: `Select text from at most ${WRITER_SELECTION_MAX_PAGE_SPAN} consecutive PDF pages.`,
    };
  }
  return {
    selection: {
      kind: "pdf",
      quote,
      page,
      ...(pageEnd !== page ? { page_end: pageEnd } : {}),
      ...(segments.length > 1 ? { segments } : {}),
    },
    error: null,
  };
}

export function buildWriterSourceSelection(input: {
  quote: string;
  line: number;
  line_end?: number;
  path: string;
}): WriterSelectionResult {
  const error = writerSourceSelectionError(input.quote);
  if (error) return { selection: null, error };
  return {
    selection: {
      kind: "source",
      quote: input.quote,
      line: input.line,
      ...(input.line_end && input.line_end !== input.line
        ? { line_end: input.line_end }
        : {}),
      path: input.path,
    },
    error: null,
  };
}

export function writerPdfPageLabel(
  selection: Pick<Extract<WriterSelection, { kind: "pdf" }>, "page" | "page_end">,
): string {
  return selection.page_end && selection.page_end !== selection.page
    ? `pages ${selection.page}\u2013${selection.page_end}`
    : `page ${selection.page}`;
}
