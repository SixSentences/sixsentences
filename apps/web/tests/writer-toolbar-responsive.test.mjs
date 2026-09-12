import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const page = await readFile(
  new URL("../src/app/(app)/writer/[id]/page.tsx", import.meta.url),
  "utf8",
);
const tour = await readFile(
  new URL("../src/components/tour/product-tour.tsx", import.meta.url),
  "utf8",
);

const header = page.slice(page.indexOf("<header"), page.indexOf("</header>") + 9);
const datasetPicker = page.slice(
  page.indexOf("function DatasetList("),
  page.indexOf("/* ---------- linked interview evidence ---------- */"),
);

test("manuscript compile controls stay outside horizontal overflow", () => {
  assert.ok(header.includes('data-tour="writer-compile-controls"'));
  assert.ok(header.includes('data-tour="writer-toolbar-more"'));
  assert.doesNotMatch(header, /overflow-x-auto/);
  assert.doesNotMatch(header, /min-w-max/);
  assert.doesNotMatch(header, /order-\[99\]/);
  assert.match(header, /min-w-8 max-w-\[18rem\].*sm:min-w-\[6\.5rem\]/);
  assert.match(header, /hidden min-w-0 flex-1.*sm:block/);
  assert.ok(
    header.indexOf('data-tour="writer-compile-controls"') >
      header.indexOf('data-tour="writer-toolbar-more"'),
  );
  assert.match(
    header,
    /aria-label=\{compiling \? "Compiling manuscript" : "Compile manuscript"\}/,
  );
  assert.match(header, /aria-pressed=\{autoCompile\}/);
});

test("inline manuscript actions cannot overlap the document title", () => {
  assert.match(
    header,
    /data-tour="writer-document-header"[\s\S]*?overflow-hidden/,
  );
  assert.match(
    header,
    /hidden min-w-0 flex-1 items-center justify-end overflow-hidden 2xl:flex/,
  );
  assert.match(
    header,
    /hidden min-w-0 items-center justify-end gap-x-3 min-\[2350px\]:flex/,
  );
  assert.doesNotMatch(
    header,
    /className="flex min-w-0 items-center justify-end gap-x-3"/,
  );
});

test("secondary manuscript actions remain reachable through More", () => {
  for (const label of [
    "Cite included paper",
    "Sources",
    "Data",
    "Study data",
    "Visuals",
    "Insert artifact",
    "Review manuscript",
    "Version history",
    "Download and transfer",
    "Linked searches",
    "Open manuscript guide",
  ]) {
    assert.ok(header.includes(label), `${label} is missing from the responsive toolbar`);
  }
  assert.match(page, /data-toolbar-panel=\{toolbarPanel\}/);
  assert.match(page, /max-h-\[min\(44rem,calc\(100dvh-1rem\)\)\]/);
  assert.doesNotMatch(
    tour,
    /target: "writer-(?:context-tools|search-links|export-tools)"/,
  );
  assert.match(tour, /target: "writer-toolbar-more"/);
});

test("manuscript data popover stays inside narrow viewports", () => {
  assert.match(header, /data-writer-data-popover/);
  assert.match(header, /collisionPadding=\{16\}/);
  assert.match(header, /w-\[calc\(100vw-2rem\)\]/);
  assert.match(header, /max-w-\[24rem\]/);
  assert.match(header, /overflow-x-hidden/);
  assert.match(header, /aria-label="Workspace data"/);
});

test("manuscript data picker handles long names and resource states accessibly", () => {
  assert.match(datasetPicker, /aria-labelledby=\{headingId\}/);
  assert.match(datasetPicker, /aria-describedby=\{descriptionId\}/);
  assert.match(datasetPicker, /\[overflow-wrap:anywhere\]/);
  assert.match(datasetPicker, /flex flex-wrap gap-x-2/);
  assert.match(datasetPicker, /aria-pressed=\{active\}/);
  assert.match(datasetPicker, /focus-visible:ring-2/);
  assert.match(datasetPicker, /role="status"/);
  assert.match(datasetPicker, /role="alert"/);
  assert.match(datasetPicker, /No workspace datasets yet/);
  assert.match(datasetPicker, /Open Data Hub/);
  assert.doesNotMatch(datasetPicker, /block truncate text-\[0\.75rem\]/);
});

test("manuscript loading retries transient failures and offers a manual recovery action", () => {
  const query = page.slice(page.indexOf('queryKey: ["writer-doc", docId]'), page.indexOf("const { data: runs } = useRuns()"));
  assert.match(query, /retry: retryTransientApiQuery/);
  assert.match(query, /retryDelay: transientApiRetryDelay/);
  assert.match(page, /refetch: retryDocument/);
  assert.match(page, /disabled=\{docIsFetching\} onClick=\{\(\) => void retryDocument\(\)\}/);
});

test("a failed manuscript refresh does not remove the loaded editor or overwrite local text", () => {
  assert.match(page, /if \(loadError && !doc\)/);
  assert.doesNotMatch(page, /if \(loadError\) \{/);
  const errorView = page.slice(page.indexOf("if (loadError && !doc)"), page.indexOf("if (isLoading || !doc || content === null)"));
  assert.doesNotMatch(errorView, /setContent|removeQueries|clear\(/);
  assert.match(page, /\{Boolean\(loadError\) && \([\s\S]*?Your current text is still here; recent changes may not be saved yet\./);
  assert.match(page, /if \(doc && content === null\) \{\s*setContent\(doc\.content\)/);
});
