import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("../src/components/run/control-room-presentation.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const exports = {};
runInNewContext(compiled, { exports });
const { checkpointEstimate, controlStageStatus } = exports;
const stage = {
  id: "screening_title_abstract", status: "active", completed_units: 6,
  total_units: 106, event_count: 4, duration_seconds: 600,
};
const at = (seconds) => new Date(Date.UTC(2026, 8, 4, 12, 0, seconds)).toISOString();
const progress = (id, completed, seconds) => ({
  id, stage: stage.id, event: "screening_progress", created_at: at(seconds), payload: { completed },
});
const snapshot = (activity, overrides = {}) => ({
  status: "running", current_stage: stage.id, stages: [stage], activity, ...overrides,
});

test("one or two records never become a multi-hour stage estimate", () => {
  assert.equal(checkpointEstimate(snapshot([progress(1, 1, 600)])), null);
  assert.equal(checkpointEstimate(snapshot([progress(1, 1, 600), progress(2, 2, 630)])), null);
  assert.equal(checkpointEstimate(snapshot([
    progress(1, 1, 0), progress(2, 2, 30), progress(3, 3, 60),
  ])), null);
});

test("a useful monotonic checkpoint sample gives a current-stage estimate", () => {
  const estimate = checkpointEstimate(snapshot([
    progress(3, 6, 60), progress(2, 3, 30), progress(1, 1, 0),
  ]));
  assert.equal(estimate.etaSeconds, 1200);
  assert.equal(estimate.recordsPerMinute, 5);
  assert.equal(estimate.sampleUnits, 5);
});

test("resume and stage restart discard pre-pause throughput samples", () => {
  const old = [progress(1, 1, 0), progress(2, 3, 30), progress(3, 6, 60)];
  for (const boundary of [
    { id: 4, stage: "control", event: "run_control_cleared", created_at: at(600), payload: {} },
    { id: 4, stage: stage.id, event: "screening_started", created_at: at(600), payload: {} },
  ]) {
    assert.equal(checkpointEstimate(snapshot([...old, boundary, progress(5, 7, 660)])), null);
  }
});

test("invalid samples and stopped run states never expose a future ETA", () => {
  const points = [progress(1, 1, 0), progress(2, 3, 30), progress(3, 6, 60)];
  for (const status of ["paused", "cancelled", "failed", "completed", "awaiting_protocol_approval", "pending"]) {
    assert.equal(checkpointEstimate(snapshot(points, { status })), null);
  }
  assert.equal(checkpointEstimate(snapshot([points[0], { ...points[1], created_at: "invalid" }, points[2]])), null);
  assert.equal(checkpointEstimate(snapshot([progress(1, 6, 0), progress(2, 3, 30), progress(3, 11, 60)])), null);
});

test("terminal and paused run state overrides a stale active-stage snapshot", () => {
  assert.equal(controlStageStatus(stage, "cancelled"), "stopped");
  assert.equal(controlStageStatus(stage, "paused"), "paused");
  assert.equal(controlStageStatus({ ...stage, status: "waiting" }, "cancelled"), "stopped");
  assert.equal(controlStageStatus({ ...stage, status: "completed" }, "cancelled"), "completed");
});

test("the panel refreshes on status transitions and does not reuse unsupported aggregate timings", () => {
  const panel = readFileSync(new URL("../src/components/run/research-control-room.tsx", import.meta.url), "utf8");
  assert.match(panel, /queryKey: \["control-room", run\.id, run\.status\]/);
  assert.match(panel, /run\.status === "completed" \? "Completed review" : stateLabel/);
  assert.match(panel, /running && snapshotCurrent \? checkpointEstimate\(data\) : null/);
  assert.match(panel, /wall-clock elapsed, including pauses/);
  assert.doesNotMatch(panel, /duration\(stage\.duration_seconds\)/);
  assert.doesNotMatch(panel, /data\.(eta_seconds|throughput_per_minute)/);
});
