import Foundation

enum CompanionExperienceMode: String, CaseIterable, Equatable, Sendable {
    case conversation
    case brainstorm
}

enum BrainstormOutputLanguage: String, CaseIterable, Codable, Equatable, Sendable {
    case german = "de"
    case english = "en"
}

struct LiveThought: Equatable, Identifiable, Sendable {
    let id: String
    let channel: SegmentChannel
    let text: String
    let startMS: Int64
    let endMS: Int64

    var speaker: String { channel.speakerLabel }
}

struct CreateBrainstormRequest: Codable, Equatable, Sendable {
    let clientRequestID: String
    let outputLanguage: BrainstormOutputLanguage
    let contextThroughSequence: Int
    let schemaVersion: Int

    init(
        clientRequestID: String,
        outputLanguage: BrainstormOutputLanguage,
        contextThroughSequence: Int,
        schemaVersion: Int = 1
    ) {
        self.clientRequestID = clientRequestID
        self.outputLanguage = outputLanguage
        self.contextThroughSequence = contextThroughSequence
        self.schemaVersion = schemaVersion
    }

    enum CodingKeys: String, CodingKey {
        case clientRequestID = "client_request_id"
        case outputLanguage = "output_language"
        case contextThroughSequence = "context_through_sequence"
        case schemaVersion = "schema_version"
    }
}

struct BrainstormTheme: Codable, Equatable, Identifiable, Sendable {
    let title: String
    let description: String

    var id: String { "\(title)\u{1f}\(description)" }
}

struct BrainstormIdea: Codable, Equatable, Identifiable, Sendable {
    let title: String
    let description: String

    var id: String { "\(title)\u{1f}\(description)" }
}

struct BrainstormNextStep: Codable, Equatable, Identifiable, Sendable {
    let action: String
    let owner: String?

    var id: String { "\(action)\u{1f}\(owner ?? "")" }
}

struct BrainstormEvidence: Codable, Equatable, Identifiable, Sendable {
    let kind: String
    let index: Int
    let segmentID: String
    let quote: String

    var id: String { "\(kind)\u{1f}\(index)\u{1f}\(segmentID)\u{1f}\(quote)" }

    enum CodingKeys: String, CodingKey {
        case kind, index, quote
        case segmentID = "segment_id"
    }
}

struct BrainstormResult: Codable, Equatable, Sendable {
    let summary: String
    let themes: [BrainstormTheme]
    let ideas: [BrainstormIdea]
    let openQuestions: [String]
    let decisions: [String]
    let nextSteps: [BrainstormNextStep]
    let evidence: [BrainstormEvidence]

    enum CodingKeys: String, CodingKey {
        case summary, themes, ideas, decisions, evidence
        case openQuestions = "open_questions"
        case nextSteps = "next_steps"
    }
}

struct BrainstormReceipt: Codable, Equatable, Identifiable, Sendable {
    let id: String
    let clientRequestID: String
    let status: String
    let outputLanguage: BrainstormOutputLanguage
    let contextThroughSequence: Int
    let result: BrainstormResult?
    let error: String?
    let errorCode: String?
    let schemaVersion: Int
    let createdAt: String

    enum CodingKeys: String, CodingKey {
        case id, status, result, error
        case clientRequestID = "client_request_id"
        case outputLanguage = "output_language"
        case contextThroughSequence = "context_through_sequence"
        case errorCode = "error_code"
        case schemaVersion = "schema_version"
        case createdAt = "created_at"
    }
}

struct BrainstormListResponse: Codable, Equatable, Sendable {
    let brainstorms: [BrainstormReceipt]
}

enum BrainstormSubmissionPolicy {
    static func canStructure(
        experienceMode: CompanionExperienceMode?,
        sessionStatus: String?,
        hasFinalTranscript: Bool,
        isSubmitting: Bool
    ) -> Bool {
        experienceMode == .brainstorm
            && sessionStatus == "completed"
            && hasFinalTranscript
            && !isSubmitting
    }

    static func shouldContinuePolling(status: String) -> Bool {
        status == "pending"
    }
}

struct BrainstormRecoveryContext: Equatable {
    let accountID: String?
    let isConnected: Bool
}

enum BrainstormAccountTransitionPolicy {
    static func shouldScrub(
        previous: BrainstormRecoveryContext,
        current: BrainstormRecoveryContext
    ) -> Bool {
        previous.accountID != current.accountID || current.accountID == nil
    }

    static func recoveryAccountID(for context: BrainstormRecoveryContext) -> String? {
        guard context.isConnected else { return nil }
        let clean = context.accountID?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return clean.isEmpty ? nil : clean
    }
}

enum CompanionSessionProjectPolicy {
    static func projectID(
        experienceMode: CompanionExperienceMode?,
        conversationProjectID: Int?,
        brainstormProjectID: Int?,
        availableProjects: [LiveProjectSummary]
    ) -> Int? {
        let selected = experienceMode == .brainstorm
            ? brainstormProjectID
            : conversationProjectID
        guard let selected,
              availableProjects.contains(where: { $0.id == selected })
        else { return nil }
        return selected
    }
}

enum BrainstormProjectCopy {
    static func label(usesGerman: Bool) -> String {
        usesGerman ? "Brainstorm-Projekt" : "Brainstorm project"
    }

    static func noProject(usesGerman: Bool) -> String {
        usesGerman ? "Ohne Projekt" : "No project"
    }

    static func help(usesGerman: Bool) -> String {
        usesGerman
            ? "Speichert den nächsten Brainstorm optional in einem vorhandenen Projekt."
            : "Optionally saves the next brainstorm in an existing project."
    }
}

enum BrainstormWebHandoffPolicy {
    static func destination(
        purpose: String?,
        webURL: String?,
        communityOrigin: CommunityOrigin?
    ) -> URL? {
        guard purpose == CompanionExperienceMode.brainstorm.rawValue,
              let rawValue = webURL?.trimmingCharacters(in: .whitespacesAndNewlines),
              !rawValue.isEmpty,
              let url = communityOrigin?.validatedWebURL(rawValue)
        else { return nil }
        return url
    }

    static func shouldOpenAtSessionStart(
        preferenceEnabled: Bool,
        experienceMode: CompanionExperienceMode?
    ) -> Bool {
        preferenceEnabled && experienceMode != .brainstorm
    }

    static func shouldOpenAfterCompletion(purpose: String?) -> Bool {
        purpose == CompanionExperienceMode.brainstorm.rawValue
    }
}

enum BrainstormCaptureLimits {
    /// Leaves headroom below the backend's 500k-character immutable snapshot
    /// bound for Unicode/counting differences and the final recognition flush.
    static let automaticStopCharacters = 480_000

    static func shouldStop(finalCharacterCount: Int) -> Bool {
        finalCharacterCount >= automaticStopCharacters
    }
}

enum CompanionCapturePolicy {
    static func accepts(
        channel: SegmentChannel,
        experienceMode: CompanionExperienceMode?
    ) -> Bool {
        experienceMode != .brainstorm || channel == .microphone
    }
}

enum CompanionTranscriptPersistencePolicy {
    static func sessionID(for state: CompanionSessionState) -> String? {
        switch state {
        case let .recording(sessionID), let .stopping(sessionID): sessionID
        default: nil
        }
    }
}

enum BrainstormCutoffPolicy {
    static func confirmed(
        uploadedThrough: Int?,
        lastConfirmed: Int?,
        completedThrough: Int?,
        serverLastSegment: Int?
    ) -> Int? {
        [uploadedThrough, lastConfirmed, completedThrough, serverLastSegment]
            .compactMap { $0 }
            .filter { $0 >= 1 }
            .max()
    }
}

enum BrainstormMarkdownExporter {
    static func render(
        _ result: BrainstormResult,
        language: BrainstormOutputLanguage
    ) -> String {
        let labels: (summary: String, themes: String, ideas: String, questions: String,
                     decisions: String, steps: String, evidence: String, owner: String) = language == .german
            ? ("Kurzfassung", "Themen", "Ideen", "Offene Fragen", "Entscheidungen",
               "Nächste Schritte", "Belege", "Verantwortlich")
            : ("Summary", "Themes", "Ideas", "Open questions", "Decisions",
               "Next steps", "Evidence", "Owner")
        var lines = ["# Brainstorm", "", "## \(labels.summary)", "", result.summary]
        appendRichSection(labels.themes, items: result.themes.map { ($0.title, $0.description) }, to: &lines)
        appendRichSection(labels.ideas, items: result.ideas.map { ($0.title, $0.description) }, to: &lines)
        appendStringSection(labels.questions, items: result.openQuestions, to: &lines)
        appendStringSection(labels.decisions, items: result.decisions, to: &lines)
        lines.append(contentsOf: ["", "## \(labels.steps)", ""])
        for step in result.nextSteps {
            let owner = step.owner.map { " — \(labels.owner): \($0)" } ?? ""
            lines.append("- \(step.action)\(owner)")
        }
        lines.append(contentsOf: ["", "## \(labels.evidence)", ""])
        for source in result.evidence {
            lines.append("- [\(source.kind) \(source.index + 1)] “\(source.quote)” (`\(source.segmentID)`)")
        }
        return lines.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines) + "\n"
    }

    private static func appendRichSection(
        _ title: String,
        items: [(String, String)],
        to lines: inout [String]
    ) {
        lines.append(contentsOf: ["", "## \(title)", ""])
        for item in items {
            lines.append("- **\(item.0)** — \(item.1)")
        }
    }

    private static func appendStringSection(
        _ title: String,
        items: [String],
        to lines: inout [String]
    ) {
        lines.append(contentsOf: ["", "## \(title)", ""])
        lines.append(contentsOf: items.map { "- \($0)" })
    }
}

enum BrainstormErrorCopy {
    static func localized(code: String?, usesGerman: Bool) -> String {
        if usesGerman {
            return german(for: code)
                ?? "Die Brainstorm-Anfrage ist fehlgeschlagen. Dein finaler Text bleibt gespeichert."
        }
        return english(for: code)
            ?? "The brainstorm request failed. Your finalized text remains saved."
    }

    static func german(for code: String?) -> String? {
        return switch code {
        case "brainstorm_request_conflict":
            "Diese Strukturierungsanfrage wurde bereits mit anderen Einstellungen verwendet."
        case "no_live_transcript", "no_brainstorm_transcript":
            "Es gibt noch keinen sicher gespeicherten Text zum Strukturieren."
        case "capacity_unavailable":
            "Diese Anfrage überschreitet eine vom Betreiber konfigurierte Kapazitätsgrenze. Versuche es später erneut."
        case "brainstorm_unavailable", "ask_queue_unavailable":
            "Die Brainstorm-KI ist gerade nicht verfügbar. Dein finaler Text bleibt sicher gespeichert."
        case "brainstorm_in_progress":
            "Für diesen Transkriptstand läuft bereits eine Strukturierung. Warte kurz auf das Ergebnis."
        case "brainstorm_session_unavailable":
            "Diese Brainstorm-Sitzung ist nicht mehr verfügbar. Dein lokal gespeicherter Text bleibt erhalten."
        case "brainstorm_microphone_only":
            "Ein Brainstorm darf ausschließlich Text aus deinem Mikrofon enthalten."
        case "brainstorm_cutoff_mismatch", "invalid_brainstorm_cutoff":
            "Der bestätigte Transkriptstand passt nicht mehr. Lade den Sitzungsstatus neu."
        case "context_not_available":
            "Der finale Text ist noch nicht vollständig synchronisiert. Versuche es gleich erneut."
        case "brainstorm_too_large":
            "Dieser Brain Dump überschreitet das sichere Verarbeitungslimit. Teile ihn in zwei Sitzungen auf."
        case "brainstorm_too_many_segments":
            "Dieser Brain Dump enthält zu viele einzelne Abschnitte. Starte für weitere Gedanken eine neue Sitzung."
        case "brainstorm_is_solo":
            "Dieser Brainstorm ist eine private Solo-Sitzung und kann nicht als Gespräch verarbeitet werden."
        case "session_unavailable":
            "Diese Brainstorm-Sitzung ist nicht mehr verfügbar. Dein lokal gesicherter finaler Text bleibt erhalten."
        case "brainstorm_result_not_grounded":
            "Die erzeugte Struktur konnte nicht zuverlässig am Transkript belegt werden. Es wurde nichts Ungeprüftes angezeigt."
        case "brainstorm_result_unavailable":
            "Das strukturierte Ergebnis konnte nicht sicher geladen werden. Dein finaler Text bleibt gespeichert."
        case "brainstorm_cancelled":
            "Die Strukturierung wurde abgebrochen."
        case "brainstorm_snapshot_conflict":
            "Der eingefrorene Transkriptstand steht bereits mit anderen Einstellungen in Verarbeitung."
        case "session_not_brainstorm":
            "Diese Sitzung wurde nicht als privater Brainstorm gestartet."
        case "brainstorm_complete_payload_required":
            "Zum Strukturieren fehlt der bestätigte Transkriptstand. Synchronisiere die Sitzung und versuche es erneut."
        default:
            nil
        }
    }

    static func english(for code: String?) -> String? {
        return switch code {
        case "brainstorm_request_conflict":
            "This structuring request was already used with different settings."
        case "no_live_transcript", "no_brainstorm_transcript":
            "There is no safely saved finalized text to organize yet."
        case "capacity_unavailable":
            "This request exceeds an operator-configured capacity limit. Try again later."
        case "brainstorm_unavailable", "ask_queue_unavailable":
            "Brainstorm AI is currently unavailable. Your finalized text remains safely saved."
        case "brainstorm_in_progress":
            "A structuring run is already in progress for this transcript snapshot."
        case "brainstorm_session_unavailable", "session_unavailable":
            "This brainstorm session is no longer available. Your locally saved finalized text remains intact."
        case "brainstorm_microphone_only":
            "A private brainstorm may contain microphone transcript text only."
        case "brainstorm_cutoff_mismatch", "invalid_brainstorm_cutoff":
            "The confirmed transcript snapshot no longer matches. Refresh the session status."
        case "context_not_available":
            "The finalized text has not fully synchronized yet. Try again shortly."
        case "brainstorm_too_large":
            "This brain dump exceeds the safe processing limit. Split it into two sessions."
        case "brainstorm_too_many_segments":
            "This brain dump contains too many separate segments. Start a new session for additional thoughts."
        case "brainstorm_is_solo":
            "This is a private solo brainstorm and cannot be processed as a conversation."
        case "brainstorm_result_not_grounded":
            "The structure could not be grounded reliably in the transcript, so no unverified result was shown."
        case "brainstorm_result_unavailable":
            "The structured result could not be loaded safely. Your finalized text remains saved."
        case "brainstorm_cancelled":
            "Structuring was cancelled."
        case "brainstorm_snapshot_conflict":
            "This frozen transcript snapshot is already being processed with different settings."
        case "session_not_brainstorm":
            "This session was not started as a private brainstorm."
        case "brainstorm_complete_payload_required":
            "The confirmed transcript snapshot is missing. Synchronize the session and try again."
        default:
            nil
        }
    }
}
