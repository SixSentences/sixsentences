import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import ts from "typescript";

const source = await readFile(
  new URL("../src/lib/safe-agent-display.ts", import.meta.url),
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

test("agent display removes internal controller limits from every value depth", () => {
  assert.equal(
    helpers.safeAgentDisplayText("The 4-iteration ceiling was reached."),
    "",
  );
  assert.deepEqual(
    helpers.safeAgentDisplayValue({
      plan: ["Read the source", "Only 2 tool calls remain"],
      output: { detail: "The iteration count is 4", result: "Five passages found" },
    }),
    {
      plan: ["Read the source"],
      output: { result: "Five passages found" },
    },
  );
});

test("agent display preserves domain iteration results", () => {
  assert.equal(
    helpers.safeAgentDisplayText(
      "The optimization algorithm converged after four iterations.",
    ),
    "The optimization algorithm converged after four iterations.",
  );
});

test("ordinary progress hides workaround history and unnecessary non-actions", () => {
  assert.equal(
    helpers.safeAgentProgressText(
      "The previous attempt failed because of a temporary workaround. Reviewed 12 responses.",
    ),
    "Reviewed 12 responses.",
  );
  assert.equal(
    helpers.safeAgentProgressText(
      "No changes were made. Prepared the questionnaire for review.",
    ),
    "Prepared the questionnaire for review.",
  );
  assert.equal(
    helpers.safeAgentProgressText("Skipped a duplicate agent action."),
    "Reviewed the available results",
  );
  assert.equal(
    helpers.safeAgentProgressText("Compacted the working context."),
    "Reviewed the latest workspace state",
  );
  assert.equal(
    helpers.safeAgentProgressText(
      "Using the stored interview as evidence, not creating a new interview stud",
    ),
    "Reviewed the saved interview transcript",
  );
  assert.equal(
    helpers.safeAgentProgressText(
      "Working only inside the open survey and its validated editor actions.",
    ),
    "Reviewed the current survey draft",
  );
  assert.equal(
    helpers.safeAgentProgressText(
      "Working with the current interview study; do not create a separate study.",
    ),
    "Reviewed the current interview study",
  );
});

test("structured tool results remain verbatim apart from controller limits", () => {
  assert.deepEqual(
    helpers.safeAgentDisplayValue({
      finding: "No changes were made in the control group.",
      internal: "Only 2 tool calls remain",
    }),
    { finding: "No changes were made in the control group." },
  );
});

test("honest errors remain available to error renderers", () => {
  assert.equal(
    helpers.safeAgentDisplayText("The requested change was not applied."),
    "The requested change was not applied.",
  );
});

test("research links accept only explicit HTTP or HTTPS URLs", () => {
  assert.equal(helpers.safeExternalHttpUrl("javascript:alert(1)"), "");
  assert.equal(helpers.safeExternalHttpUrl("//evil.example/path"), "");
  assert.equal(
    helpers.safeExternalHttpUrl("https://example.org/paper"),
    "https://example.org/paper",
  );
});

test("plain research URLs link only exact observed pages and preserve prose", () => {
  const url = "https://www.prisma-statement.org/prisma-2020";
  const text = `Read (${url}), not https://invented.example/result.`;
  const parts = helpers.observedSourceTextParts(text, [url]);
  assert.equal(parts.map((part) => part.text).join(""), text);
  assert.deepEqual(parts.filter((part) => part.href), [{ text: url, href: url }]);
  assert.deepEqual(helpers.observedSourceTextParts(text, []), [{ text }]);
  const balanced = "https://example.org/article_(2026)";
  assert.equal(
    helpers.observedSourceTextParts(`${balanced}.`, [balanced])[0].href,
    balanced,
  );
  assert.equal(
    helpers.observedSourceTextParts("javascript:alert(1)", ["javascript:alert(1)"])
      .some((part) => part.href),
    false,
  );
});

test("artifact links reject cross-origin and path traversal routes", () => {
  assert.equal(helpers.safeAgentArtifactHref("//evil.example/path"), "");
  assert.equal(helpers.safeAgentArtifactHref("/writer/../admin"), "");
  assert.equal(helpers.safeAgentArtifactHref("/writer/%2e%2e/admin"), "");
  assert.equal(helpers.safeAgentArtifactHref("javascript:alert(1)"), "");
  assert.equal(
    helpers.safeAgentArtifactHref("/data/nzr62tkj2j?analysis=analysis_1"),
    "/data/nzr62tkj2j?analysis=analysis_1",
  );
});
