import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const source = readFileSync("src/app/(app)/interviews/studies/[id]/page.tsx", "utf8");
const callback = source.slice(
  source.indexOf("const startPilot = useCallback("),
  source.indexOf("const finalizePilot = useCallback("),
);
const compiled = ts.transpileModule(`${callback}\nglobalThis.startPilot = startPilot;`, {
  compilerOptions: { target: ts.ScriptTarget.ES2022 },
}).outputText;

function harness(overrides = {}) {
  const calls = [];
  let resolveStart;
  let rejectStart;
  const pending = new Promise((resolve, reject) => {
    resolveStart = resolve;
    rejectStart = reject;
  });
  const context = {
    useCallback: (fn) => fn,
    pilotStartInFlight: { current: false },
    session: null, settlingSession: false, pendingPilotFinalize: null,
    study: { spoken_processing_ready: true }, studyId: "synthetic",
    minLiveSessionMinutes: 30, voiceStudyLimitsAreValid: () => true,
    setStarting: (value) => calls.push(["starting", value]),
    setSession: (value) => calls.push(["session", value]),
    setLastInterviewId: () => {},
    setPilotEndNotice: () => {},
    queryClient: { invalidateQueries: () => {} },
    toast: { error: () => calls.push("error") },
    api: { voiceSessionStart: () => { calls.push("request"); return pending; } },
    ...overrides,
  };
  vm.runInNewContext(compiled, context);
  return { context, calls, resolveStart, rejectStart };
}

test("repeated activation before a render submits one pilot reservation", async () => {
  const run = harness();
  const first = run.context.startPilot();
  await run.context.startPilot();
  assert.equal(run.calls.filter((call) => call === "request").length, 1);
  run.resolveStart({ id: "synthetic-session" });
  await first;
  assert.equal(run.context.pilotStartInFlight.current, false);
});

test("an active pilot or unfinished settlement cannot allocate another pilot", async () => {
  for (const state of [{ session: {} }, { settlingSession: true }, { pendingPilotFinalize: {} }]) {
    const run = harness(state);
    await run.context.startPilot();
    assert.equal(run.calls.length, 0);
  }
});

test("an unapproved spoken scope cannot allocate a pilot", async () => {
  const run = harness({ study: { spoken_processing_ready: false } });
  await run.context.startPilot();
  assert.equal(run.calls.filter((call) => call === "request").length, 0);
  assert.ok(run.calls.includes("error"));
});

test("failed starts release only the local activation lock", async () => {
  const run = harness();
  const first = run.context.startPilot();
  run.rejectStart(new Error("Synthetic failure"));
  await first;
  assert.equal(run.context.pilotStartInFlight.current, false);
  assert.ok(run.calls.includes("error"));
  assert.equal(run.calls.some((call) => Array.isArray(call) && call[0] === "session"), false);
});
