import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const page = await readFile(
  new URL("../src/app/(app)/writer/page.tsx", import.meta.url),
  "utf8",
);
const api = await readFile(new URL("../src/lib/api.ts", import.meta.url), "utf8");

test("writer templates expose a revocable copy link instead of source text", () => {
  assert.match(api, /writerTemplateShare:/);
  assert.match(api, /writerTemplateShareRevoke:/);
  assert.match(api, /writerTemplateSharedPreview:/);
  assert.match(api, /writerTemplateSharedImport:/);
  assert.match(page, /Share template/);
  assert.match(page, /Revoke link/);
  assert.match(page, /never exposes the LaTeX source/);
});

test("a shared template link opens a preview and creates an independent copy", () => {
  assert.match(page, /new URLSearchParams\(window\.location\.search\)/);
  assert.match(page, /writerTemplateSharedPreview\(sharedToken\)/);
  assert.match(page, /writerTemplateSharedImport\(sharedToken\)/);
  assert.match(page, /independent copy/);
  assert.match(page, /OwnTemplateMiniature/);
});
