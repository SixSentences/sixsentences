import type { WriterComment } from "@/lib/types";

export type PdfCommentMarks = Map<number, Map<number, number>>;

export interface PdfCommentResolution {
  marks: PdfCommentMarks;
  pagesByComment: Map<number, number>;
}

interface PageIndex {
  joined: string;
  spans: Array<[number, number]>;
  normalizedItems: string[];
}

interface Match {
  page: number;
  start: number;
  end: number;
  score: number;
}

export function normalizePdfText(text: string): string {
  return text
    .normalize("NFKC")
    .replace(/[‘’]/g, "'")
    .replace(/[“”]/g, '"')
    .replace(/[–—­-]/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

export function escapePdfText(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function indexPage(values: string[]): PageIndex {
  const normalizedItems = values.map(normalizePdfText);
  let joined = "";
  const spans: Array<[number, number]> = [];
  for (const value of normalizedItems) {
    if (!value) {
      spans.push([joined.length, joined.length]);
      continue;
    }
    const start = joined.length + (joined ? 1 : 0);
    joined += `${joined ? " " : ""}${value}`;
    spans.push([start, start + value.length]);
  }
  return { joined, spans, normalizedItems };
}

function matchesOnPage(
  page: number,
  index: PageIndex,
  comment: WriterComment,
): Match[] {
  const quote = normalizePdfText(comment.quote);
  if (!quote) return [];
  const prefix = normalizePdfText(comment.anchor_prefix ?? "");
  const suffix = normalizePdfText(comment.anchor_suffix ?? "");
  const matches: Match[] = [];
  let cursor = index.joined.indexOf(quote);
  while (cursor >= 0 && matches.length < 20) {
    const before = index.joined.slice(Math.max(0, cursor - prefix.length), cursor);
    const after = index.joined.slice(
      cursor + quote.length,
      cursor + quote.length + suffix.length,
    );
    let score = comment.page === page ? 4 : 0;
    if (prefix && before.endsWith(prefix)) score += prefix.length;
    if (suffix && after.startsWith(suffix)) score += suffix.length;
    matches.push({
      page,
      start: cursor,
      end: cursor + quote.length,
      score,
    });
    cursor = index.joined.indexOf(quote, cursor + Math.max(1, quote.length));
  }
  return matches;
}

/**
 * Resolve every open text-anchored comment to one position in the document.
 *
 * Page numbers are only a navigation hint. The quote plus its surrounding
 * prefix/suffix wins when LaTeX reflow moves a paragraph to another page.
 */
export function resolvePdfComments(
  pages: Map<number, string[]>,
  comments: WriterComment[],
): PdfCommentResolution {
  const indexedPages = new Map<number, PageIndex>();
  for (const [page, values] of pages) indexedPages.set(page, indexPage(values));

  const marks: PdfCommentMarks = new Map();
  const pagesByComment = new Map<number, number>();
  for (const comment of comments) {
    if (comment.status !== "open" || !comment.quote) continue;
    let best: Match | null = null;
    for (const [page, index] of indexedPages) {
      for (const candidate of matchesOnPage(page, index, comment)) {
        if (
          best === null
          || candidate.score > best.score
          || (
            candidate.score === best.score
            && candidate.page === comment.page
            && best.page !== comment.page
          )
        ) {
          best = candidate;
        }
      }
    }
    if (best === null) continue;
    pagesByComment.set(comment.id, best.page);
    const index = indexedPages.get(best.page);
    if (!index) continue;
    const pageMarks = marks.get(best.page) ?? new Map<number, number>();
    index.spans.forEach(([start, end], itemIndex) => {
      if (
        index.normalizedItems[itemIndex]
        && Math.min(end, best.end) - Math.max(start, best.start) > 0
        && !pageMarks.has(itemIndex)
      ) {
        pageMarks.set(itemIndex, comment.color_index % 6);
      }
    });
    if (pageMarks.size > 0) marks.set(best.page, pageMarks);
  }
  return { marks, pagesByComment };
}

export function pdfSelectionContext(
  values: string[] | undefined,
  quote: string,
  radius = 120,
): { prefix: string; suffix: string } {
  const pageText = (values ?? [])
    .map(normalizePdfText)
    .filter(Boolean)
    .join(" ");
  const normalizedQuote = normalizePdfText(quote);
  const quoteAt = pageText.indexOf(normalizedQuote);
  if (quoteAt < 0) return { prefix: "", suffix: "" };
  return {
    prefix: pageText.slice(Math.max(0, quoteAt - radius), quoteAt),
    suffix: pageText.slice(
      quoteAt + normalizedQuote.length,
      quoteAt + normalizedQuote.length + radius,
    ),
  };
}
