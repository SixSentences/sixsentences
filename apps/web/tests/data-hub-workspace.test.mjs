import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const pagePath = new URL("../src/app/(app)/data/[id]/page.tsx", import.meta.url);

test("dataset workspace has one integrated information architecture", async () => {
  const source = await readFile(pagePath, "utf8");

  assert.doesNotMatch(source, /setView\(|view === "studio"|\["studio", BarChart3/);
  assert.doesNotMatch(source, /Dices|rollChart|Surprise me/);
  assert.match(source, /aria-labelledby="dataset-overview-heading"/);
  assert.match(source, /aria-labelledby="schema-heading"/);
  assert.match(source, /aria-labelledby="context-heading"/);
  assert.match(source, /aria-labelledby="versions-heading"/);
  assert.match(source, /aria-labelledby="chart-heading"/);
});

test("integrated workspace preserves real dataset operations", async () => {
  const source = await readFile(pagePath, "utf8");

  assert.match(source, /api\.datasetVersionAdd\(datasetId/);
  assert.match(source, /versionInputRef\.current\?\.click\(\)/);
  assert.match(source, /api\.datasetUpdate\(datasetId/);
  assert.match(source, /setProfileEditing\(false\)/);
  assert.match(source, /setEditDescription\(dataset\.description\)/);
  assert.match(source, /api\.datasetChart\(datasetId/);
  assert.match(source, /chart\.mutate\(\{/);
  assert.match(source, /chartX === chartY/);
  assert.match(source, /href=\{`\/figures\?dataset=\$\{dataset\.public_id\}`\}/);
  assert.match(source, /dataset\.preview\.slice\(0, 12\)/);
  assert.match(source, /dataset\.columns\.map/);
  assert.match(source, /datasetChatStream/);
});

test("dataset workspace is keyboard-labelled and bilingual", async () => {
  const source = await readFile(pagePath, "utf8");

  for (const control of [
    "dataset-description",
    "dataset-provenance",
    "dataset-license",
    "chart-kind",
    "chart-x",
    "chart-y",
    "chart-title",
  ]) {
    assert.match(source, new RegExp(`htmlFor="${control}"`));
    assert.match(source, new RegExp(`id="${control}"`));
  }
  assert.match(source, /<caption className="sr-only">/);
  assert.match(source, /<th scope="col"/);
  assert.match(source, /<th scope="row"/);
  assert.match(source, /href="\/data" aria-label=\{t\("Zurück zum Data Hub", "Back to Data Hub"\)\}/);
  assert.match(source, /const isGerman = me\?\.language === "de"/);
  assert.match(source, /const t = \(de: string, en: string\)/);
  assert.match(source, /"Schema und Datenqualität", "Schema and data quality"/);
  assert.match(source, /"Versionsverlauf", "Version history"/);
});
