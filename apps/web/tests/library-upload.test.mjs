import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const source = readFileSync(
  join(process.cwd(), "src/app/(app)/library/page.tsx"),
  "utf8",
);
const metadataEditor = readFileSync(
  join(process.cwd(), "src/components/library/metadata-editor.tsx"),
  "utf8",
);
const libraryDetail = readFileSync(
  join(process.cwd(), "src/components/library/library-entry-detail.tsx"),
  "utf8",
);
const paperEnrichment = readFileSync(
  join(process.cwd(), "src/components/library/paper-enrichment.tsx"),
  "utf8",
);
const paperPanel = readFileSync(
  join(process.cwd(), "src/components/run/paper-panel.tsx"),
  "utf8",
);
const libraryTypes = readFileSync(
  join(process.cwd(), "src/lib/types.ts"),
  "utf8",
);

test("the Library upload is reachable without drag and drop", () => {
  assert.match(source, /type="file"/);
  assert.match(source, /aria-label="Add papers to the library"/);
  assert.match(source, /multiple/);
  assert.match(source, /\n\s+Add papers\n/);
  assert.match(source, /uploadInputRef\.current\?\.click\(\)/);
});

test("the Library describes optional connected import tools without a dead install CTA", () => {
  assert.match(source, /Connected import tools save sources only after an explicit action/);
  assert.match(source, /Add one through a compatible connected source/);
  assert.doesNotMatch(source, /click Browser Capture|Saved in one click/);
});

test("the file picker and drop target share the validated upload path", () => {
  assert.match(source, /queueUploads\(Array\.from\(event\.target\.files \?\? \[\]\)\)/);
  assert.match(source, /queueUploads\(Array\.from\(event\.dataTransfer\.files\)\)/);
  assert.match(source, /\(pdf\|png\|jpe\?g\|webp\|gif\)/);
});

test("the bulk delete confirmation is opaque, bounded and responsive", () => {
  assert.match(
    source,
    /!w-\[calc\(100vw-2rem\)\].*!max-w-lg.*overflow-hidden.*bg-popover/,
  );
  assert.match(source, /sm:grid-cols-2/);
  assert.match(source, /<AlertDialogCancel className="w-full">/);
  assert.match(source, /variant="destructive"\s+className="w-full"/);
});

test("the Library exposes composable type, origin and project filters", () => {
  assert.match(source, /const activeFilterCount =/);
  assert.match(source, /Filters, \$\{activeFilterCount\} active/);
  assert.match(source, /<PopoverTrigger asChild>/);
  assert.match(source, /aria-label="Filter library by file type"/);
  assert.match(source, /aria-label="Filter library by origin"/);
  assert.match(source, /aria-label="Filter library by location"/);
  assert.match(source, /location === "unfiled"/);
  assert.match(source, /libraryDocumentType\(doc\) === typeFilter/);
  assert.match(source, /originFilter === "search" \? doc\.run !== null : doc\.run === null/);
  assert.match(source, /Clear filters/);
  assert.doesNotMatch(source, /> Refine\s*</);
});

test("the Library can sort visible papers without changing the source collection", () => {
  assert.match(source, /aria-label="Sort library papers"/);
  assert.match(source, /value="added-desc">Recently added/);
  assert.match(source, /value="title-asc">Title A to Z/);
  assert.match(source, /value="year-desc">\s*Newest publication/);
  assert.match(source, /value="size-desc">Largest files/);
  assert.match(source, /return visible\.toSorted/);
});

test("selection mode replaces the lean toolbar with bulk actions", () => {
  assert.match(source, /const \[selectionMode, setSelectionMode\] = useState\(false\)/);
  assert.match(source, /aria-pressed=\{selectionMode\}/);
  assert.match(source, /onClick=\{\(\) => \{\s*closeDetail\(\);\s*setSelectionMode\(true\);\s*\}\}/);
  assert.match(source, /aria-label="Paper selection actions"/);
  assert.match(source, /\{libraryView === "papers" && selectionMode \? \(\s*<div/);
  assert.match(source, /Choose papers for a bulk action\./);
  assert.match(source, /\{!selectionMode \? \(/);
  assert.match(source, /aria-label="Paper actions"/);
  assert.match(source, /setSelectionMode\(false\)/);
  assert.doesNotMatch(source, /selectionMode && filtered\.length > 0/);
});

test("paper and web rows expose keyboard-accessible details without hijacking selection", () => {
  assert.match(source, /parseLibraryDetailTarget/);
  assert.match(source, /params\.set\("entry", `\$\{target\.kind\}:\$\{target\.id\}`\)/);
  assert.match(source, /View all details for"\} \$\{source\.title\}/);
  assert.match(source, /if \(selectionMode\) toggleDocument\(doc\.id\)/);
  assert.match(source, /else openDetail\(\{ kind: "paper", id: doc\.id \}\)/);
  assert.match(source, /aria-pressed=\{selectionMode \? selected : undefined\}/);
  assert.match(source, /aria-current=\{!selectionMode && detailActive \? "true" : undefined\}/);
  assert.match(source, /onClick=\{\(event\) => event\.stopPropagation\(\)\}/);
  assert.match(source, /Alle Details anzeigen für/);
});

test("deep links resolve one exact entry into a responsive split detail", () => {
  assert.match(source, /<LibraryEntryDetail/);
  assert.match(source, /api\.libraryDocument/);
  assert.match(source, /api\.libraryWebSource/);
  assert.match(source, /queryKey: \["library", "document"/);
  assert.match(source, /queryKey: \["library-web-sources", "entry"/);
  assert.doesNotMatch(source, /detailTarget[\s\S]{0,180}void fetchMoreDocuments\(\)/);
  assert.doesNotMatch(source, /detailTarget[\s\S]{0,180}void fetchMoreWebSources\(\)/);
  assert.match(libraryDetail, /data-library-detail-panel/);
  assert.match(libraryDetail, /xl:static/);
  assert.match(libraryDetail, /role=\{mobileModal \? "dialog" : "complementary"\}/);
  assert.match(libraryDetail, /aria-modal=\{mobileModal \|\| undefined\}/);
  assert.doesNotMatch(libraryDetail, /<Dialog/);
  assert.match(libraryDetail, /Original file/);
  assert.match(libraryDetail, /Text extraction/);
  assert.match(libraryDetail, /Selected passage/);
  assert.match(libraryDetail, /Source URL/);
  assert.match(libraryDetail, /PDF URL/);
  assert.match(libraryDetail, /Captured at/);
  assert.match(libraryDetail, /provenanceLabel/);
  assert.match(libraryDetail, /Reviewed by you/);
  assert.match(libraryDetail, /Edit metadata/);
  assert.match(libraryDetail, /overflow-y-auto overscroll-contain/);
  assert.match(libraryDetail, /break-all/);
  assert.doesNotMatch(libraryDetail, /JSON\.stringify/);
  assert.doesNotMatch(libraryDetail, /capture\.bibliographic|provenance\.bibliographic/);

  const metadataInterface = libraryTypes.match(
    /export interface LibraryPaperMetadata \{([\s\S]*?)\n\}/,
  )?.[1] ?? "";
  const metadataFields = [...metadataInterface.matchAll(/^  ([a-z_]+):/gm)].map((match) => match[1]);
  const renderedFields = new Set(
    [...libraryDetail.matchAll(/\{ key: "([a-z_]+)"/g)].map((match) => match[1]),
  );
  assert.deepEqual(
    metadataFields.filter((field) => !renderedFields.has(field)),
    [],
    "every LibraryPaperMetadata field must appear in the detail view",
  );
});

test("detail, editor and reader remain one coherent secondary surface", () => {
  assert.match(source, /setMetadataEditorTarget\(detailTarget\)/);
  assert.match(source, /open=\{!metadataEditorTarget\}/);
  assert.match(source, /\{detailTarget && !activePaper \? \(/);
  assert.match(source, /\{activePaper && \(/);
  assert.match(source, /onClose=\{\(\) => setPaper\(null\)\}/);
  assert.match(source, /openReader\(detailDocument\)/);
  assert.match(source, /<PaperMetadataEditor[\s\S]*open[\s\S]*hideTrigger/);
  assert.match(source, /<WebSourceMetadataEditor[\s\S]*open[\s\S]*hideTrigger/);
  assert.match(metadataEditor, /controlledOpen \?\? internalOpen/);
  assert.match(metadataEditor, /hideTrigger/);
  assert.match(source, /resolvedMetadataEditorTargetRef\.current === targetKey/);
  assert.match(source, /setMetadataEditorTarget\(null\)/);
});

test("mobile detail and PDF reader trap focus while desktop stays nonmodal", () => {
  assert.match(libraryDetail, /matchMedia\("\(max-width: 1279px\)"\)/);
  assert.match(libraryDetail, /masterPanel\.inert = mobile/);
  assert.match(libraryDetail, /event\.key !== "Tab"/);
  assert.match(libraryDetail, /event\.key === "Escape"/);
  assert.match(libraryDetail, /closest\('\[role="alertdialog"\]'\)/);
  assert.match(libraryDetail, /restoreFocusRef/);
  assert.match(libraryDetail, /scrollRef\.current\?\.scrollTo\(\{ top: 0 \}\)/);
  assert.match(source, /data-library-master-panel/);
  assert.match(source, /data-library-reader-panel/);
  assert.match(source, /role=\{compactSecondarySurface \? "dialog" : "complementary"\}/);
  assert.match(source, /aria-modal=\{compactSecondarySurface \|\| undefined\}/);
  assert.match(source, /masterPanel\.inert = true/);
  assert.match(source, /eventTarget\?\.closest\('\[role="alertdialog"\], \[data-radix-popper-content-wrapper\]'\)/);
  assert.equal((source.match(/<Popover modal open=\{open\}/g) ?? []).length, 2);
  assert.match(source, /const hiddenRegionObserver = new MutationObserver\(syncHiddenReaderRegions\)/);
  assert.match(source, /region\.inert = true/);
  assert.match(source, /!element\.closest\('\[inert\], \[aria-hidden="true"\]'\)/);
  assert.match(paperPanel, /inert=\{!controlsExpanded \? true : undefined\}/);
});

test("missing metadata is compact and manual empty tombstones stay protected", () => {
  assert.match(libraryDetail, /const visibleGroups =/);
  assert.match(libraryDetail, /const missingGroups =/);
  assert.match(libraryDetail, /const protectedEmptyGroups =/);
  assert.match(libraryDetail, /sourceType === "user" \|\| sourceType === "user_fill"/);
  assert.match(libraryDetail, /fields intentionally left blank/);
  assert.match(libraryDetail, /will not be filled automatically by metadata enrichment/);
  assert.doesNotMatch(libraryDetail, /Not available/);
  assert.doesNotMatch(libraryDetail, /Nicht vorhanden/);
});

test("papers and web sources expose the same branded metadata editor", () => {
  assert.match(source, /<PaperMetadataEditor document=\{doc\} \/>/);
  assert.match(source, /<WebSourceMetadataEditor source=\{source\} \/>/);
  assert.match(source, /doc\.metadata\.container_title/);
  assert.match(source, /source\.metadata\.container_title/);
  assert.match(metadataEditor, /SixSentences Library/);
  assert.match(metadataEditor, /Bibliographic details/);
  assert.match(metadataEditor, /View and edit metadata/);
});

test("metadata saves only changed fields against an optimistic revision", () => {
  assert.match(metadataEditor, /metadataChanges\(metadata, draft\)/);
  assert.match(metadataEditor, /expected_revision: revision/);
  assert.match(metadataEditor, /mode: "edit"/);
  assert.match(metadataEditor, /Object\.keys\(changes\)\.length === 0/);
  assert.match(metadataEditor, /error instanceof ApiError && error\.status === 409/);
  assert.match(metadataEditor, /detail\?\.code === "library_metadata_revision_conflict"/);
  assert.match(metadataEditor, /detail\?\.code === "library_metadata_identity_conflict"/);
  assert.match(metadataEditor, /already belongs to another Library entry/);
  assert.match(metadataEditor, /invalidateQueries/);
});

test("the editor covers practical Zotero-style details and explicit links", () => {
  for (const label of [
    "Authors · one per line",
    "Abstract or description",
    "Publication / venue",
    "Publisher",
    "Volume",
    "Issue",
    "Pages / article number",
    "DOI",
    "arXiv ID",
    "ISBN",
    "ISSN",
    "Keywords · one per line",
    "Source URL",
    "Canonical URL",
    "PDF URL",
    "Subtitle",
    "Short title",
    "Series title",
    "Series number",
    "Edition",
    "Publisher place",
    "PMID",
    "PMCID",
    "Citation key",
    "Call number",
    "Accessed at",
    "Archive location",
    "Extra bibliographic details",
  ]) {
    assert.match(metadataEditor, new RegExp(label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }
  assert.match(metadataEditor, /Selected passage/);
  assert.match(metadataEditor, /put\(\s*"selected_excerpt"/);
  assert.match(metadataEditor, /maxLength=\{4000\}/);
});

test("the metadata editor follows account language and exposes provenance", () => {
  assert.match(metadataEditor, /const \{ me \} = useAuth\(\)/);
  assert.match(metadataEditor, /me\?\.language === "de"/);
  assert.match(metadataEditor, /Bibliografische Angaben/);
  assert.match(metadataEditor, /Bibliographic details/);
  assert.match(metadataEditor, /Von dir geprüft/);
  assert.match(metadataEditor, /Reviewed by you/);
  assert.match(metadataEditor, /Automatisch erkannt/);
  assert.match(metadataEditor, /Detected automatically/);
});

test("paper enrichment is explicit, exact-identity bound, and review-first", () => {
  assert.match(libraryDetail, /<PaperEnrichment[\s\S]{0,180}document=\{document\}[\s\S]{0,180}actionHost=\{enrichmentActionHost\}/);
  assert.match(libraryDetail, /data-paper-enrichment-action/);
  assert.match(paperEnrichment, /createPortal\(primaryAction, actionHost\)/);
  assert.match(paperEnrichment, /Fehlende Metadaten suchen/);
  assert.match(paperEnrichment, /Find missing metadata/);
  assert.match(paperEnrichment, /FileSearch/);
  assert.match(paperEnrichment, /SearchCheck/);
  assert.doesNotMatch(paperEnrichment, /Sparkles|Wand|Complete paper metadata|Paper vervollständigen|Fehlende Infos suchen|Find missing information/);
  assert.match(paperEnrichment, /Titel allein werden nie zum Zuordnen verwendet/);
  assert.match(paperEnrichment, /Vorschläge übernehmen/);
  assert.match(paperEnrichment, /Crossref sowie OpenAlex mit Unpaywall-OA-Hinweisen/);
  assert.match(paperEnrichment, /Paywalls werden nicht umgangen/);
  assert.doesNotMatch(paperEnrichment, /apply\.mutate\(\).*onSuccess/);
});

test("paper enrichment reuses one durable start intent after an ambiguous response", () => {
  assert.match(paperEnrichment, /const startRequestId = useRef<string \| null>\(null\)/);
  assert.match(paperEnrichment, /mutationFn: \(requestId: string\)/);
  assert.match(paperEnrichment, /startRequestId\.current \?\?= `enrich:\$\{crypto\.randomUUID\(\)\}`/);
  assert.match(paperEnrichment, /start\.mutate\(startRequestId\.current\)/);
  assert.match(paperEnrichment, /onSuccess:[\s\S]*startRequestId\.current = null/);
  assert.doesNotMatch(paperEnrichment, /mutationFn:[^\n]*randomUUID/);
});

test("paper enrichment never turns a failed latest lookup into a fresh-start state", () => {
  assert.match(paperEnrichment, /latest\.isError/);
  assert.match(paperEnrichment, /latest\.refetch\(\)/);
  assert.match(paperEnrichment, /!latest\.isError && !latest\.isLoading/);
  assert.match(paperEnrichment, /job\.retry_count < 3/);
  assert.match(paperEnrichment, /Neue Prüfung starten/);
  assert.match(paperEnrichment, /onClick=\{startCheck\}/);
});

test("paper enrichment localizes stable failures and filters external links", () => {
  assert.match(paperEnrichment, /function requestErrorMessage/);
  assert.match(paperEnrichment, /paper_enrichment_strong_identity_required/);
  assert.match(paperEnrichment, /paper_enrichment_rate_limited/);
  assert.match(paperEnrichment, /paper_enrichment_retry_limit/);
  assert.match(paperEnrichment, /function jobFailureMessage/);
  assert.match(paperEnrichment, /paper_enrichment_identity_mismatch/);
  assert.match(paperEnrichment, /paper_enrichment_worker_failed/);
  assert.doesNotMatch(paperEnrichment, /job\.error\?\.message/);
  assert.doesNotMatch(paperEnrichment, /return error\.message/);
  assert.match(paperEnrichment, /function safeExternalUrl/);
  assert.match(paperEnrichment, /\["http:", "https:"\]\.includes\(parsed\.protocol\)/);
  assert.match(paperEnrichment, /const sourceUrl = safeExternalUrl\(suggestion\.source\.url\)/);
  assert.match(paperEnrichment, /href=\{sourceUrl\}/);
});

test("applying enriched fields refreshes Library without legacy Knowledge aggregation", () => {
  assert.match(paperEnrichment, /queryKey: \["library"\]/);
  assert.doesNotMatch(
    paperEnrichment,
    /knowledge-recent-papers|knowledge-paper-search|knowledge-recent-web-sources|knowledge-web-search/,
  );
});
