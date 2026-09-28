import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { runInNewContext } from "node:vm";
import ts from "typescript";

const source = readFileSync("src/lib/pinboard.ts", "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
}).outputText;
const { PinboardSession, appendPinboardNote, arrangePinboardNotes, newPinboardNote, pinboardPosition, pinboardPoint, pinboardDragStarted, validatePinboard } = await import(
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
  ]) assert.throws(() => validatePinboard({ revision: 0, notes: [invalid] }));
  assert.throws(() => validatePinboard({ revision: 0, notes: [note(), note()] }));
  assert.throws(() => validatePinboard({ revision: 0, notes: [note(), { ...note(), id: id.toUpperCase() }] }));
  assert.throws(() => validatePinboard({ revision: -1, notes: [] }));
  assert.throws(() => validatePinboard({ revision: 2_147_483_648, notes: [] }));
  assert.throws(() => validatePinboard({ revision: 0, notes: Array(25).fill(note()) }));
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
    window: {
      addEventListener: (type, handler) => { assert.equal(type, "beforeunload"); listeners.add(handler); },
      removeEventListener: (type, handler) => { assert.equal(type, "beforeunload"); listeners.delete(handler); },
    },
    Dialog: "Dialog", DialogContent: "DialogContent", DialogTitle: "DialogTitle", DialogDescription: "DialogDescription",
    Popover: "Popover", PopoverAnchor: "PopoverAnchor", PopoverContent: "PopoverContent", PopoverTrigger: "PopoverTrigger",
    PinnedNote: "PinnedNote", NoteEditor: "NoteEditor", Button: "Button", Check: "Check", Trash2: "Trash2", Pin: "Pin",
    Plus: "Plus", Grip: "Grip", Pencil: "Pencil", RotateCcw: "RotateCcw", LoaderCircle: "LoaderCircle", styles: {},
    pinboardPoint, pinboardDragStarted, pinboardPosition, newPinboardNote, PINBOARD_LIMIT: 24,
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
