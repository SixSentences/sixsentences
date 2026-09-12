import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import ts from "typescript";

const read = (path) => readFileSync(join(process.cwd(), path), "utf8");
const page = read("src/app/(app)/library/page.tsx");
const button = read("src/components/library/paper-pdf-download-button.tsx");
const reader = read("src/components/run/paper-panel.tsx");
const api = read("src/lib/api.ts");
const download = read("src/lib/paper-download.ts");
const documentTypes = read("src/lib/library-document.ts");

function loadDocumentTypeHelpers() {
  const compiled = ts.transpileModule(documentTypes, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText;
  const module = { exports: {} };
  Function("module", "exports", compiled)(module, module.exports);
  return module.exports;
}

test("stored Library PDFs download through the authenticated byte endpoint", () => {
  assert.match(api, /async function fetchAuthenticatedDocumentBytes[\s\S]*Authorization:[\s\S]*cache: "no-store"/);
  assert.match(api, /fetchDocumentBytes[\s\S]*fetchAuthenticatedDocumentBytes\(`\/documents\/\$\{documentId\}\/file`\)/);
  assert.match(api, /fetchTranslatedDocumentBytes[\s\S]*fetchAuthenticatedDocumentBytes/);
  assert.doesNotMatch(page, /href=\{[^}]*pdf_url/);
  assert.match(button, /await fetchDocumentBytes\(documentId\)/);
  assert.match(button, /downloadPdfBytes\(bytes, paperPdfFilename\(title\)\)/);
});

test("paper rows and the detail surface expose the same localized download action", () => {
  assert.equal((page.match(/<PaperPdfDownloadButton/g) ?? []).length, 2);
  assert.match(page, /const isPdf = isStoredLibraryPdf\(doc\)/);
  assert.match(page, /isStoredLibraryPdf\(detailDocument\)/);
  assert.match(button, /PDF herunterladen/);
  assert.match(button, /Download PDF/);
  assert.match(button, /aria-busy=\{download\.isPending\}/);
  assert.match(button, /Die PDF konnte nicht heruntergeladen werden/);
  assert.match(button, /The PDF could not be downloaded/);
  assert.match(page, /flex w-full shrink-0 flex-wrap items-center justify-end/);
});

test("only strict stored PDF media types can reach row, detail, reader or handoff actions", () => {
  const { isPdfMimeType, isStoredLibraryPdf } = loadDocumentTypeHelpers();
  for (const value of [
    "application/pdf",
    "APPLICATION/PDF; charset=binary",
    " application/x-pdf ; version=1.7",
  ]) {
    assert.equal(isPdfMimeType(value), true, value);
  }
  for (const value of [
    null,
    "",
    "application/pdf+xml",
    "application/xml; profile=pdf",
    "text/plain?format=application/pdf",
    "application/octet-stream",
  ]) {
    assert.equal(isPdfMimeType(value), false, String(value));
  }
  assert.equal(isStoredLibraryPdf({ has_file: true, content_type: "APPLICATION/PDF; charset=binary" }), true);
  assert.equal(isStoredLibraryPdf({ has_file: false, content_type: "application/pdf" }), false);
  assert.equal(isStoredLibraryPdf({ has_file: true, content_type: "application/pdf+xml" }), false);

  assert.match(page, /libraryDocumentType[\s\S]*isPdfMimeType\(document\.content_type\)/);
  assert.match(page, /const isPdf = isStoredLibraryPdf\(doc\)/);
  assert.match(page, /if \(!isStoredLibraryPdf\(document\)\) return;[\s\S]*setMetadataEditorTarget/);
  const handoff = page.match(/const requestedParam = params\.get\("document"\)[\s\S]*?openReader\(document\);/)?.[0] ?? "";
  assert.match(handoff, /if \(!isStoredLibraryPdf\(document\)\) return;\s*openedDocumentRef\.current = requested;\s*openReader\(document\)/);
});

test("the reader reuses its loaded original bytes without a second fetch", () => {
  const originalButton = reader.match(/aria-label=\{`\$\{isGerman \? "Original-PDF herunterladen"[\s\S]*?<\/Tooltip>/)?.[0] ?? "";
  assert.match(originalButton, /if \(!bytes\) return;[\s\S]*downloadPdfBytes\(bytes, paperPdfFilename\(title\)\)/);
  assert.doesNotMatch(originalButton, /fetchDocumentBytes/);
  assert.match(originalButton, /disabled=\{!bytes \|\| isLoading \|\| isError\}/);
  assert.match(originalButton, /Original-PDF/);
  assert.match(originalButton, /Original PDF/);
});

test("download filenames are bounded and temporary object URLs are always released", () => {
  assert.match(download, /normalize\("NFKC"\)/);
  assert.match(download, /replace\(\/\[\^\\p\{L\}\\p\{N\}\]\+\/gu, "-"\)/);
  assert.match(download, /MAX_FILENAME_STEM_CODEPOINTS = 96/);
  assert.match(download, /MAX_QUALIFIER_CODEPOINTS = 24/);
  assert.match(download, /anchor\.download = filename/);
  assert.match(download, /try \{[\s\S]*anchor\.click\(\)[\s\S]*\} finally \{[\s\S]*anchor\.remove\(\)[\s\S]*URL\.revokeObjectURL\(url\)/);
});
