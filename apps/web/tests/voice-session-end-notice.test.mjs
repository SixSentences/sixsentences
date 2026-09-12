import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const live = readFileSync("src/components/voice/live-session.tsx", "utf8");
const study = readFileSync("src/app/(app)/interviews/studies/[id]/page.tsx", "utf8");
const talk = readFileSync("src/app/talk/[token]/page.tsx", "utf8");

function between(source, start, end) {
  const first = source.indexOf(start);
  const last = source.indexOf(end, first + start.length);
  assert.ok(first >= 0 && last > first);
  return source.slice(first, last);
}

function evaluate(source, context) {
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  vm.runInNewContext(compiled, context);
}

const helpers = { exports: {} };
evaluate(between(live, "export function liveSessionInterruptionNotice(", "interface LiveRelayMessage"), helpers);
const noticeFor = helpers.exports.liveSessionInterruptionNotice;

test("interruption notices distinguish saved partial work without exposing technical reasons", () => {
  for (const german of [false, true]) {
    for (const reason of ["connection", "unavailable"]) {
      const saved = noticeFor(reason, true, german);
      const absent = noticeFor(reason, false, german);
      assert.match(saved, german ? /gespeichert.*unvollständig/ : /saved.*incomplete/);
      assert.match(absent, german ? /kein Interview-Transkript gespeichert/ : /No interview transcript was saved/);
      assert.doesNotMatch(saved + absent, /too short|zu kurz|API|provider|token/);
    }
    for (const reason of [undefined, "user", "time", "budget", "__proto__", "private diagnostic"]) {
      assert.equal(noticeFor(reason, false, german), null);
    }
  }
});

function finalizerHarness(kind, settled, failure = null) {
  const events = [];
  const requested = [];
  const request = async (...args) => {
    requested.push(args);
    if (failure) throw failure;
    return settled;
  };
  const context = {
    useCallback: (callback) => callback,
    liveSessionInterruptionNotice: noticeFor,
    german: false, token: "synthetic-invite", studyId: "synthetic-study",
    setPendingPilotFinalize: (value) => events.push(["pending", value]),
    setPendingFinalize: (value) => events.push(["pending", value]),
    setSettlingSession: () => {},
    setPilotEndNotice: (value) => events.push(["notice", value]),
    setEndNotice: (value) => events.push(["notice", value]),
    setLastInterviewId: (value) => events.push(["interview", value]),
    setStep: (value) => events.push(["step", value]),
    setError: (value) => events.push(["error", value]),
    queryClient: { invalidateQueries: () => Promise.resolve() },
    userFacingPublicTalkErrorMessage: () => "Saving failed. Please retry.",
    toast: {
      success: (value) => events.push(["success", value]),
      info: (value) => events.push(["info", value]),
      error: (value) => events.push(["error", value]),
    },
    api: { voiceSessionFinalize: request, publicTalkFinalize: request },
  };
  const body = kind === "pilot"
    ? between(study, "const finalizePilot = useCallback(", "const handleFinished = useCallback(")
    : between(talk, "async function finalizeSession(", "async function handleFinished(");
  const name = kind === "pilot" ? "finalizePilot" : "finalizeSession";
  evaluate(`${body}\nglobalThis.finalize = ${name};`, context);
  return { events, requested, finalize: context.finalize };
}

test("pilot keeps partial interview access and replaces success or too-short toast with interruption", async () => {
  for (const saved of [false, true]) {
    const harness = finalizerHarness("pilot", {
      status: saved ? "completed" : "aborted",
      interview_id: saved ? "synthetic-partial" : null,
    });
    await harness.finalize({ id: "synthetic-session" }, {
      turns: [], duration_ms: 9194, aborted: false, endReason: "connection",
    });
    assert.ok(harness.events.some(([event, text]) => event === "notice" && /connection ended/.test(text)));
    assert.ok(harness.events.some(([event]) => event === "info"));
    assert.equal(harness.events.some(([event]) => event === "success"), false);
    assert.equal(harness.events.some(([event]) => event === "interview"), saved);
    assert.doesNotMatch(JSON.stringify(harness.events), /too short/);
    assert.equal("endReason" in harness.requested[0][1], false, "display metadata never changes settlement input");
  }
});

test("normal manual pilot completion keeps its existing success behavior", async () => {
  const harness = finalizerHarness("pilot", { status: "completed", interview_id: "synthetic-interview" });
  await harness.finalize({ id: "synthetic-session" }, {
    turns: [], duration_ms: 60000, aborted: false, endReason: "user",
  });
  assert.ok(harness.events.some(([event]) => event === "success"));
  assert.ok(harness.events.some(([event, value]) => event === "notice" && value === null));
});

test("participant settlement retains interruption notice for both saved and unsaved outcomes", async () => {
  for (const saved of [false, true]) {
    const harness = finalizerHarness("participant", { status: saved ? "completed" : "aborted" });
    await harness.finalize({ id: "synthetic-session" }, {
      turns: [], duration_ms: 9194, aborted: false, endReason: "connection",
    });
    assert.ok(harness.events.some(([event, value]) => event === "notice" && /connection ended/.test(value)));
    assert.ok(harness.events.some(([event, value]) => event === "step" && value === (saved ? "done" : "discarded")));
    assert.equal("endReason" in harness.requested[0][2], false);
  }
  assert.match(talk, /endNotice[\s\S]*Interview unterbrochen/);
  assert.doesNotMatch(talk, /too short to save|war zu kurz/);
  assert.match(study, /pilotEndNotice &&[\s\S]*role="status"/);
});

test("failed participant saving retains the end reason for a retry without claiming saved work", async () => {
  const harness = finalizerHarness("participant", null, new Error("Synthetic failure"));
  const result = { turns: [], duration_ms: 9194, aborted: false, endReason: "connection" };
  await harness.finalize({ id: "synthetic-session" }, result);
  assert.ok(harness.events.some(([event, value]) => event === "pending" && value?.result === result));
  assert.ok(harness.events.some(([event, value]) => event === "step" && value === "failed"));
  assert.equal(harness.events.some(([event]) => event === "notice"), false);
});
