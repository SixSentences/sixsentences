import XCTest
@testable import SixSentencesCompanion

private final class TestCredentialStore: CompanionCredentialStore, @unchecked Sendable {
    private let lock = NSLock()
    private var token: String?

    init(token: String? = nil) {
        self.token = token
    }

    func bearerToken() throws -> String {
        try lock.withLock {
            guard let token else { throw CredentialError.missing }
            return token
        }
    }

    func save(token: String) throws {
        lock.withLock { self.token = token }
    }

    func delete() throws {
        lock.withLock { token = nil }
    }

    var hasToken: Bool {
        lock.withLock { token != nil }
    }
}

private struct TestPairingStore: CompanionPairingStore {
    func begin(webBaseURL: String) throws -> URL {
        try XCTUnwrap(URL(string: webBaseURL))
    }

    func consumeCallback(_ url: URL) throws -> (code: String, verifier: String) {
        ("synthetic-code", "synthetic-verifier")
    }

    func clear() throws {}
}

@MainActor
final class ConnectionStateTests: XCTestCase {
    func testFirstRunExposesDisconnectedStateAndBlocksRecording() {
        let model = makeModel(credentials: TestCredentialStore())
        XCTAssertFalse(model.hasCredential)
        XCTAssertFalse(model.isConnected)
        XCTAssertEqual(model.connectionState, .disconnected)

        model.requestStart()

        XCTAssertEqual(model.state, .idle)
        XCTAssertFalse(model.errorMessage.isEmpty)
    }

    func testStoredCredentialStartsInValidationState() {
        let model = makeModel(credentials: TestCredentialStore(token: "stored-token"))
        XCTAssertTrue(model.hasCredential)
        XCTAssertFalse(model.isConnected)
        XCTAssertEqual(model.connectionState, .validating)
    }

    func testAccountSwapIsBlockedForCaptureAndPendingCompletionStates() {
        XCTAssertTrue(CompanionAccountTransitionPolicy.canChangeAccount(
            state: .idle,
            hasInterruptedSession: false
        ))
        XCTAssertFalse(CompanionAccountTransitionPolicy.canChangeAccount(
            state: .starting,
            hasInterruptedSession: false
        ))
        XCTAssertFalse(CompanionAccountTransitionPolicy.canChangeAccount(
            state: .recording(sessionID: "live_a"),
            hasInterruptedSession: false
        ))
        XCTAssertFalse(CompanionAccountTransitionPolicy.canChangeAccount(
            state: .stopping(sessionID: "live_a"),
            hasInterruptedSession: false
        ))
        XCTAssertFalse(CompanionAccountTransitionPolicy.canChangeAccount(
            state: .queuedOffline(sessionID: "live_a"),
            hasInterruptedSession: false
        ))
        XCTAssertFalse(CompanionAccountTransitionPolicy.canChangeAccount(
            state: .failed(message: "retry required"),
            hasInterruptedSession: true
        ))
    }

    func testRemovingStoredBrowserCredentialUpdatesPublishedState() async throws {
        let store = TestCredentialStore(token: "browser-paired-token")
        let model = makeModel(credentials: store)

        XCTAssertTrue(model.hasCredential)
        XCTAssertEqual(model.connectionState, .validating)
        model.sessionTitle = "Account A private thesis"
        model.question = "Account A private question"

        try await model.removeToken()
        XCTAssertFalse(model.hasCredential)
        XCTAssertFalse(model.isConnected)
        XCTAssertEqual(model.connectionState, .disconnected)
        XCTAssertFalse(store.hasToken)
        XCTAssertNil(model.accountIdentity)
        XCTAssertEqual(model.sessionTitle, "Live research conversation")
        XCTAssertEqual(model.question, "")
        XCTAssertNil(model.session)
        XCTAssertTrue(model.liveThoughts.isEmpty)
        XCTAssertNil(model.completedBrainstormReceipt)
    }

    private func makeModel(credentials: TestCredentialStore) -> CompanionViewModel {
        let suite = "SixSentencesCompanionTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("companion-\(UUID().uuidString)", isDirectory: true)
        return CompanionViewModel(
            preferences: CompanionPreferences(defaults: defaults),
            credentials: credentials,
            outbox: SegmentOutbox(
                fileURL: directory.appendingPathComponent("outbox.json")
            ),
            recovery: SessionRecoveryStore(
                fileURL: directory.appendingPathComponent("sessions.json")
            ),
            pendingAskRecovery: PendingAskRecoveryStore(
                fileURL: directory.appendingPathComponent("pending-asks.json")
            ),
            brainstormRecovery: BrainstormRecoveryStore(
                fileURL: directory.appendingPathComponent("brainstorms.json")
            ),
            pairing: TestPairingStore()
        )
    }
}

private extension NSLock {
    func withLock<T>(_ operation: () throws -> T) rethrows -> T {
        lock()
        defer { unlock() }
        return try operation()
    }
}
