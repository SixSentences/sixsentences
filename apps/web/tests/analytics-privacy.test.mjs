import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const analyticsUrl = new URL("../src/lib/analytics.ts", import.meta.url);
const componentUrl = new URL("../src/components/analytics.tsx", import.meta.url);
const composerUrl = new URL("../src/components/search/composer.tsx", import.meta.url);
const chatUrl = new URL("../src/components/run/chat-panel.tsx", import.meta.url);
const figuresUrl = new URL("../src/app/(app)/figures/page.tsx", import.meta.url);

test("analytics has a closed runtime payload schema", async () => {
  const source = await readFile(analyticsUrl, "utf8");

  assert.match(source, /interface AnalyticsPayloads/);
  assert.match(source, /const EVENT_FIELDS/);
  assert.match(source, /sanitizeAnalyticsData/);
  assert.doesNotMatch(source, /export type EventData\s*=\s*Record/);
  assert.doesNotMatch(
    source,
    /UmamiTracker|TrackerPayload|sixBeforeSend|\bumami\b|\bbeforeSend\b|normalizeReferrer|normalizePath|routeLabel/,
  );
});

test("analytics never identifies accounts or reports model routing", async () => {
  const [component, composer, chat, figures] = await Promise.all([
    readFile(componentUrl, "utf8"),
    readFile(composerUrl, "utf8"),
    readFile(chatUrl, "utf8"),
    readFile(figuresUrl, "utf8"),
  ]);

  assert.doesNotMatch(component, /\bidentify\s*\(/);
  assert.doesNotMatch(component, /userId|orgId/);
  for (const source of [composer, chat, figures]) {
    assert.doesNotMatch(source, /track\([^;]+\bmodel\s*[,}]/s);
  }
});

test("disabled analytics neither loads a script nor touches browser storage or a legacy tracker", async () => {
  const source = await readFile(analyticsUrl, "utf8");
  const exports = {};
  const forbidden = new Proxy({}, { get() { throw new Error("Unexpected browser access"); }, set() { throw new Error("Unexpected browser write"); } });
  vm.runInNewContext(ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText, { exports, window: forbidden, localStorage: forbidden, document: forbidden });
  assert.equal(exports.analyticsDisabled(), true);
  exports.setAnalyticsDisabled(false);
  exports.track("chat_message", { surface: "run" });
  assert.equal(exports.analyticsDisabled(), true);
  const component = await readFile(componentUrl, "utf8");
  assert.match(component, /return null/);
  assert.doesNotMatch(component, /next\/script|<Script|createElement/);
});
