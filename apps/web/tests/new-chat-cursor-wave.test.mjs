import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const page = await readFile(
  new URL("../src/app/(app)/page.tsx", import.meta.url),
  "utf8",
);
const wave = await readFile(
  new URL("../src/components/brand/cursor-wave.tsx", import.meta.url),
  "utf8",
);
const workspaceWave = await readFile(
  new URL("../src/components/brand/new-chat-cursor-wave.tsx", import.meta.url),
  "utf8",
);

test("the empty workspace clips a sparse, theme-aware cursor wave", () => {
  assert.match(page, /<NewChatCursorWave/);
  assert.match(page, /overflow-x-hidden overflow-y-auto/);
  assert.doesNotMatch(page, /new-chat-dot-field/);
  assert.match(workspaceWave, /backgroundColor="transparent"/);
  assert.match(workspaceWave, /cellSize=\{40\}/);
  assert.match(workspaceWave, /mode \? mode === "dark" : resolvedTheme === "dark"/);
});

test("the reusable wave can render transparently without blocking touch scroll", () => {
  assert.match(wave, /context\.clearRect\(0, 0, width, height\)/);
  assert.match(wave, /touchAction: "pan-y"/);
  assert.match(wave, /\.\.\.style/);
});

test("the wave does not accumulate hidden-tab time into its rotation", () => {
  assert.match(wave, /document\.addEventListener\("visibilitychange"/);
  assert.match(wave, /Math\.min\(\(now - lastTime\) \/ 1000, 0\.05\)/);
  assert.match(
    wave,
    /cell\.rotation \+ deltaSeconds \* cell\.rotationSpeed \* activeScale/,
  );
  assert.doesNotMatch(wave, /elapsedSeconds \* cell\.rotationSpeed/);
});
