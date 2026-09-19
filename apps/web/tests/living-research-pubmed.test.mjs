import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const panel = readFileSync(
  new URL("../src/components/run/living-research-panel.tsx", import.meta.url),
  "utf8",
);

test("living research exposes PubMed only when the deployment can run it", () => {
  assert.match(panel, /pubmed:\s*"PubMed"/);
  assert.match(panel, /const \{ data: modelCatalog, isPending: modelCatalogPending \} = useModels\(\);/);
  assert.match(
    panel,
    /modelCatalog\?\.runtime_capabilities\?\.pubmed === true/,
  );
  assert.match(
    panel,
    /source !== "pubmed" \|\| pubmedAvailable \|\| pubmedSelected/,
  );
  assert.match(panel, /PubMed is not available on this deployment/);
  assert.doesNotMatch(panel, /web:\s*"Web sources"/);
  assert.doesNotMatch(panel, /imports:\s*"Reference imports"/);
  assert.match(panel, /watch_sources:\s*settings\.watch_sources/);
  assert.match(
    panel,
    /settings\.enabled &&[\s\S]*?settings\.watch_sources\.includes\("pubmed"\)/,
  );
});
