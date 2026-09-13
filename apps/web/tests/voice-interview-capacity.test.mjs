import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import { createLiveSessionHarness, deferred } from "./helpers/live-session-harness.mjs";

const read = (path) => readFileSync(join(process.cwd(), path), "utf8");
const interviews = read("src/app/(app)/interviews/page.tsx");
const interviewDetail = read("src/app/(app)/interviews/[id]/page.tsx");
const study = read("src/app/(app)/interviews/studies/[id]/page.tsx");
const publicTalk = read("src/app/talk/[token]/page.tsx");
const liveSession = read("src/components/voice/live-session.tsx");
const textSession = read("src/components/voice/text-session.tsx");
const api = read("src/lib/api.ts");
const types = read("src/lib/types.ts");
const participantInformation = read("src/components/participant-information.tsx");

function section(source, start, end) {
  const startIndex = source.indexOf(start);
  assert.notEqual(startIndex, -1, `missing section start: ${start}`);
  const endIndex = source.indexOf(end, startIndex + start.length);
  assert.notEqual(endIndex, -1, `missing section end: ${end}`);
  return source.slice(startIndex, endIndex);
}

const voiceConfigTypes = section(
  types,
  "interface VoiceSessionBaseConfig {",
  "export interface VoiceTurn {",
);
const textVoiceConfig = section(
  voiceConfigTypes,
  "export interface TextVoiceSessionConfig",
  "export type VoiceSessionConfig",
);

test("a live admission limit uses deployment-neutral resource language", () => {
  assert.match(liveSession, /Das Ressourcenlimit für dieses Interview ist erreicht/);
  assert.match(liveSession, /This interview has reached its resource limit/);
  assert.doesNotMatch(liveSession, /capacity limit|Kapazitätsgrenze|available interview capacity/);
});

test("spoken interviews use only the authenticated server-controlled relay", () => {
  assert.match(liveSession, /import \{ API_URL \} from "@\/lib\/api"/);
  assert.match(liveSession, /config\.transport !== "relay"/);
  assert.match(liveSession, /authenticate: \{ token: config\.token \}/);
  assert.doesNotMatch(liveSession, /access_token|setup:|sessionResumption|resumeHandle|goAway/);
  assert.match(liveSession, /relayState\?: \{ phase\?: "reconnecting" \| "live" \}/);
  assert.match(liveSession, /relayEnd\?: \{ reason\?: RelayEndReason \}/);
  assert.match(liveSession, /MAX_BUFFERED_AUDIO_CHUNKS/);
  assert.match(liveSession, /function drainAudio\(\)/);
  assert.match(liveSession, /durationMs: pcm\.byteLength \/ 32/);
  assert.doesNotMatch(liveSession, /const bufferedAudio = state\.pendingAudio\.splice\(0\)/);
  assert.equal(
    (liveSession.match(/new WebSocket\(/g) ?? []).length,
    1,
    "the one-use relay ticket must never create a replacement browser socket",
  );
  assert.doesNotMatch(liveSession, /reissue|replacement credential/i);
  assert.doesNotMatch(publicTalk, /publicTalkReissue|reissue=/);
  assert.doesNotMatch(api, /publicTalkReissue/);
  assert.doesNotMatch(liveSession, /systemInstruction|contextWindowCompression/);
  assert.doesNotMatch(liveSession, /inputAudioTranscription|outputAudioTranscription/);
  assert.doesNotMatch(liveSession, /live\.(?:instructions|voice|patience_ms)/);
  assert.match(voiceConfigTypes, /export interface LiveVoiceSessionConfig[\s\S]*mode: "live";[\s\S]*transport: "relay";[\s\S]*token: string;[\s\S]*ws_url: string;[\s\S]*model\?: string;/);
  assert.match(voiceConfigTypes, /export interface TextVoiceSessionConfig[\s\S]*mode: "text";/);
  assert.doesNotMatch(voiceConfigTypes, /instructions: string;/);
});

test("Live provider diagnostics never appear in participant-facing errors", () => {
  assert.doesNotMatch(liveSession, /event\.reason/);
  assert.doesNotMatch(liveSession, /onError\([^)]*(?:Gemini|provider|token)/i);
  assert.match(liveSession, /Das Sprachinterview konnte gerade nicht verbunden werden/);
  assert.match(liveSession, /The spoken interview could not connect right now/);
  assert.match(publicTalk, /userFacingPublicTalkErrorMessage\(requestError, german \? "de" : "en"\)/);
  assert.doesNotMatch(
    publicTalk,
    /requestError instanceof Error\s*\?\s*requestError\.message/,
  );
});

test("written interviews never receive or initialize a Gemini Live connection", () => {
  assert.match(api, /publicTalkStart:[\s\S]*mode: "live" \| "text"[\s\S]*body: \{[\s\S]*consent,[\s\S]*age_confirmed: ageConfirmed,[\s\S]*passcode,[\s\S]*mode/);
  assert.match(publicTalk, /api\.publicTalkStart\([\s\S]*ageConfirmed,[\s\S]*info\.consent_fingerprint,[\s\S]*passcode\.trim\(\),[\s\S]*mode/);
  assert.match(publicTalk, /step === "live" && session\?\.mode === "live"/);
  assert.match(textVoiceConfig, /mode: "text";/);
  assert.doesNotMatch(textVoiceConfig, /token:|ws_url:/);
});

test("public talk fails closed into written mode when Live is unavailable", () => {
  assert.match(types, /export interface PublicTalkInfo[\s\S]*live_available: boolean;/);
  assert.match(types, /available_modes: Array<"text" \| "live">;/);
  assert.match(publicTalk, /mode === "live" && !info\.live_available/);
  assert.match(publicTalk, /info\?\.live_available \? selectedMode : "text"/);
  assert.match(publicTalk, /if \(participationMode === "live"\)[\s\S]*setStep\("miccheck"\)[\s\S]*begin\("text"\)/);
  assert.match(publicTalk, /Schriftliches Interview starten/);
  assert.match(publicTalk, /step === "miccheck" && info\.live_available/);
  assert.match(publicTalk, /Mikrofon und Audioaufnahme werden nicht verwendet/);
  assert.match(interviews, /participation links for spoken or written AI interviews/);
});

test("unavailable invitation links show an actionable error instead of an endless spinner", () => {
  const errorGuard = publicTalk.indexOf("if (!isLoading && (isError || !info))");
  const loadingGuard = publicTalk.indexOf("if (isLoading || !info || step === null)");
  assert.ok(errorGuard >= 0 && loadingGuard > errorGuard);
  assert.match(publicTalk, /Please check the invitation link or try again later/);
  assert.doesNotMatch(publicTalk, /if \(isLoading \|\| step === null\)/);
});

test("public consent is bound to the exact disclosed text", () => {
  assert.match(types, /export interface PublicTalkInfo[\s\S]*consent_fingerprint: string;/);
  assert.match(types, /minimum_age: 18;/);
  assert.match(types, /live_provider: string;/);
  assert.match(types, /privacy_notice_url: string;/);
  assert.match(types, /terms_url: string;/);
  assert.match(
    api,
    /publicTalkStart:[\s\S]*ageConfirmed: boolean[\s\S]*consentFingerprint: string[\s\S]*age_confirmed: ageConfirmed[\s\S]*consent_fingerprint: consentFingerprint/,
  );
  assert.match(publicTalk, /ageConfirmed,[\s\S]*info\.consent_fingerprint,[\s\S]*passcode\.trim\(\),[\s\S]*mode/);
  assert.match(publicTalk, /ParticipantNoticeContent notice=\{participantNotice\}/);
  assert.match(publicTalk, /participant_notices\?\.\[participationMode\]/);
  assert.match(publicTalk, /participantNotice\?\.audio_declaration/);
  assert.match(api, /audio_consent: audioConsent/);
});

test("public interview participation requires a separate adult confirmation", () => {
  assert.match(publicTalk, /const \[ageConfirmed, setAgeConfirmed\] = useState\(false\)/);
  assert.match(publicTalk, /info\.age_declaration/);
  assert.match(publicTalk, /participantNotice.requires_consent && !consented/);
  assert.match(publicTalk, /participationMode === "live" && !audioConsented/);
  assert.match(publicTalk, /!ageConfirmed/);
  assert.match(api, /age_confirmed: ageConfirmed/);
});

test("public participation links require completed participant information", () => {
  assert.match(study, /participantInformationIsReady\(study\)/);
  assert.match(study, /study\.participant_information_ready === true/);
  assert.match(study, /ParticipantInformationEditor/);
  assert.match(study, /<ParticipantInformationEditor[\s\S]*?requireDpia/);
  assert.match(participantInformation, /dpia_status/);
  assert.match(participantInformation, /DPIA decision reference and reasoning/);
  assert.match(participantInformation, /ai_interview_scope_attested/);
  assert.match(participantInformation, /spoken_processing_approved/);
  assert.match(participantInformation, /Leave this unchecked to approve written interviews only/);
  assert.match(participantInformation, /biometric identification/);
  assert.match(participantInformation, /material guide or setting change clears this approval/);
  assert.match(study, /gaps=\{study\.participant_information_gaps \?\? \[\]\}/);
  assert.match(study, /key=\{`\$\{study\.id\}:\$\{study\.updated_at\}`\}/);
  assert.match(types, /dpia_status\?: "" \| "completed"/);
  assert.doesNotMatch(types, /not_required/);
  assert.match(
    study,
    /publicSpokenGloballyAvailable && studySpokenProcessingReady/,
  );
  assert.match(
    study,
    /!pendingPilotFinalize && \(!configured \|\| !studySpokenProcessingReady\)/,
  );
  assert.match(
    study,
    /Approve the exact spoken-processing scope in Participant information/,
  );
  assert.match(
    study,
    /spoken pilot stays disabled and participation links remain written-only/,
  );
  assert.match(
    study,
    /disabled=\{[\s\S]*?createInvite\.isPending \|\|[\s\S]*?!participantInformationIsReady\(study\) \|\|[\s\S]*?!studyLimitsAreValid[\s\S]*?\}/,
  );
});

test("client-reported live transcripts never claim independent source verification", () => {
  assert.match(types, /source_integrity\?: "client_reported_unverified" \| "provider_transcript" \| null/);
  assert.match(interviews, /interview\.source_integrity === "client_reported_unverified"/);
  assert.match(interviewDetail, /interview\.source_integrity === "client_reported_unverified"/);
  assert.doesNotMatch(interviews, /Unverified source|Quelle ungeprüft/);
  assert.match(interviews, /quotes matched to client-reported transcript/);
  assert.doesNotMatch(interviewDetail, /Client-reported transcript/);
  assert.doesNotMatch(interviewDetail, /Its wording was not independently checked/);
  assert.match(interviewDetail, /matched in client-reported transcript/);
});

test("written messages send prior history without duplicating the current answer", () => {
  assert.match(
    textSession,
    /const priorTurns = \[\.\.\.engine\.turns\];[\s\S]*engine\.turns\.push\(userTurn\)[\s\S]*sendMessage\(text, priorTurns\)/,
  );
  assert.doesNotMatch(textSession, /sendMessage\(text, engine\.turns\)/);
});

test("study and written-session duration respect the API-authoritative limit", () => {
  assert.match(types, /export interface VoiceConfig[\s\S]*min_live_session_minutes: number;/);
  assert.match(types, /export interface VoiceConfig[\s\S]*max_live_session_minutes: number;/);
  assert.match(types, /export interface VoiceConfig[\s\S]*public_spoken_available: boolean;/);
  assert.match(api, /voiceConfig: \(\) =>[\s\S]*request<VoiceConfig>/);
  assert.match(study, /SESSION_DURATION_OPTIONS = \[30, 45, 60\]/);
  assert.match(study, /FIELDWORK_LIMIT_OPTIONS = \[30, 60, 120, 300, 600, 1200\]/);
  assert.doesNotMatch(study, /cap sessions at 15 minutes/);
  assert.match(study, /cap sessions at 30 minutes/);
  assert.match(study, /minutes >= minLiveSessionMinutes/);
  assert.match(study, /minutes <= study\.budget_minutes/);
  assert.match(study, /minutes <= maxLiveSessionMinutes/);
  assert.match(study, /minutes >= study\.max_session_minutes/);
  assert.match(study, /study\.budget_minutes >= study\.max_session_minutes/);
  assert.match(study, /!studyLimitsAreValid/);
  assert.match(study, /below session cap/);
  assert.match(study, /above current limit/);
  assert.match(study, /voiceConfig\?\.max_live_session_minutes/);
  assert.match(study, /connected API currently allows spoken sessions up to/);
  assert.doesNotMatch(study, /recorded model costs|capacity allocation|allowance supports/);
  assert.match(study, /Connection renewals happen automatically/);
  assert.match(study, /Public\s+participation links currently open the written AI interview/);
  assert.match(study, /Create written participation link/);
  assert.match(study, /disabled=\{[\s\S]*currentSessionCapExceedsLimit/);
  assert.match(study, /Choose a session cap of \$\{maxLiveSessionMinutes\} minutes or less first/);
  assert.match(
    liveSession,
    /elapsed >= config\.max_session_minutes \* 60_000[\s\S]*phase === "live" \|\| phase === "reconnecting"/,
  );
  assert.match(publicTalk, /step === "text" && session\?\.mode === "text"/);
  assert.match(textSession, /config: TextVoiceSessionConfig/);
  assert.match(textSession, /config\.max_session_minutes\) \* 60_000/);
  assert.match(textSession, /setTimeLimitReached\(true\);[\s\S]*finish\(\)/);
  assert.match(textSession, /error instanceof ApiError && error\.status === 409/);
  assert.match(textSession, /closing \|\| timeLimitReached/);
});

test("spoken sessions show the API-authorized duration before any connection opens", () => {
  function renderedText(node) {
    if (Array.isArray(node)) return node.map(renderedText).join("");
    if (node && typeof node === "object") return renderedText(node.props?.children);
    return typeof node === "string" || typeof node === "number" ? String(node) : "";
  }

  for (const minutes of [30, 40, 60]) {
    const microphone = deferred();
    const live = createLiveSessionHarness({
      microphone: microphone.promise,
      maxSessionMinutes: minutes,
    });
    assert.match(renderedText(live.render()), new RegExp(`max ${minutes} min`));
    assert.equal(live.sockets.length, 0, "the limit is visible before microphone permission or setup");
    assert.ok(live.button().props.children.includes("Cancel connection"));
    live.unmount();
  }
});

test("Live failures are idempotent and release already-created sessions", () => {
  assert.match(liveSession, /if \(engine\.current\.closed\) return;[\s\S]*teardown\(\);[\s\S]*onError\(message\)/);
  assert.match(liveSession, /if \(state\.closed \|\| state\.finishing \|\| phase === "closing"\) return/);
  assert.match(publicTalk, /abandoningSessionIdsRef\.current\.has\(active\.id\)/);
  assert.match(publicTalk, /publicTalkFinalize\(token, active\.id,[\s\S]*turns: \[\],[\s\S]*duration_ms: 0,[\s\S]*aborted: true/);
});

test("public participants only see success after durable finalization", () => {
  assert.match(types, /\| "unavailable"/);
  assert.match(publicTalk, /unavailable: \{[\s\S]*not accepting new interviews right now/);
  assert.match(publicTalk, /setStep\("saving"\)[\s\S]*await api\.publicTalkFinalize/);
  assert.match(publicTalk, /setStep\(settled\.status === "completed" \? "done" : "discarded"\)/);
  assert.match(publicTalk, /setPendingFinalize\(\{ session: active, result \}\)/);
  assert.match(publicTalk, /if \(pendingFinalize\)[\s\S]*finalizeSession\(pendingFinalize\.session, pendingFinalize\.result\)/);
  assert.match(publicTalk, /step === "saving"/);
  assert.match(publicTalk, /step === "discarded"/);
});

test("an unanswered microphone prompt times out and releases a late stream", async () => {
  const microphone = deferred();
  const live = createLiveSessionHarness({ microphone: microphone.promise });
  await live.advance(30_000);
  assert.equal(live.errors.length, 1);
  assert.match(live.errors[0], /check microphone permission and your internet connection/);
  assert.equal(live.pendingTimeouts(), 0);
  microphone.resolve(live.stream);
  await live.flush();
  assert.equal(live.track.stopped, true);
  assert.equal(live.contexts.length, 0);
  assert.equal(live.sockets.length, 0);
  live.unmount();
});

test("an open socket without setup confirmation times out without sending late setup", async () => {
  const live = createLiveSessionHarness();
  await live.flush();
  const socket = live.sockets[0];
  socket.open();
  assert.equal(socket.sent.length, 1);
  await live.advance(20_000);
  assert.equal(live.errors.length, 1);
  assert.match(live.errors[0], /check your internet connection and try again/);
  assert.equal(live.track.stopped, true);
  assert.equal(live.contexts[0].closed, true);
  assert.equal(live.pendingTimeouts(), 0);
  socket.open();
  assert.equal(socket.sent.length, 1, "a timed-out socket cannot send setup later");
  await live.advance(30_000);
  assert.equal(live.errors.length, 1, "the startup timer must not report twice");
  live.unmount();
});

test("successful setup cancels both startup deadlines", async () => {
  const live = createLiveSessionHarness();
  await live.flush();
  const socket = live.sockets[0];
  socket.open();
  await socket.message({ setupComplete: {} });
  assert.equal(live.pendingTimeouts(), 0);
  await live.advance(31_000);
  assert.equal(live.errors.length, 0);
  assert.equal(live.results.length, 0);
  assert.equal(live.track.stopped, false);
  assert.equal(live.button().props.disabled, false);
  live.unmount();
  assert.equal(live.track.stopped, true);
});

test("participants can cancel startup and late audio initialization cannot reconnect", async () => {
  const worklet = deferred();
  const live = createLiveSessionHarness({ worklet: worklet.promise });
  await live.flush();
  const button = live.button();
  assert.equal(button.props.disabled, false);
  assert.ok(button.props.children.includes("Cancel connection"));
  button.props.onClick();
  await live.flush();
  assert.equal(live.results.length, 1);
  assert.equal(live.results[0].aborted, true);
  assert.equal(live.results[0].duration_ms, 0);
  assert.equal(live.track.stopped, true);
  assert.equal(live.pendingTimeouts(), 0);
  worklet.resolve();
  await live.flush();
  await live.advance(30_000);
  assert.equal(live.sockets.length, 0);
  assert.equal(live.errors.length, 0);
  assert.equal(live.results.length, 1);
  live.unmount();
});

test("a timed-out server handover preserves turns without creating another socket", async () => {
  const live = createLiveSessionHarness();
  await live.flush();
  const socket = live.sockets[0];
  socket.open();
  await socket.message({ setupComplete: {} });
  await socket.message({ serverContent: { inputTranscription: { text: "Keep my answer." } } });
  await socket.message({ relayState: { phase: "reconnecting" } });
  await live.advance(20_000);
  assert.equal(live.errors.length, 0, "an error callback would discard recorded turns");
  assert.equal(live.results.length, 1);
  assert.equal(live.results[0].aborted, false);
  assert.equal(live.results[0].turns.length, 0, "the server owns the saved transcript");
  assert.match(JSON.stringify(live.render()), /Keep my answer\./);
  assert.equal(live.track.stopped, true);
  assert.equal(live.pendingTimeouts(), 0);
  await live.advance(30_000);
  assert.equal(live.sockets.length, 1);
  assert.equal(live.results.length, 1);
  live.unmount();
});

test("a confirmed server handover clears its timeout on the same browser socket", async () => {
  const live = createLiveSessionHarness();
  await live.flush();
  const socket = live.sockets[0];
  socket.open();
  await socket.message({ setupComplete: {} });
  await socket.message({ relayState: { phase: "reconnecting" } });
  await socket.message({ relayState: { phase: "live" } });
  await live.advance(31_000);
  assert.equal(live.errors.length, 0);
  assert.equal(live.results.length, 0);
  assert.equal(live.pendingTimeouts(), 0);
  assert.equal(live.sockets.length, 1);
  live.unmount();
});

test("relay tickets authenticate only in the first frame to the configured API origin", async () => {
  for (const ws_url of [
    "wss://api.example.test/voice/sessions/session-test/relay",
    "/voice/sessions/session-test/relay",
  ]) {
    const live = createLiveSessionHarness({ config: { ws_url, model: "untrusted-legacy-model" } });
    await live.flush();
    assert.equal(live.errors.length, 0);
    const socket = live.sockets[0];
    assert.equal(socket.url, "wss://api.example.test/voice/sessions/session-test/relay");
    assert.equal(new URL(socket.url).search, "");
    live.audioFrame();
    assert.deepEqual(socket.sent, [], "audio must wait for authenticated setup");
    socket.open();
    assert.deepEqual(socket.sent, [{ authenticate: { token: "test-only" } }]);
    socket.open();
    await socket.message({ relayState: { phase: "live" } });
    live.audioFrame();
    assert.equal(socket.sent.length, 1, "live status alone cannot replace setup confirmation");
    await socket.message({ setupComplete: {} });
    const encoded = live.audioFrame();
    assert.deepEqual(socket.sent[1], {
      realtimeInput: { audio: { data: encoded, mimeType: "audio/pcm;rate=16000" } },
    });
    assert.equal(JSON.stringify(socket.sent).includes("untrusted-legacy-model"), false);
    live.unmount();
  }
});

test("untrusted relay configuration is rejected before opening microphone or socket", async (t) => {
  const invalid = [
    ["missing transport", { transport: undefined }],
    ["legacy direct transport", { transport: "direct" }],
    ["unknown transport", { transport: "provider" }],
    ["text mode", { mode: "text" }],
    ["missing ticket", { token: undefined }],
    ["blank ticket", { token: "" }],
    ["padded ticket", { token: " test-only" }],
    ["oversized ticket", { token: "x".repeat(8193) }],
    ["path traversal id", { id: "../session-test" }],
    ["encoded id", { id: "session%2ftest" }],
    ["provider host", { ws_url: "wss://generativelanguage.googleapis.com/voice/sessions/session-test/relay" }],
    ["suffix host", { ws_url: "wss://api.example.test.evil.test/voice/sessions/session-test/relay" }],
    ["wrong scheme", { ws_url: "ws://api.example.test/voice/sessions/session-test/relay" }],
    ["http scheme", { ws_url: "https://api.example.test/voice/sessions/session-test/relay" }],
    ["different port", { ws_url: "wss://api.example.test:9443/voice/sessions/session-test/relay" }],
    ["credentials", { ws_url: "wss://user:password@api.example.test/voice/sessions/session-test/relay" }],
    ["another session", { ws_url: "/voice/sessions/another/relay" }],
    ["another path", { ws_url: "/voice/sessions/session-test/finalize" }],
    ["trailing slash", { ws_url: "/voice/sessions/session-test/relay/" }],
    ["query ticket", { ws_url: "/voice/sessions/session-test/relay?token=secret" }],
    ["empty query", { ws_url: "/voice/sessions/session-test/relay?" }],
    ["fragment", { ws_url: "/voice/sessions/session-test/relay#secret" }],
    ["empty fragment", { ws_url: "/voice/sessions/session-test/relay#" }],
    ["protocol-relative URL", { ws_url: "//api.example.test/voice/sessions/session-test/relay" }],
    ["relative path alias", { ws_url: "/voice/sessions/session-test/../session-test/relay" }],
    ["encoded path alias", { ws_url: "/voice/sessions/%73ession-test/relay" }],
  ];
  for (const [label, config] of invalid) {
    await t.test(label, async () => {
      const live = createLiveSessionHarness({ config });
      await live.flush();
      assert.equal(live.errors.length, 1);
      assert.equal(live.results.length, 0);
      assert.equal(live.microphoneRequests.length, 0);
      assert.equal(live.sockets.length, 0);
      assert.equal(live.pendingTimeouts(), 0);
      assert.match(live.errors[0], /could not connect right now/);
      assert.doesNotMatch(live.errors[0], /secret|password|provider|token|wss?:/i);
      live.unmount();
    });
  }
});

test("unencrypted relay transport is available only for the configured loopback API", async () => {
  for (const apiUrl of ["http://127.0.0.1:8000", "http://localhost:8000", "http://[::1]:8000"]) {
    const live = createLiveSessionHarness({ apiUrl, config: { ws_url: "/voice/sessions/session-test/relay" } });
    await live.flush();
    assert.equal(live.errors.length, 0);
    assert.equal(live.sockets[0].url, `${apiUrl.replace("http:", "ws:")}/voice/sessions/session-test/relay`);
    live.unmount();
  }
  for (const apiUrl of ["http://api.example.test", "https://api.example.test/prefix", "https://user:password@api.example.test"]) {
    const live = createLiveSessionHarness({ apiUrl, config: { ws_url: "/voice/sessions/session-test/relay" } });
    await live.flush();
    assert.equal(live.errors.length, 1);
    assert.equal(live.microphoneRequests.length, 0);
    assert.equal(live.sockets.length, 0);
    live.unmount();
  }
});

test("server handovers retain a bounded audio bridge and never expose provider controls", async () => {
  const live = createLiveSessionHarness();
  await live.flush();
  const socket = live.sockets[0];
  socket.open();
  await socket.message({ setupComplete: {} });
  const reply = Buffer.from(Int16Array.from([1, -1]).buffer).toString("base64");
  await socket.message({ serverContent: { modelTurn: { parts: [{ inlineData: { data: reply } }] } } });
  assert.equal(live.playback.length, 1);
  await socket.message({ relayState: { phase: "reconnecting" } });
  assert.equal(live.playback[0].stopped, true);
  const frames = Array.from({ length: 170 }, (_, index) => live.audioFrame([index, -index]));
  assert.equal(socket.sent.length, 1, "microphone data waits during a server handover");
  await socket.message({ sessionResumptionUpdate: { resumable: true, newHandle: "never-client-owned" } });
  await socket.message({ goAway: { timeLeft: "30s" } });
  assert.equal(live.sockets.length, 1);
  assert.equal(socket.sent.length, 1, "legacy provider controls cannot send setup or open sockets");
  await socket.message({ relayState: { phase: "live" } });
  assert.equal(socket.sent.length, 2, "handover sends only the first buffered frame immediately");
  await live.advance(200);
  assert.deepEqual(socket.sent.slice(1).map((frame) => frame.realtimeInput.audio.data), frames.slice(-160));
  assert.equal(live.pendingTimeouts(), 0);
  assert.equal(live.sockets.length, 1);
  assert.equal(live.contexts.length, 1);
  live.unmount();
});

test("handover audio drains in real time without overtaking or bursting the server gate", async () => {
  const live = createLiveSessionHarness();
  await live.flush();
  const socket = live.sockets[0];
  socket.open();
  await socket.message({ setupComplete: {} });
  await socket.message({ relayState: { phase: "reconnecting" } });
  const expected = [];
  for (let index = 0; index < 5; index += 1) {
    expected.push(live.audioFrame(Array(1600).fill(index + 1)));
    await live.advance(100);
  }
  await socket.message({ relayState: { phase: "live" } });
  assert.equal(socket.sent.length, 2);
  for (let index = 0; index < 5; index += 1) {
    expected.push(live.audioFrame(Array(1600).fill(index + 10)));
    await live.advance(100);
  }
  await live.advance(500);
  const audio = socket.sent.slice(1);
  assert.deepEqual(audio.map((frame) => frame.realtimeInput.audio.data), expected);
  let tokens = 6400;
  let last = socket.sentAt[1];
  for (let index = 0; index < audio.length; index += 1) {
    const now = socket.sentAt[index + 1];
    tokens = Math.min(6400, tokens + (now - last) * 32);
    const size = Buffer.from(audio[index].realtimeInput.audio.data, "base64").length;
    assert.ok(size <= tokens, "each frame must satisfy the exact backend PCM rate gate");
    tokens -= size;
    last = now;
    if (index > 0) assert.ok(now - socket.sentAt[index] >= 100);
  }
  assert.equal(live.pendingTimeouts(), 0);
  assert.equal(live.sockets.length, 1);
  live.unmount();
});

test("queued audio pauses for a second handover and is cancelled by stop or unmount", async () => {
  for (const termination of ["stop", "unmount"]) {
    const live = createLiveSessionHarness();
    await live.flush();
    const socket = live.sockets[0];
    socket.open();
    await socket.message({ setupComplete: {} });
    for (let index = 0; index < 3; index += 1) live.audioFrame(Array(1600).fill(index));
    assert.equal(socket.sent.length, 2);
    await socket.message({ relayState: { phase: "reconnecting" } });
    await live.advance(1000);
    assert.equal(socket.sent.length, 2, "a pending timer cannot send while the relay rotates");
    await socket.message({ setupComplete: {} });
    assert.equal(socket.sent.length, 3);
    if (termination === "stop") {
      live.button().props.onClick();
      await socket.message({ relayEnd: { reason: "connection" } });
    } else live.unmount();
    const sent = socket.sent.length;
    await live.advance(2000);
    assert.equal(socket.sent.length, sent, "termination cancels the PCM drain immediately");
    assert.equal(live.pendingTimeouts(), 0);
    live.unmount();
  }
});

test("relay end reasons preserve transcript and optional recording with safe notices", async (t) => {
  for (const reason of ["budget", "time", "connection", "unavailable", "__proto__", "token: technical-secret"]) {
    for (const language of ["en", "de"]) {
      await t.test(`${reason} / ${language}`, async () => {
        const live = createLiveSessionHarness({ config: { retention: "keep", language } });
        await live.flush();
        const socket = live.sockets[0];
        socket.open();
        await socket.message({ setupComplete: {} });
        live.recorders[0].chunk("recorded-audio");
        await socket.message({ serverContent: { inputTranscription: { text: "Keep this answer." } } });
        await live.advance(5000);
        await socket.message({ relayEnd: { reason } });
        await live.flush();
        socket.close(1011, "Never show raw provider failure");
        socket.error("Private diagnostic");
        await live.flush();
        assert.equal(live.errors.length, 0);
        assert.equal(live.results.length, 1);
        assert.equal(live.results[0].aborted, false);
        assert.equal(
          live.results[0].endReason,
          ["budget", "time", "unavailable"].includes(reason) ? reason : "connection",
        );
        assert.equal(live.results[0].turns.length, 0, "local captions never replace provider evidence");
        assert.match(JSON.stringify(live.render()), /Keep this answer\./);
        assert.equal(live.results[0].audio_base64, Buffer.from("recorded-audio").toString("base64"));
        assert.equal(live.results[0].duration_ms, 5000);
        assert.equal(live.track.stopped, true);
        assert.equal(live.contexts[0].closed, true);
        assert.equal(live.pendingTimeouts(), 0);
        assert.equal(live.sockets.length, 1);
        const ui = JSON.stringify(live.render());
        assert.match(ui, language === "de" ? /gespeichert/ : /being saved/);
        assert.doesNotMatch(ui, /technical-secret|__proto__|Private diagnostic|Never show raw provider/);
        live.unmount();
      });
    }
  }
});

test("manual stop waits for durable relay completion and cannot finalize twice", async () => {
  const live = createLiveSessionHarness();
  await live.flush();
  const socket = live.sockets[0];
  socket.open();
  await socket.message({ setupComplete: {} });
  await socket.message({ serverContent: { inputTranscription: { text: "My answer." } } });
  const stop = live.button().props.onClick;
  stop();
  stop();
  await live.flush();
  assert.deepEqual(socket.sent, [{ authenticate: { token: "test-only" } }, { type: "stop" }]);
  assert.equal(live.results.length, 0, "finalization must wait for the server close checkpoint");
  assert.equal(live.track.stopped, true, "microphone stops immediately on user request");
  const sent = socket.sent.length;
  live.audioFrame();
  assert.equal(socket.sent.length, sent);
  assert.equal(live.button().props.disabled, true);
  await socket.message({ serverContent: { inputTranscription: { text: " Final words." } } });
  await socket.message({ relayEnd: { reason: "connection" } });
  await live.flush();
  assert.equal(live.results.length, 1);
  assert.equal(live.results[0].turns.length, 0);
  assert.match(JSON.stringify(live.render()), /My answer\. Final words\./);
  assert.equal(live.results[0].aborted, false);
  assert.equal(live.results[0].endReason, "user", "a stop acknowledgement cannot replace user intent");
  assert.equal(live.pendingTimeouts(), 0);
  await live.advance(30_000);
  assert.equal(live.results.length, 1);
  live.unmount();
});

test("manual relay stop has a three-second bound and releases its timer on unmount", async () => {
  for (const unmount of [false, true]) {
    const live = createLiveSessionHarness();
    await live.flush();
    const socket = live.sockets[0];
    socket.open();
    await socket.message({ setupComplete: {} });
    await socket.message({ serverContent: { inputTranscription: { text: "Preserved." } } });
    live.button().props.onClick();
    await live.advance(2999);
    assert.equal(live.results.length, 0);
    if (unmount) live.unmount();
    await live.advance(1);
    assert.equal(live.results.length, unmount ? 0 : 1);
    assert.equal(live.pendingTimeouts(), 0);
    assert.equal(live.sockets.length, 1);
    assert.equal(live.track.stopped, true);
    assert.equal(live.errors.length, 0);
    live.unmount();
  }
});

test("socket close waits for an already-received transcript frame before saving", async () => {
  const live = createLiveSessionHarness();
  await live.flush();
  const socket = live.sockets[0];
  socket.open();
  await socket.message({ setupComplete: {} });
  const text = deferred();
  const reading = socket.onmessage({ data: { text: () => text.promise } });
  socket.close(1011, "Provider credentials rejected");
  await live.flush();
  assert.equal(live.results.length, 0);
  text.resolve(JSON.stringify({ serverContent: { inputTranscription: { text: "Last answer." } } }));
  await reading;
  await live.flush();
  assert.equal(live.results.length, 1);
  assert.equal(live.results[0].turns.length, 0);
  assert.match(JSON.stringify(live.render()), /Last answer\./);
  assert.equal(live.errors.length, 0);
  assert.equal(live.sockets.length, 1);
  live.unmount();
});

test("lost local captions cannot abort the server-owned relay transcript", async () => {
  const live = createLiveSessionHarness();
  await live.flush();
  const socket = live.sockets[0];
  socket.open();
  await socket.message({ setupComplete: {} });
  await live.advance(6000);
  // The provider may have produced a durable transcript even if its browser
  // caption frame was lost with the participant's network connection.
  socket.close(1006, "Private diagnostic");
  await live.flush();
  assert.equal(live.results.length, 1);
  assert.equal(live.results[0].turns.length, 0);
  assert.equal(live.results[0].duration_ms, 6000);
  assert.equal(live.results[0].aborted, false);
  assert.equal(live.results[0].endReason, "connection");
  assert.equal(live.errors.length, 0);
  assert.equal(live.sockets.length, 1);
  live.unmount();
});

test("long local captions cannot exceed server-owned relay finalization limits", async () => {
  const live = createLiveSessionHarness({ config: { retention: "keep" } });
  await live.flush();
  const socket = live.sockets[0];
  socket.open();
  await socket.message({ setupComplete: {} });
  live.recorders[0].chunk("unchanged-recording");
  await socket.message({ serverContent: {
    inputTranscription: { text: `${"A detailed answer. ".repeat(1000)}End of long answer.` },
  } });
  await live.advance(6000);
  await socket.message({ relayEnd: { reason: "time" } });
  await live.flush();
  assert.equal(live.results.length, 1);
  assert.equal(live.results[0].turns.length, 0, "no browser transcript is sent back or schema-validated");
  assert.equal(live.results[0].duration_ms, 6000);
  assert.equal(live.results[0].aborted, false);
  assert.equal(live.results[0].audio_base64, Buffer.from("unchanged-recording").toString("base64"));
  assert.match(JSON.stringify(live.render()), /End of long answer\./);
  assert.equal(live.errors.length, 0);
  live.unmount();
});

test("startup relay errors are friendly and never consume a second ticket", async () => {
  for (const event of ["close", "error", "end"]) {
    const live = createLiveSessionHarness();
    await live.flush();
    const socket = live.sockets[0];
    socket.open();
    if (event === "close") socket.close(1011, "Token API key debug details");
    else if (event === "error") socket.error("Token API key debug details");
    else await socket.message({ relayEnd: { reason: "unavailable" } });
    await live.flush();
    assert.equal(live.errors.length, 1);
    assert.match(live.errors[0], /could not connect right now/);
    assert.doesNotMatch(live.errors[0], /token|api key|debug/i);
    assert.equal(live.track.stopped, true);
    assert.equal(live.pendingTimeouts(), 0);
    assert.equal(live.results.length, 0);
    await live.advance(30_000);
    assert.equal(live.errors.length, 1);
    assert.equal(live.sockets.length, 1);
    live.unmount();
  }
});
