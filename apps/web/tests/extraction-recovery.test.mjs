import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const surfaces = [
  ["Studio", "src/components/run/extraction-studio.tsx", "extract"],
  ["Evidence panel", "src/components/run/evidence-panel.tsx", "start"],
];

function harness(path, mutationName, overrides = {}) {
  const source = readFileSync(path, "utf8");
  const ast = ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const functions = [];
  function visit(node) {
    if (ts.isFunctionDeclaration(node)
      && ["requestExtraction", "confirmReextraction"].includes(node.name?.text)) {
      functions.push(node.getText(ast));
    }
    ts.forEachChild(node, visit);
  }
  visit(ast);
  assert.equal(functions.length, 2);
  const calls = [];
  const context = {
    table: { status: "done", is_running: false, unfinished_count: 0, failed_count: 0 },
    isRunning: false,
    reextractOpen: false,
    fields: ["method", "outcomes"],
    [mutationName]: { isPending: false, mutate: (value) => calls.push(value) },
    setReextractOpen: (value) => { context.reextractOpen = value; },
    ...overrides,
  };
  const compiled = ts.transpileModule(functions.join("\n"), {
    compilerOptions: { target: ts.ScriptTarget.ES2022 },
  }).outputText;
  vm.runInNewContext(compiled, context);
  return { source, calls, context };
}

for (const [name, path, mutationName] of surfaces) {
  const forceValue = (input) => typeof input === "boolean" ? input : input.force;

  test(`${name}: ordinary recovery and checking completed studies never request force`, () => {
    for (const table of [
      { status: "none", is_running: false, unfinished_count: 0, failed_count: 0 },
      { status: "done", is_running: false, unfinished_count: 1, failed_count: 1 },
      { status: "pending", is_running: false, unfinished_count: 2, failed_count: 0 },
      { status: "failed", is_running: false, unfinished_count: 2, failed_count: 2 },
      { status: "done", is_running: false, unfinished_count: 0, failed_count: 0 },
    ]) {
      const run = harness(path, mutationName, { table });
      run.context.requestExtraction();
      assert.equal(run.calls.length, 1);
      assert.equal(forceValue(run.calls[0]), false);
      assert.equal(run.context.reextractOpen, false);
    }
  });

  test(`${name}: regenerating requires a separate confirmation and keeps selected fields`, () => {
    const run = harness(path, mutationName);
    run.context.confirmReextraction();
    assert.equal(run.calls.length, 0);
    run.context.requestExtraction(true);
    assert.equal(run.context.reextractOpen, true);
    assert.equal(run.calls.length, 0);
    run.context.confirmReextraction();
    assert.equal(run.calls.length, 1);
    assert.equal(forceValue(run.calls[0]), true);
    if (mutationName === "start") assert.equal(run.calls[0].fields, run.context.fields);
    assert.match(run.source, /<AlertDialog open=\{reextractOpen\}/);
    assert.match(run.source, /onClick=\{confirmReextraction\}/);
    assert.match(run.source, /replaces its current table values/);
    assert.doesNotMatch(run.source, /uses capacity again/);
    assert.match(run.source, /reviewed edits/);
  });

  test(`${name}: cancellation or changed running state never forces a mutation`, () => {
    const cancelled = harness(path, mutationName);
    cancelled.context.requestExtraction(true);
    cancelled.context.setReextractOpen(false);
    cancelled.context.confirmReextraction();
    assert.equal(cancelled.calls.length, 0);
    for (const state of [
      { isRunning: true },
      { table: undefined },
      { [mutationName]: { isPending: true, mutate: () => assert.fail("Pending mutation repeated") } },
    ]) {
      const run = harness(path, mutationName, { reextractOpen: true, ...state });
      run.context.requestExtraction();
      run.context.requestExtraction(true);
      run.context.confirmReextraction();
      assert.equal(run.calls.length, 0);
    }
  });

  test(`${name}: polling and active locks use the server activity flag, not unfinished rows`, () => {
    const { source } = harness(path, mutationName);
    assert.match(source, /query\.state\.data\?\.is_running === true \? [\d_]+ : false/);
    assert.match(source, /const isRunning = table\?\.is_running === true/);
    assert.doesNotMatch(source, /query\.state\.data\?\.status === "pending"/);
    assert.doesNotMatch(source, /disabled=\{[^}]*table\.status === "pending"/);
    assert.match(source, /Retry unfinished/);
    assert.match(source, /Extract unfinished/);
    assert.match(source, /Check for new studies/);
    assert.match(source, /Completed results are kept when you retry/);
    assert.match(source, /Every included study is already extracted/);
  });
}

test("extraction table exposes additive active-work and recovery counts", () => {
  const types = readFileSync("src/lib/types.ts", "utf8");
  const evidence = types.slice(types.indexOf("export interface EvidenceTable"), types.indexOf("export interface ControlRoomStage"));
  assert.match(evidence, /is_running: boolean/);
  assert.match(evidence, /unfinished_count: number/);
  assert.match(evidence, /failed_count: number/);
});

test("the studio action bar wraps rather than pushing its extra action outside the pane", () => {
  const studio = readFileSync("src/components/run/extraction-studio.tsx", "utf8");
  assert.match(studio, /className="flex min-w-0 flex-wrap items-center gap-2"/);
  assert.match(studio, /className="min-w-0 flex-1 basis-56"/);
});

test("extraction titles remove every angle bracket without multi-pass tag stripping", () => {
  const path = "src/components/run/extraction-studio.tsx";
  const source = readFileSync(path, "utf8");
  const ast = ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let helper = "";
  ts.forEachChild(ast, (node) => {
    if (ts.isFunctionDeclaration(node) && node.name?.text === "cleanTitle") {
      helper = node.getText(ast);
    }
  });
  assert.ok(helper);
  const context = {};
  vm.runInNewContext(ts.transpileModule(helper, {
    compilerOptions: { target: ts.ScriptTarget.ES2022 },
  }).outputText, context);
  assert.equal(context.cleanTitle("<<script>alert(1)</script>  Study"), "scriptalert(1)/script Study");
  assert.doesNotMatch(context.cleanTitle("<<<b>Title</b>>"), /[<>]/);
  assert.doesNotMatch(helper, /<\[\^>\]\*>/);
});
