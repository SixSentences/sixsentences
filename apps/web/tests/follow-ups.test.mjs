import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const source = readFileSync(join(process.cwd(), "src/lib/follow-ups.ts"), "utf8");

test("paper comparison is offered only when at least two papers are available", () => {
  assert.match(
    source,
    /!isSinglePaperTask && \(evidenceSourceCount >= 2 \|\| paperCount >= 2\)/,
  );
  assert.match(source, /evidencePassageCount > 0/);
  assert.doesNotMatch(source, /evidencePassageCount >= 2/);
  assert.doesNotMatch(source, /\|\| hasPapers/);
});

test("a named single-paper task teaches paper actions instead of a fake comparison", () => {
  assert.match(source, /const isSinglePaperTask = !isComparative/);
  assert.match(source, /"Explain this paper"/);
  assert.match(source, /"Trace later work"/);
});
