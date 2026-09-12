import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import ts from "typescript";

const source = await readFile(
  new URL("../src/lib/agent-event-lifecycle.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
}).outputText;
const lifecycle = await import(
  `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`
);

test("checkpoint lifecycle frames share one stable card", () => {
  const events = [
    { id: 1, event: "checkpoint.started", call_id: "review-1", label: "Inspect the completed tool results" },
    { id: 2, event: "checkpoint.progress", call_id: "review-1", label: "Reviewing the result" },
    { id: 3, event: "checkpoint.completed", call_id: "review-1", label: "Result review complete" },
  ];
  const stable = lifecycle.stabilizeAgentEventLedger(events);
  assert.equal(stable.length, 1);
  assert.equal(stable[0].label, "Inspect the completed tool results");
  assert.deepEqual(stable[0].frames.map((event) => event.event), [
    "checkpoint.started",
    "checkpoint.progress",
    "checkpoint.completed",
  ]);
});

test("a failed checkpoint closes its call id before a later review", () => {
  const stable = lifecycle.stabilizeAgentEventLedger([
    { id: 1, event: "checkpoint.started", call_id: "review-1" },
    { id: 2, event: "checkpoint.failed", call_id: "review-1", lifecycle: "failed" },
    { id: 3, event: "checkpoint.started", call_id: "review-1" },
  ]);
  assert.equal(stable.length, 2);
  assert.deepEqual(stable[0].frames.map((event) => event.event), [
    "checkpoint.started",
    "checkpoint.failed",
  ]);
  assert.deepEqual(stable[1].frames.map((event) => event.event), ["checkpoint.started"]);
});

test("only recoverable verification misses use the review presentation", () => {
  assert.equal(
    lifecycle.isRecoverableReview({ id: 1, event: "checkpoint.failed" }),
    true,
  );
  assert.equal(
    lifecycle.isRecoverableReview({
      id: 2,
      event: "tool.failed",
      tool: "manuscript.compile_candidate",
    }),
    true,
  );
  assert.equal(
    lifecycle.isRecoverableReview({
      id: 3,
      event: "tool.failed",
      tool: "manuscript.read_source",
    }),
    false,
  );
  assert.equal(
    lifecycle.isRecoverableReview({ id: 4, event: "turn.failed" }),
    false,
  );
});

test("a one-shot checkpoint progress receipt is already terminal", () => {
  const stable = lifecycle.stabilizeAgentEventLedger([
    { id: 1, event: "checkpoint.progress", call_id: "review-1", lifecycle: "completed" },
    { id: 2, event: "checkpoint.started", call_id: "review-1", lifecycle: "started" },
  ]);
  assert.equal(stable.length, 2);
  assert.deepEqual(stable.map((event) => event.frames.length), [1, 1]);
});

test("a terminal turn cannot leave an earlier card running", () => {
  assert.equal(
    lifecycle.resolveTerminalToolState("running", { id: 2, event: "turn.completed" }),
    "completed",
  );
  assert.equal(
    lifecycle.resolveTerminalToolState("running", { id: 2, event: "turn.cancelled" }),
    "cancelled",
  );
  assert.equal(
    lifecycle.resolveTerminalToolState("running", { id: 2, event: "turn.failed" }),
    "failed",
  );
  assert.equal(lifecycle.resolveTerminalToolState("running", undefined), "running");
});

test("a terminal turn suppresses thinking after an orphaned checkpoint start", () => {
  const events = [
    {
      id: 1,
      event: "checkpoint.started",
      call_id: "orphaned-review",
      lifecycle: "started",
    },
    { id: 2, event: "turn.completed" },
  ];
  const stable = lifecycle.stabilizeAgentEventLedger(events);
  assert.equal(stable[0].event, "checkpoint.started");
  assert.equal(
    lifecycle.resolveTerminalToolState(
      "running",
      lifecycle.terminalAgentEvent(events),
    ),
    "completed",
  );
  assert.equal(lifecycle.shouldShowAgentThinking(events, true, false), false);
});
