(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.SixSentencesCaptureResult = api;
})(typeof globalThis === "object" ? globalThis : this, function () {
  "use strict";

  const OUTCOMES = new Set([
    "created",
    "already_saved",
    "metadata_enriched",
    "pdf_attached",
  ]);
  const WARNING_CODES = new Set([
    "citation_only",
    "different_pdf_same_identity",
    "conflicting_doi_for_identical_pdf",
  ]);
  const WARNING_ALIASES = new Map([
    ["Saved as a paper citation without a PDF attachment.", "citation_only"],
  ]);

  const cleanFields = (value) => Array.isArray(value)
    ? [...new Set(value.filter((field) => typeof field === "string").map((field) => field.trim()).filter(Boolean))]
    : [];

  const fieldLabel = (field) => ({
    title: "title",
    authors: "authors",
    doi: "DOI",
    published_at: "publication date",
    description: "abstract or description",
    source_url: "source URL",
    canonical_url: "canonical URL",
    selected_excerpt: "selected passage",
    container_title: "journal or publication",
    volume: "volume",
    issue: "issue",
    pages: "pages",
    publisher: "publisher",
    language: "language",
    license: "license",
    isbn: "ISBN",
    issn: "ISSN",
    arxiv_id: "arXiv ID",
    keywords: "keywords",
    item_type: "source type",
    subtitle: "subtitle",
    short_title: "short title",
    series_title: "series",
    series_number: "series number",
    edition: "edition",
    publisher_place: "publisher place",
    accessed_at: "access date",
    archive: "archive",
    archive_location: "archive location",
    citation_key: "citation key",
    format: "format",
    call_number: "call number",
    pmid: "PMID",
    pmcid: "PMCID",
    extra: "extra details",
  }[field] || (field.startsWith("provenance.") ? "capture provenance" : field.replaceAll("_", " ")));

  function normalizeCaptureResult(result) {
    const rawStatus = typeof result?.status === "string" ? result.status : "";
    const status = OUTCOMES.has(rawStatus)
      ? rawStatus
      : rawStatus === "duplicate"
        ? "already_saved"
        : null;
    if (!status) throw new Error("SixSentences returned an unknown save result. Check your Library before retrying.");
    const rawWarnings = Array.isArray(result?.warnings)
      ? result.warnings.filter((warning) => typeof warning === "string")
      : [];
    const normalizedWarnings = rawWarnings.map((warning) => WARNING_ALIASES.get(warning) || warning);
    return {
      status,
      metadataFieldsAdded: cleanFields(result?.metadata_fields_added),
      itemId: result?.item?.id ?? null,
      itemTitle: typeof result?.item?.title === "string" ? result.item.title.trim() : "",
      hasFile: Boolean(result?.has_file),
      warningCodes: [...new Set(normalizedWarnings.filter((warning) => WARNING_CODES.has(warning)))],
      hasUnknownWarnings: normalizedWarnings.some((warning) => !WARNING_CODES.has(warning)),
    };
  }

  function warningNotes(normalized) {
    const notes = [];
    if (normalized.warningCodes.includes("different_pdf_same_identity")) {
      notes.push("The existing PDF was kept; this different attachment was not stored.");
    }
    if (normalized.warningCodes.includes("conflicting_doi_for_identical_pdf")) {
      notes.push("The existing DOI was kept because these PDF bytes were already saved with a different DOI.");
    }
    if (normalized.hasUnknownWarnings) {
      notes.push("The save completed with an additional notice. Review the existing Library entry before retrying.");
    }
    return notes;
  }

  function captureResultCopy(result, fallbackKind, context = {}) {
    const normalized = normalizeCaptureResult(result);
    const isWeb = fallbackKind === "web";
    const isDocument = fallbackKind === "document";
    let copy;
    switch (normalized.status) {
      case "already_saved":
        copy = {
          tone: "neutral",
          symbol: "=",
          eyebrow: "Already in your Library",
          title: isWeb ? "Web source already saved" : isDocument ? "PDF already saved" : "Paper already saved",
          detail: "Nothing new was created or changed.",
        };
        break;
      case "metadata_enriched":
        copy = {
          tone: "updated",
          symbol: "+",
          eyebrow: "Library updated",
          title: isWeb ? "Web source details updated" : isDocument ? "PDF details updated" : "Paper details updated",
          detail: normalized.metadataFieldsAdded.length
            ? `Added ${normalized.metadataFieldsAdded.map(fieldLabel).join(", ")} to the existing entry.`
            : "Added new source details to the existing Library entry.",
        };
        break;
      case "pdf_attached":
        copy = {
          tone: "updated",
          symbol: "+",
          eyebrow: "Library updated",
          title: "PDF attached to saved paper",
          detail: "The existing citation now includes the confirmed PDF.",
        };
        break;
      case "created":
      default:
        copy = {
          tone: "created",
          symbol: "✓",
          eyebrow: "Saved to your Library",
          title: isWeb ? "Web source saved" : isDocument ? "PDF saved" : "Paper saved",
          detail: isWeb
            ? "Metadata and provenance are now available under Web sources."
            : isDocument
              ? "The PDF and its available document details are now in your Library."
            : normalized.hasFile || context.pdfRequested
              ? "The paper and its PDF are now available under Papers."
              : "The citation is now under Papers. No PDF attachment was claimed.",
        };
        break;
    }
    const notes = warningNotes(normalized);
    return {
      ...copy,
      detail: notes.length ? `${copy.detail} ${notes.join(" ")}` : copy.detail,
    };
  }

  return { normalizeCaptureResult, captureResultCopy };
});
