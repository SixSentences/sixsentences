import Foundation

struct OutboxEntry: Codable, Equatable, Sendable {
    let sessionID: String
    let segment: FinalTranscriptSegment
}

enum OutboxError: LocalizedError {
    case conflictingEventID
    case unreadableStore
    case capacityExceeded

    var errorDescription: String? {
        switch self {
        case .conflictingEventID:
            "A transcript event ID was reused with different content."
        case .unreadableStore:
            "The local transcript queue could not be read. It was left untouched for recovery."
        case .capacityExceeded:
            "The local transcript safety limit was reached. Stop and synchronize this session before recording more."
        }
    }
}

actor SegmentOutbox {
    static let maximumQueuedCharactersPerSession = 500_000
    static let maximumQueuedSegmentsPerSession = 10_000
    private let fileURL: URL
    private var entries: [OutboxEntry]
    private var loadFailed: Bool

    init(fileURL: URL? = nil) {
        self.fileURL = fileURL ?? Self.defaultFileURL()
        let loaded = Self.load(from: self.fileURL)
        entries = loaded.entries
        loadFailed = loaded.failed
    }

    func validateIntegrity() throws {
        if loadFailed { throw OutboxError.unreadableStore }
    }

    @discardableResult
    func enqueue(sessionID: String, segments: [FinalTranscriptSegment]) throws -> Int {
        try validateIntegrity()
        let before = entries.count
        var candidateEntries = entries
        var existingByID = Dictionary(
            uniqueKeysWithValues: candidateEntries.map { ($0.segment.clientEventID, $0) }
        )
        var queuedCharacters = candidateEntries.lazy
            .filter { $0.sessionID == sessionID }
            .reduce(0) { $0 + $1.segment.text.count }
        var queuedSegments = candidateEntries.count { $0.sessionID == sessionID }
        for segment in segments {
            if let existing = existingByID[segment.clientEventID] {
                guard existing.sessionID == sessionID, existing.segment == segment else {
                    throw OutboxError.conflictingEventID
                }
                continue
            }
            guard queuedCharacters + segment.text.count <= Self.maximumQueuedCharactersPerSession,
                  queuedSegments + 1 <= Self.maximumQueuedSegmentsPerSession
            else { throw OutboxError.capacityExceeded }
            let entry = OutboxEntry(sessionID: sessionID, segment: segment)
            candidateEntries.append(entry)
            existingByID[segment.clientEventID] = entry
            queuedCharacters += segment.text.count
            queuedSegments += 1
        }
        let previousEntries = entries
        entries = candidateEntries
        do {
            try persist()
        } catch {
            entries = previousEntries
            throw error
        }
        return candidateEntries.count - before
    }

    func batch(sessionID: String, limit: Int) -> [FinalTranscriptSegment] {
        Array(
            entries.lazy
                .filter { $0.sessionID == sessionID }
                .prefix(max(1, limit))
                .map(\.segment)
        )
    }

    func acknowledge(eventIDs: Set<String>) throws {
        try validateIntegrity()
        entries.removeAll { eventIDs.contains($0.segment.clientEventID) }
        try persist()
    }

    func count(sessionID: String) -> Int {
        entries.count { $0.sessionID == sessionID }
    }

    func sessionIDs() -> [String] {
        Array(Set(entries.map(\.sessionID))).sorted()
    }

    func removeSession(sessionID: String) throws {
        try validateIntegrity()
        entries.removeAll { $0.sessionID == sessionID }
        try persist()
    }

    /// Deletes every locally queued finalized transcript, including an
    /// unreadable store that normal recovery deliberately leaves untouched.
    func purge() throws {
        entries.removeAll(keepingCapacity: false)
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
        let data = try JSONEncoder().encode(entries)
        try data.write(to: fileURL, options: [.atomic, .completeFileProtection])
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o600],
            ofItemAtPath: fileURL.path
        )
    }

    private static func load(from url: URL) -> (entries: [OutboxEntry], failed: Bool) {
        guard FileManager.default.fileExists(atPath: url.path) else { return ([], false) }
        do {
            let data = try Data(contentsOf: url)
            return (try JSONDecoder().decode([OutboxEntry].self, from: data), false)
        } catch {
            return ([], true)
        }
    }

    private static func defaultFileURL() -> URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        return base
            .appendingPathComponent("SixSentencesCompanion", isDirectory: true)
            .appendingPathComponent("finalized-segment-outbox-v1.json")
    }
}
