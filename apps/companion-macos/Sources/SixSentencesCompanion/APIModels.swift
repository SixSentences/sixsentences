import Foundation

enum SegmentChannel: String, Codable, CaseIterable, Sendable {
    case microphone
    case system

    var speakerLabel: String {
        switch self {
        case .microphone: "You"
        case .system: "Others"
        }
    }
}

struct ConsentPayload: Codable, Equatable, Sendable {
    let participantsNotified: Bool
    let noticeText: String

    enum CodingKeys: String, CodingKey {
        case participantsNotified = "participants_notified"
        case noticeText = "notice_text"
    }
}

struct CreateLiveSessionRequest: Codable, Equatable, Sendable {
    let clientSessionID: String
    let title: String
    let projectID: Int?
    let language: String
    let purpose: String
    let consent: ConsentPayload?

    init(
        clientSessionID: String,
        title: String,
        projectID: Int?,
        language: String,
        purpose: String = "conversation",
        consent: ConsentPayload?
    ) {
        self.clientSessionID = clientSessionID
        self.title = title
        self.projectID = projectID
        self.language = language
        self.purpose = purpose
        self.consent = consent
    }

    enum CodingKeys: String, CodingKey {
        case clientSessionID = "client_session_id"
        case title
        case projectID = "project_id"
        case language, purpose
        case consent
    }
}

struct LiveProjectSummary: Codable, Equatable, Identifiable, Sendable {
    let id: Int
    let name: String
}

struct LiveSession: Codable, Equatable, Identifiable, Sendable {
    let id: String
    let title: String
    let projectID: Int?
    let project: LiveProjectSummary?
    let status: String
    let language: String
    let purpose: String?
    let startedAt: String?
    let endedAt: String?
    let durationMS: Int64
    let maxDurationMS: Int64?
    let consent: ConsentPayload?
    let segmentCount: Int
    let askCount: Int
    let interviewID: String?
    let webURL: String
    let revision: Int
    let lastSegmentSequence: Int?
    let completedThroughSequence: Int?
    let transcriptCharacterCount: Int?
    let failureReason: String?
    let createdAt: String
    let updatedAt: String

    enum CodingKeys: String, CodingKey {
        case id, title, project, status, language, purpose, consent, revision
        case projectID = "project_id"
        case startedAt = "started_at"
        case endedAt = "ended_at"
        case durationMS = "duration_ms"
        case maxDurationMS = "max_duration_ms"
        case segmentCount = "segment_count"
        case askCount = "ask_count"
        case interviewID = "interview_id"
        case webURL = "web_url"
        case lastSegmentSequence = "last_segment_sequence"
        case completedThroughSequence = "completed_through_sequence"
        case transcriptCharacterCount = "transcript_char_count"
        case failureReason = "failure_reason"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }
}

struct FinalTranscriptSegment: Codable, Equatable, Identifiable, Sendable {
    let clientEventID: String
    let channel: SegmentChannel
    let speaker: String
    let startMS: Int64
    let endMS: Int64
    let text: String
    let isFinal: Bool

    var id: String { clientEventID }

    enum CodingKeys: String, CodingKey {
        case channel, speaker, text
        case clientEventID = "client_event_id"
        case startMS = "start_ms"
        case endMS = "end_ms"
        case isFinal = "is_final"
    }
}

struct SegmentBatchRequest: Codable, Equatable, Sendable {
    let segments: [FinalTranscriptSegment]
}

struct SegmentBatchResponse: Codable, Equatable, Sendable {
    let accepted: Int?
    let duplicates: Int?
    let revision: Int?
    let lastEventSequence: Int?
    let transcriptCharacterCount: Int?

    enum CodingKeys: String, CodingKey {
        case accepted, duplicates, revision
        case lastEventSequence = "last_event_sequence"
        case transcriptCharacterCount = "transcript_char_count"
    }
}

struct AskLiveSessionRequest: Codable, Equatable, Sendable {
    let clientRequestID: String
    let question: String
    let contextThroughSequence: Int

    enum CodingKeys: String, CodingKey {
        case clientRequestID = "client_request_id"
        case question
        case contextThroughSequence = "context_through_sequence"
    }
}

struct TranscriptSourceReceipt: Codable, Equatable, Identifiable, Sendable {
    let segmentID: String
    let speaker: String
    let quote: String?
    let startMS: Int64?
    let endMS: Int64?

    var id: String { segmentID }

    enum CodingKeys: String, CodingKey {
        case speaker, quote
        case segmentID = "segment_id"
        case startMS = "start_ms"
        case endMS = "end_ms"
    }
}

struct ProjectSourceReceipt: Codable, Equatable, Identifiable, Sendable {
    let type: String?
    let id: String?
    let title: String?
    let locator: String?
    let quote: String?
}

struct AskLiveSessionResponse: Codable, Equatable, Identifiable, Sendable {
    let id: String
    let clientRequestID: String
    let question: String
    let answer: String
    let transcriptSources: [TranscriptSourceReceipt]
    let projectSources: [ProjectSourceReceipt]
    let createdAt: String?
    let status: String?
    let error: String?

    enum CodingKeys: String, CodingKey {
        case id, question, answer, status, error
        case clientRequestID = "client_request_id"
        case transcriptSources = "transcript_sources"
        case projectSources = "project_sources"
        case createdAt = "created_at"
    }
}

struct CompleteLiveSessionResponse: Codable, Equatable, Sendable {
    let session: LiveSession
    let interviewID: String?
    let analysisStatus: String?
    let brainstorm: BrainstormReceipt?

    enum CodingKeys: String, CodingKey {
        case session, brainstorm
        case interviewID = "interview_id"
        case analysisStatus = "analysis_status"
    }
}

struct LiveLimits: Codable, Equatable, Sendable {
    let maxBatchSegments: Int?
    let maxSegmentCharacters: Int?
    let maxSessionDurationMS: Int64?

    enum CodingKeys: String, CodingKey {
        case maxBatchSegments = "max_batch_segments"
        case maxSegmentCharacters = "max_segment_characters"
        case maxSessionDurationMS = "max_session_duration_ms"
    }
}

struct DesktopAppConfig: Codable, Equatable, Sendable {
    let minimumVersion: String?

    enum CodingKeys: String, CodingKey {
        case minimumVersion = "minimum_version"
    }
}

struct LiveConfig: Codable, Equatable, Sendable {
    let accountID: String
    let enabled: Bool
    let maxSessionMinutes: Int?
    let maxBatchSegments: Int?
    let maxSegmentCharacters: Int?
    let pollIntervalMS: Int?
    let projects: [LiveProjectSummary]
    let desktop: DesktopAppConfig?
    let brainstorm: BrainstormLiveConfig?

    enum CodingKeys: String, CodingKey {
        case enabled, projects, desktop, brainstorm
        case accountID = "account_id"
        case maxSessionMinutes = "max_session_minutes"
        case maxBatchSegments = "max_batch_segments"
        case maxSegmentCharacters = "max_segment_chars"
        case pollIntervalMS = "poll_interval_ms"
    }
}

struct BrainstormLiveConfig: Codable, Equatable, Sendable {
    let maxTranscriptCharacters: Int

    enum CodingKeys: String, CodingKey {
        case maxTranscriptCharacters = "max_transcript_chars"
    }
}

struct CancelLiveSessionResponse: Codable, Equatable, Sendable {
    let id: String
    let status: String
}

struct APIErrorEnvelope: Codable, Sendable {
    let detail: String?
}

struct PairExchangeRequest: Codable, Equatable, Sendable {
    let code: String
    let codeVerifier: String

    enum CodingKeys: String, CodingKey {
        case code
        case codeVerifier = "code_verifier"
    }
}

struct PairExchangeResponse: Codable, Equatable, Sendable {
    let apiKey: String
    let scopes: [String]
    let expiresInDays: Int

    enum CodingKeys: String, CodingKey {
        case scopes
        case apiKey = "api_key"
        case expiresInDays = "expires_in_days"
    }
}
