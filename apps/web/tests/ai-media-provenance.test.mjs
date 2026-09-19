import assert from "node:assert/strict";
import { readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { createRequire } from "node:module";
import vm from "node:vm";
import test from "node:test";
import ts from "typescript";
import React from "react";
import * as renderer from "@react-pdf/renderer";
import { getDocument } from "pdfjs-dist/legacy/build/pdf.mjs";

const nodeRequire = createRequire(import.meta.url);
function loadTs(path, dependencies = {}) {
  const exports = {};
  const output = ts.transpileModule(readFileSync(path, "utf8"), {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  }).outputText;
  vm.runInNewContext(output, {
    exports,
    require: (name) => dependencies[name] ?? nodeRequire(name),
    Date,
  }, { filename: path });
  return exports;
}

const provenance = loadTs("src/lib/ai-media-provenance.ts");
const scholarlyWork = loadTs("src/lib/scholarly-work.ts");
const pdfRenderer = {
  ...renderer,
  Font: {
    register: (font) => renderer.Font.register({
      ...font,
      fonts: font.fonts.map((face) => ({ ...face, src: resolve(`public${face.src}`) })),
    }),
    registerHyphenationCallback: renderer.Font.registerHyphenationCallback,
  },
};
const dependencies = {
  "@/lib/ai-media-provenance": provenance,
  "@/lib/scholarly-work": scholarlyWork,
  "@react-pdf/renderer": pdfRenderer,
  "@/lib/format": { formatClock: (ms) => `0:${String(Math.floor(ms / 1000)).padStart(2, "0")}` },
};
const { ReportDoc } = loadTs("src/lib/report-doc.tsx", dependencies);
const { InterviewReportDoc } = loadTs("src/lib/interview-report-doc.tsx", dependencies);

test("actual report PDFs contain machine-readable PDF Info and XMP markers", async () => {
  const report = {
    title: "Synthetic provenance QA", question: "What does this synthetic fixture show?",
    ai_generated_sections: true, prisma: {}, included: [], web_sources: [],
    criteria: { inclusion: [], exclusion: [] },
    sections: { executive_summary: "Synthetic AI-assisted summary.", key_findings: [], themes: [], limitations: "Fixture only.", next_steps: [] },
    run: { id: 42, created_at: "2026-09-04T10:00:00Z", finished_at: null },
  };
  const interview = {
    id: "synthetic-interview", kind: "live", title: "Synthetic interview QA", language: "en",
    duration_ms: 12000, speakers: { S1: "AI interviewer", S2: "Synthetic participant" },
    analysis: { summary: "Synthetic AI-assisted analysis." },
    segments: [{ idx: 1, speaker: "S2", start_ms: 0, end_ms: 12000, text: "Human fixture contribution.", edited: false }],
  };
  for (const [name, component, props] of [
    ["research-report", ReportDoc, { report }],
    ["interview-report", InterviewReportDoc, { interview }],
  ]) {
    const bytes = await renderer.renderToBuffer(React.createElement(component, props));
    assert.equal(bytes.subarray(0, 5).toString(), "%PDF-");
    const document = await getDocument({ data: new Uint8Array(bytes), useSystemFonts: true }).promise;
    const metadata = await document.getMetadata();
    assert.match(metadata.info.Keywords, /sixsentences-ai-provenance-v1/);
    assert.match(metadata.info.Keywords, new RegExp(`artifact=${name}`));
    assert.match(metadata.info.Keywords, /ai-generated-parts=true/);
    assert.match(metadata.info.Keywords, /not-all-content-is-ai-generated/);
    assert.match(metadata.info.Keywords, /digitalsourcetype\/compositeSynthetic/);
    assert.match(metadata.metadata.get("pdf:keywords"), /sixsentences-ai-provenance-v1/);
    assert.equal(metadata.info.Creator, "SixSentences_");
    assert.ok(document.numPages >= 2);
    if (process.env.SIX_PDF_QA_DIR) {
      mkdirSync(process.env.SIX_PDF_QA_DIR, { recursive: true });
      writeFileSync(resolve(process.env.SIX_PDF_QA_DIR, `${name}.pdf`), bytes);
    }
    await document.destroy();
  }
});

test("deterministic and unknown reports are not falsely labelled AI-generated", () => {
  for (const [value, expected] of [[false, "false"], [null, "unknown"]]) {
    const result = provenance.reportPdfProvenance("research-report", value, "source-records");
    assert.match(result.keywords, new RegExp(`ai-generated-parts=${expected}`));
    assert.doesNotMatch(result.keywords, /DigitalSourceType=/);
  }
});
