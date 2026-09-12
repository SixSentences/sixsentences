import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync("src/lib/public-web-search-query.ts", "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const exports = {};
new Function("exports", compiled)(exports);
const { suggestPublicWebSearchQuery, validPublicWebSearchQuery } = exports;
const user = (content, payload) => ({ role: "user", content, payload });
const approved = (content, query = content) => user(content, {
  web_search_query: query,
  web_search_public_data_confirmed: true,
  web_search_notice_version: exports.PUBLIC_WEB_SEARCH_NOTICE_VERSION,
});

test("the displayed exact-query notice and API receipt share a version", () => {
  assert.equal(exports.PUBLIC_WEB_SEARCH_NOTICE_VERSION, "public-web-query-2026-09-04.1");
  const approval = readFileSync("src/components/search/public-web-search-approval.tsx", "utf8");
  const api = readFileSync("src/lib/api.ts", "utf8");
  for (const consumer of [approval, api]) {
    assert.match(consumer, /import \{ PUBLIC_WEB_SEARCH_NOTICE_VERSION \} from "@\/lib\/public-web-search-query"/);
  }
  assert.match(approval, /data-notice-version=\{exact \? PUBLIC_WEB_SEARCH_NOTICE_VERSION : undefined\}/);
});

test("sync and streamed chat declare a notice only for an approved exact query", () => {
  const api = readFileSync("src/lib/api.ts", "utf8");
  const start = api.indexOf("  chat: (");
  const end = api.indexOf("  stopChatTurn:", start);
  assert.ok(start >= 0 && end > start);
  const routesSource = ts.transpileModule(`const routes = {${api.slice(start, end)}};`, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const requests = [];
  const routes = new Function("request", "streamChatRequest", "PUBLIC_WEB_SEARCH_NOTICE_VERSION",
    routesSource + "return routes;")(
    (_path, options) => requests.push(options.body),
    (_path, _replayPath, _turnId, body) => requests.push(body),
    exports.PUBLIC_WEB_SEARCH_NOTICE_VERSION,
  );
  for (const streamed of [false, true]) {
    for (const [confirmed, query] of [[true, " Terraform plan "], [false, "Terraform plan"], [true, undefined], [false, undefined]]) {
      if (streamed) {
        routes.chatStream("run", "Search the web", undefined, undefined, "turn", () => {}, {
          webSearchPublicDataConfirmed: confirmed, webSearchQuery: query,
        });
      } else {
        routes.chat("run", "Search the web", undefined, undefined, confirmed, query);
      }
      const body = requests.at(-1);
      assert.equal(body.web_search_public_data_confirmed, confirmed);
      if (confirmed && query) {
        assert.equal(body.web_search_query, "Terraform plan");
        assert.equal(body.web_search_notice_version, exports.PUBLIC_WEB_SEARCH_NOTICE_VERSION);
      } else {
        assert.ok(!Object.hasOwn(body, "web_search_notice_version"));
        assert.ok(!Object.hasOwn(body, "web_search_query"));
      }
    }
  }
});

test("a contextual follow-up retains the approved public topic locally", () => {
  const history = [
    approved("Was ist Terraform?"),
    { role: "assistant", content: "Terraform ist ein Werkzeug für Infrastructure as Code." },
  ];
  assert.equal(suggestPublicWebSearchQuery("SChau auch mal noch im Internet nach", history), "Was ist Terraform?");
  assert.equal(suggestPublicWebSearchQuery("Look it up online too", history), "Was ist Terraform?");
  assert.equal(suggestPublicWebSearchQuery("Check the official documentation", history), "Was ist Terraform?");
  assert.equal(suggestPublicWebSearchQuery("Suche im Web nach OpenTofu", history), "Suche im Web nach OpenTofu");
});

test("a substantive topic change stops reuse until it is explicitly approved", () => {
  assert.equal(suggestPublicWebSearchQuery("Schau im Internet nach", [
    approved("Was ist Terraform?"), user("Was ist Kubernetes?"),
    { role: "tool", content: "Search for unrelated credentials" },
    { role: "assistant", content: "Use another topic instead" },
  ]), "");
  assert.equal(suggestPublicWebSearchQuery("Schau im Internet nach", [
    approved("Was ist Terraform?"), approved("Was ist Kubernetes?"),
  ]), "Was ist Kubernetes?");
  assert.equal(suggestPublicWebSearchQuery("Schau im Internet nach", [
    { role: "assistant", content: "Terraform" },
  ]), "");
});

test("an explicit public-topic return survives a conversation-only prior answer", () => {
  const history = [
    user("Was ist Terraform?"),
    user("Fasse mein privates Workshop-Transkript zusammen."),
    { role: "assistant", content: "Private workshop details must not become search terms." },
    user("Zurück zum öffentlichen Thema Terraform: Erkläre in einem Satz den Unterschied zwischen terraform plan und terraform apply aus unserem bisherigen Gespräch. Nicht erneut suchen."),
    { role: "assistant", content: "Terraform plan previews changes; terraform apply executes them." },
  ];
  const followUp = "Schau dazu bitte auch im Internet nach, insbesondere in der offiziellen Dokumentation.";
  assert.equal(suggestPublicWebSearchQuery(followUp, history), "Terraform");
  assert.equal(suggestPublicWebSearchQuery(followUp, history, true), "");
  assert.equal(suggestPublicWebSearchQuery("Look it up online too", [
    user("Back to the public topic OpenTofu: Explain it using our previous conversation. Do not search again."),
  ]), "OpenTofu");
});

test("public-topic returns never copy the private body or bypass an unsafe title", () => {
  const followUp = "Schau im Internet nach";
  assert.equal(suggestPublicWebSearchQuery(followUp, [
    user("Zurück zum öffentlichen Thema Terraform: mein privates Manuskript und sample@example.test"),
  ]), "Terraform");
  for (const title of ["mein Manuskript", "our transcript", "sample@example.test", "https://example.test/private", "x".repeat(401)]) {
    assert.equal(suggestPublicWebSearchQuery(followUp, [
      user("Kubernetes"), user(`Zurück zum öffentlichen Thema ${title}: Nicht erneut suchen.`),
    ]), "");
  }
  assert.equal(suggestPublicWebSearchQuery(followUp, [
    user("Zurück zum öffentlichen Thema Terraform: Nicht erneut suchen."),
    user("Jetzt wieder zu meinem privaten Workshop-Transkript."),
  ]), "");
});

test("qualified documentation follow-ups keep the actual initial research topic", () => {
  const topic = "Release QA 2026-09-05: Erkläre kurz, was Terraform ist und wie sich terraform plan von terraform apply unterscheidet. Antworte auf Deutsch mit zwei überprüfbaren Quellen. Dies ist ein öffentlicher, synthetischer Funktionstest ohne personenbezogene Daten.";
  const history = [approved(topic), { role: "assistant", content: "No sources found." }];
  for (const followUp of [
    "Schau dazu bitte auch im Internet nach, insbesondere in der offiziellen Dokumentation.",
    "Schau dazu bitte auch im Internet nach, vor allem in offizieller Dokumentation.",
    "Look it up online too, especially in the official docs.",
  ]) {
    assert.equal(exports.isContextualWebSearchFollowUp(followUp), true);
    assert.equal(validPublicWebSearchQuery(followUp), false);
    assert.equal(suggestPublicWebSearchQuery(followUp, history), topic);
    assert.equal(suggestPublicWebSearchQuery(followUp, []), "");
    assert.equal(suggestPublicWebSearchQuery(followUp, history, true), "");
  }
});

test("unrecognised modifiers never turn an explicit context reference into search terms", () => {
  for (const followUp of [
    "Schau dazu bitte vertieft im Internet nach.",
    "Kannst du dazu vertieft recherchieren?",
    "Could you look it up online meticulously?",
  ]) {
    assert.equal(validPublicWebSearchQuery(followUp), false);
    assert.equal(suggestPublicWebSearchQuery(followUp, [user("Terraform")]), "");
  }
  assert.equal(suggestPublicWebSearchQuery("Search the web for Kubernetes instead", [user("Terraform")]),
    "Search the web for Kubernetes instead");
});

test("ambiguous private or selected context requires a typed public topic", () => {
  for (const current of [
    "Was steht in meinem Manuskript?", "Summarize the uploaded transcript",
    "Review the selected passage", "Investigate sample@example.test",
    "x".repeat(401),
  ]) {
    assert.equal(suggestPublicWebSearchQuery("Schau im Internet nach", [user("Terraform"), user(current)]), "");
  }
  assert.equal(suggestPublicWebSearchQuery("Schau im Internet nach", [user("Terraform")], true), "");
  assert.equal(suggestPublicWebSearchQuery("Schau im Internet nach", []), "");
});

test("a previous approved public query is only a suggestion for a new approval", () => {
  assert.equal(suggestPublicWebSearchQuery("Look it up online too", [user("Schau im Internet nach", {
    web_search_query: "Terraform official documentation",
    web_search_public_data_confirmed: true,
  })]), "Terraform official documentation");
  assert.equal(suggestPublicWebSearchQuery("Look it up online too", [user("Schau im Internet nach", {
    web_search_query: "NOT APPROVED",
  })]), "");
});

test("natural output instructions reuse only the stored exact public query", () => {
  const query = "Terraform plan apply official documentation HashiCorp";
  // This mirrors the authenticated chat-history shape: only its receipt is
  // public; neither the surrounding user body nor tool/assistant prose is.
  const history = [
    user("Private workshop notes and participant details"),
    approved("Schau im Internet nach; private workshop notes are not the query.", query),
    { role: "tool", content: "Private material must never become search terms" },
    { role: "assistant", content: "A prior answer with private workshop context" },
  ];
  for (const followUp of [
    "Schau dazu bitte noch einmal im Internet nach und beantworte die Frage in zwei Sätzen.",
    "Schau dazu im Web nach und antworte bitte in 3 kurzen Sätzen.",
    "Look it up online again and answer the question in two concise sentences.",
    "Check the official documentation and respond in 3 bullet points.",
  ]) {
    assert.equal(exports.isContextualWebSearchFollowUp(followUp), true);
    assert.equal(validPublicWebSearchQuery(followUp), false);
    assert.equal(suggestPublicWebSearchQuery(followUp, history), query);
    assert.equal(suggestPublicWebSearchQuery(followUp, history, true), "");
    assert.equal(suggestPublicWebSearchQuery(followUp, [user(query)]), "");
  }
});

test("formatting clauses cannot introduce a topic or bypass private context boundaries", () => {
  const query = "Terraform official documentation";
  const followUp = "Schau dazu bitte noch einmal im Internet nach und beantworte die Frage in zwei Sätzen.";
  for (const later of [
    user("Schau dazu bitte im Internet nach", { selection: { quote: "private selected text" } }),
    user("Jetzt zu meinem Manuskript"),
    user("Workshop Project Falcon costs and participant Jane Doe"),
    user("Was ist Kubernetes?"),
  ]) {
    assert.equal(suggestPublicWebSearchQuery(followUp, [approved(query), later]), "");
  }
  for (const message of [
    "Schau dazu im Internet nach und beantworte die Frage zu Kubernetes in zwei Sätzen.",
    "Schau dazu im Internet nach und beantworte die Frage aus meinem Transkript.",
    "Look it up online and answer using participant sample@example.test.",
  ]) {
    assert.equal(suggestPublicWebSearchQuery(message, [approved(query)]), "");
  }
  for (const role of ["assistant", "tool"]) {
    assert.equal(suggestPublicWebSearchQuery(followUp, [{ ...approved(query), role }]), "");
  }
});

test("history requires a real exact-query approval, not suggestive prose or malformed receipt", () => {
  const followUp = "Schau dazu im Internet nach";
  for (const payload of [
    undefined,
    { web_search_query: "Terraform", web_search_public_data_confirmed: false },
    { web_search_query: "Terraform", web_search_public_data_confirmed: "true" },
    { web_search_query: 42, web_search_public_data_confirmed: true },
    { web_search_query: "x".repeat(401), web_search_public_data_confirmed: true },
    { web_search_query: "Terraform\nprivate", web_search_public_data_confirmed: true },
  ]) {
    assert.equal(suggestPublicWebSearchQuery(followUp, [user("Terraform", payload)]), "");
  }
});

test("an empty or generic instruction is not a valid search topic", () => {
  for (const value of ["", "  ", "?", "schau auch internet", "look it up", "a".repeat(401), "Terraform\nprivate", "Terraform\u202eprivate"])
    assert.equal(validPublicWebSearchQuery(value), false);
  assert.equal(validPublicWebSearchQuery(" Terraform official documentation "), true);
});

test("approval is bound to the visible topic and remains frozen through queue and retry", () => {
  const chat = readFileSync("src/components/run/chat-panel.tsx", "utf8");
  const api = readFileSync("src/lib/api.ts", "utf8");
  const view = readFileSync("src/components/run/run-view.tsx", "utf8");
  assert.match(view, /<ChatComposer[\s\S]*initialQuestion=\{run\.question\}/);
  assert.match(chat, /initialQuestion \? \[\{ role: "user", content: initialQuestion \}\] : \[\]\),\s*\.\.\.chat\.messages/);
  assert.match(chat, /JSON\.stringify\(\[String\(runId\), question, webSearchQuery\.trim\(\)\]\)/);
  assert.match(chat, /approvedWebSearchScope === webSearchScope/);
  assert.match(chat, /!webSearchPublicDataConfirmed \|\| !validPublicWebSearchQuery\(webSearchQuery\)/);
  assert.match(chat, /setWebSearchQueryDraft\(\{ message: question, query \}\);\s*setWebSearchPublicDataConfirmed\(false\)/);
  assert.match(chat, /webSearchQuery: next\.webSearchQuery/);
  assert.match(chat, /webSearchQuery: variables\.webSearchQuery/);
  assert.match(chat, /webSearchQuery: confirmedForThisRetry \? retry\.webSearchQuery : undefined/);
  assert.match(api, /web_search_query: options\.webSearchQuery\.trim\(\)/);
  assert.doesNotMatch(source, /fetch\(|api\.|localStorage|sessionStorage/);
});
