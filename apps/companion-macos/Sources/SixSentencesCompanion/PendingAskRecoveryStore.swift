import Foundation

struct RecoverablePendingAsk: Codable, Equatable, Sendable {
    let sessionID: String
    let clientRequestID: String
    let question: String
    let contextThroughSequence: Int
    let createdAt: Date
}

enum PendingAskRelaunchDecision: Equatable, Sendable {
    case missing
    case pending(AskLiveSessionResponse)
    case completed(AskLiveSessionResponse)
    case failed(AskLiveSessionResponse)

    static func resolve(
        intent: RecoverablePendingAsk,
        receipts: [AskLiveSessionResponse]
    ) -> PendingAskRelaunchDecision {
        guard let receipt = receipts.last(where: {
            $0.clientRequestID == intent.clientRequestID
        }) else { return .missing }
        switch receipt.status {
        case "pending": return .pending(receipt)
        case "failed": return .failed(receipt)
        default: return .completed(receipt)
        }
    }
}

enum PendingAskRelaunchPlan: Equatable, Sendable {
    case replay(AskLiveSessionRequest)
    case observe(PendingAskRelaunchDecision)

    static func make(
        intent: RecoverablePendingAsk,
        receipts: [AskLiveSessionResponse]
    ) -> PendingAskRelaunchPlan {
        let decision = PendingAskRelaunchDecision.resolve(
            intent: intent,
            receipts: receipts
        )
        if decision == .missing {
            return .replay(
                AskLiveSessionRequest(
                    clientRequestID: intent.clientRequestID,
                    question: intent.question,
                    contextThroughSequence: intent.contextThroughSequence
                )
            )
        }
        return .observe(decision)
    }
}

/// Persists an idempotent Ask intent before its POST starts so a terminated
/// app can reconcile the exact request ID without creating a second answer.
actor PendingAskRecoveryStore {
    private let fileURL: URL
    private var records: [RecoverablePendingAsk]
    private var loadFailed: Bool

    init(fileURL: URL? = nil) {
        self.fileURL = fileURL ?? Self.defaultFileURL()
        let loaded = Self.load(from: self.fileURL)
        records = loaded.records
        loadFailed = loaded.failed
    }

    func validateIntegrity() throws {
        if loadFailed { throw PendingAskRecoveryError.unreadableStore }
    }

    func save(_ intent: RecoverablePendingAsk) throws {
        try validateIntegrity()
        if let index = records.firstIndex(where: {
            $0.sessionID == intent.sessionID
                && $0.clientRequestID == intent.clientRequestID
        }) {
            guard records[index].question == intent.question,
                  records[index].contextThroughSequence == intent.contextThroughSequence
            else {
                throw PendingAskRecoveryError.conflictingRequest
            }
            return
        }
        records.append(intent)
        try persist()
    }

    func all() throws -> [RecoverablePendingAsk] {
        try validateIntegrity()
        return records.sorted { $0.createdAt < $1.createdAt }
    }

    func remove(sessionID: String, clientRequestID: String) throws {
        try validateIntegrity()
        let originalCount = records.count
        records.removeAll {
            $0.sessionID == sessionID && $0.clientRequestID == clientRequestID
        }
        if records.count != originalCount { try persist() }
    }

    func purge() throws {
        records.removeAll(keepingCapacity: false)
        loadFailed = false
        if FileManager.default.fileExists(atPath: fileURL.path) {
            try FileManager.default.removeItem(at: fileURL)
        }
    }

    private func persist() throws {
        let directory = fileURL.deletingLastPathComponent()
        try FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700]
        )
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o700],
            ofItemAtPath: directory.path
        )
        try JSONEncoder().encode(records).write(
            to: fileURL,
            options: [.atomic, .completeFileProtection]
        )
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o600],
            ofItemAtPath: fileURL.path
        )
    }

    private static func load(
        from url: URL
    ) -> (records: [RecoverablePendingAsk], failed: Bool) {
        guard FileManager.default.fileExists(atPath: url.path) else { return ([], false) }
        do {
            let data = try Data(contentsOf: url)
            return (try JSONDecoder().decode([RecoverablePendingAsk].self, from: data), false)
        } catch {
            return ([], true)
        }
    }

    private static func defaultFileURL() -> URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("SixSentencesCompanion", isDirectory: true)
            .appendingPathComponent("pending-asks-v1.json")
    }
}

enum PendingAskRecoveryError: LocalizedError {
    case unreadableStore
    case conflictingRequest

    var errorDescription: String? {
        switch self {
        case .unreadableStore:
            "Local pending-answer recovery data could not be read. It was left untouched."
        case .conflictingRequest:
            "A pending answer ID cannot be reused for a different question."
        }
    }
}
