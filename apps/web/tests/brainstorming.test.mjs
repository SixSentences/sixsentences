import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const read = (path) => readFileSync(join(process.cwd(), path), "utf8");
const page = read("src/app/(app)/brainstorming/page.tsx");
const legacy = read("src/app/(app)/knowledge/page.tsx");
const result = read("src/components/brainstorming/brainstorm-result.tsx");
const projectWorkspace = read("src/components/brainstorming/project-brainstorm-workspace.tsx");
const helper = read("src/lib/brainstorming.ts");
const sidebar = read("src/components/shell/sidebar.tsx");
const interviews = read("src/app/(app)/interviews/page.tsx");
const tour = read("src/components/tour/product-tour.tsx");
const api = read("src/lib/api.ts");
const types = read("src/lib/types.ts");
const auth = read("src/lib/auth.tsx");
const livePanel = read("src/components/interviews/live-sessions-panel.tsx");

function executableBrainstormingHelper() {
  const compiled = ts.transpileModule(helper, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText;
  const module = { exports: {} };
  vm.runInNewContext(compiled, {
    module,
    exports: module.exports,
    Uint8Array,
    Set,
    JSON,
    window: {},
  });
  return module.exports;
}

test("Brainstorming replaces Knowledge as the first-class product destination", () => {
  assert.match(sidebar, /router\.push\("\/brainstorming"\)/);
  assert.match(sidebar, /pathname\.startsWith\("\/brainstorming"\)/);
  assert.match(sidebar, /<BrainCircuit aria-hidden="true" className="size-4 text-moss" \/>/);
  assert.match(sidebar, />\s*Brainstorming\s*</);
  assert.doesNotMatch(sidebar, /router\.push\("\/knowledge"\)|>\s*Knowledge\s*</);
  assert.match(page, /data-tour="brainstorming-page"/);
  assert.match(tour, /route: "brainstorming"/);
  assert.match(legacy, /redirect\(`\/brainstorming\$\{suffix\}`\)/);
});

test("the workspace is exclusively a private brainstorm surface, not a Knowledge aggregator", () => {
  assert.match(page, /api\.liveSessions\(RECENT_SESSION_LIMIT, "brainstorm"\)/);
  assert.match(page, /session\.purpose !== "brainstorm"/);
  assert.equal((page.match(/enabled: Boolean\(userId && selectedSessionId && session\?\.purpose === "brainstorm"\)/g) ?? []).length, 2);
  assert.doesNotMatch(page, /knowledgePages|KnowledgeEditor|LibraryDocument|libraryDocuments|evergreen|pinned|parent page/i);
  assert.doesNotMatch(page, /mock|sample brainstorm|placeholder result/i);
  assert.match(
    page,
    /eyebrow=\{german \? "Privater Arbeitsbereich" : "Private workspace"\}/,
  );
});

test("browser typing and speech share the canonical live-brainstorm contract", () => {
  assert.match(types, /LiveAudioChannel = "microphone" \| "system" \| "typed"/);
  assert.match(types, /web_input_channels: Array<"typed" \| "microphone">/);
  assert.match(types, /raw_audio_upload: false/);
  assert.match(api, /liveSessionSegments:[\s\S]*?method: "POST"/);
  assert.match(api, /liveBrainstormComplete:[\s\S]*?method: "POST"/);
  assert.match(api, /liveSessionBrainstormCreate:[\s\S]*?method: "POST"/);
  assert.match(api, /liveSessionBrainstormCancel:[\s\S]*?method: "POST"/);
  assert.match(page, /channel: inputMode/);
  assert.match(page, /last_event_sequence/);
  assert.match(page, /context_through_sequence: cutoff/);
});

test("direct input is a multi-entry chat-like stream with an explicit frozen structure action", () => {
  assert.match(page, /onKeyDown=\{\(event\) => \{[\s\S]*?event\.key === "Enter"[\s\S]*?submitThought/);
  assert.match(page, /api\.liveSessionSegments\(session\.id, pending\.segments\)/);
  assert.match(page, /Gedankenstrom/);
  assert.match(page, /Bestätigtes Rohtranskript anzeigen/);
  assert.match(page, /Strukturieren/);
  assert.match(page, /session\.status === "recording"[\s\S]*?api\.liveBrainstormComplete/);
  assert.match(page, /ThoughtTimeline/);
  assert.match(page, /\|\| recording\n\s*\) return/);
  assert.match(page, /remainingSegmentSlots[\s\S]*?brainstorm\.max_segments/);
});

test("all create, append and complete retries keep durable idempotency identities", () => {
  assert.match(helper, /BRAINSTORMING_CREATE_STORAGE_PREFIX/);
  assert.match(helper, /BRAINSTORMING_COMPOSER_STORAGE_PREFIX/);
  assert.match(helper, /BRAINSTORMING_COMPLETE_STORAGE_PREFIX/);
  assert.match(helper, /pending_append/);
  assert.match(page, /readCreateIntent\(userId\)/);
  assert.match(page, /stored\?\.pending_append \?\? buildAppendIntent/);
  assert.match(page, /readCompleteIntent\(userId, session\.id\)/);
  assert.match(page, /writeCompleteIntent/);
  assert.match(page, /removeCompleteIntent/);
  assert.match(helper, /client_event_id: `\$\{baseId\}\.\$\{index\}`/);
  assert.match(helper, /function writeJson\(key: string, value: unknown\): boolean/);
  assert.match(helper, /pending\.segments\.length > 100/);
  assert.match(page, /const pendingWasStored = hasStoredPending \|\| writeComposerDraft/);
  assert.match(page, /text: "",\s*microphone_text: ""/);
  assert.match(page, /if \(!pendingWasStored\)[\s\S]*?Es wurde nichts gesendet/);
  assert.match(page, /if \(!stored && !writeCompleteIntent/);
});

test("speech recognition is transparent and never uploads raw audio", () => {
  assert.match(page, /SpeechRecognition|webkitSpeechRecognition/);
  assert.match(page, /interimResults = true/);
  assert.match(page, /result\.isFinal/);
  assert.match(page, /setComposerText/);
  assert.match(page, /Nur finaler Text wird an die konfigurierte API gesendet/);
  assert.doesNotMatch(page, /An SixSentences wird nur finaler Text gesendet/);
  assert.match(page, /depending on the browser, its provider may process recognition/);
  assert.match(page, /recognition\.onresult = null;[\s\S]*?recognition\.abort\(\)/);
  assert.match(page, /window\.setTimeout\(\(\) => \{\s*preserveInterimText\(\);\s*abortSpeech\(\);\s*\}, 1_500\)/);
  assert.match(page, /speechStopTimerRef\.current = window\.setTimeout\([\s\S]*?\}, 1_500\);\s*recognitionRef\.current\?\.stop\(\)/);
  assert.match(page, /recognition\.onspeechstart = beginSpeechActivity/);
  assert.match(page, /recognition\.onspeechend = endSpeechActivity/);
  assert.match(page, /data-speech-wave=\{side\}[\s\S]*?data-active=\{active \? "true" : "false"\}/);
  assert.match(page, /motion-reduce:animate-none/);
  assert.match(page, /case "no-speech"[\s\S]*?tone: "neutral"/);
  assert.match(page, /case "not-allowed"[\s\S]*?Website-Einstellungen/);
  assert.match(page, /case "audio-capture"[\s\S]*?kein verfügbares Mikrofon/);
  assert.match(page, /case "network"[\s\S]*?Internetverbindung/);
  assert.match(page, /case "aborted":\s*return null/);
  assert.match(page, /recognition\.onerror = \(event\) => \{[\s\S]*?preserveInterimText\(\)/);
  assert.match(page, /session && session\.status !== "recording"\) abortSpeech\(\)/);
  assert.match(page, /recognition\.lang = brainstormLanguage === "de"/);
  assert.match(page, /api\.liveSessionUpdate\(session\.id, \{ language: nextLanguage \}\)/);
  assert.doesNotMatch(`${page}\n${helper}`, /audio_base64|MediaRecorder|getUserMedia/);
});

test("structured results expose every grounded output section and locate evidence", () => {
  for (const label of ["Zusammenfassung", "Themen", "Ideen", "Offene Fragen", "Entscheidungen", "Nächste Schritte", "Belege"]) {
    assert.match(result, new RegExp(label));
  }
  assert.match(result, /onLocateEvidence\(item\.segment_id\)/);
  assert.match(page, /document\.getElementById\(`brainstorm-segment-\$\{segmentId\}`\)/);
  assert.match(result, /receipt\.status === "pending"/);
  assert.match(result, /receipt\.status === "failed"/);
  assert.match(result, /NON_RETRYABLE_FAILURE_CODES\.has\(receipt\.error_code \?\? ""\)/);
  assert.match(result, /"brainstorm_too_large"/);
  assert.match(result, /onRetry/);
  assert.match(result, /onCancel/);
  assert.match(result, /Gespeicherten Stand strukturieren/);
});

test("history, receipts and visible transcript stay explicitly bounded", () => {
  assert.match(page, /const RECENT_SESSION_LIMIT = 100/);
  assert.match(page, /const VISIBLE_EVENT_LIMIT = 300/);
  assert.match(api, /liveSessions: \(limit = 100, purpose\?/);
  assert.match(api, /params\.set\("purpose", purpose\)/);
  assert.match(api, /liveSessionBrainstorms: \(id: string, limit = 20\)/);
  assert.match(page, /Showing the latest 100 brainstorms/);
  assert.match(page, /earlier thoughts remain stored/);
  assert.match(page, /last_segment_sequence \?\? 0\) - VISIBLE_EVENT_LIMIT\)/);
  assert.match(page, /session\?\.status === "recording" \? 2_000 : 10_000/);
});

test("responsive master-detail, deep links and failure states remain operable", () => {
  assert.match(page, /<ResizableWorkspaceSplit/);
  assert.match(page, /group-data-\[workspace-layout=split\]\/workspace:!flex/);
  assert.match(page, /hasMobileDetail \? "hidden" : "flex"/);
  assert.match(page, /hasMobileDetail \? "flex" : "hidden"/);
  assert.doesNotMatch(page, /hasMobileDetail \? "hidden lg:flex"/);
  assert.match(page, /const SESSION_ID =/);
  assert.match(page, /router\.replace\("\/brainstorming"\)/);
  assert.match(page, /role="status"/);
  assert.match(page, /role="alert"/);
  assert.match(page, /focus-visible:ring-2/);
  assert.match(page, /sessionRowRefs\.current\.get\(restoreId\)\?\.focus/);
  assert.match(page, /selectedSessionId[\s\S]*?!selected\.isLoading[\s\S]*?!selected\.isError[\s\S]*?selected\.data\?\.purpose === "brainstorm"[\s\S]*?detailHeadingRef\.current\?\.focus/);
  assert.match(page, /\[selected\.data\?\.id, selected\.data\?\.purpose, selected\.isError, selected\.isLoading, selectedSessionId\]/);
  assert.match(page, /id="brainstorming-history-search"/);
  assert.match(page, /aria-current=\{item\.id === selectedSessionId \? "page" : undefined\}/);
  assert.match(page, /aria-pressed=\{inputMode === "typed"\}/);
  assert.match(page, /aria-pressed=\{inputMode === "microphone"\}/);
  assert.match(page, /Lokaler Entwurf nicht im bestätigten Stand/);
  assert.match(page, /pendingIds\.every[\s\S]*?removeCompleteIntent\(userId, session\.id\)/);
  assert.match(page, /config\.data && !config\.data\.enabled/);
});

test("auth boundaries purge identity-bound brainstorm drafts and the forbidden completion icon is absent", () => {
  assert.match(
    auth,
    /PROTECTED_SESSION_STORAGE_PREFIXES = \[[\s\S]*?"six:brainstorming:"/,
  );
  assert.match(
    auth,
    /function clearMatchingStorageEntries[\s\S]*?storage\.removeItem\(key\)/,
  );
  assert.match(
    auth,
    /clearMatchingStorageEntries\(\s*window\.sessionStorage,[\s\S]*?queryClient\.clear\(\)/,
  );
  assert.doesNotMatch(auth, /sessionStorage\.clear\(\)/);
  assert.doesNotMatch(`${page}\n${result}\n${sidebar}`, /\bSparkles\b/);
});

test("legacy Conversation handoffs route brainstorms to the new workspace", () => {
  assert.match(interviews, /requestedTab === "knowledge"/);
  assert.match(interviews, /`\/brainstorming\$\{brainstormParams/);
  assert.match(interviews, /session\.purpose === "brainstorm"[\s\S]*?router\.replace\(`\/brainstorming\?session=/);
  assert.match(tour, /Eigene Gedanken werden im Hauptbereich Brainstorming/);
  assert.match(tour, /Personal thought streams are collected and structured/);
  assert.match(livePanel, /queryKey: \["live-sessions", sessionPurpose \?\? "all"\]/);
  assert.match(livePanel, /api\.liveSessions\(100, sessionPurpose\)/);
});

test("project workspace combines only completed sessions from one creator-visible project", () => {
  const helperModule = executableBrainstormingHelper();
  const challenge = {
    session_revision: 7,
    source_project_id: 41,
    source_document_id: "doc_12345678",
    source_document_revision: 3,
  };
  assert.equal(helperModule.parseBrainstormFilingChallenge({
    code: "brainstorm_session_already_synthesized",
    challenge,
  }), challenge);
  for (const malformed of [
    null,
    { code: "brainstorm_session_already_synthesized" },
    { code: "brainstorm_session_already_synthesized", challenge: { ...challenge, session_revision: -1 } },
    { code: "brainstorm_session_already_synthesized", challenge: { ...challenge, extra: true } },
    { code: "different_conflict", challenge },
  ]) {
    assert.equal(helperModule.parseBrainstormFilingChallenge(malformed), null);
  }
  assert.match(page, /session\.project_id === projectScopeId/);
  assert.match(projectWorkspace, /session\.project_id === project\.id[\s\S]*?session\.status === "completed"/);
  assert.match(page, /api\.liveSessionUpdate\(sessionId, \{ project_id: nextProjectId \}\)/);
  assert.match(page, /code === "brainstorm_session_already_synthesized"[\s\S]*?parseBrainstormFilingChallenge\(error\.detail\)[\s\S]*?if \(!invalidationChallenge\)[\s\S]*?return;[\s\S]*?setProjectMoveConfirmation/);
  assert.match(page, /invalidate_project_document: true/);
  assert.match(page, /invalidation_challenge: confirmation\.challenge/);
  assert.match(page, /Deine eigenen Projektnotizen bleiben erhalten/);
  assert.match(page, /code === "brainstorm_filing_challenge_stale"[\s\S]*?setProjectMoveConfirmation\(null\)/);
  assert.match(page, /session\.revision !== confirmation\.challenge\.session_revision/);
  assert.match(page, /<AlertDialog[\s\S]*?open=\{projectMoveConfirmation !== null\}[\s\S]*?Session verschieben und KI-Dokument leeren/);
  assert.match(api, /invalidate_project_document\?: boolean/);
  assert.match(api, /invalidation_challenge\?: BrainstormFilingChallenge/);
  assert.match(types, /interface BrainstormFilingChallenge[\s\S]*?session_revision: number;[\s\S]*?source_project_id: number;[\s\S]*?source_document_id: string;[\s\S]*?source_document_revision: number;/);
  assert.doesNotMatch(page, /invalidation_challenge:\s*\{[\s\S]*?session\.revision/);
  assert.match(projectWorkspace, /MAX_SELECTED_SESSIONS = 50/);
  assert.match(projectWorkspace, /selectedIds\.length < 2/);
  assert.match(projectWorkspace, /include_all_completed: includeAll/);
  assert.match(projectWorkspace, /output_language: outputLanguage/);
  assert.match(projectWorkspace, /id="project-brainstorm-output-language"/);
  assert.match(projectWorkspace, /id="project-brainstorm-output-language"[\s\S]*?appearance-none[\s\S]*?<ChevronDown aria-hidden="true" className="pointer-events-none absolute right-2\.5/);
  assert.match(page, /id="brainstorming-project-filter"[\s\S]*?appearance-none[\s\S]*?<ChevronDown aria-hidden="true" className="pointer-events-none absolute right-3/);
  assert.match(projectWorkspace, /expected_document_revision: document\?\.revision \?\? 0/);
  assert.match(api, /liveProjectBrainstormSynthesisCreate/);
  assert.match(api, /brainstorm-projects\/\$\{encodeURIComponent\(projectId\)\}\/syntheses/);
  assert.doesNotMatch(projectWorkspace, /mock|fallback result|sample project/i);
});

test("project master document retains unsupported threads and exposes raw provenance", () => {
  assert.match(projectWorkspace, /Ein lebendes Hauptdokument verbindet nur nachweislich/);
  assert.match(projectWorkspace, /unconnected_cluster_ids/);
  assert.match(projectWorkspace, /Bewusst nicht verbunden/);
  assert.match(projectWorkspace, /document\?\.update_available/);
  assert.match(projectWorkspace, /Weitere Projektquellen nicht eingearbeitet/);
  assert.match(projectWorkspace, /die in dieser belegten Fassung nicht enthalten sind/);
  assert.doesNotMatch(projectWorkspace, /Seit der letzten belegten Fassung wurden weitere abgeschlossene Brainstormings zugeordnet/);
  assert.match(projectWorkspace, /sichtbare Hauptdokument bleibt unverändert/);
  assert.match(projectWorkspace, /onOpenSession\(item\.session_id, item\.segment_id\)/);
  assert.match(page, /params\.set\("evidence", evidenceId\)/);
  assert.match(page, /brainstorm-raw-thoughts/);
  assert.match(page, /focusEvidence\(requestedEvidence\)/);
  assert.match(projectWorkspace, /Rohgedanken und Einzelstrukturen bleiben unverändert/);
});

test("evidence deep links fetch and focus one exact older raw thought without draining history", () => {
  assert.match(api, /liveSessionSegment: \(id: string, clientEventId: string\)/);
  assert.match(api, /sessions\/\$\{encodeURIComponent\(id\)\}\/segments\/\$\{encodeURIComponent\(clientEventId\)\}/);
  assert.match(types, /interface LiveSessionSegment[\s\S]*?id: string;[\s\S]*?sequence: number;[\s\S]*?created_at: string;/);
  assert.match(page, /queryKey: \["brainstorming-segment", userId, selectedSessionId, requestedEvidence\]/);
  assert.match(page, /!requestedEvidenceInTail[\s\S]*?retry: false,[\s\S]*?staleTime: Infinity/);
  assert.match(page, /selectSession\(session\.id, true, segmentId\)/);
  assert.match(page, /locatedEvidenceSegment[\s\S]*?focusEvidence\(requestedEvidence\)/);
  assert.match(page, /Exakt geladene Quelle/);
  assert.match(page, /umliegender Kontext ist nicht Teil dieser kompakten Ansicht/);
  assert.match(page, /exactEvidence\.error instanceof ApiError[\s\S]*?status === 404/);
  assert.match(page, /wurde nicht gefunden oder ist für dich nicht mehr zugänglich/);
  assert.match(page, /onRetryLocated=\{\(\) => void exactEvidence\.refetch\(\)\}/);
  assert.doesNotMatch(page, /ältere Rohgedanke liegt außerhalb der kompakten Timeline|evidence quote remains visible above/);
  assert.doesNotMatch(page, /while[\s\S]{0,300}liveSessionEvents/);
});

test("project-grounded session deletion uses the exact cleanup challenge and preserves caches on refusal", () => {
  const helperModule = executableBrainstormingHelper();
  const challenge = {
    session_revision: 9,
    impact_sha256: "a".repeat(64),
    affected_document_count: 1,
    affected_synthesis_count: 3,
    pending_synthesis_count: 1,
  };
  assert.equal(helperModule.parseBrainstormDeleteChallenge({
    code: "brainstorm_session_delete_requires_cleanup",
    challenge,
  }), challenge);
  for (const malformed of [
    { code: "brainstorm_session_delete_requires_cleanup", challenge: { ...challenge, impact_sha256: "bad" } },
    { code: "brainstorm_session_delete_requires_cleanup", challenge: { ...challenge, pending_synthesis_count: -1 } },
    { code: "brainstorm_session_delete_requires_cleanup", challenge: { ...challenge, affected_document_count: -1 } },
    { code: "brainstorm_session_delete_requires_cleanup", challenge: { ...challenge, extra: true } },
  ]) {
    assert.equal(helperModule.parseBrainstormDeleteChallenge(malformed), null);
  }
  assert.match(page, /confirmation\.kind === "session"[\s\S]*?code === "brainstorm_session_delete_requires_cleanup"[\s\S]*?parseBrainstormDeleteChallenge\(error\.detail\)[\s\S]*?setDeleteConfirmation\(\{[\s\S]*?kind: "project_cleanup"/);
  assert.match(page, /affected_document_count[\s\S]*?affected_synthesis_count[\s\S]*?pending_synthesis_count/);
  assert.match(page, /Manuelle Projektnotizen bleiben erhalten/);
  assert.match(page, /confirm_project_cleanup: true,[\s\S]*?cleanup_challenge: confirmation\.challenge/);
  assert.match(page, /confirmation\.kind === "project_cleanup"[\s\S]*?code === "brainstorm_delete_challenge_stale"[\s\S]*?kind: "session"/);
  assert.match(page, /<ConfirmDeleteDialog[\s\S]*?target=\{deleteDialogTarget\}[\s\S]*?pending=\{deleting\}/);
  assert.match(api, /cleanup_challenge: BrainstormDeleteChallenge/);
  assert.match(page, /removeQueries\(\{ queryKey: \["project-brainstorm-document", userId\] \}\)/);
  assert.match(page, /removeQueries\(\{ queryKey: \["project-brainstorm-syntheses", userId\] \}\)/);
  assert.doesNotMatch(page, /cleanup_challenge:\s*\{[\s\S]*?session\.revision/);
});

test("all brainstorming destructive choices use accessible product dialogs and native selects use one custom chevron", () => {
  assert.doesNotMatch(page, /(?:window\.)?confirm\s*\(/);
  assert.match(page, /<AlertDialogTitle>[\s\S]*?Lokalen Entwurf verwerfen\?/);
  assert.match(page, /Nur der nicht bestätigte Entwurf in diesem Browser wird entfernt/);
  assert.match(page, /requestDiscardLocalDraft[\s\S]*?setDiscardDraftSessionId\(session\.id\)/);
  assert.match(page, /confirmDiscardLocalDraft[\s\S]*?session\.id !== sessionId[\s\S]*?removeComposerDraft\(userId, sessionId\)/);
  assert.match(page, /Dauerhaft löschen[\s\S]*?Brainstorming behalten/);
  assert.match(page, /Bereinigen und löschen[\s\S]*?Alles behalten/);
  assert.match(page, /onOpenChange=\{\(open\) => \{[\s\S]*?if \(open \|\| projectUpdating\) return/);
  assert.match(page, /<AlertDialogCancel disabled=\{projectUpdating\}>/);
  assert.match(page, /<AlertDialogCancel disabled=\{revalidatingDraft\}>/);
  assert.equal((page.match(/<select/g) ?? []).length, 4);
  assert.equal((page.match(/forced-colors:appearance-auto/g) ?? []).length, 4);
  assert.equal((page.match(/<ChevronDown aria-hidden="true" className="pointer-events-none/g) ?? []).length, 4);
  assert.equal((page.match(/forced-colors:hidden/g) ?? []).length, 4);
  assert.equal((page.match(/peer-disabled:opacity-50/g) ?? []).length, 4);
});

test("project synthesis has bounded caches, durable identity and honest lifecycle controls", () => {
  assert.match(projectWorkspace, /PROJECT_RECEIPT_LIMIT = 20/);
  assert.match(projectWorkspace, /\["project-brainstorm-document", userId, project\.id\]/);
  assert.match(projectWorkspace, /\["project-brainstorm-syntheses", userId, project\.id, PROJECT_RECEIPT_LIMIT, 0\]/);
  assert.match(projectWorkspace, /projectFenceRef\.current/);
  assert.match(projectWorkspace, /uncertainCreate/);
  assert.match(projectWorkspace, /Auswahl, Sprache und sichere Anfrage-ID sind eingefroren/);
  assert.match(helper, /BRAINSTORMING_PROJECT_SYNTHESIS_STORAGE_PREFIX/);
  assert.match(helper, /readProjectSynthesisIntent/);
  assert.match(helper, /writeProjectSynthesisIntent/);
  assert.match(helper, /value\.include_all_completed && value\.session_ids\.length === 0/);
  assert.match(helper, /!value\.include_all_completed && value\.session_ids\.length >= 2/);
  assert.match(projectWorkspace, /liveProjectBrainstormSynthesisCancel/);
  assert.match(projectWorkspace, /liveProjectBrainstormSynthesisRetry/);
  assert.match(helper, /BRAINSTORMING_PROJECT_RETRY_STORAGE_PREFIX/);
  assert.match(projectWorkspace, /readProjectRetryIntent/);
  assert.match(projectWorkspace, /writeProjectRetryIntent/);
  assert.match(projectWorkspace, /removeProjectRetryIntent/);
  assert.match(projectWorkspace, /project_brainstorm_revision_conflict/);
  assert.doesNotMatch(projectWorkspace, /return receipt\.error|\{receipt\.error\}/);
  assert.match(projectWorkspace, /role="status"/);
  assert.match(projectWorkspace, /role="alert"/);
  assert.match(projectWorkspace, /SOURCE_PAGE_SIZE = 12/);
  assert.match(projectWorkspace, /visibleSourceSessions/);
  assert.match(projectWorkspace, /neueste geladene abgeschlossene Sessions/);
  assert.match(projectWorkspace, /aria-label=\{german \? "Seiten der geladenen Brainstormings"/);
  assert.equal((projectWorkspace.match(/id="project-document-clusters-heading"/g) ?? []).length, 1);
});

test("speech context is progressive enhancement and never post-processes the raw transcript", () => {
  const allStructuralTerms = ["Einleitung", "Methodik", "Ergebnisse", "Diskussion", "Introduction", "Methodology", "Results", "Discussion"];
  for (const term of allStructuralTerms) {
    assert.match(helper, new RegExp(`"${term}"`));
  }
  const helperModule = executableBrainstormingHelper();
  assert.deepEqual(
    Array.from(helperModule.buildSpeechVocabulary("de", null, [])),
    allStructuralTerms,
  );
  assert.match(page, /if \(typeof window === "undefined" \|\| !\("phrases" in recognition\)\) return false/);
  assert.match(page, /SpeechRecognitionPhrase\?/);
  assert.match(page, /catch \{\s*return false;\s*\}/);
  assert.match(page, /if \(result\.isFinal\) finalText \+= `\$\{result\[0\]\.transcript\.trim\(\)\} `/);
  assert.match(page, /setComposerText\(\(current\) => appendRecognisedText\(current, finalText, composerMaxChars\)\)/);
  assert.match(page, /function appendRecognisedText[\s\S]*?addition = next\.trim\(\)[\s\S]*?\.slice\(0, maxChars\)/);
  assert.doesNotMatch(page, /replace\([^\n]*finalText|correctTranscript|rewriteTranscript|normaliseTranscript/i);
  assert.match(page, /erkannter Rohtext wird niemals nachträglich umgeschrieben/);
  assert.match(helper, /MAX_SPEECH_HINTS = 8/);
  assert.match(helper, /window\.sessionStorage\.setItem/);
  assert.doesNotMatch(helper, /window\.localStorage\.(?:getItem|setItem)/);
});
