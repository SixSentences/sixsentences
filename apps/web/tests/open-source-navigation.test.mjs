import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const menu = readFileSync(
  join(process.cwd(), "src/components/shell/user-menu.tsx"),
  "utf8",
);

test("open-source feedback navigation uses the canonical GitHub Issues page", () => {
  assert.match(
    menu,
    /href="https:\/\/github\.com\/SixSentences\/sixsentences\/issues"/,
  );
  assert.match(menu, /target="_blank"/);
  assert.match(menu, /rel="noopener noreferrer"/);
  assert.match(menu, /Ideas on GitHub/);
  assert.doesNotMatch(menu, /window\.location\.assign\("\/ideas"\)|feature votes/i);
});
