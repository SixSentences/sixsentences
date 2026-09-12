import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const read = (path) => readFileSync(join(process.cwd(), path), "utf8");
const api = read("src/lib/api.ts");
const component = read("src/components/library/library-share-dialog.tsx");
const page = read("src/app/(app)/library/page.tsx");
const types = read("src/lib/types.ts");

function section(source, start, end = null) {
  const startIndex = source.indexOf(start);
  assert.notEqual(startIndex, -1, `missing section start: ${start}`);
  const endIndex = end === null ? source.length : source.indexOf(end, startIndex + start.length);
  if (end !== null) assert.notEqual(endIndex, -1, `missing section end: ${end}`);
  return source.slice(startIndex, endIndex);
}

test("Library sharing uses the dedicated account-bound API namespace", () => {
  assert.match(api, /libraryShares: \(\) => request<LibraryShare\[\]>\("\/library\/shares"\)/);
  assert.match(api, /createLibraryShare:[\s\S]*rights_confirmed: true;[\s\S]*request<LibraryShare>\("\/library\/shares", \{ method: "POST", body \}\)/);
  assert.match(api, /deleteLibraryShare:[\s\S]*`\/library\/shares\/\$\{encodeURIComponent\(shareId\)\}`[\s\S]*method: "DELETE"/);
  assert.match(api, /receivedLibraryShares:[\s\S]*"\/library\/shares\/received"/);
  assert.match(api, /receivedLibraryDocuments:[\s\S]*\/library\/shares\/received\/\$\{encodeURIComponent\(shareId\)\}\/documents\?/);
  assert.match(api, /receivedLibraryWebSources:[\s\S]*\/library\/shares\/received\/\$\{encodeURIComponent\(shareId\)\}\/web-sources\?/);
});

test("received PDF downloads stay scoped to the grant and sanitize filenames", () => {
  assert.match(api, /fetchReceivedLibraryDocumentBytes[\s\S]*fetchAuthenticatedDocumentBytes\([\s\S]*\/library\/shares\/received\/\$\{encodeURIComponent\(shareId\)\}\/documents\/\$\{documentId\}\/file/);
  assert.match(component, /await fetchReceivedLibraryDocumentBytes\(shareId, document\.id\)/);
  assert.match(component, /downloadPdfBytes\(bytes, paperPdfFilename\(document\.title \?\? document\.metadata\.title\)\)/);
  assert.doesNotMatch(
    section(component, "function SharedPdfDownloadButton(", "function AccessUnavailable("),
    /fetchDocumentBytes|PaperPanel/,
  );
});

test("only workspace owners see grant management and shared mode hides owned mutations", () => {
  assert.match(page, /me\?\.role === "owner" \? \(\s*<LibraryShareDialog/);
  assert.match(page, /libraryView !== "shared" \? <BrowserCaptureConnect \/>/);
  assert.match(page, /libraryView !== "shared" && uploadPdfs\.isPending/);
  assert.match(page, /libraryView === "shared" \? \(\s*<SharedWithMeView/);
  assert.match(page, /setSelectionMode\(false\);[\s\S]*setLibraryView\("shared"\)/);
  assert.match(page, /max-w-full w-fit overflow-x-auto rounded-full/);
  assert.equal((page.match(/className=\{`[^`]*shrink-0 rounded-full px-4/g) ?? []).length, 2);
  assert.match(page, /inline-flex shrink-0 items-center/);
});

test("received DTOs are viewer-safe and the received view exposes no owned actions", () => {
  const sharedDocument = section(types, "export interface SharedLibraryDocument {", "export interface SharedLibraryWebSource {");
  const sharedWebSource = section(types, "export interface SharedLibraryWebSource {", "export interface BrowserCaptureRevision {");
  for (const dto of [sharedDocument, sharedWebSource]) {
    assert.match(dto, /access_role: LibraryShareRole/);
    assert.match(dto, /share_id: string/);
    assert.match(dto, /library_owner:/);
    assert.doesNotMatch(dto, /browser_capture|metadata_provenance|provenance:|annotations|run:/);
  }
  assert.match(sharedWebSource, /url: string \| null/);
  assert.match(sharedWebSource, /canonical_url: string \| null/);

  const receivedView = section(component, "export function SharedWithMeView(");
  assert.match(receivedView, /data-shared-library-readonly/);
  assert.match(receivedView, /safeLibrarySourceUrl\(document\.url\)/);
  assert.match(receivedView, /safeLibrarySourceUrl\(source\.url\)/);
  assert.match(receivedView, /No editing, annotations, moving or AI actions/);
  assert.doesNotMatch(receivedView, /PaperPanel|PaperMetadataEditor|WebSourceMetadataEditor|DeletePaperButton|DeleteWebSourceButton|AskButton|FilePaperButton/);
  assert.doesNotMatch(receivedView, /updateLibrary|deleteLibrary|Enrichment|documentAnnotations|createRun/);
});

test("grant consent is explicit, non-persistent and reset when its context changes", () => {
  assert.match(component, /<form\s+method="post"/);
  assert.match(component, /I confirm that I have the right to share these Library records and any attached PDFs with this recipient, including records added to this scope later\./);
  assert.match(component, /rights_confirmed: true/);
  assert.match(component, /onChange=\{\(event\) => \{\s*setEmail\(event\.target\.value\);\s*setRightsConfirmed\(false\)/);
  assert.match(component, /setScope\(value as LibraryShareScope\);\s*setRightsConfirmed\(false\)/);
  assert.match(component, /if \(!nextOpen\) \{[\s\S]*setEmail\(""\);[\s\S]*setRightsConfirmed\(false\)/);
  assert.doesNotMatch(component, /localStorage|sessionStorage/);
});

test("revocation uses an app-owned, lifecycle-honest confirmation", () => {
  assert.match(component, /<AlertDialog[\s\S]*<AlertDialogTitle>[\s\S]*Revoke Library access\?/);
  assert.match(component, /PDF copies already downloaded cannot be recalled/);
  assert.match(component, /revokeTarget && !revoke\.isPending/);
  assert.match(component, /error instanceof ApiError && error\.status === 404/);
  assert.doesNotMatch(component, /window\.confirm|globalThis\.confirm|\bconfirm\(/);
});

test("share success copy describes in-product visibility rather than an invitation", () => {
  assert.match(component, /The recipient will see it under Shared with me\./);
  assert.doesNotMatch(component, /invitation (?:sent|emailed)|email (?:sent|delivered)/i);
  assert.doesNotMatch(component, /error instanceof Error \? error\.message/);
});

test("received lists refresh safely and distinguish transport errors from revoked access", () => {
  assert.equal((component.match(/refetchOnWindowFocus: "always"/g) ?? []).length, 3);
  assert.match(component, /value=\{query\}\s+maxLength=\{200\}/);
  assert.match(component, /function SharedListLoadError/);
  assert.match(component, /Shared papers could not be loaded\./);
  assert.match(component, /Shared web sources could not be loaded\./);
  assert.match(component, /accessIsStale \? \(\s*<AccessUnavailable/);
  assert.match(component, /documents\.isError \? \(\s*<SharedListLoadError sourceType="papers"/);
  assert.match(component, /webSources\.isError \? \(\s*<SharedListLoadError sourceType="web"/);
});
