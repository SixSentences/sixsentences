import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const approval = readFileSync("src/components/search/public-web-search-approval.tsx", "utf8");

test("public-search approval keeps the compact acknowledgement and exclusions visible", () => {
  const visible = approval.slice(0, approval.indexOf("<details"));
  for (const text of [
    "Nur öffentliche Suchbegriffe",
    "Public search terms only",
    "Keine persönlichen oder vertraulichen Inhalte.",
    "No personal or confidential content.",
  ]) assert.ok(visible.includes(text));
  assert.match(visible, /checked=\{confirmed\}/);
  assert.match(visible, /onCheckedChange=\{\(checked\) => onConfirmedChange\(checked === true\)\}/);
  assert.match(visible, /aria-required="true"/);
  assert.match(approval, /const id = useId\(\)/);
  assert.match(approval, /htmlFor=\{`\$\{id\}-confirmed`\}/);
  assert.doesNotMatch(approval, /Dialog|Popover|window\.confirm/);
  assert.match(visible, /sm:flex-row sm:items-center sm:gap-3/);
  assert.match(visible, /flex-wrap items-baseline gap-x-2/);
  assert.match(approval, /<details className="ml-auto[^"]*open:basis-full"/);
});

test("readonly requests remain fully readable and editable search terms reset their approval", () => {
  assert.match(approval, /onQueryChange\?: \(value: string\) => void/);
  assert.match(approval, /onQueryChange \? \([\s\S]*<Input[\s\S]*value=\{query\}[\s\S]*maxLength=\{400\}/);
  assert.match(approval, /onQueryChange\(event\.target\.value\);\s*onConfirmedChange\(false\)/);
  assert.match(approval, /max-h-24 min-w-0 flex-1 overflow-y-auto[\s\S]*whitespace-pre-wrap break-words/);
  assert.match(approval, /tabIndex=\{0\}/);
  assert.match(approval, />\s*\{query\}\s*<\/div>/);
  assert.doesNotMatch(approval, /query\.(?:slice|substring)|truncate|line-clamp/);
  for (const label of ["Websuche", "Web search", "Öffentlicher Suchauftrag", "Public search request"]) {
    assert.ok(approval.includes(label));
  }
  assert.match(approval, /placeholder=\{isGerman \? "Öffentliches Suchthema eingeben" : "Enter a public search topic"\}/);
});

test("expandable details distinguish formulated requests from targeted terms without granting workspace forwarding", () => {
  const details = approval.slice(approval.indexOf("<details"));
  assert.match(details, /<summary/);
  assert.doesNotMatch(details, /<details[^>]*\bopen(?:=|>)/);
  for (const text of [
    "Search terms are formulated from the approved public search request",
    "This approval applies only to the search terms shown above",
    "protocol query and criteria",
    "personal data, confidential or sensitive information",
    "does not automatically include conversation history, transcripts, manuscripts or uploads",
    "Public search terms from that context require your explicit approval",
    "OpenRouter to Perplexity",
    "processing may take place in the USA",
  ]) assert.ok(details.includes(text));
  assert.doesNotMatch(details, /conversation history must not be forwarded/);
  assert.doesNotMatch(details, /anonymous|anonymisiert|legally guaranteed|rechtssicher/i);
  assert.match(approval, /const exact = exactQuery \?\? editable/);
  assert.match(details, /full anonymization/);
  assert.match(details, /href=\{publicLegalUrl\("privacy"\)\}/);
});
