import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const composer = await readFile(
  new URL("../src/components/search/composer.tsx", import.meta.url),
  "utf8",
);
const approval = await readFile(
  new URL("../src/components/search/public-web-search-approval.tsx", import.meta.url),
  "utf8",
);
const types = await readFile(
  new URL("../src/lib/types.ts", import.meta.url),
  "utf8",
);
const chat = await readFile(
  new URL("../src/components/run/chat-panel.tsx", import.meta.url),
  "utf8",
);
const runView = await readFile(
  new URL("../src/components/run/run-view.tsx", import.meta.url),
  "utf8",
);
const protocolCard = await readFile(
  new URL("../src/components/run/protocol-card.tsx", import.meta.url),
  "utf8",
);
const api = await readFile(
  new URL("../src/lib/api.ts", import.meta.url),
  "utf8",
);
const consent = await readFile(
  new URL("../src/lib/web-search-consent.ts", import.meta.url),
  "utf8",
);

test("web-search confirmation is explicit, public-data-only and bilingual", () => {
  assert.match(
    composer,
    /\{effectiveWebSearch && \([\s\S]*data-testid="web-search-public-data-confirmation"[\s\S]*<PublicWebSearchApproval/,
  );
  assert.match(composer, /confirmed=\{webSearchPublicDataConfirmed\}/);
  assert.match(composer, /query=\{question\}/);
  assert.match(
    composer,
    /onConfirmedChange=\{setWebSearchPublicDataConfirmed\}/,
  );
  for (const copy of [
    "Nur öffentliche Suchbegriffe",
    "Public search terms only",
    "Keine persönlichen oder vertraulichen Inhalte.",
    "No personal or confidential content.",
    "Keine personenbezogenen Daten, vertraulichen oder sensiblen Angaben",
    "Do not use personal data, confidential or sensitive information",
    "Gesprächsverlauf, Transkripte, Manuskripte und Uploads nicht automatisch beigefügt",
    "does not automatically include conversation history, transcripts, manuscripts or uploads",
    "public-search service configured by this deployment",
    "disclose recipients, processing locations and retention",
  ]) {
    assert.ok(approval.includes(copy), `Missing public-search disclosure: ${copy}`);
  }
});

test("web-search confirmation blocks submit and is sent only as true for active search", () => {
  assert.match(
    composer,
    /const webSearchConfirmationRequired =[\s\S]*effectiveWebSearch \|\| quickAnswerWebSearch/,
  );
  assert.match(
    composer,
    /const webSearchConfirmationMissing =[\s\S]*webSearchConfirmationRequired && !webSearchPublicDataConfirmed/,
  );
  assert.match(
    composer,
    /const canSubmit =[\s\S]*!webSearchConfirmationMissing[\s\S]*!createRun\.isPending/,
  );
  assert.match(
    composer,
    /\.\.\.\(effectiveWebSearch && \{[\s\S]*web_search_public_data_confirmed: true,[\s\S]*\}\)/,
  );
  assert.match(
    composer,
    /\.\.\.\(quickAnswerWebSearch && \{[\s\S]*web_search: true,[\s\S]*web_search_public_data_confirmed: true/,
  );
  assert.doesNotMatch(
    composer,
    /web_search_public_data_confirmed:\s*(?:false|webSearchPublicDataConfirmed)/,
  );
  assert.match(
    types,
    /export interface RunConfig[\s\S]*web_search_public_data_confirmed\?: boolean;/,
  );
  assert.match(
    types,
    /export interface RunCreateRequest[\s\S]*web_search_public_data_confirmed\?: boolean;/,
  );
});

test("web-search confirmation never carries into a different scope", () => {
  assert.match(
    composer,
    /setQuestion\(stash\.question\);[\s\S]*setWebSearchPublicDataConfirmed\(false\);[\s\S]*setOptions\(optionsFromConfig\(stash\.config\)\)/,
  );
  assert.match(
    composer,
    /const webSearchAvailable = true;/,
  );
  assert.match(
    composer,
    /key === "webSearch" && options\.webSearch[\s\S]*setWebSearchPublicDataConfirmed\(false\)/,
  );
  assert.match(
    composer,
    /function resetSystematicReview\(\)[\s\S]*setOptions\(DEFAULTS\);[\s\S]*setWebSearchPublicDataConfirmed\(false\)/,
  );
  assert.match(
    composer,
    /const pool = EXAMPLE_QUESTIONS[\s\S]*setQuestion\([\s\S]*setWebSearchPublicDataConfirmed\(false\)/,
  );
});

test("Quick Answer and follow-up chat require a fresh confirmation for each web turn", () => {
  assert.match(
    composer,
    /\{quickAnswerWebSearch && \([\s\S]*data-testid="quick-answer-web-search-confirmation"[\s\S]*<PublicWebSearchApproval/,
  );
  assert.match(chat, /const webSearchRequested = explicitWebResearchRequested\(question\)/);
  assert.match(
    chat,
    /webSearchRequested && \([\s\S]*!webSearchPublicDataConfirmed \|\| !validPublicWebSearchQuery\(webSearchQuery\)/,
  );
  assert.match(
    chat,
    /setQuestion\(event\.target\.value\);[\s\S]*setWebSearchPublicDataConfirmed\(false\)/,
  );
  assert.match(
    chat,
    /send\(text, \{[\s\S]*webSearchPublicDataConfirmed:[\s\S]*webSearchRequested && webSearchPublicDataConfirmed/,
  );
  assert.match(
    api,
    /chat\/stream[\s\S]*web_search_public_data_confirmed:[\s\S]*options\?\.webSearchPublicDataConfirmed === true/,
  );
});

test("failed Quick Answer and chat retries never inherit the original confirmation", () => {
  assert.match(
    runView,
    /retryWebSearchConfirmed[\s\S]*api\.retryRun\(runId, \{[\s\S]*webSearchPublicDataConfirmed/,
  );
  assert.match(
    runView,
    /retryNeedsWebSearchConfirmation[\s\S]*!retryWebSearchConfirmed/,
  );
  assert.match(
    runView,
    /onSettled: \(\) => setRetryWebSearchConfirmed\(false\)/,
  );
  assert.match(chat, /retryFailedTurn: \(webSearchPublicDataConfirmed = false\)/);
  assert.doesNotMatch(
    chat,
    /setFailedTurn\(\{[\s\S]{0,300}webSearchPublicDataConfirmed/,
  );
});

test("protocol approval requires a fresh confirmation bound to the visible edits", () => {
  assert.match(
    protocolCard,
    /webSearchConfirmationRequired = gated && run\.config\.web_search/,
  );
  assert.match(
    protocolCard,
    /data-testid="protocol-web-search-public-data-confirmation"[\s\S]*<PublicWebSearchApproval[\s\S]*query=\{run.question\}/,
  );
  assert.match(
    protocolCard,
    /edits\.web_search_public_data_confirmed = true/,
  );
  assert.doesNotMatch(
    protocolCard,
    /web_search_public_data_confirmed\s*=\s*webSearchPublicDataConfirmed/,
  );
  assert.match(
    protocolCard,
    /webSearchConfirmationRequired &&[\s\S]*!webSearchPublicDataConfirmed/,
  );
  assert.match(
    api,
    /approveProtocol:[\s\S]*web_search_public_data_confirmed\?: true;/,
  );
});

test("editing or regenerating a protocol invalidates its checkbox", () => {
  assert.match(
    protocolCard,
    /onMutate: \(\) => setWebSearchPublicDataConfirmed\(false\)/,
  );
  for (const setter of ["setQuery", "setInclusion", "setExclusion"]) {
    assert.match(
      protocolCard,
      new RegExp(`${setter}\\(event\\.target\\.value\\);[\\s\\S]{0,120}setWebSearchPublicDataConfirmed\\(false\\)`),
    );
  }
});

test("an exact URL stays on the separate reader path", () => {
  assert.match(consent, /URL_IN_MESSAGE/);
  assert.match(
    consent,
    /!URL_IN_MESSAGE\.test\(message\)[\s\S]*EXPLICIT_WEB_RESEARCH\.test\(message\)/,
  );
});

test("using saved web sources without new research does not trigger discovery", () => {
  const check = new Function(
    consent.replace(
      "export function explicitWebResearchRequested(message: string): boolean",
      "function explicitWebResearchRequested(message)",
    ) + "\nreturn explicitWebResearchRequested;",
  )();
  for (const message of [
    "Use the saved official web sources, without new research.",
    "Nutze die vorhandenen Webquellen, ohne neue Recherche.",
    "Explain https://developer.hashicorp.com/terraform/cli/commands/plan",
  ]) {
    assert.equal(check(message), false);
  }
  assert.equal(check("Search the web for official Terraform documentation"), true);
});

test("incidental online context and negated web requests do not lock the composer", () => {
  const check = new Function(
    consent.replace(
      "export function explicitWebResearchRequested(message: string): boolean",
      "function explicitWebResearchRequested(message)",
    ) + "\nreturn explicitWebResearchRequested;",
  )();
  for (const message of [
    "Unser fiktiver Workshop findet online statt und umfasst fünf Teilnehmende. Bitte bestätige nur kurz.",
    "Unser Workshop findet online statt. Bitte bestätige nur kurz, keine Websuche.",
    "Our workshop takes place online. Remember BLUE ORCHID 42; no web search.",
    "Bitte keine Websuche zu Terraform.",
    "Bitte nicht im Internet suchen.",
    "Suche bitte nicht online nach Terraform.",
    "Do not browse the web for this answer.",
    "Don't look it up online.",
    "Do not check the official documentation; use the saved excerpt.",
    "Bitte keine Internetrecherche und keine Websuche.",
    "No further online research; summarize the existing material.",
    "Nutze die offizielle Dokumentation, ohne erneut zu suchen.",
    "Die Website ist blau. Unsere Dokumentation enthält fünf Abschnitte.",
    "I use Google for my work. Our vendor is listed in the documentation.",
    "Explain what web search means.",
  ]) {
    assert.equal(check(message), false, message);
  }
  for (const message of [
    "Schau auch mal noch im Internet nach.",
    "Suche dafür bitte im Web nach aktuellen Quellen.",
    "Look it up online too, especially in the official docs.",
    "Check the official vendor documentation.",
    "Do not edit the manuscript; search the web for official Terraform documentation.",
    "Bearbeite das Manuskript nicht, aber schau im Internet nach aktuellen Quellen.",
    "I do not know the budget. Look up public workshop prices online.",
    "Websuche zu Terraform",
    "Kannst du im Internet nach Terraform suchen?",
  ]) {
    assert.equal(check(message), true, message);
  }
});
