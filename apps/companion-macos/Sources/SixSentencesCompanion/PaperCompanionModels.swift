import CryptoKit
import Foundation

enum PaperCompanionLimits {
    static let maximumPDFBytes = 25 * 1024 * 1024
    static let maximumSelectionCharacters = 1_200
}

enum PaperCompanionError: LocalizedError, Equatable {
    case fileOnly
    case pdfOnly
    case emptyPDF
    case pdfTooLarge
    case unreadablePDF
    case selectionSpansPages
    case selectionTooShort
    case invalidEventStream

    var errorDescription: String? {
        switch self {
        case .fileOnly:
            "Choose a local PDF file."
        case .pdfOnly:
            "Paper Companion accepts PDF files only."
        case .emptyPDF:
            "This PDF is empty."
        case .pdfTooLarge:
            "PDFs are limited to 25 MiB."
        case .unreadablePDF:
            "This PDF could not be read."
        case .selectionSpansPages:
            "Select text on one page so SixSentences can verify the passage."
        case .selectionTooShort:
            "Select at least three characters."
        case .invalidEventStream:
            "SixSentences returned an invalid chat stream."
        }
    }
}

struct ValidatedLocalPDF: Equatable, Sendable {
    let filename: String
    let data: Data
    let sha256: String
}

enum PaperFileValidator {
    static func read(url: URL) throws -> ValidatedLocalPDF {
        guard url.isFileURL else { throw PaperCompanionError.fileOnly }
        let hasScopedAccess = url.startAccessingSecurityScopedResource()
        defer {
            if hasScopedAccess { url.stopAccessingSecurityScopedResource() }
        }
        let values = try url.resourceValues(forKeys: [.isRegularFileKey, .fileSizeKey])
        guard values.isRegularFile == true else { throw PaperCompanionError.fileOnly }
        if let fileSize = values.fileSize,
           fileSize > PaperCompanionLimits.maximumPDFBytes {
            throw PaperCompanionError.pdfTooLarge
        }
        let data: Data
        do {
            // Intentionally copy bytes while the explicit user-granted URL is
            // accessible. No URL, bookmark, or local path is persisted.
            data = try Data(contentsOf: url, options: [])
        } catch {
            throw PaperCompanionError.unreadablePDF
        }
        return try validate(data: data, filename: url.lastPathComponent)
    }

    static func validate(data: Data, filename: String) throws -> ValidatedLocalPDF {
        let cleanFilename = URL(fileURLWithPath: filename).lastPathComponent
        guard cleanFilename.lowercased().hasSuffix(".pdf") else {
            throw PaperCompanionError.pdfOnly
        }
        guard !data.isEmpty else { throw PaperCompanionError.emptyPDF }
        guard data.count <= PaperCompanionLimits.maximumPDFBytes else {
            throw PaperCompanionError.pdfTooLarge
        }
        guard data.starts(with: Data("%PDF-".utf8)) else {
            throw PaperCompanionError.pdfOnly
        }
        return ValidatedLocalPDF(
            filename: cleanFilename,
            data: data,
            sha256: SHA256Digest.hex(data)
        )
    }
}

enum PaperCompanionWorkspaceState: Equatable, Sendable {
    case empty
    case loading
    case ready
    case asking
    case failed
}

enum PaperCompanionWorkspaceEvent: Sendable {
    case openRequested
    case openSucceeded
    case openFailed
    case askStarted
    case askFinished
    case askFailed
    case reset
}

struct PaperCompanionWorkspaceMachine: Sendable {
    private(set) var state: PaperCompanionWorkspaceState = .empty

    @discardableResult
    mutating func apply(_ event: PaperCompanionWorkspaceEvent) -> PaperCompanionWorkspaceState {
        switch (state, event) {
        case (_, .openRequested): state = .loading
        case (.loading, .openSucceeded): state = .ready
        case (.loading, .openFailed): state = .failed
        case (.ready, .askStarted): state = .asking
        case (.asking, .askFinished), (.asking, .askFailed): state = .ready
        case (_, .reset): state = .empty
        default: break
        }
        return state
    }
}

enum PaperSelectionMapper {
    static func make(
        quote rawQuote: String?,
        zeroBasedPageIndexes: [Int]
    ) throws -> PaperChatSelection? {
        let quote = (rawQuote ?? "")
            .replacingOccurrences(of: "\u{00AD}", with: "")
            .replacingOccurrences(of: "\r\n", with: "\n")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard !quote.isEmpty else { return nil }
        guard quote.count >= 3 else { throw PaperCompanionError.selectionTooShort }
        let pages = Array(Set(zeroBasedPageIndexes)).sorted()
        guard pages.count == 1, let pageIndex = pages.first, pageIndex >= 0 else {
            throw PaperCompanionError.selectionSpansPages
        }
        return PaperChatSelection(
            page: pageIndex + 1,
            quote: String(quote.prefix(PaperCompanionLimits.maximumSelectionCharacters))
        )
    }
}

private enum SHA256Digest {
    static func hex(_ data: Data) -> String {
        // Kept behind a tiny boundary so file validation stays deterministic
        // and independently testable.
        let digest = SHA256.hash(data: data)
        return digest.map { String(format: "%02x", $0) }.joined()
    }
}

struct CreatePaperChatRequest: Codable, Equatable, Sendable {
    let clientRequestID: String
    let filename: String
    let contentBase64: String
    let sha256: String
    let projectID: Int?

    enum CodingKeys: String, CodingKey {
        case filename, sha256
        case clientRequestID = "client_request_id"
        case contentBase64 = "content_base64"
        case projectID = "project_id"
    }
}

struct PaperChatPaperSummary: Codable, Equatable, Sendable {
    let libraryDocumentID: Int
    let chatDocumentID: Int
    let title: String
    let workID: String
    let verified: Bool
    let textStatus: String
    let byteSize: Int
    let metadataFieldsAdded: [String]
    let warnings: [String]

    enum CodingKeys: String, CodingKey {
        case title, verified, warnings
        case libraryDocumentID = "library_document_id"
        case chatDocumentID = "chat_document_id"
        case workID = "work_id"
        case textStatus = "text_status"
        case byteSize = "byte_size"
        case metadataFieldsAdded = "metadata_fields_added"
    }
}

struct PaperChatSummary: Codable, Equatable, Sendable {
    struct Chat: Codable, Equatable, Sendable {
        let id: String
        let title: String
        let webURL: String

        enum CodingKeys: String, CodingKey {
            case id, title
            case webURL = "web_url"
        }
    }

    let clientRequestID: String
    let status: String
    let paper: PaperChatPaperSummary
    let chat: Chat

    enum CodingKeys: String, CodingKey {
        case status, paper, chat
        case clientRequestID = "client_request_id"
    }
}

struct PaperChatHistoryMessage: Codable, Equatable, Identifiable, Sendable {
    let id: Int?
    let role: String
    let content: String
    let citations: [PaperChatCitation]
    let createdAt: String

    enum CodingKeys: String, CodingKey {
        case id, role, content, citations
        case createdAt = "created_at"
    }
}

struct PaperChatCitation: Codable, Equatable, Sendable {
    let id: String?
    let title: String?
    let label: String?

    init(from decoder: Decoder) throws {
        if let text = try? decoder.singleValueContainer().decode(String.self) {
            id = nil
            title = nil
            label = text
            return
        }
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decodeIfPresent(String.self, forKey: .id)
        title = try container.decodeIfPresent(String.self, forKey: .title)
        label = nil
    }

    func encode(to encoder: Encoder) throws {
        if let label, id == nil, title == nil {
            var container = encoder.singleValueContainer()
            try container.encode(label)
            return
        }
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encodeIfPresent(id, forKey: .id)
        try container.encodeIfPresent(title, forKey: .title)
    }

    private enum CodingKeys: String, CodingKey { case id, title }
}

struct PaperChatSelection: Codable, Equatable, Sendable {
    let page: Int
    let quote: String
}

struct PaperChatTurnRequest: Codable, Equatable, Sendable {
    let turnID: String
    let question: String
    let selection: PaperChatSelection?
    let model: String?

    enum CodingKeys: String, CodingKey {
        case question, selection, model
        case turnID = "turn_id"
    }
}

struct PaperChatEvidence: Codable, Equatable, Sendable {
    let workID: String
    let documentID: Int
    let page: Int
    let quote: String

    enum CodingKeys: String, CodingKey {
        case page, quote
        case workID = "work_id"
        case documentID = "document_id"
    }
}

struct PaperChatAnswer: Codable, Equatable, Sendable {
    let answer: String
    let citations: [PaperChatCitation]?
    let evidence: [PaperChatEvidence]?
}

struct PaperChatTurnState: Codable, Equatable, Sendable {
    let turnID: String
    let status: String
    let lastEventID: Int
    let answer: PaperChatAnswer?
    let errorMessage: String?
    let createdAt: String?
    let startedAt: String?
    let finishedAt: String?

    enum CodingKeys: String, CodingKey {
        case status, answer
        case turnID = "turn_id"
        case lastEventID = "last_event_id"
        case errorMessage = "error_message"
        case createdAt = "created_at"
        case startedAt = "started_at"
        case finishedAt = "finished_at"
    }
}

struct PaperChatTurnStopResponse: Codable, Equatable, Sendable {
    let turnID: String
    let status: String

    enum CodingKeys: String, CodingKey {
        case status
        case turnID = "turn_id"
    }
}

struct PaperChatStreamEvent: Codable, Equatable, Sendable {
    let id: Int
    let event: String
    let turnID: String
    let delta: String?
    let reasoning: String?
    let answer: PaperChatAnswer?
    let message: String?
    let label: String?

    enum CodingKeys: String, CodingKey {
        case id, event, delta, reasoning, answer, message, label
        case turnID = "turn_id"
    }

    var isTerminal: Bool {
        ["turn.completed", "turn.failed", "turn.cancelled"].contains(event)
    }
}

struct PaperChatStreamResult: Equatable, Sendable {
    let lastEventID: Int
    let terminalEvent: PaperChatStreamEvent?
}

struct PaperChatSSEParser: Sendable {
    private var buffer = ""
    private(set) var lastEventID: Int
    private let turnID: String
    private let decoder = JSONDecoder()

    init(turnID: String, afterEventID: Int = 0) {
        self.turnID = turnID
        lastEventID = afterEventID
    }

    mutating func append(_ data: Data, final: Bool = false) -> [PaperChatStreamEvent] {
        buffer += String(decoding: data, as: UTF8.self).replacingOccurrences(of: "\r\n", with: "\n")
        var events: [PaperChatStreamEvent] = []
        while let boundary = buffer.range(of: "\n\n") {
            let frame = String(buffer[..<boundary.lowerBound])
            buffer.removeSubrange(..<boundary.upperBound)
            if let event = parse(frame) { events.append(event) }
        }
        if final, !buffer.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            let frame = buffer
            buffer = ""
            if let event = parse(frame) { events.append(event) }
        }
        return events
    }

    private mutating func parse(_ frame: String) -> PaperChatStreamEvent? {
        guard !frame.isEmpty, !frame.hasPrefix(":") else { return nil }
        var eventName = ""
        var streamID: Int?
        var dataLines: [String] = []
        for line in frame.split(separator: "\n", omittingEmptySubsequences: false) {
            if line.hasPrefix("id:") {
                streamID = Int(line.dropFirst(3).trimmingCharacters(in: .whitespaces))
            } else if line.hasPrefix("event:") {
                eventName = line.dropFirst(6).trimmingCharacters(in: .whitespaces)
            } else if line.hasPrefix("data:") {
                dataLines.append(String(line.dropFirst(5)).trimmingCharacters(in: .whitespaces))
            }
        }
        guard
            let streamID,
            streamID > lastEventID,
            !eventName.isEmpty,
            !dataLines.isEmpty,
            let data = dataLines.joined(separator: "\n").data(using: .utf8),
            let event = try? decoder.decode(PaperChatStreamEvent.self, from: data),
            event.id == streamID,
            event.event == eventName,
            event.turnID == turnID
        else { return nil }
        lastEventID = streamID
        return event
    }
}
