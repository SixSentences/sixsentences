import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const composer = readFileSync("src/components/search/composer.tsx", "utf8");
const apiClient = readFileSync("src/lib/api.ts", "utf8");
const types = readFileSync("src/lib/types.ts", "utf8");

test("PubMed discovery follows the model catalog capability and stays opt-in", () => {
  assert.match(
    types,
    /interface ChatModelCatalog[\s\S]*?runtime_capabilities\?:\s*\{[\s\S]*?pubmed: boolean;/,
  );
  assert.match(composer, /const \{ data: modelCatalog, isPending: modelCatalogPending \} = useModels\(\);/);
  assert.match(
    composer,
    /modelCatalog\?\.runtime_capabilities\?\.pubmed === true/,
  );
  assert.match(composer, /pubmed: false/);
  assert.match(composer, /const effectivePubmed = pubmedAvailable && options\.pubmed;/);
  assert.match(
    composer,
    /if \(!modelCatalogPending && !pubmedAvailable\)[\s\S]*?current\.pubmed \? \{ \.\.\.current, pubmed: false \} : current/,
  );
  assert.match(
    composer,
    /\.\.\.\(pubmedAvailable && \{ pubmed: effectivePubmed \}\)/,
  );
  assert.match(
    composer,
    /const askMode =[\s\S]*?!options\.pubmed[\s\S]*?!options\.acquire/,
  );
  assert.match(
    composer,
    /if \(options\.pubmed && !pubmedAvailable\)[\s\S]*?PubMed availability is still being checked/,
  );
  assert.match(
    composer,
    /\{pubmedAvailable && \(\s*<WorkflowToggle\s*label="PubMed"/,
  );
  assert.match(
    composer,
    /Sendet die erzeugte wissenschaftliche Suchanfrage an die externe öffentliche Datenbank PubMed\./,
  );
  assert.match(
    composer,
    /Sends the generated scholarly query to the external public PubMed database\./,
  );
  assert.match(
    apiClient,
    /createRun:[\s\S]*?body: \{ \.\.\.body, pubmed: body\.pubmed === true \}/,
  );
  assert.match(types, /interface RunCreateRequest[\s\S]*?pubmed\?: boolean;/);
});
