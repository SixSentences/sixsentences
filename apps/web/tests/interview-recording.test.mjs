import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const source = readFileSync("src/app/(app)/interviews/[id]/page.tsx", "utf8");
const start = source.indexOf("  // The recording route needs authentication;");
const end = source.indexOf("  useEffect(() => {\n    chatEndRef", start);
assert.ok(start > 0 && end > start);
const compiled = ts.transpileModule(
  `function render() { ${source.slice(start, end)}
    return { audioUrl, audioLoadFailed, retryAudio, markAudioUnavailable };
  } exports.render = render;`,
  { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } },
).outputText;

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function harness() {
  const requests = [], revoked = [];
  let effect, cleanup;
  const context = {
    exports: {}, interviewId: "synthetic-1", ready: true,
    interview: { audio_available: true }, audioLoadAttempt: 0, audioState: null,
    AbortController,
    URL: { revokeObjectURL: (url) => revoked.push(url) },
    setAudioState: (update) => {
      context.audioState = typeof update === "function" ? update(context.audioState) : update;
    },
    setAudioLoadAttempt: (update) => { context.audioLoadAttempt = update(context.audioLoadAttempt); },
    useEffect: (callback) => { effect = callback; },
    fetchInterviewAudioUrl: (id, signal) => {
      const result = deferred();
      requests.push({ id, signal, ...result });
      return result.promise;
    },
  };
  vm.runInNewContext(compiled, context);
  return {
    context, requests, revoked,
    render: () => context.exports.render(),
    load: () => {
      cleanup?.();
      context.exports.render();
      cleanup = effect();
    },
    dispose: () => cleanup?.(),
    flush: async () => { await Promise.resolve(); await Promise.resolve(); },
  };
}

test("a failed recording leaves loading, exposes no technical error, and can retry", async () => {
  const ui = harness();
  ui.load();
  assert.equal(ui.context.audioState.status, "loading");
  ui.requests[0].reject(new Error("secret upstream response"));
  await ui.flush();
  let view = ui.render();
  assert.equal(view.audioLoadFailed, true);
  assert.equal(view.audioUrl, null);
  view.retryAudio();
  ui.load();
  assert.equal(ui.requests[0].signal.aborted, true);
  assert.equal(ui.requests[1].id, "synthetic-1");
  assert.equal(ui.context.audioState.status, "loading");
  ui.requests[1].resolve("blob:synthetic-retry");
  await ui.flush();
  view = ui.render();
  assert.equal(view.audioLoadFailed, false);
  assert.equal(view.audioUrl, "blob:synthetic-retry");
  ui.dispose();
  assert.deepEqual(ui.revoked, ["blob:synthetic-retry"]);
});

test("navigation hides old audio and aborts its request without late failure overwriting the new state", async () => {
  const ui = harness();
  ui.load();
  ui.context.interviewId = "synthetic-2";
  assert.equal(ui.render().audioUrl, null);
  ui.load();
  assert.equal(ui.requests[0].signal.aborted, true);
  ui.requests[1].resolve("blob:synthetic-2");
  await ui.flush();
  ui.requests[0].reject(new Error("late failure"));
  await ui.flush();
  assert.equal(ui.render().audioUrl, "blob:synthetic-2");
  assert.equal(ui.render().audioLoadFailed, false);
  ui.dispose();
});

test("a late blob after cleanup is revoked without updating the removed player", async () => {
  const ui = harness();
  ui.load();
  ui.dispose();
  const lastState = ui.context.audioState;
  ui.requests[0].resolve("blob:late");
  await ui.flush();
  assert.equal(ui.context.audioState, lastState);
  assert.deepEqual(ui.revoked, ["blob:late"]);
});

test("playback errors offer the same retry and removed recordings never reuse a loaded URL", async () => {
  const ui = harness();
  ui.load();
  ui.requests[0].resolve("blob:recording");
  await ui.flush();
  ui.render().markAudioUnavailable();
  assert.equal(ui.render().audioLoadFailed, true);
  ui.context.interview.audio_available = false;
  ui.load();
  assert.equal(ui.render().audioUrl, null);
  assert.equal(ui.render().audioLoadFailed, false);
  assert.equal(ui.requests.length, 1);
  assert.deepEqual(ui.revoked, ["blob:recording"]);
});

test("loaded audio cannot appear for another interview or retry before effects run", async () => {
  const ui = harness();
  ui.load();
  ui.requests[0].resolve("blob:recording");
  await ui.flush();
  assert.equal(ui.render().audioUrl, "blob:recording");
  ui.context.interviewId = "synthetic-2";
  assert.equal(ui.render().audioUrl, null);
  ui.context.interviewId = "synthetic-1";
  ui.render().retryAudio();
  assert.equal(ui.render().audioUrl, null);
  ui.dispose();
});

test("the recording request forwards cancellation and the UI has distinct accessible states", async () => {
  const api = readFileSync("src/lib/api.ts", "utf8");
  const apiStart = api.indexOf("export async function fetchInterviewAudioUrl(");
  const apiEnd = api.indexOf("export async function downloadInterviewReport(", apiStart);
  const requests = [];
  const apiContext = {
    exports: {}, API_URL: "https://synthetic.invalid", getToken: () => null,
    fetchApiResponse: async (url, init) => { requests.push({ url, init }); return { ok: true }; },
    readBlobResponse: async () => "synthetic bytes",
    URL: { createObjectURL: () => "blob:synthetic" },
  };
  vm.runInNewContext(ts.transpileModule(api.slice(apiStart, apiEnd), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText, apiContext);
  const controller = new AbortController();
  assert.equal(await apiContext.exports.fetchInterviewAudioUrl("synthetic-1", controller.signal), "blob:synthetic");
  assert.equal(requests[0].init.signal, controller.signal);
  assert.match(source, /audioLoadFailed \? \([\s\S]*role="alert"[\s\S]*The recording could not be loaded\. Your transcript is still available/);
  assert.match(source, /onClick=\{retryAudio\}/);
  assert.match(source, /onError=\{markAudioUnavailable\}/);
  assert.match(source, /role="status"[^>]*>[\s\S]*Loading the recording/);
});
