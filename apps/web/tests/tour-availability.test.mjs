import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = await readFile(
  new URL("../src/components/tour/product-tour.tsx", import.meta.url),
  "utf8",
);

test("product tour counts only example-backed steps that can be shown", () => {
  assert.match(source, /const availableProductSteps = useMemo/);
  assert.match(
    source,
    /candidate\.route === "demo" && runsQuery\.isFetched/,
  );
  assert.match(
    source,
    /candidate\.route === "writer-demo" && docsQuery\.isFetched/,
  );
  assert.match(source, /const shortCount = availableProductSteps\.filter/);
  assert.match(source, /\{availableProductSteps\.length\}/);
});
