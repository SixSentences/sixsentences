import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const queueSource = readFileSync(
  join(process.cwd(), "src/components/agent/agent-turn-queue.tsx"),
  "utf8",
);
const workStatusSource = readFileSync(
  join(process.cwd(), "src/components/agent-work-status.tsx"),
  "utf8",
);
const lifecycleSource = readFileSync(
  join(process.cwd(), "src/lib/agent-event-lifecycle.ts"),
  "utf8",
);
const toolCallSource = readFileSync(
  join(process.cwd(), "src/components/agent/tool-call-card.tsx"),
  "utf8",
);
const quickChatSource = readFileSync(
  join(process.cwd(), "src/components/run/chat-panel.tsx"),
  "utf8",
);
const apiSource = readFileSync(
  join(process.cwd(), "src/lib/api.ts"),
  "utf8",
);
const typeSource = readFileSync(
  join(process.cwd(), "src/lib/types.ts"),
  "utf8",
);
const recoveryHookSource = readFileSync(
  join(process.cwd(), "src/hooks/use-durable-specialist-turn.ts"),
  "utf8",
);
const appShellSource = readFileSync(
  join(process.cwd(), "src/components/shell/app-shell.tsx"),
  "utf8",
);
const specialistPages = [
  "src/app/(app)/surveys/[id]/page.tsx",
  "src/app/(app)/data/[id]/page.tsx",
  "src/app/(app)/interviews/[id]/page.tsx",
  "src/app/(app)/interviews/studies/[id]/page.tsx",
  "src/app/(app)/writer/[id]/page.tsx",
].map((path) => [path, readFileSync(join(process.cwd(), path), "utf8")]);

function hookOwner(source, hookIndex) {
  const functionStarts = [
    ...source.matchAll(/^(?:export default )?function [A-Za-z0-9_]+\(/gm),
  ].map((match) => match.index);
  const ownerStart = functionStarts.filter((index) => index <= hookIndex).at(-1);
  assert.notEqual(ownerStart, undefined, "durable-turn hook must belong to a named component");
  const ownerEnd = functionStarts.find((index) => index > hookIndex) ?? source.length;
  return source.slice(ownerStart, ownerEnd);
}

test("specialist agents preserve FIFO order and dispatch the next turn after completion", () => {
  assert.match(queueSource, /const \[next, \.\.\.rest\] = current/);
  assert.match(queueSource, /queueMicrotask\(\(\) => \{[\s\S]*?runRef\.current\(next\.payload\)/);
  assert.match(queueSource, /generationRef\.current === generation/);
  assert.match(queueSource, /\.\.\.current,[\s\S]*?crypto\.randomUUID\(\)/);
});

test("specialist agents expose queued prompts and let the user remove them", () => {
  assert.match(queueSource, /aria-label="Queued assistant messages"/);
  assert.match(queueSource, /items\.map\(\(item, index\)/);
  assert.match(queueSource, /onClick=\{\(\) => onRemove\(item\.id\)\}/);
  assert.match(queueSource, /index === 0 \? "Next"/);
});

test("every specialist composer uses the same non-blocking queue", () => {
  for (const [path, source] of specialistPages) {
    assert.match(source, /useAgentTurnQueue/, `${path} does not use the queue hook`);
    assert.match(source, /<AgentTurnQueue/, `${path} does not show queued prompts`);
    assert.match(source, /queuedChat\.submit/, `${path} does not submit through the queue`);
  }
});

test("all specialist pages inherit the same safe user-facing agent timeline", () => {
  for (const [path, source] of specialistPages) {
    assert.match(source, /<AgentActivityTimeline/, `${path} bypasses the shared timeline`);
  }
  assert.match(workStatusSource, /safeAgentProgressText\(event\.label\)/);
  assert.doesNotMatch(workStatusSource, /return "Reused an earlier tool result"/);
  assert.doesNotMatch(workStatusSource, /return "Compacted the working context"/);
});

test("checkpoint misses use a calm review state while real failures stay destructive", () => {
  assert.match(workStatusSource, /if \(isRecoverableReview\(event\)\) return "review"/);
  assert.match(
    workStatusSource,
    /isRecoverableReview\(event\)[\s\S]*?"Prüfung"[\s\S]*?"Review"/,
  );
  assert.match(toolCallSource, /\| "review"/);
  assert.match(toolCallSource, /state === "failed"[\s\S]*?text-destructive/);
  assert.match(
    toolCallSource,
    /state === "review"[\s\S]*?border-sky-500\/25[\s\S]*?text-sky-700/,
  );
});

test("confirmed survey writes synchronize the draft while other resources reconcile terminally", () => {
  const survey = specialistPages.find(([path]) => path.includes("surveys/"))[1];
  const data = specialistPages.find(([path]) => path.includes("data/"))[1];
  const interview = specialistPages.find(([path]) => /interviews\/\[id\]/.test(path))[1];
  const study = specialistPages.find(([path]) => path.includes("interviews/studies/"))[1];

  assert.match(survey, /handleSurveyAgentEvent/);
  assert.match(survey, /event\.event !== "change\.completed" \|\| event\.applied !== true/);
  assert.match(survey, /setDraft\(\(current\)[\s\S]*?projectConfirmedSurveyChange/);
  assert.match(survey, /setQueryData<Survey>[\s\S]*?projectConfirmedSurveyChange/);
  assert.equal((survey.match(/handleSurveyAgentEvent/g) ?? []).length >= 3, true);
  assert.match(survey, /onError:[\s\S]*?refetchQueries\(\{ queryKey: \["survey", surveyId\] \}\)/);

  assert.doesNotMatch(data, /handleDatasetAgentEvent/);
  assert.doesNotMatch(interview, /handleInterviewAgentEvent/);
  assert.doesNotMatch(study, /handleStudyAgentEvent/);
  assert.match(data, /onTerminal:[\s\S]*?queryKey: \["dataset", datasetId\]/);
  assert.match(interview, /onTerminal:[\s\S]*?queryKey: \["interview", interviewId\]/);
  assert.match(study, /onTerminal:[\s\S]*?queryKey: \["voice-study", studyId\]/);
});

test("writer keeps a newly drafted follow-up when the active request fails", () => {
  const writer = specialistPages.find(([path]) => path.includes("writer/"))[1];
  assert.match(writer, /setDraft\(\(current\) => current\.trim\(\) \? current : turn\.text\)/);
  assert.match(
    writer,
    /onMutate: \(turn\) => \{[\s\S]*?setPending\(turn\);\s*},\s*onSuccess/,
  );
  assert.match(writer, /disabled=\{!draft\.trim\(\) \|\| agentBusy\}/);
});

test("writer renders saved edit proposals immediately without invented reveal progress", () => {
  const writer = specialistPages.find(([path]) => path.includes("writer/"))[1];
  assert.match(writer, /\(message\.payload\.edits \?\? \[\]\)\.map\(\(edit, index\) =>/);
  assert.doesNotMatch(writer, /revealedEditCounts/);
  assert.doesNotMatch(writer, /Preparing the next proposed change/);
  assert.doesNotMatch(writer, /}, 420\)/);
  assert.doesNotMatch(writer, /<AgentContextReceipt kind="manuscript"/);
  assert.doesNotMatch(workStatusSource, /Preparing change/);
  assert.doesNotMatch(workStatusSource, /}, 380\)/);
  assert.match(workStatusSource, /changes\.map\(\(change, index\) =>/);
});

test("specialist SSE distinguishes cancellation, terminal failure, and transport loss", () => {
  assert.match(apiSource, /export class SpecialistStreamError extends ApiError/);
  assert.match(apiSource, /kind: SpecialistStreamFailureKind/);
  assert.match(apiSource, /accepted: boolean/);
  assert.match(apiSource, /export interface SpecialistStreamOptions/);
  assert.match(apiSource, /signal\?: AbortSignal/);
  assert.match(apiSource, /body: JSON\.stringify\(requestBody\),\s*signal,/);
  assert.match(
    apiSource,
    /if \(signal\?\.aborted\) throw cancellationError\(\)/,
  );
  assert.match(
    apiSource,
    /new SpecialistStreamError\([\s\S]*?"transport",[\s\S]*?accepted/,
  );
  assert.match(
    apiSource,
    /if \(failure\) \{\s*throw new SpecialistStreamError\("terminal", true, failure\)/,
  );
  assert.match(
    apiSource,
    /const terminalResult = \(\): T \| null => \{[\s\S]*?if \(cancelled\)[\s\S]*?if \(failure\)/,
  );
  assert.match(apiSource, /event\.event === "turn\.cancelled"/);
  assert.match(
    apiSource,
    /event\.event === "turn\.completed" && event\.result !== undefined/,
  );
  assert.match(typeSource, /\| "turn\.cancelled"/);
  assert.match(workStatusSource, /event\.event === "turn\.cancelled"\) return "cancelled"/);
  assert.match(workStatusSource, /event\.event === "turn\.cancelled"\)[\s\S]*?"Stopped"/);
  assert.match(workStatusSource, /eventStatusLabel\(currentEvent, language\)/);
});

test("specialist reconnects use one stable turn id and the durable SSE cursor", () => {
  assert.equal(
    apiSource.match(/options: SpecialistStreamOptions = \{\}/g)?.length,
    6,
    "the shared stream and all five specialist APIs must accept stable turn options",
  );
  assert.match(apiSource, /export function createSpecialistTurnId\(\)/);
  assert.match(apiSource, /const turnId = options\.turnId \?\? createSpecialistTurnId\(\)/);
  assert.match(apiSource, /const requestBody = \{ \.\.\.body, turn_id: turnId \}/);
  assert.match(apiSource, /"Last-Event-ID": String\(lastEventId\)/);
  assert.match(apiSource, /if \(eventId > 0\) lastEventId = eventId/);
  assert.match(apiSource, /options\.onAccepted\?\.\(turnId\)/);
  assert.match(
    apiSource,
    /const followAcceptedTurn = async \(\): Promise<T>[\s\S]*?streamSpecialistTurnEvents<T>[\s\S]*?lastEventId/,
  );
  assert.match(
    apiSource,
    /if \(!response\.body\) return followAcceptedTurn\(\)/,
  );
  assert.match(apiSource, /return followAcceptedTurn\(\);\s*}\s*if \(buffer\.trim\(\)\)/);
  const acceptedPost = apiSource.slice(
    apiSource.indexOf("async function streamSpecialistRequest"),
    apiSource.indexOf("async function streamSpecialistTurnEvents"),
  );
  assert.equal(
    acceptedPost.match(/method: "POST"/g)?.length,
    1,
    "an accepted specialist POST must never be resubmitted",
  );
  assert.doesNotMatch(acceptedPost, /maxReconnectAttempts|reconnectAttempts/);
  assert.match(apiSource, /agentTurnStatus: \(turnId: string\)/);
  assert.match(apiSource, /agentTurnStop: \(turnId: string\)/);
  assert.match(typeSource, /export type AgentTurnStatus =/);
  assert.match(typeSource, /export interface AgentTurnState/);
  assert.match(typeSource, /export interface AgentTurnStopResult/);
});

test("writer requests cooperative stop and keeps SSE open for cancellation", () => {
  const writer = specialistPages.find(([path]) => path.includes("writer/"))[1];
  assert.match(writer, /new AbortController\(\)/);
  assert.match(writer, /const turnId = createSpecialistTurnId\(\)/);
  assert.match(writer, /turnId,\s*signal: controller\.signal,\s*onAccepted:/);
  assert.match(writer, /turnAcceptedRef\.current = true/);
  assert.match(writer, /await api\.agentTurnStop\(turnId\)/);
  assert.match(writer, /event\.event === "turn\.cancelled"/);
  assert.match(writer, /window\.setTimeout\(\(\) => \{[\s\S]*?controller\.abort\(\)/);
  assert.match(writer, /}, 12_000\)/);
  assert.match(writer, /aria-label="Stop agent turn"/);
  assert.match(writer, /disabled=\{!turnAccepted \|\| stopping\}/);
  assert.match(writer, /error instanceof SpecialistStreamError/);
  assert.match(
    writer,
    /!streamError\.accepted \|\| streamError\.kind === "terminal"/,
  );
  assert.doesNotMatch(writer, /server may still finish|may still finish on the server/i);
});

test("long specialist turns only show server-backed work instead of invented phases", () => {
  assert.match(workStatusSource, /AgentActivityTimeline/);
  assert.match(workStatusSource, /events\.filter/);
  assert.match(workStatusSource, /Thinking…/);
  assert.doesNotMatch(workStatusSource, /setElapsedSeconds/);
  assert.doesNotMatch(workStatusSource, /activity\.phases\.slice/);
  assert.doesNotMatch(workStatusSource, /window\.setTimeout\(\(\) => setPhase/);
  assert.doesNotMatch(workStatusSource, /You can write the next message/);
  assert.doesNotMatch(workStatusSource, /Nächste Nachricht schon möglich/);
});

test("specialist progress uses the same 40px tool-call shell as Quick Answer", () => {
  const liveTimeline = workStatusSource.slice(
    workStatusSource.indexOf("export function AgentActivityTimeline"),
    workStatusSource.indexOf("export function AgentWorkDisclosure"),
  );
  assert.match(liveTimeline, /stabilizeAgentEvents/);
  assert.match(liveTimeline, /running\?: boolean/);
  assert.match(liveTimeline, /"agent\.update"/);
  assert.match(workStatusSource, /event\.event === "agent\.update"/);
  assert.match(toolCallSource, /aria-expanded=\{hasDetails \? open : undefined\}/);
  assert.match(liveTimeline, /data-agent-timeline/);
  assert.match(liveTimeline, /data-agent-event-card/);
  assert.match(liveTimeline, /data-agent-event-message/);
  assert.match(liveTimeline, /<AgentToolCallCard/);
  assert.match(toolCallSource, /data-agent-tool-node/);
  assert.match(toolCallSource, /grid size-10 shrink-0 place-items-center rounded-full/);
  assert.match(toolCallSource, /data-agent-timeline-rail/);
  assert.match(toolCallSource, /data-agent-tool-progress/);
  assert.match(toolCallSource, /rounded-full border border-border bg-secondary\/60/);
  assert.match(toolCallSource, /<ChevronDown/);
  assert.match(quickChatSource, /<AgentToolCallCard/);
  assert.doesNotMatch(liveTimeline, /Agent work/);
  assert.doesNotMatch(liveTimeline, /steps complete/);
  assert.doesNotMatch(liveTimeline, /divide-y divide-border/);
  assert.match(liveTimeline, /event\.frames/);
  assert.doesNotMatch(liveTimeline, /setExpanded\(new Set\(\)\)/);
  assert.match(liveTimeline, /\["Before", safeAgentDisplayValue\(firstBefore\)\]/);
  assert.match(liveTimeline, /\["After", safeAgentDisplayValue\(latestAfter\)\]/);
  assert.match(liveTimeline, /<ReadableValue value=\{value\}/);
  assert.doesNotMatch(liveTimeline, /<pre/);
  assert.doesNotMatch(liveTimeline, /JSON\.stringify/);
  assert.doesNotMatch(liveTimeline, /private scratchpad|chain-of-thought/i);
});

test("agent timeline rails stay centered and continuous across quiet rows", () => {
  const liveTimeline = workStatusSource.slice(
    workStatusSource.indexOf("function AgentTimelineConnector"),
    workStatusSource.indexOf("export function AgentWorkDisclosure"),
  );
  const thinkingIndicator = workStatusSource.slice(
    workStatusSource.indexOf("export function AgentThinkingIndicator"),
    workStatusSource.indexOf("/** Append an immutable SSE event"),
  );

  // A 1px stroke starts half a pixel before 1.25rem, putting its visual centre
  // exactly on the responsive centreline shared by every size-10 node.
  assert.match(toolCallSource, /left-\[calc\(1\.25rem-0\.5px\)\]/);
  assert.match(toolCallSource, /start === "node"[\s\S]*?"top-5"/);
  assert.match(
    toolCallSource,
    /end === "node"[\s\S]*?"bottom-\[calc\(100%-1\.25rem\)\]"/,
  );

  // Specialist rows retain one rail segment for every interleaved narrative
  // or observation row between the first and last semantic nodes.
  assert.match(liveTimeline, /index < firstNodeIndex/);
  assert.match(liveTimeline, /index > lastNodeIndex/);
  assert.match(liveTimeline, /start=\{index === firstNodeIndex \? "node" : "gap"\}/);
  assert.match(liveTimeline, /end=\{index === lastNodeIndex \? "node" : "gap"\}/);
  assert.match(liveTimeline, /semanticNodeRows\.push\(true\)/);
  assert.match(thinkingIndicator, /<AgentLoadingOrb className="size-10" \/>/);

  // Quick Answer consumes the same geometry, using its 20px row gap, and no
  // longer owns a second hand-positioned connector implementation.
  assert.match(quickChatSource, /<AgentTimelineRail/);
  assert.match(quickChatSource, /spacing="quick"/);
  assert.match(quickChatSource, /start=\{previousIsTool \? "gap" : "node"\}/);
  assert.match(quickChatSource, /data-quick-agent-history-row/);
  assert.match(quickChatSource, /start=\{toolIndex === 0 \? "node" : "gap"\}/);
  assert.match(
    quickChatSource,
    /end=\{toolIndex === turn\.tools\.length - 1 \? "node" : "gap"\}/,
  );
  assert.doesNotMatch(quickChatSource, /left-\[1\.21875rem\]/);
  assert.doesNotMatch(toolCallSource, /data-agent-tool-rail/);
});

test("specialist timelines keep visible wording immutable across SSE replay and tool updates", () => {
  assert.match(
    workStatusSource,
    /const alreadyVisible = current\.some\([\s\S]*?return alreadyVisible \? current : \[\.\.\.current, event\]/,
  );
  assert.doesNotMatch(workStatusSource, /next\[existing\] = event/);
  assert.match(typeSource, /call_id\?: string/);
  assert.match(typeSource, /lifecycle\?: "started" \| "progress" \| "completed" \| "failed"/);
  assert.match(workStatusSource, /export function stabilizeAgentEvents/);
  assert.match(workStatusSource, /return stabilizeAgentEventLedger\(events\)/);
  assert.match(lifecycleSource, /if \(seen\.has\(event\.id\)\) continue/);
  assert.match(lifecycleSource, /timeline\.push\(\{ \.\.\.event, frames: \[event\] \}\)/);
  const stabilizer = lifecycleSource;
  assert.match(stabilizer, /const lifecycleIndexes = new Map<string, number>\(\)/);
  assert.match(stabilizer, /isGroupedLifecycleEvent\(event\) && event\.call_id/);
  assert.match(stabilizer, /frames: \[\.\.\.existing\.frames, event\]/);
  assert.match(workStatusSource, /title=\{eventTitle\(event\)\}/);
  assert.match(workStatusSource, /const currentEvent = event\.frames\.at\(-1\) \?\? event/);
  assert.match(workStatusSource, /state=\{state\}/);
  assert.doesNotMatch(stabilizer, /label: event\.label|detail: event\.detail|event: event\.event/);
  assert.doesNotMatch(stabilizer, /\.\.\.existing,\s*\.\.\.event/);
});

test("one running agent owns the timeline while final prose owns the persisted avatar", () => {
  const liveTimeline = workStatusSource.slice(
    workStatusSource.indexOf("export function AgentActivityTimeline"),
    workStatusSource.indexOf("export default function AgentWorkStatus"),
  );
  assert.match(liveTimeline, /const activeToolCall = visibleEvents\.some/);
  assert.match(liveTimeline, /const showThinking = shouldShowAgentThinking\(events, running, activeToolCall\)/);
  assert.match(workStatusSource, /data-agent-thinking/);
  assert.match(liveTimeline, /\{showThinking \?[\s\S]*?<AgentThinkingIndicator/);
  assert.match(liveTimeline, /eventState\(current\) === "progress"/);
  assert.doesNotMatch(liveTimeline, /<AgentResponseAvatar/);
  assert.match(workStatusSource, /data-agent-final-message[\s\S]*?<AgentResponseAvatar/);
  assert.match(liveTimeline, /data-agent-event-message/);
  assert.doesNotMatch(
    liveTimeline.slice(
      liveTimeline.indexOf("if (isNarrative)"),
      liveTimeline.indexOf("return (", liveTimeline.indexOf("if (isNarrative)") + 40),
    ),
    /AgentResponseAvatar|AgentLoadingOrb/,
  );
});

test("thinking is plain shimmer text and tool rows do not impersonate extra agents", () => {
  const thinking = workStatusSource.slice(
    workStatusSource.indexOf("function AgentThinkingBubble"),
    workStatusSource.indexOf("export function appendAgentEvent"),
  );
  assert.match(thinking, /data-agent-thinking/);
  assert.match(thinking, /shimmer-text/);
  assert.doesNotMatch(thinking, /rounded|border|background|bg-|px-|py-/);

  const liveTimeline = workStatusSource.slice(
    workStatusSource.indexOf("export function AgentActivityTimeline"),
    workStatusSource.indexOf("export function AgentWorkDisclosure"),
  );
  assert.doesNotMatch(liveTimeline, /<AgentResponseAvatar/);
  assert.match(liveTimeline, /<AgentToolCallCard/);
  assert.match(liveTimeline, /data-agent-event-observation/);
});

test("final report projections do not repeat the assistant answer in the activity ledger", () => {
  const liveTimeline = workStatusSource.slice(
    workStatusSource.indexOf("export function AgentActivityTimeline"),
    workStatusSource.indexOf("export default function AgentWorkStatus"),
  );
  assert.match(
    workStatusSource,
    /event\.event === "agent\.update" && event\.tool\?\.endsWith\("\.report_results"\) === true/,
  );
  assert.match(workStatusSource, /!isFinalReportProjection\(event\)/);
  assert.match(liveTimeline, /const isNarrative =[\s\S]*?event\.event === "agent\.update"/);
});

test("maintenance observations use natural copy while internal limits stay hidden", () => {
  assert.match(workStatusSource, /EXPECTED_DUPLICATE_TEXT/);
  assert.match(workStatusSource, /EXPECTED_EOF_TEXT/);
  assert.match(workStatusSource, /event\.tool\?\.endsWith\("\.agent_loop"\)/);
  assert.match(workStatusSource, /return "Reviewed the available results"/);
  assert.match(workStatusSource, /return "Finished reviewing the source"/);
  assert.match(workStatusSource, /return "Reviewed the current work"/);
  assert.doesNotMatch(workStatusSource, /return "Reused an earlier tool result"/);
  assert.doesNotMatch(workStatusSource, /return "Compacted the working context"/);
  assert.match(workStatusSource, /if \(expectedObservation\(event\)\) return "neutral"/);
  assert.doesNotMatch(
    workStatusSource,
    /if \(event\.event !== "tool\.failed"\) return null/,
  );
  assert.match(workStatusSource, /userFacingStoredErrorMessage\(\s*event\.message/);
  assert.match(workStatusSource, /event\.tool\?\.endsWith\("\.agent_loop"\)[\s\S]*?"Agent review"/);
  for (const key of ["iteration", "iterations", "max_iterations", "tool_calls"]) {
    assert.ok(workStatusSource.includes(`"${key}"`), `${key} is not hidden`);
  }
});

test("live specialist ledgers remain visible until the same durable turn is in history", () => {
  assert.match(workStatusSource, /export function useAgentTimelineLedger/);
  assert.match(workStatusSource, /export function agentTimelineIsPersisted/);
  assert.match(workStatusSource, /event\.turn_id === handoff\.turnId/);
  assert.match(workStatusSource, /expectedIds\.every\(\(id\) => persistedIds\.has\(id\)\)/);
  assert.match(workStatusSource, /if \(!handoff\.turnId\)/);
  assert.match(workStatusSource, /export function AgentTimelineHandoffs/);
  for (const [path, source] of specialistPages) {
    assert.match(source, /useAgentTimelineLedger/, `${path} does not retain the live ledger`);
    assert.match(source, /handoffAgentTurn\(\)/, `${path} drops events when settling`);
    assert.match(source, /<AgentTimelineHandoffs/, `${path} lacks the history handoff overlay`);
    assert.doesNotMatch(source, /setAgentEvents\(\[\]\)/, `${path} clears visible events directly`);
  }
});

test("plan revisions share one Quick-style card without mutating earlier text", () => {
  const liveTimeline = workStatusSource.slice(
    workStatusSource.indexOf("export function AgentActivityTimeline"),
    workStatusSource.indexOf("export default function AgentWorkStatus"),
  );
  const stabilizer = lifecycleSource;
  assert.match(stabilizer, /const planIndexes = new Map<string, number>\(\)/);
  assert.match(stabilizer, /const planKey = event\.turn_id\?\.trim\(\) \|\| "__legacy_plan__"/);
  assert.match(stabilizer, /if \(isPlanEvent\(event\)\)/);
  assert.match(stabilizer, /frames: \[\.\.\.existing\.frames, event\]/);
  assert.doesNotMatch(stabilizer, /\.\.\.existing,\s*\.\.\.event/);
  assert.match(liveTimeline, /data-agent-event-plan/);
  assert.match(liveTimeline, /<AgentToolCallCard[\s\S]*?icon=\{ListChecks\}/);
  assert.match(liveTimeline, /title=\{eventTitle\(event\)\}/);
  assert.match(liveTimeline, /data-agent-plan-frames/);
  assert.match(liveTimeline, /event\.frames\.map\(\(frame\) =>/);
  assert.match(liveTimeline, /\[frame\.detail, frame\.message\]/);
  assert.match(liveTimeline, /\(frame\.steps \?\? \[\]\)/);
  assert.match(liveTimeline, /currentEvent\.event === "plan\.updated"/);
  assert.doesNotMatch(liveTimeline, /currentPlan|replacePlan|mergePlan/);
});

test("every live specialist timeline stays active until its turn completes", () => {
  for (const [path, source] of specialistPages) {
    assert.match(
      source,
      /<AgentActivityTimeline[\s\S]{0,300}?running/,
      `${path} does not mark its live server timeline as running`,
    );
  }
});

test("persisted specialist timelines replace duplicate legacy change summaries", () => {
  for (const [path, source] of specialistPages) {
    assert.match(
      source,
      /message\.payload\.agent_events/,
      `${path} does not render the persisted event ledger`,
    );
  }

  for (const path of [
    "src/app/(app)/surveys/[id]/page.tsx",
    "src/app/(app)/interviews/studies/[id]/page.tsx",
  ]) {
    const source = specialistPages.find(([candidate]) => candidate === path)[1];
    assert.match(source, /<AgentProposalControls/);
    assert.match(source, /agent_events\?\.length \?\? 0\) === 0/);
  }
});

test("specialist timelines render compact plans and final prose as a real answer", () => {
  const liveTimeline = workStatusSource.slice(
    workStatusSource.indexOf("export function AgentActivityTimeline"),
    workStatusSource.indexOf("export default function AgentWorkStatus"),
  );
  assert.match(liveTimeline, /data-agent-event-plan/);
  assert.match(typeSource, /\| "plan\.updated"/);
  assert.match(liveTimeline, /currentEvent\.event === "plan\.updated"/);
  assert.match(workStatusSource, /event\.event !== "answer\.completed"/);
  assert.match(workStatusSource, /data-agent-final-message/);
  assert.match(workStatusSource, /break-words/);
  assert.match(liveTimeline, /max-w-full[^"]*overflow-hidden/);

  assert.match(workStatusSource, /export function SpecialistCompletedTurn/);
  assert.match(workStatusSource, /<AgentFinalResponse>\{answer\}<\/AgentFinalResponse>/);
  for (const [path, source] of specialistPages) {
    assert.match(source, /<SpecialistCompletedTurn/, `${path} bypasses the shared final turn layout`);
  }
});

test("completed specialist work collapses while failures stay open and outputs stay outside", () => {
  assert.match(workStatusSource, /export function AgentWorkDisclosure/);
  assert.match(workStatusSource, /useState\(defaultOpen \|\| failed\)/);
  assert.match(workStatusSource, /data-agent-work-disclosure/);
  assert.match(workStatusSource, /Agent work/);
  assert.match(workStatusSource, /data-agent-work-node/);
  assert.match(workStatusSource, /grid size-10 shrink-0 place-items-center rounded-full/);
  assert.match(workStatusSource, /language === "de"[\s\S]*?"Prüfen"[\s\S]*?"Needs attention"/);
  assert.match(workStatusSource, /language === "de"[\s\S]*?"Erledigt"[\s\S]*?"Done"/);
  assert.match(workStatusSource, /const workLabel = language === "de" \? "Agent-Arbeit" : "Agent work"/);
  assert.match(workStatusSource, /<AgentArtifactLinks artifacts=\{artifacts\} \/>/);
  assert.match(workStatusSource, /safeAgentArtifactHref\(artifact\.href\)/);
  assert.match(workStatusSource, /artifact\.href\.startsWith\("https:\/\/"\)/);
  assert.match(typeSource, /export interface SpecialistArtifact/);
});

test("specialist workspaces recover owner-scoped durable turns before accepting another send", () => {
  assert.match(apiSource, /agentTurnActive: \(resourceKind: string, resourceId: string\)/);
  assert.match(apiSource, /agentTurnLatest: \(resourceKind: string, resourceId: string\)/);
  assert.match(apiSource, /agentTurnEventsStream: <T>/);
  assert.match(apiSource, /\/events\/stream/);
  assert.match(recoveryHookSource, /api\.agentTurnActive/);
  assert.match(recoveryHookSource, /api\.agentTurnLatest/);
  assert.match(recoveryHookSource, /api\.agentTurnEventsStream/);
  assert.match(recoveryHookSource, /status: terminal\.status/);
  assert.doesNotMatch(recoveryHookSource, /localStorage|sessionStorage/);
  for (const [path, source] of specialistPages) {
    const hookIndex = source.indexOf("useDurableSpecialistTurn<");
    assert.ok(hookIndex >= 0, `${path} does not recover durable turns`);
    const owner = hookOwner(source, hookIndex);
    const ownerHookIndex = owner.indexOf("useDurableSpecialistTurn<");
    const firstReturnIndex = owner.search(/if \((?:loadError|isLoading)/);
    if (firstReturnIndex >= 0) {
      assert.ok(ownerHookIndex < firstReturnIndex, `${path} changes hook order after loading`);
    }
    assert.match(source, /checkingAgentTurn/);
    assert.match(source, /recoveringAgentTurn/);
  }
});

test("bodyless recovery retries transient discovery and stream outages until terminal", () => {
  assert.match(recoveryHookSource, /while \(mounted && !turn\)/);
  assert.match(recoveryHookSource, /error\.status === 408/);
  assert.match(recoveryHookSource, /error\.status === 429/);
  assert.match(recoveryHookSource, /error\.status >= 500/);
  const replay = apiSource.slice(
    apiSource.indexOf("async function streamSpecialistTurnEvents"),
    apiSource.indexOf("export const api"),
  );
  assert.doesNotMatch(replay, /reconnectAttempts < 3/);
  assert.match(replay, /reconnectAttempts \+= 1/);
  assert.match(replay, /reconnectAttempts = 0/);
  assert.match(replay, /waitForSpecialistReconnect\(reconnectAttempts, signal\)/);
  assert.match(replay, /initialLastEventId = 0/);
  assert.match(replay, /let lastEventId = initialLastEventId/);
});

test("all non-writer specialists keep Stop wired to the durable turn and refetch receipts", () => {
  const nonWriterPages = specialistPages.filter(([path]) => !path.includes("writer/"));
  const receiptKeyByPath = new Map([
    ["src/app/(app)/data/[id]/page.tsx", "dataset-chat"],
    ["src/app/(app)/interviews/[id]/page.tsx", "interview-chat"],
    ["src/app/(app)/interviews/studies/[id]/page.tsx", "voice-study-chat"],
    ["src/app/(app)/surveys/[id]/page.tsx", "survey-chat"],
  ]);
  for (const [path, source] of nonWriterPages) {
    assert.match(source, /useSpecialistTurnControl\(\)/, `${path} has no durable Stop control`);
    assert.match(source, /createSpecialistTurnId\(\)/, `${path} does not own a stable turn id`);
    assert.match(source, /beginLocalTurn\(turnId\)/, `${path} does not follow its accepted turn`);
    assert.match(source, /recoverTurn\(turn\.turn_id\)/, `${path} cannot stop a recovered turn`);
    assert.match(source, /await stopTurn\(\)/, `${path} does not call the stop endpoint`);
    assert.match(
      source,
      /aria-label=\{activeTurnId \? (?:"Stop agent turn"|t\("Agentenlauf stoppen", "Stop agent turn"\)) : (?:"Send"|t\("Senden", "Send"\))\}/,
    );
    assert.match(source, /onError: async \(error\)/);
    const terminalHandler = source.slice(
      source.indexOf("onError: async (error)"),
      source.indexOf("onError: async (error)") + 900,
    );
    assert.match(
      terminalHandler,
      new RegExp(`queryKey: \\["${receiptKeyByPath.get(path)}"`),
      `${path} does not refetch its terminal assistant receipt`,
    );
    assert.match(source, /SpecialistStreamError && error\.kind === "cancelled"/);
  }
  assert.match(recoveryHookSource, /await api\.agentTurnStop\(turnId\)/);
  assert.match(recoveryHookSource, /stopRequestedTurnIdRef\.current === turnId/);
  assert.match(
    recoveryHookSource,
    /Keep the composer locked until the durable stream publishes its[\s\S]*?terminal event/,
  );
});

test("newly enabled recovery blocks sends before the discovery effect runs", () => {
  assert.match(
    recoveryHookSource,
    /const discoveryKey = enabled && resourceId/,
  );
  assert.match(
    recoveryHookSource,
    /const checking = Boolean\(discoveryKey\) && settledDiscoveryKey !== discoveryKey/,
  );
  assert.doesNotMatch(recoveryHookSource, /useState\(enabled\)/);
});

test("writer recovered completion reuses idempotent auto-apply and remains stoppable", () => {
  const writer = specialistPages.find(([path]) => path.includes("writer/"))[1];
  assert.match(writer, /turn\.status === "completed" && !alreadyApplied/);
  assert.match(writer, /event\.event === "change\.completed" && event\.applied !== false/);
  assert.match(writer, /await applyAgentResult\(result\)/);
  assert.match(writer, /await api\.agentTurnStop\(turnId\)/);
  assert.doesNotMatch(writer, /if \(\s*!controller\s*\|\| !turnId/);
  assert.match(writer, /if \(controller\) \{/);
  assert.match(
    writer,
    /\(messages \?\? \[\]\)\.length === 0 &&[\s\S]{0,160}!streamActive &&[\s\S]{0,160}!recoveringAgentTurn &&[\s\S]{0,160}!checkingAgentTurn/,
  );
});

test("specialist turn duration stays outside agent and tool events", () => {
  assert.match(workStatusSource, /data-agent-turn-elapsed/);
  assert.match(workStatusSource, /window\.setInterval\(update, 1_000\)/);
  assert.doesNotMatch(
    workStatusSource.slice(
      workStatusSource.indexOf("export function AgentActivityTimeline"),
      workStatusSource.indexOf("export default function AgentWorkStatus"),
    ),
    /Still working|Elapsed \{/,
  );

  for (const [path, source] of specialistPages) {
    assert.match(
      source,
      /<AgentTurnElapsed[\s\S]{0,180}?<AgentActivityTimeline/,
      `${path} does not render the live duration above its timeline`,
    );
  }
});

test("quick answer duration stays above the activity instead of inside it", () => {
  const chatPanel = readFileSync(
    join(process.cwd(), "src/components/run/chat-panel.tsx"),
    "utf8",
  );
  assert.match(chatPanel, /data-chat-turn-elapsed/);
  assert.doesNotMatch(chatPanel, /Still working|Arbeite weiter/);
});

test("writer CodeMirror extensions come from one runtime instance", () => {
  const codeEditor = readFileSync(
    join(process.cwd(), "src/components/writer/code-editor.tsx"),
    "utf8",
  );
  assert.match(codeEditor, /Decoration,[\s\S]*EditorState,[\s\S]*EditorView,[\s\S]*WidgetType,[\s\S]*from "@uiw\/react-codemirror"/);
  assert.doesNotMatch(codeEditor, /from "@codemirror\/(state|view)"/);
  assert.doesNotMatch(codeEditor, /import \{[\s\S]*?autocompletion[\s\S]*?\} from "@codemirror\/autocomplete"/);
  assert.match(codeEditor, /EditorState\.languageData\.of/);
});

test("quick answer and specialist replies render fenced code as usable code blocks", () => {
  const chatPanel = readFileSync(
    join(process.cwd(), "src/components/run/chat-panel.tsx"),
    "utf8",
  );
  const codeAwareText = readFileSync(
    join(process.cwd(), "src/components/ai/code-aware-text.tsx"),
    "utf8",
  );

  assert.match(chatPanel, /<CodeAwareText/);
  assert.match(workStatusSource, /<CodeAwareText/);
  assert.match(codeAwareText, /```\(\[\^\\n`\]\*\)/);
  assert.match(codeAwareText, /navigator\.clipboard\.writeText\(code\)/);
  assert.match(codeAwareText, /data-code-block/);
  assert.match(codeAwareText, /overflow-auto/);
});
