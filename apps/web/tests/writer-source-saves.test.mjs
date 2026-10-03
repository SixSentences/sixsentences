import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../src/lib/writer-source-saves.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
}).outputText;
const { WriterSourceSaves } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
const turn = () => new Promise(setImmediate);
const snapshot = (content, revision = 1) => ({ content, revision, updated_by: null });

/** Manual deterministic debounce clock; no wall-clock waits or network are used. */
function clock() {
  let now = 0, id = 0;
  const jobs = new Map();
  return {
    setTimeout(callback, delay) {
      const handle = ++id;
      jobs.set(handle, { callback, at: now + delay });
      return handle;
    },
    clearTimeout(handle) { jobs.delete(handle); },
    advance(milliseconds) {
      now += milliseconds;
      for (const [handle, job] of [...jobs]) {
        if (job.at <= now && jobs.delete(handle)) job.callback();
      }
    },
    size: () => jobs.size,
  };
}

function fixture(t, options = {}) {
  const timers = clock();
  const requests = [], changes = [], saved = [], errors = [];
  const controller = new WriterSourceSaves({
    timers,
    save(fileId, content, basis) {
      return new Promise((resolve, reject) => {
        requests.push({ fileId, content, basis: { ...basis }, resolve, reject });
      });
    },
    isConflict: error => error?.conflict === true,
    onChange: (fileId, view) => changes.push({ fileId, view }),
    onSaved: (fileId, value) => saved.push({ fileId, value }),
    onError: (fileId, error, view) => errors.push({ fileId, error, view }),
    ...options,
  });
  t.after(() => controller.setActive(false));
  return {
    controller, timers, requests, changes, saved, errors,
    async advance(milliseconds) { timers.advance(milliseconds); await turn(); },
    async resolve(index, value) { assert.ok(requests[index]); requests[index].resolve(value); await turn(); },
    async reject(index, error) { assert.ok(requests[index]); requests[index].reject(error); await turn(); },
  };
}

test("debounce retains the latest draft and captures its owning file", async t => {
  const h = fixture(t), s = h.controller;
  assert.equal(s.get(9), undefined);
  s.observe(9, snapshot("Initial"));
  s.edit(9, "Initial A");
  await h.advance(1199);
  assert.equal(h.requests.length, 0);
  s.edit(9, "Initial A B");
  s.observe(10, snapshot("Other"));
  await h.advance(1199);
  assert.equal(h.requests.length, 0);
  await h.advance(1);
  assert.equal(h.requests.length, 1);
  assert.deepEqual([h.requests[0].fileId, h.requests[0].content, h.requests[0].basis],
    [9, "Initial A B", { content: "Initial", revision: 1 }]);
  await h.resolve(0, snapshot("Initial A B", 2));
  assert.equal(s.get(9).status, "saved");
  assert.equal(s.get(10).content, "Other");
  assert.equal(h.timers.size(), 0);
});

test("late own receipt preserves newer input and serializes its successor", async t => {
  const h = fixture(t), s = h.controller;
  s.observe(0, snapshot("Initial")); s.edit(0, "Initial A");
  await h.advance(1200);
  s.edit(0, "Initial A B"); await h.advance(1200);
  assert.equal(h.requests.length, 1);
  await h.resolve(0, snapshot("Initial A", 2));
  assert.equal(s.get(0).content, "Initial A B");
  assert.notEqual(s.get(0).status, "saved");
  assert.equal(h.requests.length, 2);
  assert.deepEqual(h.requests[1].basis, { content: "Initial A", revision: 2 });
  await h.resolve(1, snapshot("Initial A B", 3));
  assert.equal(s.get(0).status, "saved");
  assert.equal(s.get(0).content, "Initial A B");
});

test("a merged receipt cannot invent a newer draft's comparison basis", async t => {
  const h = fixture(t), s = h.controller;
  s.observe(0, snapshot("Initial")); s.edit(0, "Initial A");
  const flushed = s.flush(0); await turn();
  s.edit(0, "Initial A B");
  await h.resolve(0, { ...snapshot("Initial A + coauthor", 3), merged: true });
  assert.equal(s.get(0).content, "Initial A B");
  assert.deepEqual(h.requests[1].basis, { content: "Initial", revision: 1 });
  await h.resolve(1, snapshot("Initial A B + coauthor", 4)); await flushed;
  assert.equal(s.get(0).content, "Initial A B + coauthor");
  assert.equal(s.get(0).status, "saved");
});

test("unchanged draft accepts the existing server merge result", async t => {
  const h = fixture(t), s = h.controller;
  s.observe(1, snapshot("Base")); s.edit(1, "Local");
  const flushed = s.flush(1); await turn();
  await h.resolve(0, { ...snapshot("Local and remote", 3), merged: true }); await flushed;
  assert.equal(s.get(1).content, "Local and remote");
  assert.deepEqual(s.get(1).basis, { content: "Local and remote", revision: 3 });
});

test("polling a newer source keeps dirty text attached to its genuine base", async t => {
  const h = fixture(t), s = h.controller;
  s.observe(1, snapshot("Base")); s.edit(1, "Local");
  s.observe(1, snapshot("Remote", 2));
  assert.equal(s.get(1).content, "Local");
  assert.equal(s.get(1).revision, 2);
  assert.deepEqual(s.get(1).basis, { content: "Base", revision: 1 });
  const flushed = s.flush(1); await turn();
  assert.deepEqual(h.requests[0].basis, { content: "Base", revision: 1 });
  await h.resolve(0, snapshot("Merged", 3)); await flushed;
  s.observe(1, snapshot("Stale", 1));
  assert.equal(s.get(1).content, "Merged");
});

test("reverting to an old base after a remote update is not falsely saved", async t => {
  const h = fixture(t), s = h.controller;
  s.observe(1, snapshot("Base")); s.edit(1, "Local");
  s.observe(1, snapshot("Remote", 2)); s.edit(1, "Base");
  assert.equal(s.get(1).status, "unsaved");
  const flushed = s.flush(1); await turn();
  assert.deepEqual(h.requests[0].basis, { content: "Base", revision: 1 });
  await h.resolve(0, snapshot("Remote", 3)); await flushed;
  assert.equal(s.get(1).content, "Remote");
});

test("older receipts never downgrade a newer observed revision or cache callback", async t => {
  const h = fixture(t), s = h.controller;
  s.observe(1, snapshot("Base")); s.edit(1, "Local");
  const flushed = s.flush(1); await turn();
  s.observe(1, snapshot("Newer remote", 4));
  await h.resolve(0, snapshot("Local", 2)); await flushed;
  assert.equal(s.get(1).content, "Newer remote");
  assert.equal(s.get(1).revision, 4);
  assert.deepEqual(h.saved, []);
});

test("two flush callers join one serialized stream without duplicate requests", async t => {
  const h = fixture(t), s = h.controller;
  s.observe(1, snapshot("Base")); s.edit(1, "A");
  const first = s.flush(1), second = s.flush(1); await turn();
  assert.equal(h.requests.length, 1);
  s.edit(1, "B"); s.edit(1, "C");
  await h.resolve(0, snapshot("A", 2));
  assert.equal(h.requests.length, 2);
  assert.equal(h.requests[1].content, "C");
  await h.resolve(1, snapshot("C", 3)); await Promise.all([first, second]);
  assert.equal(s.get(1).status, "saved");
});

test("flushAll includes new files and re-edits appearing while other writes await", async t => {
  const h = fixture(t), s = h.controller;
  s.observe(1, snapshot("A")); s.observe(2, snapshot("B"));
  s.edit(1, "A1"); s.edit(2, "B1");
  let done = false;
  const flushed = s.flushAll().then(() => { done = true; }); await turn();
  assert.deepEqual(h.requests.map(request => request.fileId), [1, 2]);
  await h.resolve(0, snapshot("A1", 2));
  s.edit(1, "A2"); s.observe(3, snapshot("C")); s.edit(3, "C1");
  await h.resolve(1, snapshot("B1", 2));
  assert.equal(done, false);
  assert.deepEqual(h.requests.slice(2).map(request => [request.fileId, request.content]), [[1, "A2"], [3, "C1"]]);
  await h.resolve(3, snapshot("C1", 2));
  assert.equal(done, false);
  await h.resolve(2, snapshot("A2", 3)); await flushed;
  assert.ok([1, 2, 3].every(fileId => s.get(fileId).status === "saved"));
});

test("transient failure keeps the draft, stops automatic retry and allows explicit retry", async t => {
  const h = fixture(t), s = h.controller;
  const error = new Error("Synthetic save failed");
  s.observe(1, snapshot("Base")); s.edit(1, "Local");
  const rejected = assert.rejects(s.flush(1), candidate => candidate === error); await turn();
  await h.reject(0, error); await rejected;
  assert.equal(s.get(1).content, "Local");
  assert.equal(s.get(1).status, "unsaved");
  assert.equal(h.errors[0].view.status, "unsaved");
  await h.advance(12000); assert.equal(h.requests.length, 1);
  const retried = s.flush(1); await turn();
  await h.resolve(1, snapshot("Local", 2)); await retried;
  assert.equal(s.get(1).status, "saved");
});

test("conflict blocks queued autosaves until an explicit reviewed basis is chosen", async t => {
  const h = fixture(t), s = h.controller;
  const error = Object.assign(new Error("Synthetic conflict"), { conflict: true });
  s.observe(1, snapshot("Base")); s.edit(1, "Local");
  const rejected = assert.rejects(s.flush(1), candidate => candidate === error); await turn();
  s.edit(1, "Newer local"); await h.reject(0, error); await rejected;
  s.edit(1, "Latest local"); await h.advance(1200);
  await assert.rejects(s.flushAll(), candidate => candidate === error);
  assert.equal(h.requests.length, 1);
  assert.equal(s.get(1).content, "Latest local");
  assert.equal(s.review(1, "Reviewed", snapshot("Remote", 5)), true);
  assert.equal(s.get(1).status, "unsaved");
  const retried = s.flush(1); await turn();
  assert.deepEqual(h.requests[1].basis, { content: "Remote", revision: 5 });
  assert.equal(h.requests[1].content, "Reviewed");
  await h.resolve(1, snapshot("Reviewed", 6)); await retried;
});

test("authoritative replacement and removal cannot race an in-flight write", async t => {
  const h = fixture(t), s = h.controller;
  s.observe(1, snapshot("Base")); s.edit(1, "Local");
  const flushed = s.flush(1); await turn();
  assert.equal(s.accept(1, snapshot("Replacement", 5)), false);
  assert.equal(s.review(1, "Reviewed", snapshot("Remote", 5)), false);
  assert.equal(s.remove(1), false);
  await h.resolve(0, snapshot("Local", 2)); await flushed;
  assert.equal(s.accept(1, snapshot("Stale", 1)), false);
  s.edit(1, "Queued");
  assert.equal(s.accept(1, snapshot("Authoritative", 5)), true);
  assert.equal(h.timers.size(), 0);
  assert.equal(s.get(1).status, "saved");
  assert.equal(s.remove(1), true);
  assert.equal(s.get(1), undefined);
});

test("retrying a closed conflict redisplays the latest local draft without another write", async t => {
  const h = fixture(t), s = h.controller;
  const error = Object.assign(new Error("Synthetic conflict"), { conflict: true });
  s.observe(1, snapshot("Base")); s.edit(1, "Local");
  const rejected = assert.rejects(s.flush(1), candidate => candidate === error); await turn();
  await h.reject(0, error); await rejected;
  assert.equal(h.errors.length, 1);
  s.edit(1, "Latest preserved local");
  await assert.rejects(s.flushAll(), candidate => candidate === error);
  assert.equal(h.errors.length, 2);
  assert.equal(h.errors[1].view.content, "Latest preserved local");
  assert.equal(h.requests.length, 1);
});

test("latest snapshot accessor supports an explicit accept after conflict polling advances", t => {
  const h = fixture(t), s = h.controller;
  assert.equal(s.getLatest(1), undefined);
  s.observe(1, snapshot("Base")); s.edit(1, "Local");
  s.observe(1, snapshot("Newer remote", 5));
  assert.deepEqual(s.get(1).basis, { content: "Base", revision: 1 });
  const latest = s.getLatest(1);
  assert.equal(latest.content, "Newer remote");
  latest.content = "Caller mutation";
  assert.equal(s.getLatest(1).content, "Newer remote");
  assert.equal(s.accept(1, snapshot("Old conflict remote", 3)), false);
  assert.equal(s.accept(1, s.getLatest(1)), true);
  assert.equal(s.get(1).content, "Newer remote");
  assert.equal(s.get(1).status, "saved");
});

test("cleanup cancels dispatch and notifications; StrictMode setup resumes pending edits", async t => {
  const h = fixture(t), s = h.controller;
  s.observe(1, snapshot("Base")); s.edit(1, "Local");
  const count = h.changes.length;
  s.setActive(false); await h.advance(1200);
  assert.equal(h.requests.length, 0); assert.equal(h.changes.length, count);
  assert.equal(s.observe(2, snapshot("Read-only")).content, "Read-only");
  assert.equal(h.changes.length, count);
  s.setActive(true); await h.advance(1200);
  assert.equal(h.requests.length, 1);
  await h.resolve(0, snapshot("Local", 2));
  assert.equal(s.get(1).status, "saved");
});

test("cleanup before the dispatch microtask prevents starting a new transport", async t => {
  const h = fixture(t), s = h.controller;
  s.observe(1, snapshot("Base")); s.edit(1, "Local");
  const flushed = s.flush(1); s.setActive(false);
  await assert.rejects(flushed, /inactive/);
  assert.equal(h.requests.length, 0);
  s.setActive(true); await h.advance(1200);
  await h.resolve(0, snapshot("Local", 2));
});

test("inactive read-only clean sources flush without network while dirty sources remain blocked", async t => {
  const h = fixture(t), s = h.controller;
  s.setActive(false);
  await s.flushAll();
  s.observe(1, snapshot("Read-only source"));
  await s.flush(1); await s.flushAll();
  assert.equal(h.requests.length, 0);
  assert.equal(h.changes.length, 0);
  s.edit(1, "Retained unsaved source");
  await assert.rejects(s.flush(1), /inactive/);
  await assert.rejects(s.flushAll(), /inactive/);
  assert.equal(h.requests.length, 0);
});

test("inactive completion retains newer draft without callbacks or successor dispatch", async t => {
  const h = fixture(t), s = h.controller;
  s.observe(1, snapshot("Base")); s.edit(1, "A"); await h.advance(1200);
  s.edit(1, "B"); s.setActive(false);
  const counts = [h.changes.length, h.saved.length, h.errors.length];
  await h.resolve(0, snapshot("A", 2)); await h.advance(1200);
  assert.deepEqual([h.changes.length, h.saved.length, h.errors.length], counts);
  assert.equal(h.requests.length, 1); assert.equal(s.get(1).content, "B");
  s.setActive(true); await h.advance(1200);
  assert.equal(h.requests[1].content, "B");
  assert.deepEqual(h.requests[1].basis, { content: "A", revision: 2 });
  await h.resolve(1, snapshot("B", 3));
});

test("StrictMode reactivation during an in-flight write still schedules its retained successor", async t => {
  const h = fixture(t), s = h.controller;
  s.observe(1, snapshot("Base")); s.edit(1, "A"); await h.advance(1200);
  s.edit(1, "B"); s.setActive(false); s.setActive(true);
  await h.resolve(0, snapshot("A", 2)); await h.advance(1200);
  assert.equal(h.requests.length, 2); assert.equal(h.requests[1].content, "B");
  await h.resolve(1, snapshot("B", 3));
});

test("finish drains edits before debounce without notifying the departed view", async t => {
  const h = fixture(t), s = h.controller;
  assert.equal(s.hasPending(), false);
  s.observe(1, snapshot("Base")); s.observe(2, snapshot("Other"));
  s.edit(1, "Local"); s.edit(2, "Other local");
  assert.equal(s.hasPending(), true);
  const counts = [h.changes.length, h.saved.length, h.errors.length];
  const finished = s.finish();
  assert.equal(h.timers.size(), 0);
  assert.deepEqual([h.changes.length, h.saved.length, h.errors.length], counts);
  await turn();
  assert.deepEqual(h.requests.map(request => [request.fileId, request.content]), [[1, "Local"], [2, "Other local"]]);
  await h.resolve(1, snapshot("Other local", 2));
  assert.equal(s.hasPending(), true);
  await h.resolve(0, snapshot("Local", 2)); await finished;
  assert.equal(s.hasPending(), false);
  assert.deepEqual([h.changes.length, h.saved.length, h.errors.length], counts);
  await h.advance(1200); assert.equal(h.requests.length, 2);
});

test("finish joins a held write and drains its newer draft with the acknowledged basis", async t => {
  const h = fixture(t), s = h.controller;
  s.observe(1, snapshot("Base")); s.edit(1, "A"); await h.advance(1200);
  s.edit(1, "A B");
  const counts = [h.changes.length, h.saved.length, h.errors.length];
  const finished = s.finish(); await turn();
  assert.equal(h.requests.length, 1);
  await h.resolve(0, snapshot("A", 2));
  assert.equal(h.requests.length, 2);
  assert.equal(h.requests[1].content, "A B");
  assert.deepEqual(h.requests[1].basis, { content: "A", revision: 2 });
  await h.resolve(1, snapshot("A B", 3)); await finished;
  assert.equal(s.hasPending(), false);
  assert.deepEqual([h.changes.length, h.saved.length, h.errors.length], counts);
});

test("one detached failure does not cancel another file's acknowledged successor", async t => {
  const h = fixture(t), s = h.controller;
  s.observe(1, snapshot("A")); s.observe(2, snapshot("B"));
  s.edit(1, "A1"); s.edit(2, "B1"); await h.advance(1200);
  s.edit(2, "B1 B2");
  const error = new Error("Synthetic first-file failure");
  let finished = false;
  const outcome = s.finish().then(
    () => { finished = true; return null; },
    failure => { finished = true; return failure; },
  );
  await h.reject(0, error);
  assert.equal(finished, false);
  await h.resolve(1, snapshot("B1", 2));
  assert.equal(h.requests.length, 3);
  assert.equal(h.requests[2].fileId, 2);
  assert.equal(h.requests[2].content, "B1 B2");
  assert.deepEqual(h.requests[2].basis, { content: "B1", revision: 2 });
  await h.resolve(2, snapshot("B1 B2", 3));
  assert.equal(await outcome, error);
  assert.equal(s.get(1).status, "unsaved");
  assert.equal(s.get(2).status, "saved");
  assert.equal(h.saved.length, 0);
  assert.equal(h.errors.length, 0);
});

for (const conflict of [false, true]) {
  test(`finish settles a ${conflict ? "conflict" : "transport failure"} without callbacks or retries`, async t => {
    const h = fixture(t), s = h.controller;
    s.observe(1, snapshot("Base")); s.edit(1, "Local");
    const error = Object.assign(new Error("Synthetic detached failure"), { conflict });
    const counts = [h.changes.length, h.saved.length, h.errors.length];
    const failed = assert.rejects(s.finish(), candidate => candidate === error); await turn();
    await h.reject(0, error); await failed; await h.advance(12000);
    assert.equal(s.hasPending(), true);
    assert.equal(s.get(1).content, "Local");
    assert.equal(h.requests.length, 1);
    assert.deepEqual([h.changes.length, h.saved.length, h.errors.length], counts);
    s.setActive(true);
    assert.equal(h.errors.length, counts[2] + 1);
    assert.equal(h.errors.at(-1).error, error);
  });
}

test("finish never reopens an already reported conflict in the departed view", async t => {
  const h = fixture(t), s = h.controller;
  s.observe(1, snapshot("Base")); s.edit(1, "Local");
  const error = Object.assign(new Error("Synthetic reported conflict"), { conflict: true });
  const failed = assert.rejects(s.flush(1), candidate => candidate === error); await turn();
  await h.reject(0, error); await failed;
  assert.equal(h.errors.length, 1);
  s.edit(1, "Latest retained draft");
  const counts = [h.changes.length, h.saved.length, h.errors.length];
  await assert.rejects(s.finish(), candidate => candidate === error);
  assert.deepEqual([h.changes.length, h.saved.length, h.errors.length], counts);
  assert.equal(h.requests.length, 1);
  assert.equal(s.get(1).content, "Latest retained draft");
});

test("hard deactivation before detached dispatch prevents any transport", async t => {
  const h = fixture(t), s = h.controller;
  s.observe(1, snapshot("Base")); s.edit(1, "Local");
  const finished = s.finish(); s.setActive(false);
  await assert.rejects(finished, /inactive/);
  assert.equal(h.requests.length, 0);
  assert.equal(s.hasPending(), true);
  await assert.rejects(s.finish(), /inactive/);
  assert.equal(h.requests.length, 0);
});

test("hard deactivation while finish awaits a write forbids its queued successor", async t => {
  const h = fixture(t), s = h.controller;
  s.observe(1, snapshot("Base")); s.edit(1, "A"); await h.advance(1200);
  s.edit(1, "A B");
  const failed = assert.rejects(s.finish(), /inactive/);
  s.setActive(false);
  await h.resolve(0, snapshot("A", 2)); await failed; await h.advance(1200);
  assert.equal(h.requests.length, 1);
  assert.equal(s.get(1).content, "A B");
  assert.equal(s.hasPending(), true);
});

test("StrictMode reattachment keeps notifications and dispatch after finish settles", async t => {
  const h = fixture(t), s = h.controller;
  s.observe(1, snapshot("Base")); s.edit(1, "A");
  const finished = s.finish(); s.setActive(true); await turn();
  const changes = h.changes.length;
  await h.resolve(0, snapshot("A", 2)); await finished;
  assert.ok(h.changes.length > changes);
  assert.equal(h.saved.length, 1);
  s.edit(1, "A B"); await h.advance(1200);
  assert.equal(h.requests.length, 2);
  await h.resolve(1, snapshot("A B", 3));
  assert.equal(s.hasPending(), false);
  assert.equal(h.saved.length, 2);
});

test("StrictMode reattachment replays receipts completed during detached draining", async t => {
  const h = fixture(t), s = h.controller;
  s.observe(1, snapshot("A")); s.observe(2, snapshot("B"));
  s.edit(1, "A1"); s.edit(2, "B1");
  const finished = s.finish(); await turn();
  await h.resolve(0, snapshot("A1", 2));
  assert.equal(h.saved.length, 0);
  s.setActive(true);
  assert.deepEqual(h.saved.map(item => item.fileId), [1]);
  await h.resolve(1, snapshot("B1", 2)); await finished;
  assert.deepEqual(h.saved.map(item => item.fileId), [1, 2]);
  s.edit(1, "A2"); await h.advance(1200);
  assert.equal(h.requests[2].content, "A2");
  await h.resolve(2, snapshot("A2", 3));
});

test("view and cache callbacks cannot turn success into a failed write", async t => {
  const h = fixture(t, {
    onChange() { throw new Error("Synthetic view callback failure"); },
    onSaved() { throw new Error("Synthetic cache callback failure"); },
    onError() { throw new Error("Synthetic error callback failure"); },
  }), s = h.controller;
  s.observe(1, snapshot("Base")); s.edit(1, "Local");
  const flushed = s.flush(1); await turn();
  await h.resolve(0, snapshot("Local", 2)); await flushed;
  assert.equal(s.get(1).status, "saved");
  s.edit(1, "Again");
  const error = new Error("Actual transport failure");
  const rejected = assert.rejects(s.flush(1), candidate => candidate === error); await turn();
  await h.reject(1, error); await rejected;
  assert.equal(s.get(1).content, "Again");
});

test("callers cannot mutate a returned basis or snapshot into another file's state", t => {
  const h = fixture(t), s = h.controller;
  const original = snapshot("Base"); s.observe(1, original); original.content = "Mutated outside";
  const view = s.get(1); view.basis.content = "Mutated returned basis";
  assert.equal(s.get(1).content, "Base");
  assert.equal(s.get(1).basis.content, "Base");
  s.observe(2, snapshot("Other")); s.edit(2, "Other local");
  assert.equal(s.get(1).status, "saved");
});
