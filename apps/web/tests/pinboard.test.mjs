import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync("src/lib/pinboard.ts", "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
}).outputText;
const { PinboardSession, appendPinboardNote, arrangePinboardNotes, newPinboardNote, pinboardPosition, validatePinboard } = await import(
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
