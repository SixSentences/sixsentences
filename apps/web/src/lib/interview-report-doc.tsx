/**
 * The branded interview report PDF, rendered client-side with
 * @react-pdf/renderer in the same design language as the run report:
 * pine cover with the SixSentences_ wordmark in a built-in PDF serif, ivory
 * interior pages, moss accents, mono details. Every quote shown was
 * verified verbatim against the stored transcript server-side; quotes
 * that could not be located are flagged, never presented as evidence.
 *
 * Imported dynamically from the interview export menu so the renderer
 * never enters the main bundle.
 */

import {
  Document,
  Font,
  Page,
  StyleSheet,
  Text,
  View,
  pdf,
} from "@react-pdf/renderer";

import { formatClock } from "@/lib/format";
import { reportPdfProvenance } from "@/lib/ai-media-provenance";
import type { Interview, InterviewQuote } from "@/lib/types";

const PINE = "#0c1d19";
const IVORY = "#f4f1ea";
const MOSS = "#33544c";
const MOSS_SOFT = "#5a897d";
const INK = "#1d2a26";
const MUTED = "#5c6b66";
const LINE = "#d8d2c4";
const FLAG = "#a16207";

Font.registerHyphenationCallback((word) => [word]);

const styles = StyleSheet.create({
  cover: {
    backgroundColor: PINE,
    color: IVORY,
    padding: 56,
    display: "flex",
    flexDirection: "column",
  },
  page: {
    backgroundColor: IVORY,
    color: INK,
    paddingTop: 64,
    paddingBottom: 64,
    paddingHorizontal: 56,
    fontFamily: "Helvetica",
    fontSize: 9.5,
    lineHeight: 1.55,
  },
  pageHeader: {
    position: "absolute",
    top: 24,
    left: 56,
    right: 56,
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "baseline",
  },
  pageFooter: {
    position: "absolute",
    bottom: 24,
    left: 56,
    right: 56,
    flexDirection: "row",
    justifyContent: "space-between",
    color: MUTED,
    fontSize: 7.5,
  },
  kicker: {
    fontFamily: "Courier",
    fontSize: 7.5,
    letterSpacing: 2.4,
    textTransform: "uppercase",
    color: MOSS_SOFT,
  },
  h2: {
    fontFamily: "Times-Roman",
    fontSize: 19,
    color: PINE,
    marginBottom: 6,
  },
  h3: {
    fontFamily: "Times-Roman",
    fontSize: 14,
    color: PINE,
    marginBottom: 3,
  },
  body: { fontSize: 9.5, color: INK },
  muted: { color: MUTED },
  section: { marginBottom: 22 },
  card: {
    backgroundColor: "#ffffff",
    borderRadius: 10,
    borderWidth: 1,
    borderColor: LINE,
    padding: 14,
    marginBottom: 10,
  },
  statGrid: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  stat: {
    backgroundColor: "#ffffff",
    borderWidth: 1,
    borderColor: LINE,
    borderRadius: 10,
    paddingVertical: 8,
    paddingHorizontal: 12,
    minWidth: 108,
    flexGrow: 1,
  },
  statValue: { fontFamily: "Courier", fontSize: 15, color: PINE },
  statLabel: { fontSize: 7.5, color: MUTED, marginTop: 2 },
  bullet: { flexDirection: "row", gap: 6, marginBottom: 4 },
  bulletDot: { color: MOSS, fontSize: 9.5 },
  quote: {
    borderLeftWidth: 2,
    borderLeftColor: MOSS,
    paddingLeft: 10,
    marginTop: 8,
  },
  quoteText: {
    fontFamily: "Times-Italic",
    fontSize: 11.5,
    lineHeight: 1.4,
    color: INK,
  },
  quoteAnchor: {
    fontFamily: "Courier",
    fontSize: 7.5,
    letterSpacing: 1.2,
    color: MOSS,
    marginTop: 3,
  },
  segmentRow: { flexDirection: "row", gap: 8, marginBottom: 7 },
  segmentNumber: {
    fontFamily: "Courier",
    fontSize: 8.5,
    color: MOSS,
    width: 26,
    textAlign: "right",
  },
});

const LANGUAGES: Record<string, string> = {
  en: "English",
  de: "German",
  auto: "Auto-detected",
};

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const date = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
  return date.toLocaleDateString("en-GB", {
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

function speakerName(interview: Interview, label: string): string {
  return interview.speakers[label] ?? label;
}

function quoteAnchor(interview: Interview, quote: InterviewQuote): string {
  const parts = [`SEG. ${quote.segment}`];
  if (quote.timestamp) parts.push(quote.timestamp);
  if (quote.speaker) parts.push(speakerName(interview, quote.speaker));
  return parts.join(" · ");
}

function SectionTitle({ kicker, title }: { kicker: string; title: string }) {
  return (
    <View style={{ marginBottom: 8 }} minPresenceAhead={80}>
      <Text style={styles.kicker}>{kicker}</Text>
      <Text style={styles.h2}>{title}</Text>
    </View>
  );
}

function Bullets({ items }: { items: string[] }) {
  return (
    <>
      {items.map((item, index) => (
        <View key={index} style={styles.bullet} wrap={false}>
          <Text style={styles.bulletDot}>•</Text>
          <Text style={[styles.body, { flex: 1 }]}>{item}</Text>
        </View>
      ))}
    </>
  );
}

function PageChrome({ interview }: { interview: Interview }) {
  return (
    <>
      <View style={styles.pageHeader} fixed>
        <Text style={{ fontFamily: "Times-Roman", fontSize: 11, color: PINE }}>
          SixSentences_
        </Text>
        <Text style={{ fontFamily: "Courier", fontSize: 7, color: MUTED }}>
          INTERVIEW ANALYSIS REPORT
        </Text>
      </View>
      <View style={styles.pageFooter} fixed>
        <Text>Interview {interview.id} · {formatClock(interview.duration_ms)}</Text>
        <Text
          render={({ pageNumber, totalPages }) => `${pageNumber} / ${totalPages}`}
        />
      </View>
    </>
  );
}

export function InterviewReportDoc({ interview }: { interview: Interview }) {
  const analysis = interview.analysis;
  const segments = interview.segments ?? [];
  const speakerLabels = Object.keys(interview.speakers);
  const verified = analysis.quotes_verified ?? 0;
  const total = analysis.quotes_total ?? 0;
  const edited = segments.some((segment) => segment.edited);
  const generated = formatDate(new Date().toISOString());
  const hasAiAnalysis = Boolean(analysis.summary || analysis.themes?.length);

  return (
    <Document
      {...reportPdfProvenance(
        "interview-report",
        interview.kind === "live" || hasAiAnalysis,
        (interview.kind === "live"
          ? "human-participant,ai-interviewer,machine-transcription"
          : "source-speech,machine-transcription")
          + (hasAiAnalysis ? ",ai-assisted-analysis" : ""),
      )}
      title={`SixSentences_ interview report: ${interview.title}`}
      author="SixSentences_"
      subject={interview.title}
    >
      {/* Cover */}
      <Page size="A4" style={styles.cover}>
        <View style={{ flexDirection: "row", justifyContent: "space-between" }}>
          <Text style={{ fontFamily: "Times-Roman", fontSize: 20 }}>SixSentences_</Text>
          <Text
            style={{
              fontFamily: "Courier",
              fontSize: 7.5,
              letterSpacing: 2,
              color: MOSS_SOFT,
              marginTop: 6,
            }}
          >
            AUDIT-GRADE EVIDENCE
          </Text>
        </View>

        <View style={{ flexGrow: 1, justifyContent: "center" }}>
          <Text
            style={{
              fontFamily: "Courier",
              fontSize: 8.5,
              letterSpacing: 3,
              color: MOSS_SOFT,
              marginBottom: 14,
            }}
          >
            INTERVIEW TRANSCRIPT AND ANALYSIS
          </Text>
          <Text
            style={{
              fontFamily: "Times-Roman",
              fontSize: 34,
              lineHeight: 1.15,
              color: IVORY,
            }}
          >
            {interview.title}
          </Text>
          <Text
            style={{
              fontFamily: "Times-Italic",
              fontSize: 13,
              color: "#c9c2b2",
              marginTop: 10,
            }}
          >
            {speakerLabels.map((label) => speakerName(interview, label)).join(", ")}
          </Text>
        </View>

        <View style={{ flexDirection: "row", gap: 8 }}>
          {[
            [formatClock(interview.duration_ms), "duration"],
            [String(speakerLabels.length), "speakers"],
            [String(segments.length), "segments"],
            [total > 0 ? `${verified}/${total}` : "0", "quotes verified"],
            [generated, "generated"],
          ].map(([value, label]) => (
            <View
              key={label}
              style={{
                borderWidth: 1,
                borderColor: "#2a3f39",
                borderRadius: 10,
                paddingVertical: 8,
                paddingHorizontal: 12,
                flexGrow: 1,
              }}
            >
              <Text style={{ fontFamily: "Courier", fontSize: 12, color: IVORY }}>
                {value}
              </Text>
              <Text style={{ fontSize: 7, color: MOSS_SOFT, marginTop: 2 }}>
                {label}
              </Text>
            </View>
          ))}
        </View>
        <Text style={{ fontSize: 7.5, color: "#7d8a82", marginTop: 18 }}>
          AI-assisted transcription and thematic analysis; every supporting
          quote was verified verbatim against the transcript. Interview{" "}
          {interview.id} · {LANGUAGES[interview.language] ?? interview.language}
        </Text>
      </Page>

      {/* Analysis */}
      <Page size="A4" style={styles.page}>
        <PageChrome interview={interview} />

        <View style={styles.section}>
          <SectionTitle kicker="Recording" title="The interview at a glance" />
          <View style={styles.statGrid}>
            {[
              [formatClock(interview.duration_ms), "duration"],
              [LANGUAGES[interview.language] ?? interview.language, "language"],
              [
                speakerLabels.map((label) => speakerName(interview, label)).join(", "),
                "speakers",
              ],
              [String(segments.length), "segments"],
            ].map(([value, label]) => (
              <View key={String(label)} style={styles.stat}>
                <Text style={[styles.statValue, { fontSize: 11 }]}>
                  {String(value)}
                </Text>
                <Text style={styles.statLabel}>{String(label)}</Text>
              </View>
            ))}
          </View>
          {interview.guide ? (
            <Text style={[styles.body, styles.muted, { marginTop: 8, fontSize: 8.5 }]}>
              Study context: {interview.guide}
            </Text>
          ) : null}
        </View>

        {analysis.summary ? (
          <View style={styles.section}>
            <SectionTitle kicker="Overview" title="Summary" />
            <Text style={styles.body}>{analysis.summary}</Text>
          </View>
        ) : null}

        {(analysis.themes ?? []).length > 0 && (
          <View style={styles.section}>
            <SectionTitle kicker="Synthesis" title="Themes" />
            {(analysis.themes ?? []).map((theme, index) => (
              <View key={index} style={styles.card} minPresenceAhead={70}>
                <Text style={styles.h3}>{theme.name}</Text>
                {theme.description ? (
                  <Text style={styles.body}>{theme.description}</Text>
                ) : null}
                {theme.quotes.map((quote, quoteIndex) => (
                  <View key={quoteIndex} style={styles.quote} wrap={false}>
                    <Text style={styles.quoteText}>
                      &ldquo;{quote.text}&rdquo;
                    </Text>
                    {quote.verified ? (
                      <Text style={styles.quoteAnchor}>
                        {quoteAnchor(interview, quote)}
                      </Text>
                    ) : (
                      <Text style={[styles.quoteAnchor, { color: FLAG }]}>
                        NOT FOUND VERBATIM IN THE TRANSCRIPT
                      </Text>
                    )}
                  </View>
                ))}
              </View>
            ))}
          </View>
        )}

        {(analysis.key_findings ?? []).length > 0 && (
          <View style={styles.section}>
            <SectionTitle kicker="Signal" title="Key findings" />
            <Bullets items={analysis.key_findings ?? []} />
          </View>
        )}

        {(analysis.tensions ?? []).length > 0 && (
          <View style={styles.section}>
            <SectionTitle kicker="Honesty" title="Tensions and contradictions" />
            <Bullets items={analysis.tensions ?? []} />
          </View>
        )}

        {(analysis.hypotheses ?? []).length > 0 && (
          <View style={styles.section}>
            <SectionTitle kicker="Conjecture" title="Hypotheses for future testing" />
            <Bullets items={analysis.hypotheses ?? []} />
          </View>
        )}
        {(analysis.followups ?? []).length > 0 && (
          <View style={styles.section}>
            <SectionTitle kicker="Next" title="Follow-up questions" />
            <Bullets items={analysis.followups ?? []} />
          </View>
        )}
      </Page>

      {/* Transcript */}
      <Page size="A4" style={styles.page}>
        <PageChrome interview={interview} />
        <View style={styles.section}>
          <SectionTitle kicker="Record" title="Full transcript" />
          {edited ? (
            <Text style={[styles.body, styles.muted, { fontSize: 8, marginBottom: 8 }]}>
              Segments marked with * were manually corrected.
            </Text>
          ) : null}
          {segments.map((segment) => (
            <View key={segment.idx} style={styles.segmentRow} minPresenceAhead={30}>
              <Text style={styles.segmentNumber}>
                [{segment.idx}
                {segment.edited ? "*" : ""}]
              </Text>
              <View style={{ flex: 1 }}>
                <Text>
                  <Text
                    style={{ fontFamily: "Courier", fontSize: 7.5, color: MUTED }}
                  >
                    {formatClock(segment.start_ms)}{"  "}
                  </Text>
                  <Text style={[styles.body, { fontFamily: "Helvetica-Bold" }]}>
                    {speakerName(interview, segment.speaker)}
                    {"  "}
                  </Text>
                  <Text style={styles.body}>{segment.text}</Text>
                </Text>
              </View>
            </View>
          ))}
        </View>
      </Page>
    </Document>
  );
}

/** Render the interview report to a PDF blob and download it. */
export async function downloadInterviewReportPdf(
  interview: Interview,
): Promise<void> {
  const blob = await pdf(<InterviewReportDoc interview={interview} />).toBlob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `sixsentences-interview-${interview.id}.pdf`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
