import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import ts from "typescript";

const helperSource = await readFile(
  new URL("../src/lib/writer-edit-review.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(helperSource, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
}).outputText;
const {
  activeWriterEditReviewSet,
  collectWriterEditReviews,
  locateWriterEditAnchor,
  nextWriterEditReview,
} = await import(
  `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`
);

const selectionHelperSource = await readFile(
  new URL("../src/lib/writer-selection.ts", import.meta.url),
  "utf8",
);
const selectionHelperCompiled = ts.transpileModule(selectionHelperSource, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
}).outputText;
const {
  WRITER_SELECTION_MAX_CHARACTERS,
  WRITER_SELECTION_MAX_SEGMENTS,
  buildWriterPdfSelection,
  buildWriterSourceSelection,
  normalizeWriterPdfText,
} = await import(
  `data:text/javascript;base64,${Buffer.from(selectionHelperCompiled).toString("base64")}`
);

const edit = (find, replace, applicable = true) => ({
  path: "main.tex",
  find,
  replace,
  applicable,
  occurrences: applicable ? 1 : 0,
});
const message = (id, edits, payload = {}) => ({
  id,
  role: "assistant",
  content: "Review these changes.",
  payload: { edits, ...payload },
});

test("durable decisions and exact apply receipts survive review reloads", () => {
  const messages = [
    message(10, [edit("old", "new"), edit("keep", "drop")], {
      edit_decisions: { 1: "rejected" },
      agent_events: [
        {
          id: 1,
          event: "change.completed",
          tool: "manuscript.apply_change",
          applied: true,
          input: { path: "main.tex" },
          before: "old",
          after: "new",
        },
      ],
    }),
  ];
  assert.deepEqual(
    collectWriterEditReviews(messages).map((review) => review.decision),
    ["applied", "rejected"],
  );
});

test("review collection preserves durable message and edit order", () => {
  const reviews = collectWriterEditReviews([
    message(20, [edit("first", "creates second"), edit("second", "done")]),
    message(21, [edit("later", "last")]),
  ]);
  assert.equal(nextWriterEditReview(reviews)?.id, "21:0");
  assert.deepEqual(reviews.map((review) => review.id), ["20:0", "20:1", "21:0"]);
});

test("a newer proposal set is reviewable without losing order inside that turn", () => {
  const reviews = collectWriterEditReviews([
    message(50, [edit("old pending", "old result")]),
    message(51, [edit("new first", "creates anchor"), edit("new anchor", "new second")]),
  ]);
  assert.deepEqual(
    activeWriterEditReviewSet(reviews).map((review) => review.id),
    ["51:0", "51:1"],
  );
  assert.equal(nextWriterEditReview(reviews)?.id, "51:0");
});

test("finishing the newest proposal never resurrects an abandoned older turn", () => {
  const reviews = collectWriterEditReviews([
    message(60, [edit("stale pending", "stale result")]),
    message(61, [edit("latest", "accepted")], {
      edit_decisions: { 0: "applied" },
    }),
  ]);
  assert.deepEqual(activeWriterEditReviewSet(reviews), []);
  assert.equal(nextWriterEditReview(reviews), null);
});

test("blocked edits never become the active review", () => {
  const reviews = collectWriterEditReviews([
    message(30, [edit("missing", "no", false), edit("present", "yes")]),
  ]);
  assert.equal(reviews[0].decision, "blocked");
  assert.equal(nextWriterEditReview(reviews)?.id, "30:1");
});

test("an earlier rejection closes dependent edits from the same proposal", () => {
  const reviews = collectWriterEditReviews([
    message(35, [edit("first", "one"), edit("second", "two")], {
      edit_decisions: { 0: "rejected", 1: "superseded" },
    }),
  ]);
  assert.deepEqual(
    reviews.map((review) => review.decision),
    ["rejected", "superseded"],
  );
  assert.equal(nextWriterEditReview(reviews), null);
});

test("unique anchors match backend semantics and ambiguous anchors stay blocked", () => {
  assert.deepEqual(locateWriterEditAnchor("before target after", "target"), {
    from: 7,
    to: 13,
  });
  assert.equal(locateWriterEditAnchor("target target", "target"), null);
  assert.equal(locateWriterEditAnchor("nothing", "target"), null);
});

test("legacy receipts use proposal_index to disambiguate identical proposals", () => {
  const duplicate = edit("same", "replacement");
  const reviews = collectWriterEditReviews([
    message(40, [duplicate, duplicate], {
      agent_events: [
        {
          id: 2,
          event: "change.completed",
          tool: "manuscript.apply_change",
          input: { path: "main.tex", proposal_index: 1 },
          before: "same",
          after: "replacement",
          applied: true,
        },
      ],
    }),
  ]);
  assert.deepEqual(reviews.map((review) => review.decision), ["pending", "applied"]);
});

test("source editor exposes unified red-green review and accessible decisions", async () => {
  const editor = await readFile(
    new URL("../src/components/writer/code-editor.tsx", import.meta.url),
    "utf8",
  );
  assert.match(editor, /cm-review-removed-line/);
  assert.match(editor, /Proposed replacement/);
  assert.match(editor, /Approve and apply this proposed manuscript edit|>\s*Approve\s*</);
  assert.match(editor, /Reject this proposed manuscript edit|>\s*Reject\s*</);
  assert.match(editor, /EditorView\.scrollIntoView/);
  assert.match(editor, /Show full.*line replacement/);
  assert.doesNotMatch(editor, /slice\(0,\s*600\)/);
  assert.match(editor, /writerSourceSelectionError\(quote\)/);
  const writerPage = await readFile(
    new URL("../src/app/(app)/writer/[id]/page.tsx", import.meta.url),
    "utf8",
  );
  assert.match(writerPage, /revisionRef\.current\.set\(activeFileIdRef\.current/);
  assert.match(writerPage, /baseContentRef\.current\.set\(activeFileIdRef\.current/);
  assert.match(writerPage, /readOnly=\{!canEdit \|\| Boolean\(reviewBusyId\)\}/);
  assert.match(writerPage, /Reject stale proposal/);
  assert.match(writerPage, /pendingReviewTargetMissing/);
  const pdfPreview = await readFile(
    new URL("../src/components/writer/pdf-preview.tsx", import.meta.url),
    "utf8",
  );
  assert.doesNotMatch(pdfPreview, /quote:\s*quote\.slice\(0,\s*600\)/);
  assert.match(pdfPreview, /page_end/);
  assert.match(pdfPreview, /Comments must be anchored within one PDF page/);
});

test("Writer PDF matching copy repairs layout hyphenation but keeps ordinary hyphens", () => {
  assert.equal(
    normalizeWriterPdfText(
      "Das wirkt unser- \n iös. Reini- \r\n gungstipps bleiben evidence-based und state-of-the-art.",
    ),
    "Das wirkt unseriös. Reinigungstipps bleiben evidence-based und state-of-the-art.",
  );
});

test("Writer selections preserve every bounded character beyond the legacy 600 limit", () => {
  const longSource = `\\section{Results}\n${"complete source sentence. ".repeat(55)}`;
  const source = buildWriterSourceSelection({
    quote: longSource,
    line: 4,
    line_end: 5,
    path: "main.tex",
  });
  assert.equal(source.error, null);
  assert.equal(source.selection.quote, longSource);
  assert.ok(source.selection.quote.length > 600);

  const longPdf = "Rendered passage remains complete. ".repeat(40);
  const pdf = buildWriterPdfSelection([{ quote: longPdf, page: 2 }]);
  assert.equal(pdf.error, null);
  assert.equal(pdf.selection.quote, longPdf.trim());
  assert.ok(pdf.selection.quote.length > 600);
});

test("Writer PDF selection records disjoint ranges and an honest page span", () => {
  const result = buildWriterPdfSelection([
    { quote: "First selected passage on the opening page.", page: 2 },
    { quote: "Second selected passage crossing the next pages.", page: 4, page_end: 5 },
  ]);
  assert.equal(result.error, null);
  assert.equal(result.selection.page, 2);
  assert.equal(result.selection.page_end, 5);
  assert.equal(result.selection.segments.length, 2);
  assert.equal(
    result.selection.quote,
    "First selected passage on the opening page.\n\nSecond selected passage crossing the next pages.",
  );
});

test("Writer selection rejects an over-bound passage instead of truncating it", () => {
  const quote = "x".repeat(WRITER_SELECTION_MAX_CHARACTERS + 1);
  const result = buildWriterPdfSelection([{ quote, page: 1 }]);
  assert.equal(result.selection, null);
  assert.match(result.error, /at most 12,000 characters/);

  const tooManyRanges = buildWriterPdfSelection(
    Array.from({ length: WRITER_SELECTION_MAX_SEGMENTS + 1 }, (_, index) => ({
      quote: `Selected passage number ${index}.`,
      page: 1,
    })),
  );
  assert.equal(tooManyRanges.selection, null);
  assert.match(tooManyRanges.error, /at most 32 separate ranges/);

  const tooManyPages = buildWriterPdfSelection([
    { quote: "A complete but over-wide page selection.", page: 1, page_end: 33 },
  ]);
  assert.equal(tooManyPages.selection, null);
  assert.match(tooManyPages.error, /at most 32 consecutive PDF pages/);
});
