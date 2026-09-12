import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const globals = readFileSync("src/app/globals.css", "utf8");
const shell = readFileSync("src/components/shell/app-shell.tsx", "utf8");
const splits = readFileSync(
  "src/components/workspace/resizable-workspace-split.tsx",
  "utf8",
);
const works = readFileSync("src/components/run/works-table.tsx", "utf8");
const densityBlock = globals.slice(
  globals.indexOf("@layer base"),
  globals.indexOf("/* Phone layouts"),
);

test("laptops retain the 16px rem base and only large workstations scale up", () => {
  assert.match(globals, /html\s*\{[\s\S]*?font-size:\s*100%/);
  assert.match(
    globals,
    /@media \(min-width: 120rem\) and \(min-height: 64rem\)\s*\{[\s\S]*?font-size:\s*112\.5%/,
  );
  assert.match(
    globals,
    /@media \(min-width: 160rem\) and \(min-height: 75rem\)\s*\{[\s\S]*?font-size:\s*125%/,
  );
  assert.doesNotMatch(globals, /@media \(min-width: (?:48|80)rem\)/);
  assert.doesNotMatch(densityBlock, /(?:zoom\s*:|transform\s*:\s*scale\()/);
});

test("density correction preserves the sidebar and generous split constraints", () => {
  assert.match(shell, /sidebarOpen \? "w-\[17\.5rem\]" : "w-0"/);
  assert.match(splits, /WORKSPACE_PRIMARY_MIN_PX = 400/);
  assert.match(splits, /WORKSPACE_SECONDARY_MIN_PX = 600/);
  assert.match(splits, /WORKSPACE_PRIMARY_MIN_PERCENT = 38/);
  assert.match(splits, /WORKSPACE_PRIMARY_MAX_PERCENT = 58/);
});

test("fit-height paper rows derive their estimate from the active rem base", () => {
  assert.match(works, /COLLAPSED_ROW_ESTIMATE_REM = 6\.1/);
  assert.match(
    works,
    /getComputedStyle\(document\.documentElement\)\.fontSize/,
  );
  assert.match(works, /Number\.isFinite\(rootFontSize\) \? rootFontSize : 16/);
  assert.doesNotMatch(works, /rowEstimate = 122/);
});
