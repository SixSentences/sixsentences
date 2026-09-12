import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const chatSource = readFileSync(
  join(process.cwd(), "src/components/run/chat-panel.tsx"),
  "utf8",
);
const viewSource = readFileSync(
  join(process.cwd(), "src/components/run/run-view.tsx"),
  "utf8",
);
const apiSource = readFileSync(join(process.cwd(), "src/lib/api.ts"), "utf8");
const typeSource = readFileSync(join(process.cwd(), "src/lib/types.ts"), "utf8");
const safeDisplaySource = readFileSync(
  join(process.cwd(), "src/lib/safe-agent-display.ts"),
  "utf8",
);

test("web citations bind exact result URLs and never guess a homepage", () => {
  assert.match(chatSource, /const LOOKS_LIKE_WEB_SOURCE =/);
  assert.match(chatSource, /const sourceKey = part\.toLowerCase\(\)/);
  assert.match(chatSource, /safeExternalHttpUrl\(webLinks\?\.get\(sourceKey\)\)/);
  assert.match(chatSource, /new Map<string, Set<string>>\(\)/);
  assert.match(chatSource, /keys\.push\(result\.citation_key\.toLowerCase\(\)\)/);
  assert.match(chatSource, /if \(urls\.size === 1\) map\.set\(key, \[\.\.\.urls\]\[0\]\)/);
  assert.doesNotMatch(chatSource, /`https:\/\/\$\{domain\}`/);
  assert.match(chatSource, /if \(!href\) \{[\s\S]*?<span key=\{index\} className=\{webChipClass\}>/);
  assert.match(typeSource, /citation_key\?: string/);
});

test("the client gives every streamed turn one stable id and stops it idempotently", () => {
  assert.match(chatSource, /const turnId = createClientTurnId\(\)/);
  assert.match(chatSource, /activeTurnIdRef\.current = turnId/);
  assert.match(apiSource, /turn_id: turnId/);
  assert.match(apiSource, /chat\/turns\/\$\{encodeURIComponent\(turnId\)\}\/stop/);
  assert.match(typeSource, /\| "turn\.cancelled"/);
});

test("the composer stays editable after reconnect discovery and the send slot becomes stop", () => {
  assert.doesNotMatch(chatSource, /if \(!text \|\| sending \|\| disabledReason\) return/);
  assert.match(chatSource, /disabled=\{Boolean\(discoveryDisabledReason\)\}/);
  assert.match(chatSource, /onClick=\{\(\) => \(active \? void stop\(\) : submit\(\)\)\}/);
  assert.match(chatSource, /active[\s\S]*?"Aktuelle Antwort stoppen"[\s\S]*?"Stop the current answer"/);
  assert.match(chatSource, /"Frage senden"[\s\S]*?"Ask"/);
  assert.match(chatSource, /<Square className="size-3\.5 fill-current"/);
  assert.doesNotMatch(
    viewSource,
    /initialAskWorking\s*\?\s*"The first answer is still being prepared\."/,
  );
  assert.match(viewSource, /const chatDisabledReason = !askMode && !hasWorks/);
});

test("quick answer removes the separate header run controls", () => {
  assert.match(viewSource, /\{!askMode && <RunControls run=\{run\} \/>\}/);
});

test("messages sent during a turn form a visible stable removable queue", () => {
  assert.match(chatSource, /setQueuedTurns\(\(current\) => \[\s*\.\.\.current,/);
  assert.match(chatSource, /"Queued chat messages"/);
  assert.match(chatSource, /key=\{turn\.id\}/);
  assert.match(chatSource, /removeQueued\(turn\.id\)/);
  assert.match(chatSource, /"Add this message to the queue"/);
  assert.match(
    chatSource,
    /queue: \([\s\S]*?text: string,[\s\S]*?webSearchPublicDataConfirmed[\s\S]*?enqueueTurn\(/,
  );
  assert.match(
    chatSource,
    /function queueMessage\(\)[\s\S]*?setQuestion\(""\);[\s\S]*?queue\(text, \{/,
  );
  assert.match(chatSource, /onClick=\{queueMessage\}/);
  assert.match(
    chatSource,
    /if \(runInProgress \|\| ask\.isPending \|\| sendLockRef\.current\) return;[\s\S]*?const next = queuedTurns\[0\]/,
  );
});

test("Steuern prioritizes one queued instruction and stops the current turn once", () => {
  assert.match(chatSource, /language === "de" \? "Steuern" : "Steer"/);
  assert.match(chatSource, /filter\(\(turn\) => turn\.id === id\)[\s\S]*?steering: true/);
  assert.match(chatSource, /if \(turnActive\) await stopActiveTurn\(\)/);
  assert.match(
    chatSource,
    /if \(stopLockRef\.current \|\| !turnActive \|\| checkingActiveTurn\) return/,
  );
  assert.match(chatSource, /await api\.stopChatTurn\(runId, activeTurnId\)/);
});

test("stopping keeps already streamed answer text visible", () => {
  assert.match(chatSource, /streamingTextRef\.current \+= event\.delta/);
  assert.match(
    chatSource,
    /if \(intentionallyCancelled && streamingTextRef\.current\) \{[\s\S]*?setHandoffText\(visibleStreamingText\(streamingTextRef\.current\)\)/,
  );
  assert.match(chatSource, /value\.search\(\/\\n\\s\*SOURCES:/);
  assert.match(chatSource, /if \(!intentionallyCancelled\) \{/);
});

test("a terminal provider failure stays visible and can be retried intentionally", () => {
  assert.match(chatSource, /setFailedTurn\(\{/);
  assert.match(chatSource, /role="alert"/);
  assert.match(chatSource, /The answer could not be completed\. Your message is saved\./);
  assert.match(chatSource, /retryFailedTurn/);
  assert.match(chatSource, /setFailedTurn\(null\);[\s\S]*?launchTurn\(\{/);
});

test("long turns keep an honest elapsed status visible without blocking the composer", () => {
  assert.match(chatSource, /setActivityElapsedSeconds/);
  assert.match(chatSource, /data-chat-turn-elapsed/);
  assert.match(chatSource, /String\(activityElapsedSeconds % 60\)\.padStart/);
  assert.doesNotMatch(chatSource, /Still working|Arbeite weiter/);
  assert.doesNotMatch(chatSource, /Du kannst weiter schreiben oder jederzeit stoppen/);
  assert.doesNotMatch(chatSource, /You can keep typing or stop at any time/);
  assert.match(chatSource, /window\.setInterval\(updateElapsed, 1_000\)/);
});

test("completed quick-answer work collapses only after its persisted assistant receipt", () => {
  assert.match(chatSource, /function collectCompletedAgentTurns/);
  assert.match(
    chatSource,
    /if \(messages\[assistantIndex\]\.role !== "assistant"\) continue;/,
  );
  assert.match(chatSource, /const \[open, setOpen\] = useState\(false\);/);
  assert.match(chatSource, /data-agent-work-disclosure/);
  assert.match(chatSource, /"Agent work"/);
  assert.match(chatSource, /"Done"/);
  assert.match(
    chatSource,
    /if \(completedTurn && completedTurn\.firstToolIndex !== index\) \{\s*return null;/,
  );
  assert.match(
    chatSource,
    /message\.role === "tool" && completedTurn[\s\S]*?<CompletedAgentWorkDisclosure/,
  );
  assert.match(
    chatSource,
    /: message\.role === "tool" \? \([\s\S]*?<ToolMessage[\s\S]*?message=\{message\}/,
  );
  assert.match(chatSource, /settledToolStartIds\.has\(message\.id\)/);
  assert.match(typeSource, /started_message_id\?: number/);
  assert.match(chatSource, /const assistantTurnId = messageTurnId\(messages\[assistantIndex\]\)/);
  assert.match(chatSource, /assistantTurnId\s*\? toolTurnId === assistantTurnId\s*: toolTurnId === ""/);
});

test("remount discovery blocks the first enabled render and follows the owner-scoped turn", () => {
  assert.match(
    chatSource,
    /enabled && \(turnDiscovery\?\.runKey !== runKey \|\| !turnDiscovery\.checked\)/,
  );
  assert.match(chatSource, /const active = await api\.activeChatTurn\(runId\)/);
  assert.match(chatSource, /active \?\? \(await api\.latestChatTurn\(runId\)\)/);
  assert.match(chatSource, /await api\.followChatTurn\(/);
  assert.match(chatSource, /await api\.chatTurnStatus\(runId, turnId\)/);
  assert.match(chatSource, /queryKey: \["chat", runKey\]/);
  assert.match(chatSource, /activeTurnIdRef\.current = turnId/);
  assert.match(chatSource, /discovering: checkingActiveTurn/);
  assert.doesNotMatch(chatSource, /localStorage\.(?:setItem|getItem).*turn/i);
  assert.doesNotMatch(chatSource, /sessionStorage\.(?:setItem|getItem).*turn/i);
  assert.match(apiSource, /chat\/turns\/active/);
  assert.match(apiSource, /chat\/turns\/latest/);
  assert.match(apiSource, /events\/stream/);
});

test("an accepted quick answer follows its durable turn without a polling deadline", () => {
  const recovery = chatSource.slice(
    chatSource.indexOf("async function recoverInterruptedTurn"),
    chatSource.indexOf("export interface QueuedChatTurn"),
  );
  assert.match(recovery, /if \(accepted\) return followAcceptedTurn\(\)/);
  assert.match(recovery, /await api\.followChatTurn\(runId, turnId/);
  assert.match(recovery, /await api\.chatTurnStatus\(runId, turnId\)/);
  assert.match(recovery, /while \(!signal\.aborted\)/);
  assert.doesNotMatch(recovery, /attempt < 90|accepted-pending/);
  assert.match(
    chatSource,
    /recovered = await recoverInterruptedTurn\([\s\S]*?turnId,[\s\S]*?controller\.signal/,
  );
  assert.match(
    chatSource,
    /if \(recovered\.state === "completed"\) return recovered\.answer;[\s\S]*?return stream\(\)/,
  );
});

test("failed and cancelled receipts retain honest disclosure states", () => {
  assert.match(chatSource, /terminalStatus: "completed" \| "failed" \| "cancelled"/);
  assert.match(chatSource, /useState\(turn\.terminalStatus === "failed"\)/);
  assert.match(chatSource, /assistantPayload\?\.cancelled === true/);
  assert.match(chatSource, /"Needs attention"/);
  assert.match(chatSource, /"Stopped"/);
});

test("durable quick-answer outputs stay openable below the final answer", () => {
  assert.match(chatSource, /function isPersistentTurnOutput/);
  for (const kind of [
    "ui",
    "paper",
    "export",
    "table",
    "translation",
    "workspace_action",
  ]) {
    assert.match(chatSource, new RegExp(`"${kind}"`));
  }
  assert.match(
    chatSource,
    /outputs: tools[\s\S]*?\.filter\(isPersistentTurnOutput\)/,
  );
  assert.match(chatSource, /data-completed-turn-outputs/);
  assert.match(
    chatSource,
    /<ClaimAwareText[\s\S]*?<CompletedTurnOutputs[\s\S]*?<FollowUpChips/,
  );
  assert.match(
    chatSource,
    /function CompletedTurnOutputs[\s\S]*?<ToolMessage[\s\S]*?historical/,
  );
  const completedAnswerBranch = chatSource.slice(
    chatSource.indexOf("const claims = ("),
    chatSource.indexOf("{pendingQuestion && pendingUserIndex < 0"),
  );
  assert.doesNotMatch(completedAnswerBranch, /<ReasoningDisclosure/);
});

test("quick-answer operational text and links fail closed before rendering", () => {
  assert.match(chatSource, /safeAgentProgressText\(message\.content/);
  assert.match(chatSource, /safeAgentProgressText\(payload\?\.reason\)/);
  assert.match(chatSource, /safeAgentProgressText\(update\.completion_reason\)/);
  assert.match(chatSource, /userFacingStoredErrorMessage\(\s*result\.error/);
  assert.match(chatSource, /safeExternalHttpUrl\(/);
  assert.match(safeDisplaySource, /HARD_RUNTIME_CONTEXT/);
  assert.match(safeDisplaySource, /DOMAIN_ITERATION_CONTEXT/);
  assert.match(safeDisplaySource, /url\.protocol === "http:" \|\| url\.protocol === "https:"/);
});
