import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import ts from "typescript";

const source = await readFile(
  new URL("../src/lib/survey-agent-sync.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
}).outputText;
const { projectConfirmedSurveyChange } = await import(
  `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`
);

const question = (id, title) => ({
  id,
  title,
  description: "",
  type: "short_text",
  required: false,
  options: [],
  min: null,
  max: null,
});
const baseSurvey = () => ({
  id: 1,
  public_id: "survey-1",
  project_id: null,
  title: "Old title",
  description: "Old description",
  status: "draft",
  questions: [question("q1", "One"), question("q2", "Two")],
  settings: { confirmation: "Old confirmation", collect_identity: false },
  response_count: 0,
  summary: { responses: 0, complete: 0, completion_percent: 0, questions: [] },
  created_at: "2026-08-11T00:00:00Z",
  updated_at: "2026-08-11T00:00:00Z",
});
const change = (operation, after, input = {}) => ({
  id: 1,
  event: "change.completed",
  applied: true,
  operation,
  input,
  after,
});

test("confirmed survey metadata and settings changes project immediately", () => {
  let survey = baseSurvey();
  survey = projectConfirmedSurveyChange(survey, change("set_title", "New title"));
  survey = projectConfirmedSurveyChange(survey, change("set_description", { description: "New description" }));
  survey = projectConfirmedSurveyChange(survey, change("set_confirmation", { confirmation: "Thanks" }));
  survey = projectConfirmedSurveyChange(survey, change("set_collect_identity", { collect_identity: true }));
  assert.equal(survey.title, "New title");
  assert.equal(survey.description, "New description");
  assert.equal(survey.settings.confirmation, "Thanks");
  assert.equal(survey.settings.collect_identity, true);
});

test("all question operations preserve valid question objects and order", () => {
  const q3 = question("q3", "Three");
  let survey = projectConfirmedSurveyChange(
    baseSurvey(),
    change("add_question", q3, { position: 1 }),
  );
  assert.deepEqual(survey.questions.map((item) => item.id), ["q1", "q3", "q2"]);

  survey = projectConfirmedSurveyChange(
    survey,
    change("update_question", question("q3", "Three updated"), { question_id: "q3" }),
  );
  assert.equal(survey.questions[1].title, "Three updated");

  survey = projectConfirmedSurveyChange(
    survey,
    change("delete_question", null, { question_id: "q1" }),
  );
  assert.deepEqual(survey.questions.map((item) => item.id), ["q3", "q2"]);

  const replacements = [question("q4", "Four"), question("q5", "Five")];
  survey = projectConfirmedSurveyChange(survey, change("replace_questions", replacements));
  assert.deepEqual(survey.questions.map((item) => item.id), ["q4", "q5"]);

  survey = projectConfirmedSurveyChange(survey, change("reorder_questions", ["q5", "q4"]));
  assert.deepEqual(survey.questions.map((item) => item.id), ["q5", "q4"]);
  assert.ok(survey.questions.every((item) => typeof item.title === "string"));
});

test("proposed, unapplied and malformed changes never alter the editor", () => {
  const survey = baseSurvey();
  assert.equal(
    projectConfirmedSurveyChange(survey, { ...change("set_title", "No"), event: "change.proposed" }),
    survey,
  );
  assert.equal(
    projectConfirmedSurveyChange(survey, { ...change("set_title", "No"), applied: false }),
    survey,
  );
  assert.equal(
    projectConfirmedSurveyChange(survey, change("replace_questions", ["q2", "q1"])),
    survey,
  );
  assert.equal(
    projectConfirmedSurveyChange(survey, change("reorder_questions", ["q2"])),
    survey,
  );
});
