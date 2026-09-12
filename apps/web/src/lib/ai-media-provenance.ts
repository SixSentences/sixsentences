/** Machine-readable provenance for exported reports, without private route data. */

export const MIXED_AI_SOURCE_TYPE =
  "http://cv.iptc.org/newscodes/digitalsourcetype/compositeSynthetic";

export function reportPdfProvenance(
  artifact: "research-report" | "interview-report",
  generatedParts: boolean | null,
  contributions: string,
) {
  return {
    creator: "SixSentences_",
    producer: "SixSentences_ report exporter",
    pdfVersion: "1.7" as const,
    keywords: [
      "sixsentences-ai-provenance-v1",
      `artifact=${artifact}`,
      `ai-generated-parts=${generatedParts === null ? "unknown" : String(generatedParts)}`,
      `contributions=${contributions}`,
      "scope=not-all-content-is-ai-generated",
      ...(generatedParts ? [`DigitalSourceType=${MIXED_AI_SOURCE_TYPE}`] : []),
    ].join("; "),
  };
}
