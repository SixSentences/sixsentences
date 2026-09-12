import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const read = (path) => readFileSync(join(process.cwd(), path), "utf8");
const page = read("src/app/(app)/library/page.tsx");
const detail = read("src/components/library/library-entry-detail.tsx");
const tools = read("src/components/library/citation-tools.tsx");
const api = read("src/lib/api.ts");
const types = read("src/lib/types.ts");

test("Library citations use the exact bounded paper-only API contract", () => {
  assert.match(types, /export type LibraryCitationFormat =\s*\| "apa"\s*\| "mla"\s*\| "chicago"\s*\| "harvard"\s*\| "bibtex"\s*\| "ris"/);
  assert.match(types, /export interface LibraryDocumentCitations[\s\S]*document_id: number[\s\S]*metadata_revision: string[\s\S]*missing_fields: string\[\][\s\S]*citations: Record<LibraryCitationFormat, LibraryCitationValue>/);
  assert.match(api, /libraryDocumentCitations: \(id: number\)[\s\S]*`\/documents\/\$\{id\}\/citations`/);
  assert.match(api, /downloadLibraryCitations\([\s\S]*uniqueIds\.length > 100/);
  assert.match(api, /`\$\{API_URL\}\/documents\/citations\/export`/);
  assert.match(api, /JSON\.stringify\(\{ document_ids: uniqueIds, format \}\)/);
  assert.match(api, /filename="\(\[\^"\\\\\/\\r\\n\]\+\)"/);
  assert.match(api, /triggerDownload\(await readBlobResponse\(res\), filename\)/);
});

test("selected papers can download six formats without silently truncating the selection", () => {
  for (const format of ["apa", "mla", "chicago", "harvard", "bibtex", "ris"]) {
    assert.match(tools, new RegExp(`value: "${format}"`));
  }
  assert.match(tools, /de: "Chicago \(Autor–Jahr\)", en: "Chicago Author-Date"/);
  assert.match(tools, /de: "Harvard \(Autor–Jahr\)", en: "Harvard Author-Date"/);
  assert.match(page, /libraryView === "papers" && selectionMode/);
  assert.match(page, /<LibraryCitationExportMenu[\s\S]*documentIds=\{Array\.from\(selectedIds\)\}/);
  assert.match(tools, /const tooMany = documentIds\.length > 100/);
  assert.match(tools, /Select no more than 100 papers for one export\. Your selection is preserved\./);
  assert.doesNotMatch(tools, /documentIds\.slice\(0, 100\)/);
  assert.doesNotMatch(page.match(/function WebSourceCard[\s\S]*?\n\}/)?.[0] ?? "", /LibraryCitationExportMenu/);
});

test("paper detail renders server citations, honest missing fields and localized copy failures", () => {
  assert.match(detail, /document \? \(\s*<PaperCitationSection/);
  assert.match(detail, /metadataRevision=\{document\.metadata_revision\}/);
  assert.match(tools, /queryKey: \["library-document-citations", userId, documentId, metadataRevision\]/);
  assert.match(tools, /api\.libraryDocumentCitations\(documentId\)/);
  assert.match(tools, /navigator\.clipboard\?\.writeText/);
  assert.match(tools, /await navigator\.clipboard\.writeText\(citation\)/);
  assert.match(tools, /Die Zitation konnte nicht kopiert werden/);
  assert.match(tools, /The citation could not be copied/);
  assert.match(tools, /citations\.data\?\.missing_fields/);
  assert.match(tools, /Rendered with the available metadata/);
  assert.match(tools, /data-paper-citation-section/);
  assert.match(tools, /aria-live="polite"/);
});

test("Browser Capture revision polling invalidates Library only after a newer revision", () => {
  assert.match(types, /export interface BrowserCaptureRevision \{\s*revision: number/);
  assert.match(api, /browserCaptureRevision: \(\)[\s\S]*"\/browser-capture\/revision"/);
  assert.match(page, /queryKey: \["browser-capture-revision", userId\]/);
  assert.match(page, /refetchInterval: 5_000/);
  assert.match(page, /refetchIntervalInBackground: false/);
  assert.match(page, /refetchOnWindowFocus: "always"/);
  assert.match(page, /captureRevisionBaselineRef\.current = \{ userId, revision \}/);
  assert.match(page, /!previous \|\| previous\.userId !== userId \|\| revision <= previous\.revision/);
  assert.match(page, /invalidateQueries\(\{ queryKey: \["library"\] \}\)/);
  assert.match(page, /invalidateQueries\(\{ queryKey: \["library-web-sources"\] \}\)/);
});
