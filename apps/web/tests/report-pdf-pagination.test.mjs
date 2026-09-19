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

const { ReportDoc } = loadTs("src/lib/report-doc.tsx", {
  "@/lib/ai-media-provenance": loadTs("src/lib/ai-media-provenance.ts"),
  "@/lib/scholarly-work": loadTs("src/lib/scholarly-work.ts"),
  "@react-pdf/renderer": {
    ...renderer,
    Font: {
      register: (font) => renderer.Font.register({
        ...font,
        fonts: font.fonts.map((face) => ({ ...face, src: resolve(`public${face.src}`) })),
      }),
      registerHyphenationCallback: renderer.Font.registerHyphenationCallback,
    },
  },
});

const report = {
  title: "Synthetic pagination regression", question: "Can every report field be read?",
  query_string: '"Attention Is All You Need"',
  ai_generated_sections: false,
  prisma: { records_identified: 6, records_screened: 6, duplicates_removed: 0,
    other_identified: 0, records_excluded: 1, included: 2, retracted_flagged: 0 },
  unsure_count: 3,
  included: [], web_sources: [],
  criteria: { inclusion: ["Synthetic inclusion criterion."], exclusion: ["Synthetic exclusion criterion."] },
  sections: {
    executive_summary: "This synthetic fixture checks report pagination without changing research data. ".repeat(8),
    key_findings: Array.from({ length: 4 }, (_, index) => `Finding ${index + 1}: ` +
      "All visible evidence must remain readable in the exported document. ".repeat(2)),
    themes: [{ title: "Synthetic theme", body:
      "A complete report preserves every query and keeps the page header and footer outside the content. ".repeat(6) }],
    limitations: "Synthetic pagination fixture only; not a research finding.",
    next_steps: ["Inspect the complete exported query."],
  },
  methods: "Deterministic pagination fixture; no model or network calls.",
  run: { id: 42, created_at: "2026-09-06T10:00:00Z", finished_at: null },
};

for (const [name, query, summaryLength] of [
  ...Array.from({ length: 8 }, (_, index) => [`short-query-${index + 1}`, report.query_string, index + 1]),
  ["no-query", "", 8],
  ["long-query", Array.from({ length: 420 }, (_, index) => `"TERM_${String(index).padStart(3, "0")}"`).join(" OR "), 8],
]) {
  test(`actual ${name} report preserves text and page chrome across page boundaries`, async () => {
    let layout;
    const element = React.cloneElement(ReportDoc({ report: {
      ...report,
      query_string: query,
      sections: { ...report.sections, executive_summary:
        "This synthetic fixture checks report pagination without changing research data. ".repeat(summaryLength) },
    } }), {
      onRender: (result) => { layout = result._INTERNAL__LAYOUT__DATA_; },
    });
    const bytes = await renderer.renderToBuffer(element);
    if (process.env.SIX_PDF_QA_DIR) {
      mkdirSync(process.env.SIX_PDF_QA_DIR, { recursive: true });
      writeFileSync(resolve(process.env.SIX_PDF_QA_DIR, `${name}.pdf`), bytes);
      const snapshot = (node) => ({
        type: node.type, box: node.box, style: node.style, value: node.value,
        fixed: node.props?.fixed, children: node.children?.map(snapshot),
      });
      writeFileSync(resolve(process.env.SIX_PDF_QA_DIR, `${name}-layout.json`), JSON.stringify(snapshot(layout), null, 2));
    }
    for (const page of layout.children.slice(1)) {
      const footer = page.children.find((node) => node.props?.fixed && node.style?.bottom === 24);
      assert.ok(footer.box.top >= page.box.height - 48, "Footer must stay in its reserved bottom margin");
      assert.ok(footer.box.height <= 16, "Dynamic page numbering must not inflate the footer");
      const checkQueryBounds = (node, parentTop = 0, queryBounds = null) => {
        const top = parentTop + (node.box?.top ?? 0);
        const bounds = node.style?.backgroundColor === "#0c1d19"
          ? { top, bottom: top + node.box.height }
          : queryBounds;
        if (node.style?.backgroundColor === "#0c1d19" && name.startsWith("short-query")) {
          assert.equal(node.box.paddingBottom, 12, "A one-line query card must not split at the page boundary");
          assert.equal(node.children.length, 2, "The query heading and text must stay together");
        }
        if (bounds && node.type === "TEXT") {
          assert.ok(top >= bounds.top - 0.1, "Query text must start inside its box");
          assert.ok(top + node.box.height <= bounds.bottom + 0.1, "Query text must not be clipped by its box");
          assert.ok(top + node.box.height <= page.box.height - 64 + 0.1, "Query text must not overflow into the footer");
        }
        node.children?.forEach((child) => checkQueryBounds(child, top, bounds));
      };
      checkQueryBounds(page);
    }
    const document = await getDocument({ data: new Uint8Array(bytes), useSystemFonts: true }).promise;
    try {
      assert.ok(document.numPages >= 3);
      let content = "";
      let queryPages = 0;
      for (let pageNumber = 2; pageNumber <= document.numPages; pageNumber += 1) {
        const page = await document.getPage(pageNumber);
        const items = (await page.getTextContent()).items.filter((item) => "str" in item);
        const text = items.map((item) => item.str).join("").replace(/\s+/g, " ");
        assert.match(text, /SYSTEMATIC LITERATURE SEARCH REPORT/);
        assert.match(text, /Run #\s*42/);
        assert.ok(text.includes(`${pageNumber} / ${document.numPages}`));
        if (text.includes("The search at a glance")) {
          assert.ok(text.includes("retractions flagged"), "The method heading must stay with its overview");
        }
        if (/TERM_\d{3}/.test(text)) queryPages += 1;
        content += `${text} `;
      }
      if (name === "long-query") assert.ok(queryPages >= 2, "A query longer than one page must remain breakable");
      for (const token of query.match(/"[^"]+"/g) ?? []) assert.ok(content.includes(token), token);
      assert.match(content, /Eligibility criteria/);
      assert.match(content, /Deterministic pagination fixture/);
    } finally {
      await document.destroy();
    }
  });
}

test("section eyebrows remain with their heading at a real PDF page boundary", async () => {
  for (let studyCount = 4; studyCount <= 10; studyCount += 1) {
    const boundaryReport = {
      ...report,
      included: Array.from({ length: studyCount }, (_, index) => ({
        id: `W${index + 1}`,
        title: `Synthetic study ${index + 1}`,
        authors: ["Synthetic Author"],
        year: 2026,
        reason: "Synthetic study used solely to exercise PDF pagination.",
      })),
    };
    const bytes = await renderer.renderToBuffer(ReportDoc({ report: boundaryReport }));
    if (process.env.SIX_PDF_QA_DIR) {
      mkdirSync(process.env.SIX_PDF_QA_DIR, { recursive: true });
      writeFileSync(resolve(process.env.SIX_PDF_QA_DIR, `heading-boundary-${studyCount}.pdf`), bytes);
    }
    const document = await getDocument({ data: new Uint8Array(bytes), useSystemFonts: true }).promise;
    try {
      for (let pageNumber = 2; pageNumber <= document.numPages; pageNumber += 1) {
        const page = await document.getPage(pageNumber);
        const items = (await page.getTextContent()).items.filter((item) => "str" in item);
        const text = items.map((item) => item.str).join("").replace(/\s+/g, "");
        for (const [eyebrow, heading] of [
          ["OVERVIEW", "Executivesummary"],
          ["SIGNAL", "Keyfindings"],
          ["SYNTHESIS", "Themes"],
          ["METHOD", "Thesearchataglance"],
          ["PROTOCOL", "Eligibilitycriteria"],
          ["EVIDENCE", "Includedstudies"],
          ["HONESTY", "Limitations"],
          ["PRISMA-S", "Methods"],
        ]) {
          if (text.includes(eyebrow)) {
            assert.ok(text.includes(heading), `${studyCount} studies, page ${pageNumber}: ${eyebrow} is orphaned`);
          }
        }
      }
    } finally {
      await document.destroy();
    }
  }
});
