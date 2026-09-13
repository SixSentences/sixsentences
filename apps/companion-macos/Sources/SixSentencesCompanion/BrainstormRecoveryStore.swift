import Foundation

struct RecoverableBrainstormIntent: Codable, Equatable, Sendable {
    let ownerAccountID: String
    let sessionID: String
    let request: CreateBrainstormRequest
    var brainstormID: String?
    let createdAt: Date
}

/// Keeps the exact idempotent synthesis request until the backend reaches a
/// terminal state. The record contains text-free identifiers and settings;
/// transcript content and raw audio are never stored here.
actor BrainstormRecoveryStore {
    static let shared = BrainstormRecoveryStore()

    private let fileURL: URL
    private var records: [RecoverableBrainstormIntent]
    private var loadFailed: Bool

    init(fileURL: URL? = nil) {
        self.fileURL = fileURL ?? Self.defaultFileURL()
        let loaded = Self.load(from: self.fileURL)
        records = loaded.records
        loadFailed = loaded.failed
    }

    func validateIntegrity() throws {
        if loadFailed { throw BrainstormRecoveryError.unreadableStore }
    }

    func save(_ intent: RecoverableBrainstormIntent) throws {
        try validateIntegrity()
        guard !intent.ownerAccountID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw BrainstormRecoveryError.missingAccountIdentity
        }
        if let index = records.firstIndex(where: {
            $0.ownerAccountID == intent.ownerAccountID && $0.sessionID == intent.sessionID
        }) {
            guard records[index].request == intent.request else {
                throw BrainstormRecoveryError.conflictingRequest
            }
            if let brainstormID = intent.brainstormID {
                records[index].brainstormID = brainstormID
                try persist()
            }
            return
        }
        guard !records.contains(where: { $0.ownerAccountID == intent.ownerAccountID }) else {
            throw BrainstormRecoveryError.unfinishedSession
        }
        records.append(intent)
        try persist()
    }

    func intent(sessionID: String, ownerAccountID: String) throws -> RecoverableBrainstormIntent? {
        try validateIntegrity()
        return records.first {
            $0.ownerAccountID == ownerAccountID && $0.sessionID == sessionID
        }
    }

    func all(ownerAccountID: String) throws -> [RecoverableBrainstormIntent] {
        try validateIntegrity()
        return records
            .filter { $0.ownerAccountID == ownerAccountID }
            .sorted { $0.createdAt < $1.createdAt }
    }

    func bind(sessionID: String, brainstormID: String, ownerAccountID: String) throws {
        try validateIntegrity()
        guard let index = records.firstIndex(where: {
            $0.ownerAccountID == ownerAccountID && $0.sessionID == sessionID
        }) else { return }
        guard records[index].brainstormID != brainstormID else { return }
        records[index].brainstormID = brainstormID
        try persist()
    }

    func remove(sessionID: String, ownerAccountID: String) throws {
        try validateIntegrity()
        let originalCount = records.count
        records.removeAll {
            $0.ownerAccountID == ownerAccountID && $0.sessionID == sessionID
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
    ) -> (records: [RecoverableBrainstormIntent], failed: Bool) {
        guard FileManager.default.fileExists(atPath: url.path) else { return ([], false) }
        do {
            let data = try Data(contentsOf: url)
            return (
                try JSONDecoder().decode([RecoverableBrainstormIntent].self, from: data),
                false
            )
        } catch {
            return ([], true)
        }
    }

    private static func defaultFileURL() -> URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("SixSentencesCompanion", isDirectory: true)
            .appendingPathComponent("pending-brainstorms-v2.json")
    }
}

enum BrainstormRecoveryError: LocalizedError {
    case unreadableStore
    case conflictingRequest
    case unfinishedSession
    case missingAccountIdentity

    var errorDescription: String? {
        switch self {
        case .unreadableStore:
            "Local brainstorm recovery data could not be read. It was left untouched."
        case .conflictingRequest:
            "A brainstorm session cannot be reused with a different synthesis request."
        case .unfinishedSession:
            "Finish recovering the previous brainstorm before starting another one."
        case .missingAccountIdentity:
            "Connect to a verified SixSentences account before recovering a brainstorm."
        }
    }
}
