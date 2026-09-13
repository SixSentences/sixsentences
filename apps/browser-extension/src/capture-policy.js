(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.SixSentencesCapturePolicy = api;
})(typeof globalThis === "object" ? globalThis : this, function () {
  "use strict";

  const publicHttps = (value) => {
    try { return new URL(value).protocol === "https:"; } catch { return false; }
  };

  function captureRoute(capture) {
    const activeUrl = String(capture?.url || "");
    const pdfUrl = String(capture?.pdfUrl || "");
    const detectedPdf = Boolean(pdfUrl && publicHttps(pdfUrl));
    let directPdf = false;
    let sameOriginPdf = false;
    if (detectedPdf) {
      try {
        directPdf = new URL(pdfUrl).href === new URL(activeUrl).href;
        sameOriginPdf = new URL(pdfUrl).origin === new URL(activeUrl).origin;
      } catch { /* Invalid candidates remain unavailable. */ }
    }
    const scholarlyPdf = capture?.sourceKind === "paper" && (directPdf || sameOriginPdf);
    const explicitDocumentPdf = capture?.sourceKind === "document" && directPdf;
    const uploadPdf = detectedPdf && (scholarlyPdf || explicitDocumentPdf);
    return {
      route: explicitDocumentPdf ? "document" : scholarlyPdf ? "paper" : capture?.sourceKind === "paper" ? "citation" : "web",
      uploadPdf,
      detectedPdf,
      unattachedPdf: detectedPdf && !uploadPdf,
    };
  }

  return { captureRoute };
});
