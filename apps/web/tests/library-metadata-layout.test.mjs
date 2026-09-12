import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const editor = readFileSync("src/components/library/metadata-editor.tsx", "utf8");
const dialog = readFileSync("src/components/ui/dialog.tsx", "utf8");

test("metadata fields shrink and scroll inside a viewport-bounded flex dialog", () => {
  assert.match(editor, /<DialogContent className="flex max-h-\[90dvh\] flex-col gap-0 overflow-hidden p-0 sm:max-w-3xl"/);
  assert.match(editor, /<DialogHeader className="shrink-0 /);
  assert.match(editor, /<div className="min-h-0 flex-1 space-y-6 overflow-y-auto overscroll-contain /);
  // Header text and mobile footer height vary. Subtracting a guessed height
  // from the viewport can push the actions beyond the clipped dialog edge.
  assert.doesNotMatch(editor, /max-h-\[calc\(90vh-12rem\)\]/);
});

test("metadata actions stay outside the scroll body without inherited negative footer margins", () => {
  assert.match(editor, /<\/div>\s*<DialogFooter className="mx-0 mb-0 shrink-0 /);
  // The shared footer uses negative margins to offset standard dialog padding.
  // This p-0 dialog must override them locally, without changing other dialogs.
  assert.match(dialog, /-mx-4 -mb-4 flex flex-col-reverse gap-2/);
  assert.match(dialog, /sm:flex-row sm:justify-end/);
  const footer = editor.slice(editor.indexOf("<DialogFooter"), editor.indexOf("</DialogFooter>"));
  assert.match(footer, /onClick=\{\(\) => setOpen\(false\)\}/);
  assert.match(footer, /disabled=\{save\.isPending \|\| Object\.keys\(changes\)\.length === 0\}/);
  assert.match(footer, /onClick=\{\(\) => save\.mutate\(\)\}/);
});
