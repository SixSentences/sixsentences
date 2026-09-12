import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import ts from "typescript";

const helperSource = await readFile(
  new URL("../src/lib/library-source-identity.ts", import.meta.url),
  "utf8",
);
const compiledHelper = ts.transpileModule(helperSource, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const identity = await import(
  `data:text/javascript;base64,${Buffer.from(compiledHelper).toString("base64")}`
);
const [mark, detail, page] = await Promise.all([
  readFile(new URL("../src/components/library/source-identity-mark.tsx", import.meta.url), "utf8"),
  readFile(new URL("../src/components/library/library-entry-detail.tsx", import.meta.url), "utf8"),
  readFile(new URL("../src/app/(app)/library/page.tsx", import.meta.url), "utf8"),
]);

test("Library source destinations accept only bounded credential-free HTTP(S) URLs", () => {
  assert.equal(
    identity.safeLibrarySourceUrl("https://www.researchgate.net/publication/42?q=paper#abstract"),
    "https://www.researchgate.net/publication/42?q=paper#abstract",
  );
  assert.equal(identity.safeLibrarySourceUrl("http://arxiv.org/abs/1706.03762"), "http://arxiv.org/abs/1706.03762");
  for (const value of [
    "javascript:alert(1)",
    "data:text/html,unsafe",
    "//example.org/paper",
    "https://user:secret@example.org/paper",
    "https://example.org/a path",
    `https://example.org/${"a".repeat(2_100)}`,
  ]) {
    assert.equal(identity.safeLibrarySourceUrl(value), null, value);
  }
});

test("source identity is a neutral host monogram with a metadata-only fallback", () => {
  assert.deepEqual(
    identity.librarySourceIdentity({
      url: "https://www.researchgate.net/publication/42",
      siteName: "Page-controlled label",
      fallbackLabel: "Paper",
    }),
    {
      safeUrl: "https://www.researchgate.net/publication/42",
      hostname: "researchgate.net",
      label: "researchgate.net",
      monogram: "RE",
      specific: true,
    },
  );
  const metadataFallback = identity.librarySourceIdentity({
    url: "javascript:alert(1)",
    siteName: "Open Archive",
    fallbackLabel: "Web source",
  });
  assert.equal(metadataFallback.safeUrl, null);
  assert.equal(metadataFallback.hostname, null);
  assert.equal(metadataFallback.label, "Open Archive");
  assert.equal(metadataFallback.monogram, "OP");
  assert.equal(metadataFallback.specific, true);
  assert.equal(
    identity.librarySourceIdentity({ url: null, fallbackLabel: "Paper" }).specific,
    false,
  );
});

test("Library source marks are accessible and never load third-party logo assets", () => {
  assert.match(mark, /role="img"/);
  assert.match(mark, /aria-label=\{accessibleLabel\}/);
  assert.match(mark, /isGerman \? "Quelle" : "Source"/);
  assert.match(mark, /isGerman \? "Paperquelle" : "Paper source"/);
  assert.doesNotMatch(mark, /<img\b|next\/image|\bsrc=|\bfetch\s*\(|favicon|google\.com\/s2/i);
  assert.match(page, /<SourceIdentityMark[\s\S]*?kind="web"/);
  assert.match(page, /<SourceIdentityMark[\s\S]*?kind="paper"/);
  assert.match(detail, /<SourceIdentityMark[\s\S]*?kind=\{kind\}/);
});

test("Library detail keeps a clipped rounded desktop split and a full mobile dialog", () => {
  assert.match(page, /className="relative flex min-h-0 min-w-0 flex-1 overflow-hidden rounded-2xl"/);
  assert.match(detail, /fixed inset-x-0 bottom-0 top-12[^"]*xl:static[^"]*xl:rounded-r-2xl/);
  assert.match(detail, /role=\{mobileModal \? "dialog" : "complementary"\}/);
  assert.match(detail, /aria-modal=\{mobileModal \|\| undefined\}/);
  assert.match(detail, /window\.matchMedia\("\(max-width: 1279px\)"\)/);
  assert.match(page, /safeLibrarySourceUrl\(detailDocument\?\.url\)/);
  assert.match(page, /safeLibrarySourceUrl\(detailWebSource\?\.url\)/);
});
