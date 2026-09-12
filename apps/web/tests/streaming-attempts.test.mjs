import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const source = readFileSync(
  join(process.cwd(), "src/components/run/chat-panel.tsx"),
  "utf8",
);
const apiSource = readFileSync(
  join(process.cwd(), "src/lib/api.ts"),
  "utf8",
);

test("background answer streaming keeps only the latest worker attempt", () => {
  assert.match(source, /event\.event === "ask_answer_started"/);
  assert.match(source, /event\.payload\.attempt_id === latestAskAttemptId/);
  assert.match(source, /latestAskAttemptId === null/);
  assert.match(source, /event\.event === "ask_answer_aborted"/);
  assert.match(source, /!latestAskAttemptAborted/);
});

test("reasoning streams expose translated phases rather than raw scratchpads", () => {
  assert.match(source, /const phaseLabels:/);
  assert.match(source, /Analysis in progress/);
  assert.match(source, /Analyse läuft/);
  assert.match(source, /Never turn unknown provider scratchpad text into generic fake progress/);
  assert.match(source, /Only explicitly safe phase identifiers belong in this optional panel/);
  assert.doesNotMatch(source, />\s*\{reasoning\}\s*</);
});

test("an interrupted provider stream resets its partial draft before fallback", () => {
  assert.match(source, /event\.event === "answer\.reset"/);
  assert.match(source, /setStreamingText\(""\)/);
  assert.match(source, /setStreamingReasoning\(""\)/);
});

test("the selected model validates persisted conversation state against the private catalog", () => {
  assert.match(source, /initialModel = "auto"/);
  assert.match(source, /modelTouchedRef/);
  assert.match(source, /const \[requestedModel, setModelState\] = useState\(initialModel\)/);
  assert.match(source, /resolvePrivateModelId\(requestedModel, modelCatalog\)/);
  assert.match(source, /selectedModel: resolvePrivateModelId\(turn\.model, modelCatalog\)/);
  assert.match(source, /api\.chatStream\([\s\S]*?selectedModel,/);
});

test("follow-up turns are serialized and additional sends enter the queue", () => {
  assert.match(source, /const sendLockRef = useRef\(false\)/);
  assert.match(source, /if \(turnActive \|\| sendLockRef\.current\) \{\s*enqueueTurn/);
  assert.match(source, /if \(runInProgress \|\| ask\.isPending \|\| sendLockRef\.current\) return/);
  assert.match(source, /sendLockRef\.current = true/);
  assert.match(source, /onSettled:[\s\S]*?sendLockRef\.current = false/);
});

test("POST and reconnect apply the same cursor-checked event ledger", () => {
  assert.match(apiSource, /export class ChatStreamError extends ApiError/);
  assert.equal(
    apiSource.match(/consumeChatEventStream\(/g)?.length,
    3,
    "the shared parser must be declared once and used by POST plus GET replay",
  );
  assert.match(
    apiSource,
    /eventId !== sseEventId[\s\S]*?eventId <= lastEventId[\s\S]*?parsed\.turn_id !== turnId[\s\S]*?parsed\.event !== eventName/,
  );
  assert.match(apiSource, /"Last-Event-ID": String\(lastEventId\)/);
  assert.match(
    apiSource,
    /await followChatTurnRequest\([\s\S]*?consumed\.lastEventId/,
  );
  assert.match(source, /const applyChatStreamEvent = useCallback/);
  assert.match(source, /event\.id <= appliedId/);
  assert.match(source, /api\.followChatTurn\([\s\S]*?applyChatStreamEvent/);
  assert.match(source, /api\.chatStream\([\s\S]*?applyChatStreamEvent/);
  assert.match(source, /error instanceof ChatStreamError/);
  assert.match(source, /error\.kind === "terminal"/);
  assert.match(source, /state: "completed"/);
  assert.match(source, /if \(accepted\) return followAcceptedTurn\(\)/);
  assert.match(source, /while \(!signal\.aborted\)/);
  assert.match(source, /state: "not-accepted"/);
  assert.doesNotMatch(source, /state: "accepted-pending"/);
});

test("an accepted user turn is never submitted again automatically", () => {
  const acceptedPost = apiSource.slice(
    apiSource.indexOf("async function streamChatRequest"),
    apiSource.indexOf("function waitForSpecialistReconnect"),
  );
  assert.equal(acceptedPost.match(/method: "POST"/g)?.length, 1);
  assert.match(acceptedPost, /HTTP 200 means the server durably owns this turn/);
  assert.match(acceptedPost, /if \(!response\.body\)[\s\S]*?followChatTurnRequest/);
  assert.match(acceptedPost, /consumed\.lastEventId/);
  assert.match(
    source,
    /const terminal = await api\.followChatTurn\(runId, turnId[\s\S]*?if \(terminal\.status === "completed"/,
  );
  assert.match(
    source,
    /History proves that no user row was committed\.[\s\S]*?Only this state may[\s\S]*?return stream\(\)/,
  );
  assert.doesNotMatch(source, /accepted-pending|attempt < 90/);
});

test("intentional cancellation is terminal and is never recovered as transport loss", () => {
  assert.match(apiSource, /parsed\.event === "turn\.cancelled"/);
  assert.match(
    apiSource,
    /if \(replay\.status === "cancelled"\)[\s\S]*?"cancelled",[\s\S]*?true/,
  );
  assert.match(source, /error\.kind === "terminal" \|\| error\.kind === "cancelled"/);
  assert.match(source, /error\.kind === "cancelled"/);
});
