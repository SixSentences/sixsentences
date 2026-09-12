import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const read = (path) => readFileSync(join(process.cwd(), path), "utf8");
const limits = read("src/lib/figure-limits.ts");
const figures = read("src/app/(app)/figures/page.tsx");
const actionCard = read("src/components/agent/workspace-action-card.tsx");

test("Visual Lab exposes one honest 16k brief contract on every editable surface", () => {
  assert.match(limits, /FIGURE_PROMPT_MAX_CHARACTERS = 16_000/);
  assert.match(figures, /maxLength=\{visiblePromptLimit\}/);
  assert.match(figures, /FIGURE_PROMPT_MAX_CHARACTERS\.toLocaleString\(\)/);
  assert.match(figures, /Shorten the complete brief to 16,000 characters/);
  assert.match(figures, /16,000-character limit/);
  assert.match(actionCard, /maxLength=\{FIGURE_PROMPT_MAX_CHARACTERS\}/);
  assert.match(actionCard, /prompt\.length\.toLocaleString\(\)/);
  assert.match(actionCard, /visualPromptTooLong/);
  assert.match(actionCard, /Shorten the saved visual brief to 16,000 characters/);
  assert.match(actionCard, /\|\| visualPromptTooLong/);
  assert.doesNotMatch(figures, /FIGURE_PROMPT_LIMIT|2,000-character limit/);
  assert.doesNotMatch(actionCard, /maxLength=\{2000\}|\/ 2,000/);
});
