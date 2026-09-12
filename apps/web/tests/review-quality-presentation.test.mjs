import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

function load(source, imports = {}) {
  const context = {
    exports: {},
    require: (name) => {
      assert.ok(name in imports, `Unexpected import: ${name}`);
      return imports[name];
    },
  };
  vm.runInNewContext(ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText, context);
  return context.exports;
}

const presentation = load(readFileSync("src/lib/review-quality-presentation.ts", "utf8"));
const { coveragePresentation, recallPresentation } = presentation;
const stage = load(readFileSync("src/components/run/stage-meta.tsx", "utf8"), {
  "@/lib/review-quality-presentation": presentation,
  "lucide-react": new Proxy({}, { get: (_, name) => name }),
});
const resultsSource = readFileSync("src/components/run/results-panel.tsx", "utf8");
const statsSource = resultsSource.slice(
  resultsSource.indexOf("function extractStats("), resultsSource.indexOf("function StatTile("),
);
const { extractStats } = load(
  'const { coveragePresentation, recallPresentation } = require("presentation");\n'
  + statsSource + "\nexports.extractStats = extractStats;",
  { presentation },
);
const recall = {
  method: "chao2", estimated_recall: 1, ci_low: 1, ci_high: 1,
  target_recall: 0.95, certified: false, screened: 6, min_sample: 100, reviewers: 3,
};
const event = (event, payload) => ({ event, payload });

test("undetermined coverage never turns the backend's 1.0 placeholder into 100 percent", () => {
  const payload = { method: "undetermined", completeness: 1, ci_low: 0, ci_high: 1, occasions: 1 };
  const result = coveragePresentation(payload);
  assert.equal(result.value, null);
  assert.match(result.description, /undetermined.*insufficient evidence/);
  assert.doesNotMatch(result.description, /100|%/);
  assert.equal(stage.describeEvent(event("coverage_estimated", payload)), result.description);
});

test("six screened records show an estimate and sample shortfall, never certified or below target", () => {
  const result = recallPresentation(recall);
  assert.equal(result.value, 1);
  assert.match(result.description, /Estimated screening recall 100\.0%/);
  assert.match(result.description, /95% CI 100\.0%–100\.0%/);
  assert.match(result.description, /not certified: 6 screened; minimum 100/);
  assert.doesNotMatch(result.description, /recall certified at|below target/);
  assert.equal(stage.describeEvent(event("screening_recall_certified", recall)), result.description);
});

test("an uncertified point estimate above target names the conservative lower bound", () => {
  const result = recallPresentation({ ...recall, screened: 200, ci_low: 0.72 });
  assert.match(result.description, /Estimated screening recall 100\.0%/);
  assert.match(result.description, /not certified: lower 95% bound 72\.0% is below target 95\.0%/);
});

test("rounding cannot make a below-target bound look equal to the target", () => {
  const result = recallPresentation({ ...recall, screened: 200, ci_low: 0.9499 });
  assert.match(result.description, /94\.99% is below target 95\.0%/);
  assert.doesNotMatch(result.description, /95\.0% is below target 95\.0%/);
});

test("only a complete successful certification record can claim certification", () => {
  const valid = { ...recall, screened: 200, certified: true, ci_low: 0.97 };
  assert.match(recallPresentation(valid).description, /; certified: lower 95% bound 97\.0% meets target 95\.0%/);
  for (const update of [
    { certified: false }, { certified: "true" }, { screened: 6 }, { ci_low: 0.5 },
    { min_sample: undefined }, { reviewers: 1 }, { method: "undetermined" },
  ]) {
    assert.doesNotMatch(recallPresentation({ ...valid, ...update }).description, /; certified:/);
  }
});

test("invalid values fail closed and a genuine zero recall estimate remains visible", () => {
  for (const value of [NaN, Infinity, -1, 2, "1"]) {
    assert.equal(coveragePresentation({ method: "chao2", completeness: value }).value, null);
    assert.equal(recallPresentation({ ...recall, estimated_recall: value }).value, null);
  }
  assert.equal(recallPresentation({ ...recall, estimated_recall: 0, ci_low: 0, ci_high: 0 }).value, 0);
});

test("latest undetermined evidence clears an earlier estimate in the results tiles", () => {
  const stats = extractStats([
    event("coverage_estimated", { method: "chao2", completeness: 0.9, ci_low: 0.8, ci_high: 1 }),
    event("screening_recall_certified", { ...recall, certified: true, screened: 200 }),
    event("coverage_estimated", { method: "undetermined", completeness: 1 }),
    event("screening_recall_certified", { ...recall, method: "undetermined" }),
  ]);
  assert.equal(stats.coverage.value, null);
  assert.equal(stats.recall.value, null);
  assert.match(stats.recall.detail, /not certified/);
  assert.match(resultsSource, /label="est\. screening recall"/);
  assert.match(resultsSource, /sub=\{stats\.recall\.detail\}/);
});
