import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test as nodeTest } from "node:test";
import * as jsxRuntime from "react/jsx-runtime";
import { MutationObserver, QueryClient } from "@tanstack/react-query";
import ts from "typescript";

function test(name, body) {
  return nodeTest(name, { timeout: 5_000 }, body);
}

const page = await readFile(new URL(
  "../src/app/(app)/writer/[id]/page.tsx", import.meta.url,
), "utf8");
const helper = await readFile(new URL("../src/lib/writer-source-saves.ts", import.meta.url), "utf8");
const apiSource = await readFile(new URL("../src/lib/api.ts", import.meta.url), "utf8");
const ast = ts.createSourceFile("writer.tsx", page, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);

function matching(root, predicate) {
  const found = [];
  function visit(node) {
    if (predicate(node)) found.push(node);
    ts.forEachChild(node, visit);
  }
  visit(root);
  return found;
}

function exactlyOne(nodes, description) {
  assert.equal(nodes.length, 1, `Unambiguous production ${description}`);
  return nodes[0];
}

const workspace = exactlyOne(matching(ast, (node) => ts.isFunctionDeclaration(node)
  && node.name?.text === "WriterEditorWorkspace"), "WriterEditorWorkspace");
const wrapper = exactlyOne(matching(ast, (node) => ts.isFunctionDeclaration(node)
  && node.name?.text === "WriterEditorPage"), "document identity wrapper");
const snapshotList = exactlyOne(matching(ast, (node) => ts.isFunctionDeclaration(node)
  && node.name?.text === "SnapshotList"), "SnapshotList");

function declaration(name) {
  return exactlyOne(matching(workspace, (node) => ts.isVariableDeclaration(node)
    && (node.name.getText(ast) === name
      || (ts.isArrayBindingPattern(node.name) && node.name.elements[0]?.getText(ast) === name))), name);
}

function option(name) {
  const initializer = declaration(name).initializer;
  assert.ok(ts.isCallExpression(initializer), `${name} is a production hook call`);
  assert.equal(initializer.arguments.length > 0, true);
  return initializer.arguments[0].getText(ast);
}

function transpile(source) {
  return ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  }).outputText;
}

function evaluate(expression, bindings) {
  const exports = {};
  new Function("exports", ...Object.keys(bindings), transpile(`exports.value = (${expression});`))(
    exports, ...Object.values(bindings),
  );
  return exports.value;
}

const apiAst = ts.createSourceFile("api.ts", apiSource, ts.ScriptTarget.Latest, true);
const apiErrorNode = exactlyOne(matching(apiAst, (node) => ts.isClassDeclaration(node)
  && node.name?.text === "ApiError"), "ApiError class");
const apiExports = {};
new Function("exports", transpile(apiErrorNode.getText(apiAst)))(apiExports);
const { ApiError } = apiExports;
const errorConstructor = exactlyOne(apiErrorNode.members.filter(ts.isConstructorDeclaration), "ApiError constructor");
const errorDetailIndex = errorConstructor.parameters.findIndex((parameter) => parameter.name.getText(apiAst) === "detail");
assert.ok(errorDetailIndex >= 2, "each edition exposes the actual structured error detail argument");

const initialFiles = () => [
  { id: 0, path: "main.tex", main: true, content: "Main initial", revision: 1, updated_by: null },
  { id: 11, path: "chapter.tex", main: false, content: "Chapter initial", revision: 1, updated_by: null },
];
const receipt = (content, revision = 2, extra = {}) => ({ content, revision, updated_by: null, ...extra });
const tick = () => new Promise(setImmediate);
const LIFECYCLE = 'window.addEventListener("beforeunload"';

// Execute the real page initializer/callbacks and the complete production helper.
// Only IO, timers and React state storage are synthetic. This is not a browser or
// React commit-timing test: each callback receives its render's lexical values.
function harness(t, {
  docId = "synthetic-document", activeFile = 0, canEdit = true,
  filesFirst = false, compileStatus = "none",
} = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const timers = new Map();
  const requests = [];
  const pending = [];
  const unexpected = [];
  const messages = [];
  const mutations = new Map();
  const effects = [];
  const listeners = new Map();
  let timerId = 0;
  let token = "synthetic-session-not-a-real-credential";
  const getToken = () => token;
  let projectFiles = initialFiles();
  let serverFiles = initialFiles();
  const state = {
    content: null, saveState: "saved", activeFileId: 0, sourceConflict: null,
    title: "", sourceOperationBusy: false, pdfUrl: null, newFileName: "new.tex",
  };
  const refs = {
    activeFileIdRef: { current: 0 },
    canEditRef: { current: canEdit },
    sourceOperationBusyRef: { current: false },
    previousStatus: { current: null },
    documentInitializedRef: { current: evaluate(option("documentInitializedRef"), {}) },
    deleteFileInFlightRef: { current: false },
    sourceTokenRef: { current: evaluate(option("sourceTokenRef"), { getToken }) },
    sourceAccessRef: { current: canEdit },
    sourceAuthBoundaryRef: { current: evaluate(option("sourceAuthBoundaryRef"), {}) },
  };
  const doc = { id: docId, title: "Synthetic", content: "Main initial", revision: 1, compile_status: compileStatus };
  client.setQueryData(["writer-doc", docId], doc);
  client.setQueryData(["writer-files", docId], projectFiles);

  function defer(kind, fileId, source, basis) {
    const index = pending.length;
    const request = { kind, docId, fileId, source, basis: structuredClone(basis) };
    requests.push(request);
    return new Promise((resolve, reject) => pending.push({ index, request, resolve, reject, settled: false }));
  }
  function deferOperation(kind, body) {
    const request = { kind, docId, ...structuredClone(body) };
    requests.push(request);
    return new Promise((resolve, reject) => pending.push({ request, resolve, reject, settled: false }));
  }
  const api = new Proxy({
    writerPatch(id, body) {
      assert.equal(id, docId);
      if (!("content" in body)) {
        requests.push({ kind: "metadata", docId: id, body: structuredClone(body) });
        return Promise.resolve({ ...doc, ...body });
      }
      return defer("writerPatch", 0, body.content, { revision: body.expected_revision, content: body.base_content });
    },
    writerFilePatch(id, fileId, source, revision, content) {
      assert.equal(id, docId);
      return defer("writerFilePatch", fileId, source, { revision, content });
    },
    writerCompile(id) {
      assert.equal(id, docId);
      requests.push({ kind: "compile", docId: id });
      return Promise.resolve({ status: "pending" });
    },
    writerRestore: (id, snapshotId) => {
      assert.equal(id, docId);
      return deferOperation("restore", { snapshotId });
    },
    writerFiles: (id) => {
      assert.equal(id, docId);
      requests.push({ kind: "files", docId: id });
      return serverFiles instanceof Error ? Promise.reject(serverFiles) : Promise.resolve(structuredClone(serverFiles));
    },
    writerApplyEdits: (id, edits, options) => {
      assert.equal(id, docId);
      return deferOperation("apply", { edits, options });
    },
    writerFileRename: (id, fileId, path) => {
      assert.equal(id, docId);
      return deferOperation("rename", { fileId, path });
    },
    writerFileDelete: (id, fileId) => {
      assert.equal(id, docId);
      return deferOperation("delete", { fileId });
    },
    writerFileCreate: (id, path) => {
      assert.equal(id, docId);
      return deferOperation("create", { path });
    },
  }, {
    get(target, name) {
      if (name in target) return target[name];
      return () => { unexpected.push(String(name)); throw new Error(`Unexpected API call: ${String(name)}`); };
    },
  });
  const helperExports = {};
  new Function("exports", "setTimeout", "clearTimeout", transpile(helper))(
    helperExports,
    (callback, delay) => { const id = ++timerId; timers.set(id, { callback, delay }); return id; },
    (id) => timers.delete(id),
  );
  const setters = Object.fromEntries([
    "Content", "SaveState", "ActiveFileId", "SourceConflict", "Title", "SourceOperationBusy", "PdfUrl",
    "DeleteFileTarget", "RenameFile", "RenameFilePath",
    "NewFileName", "ToolbarPanel",
  ].map((name) => [
    `set${name}`, (value) => {
      const key = name[0].toLowerCase() + name.slice(1);
      state[key] = typeof value === "function" ? value(state[key]) : value;
    },
  ]));
  let sourceSaves;
  function bindings() {
    return {
      ...refs, ...setters, ...state, doc, docId, projectFiles,
      canEdit,
      sourceSaves, queryClient: client, api, ApiError,
      getToken,
      window: {
        addEventListener(name, listener) {
          if (!listeners.has(name)) listeners.set(name, new Set());
          listeners.get(name).add(listener);
        },
        removeEventListener(name, listener) { listeners.get(name)?.delete(listener); },
      },
      WriterSourceSaves: helperExports.WriterSourceSaves,
      scheduleAutoCompile: () => messages.push("scheduleAutoCompile"),
      setView: (value) => messages.push(`view:${value}`),
      fetchWriterPdfUrl: (id) => { messages.push({ pdf: id }); return Promise.resolve("blob:synthetic-pdf"); },
      toast: Object.fromEntries(["success", "error", "message"].map((level) => [level, (value) => messages.push({ level, value })])),
    };
  }
  const initializer = declaration("sourceSaves").initializer;
  assert.ok(ts.isCallExpression(initializer) && initializer.expression.getText(ast) === "useState",
    "controller must be retained by the production state initializer");
  sourceSaves = evaluate(option("sourceSaves"), bindings())();

  function callback(name) {
    const values = bindings();
    if (name !== "withSourceOperation") values.withSourceOperation = callback("withSourceOperation");
    return evaluate(option(name), values);
  }
  function runEffect(fragment) {
    const candidates = matching(workspace, (node) => ts.isCallExpression(node)
      && node.expression.getText(ast) === "useEffect"
      && node.arguments[0]?.getText(ast).includes(fragment));
    const node = exactlyOne(candidates, `effect containing ${fragment}`);
    const cleanup = evaluate(node.arguments[0].getText(ast), bindings())();
    if (typeof cleanup === "function") effects.push(cleanup);
    return cleanup;
  }
  function mutation(name) {
    const values = bindings();
    values.flushActiveSource = callback("flushActiveSource");
    values.withSourceOperation = callback("withSourceOperation");
    values.openProjectFile = callback("openProjectFile");
    const options = evaluate(option(name), values);
    let observer = mutations.get(name);
    if (!observer) {
      observer = new MutationObserver(client, options);
      mutations.set(name, observer);
    } else observer.setOptions(options);
    return observer;
  }
  function snapshotMutation(index) {
    const instances = matching(workspace, (node) => ts.isJsxSelfClosingElement(node)
      && node.tagName.getText(ast) === "SnapshotList");
    assert.equal(instances.length, 2, "both real version-history surfaces are covered");
    const attribute = exactlyOne(instances[index].attributes.properties.filter((node) => ts.isJsxAttribute(node)
      && node.name.text === "onRestored"), "version-history onRestored callback");
    const values = {
      ...bindings(),
      autoRef: { current: true },
      compileNowRef: { current: () => messages.push("compileAfterRestore") },
      restoreSource: callback("restoreSource"),
    };
    values.onRestored = evaluate(attribute.initializer.expression.getText(ast), values);
    const restore = exactlyOne(matching(snapshotList, (node) => ts.isVariableDeclaration(node)
      && node.name.getText(ast) === "restore"), "SnapshotList restore mutation");
    const options = evaluate(restore.initializer.arguments[0].getText(ast), values);
    return new MutationObserver(client, options);
  }
  function select(fileId) {
    const file = projectFiles.find((entry) => entry.id === fileId);
    assert.ok(file);
    callback("openProjectFile")(file);
  }
  function observeFiles(files) {
    projectFiles = files;
    runEffect("sourceSaves.observe(file.id, file)");
  }
  let deactivate = runEffect(LIFECYCLE);
  if (filesFirst) observeFiles(projectFiles);
  runEffect("sourceSaves.observe(0, doc)");
  if (!filesFirst) observeFiles(projectFiles);
  if (activeFile !== 0) select(activeFile);
  t.after(async () => {
    // Revoke this synthetic session before teardown: deliberately dirty negative
    // fixtures must not start an unobserved final write after the test ends.
    token = null;
    refs.canEditRef.current = false;
    refs.sourceAccessRef.current = false;
    sourceSaves.setActive(false);
    for (const cleanup of effects.reverse()) cleanup();
    await tick();
    client.clear();
    assert.deepEqual(unexpected, [], "callbacks must not hide unexpected IO behind notification isolation");
    assert.equal(timers.size, 0, "controller cleanup removes all debounce timers");
    assert.ok(pending.every((entry) => entry.settled), "every synthetic transport must settle");
    assert.ok([...listeners.values()].every((entries) => entries.size === 0), "all page listeners are removed");
  });

  return {
    state, refs, requests, pending, timers, messages, client, sourceSaves, docId,
    callback, runEffect, mutation, snapshotMutation, select, observeFiles,
    deactivate: () => deactivate(),
    setPermission(value) {
      deactivate();
      canEdit = value;
      deactivate = runEffect(LIFECYCLE);
    },
    setToken(value) { token = value; },
    setServerFiles(value) { serverFiles = value; },
    listenerCount(name) { return listeners.get(name)?.size ?? 0; },
    dispatch(name, event = {}) {
      for (const listener of [...(listeners.get(name) ?? [])]) listener(event);
      return event;
    },
    bindings,
    edit: (next) => callback("scheduleSave")(next),
    sourceRequests: () => requests.filter((entry) => entry.kind === "writerPatch" || entry.kind === "writerFilePatch"),
    async fireTimers() {
      const scheduled = [...timers.entries()];
      assert.ok(scheduled.length, "actual controller scheduled a debounce");
      for (const [id, { callback: fire, delay }] of scheduled) {
        assert.equal(delay, 1200);
        timers.delete(id);
        fire();
      }
      await tick();
    },
    async settle(index, result, error = false) {
      await tick();
      const request = pending[index];
      assert.ok(request && !request.settled, "actual source transport must be pending");
      request.settled = true;
      if (error) request.reject(result); else request.resolve(result);
      await tick();
    },
  };
}

test("the real document wrapper gives each document its own controller lifetime", () => {
  const exports = {};
  const code = transpile(`${wrapper.getText(ast).replace("export default ", "")}\nexports.render = WriterEditorPage;`);
  let id = "synthetic-a";
  new Function("exports", "require", "useParams", "WriterEditorWorkspace", code)(
    exports, (name) => { assert.equal(name, "react/jsx-runtime"); return jsxRuntime; },
    () => ({ id }), "WriterEditorWorkspace",
  );
  const a = exports.render();
  id = "synthetic-b";
  const b = exports.render();
  assert.equal(a.props.docId, "synthetic-a");
  assert.equal(b.props.docId, "synthetic-b");
  assert.equal(a.key, "synthetic-a");
  assert.equal(b.key, "synthetic-b");
});

for (const fileId of [0, 11]) {
  test(`file ${fileId}: acknowledged old save cannot erase subsequent typing`, async (t) => {
    const h = harness(t, { activeFile: fileId });
    const initial = h.state.content;
    h.edit(`${initial} A`);
    await h.fireTimers();
    h.edit(`${initial} A B`);
    await h.settle(0, receipt(`${initial} A`));
    assert.equal(h.state.content, `${initial} A B`);
    assert.equal(h.state.saveState, "unsaved");
    h.edit(`${h.state.content} C`);
    await h.fireTimers();
    assert.deepEqual(h.sourceRequests().map(({ fileId, source, basis }) => ({ fileId, source, basis })), [
      { fileId, source: `${initial} A`, basis: { revision: 1, content: initial } },
      { fileId, source: `${initial} A B C`, basis: { revision: 2, content: `${initial} A` } },
    ]);
    await h.settle(1, receipt(`${initial} A B C`, 3));
    assert.equal(h.state.content, `${initial} A B C`);
    assert.equal(h.state.saveState, "saved");
    const cache = h.client.getQueryData(["writer-files", h.docId]).find((file) => file.id === fileId);
    assert.equal(cache.content, h.state.content);
    assert.equal(cache.revision, 3);
    if (fileId === 0) assert.equal(h.client.getQueryData(["writer-doc", h.docId]).content, h.state.content);
  });
}

test("file switches retain drafts and unrelated receipts never relabel the active file", async (t) => {
  const h = harness(t);
  h.edit("Main edited");
  await h.fireTimers();
  h.select(11);
  h.edit("Chapter edited");
  await h.settle(0, receipt("Main edited"));
  assert.equal(h.state.content, "Chapter edited");
  assert.equal(h.state.saveState, "unsaved");
  assert.equal(h.sourceRequests().length, 1, "switching after an expired timer does not duplicate its write");
  h.select(0);
  assert.equal(h.state.content, "Main edited");
  assert.equal(h.state.saveState, "saved");
  h.select(11);
  assert.equal(h.state.content, "Chapter edited");
  await h.fireTimers();
  assert.equal(h.sourceRequests()[1].fileId, 11);
  await h.settle(1, receipt("Chapter edited"));
});

test("document flush joins in-flight writes and drains the latest drafts of every file", async (t) => {
  const h = harness(t);
  h.edit("Main A");
  await h.fireTimers();
  h.edit("Main A B");
  h.select(11);
  h.edit("Chapter A");
  let finished = false;
  const flush = h.callback("flushActiveSource")().then(() => { finished = true; });
  await tick();
  assert.equal(finished, false);
  assert.equal(h.sourceRequests().filter((entry) => entry.fileId === 0).length, 1);
  assert.equal(h.sourceRequests().filter((entry) => entry.fileId === 11).length, 1);
  await h.settle(1, receipt("Chapter A"));
  assert.equal(finished, false);
  await h.settle(0, receipt("Main A"));
  assert.equal(h.sourceRequests().at(-1).source, "Main A B");
  assert.equal(finished, false);
  await h.settle(2, receipt("Main A B", 3));
  await flush;
  assert.equal(finished, true);
  assert.equal(h.timers.size, 0);
  assert.equal(h.state.content, "Chapter A");
  assert.equal(h.state.saveState, "saved");
});

test("compile waits for source flush and never starts after a save error", async (t) => {
  const h = harness(t);
  h.edit("Unconfirmed main");
  const compile = h.mutation("compile").mutate();
  const rejected = assert.rejects(compile, /Synthetic save failure/);
  await tick();
  assert.equal(h.sourceRequests().length, 1);
  assert.equal(h.requests.some((entry) => entry.kind === "compile"), false);
  await h.settle(0, new Error("Synthetic save failure"), true);
  await rejected;
  assert.equal(h.state.content, "Unconfirmed main");
  assert.equal(h.state.saveState, "unsaved");
  assert.equal(h.requests.some((entry) => entry.kind === "compile"), false);
  const retry = h.mutation("compile").mutate();
  await h.settle(1, receipt("Unconfirmed main"));
  await retry;
  assert.equal(h.requests.filter((entry) => entry.kind === "compile").length, 1);
});

test("metadata success cannot mark unsaved source text as saved", async (t) => {
  const h = harness(t);
  h.edit("Local source draft");
  await h.mutation("persist").mutate({ title: "New title" });
  assert.equal(h.state.content, "Local source draft");
  assert.equal(h.state.saveState, "unsaved");
  assert.equal(h.sourceRequests().length, 0);
});

test("late conflict uses the newest local draft and remains scoped to its file", async (t) => {
  const h = harness(t);
  h.edit("Main sent");
  await h.fireTimers();
  h.edit("Main sent plus newer draft");
  h.select(11);
  h.edit("Chapter unsaved");
  const conflictArgs = [409, "Synthetic conflict"];
  while (conflictArgs.length < errorDetailIndex) conflictArgs.push(null);
  conflictArgs.push({
    code: "writer_revision_conflict", path: "main.tex", revision: 3, content: "Remote main",
  });
  const conflict = new ApiError(...conflictArgs);
  await h.settle(0, conflict, true);
  assert.equal(h.state.content, "Chapter unsaved");
  assert.equal(h.state.saveState, "unsaved");
  assert.equal(h.state.sourceConflict.fileId, 0);
  assert.equal(h.state.sourceConflict.local, "Main sent plus newer draft");
  assert.equal(h.state.sourceConflict.reviewed, "Main sent plus newer draft");
  assert.equal(h.state.sourceConflict.remote, "Remote main");
  const blocked = assert.rejects(h.callback("flushActiveSource")(), (error) => error === conflict);
  await tick();
  assert.equal(h.pending[1]?.request.fileId, 11, "flush still drains the independent file");
  await h.settle(1, receipt("Chapter unsaved"));
  await blocked;
  assert.equal(h.sourceRequests().filter((entry) => entry.fileId === 0).length, 1);
});

test("polling newer content never rebases an unsaved draft onto unseen coauthor edits", async (t) => {
  const h = harness(t);
  h.edit("Main local draft");
  const remote = initialFiles();
  remote[0] = { ...remote[0], content: "Main remote addition", revision: 5 };
  h.observeFiles(remote);
  assert.equal(h.state.content, "Main local draft");
  assert.equal(h.state.saveState, "unsaved");
  await h.fireTimers();
  assert.deepEqual(h.sourceRequests()[0].basis, { revision: 1, content: "Main initial" });
  await h.settle(0, receipt("Main remote addition plus local draft", 6, { merged: true }));
  assert.equal(h.state.content, "Main remote addition plus local draft");
  assert.equal(h.state.saveState, "saved");
});

test("merged acknowledgement keeps a newer draft on its real editing basis", async (t) => {
  const h = harness(t);
  h.edit("Main local A");
  await h.fireTimers();
  h.edit("Main local A B");
  await h.settle(0, receipt("Remote independent\nMain local A", 3, { merged: true }));
  assert.equal(h.state.content, "Main local A B");
  assert.equal(h.state.saveState, "unsaved");
  await h.fireTimers();
  assert.deepEqual(h.sourceRequests()[1].basis, { revision: 1, content: "Main initial" });
  assert.equal(h.sourceRequests()[1].source, "Main local A B");
  await h.settle(1, receipt("Remote independent\nMain local A B", 4, { merged: true }));
  assert.equal(h.state.content, "Remote independent\nMain local A B");
  assert.equal(h.state.saveState, "saved");
});

test("read-only transitions block new source writes without contacting another endpoint", async (t) => {
  const h = harness(t);
  h.setPermission(false);
  h.edit("Forbidden edit");
  assert.equal(h.state.content, "Main initial");
  assert.equal(h.timers.size, 0);
  assert.equal(h.sourceRequests().length, 0);
  h.setPermission(true);
  h.edit("Draft before permission changed");
  h.setPermission(false);
  const flush = h.callback("flushActiveSource")();
  await assert.rejects(flush);
  assert.equal(h.sourceRequests().length, 0);
  assert.equal(h.state.content, "Draft before permission changed");
  assert.notEqual(h.state.saveState, "saved");
});

for (const filesFirst of [false, true]) {
  test(`document initialization survives ${filesFirst ? "files-first" : "document-first"} query order`, async (t) => {
    const h = harness(t, { filesFirst, compileStatus: "ok" });
    await tick();
    assert.equal(h.state.title, "Synthetic");
    assert.equal(h.state.content, "Main initial");
    assert.equal(h.state.pdfUrl, "blob:synthetic-pdf");
    assert.equal(h.refs.previousStatus.current, "ok");
    assert.deepEqual(h.messages.filter((entry) => entry?.pdf), [{ pdf: h.docId }]);
    h.runEffect("sourceSaves.observe(0, doc)");
    await tick();
    assert.equal(h.messages.filter((entry) => entry?.pdf).length, 1);
  });
}

test("read-only lifecycle still reflects observed documents and selected files", (t) => {
  const h = harness(t, { canEdit: false });
  assert.equal(h.state.content, "Main initial");
  const updated = initialFiles();
  updated[0] = { ...updated[0], content: "Read-only remote revision", revision: 3 };
  h.observeFiles(updated);
  assert.equal(h.state.content, "Read-only remote revision");
  assert.equal(h.state.saveState, "saved");
  h.select(11);
  assert.equal(h.state.content, "Chapter initial");
  h.edit("Forbidden");
  assert.equal(h.state.content, "Chapter initial");
  assert.equal(h.timers.size, 0);
  assert.equal(h.requests.length, 0);
});

test("page cache callbacks independently refuse older receipts for both cache shapes", async (t) => {
  const h = harness(t);
  h.edit("Main sent");
  await h.fireTimers();
  const newer = { ...h.client.getQueryData(["writer-doc", h.docId]), content: "Cached revision five", revision: 5 };
  h.client.setQueryData(["writer-doc", h.docId], newer);
  h.client.setQueryData(["writer-files", h.docId], initialFiles().map((file) => file.id === 0
    ? { ...file, content: newer.content, revision: newer.revision } : file));
  // Do not observe revision five in the controller: this exercises onSaved itself.
  await h.settle(0, receipt("Main sent", 2));
  assert.equal(h.client.getQueryData(["writer-doc", h.docId]).content, "Cached revision five");
  const file = h.client.getQueryData(["writer-files", h.docId]).find((entry) => entry.id === 0);
  assert.equal(file.content, "Cached revision five");
  assert.equal(file.revision, 5);
});

test("effect cleanup suppresses late callbacks and repeated setup delivers the retained outcome", async (t) => {
  const h = harness(t);
  h.edit("Main sent");
  await h.fireTimers();
  const before = structuredClone(h.state);
  h.deactivate();
  assert.equal(h.timers.size, 0);
  await h.settle(0, receipt("Server acknowledged", 2));
  assert.deepEqual(h.state, before);
  assert.equal(h.client.getQueryData(["writer-doc", h.docId]).revision, 1);
  h.runEffect(LIFECYCLE);
  assert.equal(h.state.content, "Server acknowledged");
  assert.equal(h.state.saveState, "saved");
  assert.equal(h.client.getQueryData(["writer-doc", h.docId]).revision, 2);
  assert.equal(h.sourceRequests().length, 1);
});

test("disposed document callbacks cannot affect a newly initialized document", async (t) => {
  const old = harness(t, { docId: "old-document" });
  old.edit("Old document draft");
  await old.fireTimers();
  old.deactivate();
  const current = harness(t, { docId: "new-document" });
  current.edit("New document draft");
  await old.settle(0, receipt("Late old acknowledgement", 2));
  assert.equal(current.state.content, "New document draft");
  assert.equal(current.state.saveState, "unsaved");
  assert.equal(old.client.getQueryData(["writer-doc", "old-document"]).revision, 1);
  assert.equal(current.client.getQueryData(["writer-doc", "new-document"]).revision, 1);
  assert.equal(current.requests.length, 0);
});

test("source operation locks stale edit callbacks and waits for every file before dispatch", async (t) => {
  const h = harness(t);
  h.edit("Main first");
  h.select(11);
  h.edit("Chapter first");
  const staleEdit = h.callback("scheduleSave");
  const barrier = Promise.withResolvers();
  let dispatched = 0;
  const operation = h.callback("withSourceOperation")(() => { dispatched += 1; return barrier.promise; });
  assert.equal(h.refs.sourceOperationBusyRef.current, true);
  assert.equal(h.state.sourceOperationBusy, true);
  staleEdit("Must not enter during replacement");
  assert.equal(h.state.content, "Chapter first");
  await assert.rejects(h.callback("withSourceOperation")(async () => assert.fail("duplicate operation")));
  await assert.rejects(h.callback("flushActiveSource")());
  await tick();
  assert.equal(dispatched, 0);
  assert.equal(h.sourceRequests().length, 2);
  await h.settle(1, receipt("Chapter first"));
  assert.equal(dispatched, 0);
  await h.settle(0, receipt("Main first"));
  assert.equal(dispatched, 1);
  assert.equal(h.state.sourceOperationBusy, true);
  barrier.resolve("done");
  assert.equal(await operation, "done");
  assert.equal(h.refs.sourceOperationBusyRef.current, false);
  assert.equal(h.state.sourceOperationBusy, false);
  h.edit("Edits resume afterwards");
  assert.equal(h.state.content, "Edits resume afterwards");
});

for (const failure of ["save", "permission", "operation"]) {
  test(`source operation releases its lock after ${failure} failure without early dispatch`, async (t) => {
    const h = harness(t);
    h.edit("Retain me");
    let dispatched = 0;
    const operation = h.callback("withSourceOperation")(async () => {
      dispatched += 1;
      throw new Error("Synthetic operation failure");
    });
    const failed = assert.rejects(operation);
    if (failure === "permission") {
      await tick();
      assert.equal(h.sourceRequests().length, 1, "permission changes after an authorized write has started");
      h.setPermission(false);
      await h.settle(0, receipt("Retain me"));
    }
    if (failure === "save") await h.settle(0, new Error("Synthetic save failure"), true);
    else if (failure === "operation") await h.settle(0, receipt("Retain me"));
    await failed;
    assert.equal(dispatched, failure === "operation" ? 1 : 0);
    assert.equal(h.state.sourceOperationBusy, false);
    assert.equal(h.refs.sourceOperationBusyRef.current, false);
    assert.equal(h.state.content, "Retain me");
  });
}

test("restore dispatches only after saves and adopts its own returned revision", async (t) => {
  const h = harness(t);
  h.edit("Before restore");
  const restore = h.callback("restoreSource")(42);
  await tick();
  assert.equal(h.requests.some((entry) => entry.kind === "restore"), false);
  await h.settle(0, receipt("Before restore"));
  assert.deepEqual(h.requests.at(-1), { kind: "restore", docId: h.docId, snapshotId: 42 });
  await h.settle(1, receipt("Restored main", 3));
  await restore;
  assert.equal(h.state.content, "Restored main");
  assert.equal(h.state.saveState, "saved");
  assert.equal(h.state.sourceOperationBusy, false);
});

test("approved edits preserve their target and update both controller and cache", async (t) => {
  const h = harness(t);
  h.edit("Before approved edit");
  const proposed = [{ path: "main.tex", find: "Before", replace: "After" }];
  const apply = h.callback("applyEdits")(proposed, { auto: false, messageId: 42 });
  await tick();
  assert.equal(h.requests.some((entry) => entry.kind === "apply"), false);
  await h.settle(0, receipt("Before approved edit"));
  assert.deepEqual(h.requests.at(-1), {
    kind: "apply", docId: h.docId, edits: proposed, options: { auto: false, messageId: 42 },
  });
  await h.settle(1, { applied: [0], files: [{ path: "main.tex", ...receipt("After approved edit", 3) }] });
  assert.deepEqual(await apply, [0]);
  assert.equal(h.state.content, "After approved edit");
  assert.equal(h.client.getQueryData(["writer-doc", h.docId]).content, "After approved edit");
  assert.equal(h.client.getQueryData(["writer-files", h.docId])[0].revision, 3);
  assert.equal(h.state.sourceOperationBusy, false);
});

for (const kind of ["rename", "delete"]) {
  test(`${kind} keeps its file target and runs behind the actual source barrier`, async (t) => {
    const h = harness(t, { activeFile: 11 });
    h.edit("Retained chapter");
    const mutation = h.mutation(kind === "rename" ? "renameProjectFile" : "deleteProjectFile");
    const operation = mutation.mutate(kind === "rename" ? { fileId: 11, path: "renamed.tex" } : 11);
    await tick();
    assert.equal(h.requests.some((entry) => entry.kind === kind), false);
    await h.settle(0, receipt("Retained chapter"));
    assert.equal(h.requests.at(-1).kind, kind);
    assert.equal(h.requests.at(-1).fileId, 11);
    const renamed = { ...initialFiles()[1], path: "renamed.tex", ...receipt("Retained chapter", 3) };
    await h.settle(1, kind === "rename" ? renamed : undefined);
    await operation;
    assert.equal(h.state.sourceOperationBusy, false);
    if (kind === "rename") {
      assert.equal(h.sourceSaves.get(11).revision, 3);
      assert.equal(h.client.getQueryData(["writer-files", h.docId]).find((file) => file.id === 11).path, "renamed.tex");
    } else {
      assert.equal(h.sourceSaves.get(11), undefined);
      assert.equal(h.state.activeFileId, 0);
      assert.equal(h.state.content, "Main initial");
    }
  });
}

test("a clean read-only source can flush for viewing but cannot compile", async (t) => {
  const h = harness(t, { canEdit: false });
  await h.callback("flushActiveSource")();
  await assert.rejects(h.mutation("compile").mutate(), /read-only/);
  assert.equal(h.requests.length, 0);
  assert.equal(h.state.content, "Main initial");
});

test("cleanup while draining source saves prevents a queued restore from dispatching", async (t) => {
  const h = harness(t);
  h.edit("Before leaving document");
  const restore = h.callback("restoreSource")(42);
  const rejected = assert.rejects(restore);
  await tick();
  assert.equal(h.sourceRequests().length, 1);
  h.deactivate();
  await h.settle(0, receipt("Before leaving document"));
  await rejected;
  assert.equal(h.requests.some((entry) => entry.kind === "restore"), false);
  assert.equal(h.client.getQueryData(["writer-doc", h.docId]).revision, 1);
});

for (const updatedBy of [null, undefined]) {
  test(`saved updater ${String(updatedBy)} retains its distinct cache meaning`, async (t) => {
    const h = harness(t);
    h.client.setQueryData(["writer-doc", h.docId], (old) => ({ ...old, updated_by: 42 }));
    h.client.setQueryData(["writer-files", h.docId], (old) => old.map((file) => ({ ...file, updated_by: 42 })));
    h.edit("Updater test");
    await h.fireTimers();
    await h.settle(0, receipt("Updater test", 2, { updated_by: updatedBy }));
    const expected = updatedBy === undefined ? 42 : null;
    assert.equal(h.client.getQueryData(["writer-doc", h.docId]).updated_by, expected);
    assert.equal(h.client.getQueryData(["writer-files", h.docId])[0].updated_by, expected);
  });
}

test("normal navigation before debounce drains the authorized draft without departed-view callbacks", async (t) => {
  const h = harness(t);
  h.edit("Final draft before navigating");
  assert.equal(h.pending.length, 0);
  assert.equal(h.timers.size, 1);
  const departedView = structuredClone(h.state);
  h.deactivate();
  await tick();
  assert.equal(h.timers.size, 0);
  assert.equal(h.sourceRequests().length, 1);
  assert.equal(h.sourceRequests()[0].source, "Final draft before navigating");
  await h.settle(0, receipt("Final draft before navigating"));
  assert.deepEqual(h.state, departedView);
  assert.equal(h.client.getQueryData(["writer-doc", h.docId]).revision, 1);
  assert.equal(h.sourceSaves.get(0).status, "saved");
});

test("navigation while a write is held drains its newer draft once with the acknowledged basis", async (t) => {
  const h = harness(t);
  h.edit("Main A");
  await h.fireTimers();
  h.edit("Main A B");
  const departedView = structuredClone(h.state);
  h.deactivate();
  await tick();
  assert.equal(h.sourceRequests().length, 1);
  await h.settle(0, receipt("Main A"));
  assert.equal(h.sourceRequests().length, 2);
  assert.deepEqual(h.sourceRequests()[1], {
    kind: "writerPatch", docId: h.docId, fileId: 0, source: "Main A B",
    basis: { content: "Main A", revision: 2 },
  });
  await h.settle(1, receipt("Main A B", 3));
  assert.equal(h.sourceSaves.get(0).status, "saved");
  assert.deepEqual(h.state, departedView);
  assert.equal(h.client.getQueryData(["writer-doc", h.docId]).revision, 1);
});

test("a changed session blocks a detached successor even without a storage event", async (t) => {
  const h = harness(t);
  h.edit("Authorized A");
  await h.fireTimers();
  h.edit("Authorized A B");
  h.deactivate();
  h.setToken("different-synthetic-session");
  await h.settle(0, receipt("Authorized A"));
  assert.equal(h.sourceRequests().length, 1, "every successor rechecks the captured session");
  assert.equal(h.sourceSaves.get(0).content, "Authorized A B");
  assert.equal(h.sourceSaves.get(0).status, "unsaved");
  assert.equal(h.client.getQueryData(["writer-doc", h.docId]).revision, 1);
});

for (const eventName of ["six:auth-boundary", "storage"]) {
  test(`${eventName} still prevents successors during a detached final drain`, async (t) => {
    const h = harness(t);
    h.edit("Authorized A");
    await h.fireTimers();
    h.edit("Authorized A B");
    const departedView = structuredClone(h.state);
    h.deactivate();
    assert.equal(h.listenerCount(eventName), 1, "auth listeners remain until the held final drain settles");
    let warned = false;
    h.dispatch("beforeunload", { preventDefault() { warned = true; } });
    assert.equal(warned, true, "a detached write still needs a browser-exit warning");
    // The explicit auth event must also work without a token-string change.
    if (eventName === "storage") h.setToken("different-synthetic-session");
    h.dispatch(eventName);
    assert.equal(h.refs.sourceAccessRef.current, false);
    await h.settle(0, receipt("Authorized A"));
    assert.equal(h.sourceRequests().length, 1);
    assert.equal(h.sourceSaves.get(0).content, "Authorized A B");
    assert.equal(h.sourceSaves.get(0).status, "unsaved");
    assert.deepEqual(h.state, departedView);
    assert.equal(h.client.getQueryData(["writer-doc", h.docId]).revision, 1);
    for (const name of ["six:auth-boundary", "storage", "beforeunload"]) {
      assert.equal(h.listenerCount(name), 0, "settled final drains remove their listeners");
    }
  });
}

test("a failed detached file does not prevent an independent held file draining its newer draft", async (t) => {
  const h = harness(t);
  h.edit("Main will fail");
  h.select(11);
  h.edit("Chapter A");
  await h.fireTimers();
  h.edit("Chapter A B");
  const departedView = structuredClone(h.state);
  h.deactivate();
  assert.deepEqual(h.sourceRequests().map((entry) => entry.fileId), [0, 11]);
  await h.settle(0, new Error("Synthetic main failure"), true);
  assert.equal(h.listenerCount("six:auth-boundary"), 1, "the other file still has authorized work to drain");
  await h.settle(1, receipt("Chapter A"));
  assert.equal(h.sourceRequests().length, 3);
  assert.deepEqual(h.sourceRequests()[2], {
    kind: "writerFilePatch", docId: h.docId, fileId: 11, source: "Chapter A B",
    basis: { content: "Chapter A", revision: 2 },
  });
  await h.settle(2, receipt("Chapter A B", 3));
  assert.equal(h.sourceSaves.get(0).status, "unsaved");
  assert.equal(h.sourceSaves.get(11).status, "saved");
  assert.deepEqual(h.state, departedView);
  assert.equal(h.client.getQueryData(["writer-files", h.docId])[1].revision, 1);
  assert.equal(h.listenerCount("six:auth-boundary"), 0);
});

for (const eventName of ["six:auth-boundary", "storage"]) {
  test(`${eventName} hard-stops queued drafts before navigation can drain them`, async (t) => {
    const h = harness(t);
    h.edit("Draft belonging to the previous session");
    if (eventName === "storage") h.setToken("different-synthetic-session");
    h.dispatch(eventName);
    assert.equal(h.refs.canEditRef.current, false);
    assert.equal(h.refs.sourceAccessRef.current, false);
    assert.equal(h.timers.size, 0);
    h.deactivate();
    await tick();
    assert.equal(h.requests.length, 0);
    h.runEffect(LIFECYCLE);
    assert.equal(h.refs.canEditRef.current, false, "an auth boundary stays closed for this mounted controller");
  });
}

test("the actual beforeunload listener warns only for pending source or a source operation", async (t) => {
  const h = harness(t);
  function warned() {
    let prevented = false;
    const event = h.dispatch("beforeunload", { preventDefault() { prevented = true; }, returnValue: undefined });
    return { prevented, returnValue: event.returnValue };
  }
  assert.equal(warned().prevented, false);
  const barrier = Promise.withResolvers();
  const operation = h.callback("withSourceOperation")(() => barrier.promise);
  assert.deepEqual(warned(), { prevented: true, returnValue: "" });
  barrier.resolve();
  await operation;
  assert.equal(warned().prevented, false);
  h.edit("Pending source");
  assert.deepEqual(warned(), { prevented: true, returnValue: "" });
  const flush = h.callback("flushActiveSource")();
  await h.settle(0, receipt("Pending source"));
  await flush;
  assert.equal(warned().prevented, false);
  h.deactivate();
  assert.equal(warned().prevented, false, "departed page listeners are removed");
});

test("cleanup followed immediately by setup does not disable the reattached controller", async (t) => {
  const h = harness(t);
  h.edit("Strict replay A");
  h.deactivate();
  h.runEffect(LIFECYCLE);
  h.edit("Strict replay A B");
  await h.settle(0, receipt("Strict replay A"));
  await h.settle(1, receipt("Strict replay A B", 3));
  assert.equal(h.state.content, "Strict replay A B");
  assert.equal(h.state.saveState, "saved");
  h.edit("Still attached C");
  await h.fireTimers();
  await h.settle(2, receipt("Still attached C", 4));
  assert.equal(h.state.content, "Still attached C");
  assert.equal(h.state.saveState, "saved");
  assert.equal(h.client.getQueryData(["writer-doc", h.docId]).revision, 4);
});

test("restore refreshes auxiliary revisions before the next edit can be saved", async (t) => {
  const h = harness(t, { activeFile: 11 });
  const restoredFiles = initialFiles().map((file) => ({ ...file, content: `Restored ${file.path}`, revision: 4 }));
  h.setServerFiles(restoredFiles);
  const restore = h.callback("restoreSource")(42);
  await h.settle(0, receipt("Restored main.tex", 4));
  await restore;
  assert.equal(h.state.content, "Restored chapter.tex");
  assert.equal(h.sourceSaves.get(11).revision, 4);
  assert.equal(h.client.getQueryData(["writer-files", h.docId])[1].revision, 4);
  assert.equal(h.state.sourceOperationBusy, false);
  h.edit("Restored chapter.tex plus new edit");
  await h.fireTimers();
  assert.deepEqual(h.sourceRequests()[0].basis, { content: "Restored chapter.tex", revision: 4 });
  await h.settle(1, receipt("Restored chapter.tex plus new edit", 5));
});

test("restore removes controller entries for files absent from the restored project", async (t) => {
  const h = harness(t);
  h.setServerFiles([{ ...initialFiles()[0], ...receipt("Only main remains", 4) }]);
  const restore = h.callback("restoreSource")(42);
  await h.settle(0, receipt("Only main remains", 4));
  await restore;
  assert.equal(h.sourceSaves.get(11), undefined);
  assert.deepEqual(h.client.getQueryData(["writer-files", h.docId]).map((file) => file.id), [0]);
  assert.equal(h.state.content, "Only main remains");
  assert.equal(h.requests.filter((entry) => entry.kind === "files").length, 1);
});

test("a file-list refresh error does not claim an already completed restore failed", async (t) => {
  const h = harness(t);
  h.setServerFiles(new Error("Synthetic file-list outage"));
  const restore = h.callback("restoreSource")(42);
  await h.settle(0, receipt("Successfully restored main", 4));
  const result = await restore;
  assert.equal(result.content, "Successfully restored main");
  assert.equal(h.state.content, "Successfully restored main");
  assert.equal(h.state.sourceOperationBusy, false);
  assert.ok(h.messages.some((entry) => entry?.level === "error" && /restored.*file list.*refreshed/.test(entry.value)));
  assert.equal(h.requests.filter((entry) => entry.kind === "restore").length, 1);
});

for (const kind of ["restore", "apply", "rename", "create", "delete"]) {
  test(`an auth boundary while ${kind} is held prevents its returned data reaching the departed view`, async (t) => {
    const h = harness(t, { activeFile: 11 });
    let operation;
    if (kind === "restore") operation = h.callback("restoreSource")(42);
    else if (kind === "apply") operation = h.callback("applyEdits")([{ path: "chapter.tex", find: "initial", replace: "new" }]);
    else operation = h.mutation({ rename: "renameProjectFile", create: "createProjectFile", delete: "deleteProjectFile" }[kind])
      .mutate(kind === "rename" ? { fileId: 11, path: "renamed.tex" } : kind === "delete" ? 11 : undefined);
    await tick();
    assert.equal(h.requests.at(-1).kind, kind);
    const previousFiles = structuredClone(h.client.getQueryData(["writer-files", h.docId]));
    const previousDoc = structuredClone(h.client.getQueryData(["writer-doc", h.docId]));
    const previousContent = h.state.content;
    h.dispatch("six:auth-boundary");
    const file = { id: kind === "create" ? 22 : 11, path: "returned.tex", main: false, ...receipt("Late changed file", 3) };
    const result = kind === "restore" ? receipt("Late restored main", 3)
      : kind === "apply" ? { applied: [0], files: [file] }
      : kind === "delete" ? undefined : file;
    await h.settle(0, result);
    await operation;
    assert.equal(h.state.content, previousContent);
    assert.equal(h.state.activeFileId, 11);
    assert.deepEqual(h.client.getQueryData(["writer-files", h.docId]), previousFiles);
    assert.deepEqual(h.client.getQueryData(["writer-doc", h.docId]), previousDoc);
    assert.equal(h.refs.canEditRef.current, false);
    assert.equal(h.state.sourceOperationBusy, false);
  });
}

for (const surface of [0, 1]) {
  for (const interrupted of [false, true]) {
    test(`version-history surface ${surface} executes its real restore callback ${interrupted ? "after an auth boundary" : "on success"}`, async (t) => {
      const h = harness(t, { activeFile: 11 });
      h.setServerFiles(initialFiles().map((file) => ({ ...file, ...receipt(`Restored ${file.path}`, 4) })));
      const restore = h.snapshotMutation(surface).mutate(42);
      await tick();
      assert.equal(h.requests.at(-1).kind, "restore");
      if (interrupted) h.dispatch("six:auth-boundary");
      await h.settle(0, receipt("Restored main.tex", 4));
      await restore;
      assert.equal(h.state.activeFileId, interrupted ? 11 : 0);
      assert.equal(h.refs.activeFileIdRef.current, interrupted ? 11 : 0);
      assert.equal(h.state.content, interrupted ? "Chapter initial" : "Restored main.tex");
      assert.equal(h.messages.filter((entry) => entry === "compileAfterRestore").length, interrupted ? 0 : 1);
      assert.equal(h.state.sourceOperationBusy, false);
      if (surface === 1) assert.equal(h.state.toolbarPanel, interrupted ? undefined : null);
    });
  }
}
