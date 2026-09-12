import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { runInNewContext } from "node:vm";
import test from "node:test";
import ts from "typescript";

const read = (path) => readFileSync(join(process.cwd(), path), "utf8");
function loadPureSource(source, globals = {}) {
  const exports = {};
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  runInNewContext(compiled, { exports, ...globals });
  return exports;
}

test("read-only retries recover transport/5xx but never retry denied or missing resources", () => {
  const api = read("src/lib/api.ts");
  class ApiError extends Error {
    constructor(status) { super("Synthetic"); this.status = status; }
  }
  const policy = loadPureSource(api.slice(
    api.indexOf("export function retryTransientApiQuery("),
    api.indexOf("export type ChatStreamFailureKind"),
  ), { ApiError, Error });
  for (const status of [0, 500, 502, 503]) {
    assert.equal(policy.retryTransientApiQuery(0, new ApiError(status)), true);
    assert.equal(policy.retryTransientApiQuery(5, new ApiError(status)), false);
  }
  for (const status of [400, 401, 403, 404, 409, 422, 429]) {
    assert.equal(policy.retryTransientApiQuery(0, new ApiError(status)), false);
  }
  const aborted = new Error("Synthetic abort");
  aborted.name = "AbortError";
  assert.equal(policy.retryTransientApiQuery(0, aborted), false);
  assert.equal(policy.transientApiRetryDelay(100), 10_000);
  const providers = read("src/app/providers.tsx");
  assert.match(providers, /queries: \{\s*retry: retryTransientApiQuery,\s*retryDelay: transientApiRetryDelay/);
  assert.doesNotMatch(providers, /mutations:\s*\{\s*retry:/);
});

test("run and brainstorm failures do not turn transient reads into permanent missing-resource claims", () => {
  const run = read("src/components/run/run-view.tsx");
  assert.match(run, /const notFound = runError instanceof ApiError && runError\.status === 404/);
  assert.match(run, /title=\{notFound \? "Run not found" : "Research could not be loaded"\}/);
  assert.match(run, /if \(runError \|\| !run\)/);
  assert.match(run, /retryTransientApiQuery\(0, runError\) \? \(\) => void refetchRun\(\)/);
  const brainstorm = read("src/app/(app)/brainstorming/page.tsx");
  const selectedQuery = brainstorm.slice(brainstorm.indexOf("const selected = useQuery({"), brainstorm.indexOf("const eventAfter ="));
  assert.match(selectedQuery, /retry: retryTransientApiQuery/);
  assert.match(selectedQuery, /\[401, 403, 404\]\.includes\(selected\.error\.status\)/);
  assert.match(brainstorm, /selected\.isError \|\| !session \|\| session\.purpose !== "brainstorm"/);
  assert.match(brainstorm, /This brainstorm could not be loaded right now/);
  assert.match(brainstorm, /void selected\.refetch\(\)/);
  const detail = read("src/components/detail-error.tsx");
  assert.match(detail, /userFacingErrorMessage\(error, fallback\)/);
  assert.match(detail, /disabled=\{retrying\} onClick=\{onRetry\}/);
});

test("snapshot timestamps retain explicit offsets and use UTC for legacy naive values", () => {
  const format = loadPureSource(read("src/lib/format.ts"));
  const equivalent = [
    "2026-09-04T10:30:00Z",
    "2026-09-04T10:30:00+00:00",
    "2026-09-04T12:30:00+02:00",
    "2026-09-04T12:30:00+0200",
    "2026-09-04T10:30:00",
  ];
  for (const iso of equivalent) {
    assert.equal(format.parseApiDate(iso).toISOString(), "2026-09-04T10:30:00.000Z");
    assert.equal(format.formatDateTime(iso), format.formatDateTime(equivalent[0]));
    assert.doesNotMatch(format.formatDateTime(iso), /Invalid Date/);
  }
  assert.equal(format.formatDateTime("not-a-date"), "Date unavailable");
  const writer = read("src/app/(app)/writer/[id]/page.tsx");
  const history = writer.slice(writer.indexOf("function SnapshotList("), writer.indexOf("function SnapshotList(") + 7000);
  assert.match(history, /formatDateTime\(snapshot\.created_at\)/);
  assert.doesNotMatch(history, /snapshot\.created_at\.endsWith\("Z"\)/);
  assert.ok(history.indexOf("if (snapshotsError)") < history.indexOf("if ((snapshots ?? []).length === 0)"));
});

test("only the known participant-information conflict receives targeted guidance", () => {
  const info = loadPureSource(read("src/lib/participant-information-feedback.ts"));
  assert.equal(info.isParticipantInformationRequired("Complete and review the study's participant information before accepting responses."), true);
  assert.equal(info.isParticipantInformationRequired({ code: "participant_information_required" }), true);
  for (const detail of [null, "A newer revision exists", { code: "revision_conflict" }, 409]) {
    assert.equal(info.isParticipantInformationRequired(detail), false);
  }
  const labels = info.participantInformationGapLabels([
    "controller name", "reachable study contact email",
    "researcher review of the legal basis and participant information",
    "Synthetic internal traceback", "Synthetic internal traceback",
  ]);
  assert.equal(labels.length, 4);
  assert.ok(labels.includes("Responsible institution / controller"));
  assert.ok(labels.includes("Your explicit review confirmation after completing the fields"));
  assert.doesNotMatch(labels.join(" "), /traceback/);
  assert.equal(info.participantInformationGapLabels(["__proto__", "constructor"])[0], "Review the participant information fields");
});

test("incomplete survey publication opens review without changing status or confirming on behalf of the user", () => {
  const source = read("src/app/(app)/surveys/[id]/page.tsx");
  const request = source.slice(source.indexOf("function requestPublication()"), source.indexOf("const passwordUpdate = useMutation({"));
  const compiled = ts.transpileModule(request, {
    compilerOptions: { target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const makeRequest = new Function("draft", "statusUpdate", "surveyQuestionsError", "setPublicationError", "setPublicationNeedsInfo", "setView", "setMobilePane", "showParticipantInformation", "SURVEY_PARTICIPANT_INFORMATION_REQUIRED", `${compiled}; return requestPublication;`);
  for (const ready of [false, undefined, true]) {
    const calls = [];
    const capture = (name) => (value) => calls.push([name, value]);
    makeRequest(
      { questions: [], participant_information_ready: ready },
      { isPending: false, mutate: capture("mutate") }, () => null,
      capture("error"), capture("needsInfo"), capture("view"), capture("pane"),
      capture("review"), "Review and save participant information",
    )();
    assert.equal(calls.some(([name]) => name === "mutate"), ready === true);
    assert.equal(calls.some(([name]) => name === "review"), ready !== true);
  }
  assert.match(source, /publicationError && \([\s\S]*?role="alert"/);
  assert.match(source, /isParticipantInformationRequired\(error\.detail\)/);
  assert.match(source, /gaps=\{draft\.participant_information_gaps \?\? \[\]\}/);
  assert.match(source, /refetchInterval: view === "responses" \? 8_000 : false/);
  assert.doesNotMatch(request, /researcher_reviewed:\s*true/);
  const notice = read("src/components/participant-information.tsx");
  assert.match(notice, /participantInformationGapLabels\(gaps\)/);
  assert.match(notice, /researcher_reviewed: false/);
});
