const FALLBACK_PDF_NAME = "paper";
const MAX_FILENAME_STEM_CODEPOINTS = 96;
const MAX_QUALIFIER_CODEPOINTS = 24;

function takeCodepoints(value: string, limit: number): string {
  return Array.from(value).slice(0, limit).join("").replace(/-+$/g, "");
}

/**
 * Turn user- or provider-supplied paper titles into a local-only filename.
 * The result cannot contain path separators, control characters or dot paths.
 */
export function paperPdfFilename(
  title: string | null | undefined,
  qualifier?: string | null,
): string {
  const safePart = (value: string | null | undefined): string => {
    const normalized = (value ?? "")
      .normalize("NFKC")
      .replace(/[^\p{L}\p{N}]+/gu, "-")
      .replace(/^-+|-+$/g, "");
    return normalized;
  };

  let stem = safePart(title) || FALLBACK_PDF_NAME;
  // Avoid Windows device names while keeping the paper title recognizable.
  if (/^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])$/i.test(stem)) {
    stem = `${FALLBACK_PDF_NAME}-${stem}`;
  }
  const safeQualifier = takeCodepoints(
    safePart(qualifier),
    MAX_QUALIFIER_CODEPOINTS,
  );
  const qualifierLength = Array.from(safeQualifier).length;
  stem = takeCodepoints(
    stem,
    Math.max(1, MAX_FILENAME_STEM_CODEPOINTS - qualifierLength - (safeQualifier ? 1 : 0)),
  );
  return `${stem}${safeQualifier ? `-${safeQualifier}` : ""}.pdf`;
}

/** Start a browser download and release the temporary object URL immediately. */
export function downloadPdfBytes(bytes: ArrayBuffer, filename: string): void {
  if (bytes.byteLength === 0) throw new Error("The PDF is empty.");
  const url = URL.createObjectURL(
    new Blob([bytes], { type: "application/pdf" }),
  );
  const anchor = document.createElement("a");
  try {
    anchor.href = url;
    anchor.download = filename;
    anchor.hidden = true;
    document.body.appendChild(anchor);
    anchor.click();
  } finally {
    anchor.remove();
    URL.revokeObjectURL(url);
  }
}
