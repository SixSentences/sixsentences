import XCTest
@testable import SixSentencesCompanion

final class StateAndPersistenceTests: XCTestCase {
    func testConsentIsRequiredBeforeRecordingState() {
        var machine = CompanionSessionMachine()
        XCTAssertEqual(machine.apply(.startRequested), .awaitingConsent)
        XCTAssertEqual(machine.apply(.sessionCreated(id: "not-allowed-yet")), .awaitingConsent)
        XCTAssertEqual(machine.apply(.consentConfirmed), .starting)
        XCTAssertEqual(machine.apply(.sessionCreated(id: "live_1")), .recording(sessionID: "live_1"))
    }

    func testOfflineCompletionCanRecover() {
        var machine = CompanionSessionMachine()
        _ = machine.apply(.startRequested)
        _ = machine.apply(.consentConfirmed)
        _ = machine.apply(.sessionCreated(id: "live_1"))
        XCTAssertEqual(machine.apply(.stopRequested), .stopping(sessionID: "live_1"))
        XCTAssertEqual(machine.apply(.pendingUpload), .queuedOffline(sessionID: "live_1"))
        XCTAssertEqual(machine.apply(.completionSucceeded), .completed(sessionID: "live_1"))
    }

    func testAPIBaseURLRejectsCredentialBearingAndRemoteHTTPURLs() throws {
        XCTAssertThrowsError(try CompanionAPIClient.validateBaseURL("http://example.com"))
        XCTAssertThrowsError(try CompanionAPIClient.validateBaseURL("https://user:pass@example.com"))
        XCTAssertThrowsError(try CompanionAPIClient.validateBaseURL("https://example.com?token=secret"))
        XCTAssertNoThrow(try CompanionAPIClient.validateBaseURL("https://research.example.org/api"))
        XCTAssertNoThrow(try CompanionAPIClient.validateBaseURL("http://localhost:8000"))
    }

    func testOutboxPersistsFinalTextAndAcknowledgesOnlyExplicitIDs() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        let file = directory.appendingPathComponent("outbox.json")
        defer { try? FileManager.default.removeItem(at: directory) }
        let segment = FinalTranscriptSegment(
            clientEventID: "event_123",
            channel: .microphone,
            speaker: "You",
            startMS: 0,
            endMS: 500,
            text: "A finalized sentence",
            isFinal: true
        )
        let first = SegmentOutbox(fileURL: file)
        _ = try await first.enqueue(sessionID: "live_1", segments: [segment])
        let firstCount = await first.count(sessionID: "live_1")
        XCTAssertEqual(firstCount, 1)

        let reloaded = SegmentOutbox(fileURL: file)
        let reloadedBatch = await reloaded.batch(sessionID: "live_1", limit: 100)
        XCTAssertEqual(reloadedBatch, [segment])
        try await reloaded.acknowledge(eventIDs: [segment.clientEventID])
        let finalCount = await reloaded.count(sessionID: "live_1")
        XCTAssertEqual(finalCount, 0)
    }

    func testOutboxRejectsReusedEventIDWithDifferentPayload() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        let file = directory.appendingPathComponent("outbox.json")
        defer { try? FileManager.default.removeItem(at: directory) }
        let outbox = SegmentOutbox(fileURL: file)
        let original = FinalTranscriptSegment(
            clientEventID: "event_123",
            channel: .system,
            speaker: "Others",
            startMS: 10,
            endMS: 20,
            text: "Original",
            isFinal: true
        )
        let conflicting = FinalTranscriptSegment(
            clientEventID: "event_123",
            channel: .system,
            speaker: "Others",
            startMS: 10,
            endMS: 20,
            text: "Changed",
            isFinal: true
        )
        _ = try await outbox.enqueue(sessionID: "live_1", segments: [original])
        do {
            _ = try await outbox.enqueue(sessionID: "live_1", segments: [conflicting])
            XCTFail("Expected conflicting event ID to fail")
        } catch is OutboxError {
            // Expected: a retry may only reuse an ID with identical payload.
        }
    }

    func testRemovingCancelledSessionDeletesPersistedTranscriptText() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        let file = directory.appendingPathComponent("outbox.json")
        defer { try? FileManager.default.removeItem(at: directory) }
        let segment = FinalTranscriptSegment(
            clientEventID: "event_123",
            channel: .microphone,
            speaker: "You",
            startMS: 0,
            endMS: 10,
            text: "Sensitive finalized text",
            isFinal: true
        )
        let outbox = SegmentOutbox(fileURL: file)
        _ = try await outbox.enqueue(sessionID: "live_cancelled", segments: [segment])
        try await outbox.removeSession(sessionID: "live_cancelled")
        let reloaded = SegmentOutbox(fileURL: file)
        let count = await reloaded.count(sessionID: "live_cancelled")
        XCTAssertEqual(count, 0)
        XCTAssertFalse(String(decoding: try Data(contentsOf: file), as: UTF8.self).contains("Sensitive"))
    }

    func testMalformedOutboxFailsClosedWithoutOverwritingFile() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let file = directory.appendingPathComponent("outbox.json")
        let malformed = Data("{not valid json".utf8)
        try malformed.write(to: file)
        defer { try? FileManager.default.removeItem(at: directory) }

        let outbox = SegmentOutbox(fileURL: file)
        do {
            try await outbox.validateIntegrity()
            XCTFail("Malformed queue must not look empty and healthy")
        } catch OutboxError.unreadableStore {
            XCTAssertEqual(try Data(contentsOf: file), malformed)
        }
    }

    func testPendingAskIntentSurvivesRelaunchAndTerminalRemoval() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        let file = directory.appendingPathComponent("pending-asks.json")
        defer { try? FileManager.default.removeItem(at: directory) }
        let intent = RecoverablePendingAsk(
            sessionID: "live_1",
            clientRequestID: "request_123",
            question: "What did we decide?",
            contextThroughSequence: 7,
            createdAt: Date(timeIntervalSince1970: 1_700_000_000)
        )

        let first = PendingAskRecoveryStore(fileURL: file)
        try await first.save(intent)
        let reloaded = PendingAskRecoveryStore(fileURL: file)
        let reloadedIntents = try await reloaded.all()
        XCTAssertEqual(reloadedIntents, [intent])

        try await reloaded.remove(
            sessionID: intent.sessionID,
            clientRequestID: intent.clientRequestID
        )
        let afterTerminal = PendingAskRecoveryStore(fileURL: file)
        let remainingIntents = try await afterTerminal.all()
        XCTAssertTrue(remainingIntents.isEmpty)
        XCTAssertFalse(
            String(decoding: try Data(contentsOf: file), as: UTF8.self)
                .contains(intent.question)
        )
    }

    func testPendingAskRelaunchDecisionUsesExactIdempotencyKey() {
        let intent = RecoverablePendingAsk(
            sessionID: "live_1",
            clientRequestID: "request_123",
            question: "What did we decide?",
            contextThroughSequence: 7,
            createdAt: Date()
        )
        let unrelated = Self.askReceipt(
            requestID: "request_other",
            status: "completed",
            answer: "Unrelated"
        )
        let pending = Self.askReceipt(
            requestID: intent.clientRequestID,
            status: "pending",
            answer: ""
        )
        let completed = Self.askReceipt(
            requestID: intent.clientRequestID,
            status: "completed",
            answer: "Use option B."
        )

        XCTAssertEqual(
            PendingAskRelaunchDecision.resolve(intent: intent, receipts: [unrelated]),
            .missing
        )
        XCTAssertEqual(
            PendingAskRelaunchDecision.resolve(intent: intent, receipts: [unrelated, pending]),
            .pending(pending)
        )
        XCTAssertEqual(
            PendingAskRelaunchDecision.resolve(intent: intent, receipts: [pending, completed]),
            .completed(completed)
        )
    }

    func testCrashBeforeAskSendReplaysExactPersistedSnapshot() {
        let intent = RecoverablePendingAsk(
            sessionID: "live_1",
            clientRequestID: "request_123",
            question: "What did we decide?",
            contextThroughSequence: 7,
            createdAt: Date()
        )

        XCTAssertEqual(
            PendingAskRelaunchPlan.make(intent: intent, receipts: []),
            .replay(AskLiveSessionRequest(
                clientRequestID: intent.clientRequestID,
                question: intent.question,
                contextThroughSequence: intent.contextThroughSequence
            ))
        )
    }

    func testDelayedCommitReceiptIsObservedWithoutSecondReplay() {
        let intent = RecoverablePendingAsk(
            sessionID: "live_1",
            clientRequestID: "request_123",
            question: "What did we decide?",
            contextThroughSequence: 7,
            createdAt: Date()
        )
        let pending = Self.askReceipt(
            requestID: intent.clientRequestID,
            status: "pending",
            answer: ""
        )

        XCTAssertEqual(
            PendingAskRelaunchPlan.make(intent: intent, receipts: [pending]),
            .observe(.pending(pending))
        )
    }

    func testMalformedPendingAskStoreFailsClosedWithoutOverwritingFile() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let file = directory.appendingPathComponent("pending-asks.json")
        let malformed = Data("{not valid json".utf8)
        try malformed.write(to: file)
        defer { try? FileManager.default.removeItem(at: directory) }

        let store = PendingAskRecoveryStore(fileURL: file)
        do {
            try await store.validateIntegrity()
            XCTFail("Malformed pending Ask recovery must fail closed")
        } catch PendingAskRecoveryError.unreadableStore {
            XCTAssertEqual(try Data(contentsOf: file), malformed)
        }
    }

    func testExplicitPurgeDeletesEveryRecoveryFileEvenWhenUnreadable() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let files = ["outbox.json", "sessions.json", "pending-asks.json", "brainstorms.json"]
            .map { directory.appendingPathComponent($0) }
        for file in files {
            try Data("sensitive local recovery".utf8).write(to: file)
        }

        let outbox = SegmentOutbox(fileURL: files[0])
        let sessions = SessionRecoveryStore(fileURL: files[1])
        let asks = PendingAskRecoveryStore(fileURL: files[2])
        let brainstorms = BrainstormRecoveryStore(fileURL: files[3])
        try await outbox.purge()
        try await sessions.purge()
        try await asks.purge()
        try await brainstorms.purge()

        XCTAssertTrue(files.allSatisfy { !FileManager.default.fileExists(atPath: $0.path) })
        try await outbox.validateIntegrity()
        try await sessions.validateIntegrity()
        try await asks.validateIntegrity()
        try await brainstorms.validateIntegrity()
    }

    private static func askReceipt(
        requestID: String,
        status: String,
        answer: String
    ) -> AskLiveSessionResponse {
        AskLiveSessionResponse(
            id: "ask_\(requestID)",
            clientRequestID: requestID,
            question: "What did we decide?",
            answer: answer,
            transcriptSources: [],
            projectSources: [],
            createdAt: "2026-08-13T10:00:00Z",
            status: status,
            error: nil
        )
    }
}
