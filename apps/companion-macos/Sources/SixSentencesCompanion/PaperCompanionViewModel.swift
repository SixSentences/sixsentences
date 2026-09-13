import AppKit
import Foundation
import PDFKit

enum PaperCompanionLinkPolicy {
    static func destination(
        rawValue: String?,
        communityOrigin: CommunityOrigin?
    ) -> URL? {
        guard let rawValue, let communityOrigin else { return nil }
        return communityOrigin.validatedWebURL(rawValue)
    }
}

@MainActor
final class PaperCompanionViewModel: ObservableObject {
    @Published private(set) var workspaceState: PaperCompanionWorkspaceState = .empty
    @Published private(set) var document: PDFDocument?
    @Published private(set) var filename = ""
    @Published private(set) var summary: PaperChatSummary?
    @Published private(set) var history: [PaperChatHistoryMessage] = []
    @Published private(set) var selection: PaperChatSelection?
    @Published private(set) var selectionMessage = ""
    @Published private(set) var streamingAnswer = ""
    @Published private(set) var streamingReasoning = ""
    @Published private(set) var activityLabel = ""
    @Published private(set) var errorMessage = ""
    @Published private(set) var pendingQuestion = ""
    @Published private(set) var activeTurnID: String?
    @Published var question = ""

    private let preferences: CompanionPreferences
    private let credentials: any CompanionCredentialStore
    private var machine = PaperCompanionWorkspaceMachine()
    private var validatedPDF: ValidatedLocalPDF?
    private var clientRequestID = ""
    private var lastEventID = 0
    private var openGeneration = UUID()
    private var openTask: Task<Void, Never>?
    private var turnTask: Task<Void, Never>?

    init(
        preferences: CompanionPreferences? = nil,
        credentials: any CompanionCredentialStore = KeychainCredentialProvider.shared
    ) {
        self.preferences = preferences ?? .shared
        self.credentials = credentials
    }

    var hasDocument: Bool { document != nil }
    var isLoading: Bool { workspaceState == .loading }
    var isAsking: Bool { workspaceState == .asking }
    var canRetryUpload: Bool { validatedPDF != nil && !isLoading && !isAsking }
    var canAsk: Bool {
        summary != nil
            && workspaceState == .ready
            && activeTurnID == nil
            && question.trimmingCharacters(in: .whitespacesAndNewlines).count >= 2
    }

    var displayTitle: String {
        summary?.paper.title.nonEmpty ?? filename.nonEmpty ?? copy("Lokales Paper", "Local paper")
    }

    var statusLabel: String {
        switch workspaceState {
        case .empty: copy("PDF öffnen", "Open a PDF")
        case .loading: copy("Paper wird vorbereitet …", "Preparing paper…")
        case .ready: copy("Bereit", "Ready")
        case .asking: copy("Antwort wird erstellt …", "Preparing answer…")
        case .failed: copy("Aktion erforderlich", "Action required")
        }
    }

    func copy(_ german: String, _ english: String) -> String {
        SpeechLocaleResolver.usesGerman(language: "auto") ? german : english
    }

    func open(_ url: URL) {
        guard !isAsking else {
            errorMessage = copy(
                "Stoppe zuerst die laufende Antwort.",
                "Stop the current answer before opening another paper."
            )
            return
        }
        openTask?.cancel()
        turnTask?.cancel()
        let generation = UUID()
        openGeneration = generation
        machine = PaperCompanionWorkspaceMachine()
        setState(machine.apply(.openRequested))
        filename = url.lastPathComponent
        document = nil
        summary = nil
        history = []
        selection = nil
        selectionMessage = ""
        streamingAnswer = ""
        streamingReasoning = ""
        activityLabel = ""
        errorMessage = ""
        pendingQuestion = ""
        activeTurnID = nil
        validatedPDF = nil
        clientRequestID = "paper_\(UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased())"
        lastEventID = 0

        openTask = Task { [weak self] in
            guard let self else { return }
            do {
                let validated = try await Task.detached(priority: .userInitiated) {
                    try PaperFileValidator.read(url: url)
                }.value
                try Task.checkCancellation()
                guard self.openGeneration == generation else { return }
                guard let pdf = PDFDocument(data: validated.data), pdf.pageCount > 0, !pdf.isLocked else {
                    throw PaperCompanionError.unreadablePDF
                }
                self.validatedPDF = validated
                self.document = pdf
                self.filename = validated.filename
                try await self.uploadValidatedPDF(generation: generation)
            } catch is CancellationError {
                return
            } catch {
                guard self.openGeneration == generation else { return }
                self.errorMessage = self.localized(error)
                self.setState(self.machine.apply(.openFailed))
            }
        }
    }

    func retryUpload() {
        guard canRetryUpload else { return }
        let generation = openGeneration
        setState(machine.apply(.openRequested))
        errorMessage = ""
        openTask = Task { [weak self] in
            guard let self else { return }
            do {
                try await self.uploadValidatedPDF(generation: generation)
            } catch is CancellationError {
                return
            } catch {
                guard self.openGeneration == generation else { return }
                self.errorMessage = self.localized(error)
                self.setState(self.machine.apply(.openFailed))
            }
        }
    }

    func updateSelection(_ value: PaperChatSelection?, error: Error? = nil) {
        selection = value
        selectionMessage = error.map(localized) ?? ""
    }

    func ask() {
        let clean = question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard canAsk, let chatID = summary?.chat.id else { return }
        let turnID = "paperturn_\(UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased())"
        question = ""
        pendingQuestion = clean
        activeTurnID = turnID
        lastEventID = 0
        streamingAnswer = ""
        streamingReasoning = ""
        activityLabel = copy("Frage wird geerdet …", "Grounding question…")
        errorMessage = ""
        setState(machine.apply(.askStarted))
        turnTask?.cancel()
        turnTask = Task { [weak self] in
            guard let self else { return }
            do {
                let result = try await self.makeClient().streamPaperChatTurn(
                    chatID: chatID,
                    request: PaperChatTurnRequest(
                        turnID: turnID,
                        question: clean,
                        selection: self.selection,
                        model: nil
                    )
                ) { [weak self] event in
                    self?.apply(event)
                }
                try Task.checkCancellation()
                self.lastEventID = max(self.lastEventID, result.lastEventID)
                if let terminal = result.terminalEvent {
                    self.finish(with: terminal)
                    await self.refreshHistory()
                } else {
                    try await self.reconcileTurn(chatID: chatID, turnID: turnID)
                }
            } catch is CancellationError {
                return
            } catch {
                guard self.activeTurnID == turnID else { return }
                await self.recoverAfterStreamFailure(
                    chatID: chatID,
                    turnID: turnID,
                    originalError: error
                )
            }
        }
    }

    func resumeActiveTurn() {
        guard let chatID = summary?.chat.id, let turnID = activeTurnID, !isAsking else { return }
        errorMessage = ""
        setState(machine.apply(.askStarted))
        turnTask = Task { [weak self] in
            guard let self else { return }
            do {
                try await self.reconcileTurn(chatID: chatID, turnID: turnID)
            } catch {
                self.errorMessage = self.localized(error)
                self.setState(self.machine.apply(.askFailed))
            }
        }
    }

    func stopAnswer() {
        guard let chatID = summary?.chat.id, let turnID = activeTurnID else { return }
        Task { [weak self] in
            guard let self else { return }
            do {
                _ = try await self.makeClient().stopPaperChatTurn(
                    chatID: chatID,
                    turnID: turnID
                )
                self.turnTask?.cancel()
                self.activeTurnID = nil
                self.activityLabel = self.copy("Antwort gestoppt", "Answer stopped")
                self.setState(self.machine.apply(.askFinished))
                await self.refreshHistory()
            } catch {
                self.errorMessage = self.localized(error)
            }
        }
    }

    func refreshHistory() async {
        guard let chatID = summary?.chat.id else { return }
        do {
            history = try await makeClient().paperChatHistory(chatID: chatID)
            if !isAsking {
                pendingQuestion = ""
                streamingAnswer = ""
                streamingReasoning = ""
            }
        } catch {
            if history.isEmpty { errorMessage = localized(error) }
        }
    }

    func openInSixSentences() {
        guard let url = PaperCompanionLinkPolicy.destination(
            rawValue: summary?.chat.webURL,
            communityOrigin: preferences.communityOrigin
        ) else { return }
        NSWorkspace.shared.open(url)
    }

    /// Clears every local paper, question, answer, and selection value held in
    /// memory. Paper PDFs and chat history are never persisted by this client.
    func scrubPrivateState() {
        openTask?.cancel()
        openTask = nil
        turnTask?.cancel()
        turnTask = nil
        openGeneration = UUID()
        machine = PaperCompanionWorkspaceMachine()
        workspaceState = .empty
        document = nil
        filename = ""
        summary = nil
        history = []
        selection = nil
        selectionMessage = ""
        streamingAnswer = ""
        streamingReasoning = ""
        activityLabel = ""
        errorMessage = ""
        pendingQuestion = ""
        activeTurnID = nil
        question = ""
        validatedPDF = nil
        clientRequestID = ""
        lastEventID = 0
    }

    private func uploadValidatedPDF(generation: UUID) async throws {
        guard credentials.hasToken else { throw CredentialError.missing }
        guard let validatedPDF else { throw PaperCompanionError.unreadablePDF }
        let base64 = await Task.detached(priority: .userInitiated) {
            validatedPDF.data.base64EncodedString()
        }.value
        try Task.checkCancellation()
        guard openGeneration == generation else { throw CancellationError() }
        let response = try await makeClient().createPaperChat(
            CreatePaperChatRequest(
                clientRequestID: clientRequestID,
                filename: validatedPDF.filename,
                contentBase64: base64,
                sha256: validatedPDF.sha256,
                projectID: preferences.projectID
            )
        )
        try Task.checkCancellation()
        guard openGeneration == generation else { throw CancellationError() }
        summary = response
        filename = response.paper.title.nonEmpty ?? validatedPDF.filename
        setState(machine.apply(.openSucceeded))
        await refreshHistory()
        if let active = try? await makeClient().activePaperChatTurn(chatID: response.chat.id) {
            activeTurnID = active.turnID
            lastEventID = 0
            resumeActiveTurn()
        }
    }

    private func reconcileTurn(chatID: String, turnID: String) async throws {
        let client = try makeClient()
        let state = try await client.paperChatTurn(chatID: chatID, turnID: turnID)
        guard activeTurnID == turnID else { throw CancellationError() }
        switch state.status {
        case "completed":
            if let answer = state.answer?.answer { streamingAnswer = answer }
            activeTurnID = nil
            activityLabel = ""
            selection = nil
            selectionMessage = ""
            setState(machine.apply(.askFinished))
            await refreshHistory()
        case "failed", "cancelled":
            activeTurnID = nil
            errorMessage = copy("Antwort fehlgeschlagen.", "Answer failed.")
            setState(machine.apply(.askFailed))
            await refreshHistory()
        default:
            let result = try await client.followPaperChatTurn(
                chatID: chatID,
                turnID: turnID,
                afterEventID: lastEventID
            ) { [weak self] event in
                self?.apply(event)
            }
            lastEventID = max(lastEventID, result.lastEventID)
            guard let terminal = result.terminalEvent else {
                throw PaperCompanionError.invalidEventStream
            }
            finish(with: terminal)
            await refreshHistory()
        }
    }

    private func recoverAfterStreamFailure(chatID: String, turnID: String, originalError: Error) async {
        do {
            try await reconcileTurn(chatID: chatID, turnID: turnID)
        } catch {
            guard activeTurnID == turnID else { return }
            errorMessage = localized(originalError)
            setState(machine.apply(.askFailed))
        }
    }

    private func apply(_ event: PaperChatStreamEvent) {
        guard event.turnID == activeTurnID, event.id > lastEventID else { return }
        lastEventID = event.id
        switch event.event {
        case "answer.delta":
            streamingAnswer += event.delta ?? ""
        case "reasoning.delta":
            streamingReasoning += event.reasoning ?? ""
        case "answer.reset":
            streamingAnswer = ""
            streamingReasoning = ""
        case "turn.completed", "turn.failed", "turn.cancelled":
            finish(with: event)
        default:
            if let label = event.label, !label.isEmpty { activityLabel = label }
        }
    }

    private func finish(with event: PaperChatStreamEvent) {
        guard event.turnID == activeTurnID else { return }
        if let answer = event.answer?.answer, !answer.isEmpty { streamingAnswer = answer }
        switch event.event {
        case "turn.completed":
            errorMessage = ""
            selection = nil
            selectionMessage = ""
            setState(machine.apply(.askFinished))
        case "turn.failed":
            errorMessage = copy("Antwort fehlgeschlagen.", "Answer failed.")
            setState(machine.apply(.askFailed))
        case "turn.cancelled":
            activityLabel = copy("Antwort gestoppt", "Answer stopped")
            setState(machine.apply(.askFinished))
        default:
            return
        }
        activeTurnID = nil
        pendingQuestion = ""
        activityLabel = ""
    }

    private func makeClient() throws -> CompanionAPIClient {
        try CompanionAPIClient(baseURLString: preferences.apiBaseURL, credentials: credentials)
    }

    private func setState(_ value: PaperCompanionWorkspaceState) {
        workspaceState = value
    }

    private func localized(_ error: Error) -> String {
        if let apiError = error as? APIClientError,
           case let .server(status, code, _) = apiError,
           let message = CompanionAccessErrorCopy.localized(
               status: status, code: code,
               usesGerman: SpeechLocaleResolver.usesGerman(language: "auto")
           ) {
            return message
        }
        if let paperError = error as? PaperCompanionError {
            switch paperError {
            case .fileOnly:
                return copy("Wähle eine lokale PDF-Datei.", "Choose a local PDF file.")
            case .pdfOnly:
                return copy("Paper Companion akzeptiert nur PDF-Dateien.", "Paper Companion accepts PDF files only.")
            case .emptyPDF:
                return copy("Diese PDF ist leer.", "This PDF is empty.")
            case .pdfTooLarge:
                return copy("PDFs sind auf 25 MiB begrenzt.", "PDFs are limited to 25 MiB.")
            case .unreadablePDF:
                return copy("Diese PDF konnte nicht gelesen werden.", "This PDF could not be read.")
            case .selectionSpansPages:
                return copy(
                    "Markiere Text auf genau einer Seite, damit SixSentences die Stelle verifizieren kann.",
                    "Select text on one page so SixSentences can verify the passage."
                )
            case .selectionTooShort:
                return copy("Markiere mindestens drei Zeichen.", "Select at least three characters.")
            case .invalidEventStream:
                return copy(
                    "SixSentences hat einen ungültigen Chat-Stream zurückgegeben.",
                    "SixSentences returned an invalid chat stream."
                )
            }
        }
        if let value = error as? LocalizedError, let description = value.errorDescription {
            return description
        }
        return copy("Das hat nicht funktioniert.", "That didn't work.")
    }
}

private extension String {
    var nonEmpty: String? { isEmpty ? nil : self }
}
