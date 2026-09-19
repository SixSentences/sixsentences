import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import test from "node:test";
import ts from "typescript";

const helperSource = readFileSync("src/lib/scholarly-work.ts", "utf8");
const panelSource = readFileSync("src/components/run/chat-panel.tsx", "utf8");
const reportSource = readFileSync("src/lib/report-doc.tsx", "utf8");
const worksTableSource = readFileSync("src/components/run/works-table.tsx", "utf8");

function loadHelper() {
  const compiled = ts.transpileModule(helperSource, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText;
  const module = { exports: {} };
  runInNewContext(compiled, { exports: module.exports, module });
  return module.exports;
}

test("provider work links route PubMed, OpenAlex, and DOI identities correctly", () => {
  const { normalizeScholarlyWorkId, scholarlyDoiUrl, scholarlyWorkUrl } = loadHelper();

  assert.equal(normalizeScholarlyWorkId("PUBMED:12345678"), "pubmed:12345678");
  assert.equal(
    scholarlyWorkUrl("pubmed:12345678"),
    "https://pubmed.ncbi.nlm.nih.gov/12345678/",
  );
  assert.equal(scholarlyWorkUrl("w2741809807"), "https://openalex.org/W2741809807");
  assert.equal(scholarlyDoiUrl("doi:10.1000/example"), "https://doi.org/10.1000/example");
  assert.equal(
    scholarlyWorkUrl("import:4:2", "https://doi.org/10.1000/example"),
    "https://doi.org/10.1000/example",
  );
  assert.equal(scholarlyWorkUrl("W0123"), null);
  assert.equal(scholarlyWorkUrl("import:4:2"), null);
});

test("chat citations recognize provider-neutral ids and never hard-code OpenAlex links", () => {
  assert.match(
    panelSource,
    /WORK_ID_SOURCE = String\.raw`\(\?:W\\d\+\|pubmed:\[1-9\]\\d/,
  );
  assert.match(
    panelSource,
    /const ids = \(part\.match\(WORK_ID\) \?\? \[\]\)\.map\(normalizeScholarlyWorkId\)/,
  );
  assert.match(panelSource, /href=\{scholarlyWorkUrl\(id\) \?\? undefined\}/);
  assert.match(panelSource, /href=\{scholarlyWorkUrl\(item\.work_id\) \?\? undefined\}/);
  assert.doesNotMatch(panelSource, /https:\/\/openalex\.org\/\$\{/);
});

test("downloadable reports number provider-neutral citations", () => {
  assert.match(
    reportSource,
    /WORK_ID_SOURCE = String\.raw`\(\?:W\\d\+\|pubmed:\[1-9\]\\d/,
  );
  assert.match(reportSource, /\.map\(normalizeScholarlyWorkId\)/);
  assert.match(reportSource, /normalizeScholarlyWorkId\(work\.id\)/);
  assert.match(reportSource, /scholarlyWorkUrl\(work\.id, work\.doi\)/);
  assert.match(reportSource, /<Link src=\{href\}/);
});

test("the works table links provider identities and retains DOI links", () => {
  assert.match(worksTableSource, /const providerUrl = scholarlyWorkUrl\(work\.id\);/);
  assert.match(worksTableSource, /href=\{providerUrl\}/);
  assert.match(worksTableSource, /\? "PubMed"[\s\S]*?\? "OpenAlex"[\s\S]*?: "DOI"/);
  assert.match(worksTableSource, /scholarlyDoiUrl\(work\.doi\)/);
});
