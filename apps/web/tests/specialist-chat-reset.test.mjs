import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const read = async (path) => readFile(new URL(`../${path}`, import.meta.url), "utf8");

const specialistPages = await Promise.all(
  [
    "src/app/(app)/writer/[id]/page.tsx",
    "src/app/(app)/data/[id]/page.tsx",
    "src/app/(app)/interviews/[id]/page.tsx",
    "src/app/(app)/interviews/studies/[id]/page.tsx",
    "src/app/(app)/surveys/[id]/page.tsx",
  ].map(async (path) => [path, await read(path)]),
);
const reset = await read("src/components/agent/specialist-chat-reset.tsx");
const queue = await read("src/components/agent/agent-turn-queue.tsx");
const timeline = await read("src/components/agent-work-status.tsx");
const api = await read("src/lib/api.ts");
const quickAnswer = await read("src/components/run/chat-panel.tsx");

test("every specialist workspace offers one durable clear action and /clear", () => {
  for (const [path, source] of specialistPages) {
    assert.match(source, /useSpecialistChatReset\(/, `${path} has no reset mutation`);
    assert.match(source, /<SpecialistChatResetButton/, `${path} has no clear button`);
    assert.match(source, /<SpecialistChatResetDialog/, `${path} has no clear dialog`);
    assert.match(source, /clearChatTriggerRef/, `${path} has no stable clear trigger ref`);
    assert.match(
      source,
      /returnFocusRef=\{clearChatTriggerRef\}/,
      `${path} does not return dialog focus to its clear trigger`,
    );
    assert.match(
      source,
      /triggerRef=\{clearChatTriggerRef\}/,
      `${path} does not attach its clear trigger ref`,
    );
    assert.match(source, /isClearChatCommand\(/, `${path} ignores \/clear`);
    assert.match(source, /queuedChat\.clear\(\)/, `${path} retains queued turns`);
    assert.match(source, /resetAgentTimeline\(\)/, `${path} retains live timeline state`);
  }
  assert.doesNotMatch(quickAnswer, /useSpecialistChatReset|SpecialistChatResetButton/);
});

test("clear is confirmed, localized and only removes specialist chat routes", () => {
  assert.doesNotMatch(reset, /window\.confirm\(/);
  assert.match(reset, /<AlertDialog/);
  assert.match(reset, /<AlertDialogTitle>/);
  assert.match(reset, /<AlertDialogDescription>/);
  assert.match(reset, /variant="destructive"/);
  assert.match(reset, /disabled=\{clearing\}/);
  assert.match(reset, /onEscapeKeyDown/);
  assert.match(reset, /onCloseAutoFocus/);
  assert.match(reset, /event\.preventDefault\(\)/);
  assert.match(reset, /trigger\?\.isConnected/);
  assert.match(reset, /trigger\.focus\(\)/);
  assert.match(reset, /me\?\.language === "de"/);
  assert.match(reset, /Inhalte und erstellte Dateien im Arbeitsbereich bleiben erhalten/);
  assert.match(api, /specialistChatClear:/);
  for (const prefix of ["/writer", "/datasets", "/interviews", "/voice/studies", "/surveys"]) {
    assert.ok(api.includes(prefix), `missing clear route prefix ${prefix}`);
  }
  const method = api.slice(api.indexOf("specialistChatClear:"), api.indexOf("// -- projects"));
  assert.match(method, /method: "DELETE"/);
  assert.doesNotMatch(method, /\/runs/);
});

test("clearing cancels queued dispatch and drops timeline handoffs", () => {
  assert.match(queue, /generationRef\.current \+= 1/);
  assert.match(queue, /generationRef\.current === generation/);
  assert.match(timeline, /const resetAgentTimeline = useCallback/);
  assert.match(timeline, /setHandoffs\(\[\]\)/);
});
