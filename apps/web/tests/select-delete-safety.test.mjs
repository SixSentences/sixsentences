import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import ts from "typescript";

const read = (path) => readFileSync(join(process.cwd(), path), "utf8");

function sourceFiles(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) return sourceFiles(path);
    return /\.(?:jsx|tsx)$/.test(entry.name) ? [path] : [];
  });
}

function section(source, start, end) {
  const startIndex = source.indexOf(start);
  const endIndex = source.indexOf(end, startIndex + start.length);
  assert.notEqual(startIndex, -1, `missing section start: ${start}`);
  assert.notEqual(endIndex, -1, `missing section end: ${end}`);
  return source.slice(startIndex, endIndex);
}

test("every native select owns one accessible, high-contrast-safe chevron", () => {
  const selects = [];

  for (const file of sourceFiles(join(process.cwd(), "src"))) {
    const source = readFileSync(file, "utf8");
    if (!source.includes("<select")) continue;
    const sourceFile = ts.createSourceFile(
      file,
      source,
      ts.ScriptTarget.Latest,
      true,
      ts.ScriptKind.TSX,
    );

    const visit = (node) => {
      if (
        ts.isJsxElement(node)
        && node.openingElement.tagName.getText(sourceFile) === "select"
      ) {
        const line = sourceFile.getLineAndCharacterOfPosition(node.getStart()).line + 1;
        const location = `${file}:${line}`;
        const opening = node.openingElement.getText(sourceFile);
        const wrapper = node.parent.getText(sourceFile);
        selects.push(location);

        assert.match(opening, /appearance-none/, `${location} must hide the browser arrow`);
        assert.match(opening, /\bpr-(?:\d+|\[[^\]]+\])/, `${location} must reserve chevron space`);
        assert.match(opening, /disabled:cursor-not-allowed/, `${location} needs a disabled cursor`);
        assert.match(opening, /disabled:opacity-50/, `${location} needs a disabled visual state`);
        assert.match(opening, /forced-colors:appearance-auto/, `${location} needs the system fallback`);
        assert.match(wrapper, /<ChevronDown\b/, `${location} needs an owned ChevronDown`);
        assert.match(wrapper, /aria-hidden="true"/, `${location} icon must stay out of the name`);
        assert.match(wrapper, /pointer-events-none/, `${location} icon must not intercept clicks`);
        assert.match(wrapper, /forced-colors:hidden/, `${location} must not show two high-contrast arrows`);
      }
      ts.forEachChild(node, visit);
    };

    visit(sourceFile);
  }

  assert.ok(selects.length > 0, "the audit must discover native selects");
});

test("high-risk manuscript deletes use stale- and pending-safe custom dialogs", () => {
  const writer = read("src/app/(app)/writer/[id]/page.tsx");
  const sources = section(writer, "function SourceList(", "function DatasetList(");
  const figures = section(writer, "function FigureList(", "function SnapshotList(");

  for (const [name, source, mutation, targetField] of [
    ["source", sources, "writerSourceDelete", "title"],
    ["figure", figures, "writerAssetDelete", "filename"],
  ]) {
    assert.match(source, new RegExp(`api\\.${mutation}`));
    assert.match(source, /setDeleteTarget\(\{ id:/, `${name} delete must snapshot its target`);
    assert.match(source, /<ConfirmDeleteDialog[\s\S]*?pending=\{remove\.isPending\}/);
    assert.match(source, /deleteInFlightRef\.current/, `${name} delete must reject double submit`);
    assert.match(source, new RegExp(`current\\.${targetField} !== deleteTarget\\.${targetField}`));
    assert.doesNotMatch(source, /onClick=\{\(\) => remove\.mutate\(/);
  }

  assert.match(writer, /api\.writerFileDelete\(docId, fileId\)/);
  assert.match(writer, /onClick=\{\(\) => setDeleteFileTarget\(file\)\}/);
  assert.match(writer, /<ConfirmDeleteDialog[\s\S]*?pending=\{deleteProjectFile\.isPending\}/);
  assert.match(writer, /current\.path !== deleteFileTarget\.path/);
  assert.match(writer, /deleteFileInFlightRef\.current = true/);
  assert.doesNotMatch(writer, /onClick=\{\(\) => deleteProjectFile\.mutate\(/);
});

test("workspace-agent deletes require a second target-bound custom confirmation", () => {
  const action = read("src/components/agent/workspace-action-card.tsx");

  assert.match(action, /action\.operation === "delete"[\s\S]*?setDeleteTarget\(\{ \.\.\.selectedResource \}\)/);
  assert.match(action, /<ConfirmDeleteDialog[\s\S]*?pending=\{execute\.isPending\}/);
  assert.match(action, /selectedResource\.id !== deleteTarget\.id/);
  assert.match(action, /selectedResource\.resourceType !== deleteTarget\.resourceType/);
  assert.match(action, /deleteInFlightRef\.current = true;[\s\S]*?execute\.mutate\(\)/);
  assert.doesNotMatch(action, /disabled=\{execute\.isPending[\s\S]{0,300}onClick=\{\(\) => execute\.mutate\(\)\}/);
});

test("permanent Knowledge deletion uses the shared dialog and exact page snapshot", () => {
  const knowledge = read("src/components/knowledge/knowledge-editor.tsx");

  assert.match(knowledge, /setConfirmDelete\(\{[\s\S]*?pageId: page\.public_id/);
  assert.equal(
    (knowledge.match(/deleteTarget\.pageId !== pageIdRef\.current/g) ?? []).length,
    2,
  );
  assert.match(knowledge, /<ConfirmDeleteDialog[\s\S]*?pending=\{deleting\}/);
  assert.match(knowledge, /if \(deleteInFlightRef\.current \|\| !confirmDelete\) return/);
  assert.doesNotMatch(knowledge, /window\.confirm/);
  assert.doesNotMatch(
    section(
      knowledge,
      '<div className="mt-5 flex items-center gap-2"',
      '<div className="mt-6 grid min-w-0 gap-6',
    ),
    /<div role="alert"/,
  );
});
