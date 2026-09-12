import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";

const controls = fs.readFileSync(new URL("../src/components/run/run-controls.tsx", import.meta.url), "utf8");
const statuses = fs.readFileSync(new URL("../src/lib/status.ts", import.meta.url), "utf8");

function cancellationHarness() {
  const run = { id: 42, public_id: "synthetic-cancel", status: "running" };
  const cached = [{ ...run }, { ...run }, { id: 43, status: "running" }];
  const messages = [];
  let refreshes = 0;
  const queryClient = {
    setQueriesData(filter, updater) {
      assert.deepEqual(filter.queryKey, ["run"]);
      cached.forEach((current, index) => { cached[index] = updater(current); });
    },
  };
  const mutation = controls.slice(controls.indexOf("const cancel = useMutation"), controls.indexOf("if (isTerminal(run.status))"));
  const executable = ts.transpileModule(`${mutation}\nreturn cancel;`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;
  const config = new Function("useMutation", "api", "queryClient", "run", "toast", "refresh", executable)(
    value => value,
    { cancelRun: async id => ({ status: "cancelled", run_id: id }) },
    queryClient, run,
    { success: message => messages.push(message), error: message => messages.push(message) },
    () => { refreshes += 1; },
  );
  return { config, cached, messages, get refreshes() { return refreshes; } };
}

test("cancellation remains pending until the server acknowledges its terminal state", async () => {
  const harness = cancellationHarness();
  assert.equal(harness.config.onMutate, undefined);
  assert.deepEqual(harness.cached.map(run => run.status), ["running", "running", "running"]);
  const result = await harness.config.mutationFn();
  harness.config.onSuccess(result);
  assert.deepEqual(harness.cached.map(run => run.status), ["cancelled", "cancelled", "running"]);
  assert.equal(harness.cached[0].finished_at, undefined);
  assert.equal(harness.refreshes, 1);
});

test("a rejected cancellation never fabricates a cancelled run", () => {
  const harness = cancellationHarness();
  harness.config.onError(new Error("Request could not be completed"));
  assert.deepEqual(harness.cached.map(run => run.status), ["running", "running", "running"]);
  assert.equal(harness.refreshes, 1);
});

test("pending copy and neutral cancellation remain distinct from genuine failures", () => {
  assert.match(controls, /cancel\.isPending &&[\s\S]*role="status"[\s\S]*Stopping…/);
  assert.match(controls, /disabled=\{pause\.isPending \|\| cancel\.isPending\}/);
  assert.match(controls, /disabled=\{resume\.isPending \|\| cancel\.isPending\}/);
  assert.doesNotMatch(controls, /stop immediately|finished_at: new Date/);
  assert.match(controls, /in-flight step may take a moment/);
  assert.match(statuses, /cancelled: \{\s*label: "Cancelled",[\s\S]*?badge: "bg-secondary text-muted-foreground"/);
  assert.match(statuses, /failed: \{\s*label: "Failed",[\s\S]*?badge: "bg-destructive\/10 text-destructive"/);
});
