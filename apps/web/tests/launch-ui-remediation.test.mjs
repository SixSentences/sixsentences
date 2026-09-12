import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const read = (path) => readFileSync(join(process.cwd(), path), "utf8");

test("DPA reacceptance prefills only an explicitly confirmed controller identity", () => {
  const source = read("src/components/legal-reacceptance.tsx");
  assert.match(source, /useState\(\(\) => me\.legal_controller_name \?\? ""\)/);
  assert.match(source, /setControllerName\(event\.target\.value\); setDpaAccepted\(false\)/);
  assert.match(source, /controller_name: controllerName\.trim\(\)/);
  assert.match(source, /controller_authority_confirmed: needDpa && dpaAccepted/);
  assert.match(source, /dpa_version: me\.current_dpa_version/);
  assert.doesNotMatch(source, /legal_controller_name\s*\?\?\s*me\.(?:first_name|email|org_name)/);
});

test("switching loading errors are distinct from genuine non-owner access", () => {
  const source = read("src/components/settings/switching-controls.tsx");
  assert.match(source, /enabled: me\?\.role === "owner"/);
  assert.match(source, /if \(me\.role !== "owner"\)/);
  assert.match(source, /if \(!data \|\| error\) return \(/);
  assert.match(source, /Switching information could not be loaded/);
  assert.match(source, /onClick=\{\(\) => void refetch\(\)\}/);
  assert.doesNotMatch(source, /if \(!data \|\| error\).*available to workspace owners/);
});

test("the Library reader and cards respond to available pane width, not only viewport", () => {
  const source = read("src/app/(app)/library/page.tsx");
  assert.match(source, /layout\.getBoundingClientRect\(\)\.width < 1100/);
  assert.match(source, /observer\.observe\(layout\)/);
  assert.match(source, /@container\/library min-h-0 min-w-0/);
  assert.match(source, /min-w-\[35rem\]/);
  assert.match(source, /flex w-full shrink-0 flex-wrap items-center justify-end gap-1\.5 @min-\[52rem\]\/library:w-auto/);
  assert.match(source, /role=\{compactSecondarySurface \? "dialog" : "complementary"\}/);
  assert.match(source, /aria-modal=\{compactSecondarySurface \|\| undefined\}/);
});

test("writer input has its own full-width row instead of competing with controls", () => {
  const source = read("src/app/(app)/writer/[id]/page.tsx");
  const composer = source.slice(source.indexOf('data-tour="writer-agent-composer"'), source.indexOf("/* ---------- the page ---------- */"));
  assert.match(composer, /flex min-w-0 flex-wrap items-end gap-2/);
  assert.match(composer, /aria-label="Message to the manuscript assistant"/);
  assert.match(composer, /min-h-16 min-w-0 w-full basis-full/);
});

test("study form columns depend on their container and keep generous split minimums", () => {
  const study = read("src/app/(app)/interviews/studies/[id]/page.tsx");
  const notice = read("src/components/participant-information.tsx");
  assert.match(study, /primaryMinPx=\{400\}/);
  assert.match(study, /secondaryMinPx=\{620\}/);
  assert.match(study, /@container\/study min-h-0 min-w-0/);
  assert.match(study, /@min-\[56rem\]\/study:grid-cols-2/);
  assert.match(study, /@min-\[28rem\]\/study-controls:grid-cols-2/);
  assert.match(notice, /@min-\[32rem\]\/participant-information:grid-cols-2/);
  assert.match(notice, /@min-\[32rem\]\/participant-information:col-span-2/);
});

test("review copy distinguishes final output cap from screening and search expansion", () => {
  const run = read("src/components/run/run-view.tsx");
  const composer = read("src/components/search/composer.tsx");
  assert.doesNotMatch(run, /Fast \(non-exhaustive\)/);
  assert.match(run, /No query expansion/);
  assert.match(run, /final papers/);
  assert.match(run, /not retrieval or screening/);
  assert.match(composer, /Expand search queries/);
  assert.match(composer, /This limit applies after screening and selection/);
  assert.match(composer, /body\.paper_limit = Number\(options\.paperLimit\)/);
  assert.match(composer, /exhaustive: options\.exhaustive/);
});
