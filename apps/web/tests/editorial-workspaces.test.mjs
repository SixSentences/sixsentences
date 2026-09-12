import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const read = (path) => readFileSync(join(process.cwd(), path), "utf8");

const primitives = read("src/components/workspace/editorial-workspace.tsx");
const brainstorming = read("src/app/(app)/brainstorming/page.tsx");
const conversations = read("src/app/(app)/interviews/page.tsx");
const surveys = read("src/app/(app)/surveys/page.tsx");

test("editorial workspace primitives preserve native document semantics", () => {
  assert.match(primitives, /export function EditorialMetricStrip/);
  assert.match(primitives, /<dl/);
  assert.match(primitives, /<dt/);
  assert.match(primitives, /<dd/);
  assert.match(primitives, /export function EditorialEmptyState/);
  assert.match(primitives, /aria-labelledby=\{headingId\}/);
  assert.match(primitives, /<h2[\s\S]*?id=\{headingId\}/);
  assert.match(primitives, /aria-hidden="true"/);
});

test("the three workspace surfaces share the quieter editorial hierarchy", () => {
  assert.equal((brainstorming.match(/<EditorialEmptyState/g) ?? []).length, 2);
  assert.equal((conversations.match(/<EditorialMetricStrip/g) ?? []).length, 2);
  assert.equal((conversations.match(/<EditorialEmptyState/g) ?? []).length, 2);
  assert.equal((conversations.match(/<EditorialKicker/g) ?? []).length, 2);
  assert.equal((surveys.match(/<EditorialMetricStrip/g) ?? []).length, 1);
  assert.equal((surveys.match(/<EditorialEmptyState/g) ?? []).length, 1);
  assert.equal((surveys.match(/<EditorialKicker/g) ?? []).length, 1);
});

test("decorative record and empty-state icon bubbles stay out of the redesigned slice", () => {
  const redesigned = `${brainstorming}\n${conversations}\n${surveys}`;
  assert.doesNotMatch(
    redesigned,
    /grid size-(?:10|12|16)[^"\n]*place-items-center[^"\n]*rounded-(?:xl|2xl|3xl)/,
  );
  assert.match(primitives, /h-px w-5 shrink-0 bg-moss\/65/);
  assert.match(primitives, /h-px w-10 bg-moss\/65/);
});

test("new editorial copy and actions remain available in German and English", () => {
  assert.match(brainstorming, /german \? "Privater Arbeitsbereich" : "Private workspace"/);
  assert.match(conversations, /german \? "Erstes Transkript" : "First transcript"/);
  assert.match(conversations, /german \? "KI-Studie" : "AI study"/);
  assert.match(surveys, /german \? "Erste Umfrage" : "First survey"/);
  assert.match(surveys, /disabled=\{create\.isPending\}/);
});
