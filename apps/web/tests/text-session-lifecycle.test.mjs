import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

// Source-backed lifecycle regression fixtures, not a provider or browser test.
const source = readFileSync("src/components/voice/text-session.tsx", "utf8");
const start = source.indexOf("export default function TextSession(");
const end = source.indexOf("  return (\n    <div", start);
assert.ok(start >= 0 && end > start);

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function harness() {
  const effects = [];
  const changes = [];
  const requests = [];
  const results = [];
  const errors = [];
  let stateIndex = 0;
  const context = {
    exports: {},
    performance: { now: () => 100 },
    window: { setTimeout: () => 1, clearTimeout: () => {} },
    KICKOFF_DE: "Synthetic kickoff", KICKOFF_EN: "Synthetic kickoff",
    ApiError: class extends Error {},
    useCallback: (callback) => callback,
    useRef: (value) => ({ current: value }),
    useEffect: (callback) => effects.push(callback),
    useState: (value) => {
      const index = stateIndex++;
      // Simulate a ready render; only refs can block two events in this render.
      const initial = index === 1 ? false : index === 2 ? "Synthetic answer" : value;
      return [initial, (next) => changes.push([index, next])];
    },
  };
  const body = source.slice(start, end) +
    "  return { send, finish, turns: () => state.current.turns };\n}";
  const compiled = ts.transpileModule(body, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  vm.runInNewContext(compiled, context);
  const session = context.exports.default({
    config: { language: "en", max_session_minutes: 30 },
    sendMessage: (message, history) => {
      const response = deferred();
      requests.push({ message, history, ...response });
      return response.promise;
    },
    onFinished: (result) => results.push(result),
    onError: (message) => errors.push(message),
  });
  const cleanups = effects.map((effect) => effect());
  return {
    ...session, requests, results, errors, changes,
    unmount: () => cleanups.forEach((cleanup) => cleanup?.()),
    replayEffects: () => effects.forEach((effect) => effect()),
    flush: async () => { for (let i = 0; i < 6; i++) await Promise.resolve(); },
  };
}

test("kickoff and a synchronous duplicate submit cannot start parallel turns", async () => {
  const session = harness();
  await session.send();
  assert.equal(session.requests.length, 1, "kickoff owns the same in-flight guard");
  session.requests[0].resolve("First question");
  await session.flush();
  const first = session.send();
  const duplicate = session.send();
  assert.equal(session.requests.length, 2);
  assert.equal(session.turns().filter((turn) => turn.role === "participant").length, 1);
  session.requests[1].resolve("Next question");
  await Promise.all([first, duplicate]);
  assert.equal(session.turns().length, 3);
  session.unmount();
});

test("finish snapshots are isolated and a late kickoff cannot append to them", async () => {
  const session = harness();
  session.finish();
  session.finish();
  assert.equal(session.results.length, 1);
  session.requests[0].resolve("Late question");
  await session.flush();
  assert.equal(session.turns().length, 0);
  assert.equal(session.results[0].turns.length, 0);
  session.turns().push({ role: "participant", text: "Later mutation" });
  assert.equal(session.results[0].turns.length, 0);
  session.unmount();
});

test("late kickoff and normal request errors have no effects after unmount", async () => {
  for (const duringKickoff of [true, false]) {
    const session = harness();
    let pending;
    if (!duringKickoff) {
      session.requests[0].resolve("First question");
      await session.flush();
      pending = session.send();
    }
    session.unmount();
    const before = session.changes.length;
    session.requests.at(-1).reject(new Error("Synthetic network failure"));
    await pending;
    await session.flush();
    assert.equal(session.changes.length, before);
    assert.equal(session.errors.length, 0);
    assert.equal(session.results.length, 0);
  }
});

test("an effect replay retains the one kickoff and accepts its valid response", async () => {
  const session = harness();
  session.unmount();
  session.replayEffects();
  assert.equal(session.requests.length, 1);
  session.requests[0].resolve("First question");
  await session.flush();
  assert.equal(session.turns().length, 1);
  session.unmount();
});
