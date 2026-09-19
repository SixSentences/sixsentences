import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { runInNewContext } from "node:vm";
import test from "node:test";
import ts from "typescript";

const hookSource = await readFile(
  new URL("../src/hooks/queries.ts", import.meta.url),
  "utf8",
);
const panelSource = await readFile(
  new URL("../src/components/run/queue-panel.tsx", import.meta.url),
  "utf8",
);

test("large human-review queues use bounded server pages", () => {
  assert.match(hookSource, /api\.screeningQueuePage\(id, limit, offset\)/);
  assert.match(panelSource, /const PAGE_SIZE = 25/);
  assert.match(panelSource, /queuePage\?\.total/);
  assert.match(panelSource, /aria-label="Previous review queue page"/);
  assert.match(panelSource, /aria-label="Next review queue page"/);
});

test("submitting decisions removes the visible rows before refetch", () => {
  assert.match(
    panelSource,
    /items: current\.items\.filter\(\(item\) => !submittedIds\.has\(item\.work_id\)\)/,
  );
  assert.match(panelSource, /total: Math\.max\(0, current\.total - submittedIds\.size\)/);
  assert.match(
    panelSource,
    /invalidateQueries\(\{ queryKey: \["queue", String\(runId\)\] \}\)/,
  );
});

test("review options never implicitly enable or disable live discovery", async () => {
  const source = await readFile(new URL("../src/components/search/composer.tsx", import.meta.url), "utf8");
  const toggle = source.slice(source.indexOf("  function toggle("), source.indexOf("  function resetSystematicReview("));
  const compiled = ts.transpileModule(`export ${toggle.trim()}`, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  for (const live of [false, true]) {
    for (const key of ["screen", "pubmed", "webSearch", "snowball", "semantic", "acquire", "fullText", "gateProtocol"]) {
      let options = { live, screen: false, pubmed: false, webSearch: false, snowball: false, semantic: false, acquire: false, fullText: false, gateProtocol: false };
      const exports = {};
      runInNewContext(compiled, {
        exports,
        options,
        setOptions(update) { options = update(options); },
      });
      exports.toggle(key);
      assert.equal(options.live, live, `${key} must preserve the explicit Live choice`);
      if (key === "fullText") assert.equal(options.acquire, true);
      if (key === "snowball") assert.equal(options.screen, true);
    }
  }
  assert.doesNotMatch(source, /liveTouched|next\.live\s*=\s*true/);
  assert.match(source, /live: options\.live/);
});

test("partial review results distinguish unscreened candidates from inclusions", async () => {
  const results = await readFile(new URL("../src/components/run/results-panel.tsx", import.meta.url), "utf8");
  const works = await readFile(new URL("../src/components/run/works-table.tsx", import.meta.url), "utf8");
  assert.match(results, /const resultCounts = completed\s*\? worksPage\?\.result_evidence_counts\s*: worksPage\?\.evidence_counts/);
  assert.match(results, /resultCounts\.confirmed_include \+ resultCounts\.provisional_include/);
  assert.match(results, /Partial results\./);
  assert.match(results, /not counted as inclusions/);
  assert.match(results, /WorksTable runId=\{run\.id\} completed=\{completed\}/);
  assert.match(works, /completed && work\.selected && work\.verdict !== "exclude"/);
  assert.match(works, /completed && data\.selected_total/);
  assert.match(works, /not screened/);
  assert.doesNotMatch(works, /final set/);
});
