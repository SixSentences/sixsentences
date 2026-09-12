import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const source = readFileSync(
  join(process.cwd(), "src/app/(app)/figures/page.tsx"),
  "utf8",
);

test("Visual Lab separates generated work from saved source figures", () => {
  assert.match(source, /figure\.config\.kind !== "source"/);
  assert.match(source, /figure\.config\.kind === "source"/);
  assert.match(source, /role="tablist"/);
  assert.match(source, />\s*Generated\s*</);
  assert.match(source, />\s*Saved sources\s*</);
});

test("source figures cannot expose the generated prompt reuse action", () => {
  assert.match(source, /selected\.config\.kind !== "source" \? \(/);
  assert.match(source, /Reuse prompt/);
  assert.match(source, /Save a figure from a paper in the Library/);
});

test("gallery figures stay inset and contained inside their cards", () => {
  assert.match(source, /data-figure-card-media/);
  assert.match(
    source,
    /aspect-\[4\/3\][^"\n]*overflow-hidden[^"\n]*p-3[^"\n]*sm:p-4/,
  );
  assert.match(source, /data-figure-card-frame/);
  assert.match(
    source,
    /h-full w-full min-h-0 min-w-0[^"\n]*overflow-hidden[^"\n]*rounded-xl/,
  );
  assert.match(source, /h-full w-full object-contain/);
});
