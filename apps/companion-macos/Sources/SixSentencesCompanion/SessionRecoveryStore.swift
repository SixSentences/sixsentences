import Foundation

struct RecoverableLiveSession: Codable, Equatable, Sendable {
    let clientSessionID: String
    var serverSessionID: String?
    var webURL: String?
    var completionRequested: Bool
    let createdAt: Date
}

/// Persists non-secret session identifiers so an interrupted Stop can finish
/// uploading finalized transcript text and retry the idempotent completion.
actor SessionRecoveryStore {
    private let fileURL: URL
    private var records: [RecoverableLiveSession]
    private var loadFailed: Bool

    init(fileURL: URL? = nil) {
        self.fileURL = fileURL ?? Self.defaultFileURL()
        let loaded = Self.load(from: self.fileURL)
        records = loaded.records
        loadFailed = loaded.failed
    }

    func validateIntegrity() throws {
        if loadFailed { throw RecoveryStoreError.unreadableStore }
    }

    func begin(clientSessionID: String) throws {
        try validateIntegrity()
        if records.contains(where: { $0.clientSessionID == clientSessionID }) { return }
        records.append(
            RecoverableLiveSession(
                clientSessionID: clientSessionID,
                serverSessionID: nil,
                webURL: nil,
                completionRequested: false,
                createdAt: Date()
            )
        )
        try persist()
    }

    func bind(clientSessionID: String, session: LiveSession) throws {
        try validateIntegrity()
        guard let index = records.firstIndex(where: { $0.clientSessionID == clientSessionID }) else {
            return
        }
        records[index].serverSessionID = session.id
        records[index].webURL = session.webURL
        try persist()
    }

    func requestCompletion(sessionID: String) throws {
        try validateIntegrity()
        guard let index = records.firstIndex(where: { $0.serverSessionID == sessionID }) else { return }
        records[index].completionRequested = true
        try persist()
    }

    func remove(sessionID: String) throws {
        try validateIntegrity()
        records.removeAll { $0.serverSessionID == sessionID }
        try persist()
    }

    func remove(clientSessionID: String) throws {
        try validateIntegrity()
        records.removeAll { $0.clientSessionID == clientSessionID }
        try persist()
    }

    func pendingCompletions() -> [RecoverableLiveSession] {
        records.filter { $0.completionRequested && $0.serverSessionID != nil }
    }

    func interruptedRecordings() -> [RecoverableLiveSession] {
        records.filter { !$0.completionRequested && $0.serverSessionID != nil }
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

    private static func load(from url: URL) -> (records: [RecoverableLiveSession], failed: Bool) {
        guard FileManager.default.fileExists(atPath: url.path) else { return ([], false) }
        do {
            let data = try Data(contentsOf: url)
            return (try JSONDecoder().decode([RecoverableLiveSession].self, from: data), false)
        } catch {
            return ([], true)
        }
    }

    private static func defaultFileURL() -> URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("SixSentencesCompanion", isDirectory: true)
            .appendingPathComponent("session-recovery-v1.json")
    }
}

enum RecoveryStoreError: LocalizedError {
    case unreadableStore

    var errorDescription: String? {
        "Local session recovery data could not be read. It was left untouched for recovery."
    }
}
