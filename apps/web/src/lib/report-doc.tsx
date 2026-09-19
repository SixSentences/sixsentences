/**
 * The branded run report PDF, rendered client-side with @react-pdf/renderer.
 *
 * Design language mirrors the app: pine cover with the SixSentences_ wordmark
 * in a built-in PDF serif, ivory interior pages, moss accents, mono details.
 * Provider-stable citations in synthesized sections are rewritten to numbered
 * references that resolve in the included-studies list.
 *
 * This module is imported dynamically from the report button so the renderer
 * never enters the main bundle.
 */

import {
  Document,
  Font,
  Link,
  Page,
  StyleSheet,
  Text,
  View,
  pdf,
} from "@react-pdf/renderer";
import type { Style } from "@react-pdf/stylesheet";

import type { RunReport } from "@/lib/types";
import {
  normalizeScholarlyWorkId,
  scholarlyWorkUrl,
} from "@/lib/scholarly-work";
import { reportPdfProvenance } from "@/lib/ai-media-provenance";

const PINE = "#0c1d19";
const IVORY = "#f4f1ea";
const MOSS = "#33544c";
const MOSS_SOFT = "#5a897d";
const INK = "#1d2a26";
const MUTED = "#5c6b66";
const LINE = "#d8d2c4";

// keep words whole: the default hyphenator breaks technical terms awkwardly
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
    // Use font metrics here: an inherited line height is repeatedly scaled
    // when React PDF resolves the dynamic page number during pagination.
    lineHeight: 0,
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
  queryBox: {
    backgroundColor: PINE,
    borderRadius: 10,
    padding: 12,
    marginTop: 8,
  },
  queryText: { fontFamily: "Courier", fontSize: 8, color: IVORY, lineHeight: 1.5 },
  bullet: { flexDirection: "row", gap: 6, marginBottom: 4 },
  bulletDot: { color: MOSS, fontSize: 9.5 },
  refItem: { flexDirection: "row", gap: 8, marginBottom: 8 },
  refNumber: {
    fontFamily: "Courier",
    fontSize: 8.5,
    color: MOSS,
    width: 22,
    textAlign: "right",
  },
  citation: { color: MOSS, fontFamily: "Courier", fontSize: 8 },
});

const WORK_ID_SOURCE = String.raw`(?:W\d+|pubmed:[1-9]\d{0,11})`;
const CITATION = new RegExp(
  String.raw`\[(${WORK_ID_SOURCE}(?:\s*,\s*${WORK_ID_SOURCE})*)\]`,
  "gi",
);
const WORK_ID = new RegExp(WORK_ID_SOURCE, "gi");

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const date = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
  return date.toLocaleDateString("en-GB", {
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

/** Replace provider-stable work ids with numbered reference-list citations. */
function CitedBody({
  text,
  numbering,
  style,
}: {
  text: string;
  numbering: Map<string, number>;
  style?: Style;
}) {
  const parts = text.split(CITATION);
  return (
    <Text style={[styles.body, style ?? {}]}>
      {parts.map((part, index) => {
        if (index % 2 === 0) return <Text key={index}>{part}</Text>;
        const numbers = (part.match(WORK_ID) ?? [])
          .map(normalizeScholarlyWorkId)
          .map((id) => numbering.get(id))
          .filter((n): n is number => typeof n === "number");
        return (
          <Text key={index} style={styles.citation}>
            {numbers.length ? ` [${numbers.join(", ")}]` : ""}
          </Text>
        );
      })}
    </Text>
  );
}

function SectionTitle({ kicker, title }: { kicker: string; title: string }) {
  return (
    <View style={{ marginBottom: 8 }} minPresenceAhead={80} wrap={false}>
      <Text style={styles.kicker}>{kicker}</Text>
      <Text style={styles.h2}>{title}</Text>
    </View>
  );
}

function PageChrome({ report }: { report: RunReport }) {
  return (
    <>
      <View style={styles.pageHeader} fixed>
        <Text style={{ fontFamily: "Times-Roman", fontSize: 11, color: PINE }}>
          SixSentences_
        </Text>
        <Text style={{ fontFamily: "Courier", fontSize: 7, color: MUTED }}>
          SYSTEMATIC LITERATURE SEARCH REPORT
        </Text>
      </View>
      <View style={styles.pageFooter} fixed>
        <Text>
          Run #{report.run.id}
        </Text>
        <Text
          render={({ pageNumber, totalPages }) => `${pageNumber} / ${totalPages}`}
        />
      </View>
    </>
  );
}

function authorLine(work: RunReport["included"][number]): string {
  const names = work.authors.slice(0, 4).join(", ");
  const etAl = work.authors.length > 4 ? " et al." : "";
  return names ? `${names}${etAl}` : "Unknown authors";
}

function ReferenceTitle({ work }: { work: RunReport["included"][number] }) {
  const href = scholarlyWorkUrl(work.id, work.doi);
  const style = [styles.body, { fontFamily: "Helvetica-Bold" }] as Style[];
  return href ? (
    <Link src={href} style={[...style, { color: MOSS, textDecoration: "none" }]}>
      {work.title}
    </Link>
  ) : (
    <Text style={style}>{work.title}</Text>
  );
}

export function ReportDoc({ report }: { report: RunReport }) {
  const prisma = report.prisma;
  const numbering = new Map<string, number>(
    report.included.map((work, index) => [
      normalizeScholarlyWorkId(work.id),
      index + 1,
    ]),
  );
  const generated = formatDate(report.run.finished_at ?? report.run.created_at);
  const sections = report.sections;
  const included =
    (prisma.studies_included ?? 0) > 0 ? prisma.studies_included : prisma.included;

  return (
    <Document
      {...reportPdfProvenance(
        "research-report",
        report.ai_generated_sections ?? null,
        report.ai_generated_sections
          ? "ai-synthesis,source-records,deterministic-audit-facts"
          : "source-records,deterministic-audit-facts",
      )}
      title={`SixSentences_ report: ${report.title}`}
      author="SixSentences_"
      subject={report.question}
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
            SYSTEMATIC LITERATURE SEARCH REPORT
          </Text>
          <Text
            style={{
              fontFamily: "Times-Roman",
              fontSize: 34,
              lineHeight: 1.15,
              color: IVORY,
            }}
          >
            {report.title}
          </Text>
          {report.title !== report.question && (
            <Text
              style={{
                fontFamily: "Times-Italic",
                fontSize: 13,
                color: "#c9c2b2",
                marginTop: 10,
              }}
            >
              {report.question}
            </Text>
          )}
        </View>

        <View style={{ flexDirection: "row", gap: 8 }}>
          {[
            [String(prisma.records_identified ?? 0), "records identified"],
            [String(prisma.records_screened ?? 0), "records screened"],
            [String(included ?? 0), "works included"],
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
          Every number in this report reconstructs from the run&apos;s append-only
          audit log. Run #{report.run.id}
        </Text>
      </Page>

      {/* Body */}
      <Page size="A4" style={styles.page}>
        <PageChrome report={report} />

        <View style={styles.section}>
          <SectionTitle kicker="Overview" title="Executive summary" />
          <CitedBody text={sections.executive_summary} numbering={numbering} />
        </View>

        {sections.key_findings.length > 0 && (
          <View style={styles.section}>
            <SectionTitle kicker="Signal" title="Key findings" />
            {sections.key_findings.map((finding, index) => (
              <View key={index} style={styles.bullet}>
                <Text style={styles.bulletDot}>–</Text>
                <View style={{ flex: 1 }}>
                  <CitedBody text={finding} numbering={numbering} />
                </View>
              </View>
            ))}
          </View>
        )}

        {sections.themes.map((theme, index) => (
          <View key={index} style={styles.section}>
            {index === 0 && <SectionTitle kicker="Synthesis" title="Themes" />}
            <View style={styles.card} minPresenceAhead={60}>
              <Text style={styles.h3}>{theme.title}</Text>
              <CitedBody text={theme.body} numbering={numbering} />
            </View>
          </View>
        ))}

        <>
          {/* Keep the bounded overview together and leave room for the query
              label plus its first lines. Long queries can still span pages. */}
          <View
            style={report.query_string ? undefined : styles.section}
            wrap={false}
            minPresenceAhead={report.query_string ? 72 : 0}
          >
            <SectionTitle kicker="Method" title="The search at a glance" />
            <View style={styles.statGrid}>
              {[
                [prisma.records_identified, "records identified"],
                [prisma.other_identified, "found via web sources"],
                [prisma.duplicates_removed, "duplicates removed"],
                [prisma.records_screened, "records screened"],
                [prisma.records_excluded, "excluded by the ensemble"],
                [report.unsure_count, "awaiting human review"],
                [included, "works included"],
                [prisma.retracted_flagged, "retractions flagged"],
              ]
                .filter(([value]) => typeof value === "number")
                .map(([value, label]) => (
                  <View key={String(label)} style={styles.stat}>
                    <Text style={styles.statValue}>{String(value)}</Text>
                    <Text style={styles.statLabel}>{String(label)}</Text>
                  </View>
                ))}
            </View>
          </View>
          {report.query_string ? (
            <View style={[styles.queryBox, styles.section]}>
              <Text
                style={{
                  fontFamily: "Courier",
                  fontSize: 6.5,
                  letterSpacing: 2,
                  color: MOSS_SOFT,
                  marginBottom: 4,
                }}
              >
                VERBATIM SEARCH QUERY
              </Text>
              <Text style={styles.queryText}>{report.query_string}</Text>
            </View>
          ) : null}
        </>

        {(report.criteria.inclusion.length > 0 ||
          report.criteria.exclusion.length > 0) && (
          <View style={styles.section}>
            <SectionTitle kicker="Protocol" title="Eligibility criteria" />
            <View style={{ flexDirection: "row", gap: 10 }}>
              {(
                [
                  ["Include when", report.criteria.inclusion],
                  ["Exclude when", report.criteria.exclusion],
                ] as const
              ).map(([label, items]) => (
                <View key={label} style={[styles.card, { flex: 1 }]}>
                  <Text
                    style={{
                      fontFamily: "Courier",
                      fontSize: 7,
                      letterSpacing: 1.6,
                      textTransform: "uppercase",
                      color: MOSS,
                      marginBottom: 5,
                    }}
                  >
                    {label}
                  </Text>
                  {items.map((item, index) => (
                    <View key={index} style={styles.bullet}>
                      <Text style={styles.bulletDot}>–</Text>
                      <Text style={[styles.body, { flex: 1 }]}>{item}</Text>
                    </View>
                  ))}
                </View>
              ))}
            </View>
          </View>
        )}

        {report.included.length > 0 && (
          <View style={styles.section}>
            <SectionTitle kicker="Evidence" title="Included studies" />
            {report.included.map((work, index) => (
              <View key={work.id} style={styles.refItem} minPresenceAhead={40}>
                <Text style={styles.refNumber}>[{index + 1}]</Text>
                <View style={{ flex: 1 }}>
                  <ReferenceTitle work={work} />
                  <Text style={[styles.body, styles.muted, { fontSize: 8.5 }]}>
                    {authorLine(work)}
                    {work.year ? ` (${work.year})` : ""}
                    {work.venue ? `. ${work.venue}` : ""}
                    {work.doi ? `. doi:${work.doi}` : ""} · {work.id}
                  </Text>
                  {work.reason ? (
                    <Text style={[styles.body, { fontSize: 8, color: MOSS }]}>
                      Why it is in: {work.reason}
                    </Text>
                  ) : null}
                </View>
              </View>
            ))}
          </View>
        )}

        {report.web_sources.length > 0 && (
          <View style={styles.section}>
            <SectionTitle kicker="Context" title="Grey literature" />
            {report.web_sources.map((source, index) => (
              <View key={index} style={styles.bullet}>
                <Text style={styles.bulletDot}>–</Text>
                <View style={{ flex: 1 }}>
                  <Text style={styles.body}>{source.title || source.domain}</Text>
                  <Text style={{ fontFamily: "Courier", fontSize: 7.5, color: MUTED }}>
                    {source.url}
                  </Text>
                </View>
              </View>
            ))}
          </View>
        )}

        <View style={styles.section}>
          <SectionTitle kicker="Honesty" title="Limitations" />
          <CitedBody text={sections.limitations} numbering={numbering} />
          {sections.next_steps.length > 0 && (
            <View style={{ marginTop: 10 }}>
              <Text style={[styles.h3, { fontSize: 12 }]}>Suggested next steps</Text>
              {sections.next_steps.map((step, index) => (
                <View key={index} style={styles.bullet}>
                  <Text style={styles.bulletDot}>–</Text>
                  <Text style={[styles.body, { flex: 1 }]}>{step}</Text>
                </View>
              ))}
            </View>
          )}
        </View>

        {report.methods ? (
          <View style={styles.section}>
            <SectionTitle kicker="PRISMA-S" title="Methods" />
            <Text style={[styles.body, styles.muted, { fontSize: 8 }]}>
              {report.methods}
            </Text>
          </View>
        ) : null}
      </Page>
    </Document>
  );
}

/** Render the report to a PDF blob and hand it to the browser as a download. */
export async function downloadReportPdf(report: RunReport): Promise<void> {
  const blob = await pdf(<ReportDoc report={report} />).toBlob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `sixsentences-report-run-${report.run.id}.pdf`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
