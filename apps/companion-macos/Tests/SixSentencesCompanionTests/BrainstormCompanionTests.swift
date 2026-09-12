import Foundation
import XCTest
@testable import SixSentencesCompanion

final class BrainstormCompanionTests: XCTestCase {
    func testSoloModeNeverRequestsScreenCaptureOrAcceptsSystemChannel() {
        XCTAssertFalse(
            AudioCapturePermissionPlan.requiresScreenCapture(for: .microphoneOnly)
        )
        XCTAssertTrue(
            AudioCapturePermissionPlan.requiresScreenCapture(for: .conversation)
        )
        XCTAssertTrue(CompanionCapturePolicy.accepts(
            channel: .microphone,
            experienceMode: .brainstorm
        ))
        XCTAssertFalse(CompanionCapturePolicy.accepts(
            channel: .system,
            experienceMode: .brainstorm
        ))
        XCTAssertTrue(CompanionCapturePolicy.accepts(
            channel: .system,
            experienceMode: .conversation
        ))
    }

    func testBrainstormCreateOmitsConsentAndDeclaresPurpose() throws {
        let request = CreateLiveSessionRequest(
            clientSessionID: "client_brain_1",
            title: "Thesis ideas",
            projectID: 7,
            language: "de",
            purpose: "brainstorm",
            consent: nil
        )
        let object = try XCTUnwrap(
            JSONSerialization.jsonObject(with: JSONEncoder().encode(request)) as? [String: Any]
        )

        XCTAssertEqual(object["purpose"] as? String, "brainstorm")
        XCTAssertEqual(object["project_id"] as? Int, 7)
        XCTAssertNil(object["consent"])
    }

    func testProjectPolicyKeepsConversationAndBrainstormSelectionsIndependent() {
        let projects = [
            LiveProjectSummary(id: 7, name: "Conversation"),
            LiveProjectSummary(id: 11, name: "Thesis"),
        ]
        XCTAssertEqual(CompanionSessionProjectPolicy.projectID(
            experienceMode: .conversation,
            conversationProjectID: 7,
            brainstormProjectID: 11,
            availableProjects: projects
        ), 7)
        XCTAssertEqual(CompanionSessionProjectPolicy.projectID(
            experienceMode: .brainstorm,
            conversationProjectID: 7,
            brainstormProjectID: 11,
            availableProjects: projects
        ), 11)
        XCTAssertNil(CompanionSessionProjectPolicy.projectID(
            experienceMode: .brainstorm,
            conversationProjectID: 7,
            brainstormProjectID: 99,
            availableProjects: projects
        ))
    }

    func testBrainstormProjectCopyIsCompleteInGermanAndEnglish() {
        XCTAssertEqual(BrainstormProjectCopy.label(usesGerman: true), "Brainstorm-Projekt")
        XCTAssertEqual(BrainstormProjectCopy.noProject(usesGerman: true), "Ohne Projekt")
        XCTAssertTrue(BrainstormProjectCopy.help(usesGerman: true).contains("vorhandenen Projekt"))
        XCTAssertEqual(BrainstormProjectCopy.label(usesGerman: false), "Brainstorm project")
        XCTAssertEqual(BrainstormProjectCopy.noProject(usesGerman: false), "No project")
        XCTAssertTrue(BrainstormProjectCopy.help(usesGerman: false).contains("existing project"))
    }

    func testConfirmedCutoffUsesServerSequenceAfterRelaunchWithEmptyOutbox() {
        XCTAssertEqual(
            BrainstormCutoffPolicy.confirmed(
                uploadedThrough: nil,
                lastConfirmed: nil,
                completedThrough: nil,
                serverLastSegment: 17
            ),
            17
        )
        XCTAssertEqual(
            BrainstormCutoffPolicy.confirmed(
                uploadedThrough: 18,
                lastConfirmed: 17,
                completedThrough: nil,
                serverLastSegment: 17
            ),
            18
        )
    }

    func testFinalSpeechCallbackDuringStopStillTargetsCapturedSession() {
        XCTAssertEqual(
            CompanionTranscriptPersistencePolicy.sessionID(for: .recording(sessionID: "live_1")),
            "live_1"
        )
        XCTAssertEqual(
            CompanionTranscriptPersistencePolicy.sessionID(for: .stopping(sessionID: "live_1")),
            "live_1"
        )
        XCTAssertNil(
            CompanionTranscriptPersistencePolicy.sessionID(for: .completed(sessionID: "live_1"))
        )
    }

    func testDistinctFinalArrivingDuringStopPersistsPredecessorAndFlushesTailOnce() {
        var ids = ["event_a", "event_b"]
        var reducer = TranscriptReducer(makeID: { ids.removeFirst() })
        XCTAssertTrue(reducer.ingestFinal(SpeechUpdate(
            channel: .microphone,
            text: "First finalized thought",
            isFinal: true,
            startMS: 0,
            endMS: 1_000
        )).isEmpty)

        let committedDuringStop = reducer.ingestFinal(SpeechUpdate(
            channel: .microphone,
            text: "Second finalized thought",
            isFinal: true,
            startMS: 2_100,
            endMS: 3_000
        ))
        XCTAssertEqual(
            CompanionTranscriptPersistencePolicy.sessionID(for: .stopping(sessionID: "live_1")),
            "live_1"
        )
        XCTAssertEqual(committedDuringStop.map(\.text), ["First finalized thought"])
        XCTAssertEqual(reducer.flushAll().map(\.text), ["Second finalized thought"])
        XCTAssertTrue(reducer.flushAll().isEmpty)
    }

    func testStructurePolicyFailsClosedAcrossModesAndStates() {
        XCTAssertTrue(BrainstormSubmissionPolicy.canStructure(
            experienceMode: .brainstorm,
            sessionStatus: "completed",
            hasFinalTranscript: true,
            isSubmitting: false
        ))
        XCTAssertFalse(BrainstormSubmissionPolicy.canStructure(
            experienceMode: .conversation,
            sessionStatus: "completed",
            hasFinalTranscript: true,
            isSubmitting: false
        ))
        XCTAssertFalse(BrainstormSubmissionPolicy.canStructure(
            experienceMode: .brainstorm,
            sessionStatus: "recording",
            hasFinalTranscript: true,
            isSubmitting: false
        ))
    }

    func testBrainstormWebHandoffUsesOnlyTheExactSafeSessionRoute() {
        let origin = try! CommunityOrigin("https://research.example.org")
        let route = "https://research.example.org/writer/brainstorm_123"
        XCTAssertEqual(
            BrainstormWebHandoffPolicy.destination(
                purpose: "brainstorm",
                webURL: route,
                communityOrigin: origin
            )?.absoluteString,
            route
        )
        XCTAssertNil(BrainstormWebHandoffPolicy.destination(
            purpose: "conversation",
            webURL: route,
            communityOrigin: origin
        ))
        XCTAssertNil(BrainstormWebHandoffPolicy.destination(
            purpose: "brainstorm",
            webURL: "https://other.example.org/writer/brainstorm_123",
            communityOrigin: origin
        ))
        let loopback = try! CommunityOrigin("http://localhost:3000")
        XCTAssertNotNil(BrainstormWebHandoffPolicy.destination(
            purpose: "brainstorm",
            webURL: "http://localhost:3000/writer/brainstorm_123",
            communityOrigin: loopback
        ))
    }

    func testBrainstormOpensWebAfterCompletionButNotAtCaptureStart() {
        XCTAssertFalse(BrainstormWebHandoffPolicy.shouldOpenAtSessionStart(
            preferenceEnabled: true,
            experienceMode: .brainstorm
        ))
        XCTAssertTrue(BrainstormWebHandoffPolicy.shouldOpenAtSessionStart(
            preferenceEnabled: true,
            experienceMode: .conversation
        ))
        XCTAssertTrue(BrainstormWebHandoffPolicy.shouldOpenAfterCompletion(
            purpose: "brainstorm"
        ))
        XCTAssertFalse(BrainstormWebHandoffPolicy.shouldOpenAfterCompletion(
            purpose: "conversation"
        ))
    }

    func testAccountTransitionAlwaysScrubsBeforeConnectedRecovery() {
        let disconnected = BrainstormRecoveryContext(accountID: nil, isConnected: false)
        let accountBDisconnected = BrainstormRecoveryContext(
            accountID: "user:account-b",
            isConnected: false
        )
        let accountBConnected = BrainstormRecoveryContext(
            accountID: "user:account-b",
            isConnected: true
        )

        XCTAssertTrue(BrainstormAccountTransitionPolicy.shouldScrub(
            previous: disconnected,
            current: accountBConnected
        ))
        XCTAssertEqual(
            BrainstormAccountTransitionPolicy.recoveryAccountID(for: accountBConnected),
            "user:account-b"
        )
        XCTAssertTrue(BrainstormAccountTransitionPolicy.shouldScrub(
            previous: disconnected,
            current: accountBDisconnected
        ))
        XCTAssertNil(BrainstormAccountTransitionPolicy.recoveryAccountID(for: accountBDisconnected))
        XCTAssertFalse(BrainstormAccountTransitionPolicy.shouldScrub(
            previous: accountBDisconnected,
            current: accountBConnected
        ))
    }

    func testBrainstormReceiptDecodesGroundedStableSchema() throws {
        let receipt = try JSONDecoder().decode(
            BrainstormReceipt.self,
            from: Data(Self.completedReceiptJSON.utf8)
        )

        XCTAssertEqual(receipt.schemaVersion, 1)
        XCTAssertEqual(receipt.outputLanguage, .german)
        XCTAssertEqual(receipt.result?.themes.first?.title, "Fokus")
        XCTAssertEqual(receipt.result?.evidence.first?.segmentID, "segment_7")
        XCTAssertEqual(receipt.result?.evidence.first?.quote, "Ich fokussiere die Thesis.")
    }

    func testEvidenceReceiptsFromSameItemAndSegmentHaveUniqueIdentifiers() {
        let first = BrainstormEvidence(
            kind: "themes",
            index: 0,
            segmentID: "segment_7",
            quote: "First grounded quote."
        )
        let second = BrainstormEvidence(
            kind: "themes",
            index: 0,
            segmentID: "segment_7",
            quote: "Second grounded quote."
        )

        XCTAssertNotEqual(first.id, second.id)
    }

    func testMarkdownExportIsLocalizedAndIncludesEvidenceReceipts() throws {
        let receipt = try JSONDecoder().decode(
            BrainstormReceipt.self,
            from: Data(Self.completedReceiptJSON.utf8)
        )
        let result = try XCTUnwrap(receipt.result)

        let german = BrainstormMarkdownExporter.render(result, language: .german)
        let english = BrainstormMarkdownExporter.render(result, language: .english)

        XCTAssertTrue(german.contains("## Kurzfassung"))
        XCTAssertTrue(german.contains("## Belege"))
        XCTAssertTrue(german.contains("`segment_7`"))
        XCTAssertTrue(english.contains("## Summary"))
        XCTAssertTrue(english.contains("## Evidence"))
    }

    func testBrainstormRecoveryIntentSurvivesRelaunchAndBindsReceipt() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        let file = directory.appendingPathComponent("pending-brainstorms.json")
        defer { try? FileManager.default.removeItem(at: directory) }
        let request = CreateBrainstormRequest(
            clientRequestID: "brain_request_123",
            outputLanguage: .english,
            contextThroughSequence: 17
        )
        let intent = RecoverableBrainstormIntent(
            ownerAccountID: "user:account-a",
            sessionID: "live_brain_1",
            request: request,
            brainstormID: nil,
            createdAt: Date(timeIntervalSince1970: 1_700_000_000)
        )

        let first = BrainstormRecoveryStore(fileURL: file)
        try await first.save(intent)
        let afterCrash = BrainstormRecoveryStore(fileURL: file)
        let reloadedIntent = try await afterCrash.intent(
            sessionID: intent.sessionID,
            ownerAccountID: intent.ownerAccountID
        )
        XCTAssertEqual(reloadedIntent, intent)
        try await afterCrash.bind(
            sessionID: intent.sessionID,
            brainstormID: "brain_1",
            ownerAccountID: intent.ownerAccountID
        )
        let afterBinding = BrainstormRecoveryStore(fileURL: file)
        let boundIntent = try await afterBinding.intent(
            sessionID: intent.sessionID,
            ownerAccountID: intent.ownerAccountID
        )
        XCTAssertEqual(boundIntent?.brainstormID, "brain_1")

        let attributes = try FileManager.default.attributesOfItem(atPath: file.path)
        XCTAssertEqual(attributes[.posixPermissions] as? NSNumber, NSNumber(value: 0o600))
    }

    @MainActor
    func testDisconnectScrubsVisibleResultAndAccountBRecoveryStaysIsolated() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        let file = directory.appendingPathComponent("pending-brainstorms-v2.json")
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = BrainstormRecoveryStore(fileURL: file)
        let requestA = CreateBrainstormRequest(
            clientRequestID: "brain_request_account_a",
            outputLanguage: .german,
            contextThroughSequence: 17
        )
        try await store.save(RecoverableBrainstormIntent(
            ownerAccountID: "user:account-a",
            sessionID: "live_account_a",
            request: requestA,
            brainstormID: nil,
            createdAt: Date()
        ))

        let model = BrainstormViewModel(recovery: store)
        let completed = try JSONDecoder().decode(
            BrainstormReceipt.self,
            from: Data(Self.completedReceiptJSON.utf8)
        )
        model.acceptCompletionReceipt(
            sessionID: "visible_account_a",
            receipt: completed,
            ownerAccountID: "user:account-a"
        )
        XCTAssertNotNil(model.result)

        model.scrubPrivateState()
        XCTAssertNil(model.result)
        XCTAssertNil(model.receipt)

        model.recoverPendingIntent(ownerAccountID: "user:account-b")
        await Task.yield()
        XCTAssertNil(model.receipt)
        let accountBBeforeSave = try await store.all(ownerAccountID: "user:account-b")
        let accountAAfterDisconnect = try await store.all(ownerAccountID: "user:account-a")
        XCTAssertTrue(accountBBeforeSave.isEmpty)
        XCTAssertEqual(accountAAfterDisconnect.count, 1)

        try await store.save(RecoverableBrainstormIntent(
            ownerAccountID: "user:account-b",
            sessionID: "live_account_b",
            request: CreateBrainstormRequest(
                clientRequestID: "brain_request_account_b",
                outputLanguage: .english,
                contextThroughSequence: 3
            ),
            brainstormID: nil,
            createdAt: Date()
        ))
        let accountBAfterSave = try await store.all(ownerAccountID: "user:account-b")
        XCTAssertEqual(accountBAfterSave.count, 1)
    }

    func testLegacyUnownedBrainstormRecoveryFailsClosed() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let file = directory.appendingPathComponent("pending-brainstorms-v1.json")
        let legacy = #"[{"sessionID":"live_old","request":{"client_request_id":"request_old","output_language":"de","context_through_sequence":1,"schema_version":1},"brainstormID":null,"createdAt":0}]"#
        try Data(legacy.utf8).write(to: file)
        defer { try? FileManager.default.removeItem(at: directory) }

        let store = BrainstormRecoveryStore(fileURL: file)
        do {
            try await store.validateIntegrity()
            XCTFail("An unowned legacy recovery record must fail closed")
        } catch BrainstormRecoveryError.unreadableStore {}
    }

    func testDeletedPendingRecoveryDoesNotBlockNextSameAccountSession() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        let file = directory.appendingPathComponent("pending-brainstorms-v2.json")
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = BrainstormRecoveryStore(fileURL: file)
        let owner = "user:account-a"
        try await store.save(RecoverableBrainstormIntent(
            ownerAccountID: owner,
            sessionID: "deleted_session",
            request: CreateBrainstormRequest(
                clientRequestID: "deleted_request",
                outputLanguage: .german,
                contextThroughSequence: 4
            ),
            brainstormID: "deleted_brainstorm",
            createdAt: Date()
        ))

        XCTAssertTrue(BrainstormRecoveryPolicy.shouldDiscardIntent(
            after: APIClientError.server(status: 404, code: "session_unavailable", detail: "gone")
        ))
        try await store.remove(sessionID: "deleted_session", ownerAccountID: owner)
        try await store.save(RecoverableBrainstormIntent(
            ownerAccountID: owner,
            sessionID: "new_session",
            request: CreateBrainstormRequest(
                clientRequestID: "new_request",
                outputLanguage: .english,
                contextThroughSequence: 2
            ),
            brainstormID: nil,
            createdAt: Date()
        ))
        let remaining = try await store.all(ownerAccountID: owner)
        XCTAssertEqual(remaining.map(\.sessionID), ["new_session"])
    }

    func testMalformedBrainstormRecoveryFailsClosedWithoutOverwrite() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let file = directory.appendingPathComponent("pending-brainstorms.json")
        let malformed = Data("{not json".utf8)
        try malformed.write(to: file)
        defer { try? FileManager.default.removeItem(at: directory) }

        let store = BrainstormRecoveryStore(fileURL: file)
        do {
            try await store.validateIntegrity()
            XCTFail("Malformed brainstorm recovery must fail closed")
        } catch BrainstormRecoveryError.unreadableStore {
            XCTAssertEqual(try Data(contentsOf: file), malformed)
        }
    }

    func testOutboxCapacityRejectionIsBatchAtomic() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        let file = directory.appendingPathComponent("outbox.json")
        defer { try? FileManager.default.removeItem(at: directory) }
        let first = Self.segment(id: "one", text: "safe")
        let overflow = Self.segment(
            id: "overflow",
            text: String(repeating: "x", count: SegmentOutbox.maximumQueuedCharactersPerSession)
        )
        let store = SegmentOutbox(fileURL: file)

        do {
            _ = try await store.enqueue(
                sessionID: "live_brain_1",
                segments: [first, overflow]
            )
            XCTFail("Expected the batch to exceed the local safety bound")
        } catch OutboxError.capacityExceeded {
            let count = await store.count(sessionID: "live_brain_1")
            XCTAssertEqual(count, 0)
        }
    }

    func testCaptureLimitStopsBeforeBackendHardBound() {
        XCTAssertLessThan(BrainstormCaptureLimits.automaticStopCharacters, 500_000)
        XCTAssertFalse(BrainstormCaptureLimits.shouldStop(finalCharacterCount: 479_999))
        XCTAssertTrue(BrainstormCaptureLimits.shouldStop(finalCharacterCount: 480_000))
    }

    func testGermanErrorCopyCoversTerminalBrainstormFailures() {
        let codes = [
            "brainstorm_request_conflict", "no_live_transcript", "no_brainstorm_transcript",
            "capacity_unavailable", "brainstorm_unavailable", "ask_queue_unavailable",
            "brainstorm_in_progress", "brainstorm_session_unavailable",
            "brainstorm_microphone_only", "brainstorm_cutoff_mismatch",
            "invalid_brainstorm_cutoff", "context_not_available", "brainstorm_too_large",
            "brainstorm_too_many_segments", "brainstorm_is_solo", "session_unavailable",
            "brainstorm_result_not_grounded", "brainstorm_result_unavailable",
            "brainstorm_cancelled", "brainstorm_snapshot_conflict", "session_not_brainstorm",
            "brainstorm_complete_payload_required",
        ]
        for code in codes {
            XCTAssertNotNil(BrainstormErrorCopy.german(for: code), "Missing German copy for \(code)")
        }
        XCTAssertNil(BrainstormErrorCopy.german(for: "future_unknown_code"))
        XCTAssertEqual(
            BrainstormErrorCopy.localized(code: "future_unknown_code", usesGerman: true),
            "Die Brainstorm-Anfrage ist fehlgeschlagen. Dein finaler Text bleibt gespeichert."
        )
        XCTAssertEqual(
            BrainstormErrorCopy.localized(code: "future_unknown_code", usesGerman: false),
            "The brainstorm request failed. Your finalized text remains saved."
        )
        XCTAssertEqual(
            BrainstormErrorCopy.localized(code: "brainstorm_too_large", usesGerman: false),
            "This brain dump exceeds the safe processing limit. Split it into two sessions."
        )
    }

    @MainActor
    func testOutputLanguagePreferencePersistsDEAndEN() {
        let suiteName = "brainstorm-preferences-\(UUID().uuidString)"
        let defaults = try! XCTUnwrap(UserDefaults(suiteName: suiteName))
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let first = CompanionPreferences(defaults: defaults)
        first.setBrainstormOutputLanguage(.german)
        XCTAssertEqual(CompanionPreferences(defaults: defaults).brainstormOutputLanguage, "de")
        first.setBrainstormOutputLanguage(.english)
        XCTAssertEqual(CompanionPreferences(defaults: defaults).brainstormOutputLanguage, "en")
    }

    private static func segment(id: String, text: String) -> FinalTranscriptSegment {
        FinalTranscriptSegment(
            clientEventID: id,
            channel: .microphone,
            speaker: "You",
            startMS: 0,
            endMS: 1,
            text: text,
            isFinal: true
        )
    }

    private static let completedReceiptJSON = #"""
    {
      "id":"brain_1","client_request_id":"brain_request_123","status":"completed",
      "output_language":"de","context_through_sequence":17,"schema_version":1,
      "error":null,"error_code":null,"created_at":"2026-08-14T00:00:00Z",
      "result": {
        "summary":"Die Thesis fokussieren.",
        "themes":[{"title":"Fokus","description":"Scope eingrenzen."}],
        "ideas":[{"title":"Pilot","description":"Kleines Experiment starten."}],
        "open_questions":["Welche Daten?"],
        "decisions":["Zuerst ein Pilot."],
        "next_steps":[{"action":"Datensatz prüfen","owner":"Lukas"}],
        "evidence":[{
          "kind":"summary","index":0,"segment_id":"segment_7",
          "quote":"Ich fokussiere die Thesis."
        }]
      }
    }
    """#
}
