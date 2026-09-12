import type { LibraryDocument } from "@/lib/types";

const PDF_MEDIA_TYPES = new Set(["application/pdf", "application/x-pdf"]);

/** Match only an explicit PDF base media type, ignoring case and parameters. */
export function isPdfMimeType(value: string | null | undefined): boolean {
  const baseType = (value ?? "").split(";", 1)[0]?.trim().toLowerCase() ?? "";
  return PDF_MEDIA_TYPES.has(baseType);
}

/** A Library document is reader/download eligible only when its PDF is stored. */
export function isStoredLibraryPdf(
  document: Pick<LibraryDocument, "content_type" | "has_file">,
): boolean {
  return document.has_file && isPdfMimeType(document.content_type);
}
