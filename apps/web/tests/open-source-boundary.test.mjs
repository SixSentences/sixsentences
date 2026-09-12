import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const read = (path) => readFileSync(join(process.cwd(), path), "utf8");

test("registration legal versions belong to the deployment and fail closed", () => {
  const legal = read("src/lib/legal.ts");
  const register = read("src/app/(auth)/register/page.tsx");
  const environment = read(".env.example");

  for (const variable of [
    "NEXT_PUBLIC_TERMS_VERSION",
    "NEXT_PUBLIC_PRIVACY_VERSION",
    "NEXT_PUBLIC_DPA_VERSION",
  ]) {
    assert.match(legal, new RegExp(`process\\.env\\.${variable}`));
    assert.match(environment, new RegExp(`^${variable}=$`, "m"));
  }
  assert.doesNotMatch(legal, /2026-09-12|1\.11|CheckoutLegalDeclaration|WITHDRAWAL/);
  assert.match(register, /Registration is unavailable until the deployment operator configures its legal documents/);
  assert.match(register, /SIGNUP_LEGAL_VERSIONS\.terms/);
});

test("missing legal configuration never invents localhost document routes", () => {
  const links = read("src/lib/public-links.ts");
  const environment = read(".env.example");

  assert.match(environment, /^NEXT_PUBLIC_LEGAL_BASE_URL=$/m);
  assert.match(links, /if \(!publicLegalBaseUrl\) return null/);
  assert.doesNotMatch(
    links,
    /NEXT_PUBLIC_LEGAL_BASE_URL\)[\s\S]{0,80}\?\?\s*publicAppBaseUrl/,
  );
});

test("legal and provider disclosures are deployment-neutral", () => {
  const sources = [
    "src/components/legal-reacceptance.tsx",
    "src/components/search/public-web-search-approval.tsx",
    "src/components/run/chat-report-dialog.tsx",
    "src/app/(app)/figures/page.tsx",
    "src/app/s/[id]/page.tsx",
    "src/app/talk/[token]/layout.tsx",
  ].map(read).join("\n");

  assert.doesNotMatch(
    sources,
    /OpenRouter|Perplexity|Backblaze|Google Gemini provider|SixSentences_ employees|hosted securely|Hosted by SixSentences_|hosts it on their behalf/i,
  );
  assert.match(sources, /public-search service configured by this deployment/);
  assert.match(sources, /Reviewers authorized by this deployment/);
});

test("the excluded browser extension has no download or store distribution surface", () => {
  const devices = read("src/components/library/browser-capture-devices.tsx");
  const library = read("src/app/(app)/library/page.tsx");

  assert.equal(existsSync(join(process.cwd(), "src/lib/browser-capture-distribution.mjs")), false);
  assert.doesNotMatch(devices, /Download|ExternalLink|Chrome Web Store|preview\.zip|storeUrl/);
  assert.doesNotMatch(library, /click Browser Capture|Saved in one click/);
  assert.match(devices, /does not include a browser extension/);
});

test("removed hosted account-switching UI leaves no client API surface", () => {
  const api = read("src/lib/api.ts");
  const types = read("src/lib/types.ts");

  assert.equal(existsSync(join(process.cwd(), "src/components/settings/switching-controls.tsx")), false);
  assert.doesNotMatch(api, /accountSwitching|requestAccountSwitching|auth\/account\/switching/);
  assert.doesNotMatch(types, /AccountSwitching|SwitchingRequest/);
});

test("private model choices rely on the API declaration, not one provider", () => {
  const selection = read("src/lib/private-model-selection.ts");
  const types = read("src/lib/types.ts");

  assert.match(selection, /catalog\.content_scope !== "private"/);
  assert.match(selection, /catalog\.models\.filter\(\(model\) => !model\.locked\)/);
  assert.doesNotMatch(selection, /gemini|openrouter|perplexity/i);
  assert.match(types, /routing_mode\?: string/);
  assert.match(types, /live_provider: string/);
});

test("the public client contains no hosted billing or purchased-capacity presentation", () => {
  const sources = [
    "src/app/(auth)/register/page.tsx",
    "src/app/(app)/interviews/studies/[id]/page.tsx",
    "src/app/(app)/figures/page.tsx",
    "src/app/globals.css",
    "src/components/search/model-picker.tsx",
    "src/components/run/run-controls.tsx",
    "src/components/run/evidence-panel.tsx",
    "src/components/run/extraction-studio.tsx",
    "src/components/voice/live-session.tsx",
    "src/lib/types.ts",
  ].map(read).join("\n");

  assert.doesNotMatch(
    sources,
    /one-time starter research allowance|recorded model costs?|purchased capacity|monthly allowance|capacity allocation|cost_tier|cost_units|capacity_percent|capacity_authorization_percent|capacity_accounting|used_minutes|live_capacity_percent_per_minute/i,
  );
  assert.match(sources, /resource limit/);
  assert.match(sources, /connected API currently allows spoken sessions up to/);
});

test("provider catalogs and optional companion distribution stay operator-owned", () => {
  const figures = read("src/app/(app)/figures/page.tsx");
  const liveSessions = read("src/components/interviews/live-sessions-panel.tsx");
  const brainstorm = read("src/app/(app)/brainstorming/page.tsx");
  const repository = read("src/components/figures/repository-source-dialog.tsx");
  const combined = [figures, liveSessions, brainstorm, repository].join("\n");

  assert.doesNotMatch(combined, /Nano Banana|Fast Google|Google AI Studio|sent to SixSentences|SixSentences receives/i);
  assert.doesNotMatch(liveSessions, /download_url|Download macOS preview|Companion Preview|macOS preview/i);
  assert.doesNotMatch(brainstorm, /desktop\.download_url|Companion Preview/i);
  assert.match(figures, /Model selected by the connected API/);
  assert.match(liveSessions, /separately supplied compatible desktop client/);
  assert.match(repository, /Continue only if the configured API stores the PAT encrypted/);
});

test("wire types describe a compatible API rather than a bundled hosted backend", () => {
  const api = read("src/lib/api.ts");
  const types = read("src/lib/types.ts");

  assert.match(api, /Typed client for a compatible SixSentences workspace API/);
  assert.match(types, /Wire types for a compatible SixSentences workspace API/);
  assert.doesNotMatch(`${api}\n${types}`, /SixSentences_ core API|FastAPI responses/);
});

test("visible workflow guidance describes quality and runtime without usage economics", () => {
  const sources = [
    "src/components/tour/product-tour.tsx",
    "src/components/run/evidence-panel.tsx",
    "src/components/run/chat-panel.tsx",
  ].map(read).join("\n");

  assert.doesNotMatch(sources, /\busage\b|\bNutzung\b/i);
  assert.match(sources, /quality and runtime/);
  assert.match(sources, /coverage and runtime/);
  assert.match(sources, /Extract evidence/);
});

test("the hosted ideas board and moderation API are excluded", () => {
  const api = read("src/lib/api.ts");
  const queries = read("src/hooks/queries.ts");
  const types = read("src/lib/types.ts");

  assert.equal(existsSync(join(process.cwd(), "src/app/(app)/ideas/page.tsx")), false);
  assert.doesNotMatch(api, /api\.features|createFeature|voteFeature|\/features/);
  assert.doesNotMatch(queries, /useFeatures|\["features"\]/);
  assert.doesNotMatch(types, /FeatureRequest|FeatureStatus/);
});

test("registration never offers or enables operator marketing", () => {
  const register = read("src/app/(auth)/register/page.tsx");
  const legal = read("src/lib/legal.ts");

  assert.doesNotMatch(register, /marketingConsent|product updates|workflow tips|unsubscribe/i);
  assert.match(register, /marketing_consent: false/);
  assert.match(legal, /public client never opts users into operator marketing/);
});
