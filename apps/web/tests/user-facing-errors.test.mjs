import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import ts from "typescript";

const source = await readFile(
  new URL("../src/lib/user-facing-error.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const helpers = await import(
  `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`
);

test("provider and framework diagnostics never reach customer messages", () => {
  const leaked = [
    "token-based requests cannot use project-scoped features such as tuned models",
    "Gemini provider endpoint returned PERMISSION_DENIED",
    "httpx 502 server error from /opt/app/src/client.py",
    "OpenRouter API key is invalid",
    "WebSocket connection closed: ECONNRESET",
    "HTTP 502 Bad Gateway",
    "database connection refused",
    "NetworkError when attempting to fetch resource",
    "Unexpected token '<' while parsing JSON",
    "LLMConfigError: details redacted",
    "Nebula inference backend refused deployment comet-7b",
  ];
  for (const message of leaked) {
    assert.equal(
      helpers.userFacingApiErrorMessage(422, message),
      "That didn't work with the current input. Review it and try again.",
    );
  }
});

test("network failures use the connection fallback even when a browser supplies text", () => {
  assert.equal(
    helpers.userFacingApiErrorMessage(0, "Load failed"),
    "Something went wrong on our side. Please try again in a moment.",
  );
});

test("component-boundary sanitization preserves guidance and hides diagnostics", () => {
  assert.equal(
    helpers.userFacingErrorMessage(
      new Error("Choose a manuscript section before applying this edit."),
      "Retry the request.",
    ),
    "Choose a manuscript section before applying this edit.",
  );
  assert.equal(
    helpers.userFacingErrorMessage(
      new Error("WebSocket connection closed: ECONNRESET"),
      "Retry the request.",
    ),
    "Retry the request.",
  );
  assert.equal(
    helpers.userFacingErrorMessage(
      { status: 503, message: "upstream failed" },
      "Retry the request.",
    ),
    "Retry the request.",
  );
  assert.equal(
    helpers.userFacingErrorMessage(
      new Error("A harmless-looking but unreviewed message."),
      "Retry the request.",
    ),
    "Retry the request.",
  );
});

test("short actionable validation guidance remains visible", () => {
  assert.equal(
    helpers.userFacingApiErrorMessage(422, "Choose a PDF smaller than 100 MB."),
    "Choose a PDF smaller than 100 MB.",
  );
});

test("stable public error codes select local copy and never trust server messages", () => {
  assert.equal(
    helpers.userFacingApiErrorMessage(409, {
      code: "writer_revision_conflict",
      message: "OpenRouter sk-live-secret failed at /opt/app/provider.py",
    }),
    "Another author changed this passage. Refresh and compare the latest version.",
  );
  assert.equal(
    helpers.userFacingApiErrorMessage(422, {
      code: "unknown_future_code",
      message: "This looks friendly but contains secret-customer-data",
    }),
    "That didn't work with the current input. Review it and try again.",
  );
  assert.equal(
    helpers.userFacingApiErrorMessage(422, {
      detail: { code: "selection_not_on_page", message: "ignored" },
    }),
    "Select text that is visible on the current page.",
  );
});

test("stable entitlement codes distinguish access, remaining capacity and action limits", () => {
  const cases = [
    ["feature_not_in_plan", "feature", /not enabled/],
    ["upgrade_required", "feature", /not enabled/],
    ["capacity_exhausted", "capacity", /available capacity/],
    ["capacity_unavailable", "capacity", /available capacity/],
    ["concurrency_limit", "concurrency", /actions running/],
    ["resource_limit", "limit", /workspace limit/],
    ["resource_limit_reached", "limit", /workspace limit/],
    ["action_capacity_limit", "limit", /per-action limit/],
    ["entitlement_limit", "unavailable", /currently unavailable/],
  ];
  for (const [code, kind, copy] of cases) {
    const detail = { code, error: "Synthetic secret diagnostics", routing_hint: "hosted-tier" };
    assert.equal(helpers.entitlementErrorKind(402, detail), kind);
    assert.match(helpers.userFacingApiErrorMessage(402, detail), copy);
    assert.doesNotMatch(helpers.userFacingApiErrorMessage(402, detail), /secret|diagnostics/);
    assert.match(helpers.userFacingErrorMessage({ status: 402, detail }), copy);
  }
});

test("unknown 402 errors and authorization failures remain deployment-neutral", () => {
  for (const detail of [null, { error: "capacity exhausted", routing_hint: "hosted-tier" }, { code: "unknown" }, { code: "__proto__" }, { code: "constructor" }]) {
    assert.equal(helpers.entitlementErrorKind(402, detail), "unavailable");
    assert.equal(helpers.userFacingApiErrorMessage(402, detail), "This action is currently unavailable. Please try again or contact support.");
  }
  for (const status of [400, 401, 403, 404, 409, 429, 500, 0]) {
    assert.equal(helpers.entitlementErrorKind(status, { code: "feature_not_in_plan" }), null);
  }
  assert.equal(helpers.userFacingApiErrorMessage(401, { code: "feature_not_in_plan" }), "Your session is no longer valid. Please sign in again.");
  assert.equal(helpers.userFacingApiErrorMessage(403, { code: "capacity_exhausted" }), "You don't have permission to do that.");
  assert.equal(helpers.userFacingErrorMessage({ status: 403, detail: { code: "feature_not_in_plan" } }), "You don't have permission to do that.");
});

test("participant interview capacity errors use localized product copy", () => {
  const diagnostic = {
    status: 409,
    detail: {
      code: "voice_interview_capacity_unavailable",
      message: "cost_limit_usd=3.0 provider quota exhausted",
    },
  };
  assert.equal(
    helpers.userFacingApiErrorMessage(409, diagnostic.detail),
    "This interview cannot start right now because the study does not have enough capacity for a full session. Please try again later or contact the research team.",
  );
  assert.equal(
    helpers.userFacingPublicTalkErrorMessage(diagnostic, "de"),
    "Das Interview kann gerade nicht gestartet werden, weil nicht genügend Kapazität für eine vollständige Sitzung verfügbar ist. Bitte versuchen Sie es später erneut oder wenden Sie sich an das Forschungsteam.",
  );
  assert.equal(
    helpers.userFacingPublicTalkErrorMessage(diagnostic, "en"),
    "This interview cannot start right now because the study does not have enough capacity for a full session. Please try again later or contact the research team.",
  );
  assert.equal(
    helpers.userFacingPublicTalkErrorMessage(
      { status: 409, detail: { code: "unknown", message: "sk-live-secret" } },
      "de",
    ),
    "Das Interview konnte gerade nicht gestartet werden. Bitte versuchen Sie es erneut oder wenden Sie sich an das Forschungsteam.",
  );
});

test("unreviewed fallback text also fails closed", () => {
  assert.equal(
    helpers.userFacingErrorMessage(
      new Error("database connection refused"),
      "Retry with bearer token sk-live-secret",
    ),
    "That didn't work. Please try again.",
  );
  assert.equal(
    helpers.userFacingStoredErrorMessage("raw worker failure", ""),
    "",
  );
});

test("durable worker errors fail closed while curated recovery guidance remains visible", () => {
  const fallback = "We couldn't process this interview. Return to Interviews and try again.";
  const storedFailures = [
    "Nebula inference cluster refused the request for model comet-7b.",
    "The transcription service account has run out of balance.",
    "UNKNOWN_VENDOR_FAILURE: deployment eu-42 unavailable",
    "A harmless-looking but unreviewed legacy worker message.",
  ];
  for (const message of storedFailures) {
    assert.equal(
      helpers.userFacingStoredErrorMessage(message, fallback),
      fallback,
    );
  }
});

test("server and streamed agent failures use plain product language", () => {
  assert.equal(
    helpers.userFacingApiErrorMessage(503, "database connection refused"),
    "Something went wrong on our side. Please try again in a moment.",
  );
  assert.equal(
    helpers.userFacingAgentFailureMessage("raw provider detail"),
    "We couldn't finish this request. Your workspace was left unchanged. Please try again.",
  );
});

test("direct fetches and durable event views sanitize server-owned failures", async () => {
  const [
    review,
    runView,
    controlRoom,
    chatPanel,
    agentStatus,
    figures,
    repositorySource,
    repositoryManuscript,
    apiSource,
    stageMeta,
  ] = await Promise.all([
    readFile(new URL("../src/app/w/[token]/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../src/components/run/run-view.tsx", import.meta.url), "utf8"),
    readFile(
      new URL("../src/components/run/research-control-room.tsx", import.meta.url),
      "utf8",
    ),
    readFile(new URL("../src/components/run/chat-panel.tsx", import.meta.url), "utf8"),
    readFile(
      new URL("../src/components/agent-work-status.tsx", import.meta.url),
      "utf8",
    ),
    readFile(new URL("../src/app/(app)/figures/page.tsx", import.meta.url), "utf8"),
    readFile(
      new URL("../src/components/figures/repository-source-dialog.tsx", import.meta.url),
      "utf8",
    ),
    readFile(
      new URL("../src/components/figures/repository-manuscript-panel.tsx", import.meta.url),
      "utf8",
    ),
    readFile(new URL("../src/lib/api.ts", import.meta.url), "utf8"),
    readFile(new URL("../src/components/run/stage-meta.tsx", import.meta.url), "utf8"),
  ]);

  assert.match(review, /userFacingApiErrorMessage\(res\.status, message\)/);
  assert.doesNotMatch(review, /throw new ApiError\(res\.status, message\)/);
  assert.match(runView, /userFacingStoredErrorMessage\(\s*run\.error/);
  assert.match(controlRoom, /userFacingStoredErrorMessage\(\s*data\.worker\.last_error/);
  assert.match(chatPanel, /userFacingStoredErrorMessage\(\s*terminal\.message/);
  assert.match(chatPanel, /userFacingStoredErrorMessage\(\s*state\.error_message/);
  assert.match(chatPanel, /userFacingStoredErrorMessage\(\s*message\.content/);
  assert.match(chatPanel, /userFacingStoredErrorMessage\(payload\?\.reason/);
  assert.match(chatPanel, /userFacingStoredErrorMessage\(\s*result\.error/);
  assert.match(chatPanel, /toast\.error\(userFacingErrorMessage\(error, "The download failed\."\)\)/);
  assert.match(agentStatus, /isFailureEvent\(currentEvent\)\s*\? undefined/);
  assert.match(agentStatus, /return userFacingStoredErrorMessage\(value, fallback\)/);
  assert.doesNotMatch(figures, /\{figure\.error \|\| "Generation failed\."\}/);
  assert.match(figures, /userFacingStoredErrorMessage\(\s*stage\.detail/);
  assert.match(figures, /userFacingStoredErrorMessage\(\s*figure\.error/);
  assert.match(figures, /toast\.error\(userFacingErrorMessage\(error, "Download failed\."\)\)/);
  assert.doesNotMatch(repositorySource, /\{analysis\.error \|\|/);
  assert.doesNotMatch(repositorySource, />\s*\{analysis\.error_code\}\s*</);
  assert.match(repositorySource, /userFacingStoredErrorMessage\(\s*analysis\.error/);
  assert.match(repositoryManuscript, /userFacingErrorMessage\(/);
  assert.match(
    repositoryManuscript,
    /userFacingStoredErrorMessage\(\s*turn\.error_message/,
  );
  assert.match(apiSource, /userFacingApiErrorMessage\(res\.status, payload\)/);
  assert.match(apiSource, /return readJsonResponse<T>\(res\)/);
  assert.match(apiSource, /const res = await fetchApiResponse\(/);
  assert.match(stageMeta, /p\.grey_literature \?\? p\.found \?\? p\.returned/);
});

test("interview list and detail views never render stored errors verbatim", async () => {
  const [list, detail, liveSessions] = await Promise.all([
    readFile(new URL("../src/app/(app)/interviews/page.tsx", import.meta.url), "utf8"),
    readFile(
      new URL("../src/app/(app)/interviews/[id]/page.tsx", import.meta.url),
      "utf8",
    ),
    readFile(
      new URL("../src/components/interviews/live-sessions-panel.tsx", import.meta.url),
      "utf8",
    ),
  ]);

  assert.match(list, /userFacingStoredErrorMessage\(\s*interview\.error/);
  assert.match(detail, /userFacingStoredErrorMessage\(\s*interview\.error/);
  assert.match(
    detail,
    /stage\.status === "failed"\s*\? userFacingStoredErrorMessage\(\s*stage\.detail/,
  );
  assert.match(liveSessions, /userFacingStoredErrorMessage\(\s*ask\.error/);
  assert.doesNotMatch(list, />\s*\{interview\.error\}\s*</);
  assert.doesNotMatch(detail, />\s*\{interview\.error\}\s*</);
  assert.doesNotMatch(liveSessions, /\? \(ask\.error \|\|/);
});

test("paper translation failures never render stored worker errors verbatim", async () => {
  const paperPanel = await readFile(
    new URL("../src/components/run/paper-panel.tsx", import.meta.url),
    "utf8",
  );

  assert.match(
    paperPanel,
    /userFacingStoredErrorMessage\(\s*translationStatus\.data\.error/,
  );
  assert.doesNotMatch(paperPanel, /\{translationStatus\.data\.error \?\?/);
});
