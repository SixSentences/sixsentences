import AppKit
import Foundation

private final class SessionClock: @unchecked Sendable {
    private let lock = NSLock()
    private var anchor = DispatchTime.now().uptimeNanoseconds

    func reset() {
        lock.lock()
        anchor = DispatchTime.now().uptimeNanoseconds
        lock.unlock()
    }

    func elapsedMS() -> Int64 {
        lock.lock()
        let start = anchor
        lock.unlock()
        let now = DispatchTime.now().uptimeNanoseconds
        return Int64((now - start) / 1_000_000)
    }
}

enum CompanionConnectionState: Equatable, Sendable {
    case disconnected
    case awaitingBrowser
    case validating
    case connected
    case failed
}

enum LiveAskSubmissionPolicy {
    static func canSubmit(
        isRecording: Bool,
        hasFinalTranscript: Bool,
        isAsking: Bool,
        question: String
    ) -> Bool {
        isRecording
            && hasFinalTranscript
            && !isAsking
            && question.trimmingCharacters(in: .whitespacesAndNewlines).count >= 2
    }
}

enum LiveAskCutoffPolicy {
    static func confirmedSequence(
        hasFinalTranscript: Bool,
        lastEventSequence: Int?
    ) throws -> Int {
        guard hasFinalTranscript else { throw CompanionFailure.noAskableTranscript }
        guard let lastEventSequence, lastEventSequence >= 1 else {
            throw CompanionFailure.transcriptCutoffUnconfirmed
        }
        return lastEventSequence
    }
}

enum LiveTranscriptSequence {
    static func highestConfirmed(current: Int?, response: Int?) -> Int? {
        guard let response else { return current }
        return max(current ?? 0, response)
    }
}

enum LiveAskRecoveryPolicy {
    static func shouldReconcile(after error: Error) -> Bool {
        if error is URLError { return true }
        guard let apiError = error as? APIClientError else { return false }
        switch apiError {
        case .invalidResponse, .responseTooLarge:
            return true
        case let .server(status, code, _):
            return code == "ask_pending" || status == 408 || status >= 500
        default:
            return false
        }
    }

    static func isExplicitPending(_ error: Error) -> Bool {
        guard let apiError = error as? APIClientError,
              case let .server(_, code, _) = apiError
        else { return false }
        return code == "ask_pending"
    }
}

final class CompanionAsyncGate: @unchecked Sendable {
    private let lock = NSLock()
    private var isOpen = false
    private var waiters: [CheckedContinuation<Void, Never>] = []

    func wait() async {
        await withCheckedContinuation { continuation in
            lock.lock()
            if isOpen {
                lock.unlock()
                continuation.resume()
                return
            }
            waiters.append(continuation)
            lock.unlock()
        }
    }

    func open() {
        lock.lock()
        guard !isOpen else {
            lock.unlock()
            return
        }
        isOpen = true
        let pending = waiters
        waiters.removeAll(keepingCapacity: false)
        lock.unlock()
        pending.forEach { $0.resume() }
    }
}

@MainActor
final class CompanionSerialTail {
    private var tail: Task<Void, Never>?
    private var sequence: UInt64 = 0

    @discardableResult
    func append(
        _ operation: @MainActor @escaping () async -> Void
    ) -> Task<Void, Never> {
        sequence &+= 1
        let ticket = sequence
        let previous = tail
        let task = Task { [weak self] in
            await previous?.value
            await operation()
            guard let self, self.sequence == ticket else { return }
            self.tail = nil
        }
        tail = task
        return task
    }

    func snapshot() -> Task<Void, Never>? { tail }

    func wait() async {
        let current = tail
        await current?.value
    }
}

@MainActor
final class CompanionViewModel: ObservableObject {
    @Published private(set) var state: CompanionSessionState = .idle
    @Published private(set) var config: LiveConfig?
    @Published private(set) var session: LiveSession?
    @Published private(set) var elapsedMS: Int64 = 0
    @Published private(set) var latestTranscript = ""
    @Published private(set) var answer = ""
    @Published private(set) var askHistory: [AskLiveSessionResponse] = []
    @Published private(set) var isAsking = false
    @Published private(set) var askErrorMessage = ""
    @Published private(set) var warningMessage = ""
    @Published private(set) var errorMessage = ""
    @Published private(set) var queuedSegmentCount = 0
    @Published private(set) var hasFinalTranscript = false
    @Published private(set) var liveThoughts: [LiveThought] = []
    @Published private(set) var activeExperienceMode: CompanionExperienceMode?
    @Published private(set) var confirmedTranscriptSequence: Int?
    @Published private(set) var completedBrainstormReceipt: BrainstormReceipt?
    @Published private(set) var finalTranscriptCharacterCount = 0
    @Published private(set) var hasCredential: Bool
    @Published private(set) var connectionState: CompanionConnectionState
    @Published private(set) var accountIdentity: String?
    @Published private(set) var brainstormProjectID: Int?
    @Published private(set) var connectionNotice = ""
    @Published var question = ""
    @Published var sessionTitle = "Live research conversation"
    @Published var showsConsent = false
    @Published var interruptedSessionID: String?

    let preferences: CompanionPreferences

    private let credentials: any CompanionCredentialStore
    private let outbox: SegmentOutbox
    private let recovery: SessionRecoveryStore
    private let pendingAskRecovery: PendingAskRecoveryStore
    private let brainstormRecovery: BrainstormRecoveryStore
    private let pairing: any CompanionPairingStore
    private let clock = SessionClock()
    private let speechUpdateGroup = DispatchGroup()
    private var machine = CompanionSessionMachine()
    private var reducer = TranscriptReducer()
    private var apiClient: CompanionAPIClient?
    private var capture: AudioCaptureCoordinator?
    private var timerTask: Task<Void, Never>?
    private var uploadTask: Task<Void, Never>?
    private var completionRetryTask: Task<Void, Never>?
    private var askTask: Task<Void, Never>?
    private var historyTask: Task<Void, Never>?
    private var pendingAskID: String?
    private var pendingAskQuestion = ""
    private var askUploadBarrier = false
    private var askEnqueueGate: CompanionAsyncGate?
    private var askGeneration = UUID()
    private var historyGeneration = UUID()
    private let enqueueTail = CompanionSerialTail()
    private var enqueuePersistenceFailed = false
    private var lastConfirmedSegmentSequence: Int?
    private var hasDurablePendingAsk = false
    private var didLaunch = false
    private var startGeneration = UUID()
    private var sessionLanguage: CompanionTranscriptionLanguage?

    init(
        preferences: CompanionPreferences? = nil,
        credentials: any CompanionCredentialStore = KeychainCredentialProvider.shared,
        outbox: SegmentOutbox = SegmentOutbox(),
        recovery: SessionRecoveryStore = SessionRecoveryStore(),
        pendingAskRecovery: PendingAskRecoveryStore = PendingAskRecoveryStore(),
        brainstormRecovery: BrainstormRecoveryStore = .shared,
        pairing: any CompanionPairingStore = PairingCoordinator.shared
    ) {
        self.credentials = credentials
        let credentialExists = credentials.hasToken
        hasCredential = credentialExists
        connectionState = credentialExists ? .validating : .disconnected
        self.preferences = preferences ?? .shared
        self.outbox = outbox
        self.recovery = recovery
        self.pendingAskRecovery = pendingAskRecovery
        self.brainstormRecovery = brainstormRecovery
        self.pairing = pairing
    }

    deinit {
        timerTask?.cancel()
        uploadTask?.cancel()
        completionRetryTask?.cancel()
        askTask?.cancel()
        historyTask?.cancel()
        askEnqueueGate?.open()
    }

    var isRecording: Bool {
        if case .recording = state { return true }
        return false
    }

    var isConnected: Bool {
        hasCredential && config != nil && connectionState == .connected
    }

    var isConnecting: Bool {
        connectionState == .awaitingBrowser || connectionState == .validating
    }

    var isBusy: Bool {
        switch state {
        case .starting, .stopping: true
        default: false
        }
    }

    var canChangeAccount: Bool {
        CompanionAccountTransitionPolicy.canChangeAccount(
            state: state,
            hasInterruptedSession: interruptedSessionID != nil
        )
    }

    var canAsk: Bool {
        activeExperienceMode != .brainstorm && LiveAskSubmissionPolicy.canSubmit(
            isRecording: isRecording,
            hasFinalTranscript: hasFinalTranscript,
            isAsking: isAsking,
            question: question
        )
    }

    var activeAskQuestion: String {
        pendingAskQuestion.nonEmpty ?? question
    }

    var showsOptimisticPendingAsk: Bool {
        guard isAsking else { return false }
        guard let pendingAskID else { return true }
        return !askHistory.contains {
            $0.clientRequestID == pendingAskID && $0.status == "pending"
        }
    }

    var usesGerman: Bool {
        // The app language follows macOS. Transcription language is an
        // independent per-session choice and must never relocalize the UI.
        SpeechLocaleResolver.usesGerman(language: "auto")
    }

    func copy(_ german: String, _ english: String) -> String {
        usesGerman ? german : english
    }

    var statusLabel: String {
        if !isConnected {
            switch connectionState {
            case .disconnected: return localized("Nicht verbunden", "Not connected")
            case .awaitingBrowser: return localized("Browser-Anmeldung", "Browser sign-in")
            case .validating: return localized("Verbindung wird geprüft", "Verifying connection")
            case .failed: return localized("Verbindung erforderlich", "Connection required")
            case .connected: break
            }
        }
        switch state {
        case .idle: return localized("Bereit", "Ready")
        case .awaitingConsent: return localized("Einwilligung", "Consent")
        case .starting: return localized("Wird gestartet …", "Starting …")
        case .recording: return localized("AUFNAHME", "RECORDING")
        case .stopping: return localized("Wird gespeichert …", "Saving …")
        case .queuedOffline: return localized("Offline gespeichert", "Saved offline")
        case .completed: return localized("Gespeichert", "Saved")
        case .failed: return localized("Aktion erforderlich", "Action required")
        }
    }

    var isTranscriptionLanguageLocked: Bool {
        switch state {
        case .idle, .completed, .failed: false
        case .awaitingConsent, .starting, .recording, .stopping, .queuedOffline: true
        }
    }

    var transcriptionLanguageLabel: String {
        let language = sessionLanguage ?? CompanionTranscriptionLanguage.validated(preferences.language)
        switch language {
        case .automatic: return localized("Automatisch", "Automatic")
        case .german: return "Deutsch"
        case .english: return "English"
        }
    }

    func selectTranscriptionLanguage(_ value: String) {
        guard !isTranscriptionLanguageLocked else { return }
        preferences.setLanguage(value)
    }

    func selectBrainstormProject(_ projectID: Int?) {
        guard !isTranscriptionLanguageLocked,
              isConnected,
              let accountIdentity,
              let projects = config?.projects
        else { return }
        brainstormProjectID = preferences.setBrainstormProjectID(
            projectID,
            ownerAccountID: accountIdentity,
            availableProjects: projects
        )
    }

    func launch() {
        guard !didLaunch else { return }
        didLaunch = true
        guard hasCredential else {
            connectionState = .disconnected
            return
        }
        connectionState = .validating
        Task { await refreshConfigurationAndRecover() }
    }

    func requestStart(mode: CompanionExperienceMode = .conversation) {
        guard isConnected else {
            errorMessage = localized(
                "Verbinde den Companion zuerst mit SixSentences.",
                "Connect the companion to SixSentences first."
            )
            return
        }
        guard !isRecording, !isBusy, interruptedSessionID == nil else {
            if interruptedSessionID != nil {
                errorMessage = localized(
                    "Schließe zuerst die unterbrochene Sitzung ab.",
                    "Resolve the interrupted session before starting again."
                )
            }
            return
        }
        activeExperienceMode = mode
        sessionLanguage = CompanionTranscriptionLanguage.validated(preferences.language)
        state = machine.apply(.startRequested)
        if mode == .brainstorm {
            // The record button is the explicit solo action. Brainstorm mode
            // captures only this Mac's microphone and therefore has no
            // participant-notification checkbox or ScreenCapture permission.
            state = machine.apply(.consentConfirmed)
            Task { await startSession() }
        } else {
            showsConsent = true
        }
    }

    func declineConsent() {
        showsConsent = false
        state = machine.apply(.consentDeclined)
        sessionLanguage = nil
        activeExperienceMode = nil
    }

    func confirmConsent() {
        showsConsent = false
        state = machine.apply(.consentConfirmed)
        Task { await startSession() }
    }

    func stop() {
        guard case let .recording(sessionID) = state else { return }
        state = machine.apply(.stopRequested)
        timerTask?.cancel()
        timerTask = nil
        let activeAsk = askTask
        let preservesDurableAsk = hasDurablePendingAsk
        if !preservesDurableAsk {
            cancelAskWork(clearPendingRequest: true)
        } else {
            historyGeneration = UUID()
            historyTask?.cancel()
            historyTask = nil
        }
        // Keep background upload paused until every queued final has joined
        // the Stop tail. A server-accepted Ask keeps watching its durable
        // receipt even after capture stops or the overlay closes.
        askUploadBarrier = true
        let capture = self.capture
        self.capture = nil
        Task {
            if !preservesDurableAsk { activeAsk?.cancel() }
            // Capture sources stop before any network operation. Each Apple
            // Speech task then gets a bounded chance to deliver its final text.
            let finalizationStartedAt = CompanionLatency.now()
            await capture?.stop()
            CompanionLatency.recordStopFinalization(
                durationMS: CompanionLatency.milliseconds(since: finalizationStartedAt)
            )
            // Both result streams are closed now; wait until every callback
            // already dispatched to the main actor has joined the enqueue tail.
            await waitForSpeechUpdates()
            let remaining = reducer.flushAll()
            scheduleEnqueue(remaining, for: sessionID, startUploader: false)
            await enqueueTail.wait()
            if !preservesDurableAsk { await activeAsk?.value }
            askUploadBarrier = false
            do {
                guard !enqueuePersistenceFailed else {
                    throw CompanionFailure.transcriptPersistenceFailed
                }
                try await recovery.requestCompletion(sessionID: sessionID)
                guard hasFinalTranscript else {
                    _ = try await client().cancel(sessionID: sessionID)
                    try await recovery.remove(sessionID: sessionID)
                    throw CompanionFailure.noFinalTranscript
                }
                try await finishCompletion(sessionID: sessionID)
            } catch {
                warningMessage = localize(error)
                if hasFinalTranscript, isRetryable(error) {
                    state = machine.apply(.pendingUpload)
                    beginCompletionRetry(sessionID: sessionID)
                } else {
                    state = machine.apply(.completionFailed(message: localize(error)))
                    errorMessage = localize(error)
                    interruptedSessionID = sessionID
                }
            }
        }
    }

    func windowWillHide() {
        switch state {
        case .recording:
            stop()
        case .starting:
            startGeneration = UUID()
            state = machine.apply(.startFailed(message: localized(
                "Start wurde abgebrochen, weil das sichtbare Fenster geschlossen wurde.",
                "Start was cancelled because the visible window was closed."
            )))
            sessionLanguage = nil
        default:
            break
        }
    }

    func cancelInterruptedSession() {
        guard let sessionID = interruptedSessionID else { return }
        Task {
            do {
                _ = try await client().cancel(sessionID: sessionID)
                try await outbox.removeSession(sessionID: sessionID)
                try await recovery.remove(sessionID: sessionID)
                if let accountIdentity {
                    try await brainstormRecovery.remove(
                        sessionID: sessionID,
                        ownerAccountID: accountIdentity
                    )
                }
                interruptedSessionID = nil
                warningMessage = ""
                errorMessage = ""
                state = machine.apply(.reset)
            } catch {
                errorMessage = localize(error)
            }
        }
    }

    func completeInterruptedSession() {
        guard let sessionID = interruptedSessionID else { return }
        Task {
            let count = await outbox.count(sessionID: sessionID)
            do {
                if count == 0 {
                    let serverSession = try await client().getSession(sessionID: sessionID)
                    guard serverSession.segmentCount > 0
                            || (serverSession.lastSegmentSequence ?? 0) > 0
                    else {
                        errorMessage = localized(
                            "Es gibt keinen finalen Text. Brich die leere Sitzung ab.",
                            "There is no final text. Cancel the empty session."
                        )
                        return
                    }
                    session = serverSession
                    activeExperienceMode = serverSession.purpose == "brainstorm"
                        ? .brainstorm
                        : .conversation
                    confirmedTranscriptSequence = serverSession.completedThroughSequence
                        ?? serverSession.lastSegmentSequence
                }
                try await recovery.requestCompletion(sessionID: sessionID)
                state = .queuedOffline(sessionID: sessionID)
                try await finishCompletion(sessionID: sessionID)
                interruptedSessionID = nil
            } catch {
                if isRetryable(error) {
                    beginCompletionRetry(sessionID: sessionID)
                } else {
                    errorMessage = localize(error)
                }
            }
        }
    }

    func ask() {
        let clean = question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard activeExperienceMode != .brainstorm,
              case let .recording(sessionID) = state,
              clean.count >= 2,
              !isAsking
        else { return }
        guard hasFinalTranscript else {
            askErrorMessage = localized(
                "Du kannst fragen, sobald der erste Satz sicher transkribiert ist.",
                "You can ask as soon as the first sentence is ready."
            )
            return
        }
        if pendingAskQuestion != clean {
            pendingAskQuestion = clean
            pendingAskID = UUID().uuidString.lowercased()
        }
        let requestID = pendingAskID ?? UUID().uuidString.lowercased()
        pendingAskID = requestID
        answer = localized("Antwort wird vorbereitet …", "Preparing answer …")
        askErrorMessage = ""
        isAsking = true
        askUploadBarrier = true
        historyTask?.cancel()
        historyTask = nil

        // Everything already final at the button press becomes the immutable
        // Ask cutoff. Later finals queue behind this gate and resume only once
        // the grounded request has taken its server-side transcript snapshot.
        let ready = reducer.flushAll()
        scheduleEnqueue(ready, for: sessionID, startUploader: false)
        let cutoff = enqueueTail.snapshot()
        let enqueueGate = CompanionAsyncGate()
        askEnqueueGate = enqueueGate
        let generation = UUID()
        askGeneration = generation
        askTask?.cancel()
        askTask = Task { [weak self] in
            guard let self else {
                enqueueGate.open()
                return
            }
            defer {
                self.releaseAskCutoffGate(sessionID: sessionID, generation: generation)
                if self.askGeneration == generation {
                    self.askTask = nil
                    if !self.hasDurablePendingAsk { self.isAsking = false }
                }
            }
            do {
                await cutoff?.value
                try Task.checkCancellation()
                try self.ensureAskIsCurrent(sessionID: sessionID, generation: generation)
                guard !self.enqueuePersistenceFailed else {
                    throw CompanionFailure.transcriptPersistenceFailed
                }
                let activeUpload = self.uploadTask
                await activeUpload?.value
                try Task.checkCancellation()
                try self.ensureAskIsCurrent(sessionID: sessionID, generation: generation)
                let uploadedThrough = try await self.uploadAll(sessionID: sessionID)
                let contextThroughSequence = try LiveAskCutoffPolicy.confirmedSequence(
                    hasFinalTranscript: self.hasFinalTranscript,
                    lastEventSequence: uploadedThrough
                )
                try await self.pendingAskRecovery.save(
                    RecoverablePendingAsk(
                        sessionID: sessionID,
                        clientRequestID: requestID,
                        question: clean,
                        contextThroughSequence: contextThroughSequence,
                        createdAt: Date()
                    )
                )
                // The backend snapshots exactly this confirmed sequence. Newer
                // finals can be persisted and uploaded immediately without
                // entering the already-fixed Ask context.
                self.releaseAskCutoffGate(sessionID: sessionID, generation: generation)
                let startedAt = CompanionLatency.now()
                do {
                    let response = try await self.client().ask(
                        sessionID: sessionID,
                        request: AskLiveSessionRequest(
                            clientRequestID: requestID,
                            question: clean,
                            contextThroughSequence: contextThroughSequence
                        )
                    )
                    try self.ensureAskSessionIsCurrent(sessionID: sessionID, generation: generation)
                    if response.status == "failed" {
                        await self.finishFailedAsk(response, sessionID: sessionID)
                    } else if response.status == "pending" {
                        self.hasDurablePendingAsk = true
                        self.mergeAskReceipt(response)
                        await self.reconcileAsk(
                            sessionID: sessionID,
                            requestID: requestID,
                            generation: generation,
                            startedAt: startedAt,
                            initialReceipt: response,
                            maximumDuration: .seconds(300)
                        )
                    } else {
                        await self.finishCompletedAsk(
                            response,
                            sessionID: sessionID,
                            question: clean,
                            startedAt: startedAt
                        )
                    }
                } catch is CancellationError {
                    return
                } catch {
                    try self.ensureAskSessionIsCurrent(sessionID: sessionID, generation: generation)
                    if LiveAskRecoveryPolicy.shouldReconcile(after: error) {
                        await self.reconcileAsk(
                            sessionID: sessionID,
                            requestID: requestID,
                            generation: generation,
                            startedAt: startedAt,
                            maximumDuration: LiveAskRecoveryPolicy.isExplicitPending(error)
                                ? .seconds(300)
                                : .seconds(5)
                        )
                    } else {
                        try? await self.pendingAskRecovery.remove(
                            sessionID: sessionID,
                            clientRequestID: requestID
                        )
                        self.answer = ""
                        self.askErrorMessage = self.localize(error)
                        self.pendingAskID = nil
                        self.pendingAskQuestion = ""
                    }
                }
            } catch is CancellationError {
                return
            } catch {
                guard self.askGeneration == generation else { return }
                self.answer = ""
                self.askErrorMessage = self.localize(error)
                self.pendingAskID = nil
                self.pendingAskQuestion = ""
            }
        }
    }

    func refreshAskHistory() {
        guard let sessionID = session?.id, hasCredential, historyTask == nil, !isAsking else {
            return
        }
        let generation = UUID()
        historyGeneration = generation
        historyTask = Task { [weak self] in
            guard let self else { return }
            defer {
                if self.historyGeneration == generation {
                    self.historyTask = nil
                }
            }
            let startedAt = CompanionLatency.now()
            do {
                let receipts = try await self.client().asks(sessionID: sessionID)
                guard !Task.isCancelled, self.session?.id == sessionID else { return }
                self.askHistory = receipts
                await self.removeTerminalAskIntents(
                    sessionID: sessionID,
                    receipts: receipts
                )
                if let latest = receipts.last, latest.status == "completed" {
                    self.answer = latest.answer
                }
                self.askErrorMessage = ""
                if let pending = receipts.last(where: { $0.status == "pending" }) {
                    self.resumePendingAsk(pending, sessionID: sessionID)
                }
                CompanionLatency.recordHistoryRefresh(
                    durationMS: CompanionLatency.milliseconds(since: startedAt),
                    receiptCount: receipts.count
                )
            } catch is CancellationError {
                return
            } catch {
                guard !Task.isCancelled, self.session?.id == sessionID else { return }
                self.askErrorMessage = self.localize(error)
            }
        }
    }

    func openInSixSentences() {
        let rawValue = session?.webURL ?? preferences.webBaseURL
        guard let url = safeWebURL(rawValue) else {
            errorMessage = localized("Ungültige Web-URL.", "Invalid web URL.")
            return
        }
        NSWorkspace.shared.open(url)
    }

    var canOpenBrainstormResultInSixSentences: Bool {
        BrainstormWebHandoffPolicy.destination(
            purpose: session?.purpose,
            webURL: session?.webURL,
            communityOrigin: preferences.communityOrigin
        ) != nil
    }

    @discardableResult
    func openBrainstormResultInSixSentences() -> Bool {
        guard let url = BrainstormWebHandoffPolicy.destination(
            purpose: session?.purpose,
            webURL: session?.webURL,
            communityOrigin: preferences.communityOrigin
        ) else {
            errorMessage = localized(
                "Der sichere Link zum Brainstorm-Ergebnis ist noch nicht verfügbar.",
                "The secure link to the brainstorm result is not available yet."
            )
            return false
        }
        guard NSWorkspace.shared.open(url) else {
            errorMessage = localized(
                "SixSentences konnte nicht im Browser geöffnet werden.",
                "SixSentences could not be opened in your browser."
            )
            return false
        }
        return true
    }

    func handleIncomingURL(_ url: URL) {
        guard canChangeAccount else {
            errorMessage = localize(CompanionFailure.cannotChangeAccountWhileActive)
            return
        }
        do {
            let callback = try pairing.consumeCallback(url)
            connectionState = .validating
            errorMessage = ""
            warningMessage = ""
            Task { await exchangePairingCode(callback.code, verifier: callback.verifier) }
        } catch {
            connectionState = .failed
            errorMessage = localize(error)
        }
    }

    func connectThroughBrowser() {
        guard canChangeAccount else {
            errorMessage = localize(CompanionFailure.cannotChangeAccountWhileActive)
            return
        }
        do {
            connectionNotice = ""
            let url = try pairing.begin(webBaseURL: preferences.webBaseURL)
            guard NSWorkspace.shared.open(url) else {
                try? pairing.clear()
                throw CompanionFailure.cannotOpenBrowser
            }
            connectionState = .awaitingBrowser
            errorMessage = ""
            warningMessage = ""
        } catch {
            connectionState = .failed
            errorMessage = localize(error)
        }
    }

    func removeToken() async throws {
        guard canChangeAccount else {
            throw CompanionFailure.cannotChangeAccountWhileActive
        }
        cancelAskWork(clearPendingRequest: true)
        historyTask?.cancel()
        historyTask = nil
        uploadTask?.cancel()
        uploadTask = nil
        completionRetryTask?.cancel()
        completionRetryTask = nil
        await enqueueTail.wait()

        var firstFailure: Error?
        do { try await outbox.purge() } catch { firstFailure = firstFailure ?? error }
        do { try await recovery.purge() } catch { firstFailure = firstFailure ?? error }
        do { try await pendingAskRecovery.purge() } catch { firstFailure = firstFailure ?? error }
        do { try await brainstormRecovery.purge() } catch { firstFailure = firstFailure ?? error }
        do { try pairing.clear() } catch { firstFailure = firstFailure ?? error }
        do { try credentials.delete() } catch { firstFailure = firstFailure ?? error }

        preferences.clearAccountState()
        scrubCreatorPrivateMemory()
        hasCredential = credentials.hasToken
        accountIdentity = nil
        config = nil
        apiClient = nil
        connectionState = hasCredential ? .failed : .disconnected
        connectionNotice = ""
        warningMessage = ""
        errorMessage = ""
        if let firstFailure { throw firstFailure }
    }

    func refreshConfiguration() {
        guard credentials.hasToken else {
            preferences.clearBrainstormProjectSelection()
            scrubCreatorPrivateMemory()
            hasCredential = false
            accountIdentity = nil
            config = nil
            apiClient = nil
            connectionState = .disconnected
            return
        }
        hasCredential = true
        connectionState = .validating
        Task {
            do {
                let api = try makeClient()
                let value = try await api.config()
                guard value.enabled else { throw CompanionFailure.disabled }
                try CompanionCompatibilityPolicy.validate(value)
                try activateAccountIdentity(value.accountID)
                apiClient = api
                config = value
                reconcileProjectPreferences(with: value)
                connectionState = .connected
                errorMessage = ""
            } catch {
                connectionState = .failed
                connectionNotice = ""
                errorMessage = localize(error)
            }
        }
    }

    func resetAfterCompletion() {
        guard !isRecording, !isBusy, interruptedSessionID == nil else { return }
        cancelAskWork(clearPendingRequest: true)
        state = machine.apply(.reset)
        session = nil
        elapsedMS = 0
        latestTranscript = ""
        answer = ""
        askHistory = []
        askErrorMessage = ""
        isAsking = false
        warningMessage = ""
        errorMessage = ""
        hasFinalTranscript = false
        lastConfirmedSegmentSequence = nil
        confirmedTranscriptSequence = nil
        completedBrainstormReceipt = nil
        liveThoughts = []
        finalTranscriptCharacterCount = 0
        enqueuePersistenceFailed = false
        reducer = TranscriptReducer()
        sessionLanguage = nil
        activeExperienceMode = nil
    }

    /// Clears creator-private UI state without deleting durable recovery.
    /// The recovery layer separately gates each intent by account identity.
    func scrubCreatorPrivateMemory() {
        timerTask?.cancel()
        timerTask = nil
        uploadTask?.cancel()
        uploadTask = nil
        completionRetryTask?.cancel()
        completionRetryTask = nil
        askTask?.cancel()
        askTask = nil
        historyTask?.cancel()
        historyTask = nil
        pendingAskID = nil
        pendingAskQuestion = ""
        isAsking = false
        askUploadBarrier = false
        askEnqueueGate?.open()
        askEnqueueGate = nil
        machine = CompanionSessionMachine()
        state = .idle
        session = nil
        elapsedMS = 0
        latestTranscript = ""
        answer = ""
        askHistory = []
        askErrorMessage = ""
        question = ""
        sessionTitle = "Live research conversation"
        warningMessage = ""
        errorMessage = ""
        queuedSegmentCount = 0
        hasFinalTranscript = false
        liveThoughts = []
        activeExperienceMode = nil
        confirmedTranscriptSequence = nil
        completedBrainstormReceipt = nil
        finalTranscriptCharacterCount = 0
        interruptedSessionID = nil
        lastConfirmedSegmentSequence = nil
        enqueuePersistenceFailed = false
        reducer = TranscriptReducer()
        sessionLanguage = nil
        brainstormProjectID = nil
    }

    private func startSession() async {
        // Snapshot once before the first suspension point. Speech recognition
        // and the server-side session must always receive the same language.
        let selectedLanguage = (
            sessionLanguage ?? CompanionTranscriptionLanguage.validated(preferences.language)
        ).rawValue
        cancelAskWork(clearPendingRequest: true)
        let generation = UUID()
        startGeneration = generation
        errorMessage = ""
        warningMessage = ""
        connectionNotice = ""
        answer = ""
        askHistory = []
        askErrorMessage = ""
        isAsking = false
        latestTranscript = ""
        liveThoughts = []
        finalTranscriptCharacterCount = 0
        queuedSegmentCount = 0
        hasFinalTranscript = false
        lastConfirmedSegmentSequence = nil
        confirmedTranscriptSequence = nil
        completedBrainstormReceipt = nil
        enqueuePersistenceFailed = false
        reducer = TranscriptReducer()
        do {
            try await outbox.validateIntegrity()
            try await recovery.validateIntegrity()
            try await brainstormRecovery.validateIntegrity()
            // Ask permission before creating a server-side recording row.
            let captureMode: AudioCaptureMode = activeExperienceMode == .brainstorm
                ? .microphoneOnly
                : .conversation
            try await AudioCaptureCoordinator.requestPermissions(mode: captureMode)
            try ensureStartIsCurrent(generation)
            let api = try makeClient()
            let currentConfig = try await api.config()
            try ensureStartIsCurrent(generation)
            guard currentConfig.enabled else { throw CompanionFailure.disabled }
            try CompanionCompatibilityPolicy.validate(currentConfig)
            try activateAccountIdentity(currentConfig.accountID)
            config = currentConfig
            reconcileProjectPreferences(with: currentConfig)
            apiClient = api
            let selectedProjectID = CompanionSessionProjectPolicy.projectID(
                experienceMode: activeExperienceMode,
                conversationProjectID: preferences.projectID,
                brainstormProjectID: brainstormProjectID,
                availableProjects: currentConfig.projects
            )
            let selectedProjectName = currentConfig.projects.first {
                $0.id == selectedProjectID
            }?.name
            // Resolve and install the strictly local speech model before a
            // server-side session exists. A failed model setup leaves no
            // empty session behind.
            let speechPlan = try await AudioCaptureCoordinator.prepareSpeech(
                language: selectedLanguage,
                contextualStrings: selectedProjectName.map { [$0] } ?? []
            )
            try ensureStartIsCurrent(generation)
            let clientSessionID = UUID().uuidString.lowercased()
            try await recovery.begin(clientSessionID: clientSessionID)
            let created = try await api.createSession(
                CreateLiveSessionRequest(
                    clientSessionID: clientSessionID,
                    title: sessionTitle.trimmingCharacters(in: .whitespacesAndNewlines).nonEmpty
                        ?? "Live research conversation",
                    projectID: selectedProjectID,
                    language: selectedLanguage,
                    purpose: activeExperienceMode == .brainstorm ? "brainstorm" : "conversation",
                    consent: activeExperienceMode == .brainstorm
                        ? nil
                        : ConsentPayload(
                            participantsNotified: true,
                            noticeText: "Participants were informed before local transcription started."
                        )
                )
            )
            do {
                try ensureStartIsCurrent(generation)
            } catch {
                _ = try? await api.cancel(sessionID: created.id)
                try? await recovery.remove(clientSessionID: clientSessionID)
                throw error
            }
            do {
                try await recovery.bind(clientSessionID: clientSessionID, session: created)
            } catch {
                _ = try? await api.cancel(sessionID: created.id)
                try? await recovery.remove(clientSessionID: clientSessionID)
                throw error
            }
            session = created
            clock.reset()
            let capture = AudioCaptureCoordinator(
                plan: speechPlan,
                mode: captureMode,
                elapsedMS: { [clock] in clock.elapsedMS() },
                onUpdate: { [weak self, speechUpdateGroup] update in
                    speechUpdateGroup.enter()
                    Task { @MainActor [weak self, speechUpdateGroup] in
                        self?.consume(update)
                        speechUpdateGroup.leave()
                    }
                },
                onWarning: { [weak self] warning in
                    Task { @MainActor [weak self] in self?.warningMessage = warning }
                }
            )
            self.capture = capture
            do {
                try await capture.startAuthorized()
                try ensureStartIsCurrent(generation)
            } catch {
                await capture.stop()
                _ = try? await api.cancel(sessionID: created.id)
                try? await recovery.remove(sessionID: created.id)
                self.capture = nil
                throw error
            }
            state = machine.apply(.sessionCreated(id: created.id))
            beginTimer(sessionID: created.id)
            if BrainstormWebHandoffPolicy.shouldOpenAtSessionStart(
                preferenceEnabled: preferences.autoOpenWebSession,
                experienceMode: activeExperienceMode
            ) {
                openInSixSentences()
            }
        } catch {
            let message = localize(error)
            state = machine.apply(.startFailed(message: message))
            errorMessage = message
            sessionLanguage = nil
        }
    }

    private func ensureStartIsCurrent(_ generation: UUID) throws {
        guard startGeneration == generation, case .starting = state else {
            throw CompanionFailure.startCancelled
        }
    }

    private func waitForSpeechUpdates() async {
        await withCheckedContinuation { continuation in
            speechUpdateGroup.notify(queue: .global(qos: .userInitiated)) {
                continuation.resume()
            }
        }
    }

    private func consume(_ update: SpeechUpdate) {
        if !CompanionCapturePolicy.accepts(
            channel: update.channel,
            experienceMode: activeExperienceMode
        ) {
            // Defense in depth against a stale callback from another capture
            // generation. Solo brainstorms can never persist system audio.
            return
        }
        elapsedMS = max(elapsedMS, update.endMS)
        latestTranscript = "\(update.channel.speakerLabel): \(update.text)"
        guard update.isFinal else { return }
        liveThoughts.append(
            LiveThought(
                id: "\(update.channel.rawValue)-\(update.startMS)-\(update.endMS)-\(liveThoughts.count)",
                channel: update.channel,
                text: update.text,
                startMS: update.startMS,
                endMS: update.endMS
            )
        )
        if liveThoughts.count > 240 {
            liveThoughts.removeFirst(liveThoughts.count - 240)
        }
        finalTranscriptCharacterCount += update.text.count
        let committed = reducer.ingestFinal(update)
        guard let sessionID = CompanionTranscriptPersistencePolicy.sessionID(for: state) else {
            return
        }
        scheduleEnqueue(committed, for: sessionID)
        let configuredMaximum = config?.brainstorm?.maxTranscriptCharacters ?? 500_000
        let safeMaximum = min(
            BrainstormCaptureLimits.automaticStopCharacters,
            max(1, Int(Double(configuredMaximum) * 0.96))
        )
        if activeExperienceMode == .brainstorm,
           finalTranscriptCharacterCount >= safeMaximum {
            warningMessage = localized(
                "Sicheres Brainstorm-Limit erreicht — der finale Text wird jetzt strukturiert.",
                "Safe brainstorm limit reached — final text is being organized now."
            )
            stop()
        }
    }

    private func mergeAskReceipt(_ receipt: AskLiveSessionResponse) {
        if let index = askHistory.firstIndex(where: { $0.id == receipt.id }) {
            askHistory[index] = receipt
        } else {
            askHistory.append(receipt)
        }
    }

    private func resumePendingAsk(
        _ receipt: AskLiveSessionResponse,
        sessionID: String
    ) {
        guard receipt.status == "pending", askTask == nil else { return }
        let generation = UUID()
        askGeneration = generation
        pendingAskID = receipt.clientRequestID
        pendingAskQuestion = receipt.question
        answer = localized("Antwort wird vorbereitet …", "Preparing answer …")
        isAsking = true
        hasDurablePendingAsk = true
        let startedAt = CompanionLatency.now()
        askTask = Task { [weak self] in
            guard let self else { return }
            defer {
                if self.askGeneration == generation {
                    self.askTask = nil
                    if !self.hasDurablePendingAsk { self.isAsking = false }
                }
            }
            await self.reconcileAsk(
                sessionID: sessionID,
                requestID: receipt.clientRequestID,
                generation: generation,
                startedAt: startedAt,
                initialReceipt: receipt,
                maximumDuration: .seconds(300)
            )
        }
    }

    private func reconcileAsk(
        sessionID: String,
        requestID: String,
        generation: UUID,
        startedAt: UInt64,
        initialReceipt: AskLiveSessionResponse? = nil,
        maximumDuration: Duration
    ) async {
        let api: CompanionAPIClient
        do {
            api = try client()
        } catch {
            guard askGeneration == generation else { return }
            answer = ""
            askErrorMessage = localize(error)
            return
        }
        let outcome = await AskReconciler.reconcile(
            clientRequestID: requestID,
            maximumDuration: maximumDuration,
            initialReceipt: initialReceipt,
            onReceipt: { [weak self] receipt in
                guard let self,
                      self.askGeneration == generation,
                      self.session?.id == sessionID
                else { return }
                self.hasDurablePendingAsk = true
                self.mergeAskReceipt(receipt)
            },
            fetch: {
                try Task.checkCancellation()
                return try await api.asks(sessionID: sessionID)
            }
        )
        guard !Task.isCancelled else { return }
        do {
            try ensureAskSessionIsCurrent(sessionID: sessionID, generation: generation)
        } catch {
            return
        }
        switch outcome {
        case let .completed(receipt):
            await finishCompletedAsk(
                receipt,
                sessionID: sessionID,
                question: pendingAskQuestion,
                startedAt: startedAt
            )
        case let .failed(receipt):
            await finishFailedAsk(receipt, sessionID: sessionID)
        case .notFound:
            try? await pendingAskRecovery.remove(
                sessionID: sessionID,
                clientRequestID: requestID
            )
            hasDurablePendingAsk = false
            answer = ""
            askErrorMessage = localized(
                "Die Anfrage wurde vom Server nicht bestätigt. Du kannst dieselbe Frage sicher erneut senden.",
                "The server did not confirm this request. You can safely send the same question again."
            )
        case let .timedOut(lastReceipt):
            if let lastReceipt { mergeAskReceipt(lastReceipt) }
            hasDurablePendingAsk = false
            answer = ""
            if lastReceipt == nil {
                try? await pendingAskRecovery.remove(
                    sessionID: sessionID,
                    clientRequestID: requestID
                )
                askErrorMessage = localized(
                    "Der Status der Anfrage konnte nicht bestätigt werden. Du kannst dieselbe Frage sicher erneut senden.",
                    "The request status could not be confirmed. You can safely send the same question again."
                )
            } else {
                askErrorMessage = localized(
                    "Die Antwort wird ungewöhnlich lange vorbereitet. Die Anfrage bleibt im Verlauf sichtbar; aktualisiere ihn später erneut.",
                    "The answer is taking unusually long. The request remains visible in history; refresh it again later."
                )
            }
        }
    }

    private func finishCompletedAsk(
        _ receipt: AskLiveSessionResponse,
        sessionID: String,
        question submittedQuestion: String,
        startedAt: UInt64
    ) async {
        try? await pendingAskRecovery.remove(
            sessionID: sessionID,
            clientRequestID: receipt.clientRequestID
        )
        CompanionLatency.recordAsk(
            durationMS: CompanionLatency.milliseconds(since: startedAt),
            sourceCount: receipt.transcriptSources.count + receipt.projectSources.count
        )
        answer = receipt.answer
        mergeAskReceipt(receipt)
        if question.trimmingCharacters(in: .whitespacesAndNewlines) == submittedQuestion {
            question = ""
        }
        askErrorMessage = ""
        pendingAskID = nil
        pendingAskQuestion = ""
        hasDurablePendingAsk = false
    }

    private func finishFailedAsk(
        _ receipt: AskLiveSessionResponse,
        sessionID: String
    ) async {
        try? await pendingAskRecovery.remove(
            sessionID: sessionID,
            clientRequestID: receipt.clientRequestID
        )
        mergeAskReceipt(receipt)
        answer = ""
        askErrorMessage = localized(
            "Die belegte Antwort ist fehlgeschlagen. Stelle eine neue Frage.",
            "The grounded answer failed. Ask a new question."
        )
        pendingAskID = nil
        pendingAskQuestion = ""
        hasDurablePendingAsk = false
    }

    private func ensureAskIsCurrent(sessionID: String, generation: UUID) throws {
        guard askGeneration == generation,
              session?.id == sessionID,
              case let .recording(activeSessionID) = state,
              activeSessionID == sessionID
        else { throw CompanionFailure.askSessionChanged }
    }

    private func ensureAskSessionIsCurrent(sessionID: String, generation: UUID) throws {
        guard askGeneration == generation, session?.id == sessionID else {
            throw CompanionFailure.askSessionChanged
        }
    }

    private func releaseAskCutoffGate(sessionID: String, generation: UUID) {
        askEnqueueGate?.open()
        guard askGeneration == generation else { return }
        askEnqueueGate = nil
        askUploadBarrier = false
        if case let .recording(activeSessionID) = state, activeSessionID == sessionID {
            beginBackgroundUpload(sessionID: sessionID)
        }
    }

    private func cancelAskWork(clearPendingRequest: Bool) {
        askGeneration = UUID()
        askTask?.cancel()
        askTask = nil
        historyGeneration = UUID()
        historyTask?.cancel()
        historyTask = nil
        askEnqueueGate?.open()
        askEnqueueGate = nil
        askUploadBarrier = false
        isAsking = false
        hasDurablePendingAsk = false
        if clearPendingRequest {
            pendingAskID = nil
            pendingAskQuestion = ""
        }
    }

    private func beginTimer(sessionID: String) {
        timerTask?.cancel()
        timerTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .milliseconds(400))
                guard let self else { return }
                let now = self.clock.elapsedMS()
                self.elapsedMS = now
                let configuredLimitMS = Int64(self.config?.maxSessionMinutes ?? 240) * 60_000
                let limitMS = self.session?.maxDurationMS ?? configuredLimitMS
                if now >= limitMS, self.isRecording {
                    self.warningMessage = self.localized(
                        "Sitzungslimit erreicht — Transkript wird gespeichert.",
                        "Session limit reached — saving transcript."
                    )
                    self.stop()
                    return
                }
                let ready = self.reducer.drainReady(nowMS: now)
                let scheduled = self.scheduleEnqueue(ready, for: sessionID)
                await scheduled?.value
            }
        }
    }

    @discardableResult
    private func scheduleEnqueue(
        _ segments: [FinalTranscriptSegment],
        for sessionID: String,
        startUploader: Bool = true
    ) -> Task<Void, Never>? {
        guard !segments.isEmpty else { return nil }
        let gate = askEnqueueGate
        return enqueueTail.append { [weak self] in
            if let gate { await gate.wait() }
            guard let self else { return }
            await self.persistSegments(
                segments,
                for: sessionID,
                startUploader: startUploader
            )
        }
    }

    private func persistSegments(
        _ segments: [FinalTranscriptSegment],
        for sessionID: String,
        startUploader: Bool
    ) async {
        do {
            let inserted = try await outbox.enqueue(sessionID: sessionID, segments: segments)
            let persisted = await outbox.count(sessionID: sessionID)
            hasFinalTranscript = hasFinalTranscript || inserted > 0 || persisted > 0
            queuedSegmentCount = persisted
            if startUploader { beginBackgroundUpload(sessionID: sessionID) }
        } catch {
            enqueuePersistenceFailed = true
            errorMessage = localize(error)
        }
    }

    private func beginBackgroundUpload(sessionID: String) {
        guard uploadTask == nil, !askUploadBarrier else { return }
        uploadTask = Task { [weak self] in
            guard let self else { return }
            defer { self.uploadTask = nil }
            do {
                try await self.uploadAll(sessionID: sessionID)
                self.warningMessage = ""
            } catch {
                self.warningMessage = self.localized(
                    "Offline: finale Transkripte sind lokal gespeichert.",
                    "Offline: final transcript text is stored locally."
                )
            }
        }
    }

    @discardableResult
    private func uploadAll(sessionID: String) async throws -> Int? {
        let batchLimit = min(max(config?.maxBatchSegments ?? 100, 1), 100)
        var highestConfirmedSequence = lastConfirmedSegmentSequence
        while true {
            try Task.checkCancellation()
            let batch = await outbox.batch(sessionID: sessionID, limit: batchLimit)
            guard !batch.isEmpty else {
                queuedSegmentCount = 0
                return highestConfirmedSequence
            }
            let startedAt = CompanionLatency.now()
            let response = try await client().uploadSegments(
                sessionID: sessionID,
                segments: batch
            )
            highestConfirmedSequence = LiveTranscriptSequence.highestConfirmed(
                current: highestConfirmedSequence,
                response: response.lastEventSequence
            )
            lastConfirmedSegmentSequence = highestConfirmedSequence
            if let transcriptCharacterCount = response.transcriptCharacterCount {
                finalTranscriptCharacterCount = max(
                    finalTranscriptCharacterCount,
                    transcriptCharacterCount
                )
            }
            CompanionLatency.recordUpload(
                segmentCount: batch.count,
                durationMS: CompanionLatency.milliseconds(since: startedAt)
            )
            try await outbox.acknowledge(eventIDs: Set(batch.map(\.clientEventID)))
            queuedSegmentCount = await outbox.count(sessionID: sessionID)
        }
    }

    private func finishCompletion(sessionID: String) async throws {
        await enqueueTail.wait()
        guard !enqueuePersistenceFailed else {
            throw CompanionFailure.transcriptPersistenceFailed
        }
        let activeUpload = uploadTask
        await activeUpload?.value
        let uploadedThrough = try await uploadAll(sessionID: sessionID)
        confirmedTranscriptSequence = BrainstormCutoffPolicy.confirmed(
            uploadedThrough: uploadedThrough,
            lastConfirmed: lastConfirmedSegmentSequence,
            completedThrough: session?.completedThroughSequence,
            serverLastSegment: session?.lastSegmentSequence
        )
        let brainstormRequest: CreateBrainstormRequest?
        if activeExperienceMode == .brainstorm || session?.purpose == "brainstorm" {
            guard let accountIdentity else {
                throw CompanionFailure.accountIdentityUnavailable
            }
            guard let cutoff = confirmedTranscriptSequence, cutoff >= 1 else {
                throw CompanionFailure.transcriptCutoffUnconfirmed
            }
            if let recovered = try await brainstormRecovery.intent(
                sessionID: sessionID,
                ownerAccountID: accountIdentity
            ) {
                brainstormRequest = recovered.request
            } else {
                let request = CreateBrainstormRequest(
                    clientRequestID: UUID().uuidString.lowercased(),
                    outputLanguage: brainstormOutputLanguage(for: session?.language),
                    contextThroughSequence: cutoff
                )
                try await brainstormRecovery.save(
                    RecoverableBrainstormIntent(
                        ownerAccountID: accountIdentity,
                        sessionID: sessionID,
                        request: request,
                        brainstormID: nil,
                        createdAt: Date()
                    )
                )
                brainstormRequest = request
            }
        } else {
            brainstormRequest = nil
        }
        let response = try await client().complete(
            sessionID: sessionID,
            brainstormRequest: brainstormRequest
        )
        session = response.session
        completedBrainstormReceipt = response.brainstorm
        if let brainstorm = response.brainstorm {
            guard let accountIdentity else {
                throw CompanionFailure.accountIdentityUnavailable
            }
            try await brainstormRecovery.bind(
                sessionID: sessionID,
                brainstormID: brainstorm.id,
                ownerAccountID: accountIdentity
            )
        }
        try await recovery.remove(sessionID: sessionID)
        _ = machine.apply(.completionSucceeded)
        state = .completed(sessionID: sessionID)
        interruptedSessionID = nil
        warningMessage = ""
        errorMessage = ""
        if BrainstormWebHandoffPolicy.shouldOpenAfterCompletion(
            purpose: response.session.purpose
        ) {
            _ = openBrainstormResultInSixSentences()
        } else {
            refreshAskHistory()
        }
    }

    private func beginCompletionRetry(sessionID: String) {
        guard completionRetryTask == nil else { return }
        completionRetryTask = Task { [weak self] in
            guard let self else { return }
            defer { self.completionRetryTask = nil }
            var seconds = 1
            while !Task.isCancelled {
                do {
                    try await self.finishCompletion(sessionID: sessionID)
                    return
                } catch {
                    if self.isRetryable(error) {
                        self.warningMessage = self.localized(
                            "Offline gespeichert — neuer Versuch in \(seconds) s.",
                            "Saved offline — retrying in \(seconds)s."
                        )
                        try? await Task.sleep(for: .seconds(seconds))
                        seconds = min(seconds * 2, 30)
                    } else {
                        self.errorMessage = self.localize(error)
                        _ = self.machine.apply(.completionFailed(message: self.localize(error)))
                        self.state = .failed(message: self.localize(error))
                        self.interruptedSessionID = sessionID
                        return
                    }
                }
            }
        }
    }

    private func brainstormOutputLanguage(for rawLanguage: String?) -> BrainstormOutputLanguage {
        if let configured = BrainstormOutputLanguage(
            rawValue: preferences.brainstormOutputLanguage
        ) {
            return configured
        }
        return CompanionTranscriptionLanguage.validated(rawLanguage) == .german
            ? .german
            : .english
    }

    private func refreshConfigurationAndRecover() async {
        guard credentials.hasToken else {
            preferences.clearBrainstormProjectSelection()
            scrubCreatorPrivateMemory()
            hasCredential = false
            accountIdentity = nil
            config = nil
            apiClient = nil
            connectionState = .disconnected
            return
        }
        hasCredential = true
        connectionState = .validating
        do {
            try await outbox.validateIntegrity()
            try await recovery.validateIntegrity()
            try await pendingAskRecovery.validateIntegrity()
            try await brainstormRecovery.validateIntegrity()
            let api = try makeClient()
            let currentConfig = try await api.config()
            guard currentConfig.enabled else { throw CompanionFailure.disabled }
            try CompanionCompatibilityPolicy.validate(currentConfig)
            try activateAccountIdentity(currentConfig.accountID)
            apiClient = api
            config = currentConfig
            reconcileProjectPreferences(with: currentConfig)
            connectionState = .connected
            if let interrupted = await recovery.interruptedRecordings().first,
               let sessionID = interrupted.serverSessionID {
                let localSegments = await outbox.count(sessionID: sessionID)
                interruptedSessionID = sessionID
                do {
                    let serverSession = try await api.getSession(sessionID: sessionID)
                    session = serverSession
                    activeExperienceMode = serverSession.purpose == "brainstorm"
                        ? .brainstorm
                        : .conversation
                    confirmedTranscriptSequence = serverSession.completedThroughSequence
                        ?? serverSession.lastSegmentSequence
                    finalTranscriptCharacterCount = serverSession.transcriptCharacterCount ?? 0
                    let hasServerTranscript = (serverSession.lastSegmentSequence ?? 0) > 0
                        || serverSession.segmentCount > 0
                    if serverSession.status == "recording",
                       localSegments > 0 || hasServerTranscript {
                        try await recovery.requestCompletion(sessionID: sessionID)
                        state = .queuedOffline(sessionID: sessionID)
                        try await finishCompletion(sessionID: sessionID)
                        interruptedSessionID = nil
                    } else if serverSession.status == "recording" {
                        _ = try await api.cancel(sessionID: sessionID)
                        try await outbox.removeSession(sessionID: sessionID)
                        try await recovery.remove(sessionID: sessionID)
                        interruptedSessionID = nil
                    } else if serverSession.purpose == "brainstorm" {
                        state = .queuedOffline(sessionID: sessionID)
                        try await finishCompletion(sessionID: sessionID)
                        interruptedSessionID = nil
                    } else {
                        try await outbox.removeSession(sessionID: sessionID)
                        try await recovery.remove(sessionID: sessionID)
                        session = serverSession
                        refreshAskHistory()
                        interruptedSessionID = nil
                    }
                } catch {
                    warningMessage = localSegments > 0
                        ? localized(
                            "Eine frühere Aufnahme enthält lokal gesicherten Text. Schließe sie ab oder brich sie ab.",
                            "A previous recording has locally saved text. Complete or cancel it."
                        )
                        : localized(
                            "Eine frühere leere Aufnahme wird beim nächsten Verbindungsversuch abgebrochen.",
                            "A previous empty recording will be cancelled on the next connection attempt."
                        )
                }
            }
            for record in await recovery.pendingCompletions() {
                guard let sessionID = record.serverSessionID else { continue }
                state = .queuedOffline(sessionID: sessionID)
                do {
                    let serverSession = try await api.getSession(sessionID: sessionID)
                    session = serverSession
                    activeExperienceMode = serverSession.purpose == "brainstorm"
                        ? .brainstorm
                        : .conversation
                    confirmedTranscriptSequence = serverSession.completedThroughSequence
                        ?? serverSession.lastSegmentSequence
                    finalTranscriptCharacterCount = serverSession.transcriptCharacterCount ?? 0
                    try await finishCompletion(sessionID: sessionID)
                } catch {
                    if isRetryable(error) {
                        warningMessage = localized(
                            "Eine unterbrochene Sitzung wird weiter synchronisiert.",
                            "An interrupted session is still syncing."
                        )
                        beginCompletionRetry(sessionID: sessionID)
                    } else {
                        errorMessage = localize(error)
                        state = .failed(message: localize(error))
                        interruptedSessionID = sessionID
                    }
                }
            }
            try await recoverPendingAskIntents(using: api)
        } catch {
            if config == nil {
                connectionState = .failed
                connectionNotice = ""
                warningMessage = ""
                errorMessage = localize(error)
            } else {
                connectionState = .connected
                warningMessage = localize(error)
            }
        }
    }

    private func recoverPendingAskIntents(using api: CompanionAPIClient) async throws {
        typealias Candidate = (
            intent: RecoverablePendingAsk,
            session: LiveSession,
            receipts: [AskLiveSessionResponse],
            receipt: AskLiveSessionResponse
        )
        var latestPending: Candidate?
        var latestTerminal: (candidate: Candidate, failed: Bool)?

        for intent in try await pendingAskRecovery.all() {
            do {
                var receipts = try await api.asks(sessionID: intent.sessionID)
                var decision = PendingAskRelaunchDecision.resolve(
                    intent: intent,
                    receipts: receipts
                )
                let plan = PendingAskRelaunchPlan.make(
                    intent: intent,
                    receipts: receipts
                )
                if case let .replay(request) = plan {
                    let replay = try await api.ask(
                        sessionID: intent.sessionID,
                        request: request
                    )
                    receipts = try await api.asks(sessionID: intent.sessionID)
                    if !receipts.contains(where: {
                        $0.clientRequestID == intent.clientRequestID
                    }) {
                        receipts.append(replay)
                    }
                    decision = PendingAskRelaunchDecision.resolve(
                        intent: intent,
                        receipts: receipts
                    )
                }

                switch decision {
                case .missing:
                    // The replay response itself is a receipt, so this can only
                    // happen for a malformed server response. Preserve intent.
                    warningMessage = localized(
                        "Der Status einer gespeicherten Antwort ist noch unklar.",
                        "The status of a saved answer is still unclear."
                    )
                case let .pending(receipt):
                    latestPending = (
                        intent,
                        try await api.getSession(sessionID: intent.sessionID),
                        receipts,
                        receipt
                    )
                    latestTerminal = nil
                case let .completed(receipt):
                    let candidate: Candidate = (
                        intent,
                        try await api.getSession(sessionID: intent.sessionID),
                        receipts,
                        receipt
                    )
                    try await pendingAskRecovery.remove(
                        sessionID: intent.sessionID,
                        clientRequestID: intent.clientRequestID
                    )
                    latestTerminal = (candidate, false)
                    latestPending = nil
                case let .failed(receipt):
                    let candidate: Candidate = (
                        intent,
                        try await api.getSession(sessionID: intent.sessionID),
                        receipts,
                        receipt
                    )
                    try await pendingAskRecovery.remove(
                        sessionID: intent.sessionID,
                        clientRequestID: intent.clientRequestID
                    )
                    latestTerminal = (candidate, true)
                    latestPending = nil
                }
            } catch is CancellationError {
                throw CancellationError()
            } catch {
                if isTerminalPendingAskReplayError(error) {
                    try? await pendingAskRecovery.remove(
                        sessionID: intent.sessionID,
                        clientRequestID: intent.clientRequestID
                    )
                } else {
                    warningMessage = localized(
                        "Eine gespeicherte Antwort wird wieder synchronisiert, sobald die Verbindung verfügbar ist.",
                        "A saved answer will resume syncing when the connection is available."
                    )
                }
            }
        }

        if let latestPending {
            session = latestPending.session
            askHistory = latestPending.receipts
            resumePendingAsk(latestPending.receipt, sessionID: latestPending.intent.sessionID)
        } else if let latestTerminal {
            session = latestTerminal.candidate.session
            askHistory = latestTerminal.candidate.receipts
            if latestTerminal.failed {
                answer = ""
                askErrorMessage = localized("Antwort fehlgeschlagen.", "Answer failed.")
            } else {
                answer = latestTerminal.candidate.receipt.answer
                askErrorMessage = ""
            }
        }
    }

    private func isTerminalPendingAskReplayError(_ error: Error) -> Bool {
        guard let apiError = error as? APIClientError,
              case let .server(status, code, _) = apiError
        else { return false }
        return status == 403
            || status == 404
            || status == 410
            || [
                "no_live_transcript",
                "ask_request_conflict",
                "context_not_available",
                "capacity_unavailable",
                "session_not_recording",
            ].contains(code ?? "")
    }

    private func removeTerminalAskIntents(
        sessionID: String,
        receipts: [AskLiveSessionResponse]
    ) async {
        for receipt in receipts where receipt.status == "completed" || receipt.status == "failed" {
            try? await pendingAskRecovery.remove(
                sessionID: sessionID,
                clientRequestID: receipt.clientRequestID
            )
        }
    }

    private func exchangePairingCode(_ code: String, verifier: String) async {
        do {
            guard canChangeAccount else {
                throw CompanionFailure.cannotChangeAccountWhileActive
            }
            // The one-use code exists only in memory. Only the returned API
            // key is persisted, and it goes directly into Keychain.
            let response = try await CompanionAPIClient.exchangePairingCode(
                baseURLString: preferences.apiBaseURL,
                code: code,
                codeVerifier: verifier
            )
            guard canChangeAccount else {
                throw CompanionFailure.cannotChangeAccountWhileActive
            }
            scrubCreatorPrivateMemory()
            accountIdentity = nil
            try credentials.save(token: response.apiKey)
            try pairing.clear()
            hasCredential = true
            connectionState = .validating
            warningMessage = ""
            connectionNotice = localized(
                "Mit SixSentences verbunden — Zugang sicher im Schlüsselbund gespeichert.",
                "Connected to SixSentences — access stored securely in Keychain."
            )
            await refreshConfigurationAndRecover()
        } catch {
            if !isRetryable(error) { try? pairing.clear() }
            connectionState = .failed
            errorMessage = localize(error)
        }
    }

    private func makeClient() throws -> CompanionAPIClient {
        try CompanionAPIClient(baseURLString: preferences.apiBaseURL, credentials: credentials)
    }

    private func activateAccountIdentity(_ rawValue: String) throws {
        let clean = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty else { throw CompanionFailure.accountIdentityUnavailable }
        if let accountIdentity, accountIdentity != clean {
            guard canChangeAccount else {
                throw CompanionFailure.cannotChangeAccountWhileActive
            }
            preferences.clearBrainstormProjectSelection()
            scrubCreatorPrivateMemory()
        }
        accountIdentity = clean
    }

    private func reconcileProjectPreferences(with config: LiveConfig) {
        if let selected = preferences.projectID,
           !config.projects.contains(where: { $0.id == selected }) {
            preferences.projectID = nil
        }
        guard let accountIdentity else {
            brainstormProjectID = nil
            return
        }
        brainstormProjectID = preferences.brainstormProjectID(
            ownerAccountID: accountIdentity,
            availableProjects: config.projects
        )
    }

    private func client() throws -> CompanionAPIClient {
        if let apiClient { return apiClient }
        let created = try makeClient()
        apiClient = created
        return created
    }

    private func localized(_ german: String, _ english: String) -> String {
        copy(german, english)
    }

    private func localize(_ error: Error) -> String {
        if let apiError = error as? APIClientError,
           case let .server(status, code, _) = apiError {
            if let message = CompanionAccessErrorCopy.localized(status: status, code: code, usesGerman: usesGerman) {
                return message
            }
            if activeExperienceMode == .brainstorm {
                return BrainstormErrorCopy.localized(code: code, usesGerman: usesGerman)
            }
            if !usesGerman { return error.localizedDescription }
            switch code {
            case "no_live_transcript":
                return "Du kannst fragen, sobald der erste Satz sicher transkribiert ist."
            case "ask_pending":
                return "Die Antwort wird noch vorbereitet."
            case "ask_request_conflict":
                return "Diese Anfrage-ID wurde bereits für eine andere Frage verwendet."
            case "context_not_available":
                return "Das aktuelle Transkript wird noch synchronisiert. Versuche es gleich erneut."
            case "ask_failed":
                return "Die belegte Antwort ist fehlgeschlagen. Stelle die Frage erneut."
            case "capacity_unavailable":
                return "Für Live-Antworten ist gerade keine Kapazität verfügbar. Versuche es später erneut."
            case "session_not_recording":
                return "Fragen sind nur während einer laufenden Sitzung möglich."
            default:
                break
            }
        }
        if !usesGerman { return error.localizedDescription }
        switch error {
        case CaptureError.microphoneDenied:
            return "Der Mikrofonzugriff ist erforderlich."
        case CaptureError.speechDenied:
            return "Der Zugriff auf die Spracherkennung ist erforderlich."
        case CaptureError.screenAudioDenied:
            return "Bildschirm- & Systemaudiozugriff ist erforderlich. Es werden keine Videobilder erfasst."
        case let CaptureError.speechUnavailable(language):
            return "Lokale Apple-Spracherkennung unterstützt \(language) auf diesem Mac nicht. Wähle in den Companion-Einstellungen eine andere Sprache."
        case let CaptureError.speechModelUnavailable(language):
            return "Das lokale Apple-Sprachmodell für \(language) ist nicht verfügbar. Stelle einmal eine Internetverbindung her, damit macOS es installieren kann, und versuche es erneut."
        case CompanionFailure.noFinalTranscript:
            return "Es wurde noch kein finales Transkript erkannt. Die leere Sitzung wurde abgebrochen."
        case CompanionFailure.transcriptPersistenceFailed:
            return "Finaler Transkripttext konnte nicht lokal gespeichert werden. Die Anfrage wurde deshalb gestoppt."
        case CompanionFailure.noAskableTranscript:
            return "Du kannst fragen, sobald der erste Satz sicher transkribiert ist."
        case CompanionFailure.transcriptCutoffUnconfirmed:
            return "Das aktuelle Transkript wird noch synchronisiert. Versuche es gleich erneut."
        case PendingAskRecoveryError.unreadableStore:
            return "Gespeicherte ausstehende Antworten konnten nicht gelesen werden und wurden zur Wiederherstellung unverändert gelassen."
        case PendingAskRecoveryError.conflictingRequest:
            return "Eine ausstehende Antwort-ID kann nicht für eine andere Frage wiederverwendet werden."
        case CompanionFailure.askSessionChanged:
            return "Die Live-Sitzung wurde geändert, bevor die Antwort abgeschlossen war."
        case let CompanionCompatibilityError.updateRequired(current, minimum):
            return "SixSentences Companion \(minimum) oder neuer ist erforderlich. Auf diesem Mac läuft \(current). Bitte aktualisiere die App."
        case let CompanionCompatibilityError.invalidClientVersion(version):
            return "Diese Companion-Version ist ungültig (\(version)). Installiere eine aktuelle Version."
        case let CompanionCompatibilityError.invalidMinimumVersion(version):
            return "SixSentences hat eine ungültige Mindestversion zurückgegeben (\(version)). Die App wurde vorsichtshalber gestoppt."
        default:
            return error.localizedDescription
        }
    }

    private func safeWebURL(_ rawValue: String) -> URL? {
        preferences.validatedWebURL(rawValue)
    }

    private func isRetryable(_ error: Error) -> Bool {
        if error is URLError { return true }
        guard let apiError = error as? APIClientError else { return false }
        if case let .server(status, _, _) = apiError {
            return status == 408 || status == 429 || status >= 500
        }
        return false
    }
}

enum CompanionAccountTransitionPolicy {
    static func canChangeAccount(
        state: CompanionSessionState,
        hasInterruptedSession: Bool
    ) -> Bool {
        guard !hasInterruptedSession else { return false }
        switch state {
        case .idle, .completed, .failed:
            return true
        case .awaitingConsent, .starting, .recording, .stopping, .queuedOffline:
            return false
        }
    }
}

private enum CompanionFailure: LocalizedError {
    case disabled
    case noFinalTranscript
    case transcriptPersistenceFailed
    case noAskableTranscript
    case transcriptCutoffUnconfirmed
    case askSessionChanged
    case startCancelled
    case cannotOpenBrowser
    case cannotDisconnectWhileRecording
    case accountIdentityUnavailable
    case cannotChangeAccountWhileActive

    var errorDescription: String? {
        switch self {
        case .disabled: "Live Companion is disabled for this account."
        case .noFinalTranscript: "No final transcript was recognized; the empty session was cancelled."
        case .transcriptPersistenceFailed: "Final transcript text could not be stored locally, so the request was stopped."
        case .noAskableTranscript: "You can ask as soon as the first sentence is ready."
        case .transcriptCutoffUnconfirmed: "The latest transcript is still syncing. Try again shortly."
        case .askSessionChanged: "The live session changed before the answer completed."
        case .startCancelled: "The session start was cancelled."
        case .cannotOpenBrowser: "The browser could not be opened."
        case .cannotDisconnectWhileRecording: "Stop the recording before disconnecting SixSentences."
        case .accountIdentityUnavailable: "SixSentences could not verify the connected account identity."
        case .cannotChangeAccountWhileActive: "Finish or cancel the active session before changing the connected account."
        }
    }
}

private extension String {
    var nonEmpty: String? { isEmpty ? nil : self }
}
