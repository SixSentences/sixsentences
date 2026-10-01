import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { runInNewContext } from "node:vm";
import ts from "typescript";

const source = readFileSync("src/lib/pinboard.ts", "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
}).outputText;
const { PinboardSession, appendPinboardNote, arrangePinboardNotes, newPinboardNote, pinboardPosition, pinboardPoint, pinboardScale, pinboardNoteSize, pinboardPaper, resizePinboardNote, keepPinboardHandleVisible, pinboardDragStarted, validatePinboard } = await import(
  `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`,
);
const id = "b27ed80a-2f10-4565-a61b-314508b92642";
const note = (text = "Review the methods") => ({ ...newPinboardNote(id, 0), text });
const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((ok, fail) => { resolve = ok; reject = fail; });
  return { promise, resolve, reject };
};
const copy = (value) => JSON.parse(JSON.stringify(value));

function fixture(overrides = {}) {
  let allowed = true;
  let server = { revision: 0, notes: [] };
  const writes = [];
  const session = new PinboardSession({
    canAccess: () => allowed,
    isConflict: (error) => error?.status === 409,
    read: async () => copy(server),
    write: async (value) => {
      writes.push(copy(value));
      if (value.revision !== server.revision) throw { status: 409 };
      server = { ...copy(value), revision: value.revision + 1 };
      return copy(server);
    },
    ...overrides,
  });
  return { session, writes, revoke: () => { allowed = false; }, setServer: (value) => { server = value; } };
}

test("a new board loads before accepting edits and persists bounded content", async () => {
  const { session, writes } = fixture();
  session.edit([note()]);
  assert.deepEqual(session.getSnapshot().notes, []);
  await session.load();
  session.edit([note()]);
  assert.equal(session.getSnapshot().dirty, true);
  await session.save();
  assert.deepEqual(writes, [{ revision: 0, notes: [note()] }]);
  assert.equal(session.getSnapshot().revision, 1);
  assert.equal(session.getSnapshot().dirty, false);
  assert.equal(session.getSnapshot().phase, "ready");
});

test("writes are serialized and edits during a save survive its receipt", async () => {
  const pending = deferred();
  const writes = [];
  const { session } = fixture({ write: async (value) => { writes.push(copy(value)); return pending.promise; } });
  await session.load();
  session.edit([note("First edit")]);
  const saving = session.save();
  session.edit([note("Second edit")]);
  await session.save();
  assert.equal(writes.length, 1);
  pending.resolve({ revision: 1, notes: [note("First edit")] });
  await saving;
  assert.equal(session.getSnapshot().notes[0].text, "Second edit");
  assert.equal(session.getSnapshot().dirty, true);
  assert.equal(session.getSnapshot().revision, 1);
});

test("a transient write failure retains the exact draft for explicit retry", async () => {
  let fail = true;
  const { session } = fixture({ write: async (value) => {
    if (fail) throw new Error("offline");
    return { ...value, revision: value.revision + 1 };
  } });
  await session.load();
  session.edit([note("Keep this draft")]);
  await session.save();
  assert.equal(session.getSnapshot().phase, "save-error");
  assert.equal(session.getSnapshot().dirty, true);
  assert.equal(session.getSnapshot().notes[0].text, "Keep this draft");
  fail = false;
  await session.save();
  assert.equal(session.getSnapshot().dirty, false);
});

test("conflicting tabs cannot overwrite each other without an explicit version choice", async () => {
  const { session, writes, setServer } = fixture();
  await session.load();
  session.edit([note("My draft")]);
  setServer({ revision: 2, notes: [note("Other window")] });
  await session.save();
  assert.equal(session.getSnapshot().phase, "conflict");
  assert.equal(session.getSnapshot().notes[0].text, "My draft");
  assert.equal(session.getSnapshot().savedVersion.notes[0].text, "Other window");
  await session.save();
  assert.equal(writes.length, 1);
  session.resolveConflict("draft");
  await session.save();
  assert.deepEqual(writes[1], { revision: 2, notes: [note("My draft")] });
  assert.equal(session.getSnapshot().revision, 3);
});

test("choosing the saved board discards a draft only explicitly and makes no write", async () => {
  const { session, writes, setServer } = fixture();
  await session.load();
  session.edit([note("My draft")]);
  setServer({ revision: 2, notes: [note("Other window")] });
  await session.save();
  session.resolveConflict("saved");
  await session.save();
  assert.equal(writes.length, 1);
  assert.equal(session.getSnapshot().notes[0].text, "Other window");
  assert.equal(session.getSnapshot().dirty, false);
});

test("another save while reviewing a conflict still triggers a new conflict", async () => {
  const { session, setServer } = fixture();
  await session.load();
  session.edit([note("My draft")]);
  setServer({ revision: 1, notes: [note("Other window")] });
  await session.save();
  session.resolveConflict("draft");
  setServer({ revision: 2, notes: [note("Newer edit")] });
  await session.save();
  assert.equal(session.getSnapshot().phase, "conflict");
  assert.equal(session.getSnapshot().savedVersion.notes[0].text, "Newer edit");
});

test("a lost save response is recovered without duplicate or unnecessary overwrite", async () => {
  let server = { revision: 0, notes: [] };
  const { session } = fixture({
    read: async () => copy(server),
    write: async (value) => {
      if (value.revision !== server.revision) throw { status: 409 };
      server = { ...value, revision: value.revision + 1 };
      throw new Error("response lost");
    },
  });
  await session.load();
  session.edit([note()]);
  await session.save();
  assert.equal(session.getSnapshot().phase, "save-error");
  await session.save();
  assert.equal(session.getSnapshot().phase, "ready");
  assert.equal(session.getSnapshot().dirty, false);
  assert.equal(session.getSnapshot().revision, 1);
});

test("logout aborts in-flight work, clears content, and ignores its late response", async () => {
  const pending = deferred();
  let signal;
  const { session } = fixture({ write: async (_value, abortSignal) => { signal = abortSignal; return pending.promise; } });
  await session.load();
  session.edit([note("Private draft")]);
  const saving = session.save();
  session.dispose();
  assert.equal(signal.aborted, true);
  pending.resolve({ revision: 1, notes: [note("Private draft")] });
  await saving;
  assert.equal(session.getSnapshot().phase, "closed");
  assert.deepEqual(session.getSnapshot().notes, []);
  assert.equal(session.getSnapshot().savedVersion, null);
});

test("a token swap blocks a queued write before it can reach the next identity", async () => {
  const { session, writes, revoke } = fixture();
  await session.load();
  session.edit([note()]);
  revoke();
  await session.save();
  assert.equal(writes.length, 0);
  assert.equal(session.getSnapshot().phase, "closed");
  assert.deepEqual(session.getSnapshot().notes, []);
});

test("a token swap during an initial read cannot display the old account's notes", async () => {
  const pending = deferred();
  const { session, revoke } = fixture({ read: async () => pending.promise });
  const loading = session.load();
  revoke();
  pending.resolve({ revision: 4, notes: [note("Old account")] });
  await loading;
  assert.equal(session.getSnapshot().phase, "closed");
  assert.deepEqual(session.getSnapshot().notes, []);
});

test("an unavailable first load cannot be replaced by an empty board", async () => {
  const { session, writes } = fixture({ read: async () => { throw new Error("offline"); } });
  await session.load();
  session.edit([note()]);
  await session.save();
  assert.equal(session.getSnapshot().phase, "load-error");
  assert.equal(writes.length, 0);
});

test("malformed receipts never mark unsaved drafts as durable", async () => {
  for (const receipt of [{ revision: 0, notes: [note()] }, { revision: 1, notes: [] }]) {
    const { session } = fixture({ write: async () => receipt });
    await session.load();
    session.edit([note()]);
    await session.save();
    assert.equal(session.getSnapshot().phase, "save-error");
    assert.equal(session.getSnapshot().dirty, true);
  }
});

test("notes validate type, coordinates, size, IDs, and the account limit", () => {
  assert.deepEqual(validatePinboard({ revision: 1, notes: [note()] }), { revision: 1, notes: [note()] });
  for (const invalid of [
    { ...note(), x: -0.1 }, { ...note(), y: 1.1 }, { ...note(), rotation: NaN },
    { ...note(), rotation: 13 }, { ...note(), color: "red" }, { ...note(), shape: "script" },
    { ...note(), id: "not-an-id" }, { ...note(), text: "a".repeat(2001) },
    ...[0.59, 2.41, NaN, Infinity, true, null, "1"].map((size) => ({ ...note(), size })),
  ]) assert.throws(() => validatePinboard({ revision: 0, notes: [invalid] }));
  assert.throws(() => validatePinboard({ revision: 0, notes: [note(), note()] }));
  assert.throws(() => validatePinboard({ revision: 0, notes: [note(), { ...note(), id: id.toUpperCase() }] }));
  assert.throws(() => validatePinboard({ revision: -1, notes: [] }));
  assert.throws(() => validatePinboard({ revision: 2_147_483_648, notes: [] }));
  assert.throws(() => validatePinboard({ revision: 0, notes: Array(25).fill(note()) }));
});

test("legacy notes normalize size without dirtying or rewriting the board", async () => {
  const legacy = { ...note() };
  delete legacy.size;
  const { session, writes } = fixture({ read: async () => ({ revision: 2, notes: [legacy] }) });
  await session.load();
  assert.equal(session.getSnapshot().notes[0].size, 1);
  assert.equal(session.getSnapshot().dirty, false);
  await session.save();
  assert.equal(writes.length, 0);
  assert.equal("size" in legacy, false);
  for (const size of [0.6, 1, 2.4]) {
    assert.equal(validatePinboard({ revision: 0, notes: [{ ...note(), size }] }).notes[0].size, size);
  }
});

test("a resized note persists exactly and an old server dropping size cannot acknowledge it", async () => {
  const { session, writes } = fixture();
  await session.load();
  session.edit([{ ...note(), size: 1.6 }]);
  await session.save();
  assert.equal(writes[0].notes[0].size, 1.6);
  assert.equal(session.getSnapshot().dirty, false);
  const old = fixture({ write: async (sent) => ({ revision: 1, notes: sent.notes.map(({ size, ...value }) => value) }) });
  await old.session.load();
  old.session.edit([{ ...note(), size: 1.6 }]);
  await old.session.save();
  assert.equal(old.session.getSnapshot().phase, "save-error");
  assert.equal(old.session.getSnapshot().notes[0].size, 1.6);
});

test("manual sizes have readable minima and fit short or narrow canvases without changing saved size", () => {
  for (const shape of ["note", "circle", "card"]) {
    const small = { ...note(), shape, size: 0.6 };
    const minimum = pinboardPaper(small, 347, 636);
    assert.equal(minimum.width, shape === "card" ? 140 : 120);
    if (shape === "circle") assert.equal(minimum.height, minimum.width);
    for (const [width, height] of [[280, 180], [347, 636], [80, 65], [900, 650]]) {
      const large = { ...note(), shape, size: 2.4 };
      const rendered = pinboardPaper(large, width, height);
      assert.ok(rendered.width <= width && rendered.height <= height);
      assert.equal(large.size, 2.4);
      if (shape === "circle") assert.equal(rendered.height, rendered.width);
    }
    assert.equal(small.size, 0.6);
  }
});

test("resizing anchors paper, clamps persisted bounds, and never changes note content", () => {
  const original = { ...note(), x: 0.25, y: 0.4 };
  const resized = resizePinboardNote(original, 900, 650, 1.5);
  const before = pinboardPaper(original, 900, 650);
  const after = pinboardPaper(resized, 900, 650);
  assert.ok(Math.abs(original.x * (900 - before.width) - resized.x * (900 - after.width)) < 1e-8);
  assert.ok(Math.abs(original.y * (650 - before.height) - resized.y * (650 - after.height)) < 1e-8);
  assert.equal(resized.text, original.text);
  assert.equal(resized.size, 1.5);
  assert.equal(resizePinboardNote(original, 900, 650, 100).size, 2.4);
  assert.equal(resizePinboardNote(original, 900, 650, -5).size, 0.6);
  assert.equal(resizePinboardNote(original, 0, 0, 2), original);
  assert.equal(resizePinboardNote(original, NaN, 650, 2), original);
  assert.equal(resizePinboardNote(original, 900, 650, NaN), original);
});

test("explicit drops keep the handle outside the composer when a free edge exists", () => {
  const covered = { ...note(), x: 0.4, y: 0.4, rotation: 0 };
  const rect = { left: 100, top: 120, width: 600, height: 260 };
  const visible = keepPinboardHandleVisible(covered, 900, 650, rect);
  assert.notDeepEqual(visible, covered);
  assert.equal(visible.text, covered.text);
  assert.equal(visible.size, covered.size);
  assert.deepEqual(keepPinboardHandleVisible(visible, 900, 650, rect), visible);
  assert.ok(visible.x >= 0 && visible.x <= 1 && visible.y >= 0 && visible.y <= 1);
  assert.deepEqual(keepPinboardHandleVisible(covered, 900, 650, { left: 1200, top: 0, width: 20, height: 20 }), covered);
});

test("remote Unicode notes use code points rather than UTF16 units and UUIDs canonicalize", () => {
  const value = validatePinboard({ revision: 1, notes: [{ ...note("🌱".repeat(2000)), id: id.toUpperCase() }] });
  assert.equal(value.notes[0].text.length, 4000);
  assert.equal(value.notes[0].id, id);
  assert.throws(() => validatePinboard({ revision: 1, notes: [note("🌱".repeat(2001))] }));
});

test("drag and keyboard geometry clamps to the available travel without NaN", () => {
  assert.equal(pinboardPosition(-1), 0);
  assert.equal(pinboardPosition(2), 1);
  assert.equal(pinboardPosition(0.25), 0.25);
  assert.equal(pinboardPosition(NaN), 0);
  assert.equal(pinboardPosition(Infinity), 0);
});

test("tidying bounds all rows and columns without changing content or appearance", () => {
  const notes = Array.from({ length: 12 }, (_, index) => newPinboardNote(`00000000-0000-4000-8000-${String(index).padStart(12, "0")}`, index));
  const tidy = arrangePinboardNotes(notes, 3);
  assert.equal(tidy[0].x, 0);
  assert.equal(tidy[2].x, 1);
  assert.equal(tidy[3].y, 1 / 3);
  assert.equal(tidy[11].y, 1);
  for (let index = 0; index < notes.length; index++) {
    assert.deepEqual({ ...tidy[index], x: notes[index].x, y: notes[index].y }, notes[index]);
  }
  assert.equal(arrangePinboardNotes(notes, 0)[0].x, 0.5);
});

test("new notes extend a tidy board but never rearrange hand-positioned notes", () => {
  const first = arrangePinboardNotes([note()], 3);
  const second = newPinboardNote("3d9d01c4-c0fb-4f2a-ae1e-f51365686958", 1);
  assert.equal(appendPinboardNote(first, second, 3)[1].x, 0.5);
  const manual = [{ ...first[0], x: 0.17, y: 0.36 }];
  assert.deepEqual(appendPinboardNote(manual, second, 3)[0], manual[0]);
});

test("the composer has no implicit pinboard context or browser content storage", () => {
  const composer = readFileSync("src/components/search/composer.tsx", "utf8");
  const provider = readFileSync("src/lib/use-pinboard.tsx", "utf8");
  assert.doesNotMatch(composer, /pinboard/i);
  assert.doesNotMatch(source + provider, /localStorage\.(?:getItem|setItem)|sessionStorage\.(?:getItem|setItem)/);
  assert.match(provider, /six:auth-boundary/);
  assert.match(provider, /beforeunload/);
});

// Execute the real editor with a minimal hook/element harness, without a DOM or
// browser storage. Dialog dismissal, state transitions, and listener cleanup are
// exercised through the component's actual handlers rather than source patterns.
function editorFixture(original = note(), isNew = false, componentName = "NoteEditor", additionalProps = {}) {
  const editorSource = readFileSync("src/components/home/personal-pinboard.tsx", "utf8");
  const start = editorSource.indexOf(`function ${componentName}(`);
  const next = editorSource.indexOf("\nfunction ", start + 1);
  const output = ts.transpileModule(`${editorSource.slice(start, next < 0 ? undefined : next)}\nexports.Component = ${componentName};`, {
    compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const hooks = [];
  const listeners = new Set();
  const pendingEffects = [];
  const resizeCallbacks = new Set();
  const exports = {};
  let cursor = 0;
  let tree;
  let closed = 0;
  let saved;
  const element = (type, props) => ({ type, props });
  const context = {
    exports,
    require: (name) => {
      assert.equal(name, "react/jsx-runtime");
      return { jsx: element, jsxs: element };
    },
    useState(initial) {
      const index = cursor++;
      if (!(index in hooks)) hooks[index] = initial;
      return [hooks[index], (next) => { hooks[index] = typeof next === "function" ? next(hooks[index]) : next; }];
    },
    useRef(initial) {
      const index = cursor++;
      return hooks[index] ??= { current: initial };
    },
    useEffect(callback, dependencies) {
      const index = cursor++;
      const previous = hooks[index];
      if (!previous || dependencies.some((value, i) => !Object.is(value, previous.dependencies[i]))) {
        pendingEffects.push(() => {
          previous?.cleanup?.();
          hooks[index] = { dependencies, cleanup: callback() };
        });
      }
    },
    useSyncExternalStore: (_subscribe, getSnapshot) => getSnapshot(),
    ResizeObserver: class {
      constructor(callback) { this.callback = callback; }
      observe() { resizeCallbacks.add(this.callback); }
      disconnect() { resizeCallbacks.delete(this.callback); }
    },
    window: {
      addEventListener: (type, handler) => { assert.equal(type, "beforeunload"); listeners.add(handler); },
      removeEventListener: (type, handler) => { assert.equal(type, "beforeunload"); listeners.delete(handler); },
    },
    Dialog: "Dialog", DialogContent: "DialogContent", DialogTitle: "DialogTitle", DialogDescription: "DialogDescription",
    Popover: "Popover", PopoverAnchor: "PopoverAnchor", PopoverContent: "PopoverContent", PopoverTrigger: "PopoverTrigger",
    PinnedNote: "PinnedNote", NoteEditor: "NoteEditor", Button: "Button", Check: "Check", Trash2: "Trash2", Pin: "Pin",
    Plus: "Plus", Grip: "Grip", Pencil: "Pencil", RotateCcw: "RotateCcw", LoaderCircle: "LoaderCircle", ArrowUpRight: "ArrowUpRight", Scaling: "Scaling", styles: {},
    pinboardPoint, pinboardScale, pinboardNoteSize, pinboardPaper, resizePinboardNote, keepPinboardHandleVisible, pinboardDragStarted, pinboardPosition, newPinboardNote, PINBOARD_LIMIT: 24,
    PINBOARD_SIZE_MIN: 0.6, PINBOARD_SIZE_MAX: 2.4,
    crypto: { randomUUID: () => "3d9d01c4-c0fb-4f2a-ae1e-f51365686958" },
    PINBOARD_TEXT_LIMIT: 2000, PINBOARD_COLORS: ["butter", "sage"], PINBOARD_SHAPES: ["note", "card"],
    COLOR_LABELS: { butter: ["Butter", "Butter"], sage: ["Sage", "Salbei"] },
    SHAPE_LABELS: { note: ["Sticky note", "Notiz"], card: ["Index card", "Karte"] },
  };
  runInNewContext(output, context);
  const render = () => {
    cursor = 0;
    tree = exports.Component({ note: original, isNew, de: false, index: 0, onClose: () => { closed++; },
      onEdit: () => { closed++; }, onMove: (value) => { saved = value; }, onSave: (value) => { saved = value; }, onDelete: () => {}, ...additionalProps });
    if (additionalProps.testCanvas) {
      const attach = (node) => {
        if (!node || typeof node !== "object") return;
        if (Array.isArray(node)) { node.forEach(attach); return; }
        if (node.props?.["data-testid"] === "pinboard-surface") node.props.ref.current = additionalProps.testCanvas;
        attach(node.props?.children);
      };
      attach(tree);
    }
    while (pendingEffects.length) pendingEffects.shift()();
    return tree;
  };
  const find = (predicate, node = tree) => {
    if (!node || typeof node !== "object") return undefined;
    if (Array.isArray(node)) return node.map((child) => find(predicate, child)).find(Boolean);
    return predicate(node) ? node : find(predicate, node.props?.children ?? null);
  };
  render();
  return {
    render, find,
    get closed() { return closed; },
    get saved() { return saved; },
    get warningCount() { return listeners.size; },
    get resizeObserverCount() { return resizeCallbacks.size; },
    resizeCanvas: (width, height) => {
      Object.assign(additionalProps.testCanvas, { clientWidth: width, clientHeight: height });
      for (const callback of resizeCallbacks) callback();
      render();
    },
    button: (label) => find((node) => node.type === "Button" && node.props.children === label),
    edit: (text) => { find((node) => node.type === "textarea").props.onChange({ target: { value: text } }); render(); },
    dismiss: () => { tree.props.onOpenChange(false); render(); },
    unload: () => {
      const event = { prevented: false, returnValue: "initial", preventDefault() { this.prevented = true; } };
      for (const listener of listeners) listener(event);
      return event;
    },
    unmount: () => { for (const hook of hooks) hook?.cleanup?.(); },
  };
}

test("dirty new and existing editor drafts survive dialog dismissal until explicit discard", () => {
  for (const isNew of [false, true]) {
    const editor = editorFixture(note(isNew ? "" : "Saved note"), isNew);
    editor.edit("A private unfinished thought");
    editor.dismiss(); // X, Escape, and backdrop share Dialog.onOpenChange.
    assert.equal(editor.closed, 0);
    assert.ok(editor.find((node) => node.props.role === "alert"));
    editor.dismiss(); // Repeated Escape must not bypass the confirmation.
    assert.equal(editor.closed, 0);
    editor.button("Keep editing").props.onClick();
    editor.render();
    assert.equal(editor.find((node) => node.type === "textarea").props.value, "A private unfinished thought");
    assert.equal(editor.find((node) => node.props.role === "alert"), undefined);
    editor.button("Cancel").props.onClick();
    editor.render();
    assert.equal(editor.closed, 0);
    editor.button("Discard changes").props.onClick();
    assert.equal(editor.closed, 1);
    assert.equal(editor.saved, undefined);
    editor.unmount();
    assert.equal(editor.warningCount, 0);
  }
});

test("a background click anchors the new note at the click and clamps canvas edges", () => {
  const position = pinboardPoint(320, 90, 900, 650);
  assert.equal(position.x * (900 - 210) + 105, 320);
  assert.equal(position.y * (650 - 216) + 13, 90);
  assert.deepEqual(pinboardPoint(-100, -100, 900, 650), { x: 0, y: 0 });
  assert.deepEqual(pinboardPoint(9999, 9999, 320, 450), { x: 1, y: 1 });
  assert.deepEqual(pinboardPoint(NaN, Infinity, 100, 100), { x: 0, y: 0 });
  assert.equal(pinboardDragStarted(3, 3), false);
  assert.equal(pinboardDragStarted(3, 4), true);
});

test("paper scales with the available canvas, preserves shapes, and never grows beyond desktop sizes", () => {
  assert.equal(pinboardScale(900, 650), 1);
  assert.equal(pinboardScale(1600, 1000), 1);
  assert.equal(pinboardScale(347, 636), 0.62);
  assert.deepEqual(pinboardNoteSize("note", pinboardScale(347, 636)), { width: 130, height: 134 });
  assert.deepEqual(pinboardNoteSize("card", pinboardScale(347, 636)), { width: 157, height: 110 });
  assert.deepEqual(pinboardNoteSize("circle", pinboardScale(347, 636)), { width: 135, height: 135 });
  assert.ok(pinboardScale(750, 650) < pinboardScale(900, 650));
  assert.ok(pinboardScale(900, 400) < pinboardScale(900, 650));
  for (const [width, height] of [[280, 420], [620, 220], [120, 90], [900, 650]]) {
    for (const shape of ["note", "card", "circle"]) {
      const size = pinboardNoteSize(shape, pinboardScale(width, height));
      assert.ok(size.width <= width && size.height <= height);
    }
  }
  for (const dimensions of [[0, 0], [NaN, Infinity], [-1, 400]]) {
    assert.equal(pinboardScale(...dimensions), 0.62);
  }
});

test("responsive click placement shares rendered dimensions for every paper shape", () => {
  for (const shape of ["note", "card", "circle"]) {
    for (const [width, height] of [[347, 636], [720, 420], [900, 650]]) {
      const size = pinboardNoteSize(shape, pinboardScale(width, height));
      const position = pinboardPoint(width / 2, 80, width, height, shape);
      assert.ok(Math.abs(position.x * (width - size.width) + size.width / 2 - width / 2) < 1e-9);
      assert.ok(Math.abs(position.y * (height - size.height) + 13 - 80) < 1e-9);
      assert.deepEqual(pinboardPoint(9999, 9999, width, height, shape), { x: 1, y: 1 });
    }
  }
});

test("resizing the canvas changes only presentation, never the saved board or notes", async () => {
  const { session, writes } = fixture();
  await session.load();
  session.edit([{ ...note(), x: 0.27, y: 0.41 }]);
  await session.save();
  const original = copy(session.getSnapshot());
  const board = editorFixture(note(), false, "Board", { session, testCanvas: { clientWidth: 900, clientHeight: 650 } });
  board.render();
  const pinned = () => board.find((node) => node.type === "PinnedNote");
  assert.equal(pinned().props.canvasSize.width, 900);
  assert.equal(board.resizeObserverCount, 1);
  board.resizeCanvas(347, 636);
  assert.equal(pinned().props.canvasSize.width, 347);
  assert.deepEqual(session.getSnapshot(), original);
  board.resizeCanvas(900, 650);
  assert.equal(pinned().props.canvasSize.width, 900);
  assert.deepEqual(session.getSnapshot(), original);
  assert.equal(writes.length, 1);
  board.unmount();
  assert.equal(board.resizeObserverCount, 0);
});

test("compact paper keeps drag distance in canvas pixels and maintains normalized positions", () => {
  const scale = pinboardScale(347, 636);
  const size = pinboardNoteSize("note", scale);
  const card = editorFixture({ ...note(), x: 0.2, y: 0.3 }, false, "PinnedNote", {
    canvas: { current: { getBoundingClientRect: () => ({ width: 347, height: 636 }) } }, canvasSize: { width: 347, height: 636 },
  });
  const article = card.find((node) => node.type === "article");
  assert.equal(article.props.style["--note-width"], `${size.width}px`);
  assert.equal(article.props.style["--note-height"], `${size.height}px`);
  const node = { offsetWidth: size.width, offsetHeight: size.height, setPointerCapture() {} };
  article.props.ref.current = node;
  const event = { button: 0, pointerId: 4, clientX: 100, clientY: 100, currentTarget: node };
  article.props.onPointerDown(event);
  article.props.onPointerMove({ ...event, clientX: 121.7, clientY: 150.2 });
  assert.ok(Math.abs(card.saved.x - 0.3) < 1e-9);
  assert.ok(Math.abs(card.saved.y - 0.4) < 1e-9);
  assert.equal(card.saved.text, note().text);
  card.unmount();
});

test("the board keeps its creation hint without duplicating the application theme control", async () => {
  const { session } = fixture();
  await session.load();
  for (const de of [false, true]) {
    const board = editorFixture(note(), false, "Board", { session, de });
    assert.ok(board.find((node) => node.type === "p" && node.props.children === (de
      ? "Klicken zum Anpinnen · Ziehen zum Anordnen" : "Click to pin · Drag to arrange")));
    assert.equal(board.find((node) => node.props.role === "group"
      && ["Appearance", "Darstellung"].includes(node.props["aria-label"])), undefined);
    assert.equal(board.find((node) => node.type === "button" && /appearance|Darstellung/.test(node.props["aria-label"] ?? "")), undefined);
    board.unmount();
  }
  const boardSource = readFileSync("src/components/home/personal-pinboard.tsx", "utf8");
  assert.doesNotMatch(boardSource, /useTheme|setTheme|BoardAppearance/);
  const providers = readFileSync("src/app/providers.tsx", "utf8");
  assert.match(providers, /<ThemeProvider\s+attribute="class"/);
  assert.match(providers, /storageKey="sixsentences-theme"/);
  const settings = readFileSync("src/components/settings/account-settings.tsx", "utf8");
  assert.match(settings, /function AccountSettings\(/);
  assert.match(settings, /setTheme\(option\.value\)/);
});

test("both quiet board themes retain readable text contrast", () => {
  const css = readFileSync("src/components/home/personal-pinboard.module.css", "utf8");
  const luminance = (hex) => {
    const rgb = hex.match(/[a-f\d]{2}/gi).map((value) => parseInt(value, 16) / 255)
      .map((value) => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
    return rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722;
  };
  for (const block of [css.match(/\.home \{([^}]+)\}/s)[1], css.match(/:global\(\.dark\) \.home \{([^}]+)\}/s)[1]]) {
    const colors = Object.fromEntries([...block.matchAll(/--board-([a-z-]+): #(\w{6});/g)].map((match) => [match[1], match[2]]));
    const surface = luminance(colors.surface);
    for (const key of ["text", "muted", "mode"]) {
      const foreground = luminance(colors[key]);
      const ratio = (Math.max(surface, foreground) + 0.05) / (Math.min(surface, foreground) + 0.05);
      assert.ok(ratio >= 4.5, `${key} needs 4.5:1 contrast, got ${ratio}`);
    }
  }
});

test("notes sit above transparent foreground text while only the composer has a protected higher layer", () => {
  const page = readFileSync("src/app/(app)/page.tsx", "utf8");
  const css = readFileSync("src/components/home/personal-pinboard.module.css", "utf8");
  assert.doesNotMatch(page + css, /introShield/);
  assert.match(page, /pointer-events-none relative w-full/);
  assert.match(page, /pointer-events-none relative" data-pinboard-composer/);
  const composer = readFileSync("src/components/search/composer.tsx", "utf8");
  assert.match(composer, /pointer-events-auto relative z-20 rounded-\[2rem\] border bg-card/);
  assert.match(page, /<Composer seed=\{seed\}/);
  assert.match(css, /\.note \{[^}]*z-index: 10/);
  assert.match(css, /\.note\[data-revealed="true"\], \.dragging \{[^}]*z-index: 30/);
  assert.doesNotMatch(css.match(/\.board \{([^}]+)\}/)[1], /z-index/);
});

test("left and right background clicks open a local creation popup, never child or drag clicks", async () => {
  for (const type of ["click", "contextmenu"]) {
    const { session, writes } = fixture();
    await session.load();
    const board = editorFixture(note(), false, "Board", { session });
    const surface = () => board.find((node) => node.props["data-testid"] === "pinboard-surface");
    const area = { getBoundingClientRect: () => ({ left: 20, top: 30, width: 900, height: 650 }) };
    const event = { type, target: area, currentTarget: area, clientX: 340, clientY: 120, preventDefault() {} };
    surface().props.onPointerDown(event);
    surface().props[type === "click" ? "onClick" : "onContextMenu"]({ ...event, target: {} });
    board.render();
    assert.equal(board.find((node) => node.type === "Popover").props.open, false);
    surface().props[type === "click" ? "onClick" : "onContextMenu"](event);
    board.render();
    assert.equal(board.find((node) => node.type === "Popover").props.open, true);
    board.find((node) => node.type === "Button" && node.props.children?.includes?.("Pin a note here")).props.onClick();
    board.render();
    const editor = board.find((node) => node.type === "NoteEditor");
    assert.equal(editor.props.isNew, true);
    assert.equal(editor.props.note.x, pinboardPoint(320, 90, 900, 650).x);
    assert.equal(editor.props.note.y, pinboardPoint(320, 90, 900, 650).y);
    assert.deepEqual(Object.keys(editor.props.note).sort(), Object.keys(note()).sort());
    editor.props.onSave({ ...editor.props.note, text: "Keep the exact click position" });
    await session.save();
    assert.equal(writes[0].notes[0].x, editor.props.note.x);
    assert.equal(writes[0].notes[0].y, editor.props.note.y);
    assert.equal(session.getSnapshot().dirty, false);
    board.unmount();
  }
  const { session } = fixture();
  await session.load();
  const board = editorFixture(note(), false, "Board", { session });
  const surface = board.find((node) => node.props["data-testid"] === "pinboard-surface");
  const target = {};
  surface.props.onPointerDown({ target, currentTarget: target, clientX: 50, clientY: 50 });
  surface.props.onClick({ type: "click", target, currentTarget: target, clientX: 100, clientY: 50 });
  board.render();
  assert.equal(board.find((node) => node.type === "Popover").props.open, false);
  board.unmount();
});

test("note body captures the pointer but moves only after threshold, without opening the editor", () => {
  const canvas = { current: { getBoundingClientRect: () => ({ width: 900, height: 650 }) } };
  const card = editorFixture(note(), false, "PinnedNote", { canvas });
  let article = card.find((node) => node.type === "article");
  const captured = [];
  const node = { offsetWidth: 210, offsetHeight: 216, setPointerCapture: (pointer) => captured.push(pointer) };
  article.props.ref.current = node;
  const pointer = { button: 0, pointerId: 1, clientX: 100, clientY: 100, currentTarget: node };
  article.props.onPointerDown(pointer);
  article.props.onPointerMove({ ...pointer, clientX: 103, clientY: 103 });
  assert.equal(card.saved, undefined);
  assert.deepEqual(captured, [1]);
  article.props.onPointerMove({ ...pointer, clientX: 169, clientY: 143.4 });
  assert.deepEqual(captured, [1]);
  assert.equal(card.saved.x, note().x + 0.1);
  assert.ok(Math.abs(card.saved.y - note().y - 0.1) < 1e-12);
  article.props.onPointerUp(pointer);
  assert.equal(card.saved.x, note().x + 0.1);
  card.render();
  article = card.find((item) => item.type === "article");
  let stopped = false;
  const click = { detail: 1, target: { closest: () => null }, stopPropagation: () => { stopped = true; } };
  article.props.onClick(click);
  assert.equal(stopped, true);
  assert.equal(card.closed, 0);
  article.props.onPointerDown(pointer);
  article.props.onPointerUp(pointer);
  article.props.onClick(click);
  assert.equal(card.closed, 1);
  article.props.onPointerDown(pointer);
  article.props.onPointerMove({ ...pointer, clientX: 169 });
  article.props.onPointerCancel(pointer);
  article.props.onClick({ ...click, detail: 0 });
  assert.equal(card.closed, 2);
  article.props.onPointerDown(pointer);
  article.props.onPointerMove({ ...pointer, clientX: 169 });
  article.props.onLostPointerCapture(pointer);
  article.props.onClick({ ...click, detail: 0 });
  assert.equal(card.closed, 3);
  card.unmount();
});

test("resize grip captures only its pointer and never edits, drags, or settles a plain click", () => {
  let settled;
  const original = { ...note(), rotation: 0, x: 0.2, y: 0.3 };
  const card = editorFixture(original, false, "PinnedNote", {
    canvas: { current: { getBoundingClientRect: () => ({ width: 900, height: 650 }) } },
    onSettle: (value) => { settled = value; },
  });
  const handle = card.find((node) => node.props["data-resize-handle"] !== undefined);
  const captures = [];
  let stopped = 0;
  const target = { setPointerCapture: (id) => captures.push(id), focus() {} };
  const event = { button: 0, pointerId: 7, clientX: 200, clientY: 200, currentTarget: target, stopPropagation() { stopped++; } };
  handle.props.onPointerDown(event);
  handle.props.onPointerUp(event);
  assert.equal(settled, undefined);
  assert.equal(card.saved, undefined);
  handle.props.onClick(event);
  assert.equal(card.closed, 0);
  handle.props.onPointerDown(event);
  handle.props.onPointerMove({ ...event, pointerId: 8, clientX: 410, clientY: 416 });
  assert.equal(card.saved, undefined);
  handle.props.onPointerMove({ ...event, clientX: 305, clientY: 308 });
  assert.equal(card.saved.size, 1.5);
  handle.props.onPointerUp(event);
  assert.equal(settled.size, 1.5); // No React rerender was needed to retain the last move.
  assert.deepEqual(captures, [7, 7]);
  assert.ok(stopped >= 6);
  handle.props.onLostPointerCapture(event);
  assert.equal(settled.size, 1.5);
  card.unmount();
});

test("resize keyboard controls are bounded and minimum circle text reserves fixed controls", () => {
  const card = editorFixture({ ...note(), shape: "circle", size: 0.6 }, false, "PinnedNote", {
    canvas: { current: { getBoundingClientRect: () => ({ width: 347, height: 636 }) } },
    canvasSize: { width: 347, height: 636 },
  });
  const article = card.find((node) => node.type === "article");
  assert.equal(article.props.style["--note-width"], "120px");
  assert.equal(article.props.style["--note-lines"], 2);
  assert.equal(article.props["data-compact"], true);
  const handle = card.find((node) => node.props["data-resize-handle"] !== undefined);
  const key = (key, shiftKey = false) => ({ key, shiftKey, preventDefault() {}, stopPropagation() {} });
  handle.props.onKeyDown(key("ArrowLeft"));
  assert.equal(card.saved.size, 0.6);
  handle.props.onKeyDown(key("ArrowRight", true));
  assert.ok(card.saved.size > 0.6);
  handle.props.onKeyDown(key("Home"));
  assert.equal(card.saved.size, 0.6);
  handle.props.onKeyDown(key("End"));
  assert.equal(card.saved.size, 2.4);
  card.unmount();
});

test("show-on-board is a temporary no-write recovery action with one-shot focus handoff", async () => {
  const { session, writes } = fixture();
  await session.load();
  session.edit([note()]);
  await session.save();
  const original = copy(session.getSnapshot());
  const board = editorFixture(note(), false, "Board", { session });
  const show = () => board.find((node) => node.props["aria-label"] === "Show on board: note 1");
  const popup = () => board.find((node) => node.type === "PopoverContent" && node.props["aria-label"] === "Your notes");
  const pinned = () => board.find((node) => node.type === "PinnedNote");
  show().props.onClick();
  board.render();
  assert.equal(pinned().props.revealed, true);
  const firstFocus = pinned().props.revealFocus;
  let prevented = 0;
  popup().props.onCloseAutoFocus({ preventDefault() { prevented++; } });
  assert.equal(prevented, 1);
  popup().props.onCloseAutoFocus({ preventDefault() { prevented++; } });
  assert.equal(prevented, 1); // Later Escape restores normal popup focus.
  show().props.onClick();
  board.render();
  assert.ok(pinned().props.revealFocus > firstFocus); // Re-showing the same note refocuses it.
  assert.deepEqual(session.getSnapshot(), original);
  assert.equal(writes.length, 1);
  board.unmount();
});

test("editor unload warns only for changed content and removes the listener on revert or unmount", () => {
  const editor = editorFixture(note("Original"));
  assert.equal(editor.unload().prevented, false);
  editor.edit("Unsaved");
  assert.equal(editor.warningCount, 1);
  assert.equal(editor.unload().prevented, true);
  assert.equal(editor.unload().returnValue, "");
  editor.edit("Another change");
  assert.equal(editor.warningCount, 1);
  editor.edit("Original");
  assert.equal(editor.warningCount, 0);
  editor.dismiss();
  assert.equal(editor.closed, 1);
  editor.edit("Pending when auth boundary unmounts editor");
  editor.unmount();
  assert.equal(editor.warningCount, 0);
});

test("style-only editor changes are guarded and Apply still submits the retained draft", () => {
  for (const change of [
    (editor) => editor.find((node) => node.props["aria-label"] === "Sage").props.onClick(),
    (editor) => editor.button("Index card").props.onClick(),
    (editor) => editor.find((node) => node.props.id === "pinboard-rotation").props.onChange({ target: { value: "8" } }),
  ]) {
    const editor = editorFixture();
    change(editor);
    editor.render();
    assert.equal(editor.unload().prevented, true);
    editor.dismiss();
    assert.equal(editor.closed, 0);
    editor.find((node) => node.type === "Button" && Array.isArray(node.props.children)
      && node.props.children.includes("Apply changes")).props.onClick();
    assert.equal(editor.saved.text, note().text);
    assert.notDeepEqual(editor.saved, note());
    editor.unmount();
  }
});

test("editor save shortcut preserves multiline entry, composition, and Unicode validation", () => {
  for (const modifier of ["ctrlKey", "metaKey"]) {
    const editor = editorFixture(note(""), true);
    const key = (overrides = {}) => ({ key: "Enter", ctrlKey: false, metaKey: false,
      nativeEvent: { isComposing: false }, preventDefault() {}, ...overrides });
    const input = () => editor.find((node) => node.type === "textarea");
    input().props.onKeyDown(key({ [modifier]: true }));
    assert.equal(editor.saved, undefined);
    editor.edit("🌱".repeat(2001));
    input().props.onKeyDown(key({ [modifier]: true }));
    assert.equal(editor.saved, undefined);
    editor.edit("An unfinished first line\nA second thought");
    input().props.onKeyDown(key());
    input().props.onKeyDown(key({ [modifier]: true, nativeEvent: { isComposing: true } }));
    assert.equal(editor.saved, undefined);
    let prevented = false;
    input().props.onKeyDown(key({ [modifier]: true, preventDefault() { prevented = true; } }));
    assert.equal(prevented, true);
    assert.equal(editor.saved.text, "An unfinished first line\nA second thought");
    editor.unmount();
  }
});

test("closing an editor restores the Notes control if its popup opener no longer exists", () => {
  let focused = false;
  const editor = editorFixture(note(), false, "NoteEditor", { fallbackFocus: { current: { focus() { focused = true; } } } });
  let prevented = false;
  editor.find((node) => node.type === "DialogContent").props.onCloseAutoFocus({ preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  assert.equal(focused, true);
  editor.unmount();
});
