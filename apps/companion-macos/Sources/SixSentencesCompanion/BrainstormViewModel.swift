import Foundation

@MainActor
final class BrainstormViewModel: ObservableObject {
    @Published var outputLanguage: BrainstormOutputLanguage {
        didSet { preferences.setBrainstormOutputLanguage(outputLanguage) }
    }
    @Published private(set) var receipt: BrainstormReceipt?
    @Published private(set) var isSubmitting = false
    @Published private(set) var isRecovering = false
    @Published private(set) var isCancelling = false
    @Published private(set) var errorMessage = ""
    @Published private(set) var noticeMessage = ""

    private let preferences: CompanionPreferences
    private let credentials: any CompanionCredentialProvider
    private let recovery: BrainstormRecoveryStore
    private var task: Task<Void, Never>?
    private var activeSessionID: String?
    private var activeAccountID: String?

    init(
        preferences: CompanionPreferences? = nil,
        credentials: any CompanionCredentialProvider = KeychainCredentialProvider.shared,
        recovery: BrainstormRecoveryStore = .shared,
        preferredLanguages: [String] = Locale.preferredLanguages
    ) {
        let resolvedPreferences = preferences ?? .shared
        self.preferences = resolvedPreferences
        self.credentials = credentials
        self.recovery = recovery
        outputLanguage = BrainstormOutputLanguage(rawValue: resolvedPreferences.brainstormOutputLanguage)
            ?? (SpeechLocaleResolver.usesGerman(
                language: "auto",
                preferredLanguages: preferredLanguages
            ) ? .german : .english)
    }

    deinit { task?.cancel() }

    var result: BrainstormResult? {
        guard receipt?.schemaVersion == 1 else { return nil }
        return receipt?.result
    }
    var hasPendingResult: Bool { receipt?.status == "pending" }
    var resultOutputLanguage: BrainstormOutputLanguage {
        receipt?.outputLanguage ?? outputLanguage
    }

    func prepareForNewSession() {
        task?.cancel()
        task = nil
        activeSessionID = nil
        receipt = nil
        isSubmitting = false
        isRecovering = false
        isCancelling = false
        errorMessage = ""
        noticeMessage = ""
    }

    func scrubPrivateState() {
        task?.cancel()
        task = nil
        activeSessionID = nil
        activeAccountID = nil
        receipt = nil
        isSubmitting = false
        isRecovering = false
        isCancelling = false
        errorMessage = ""
        noticeMessage = ""
    }

    /// Applies the receipt returned by the mode-specific Complete call. This
    /// is the primary path: it never creates a second synthesis request.
    func acceptCompletionReceipt(
        sessionID: String,
        receipt: BrainstormReceipt,
        ownerAccountID: String?
    ) {
        guard let ownerAccountID = normalizedAccountID(ownerAccountID) else {
            scrubPrivateState()
            return
        }
        task?.cancel()
        activeAccountID = ownerAccountID
        activeSessionID = sessionID
        outputLanguage = receipt.outputLanguage
        apply(receipt, sessionID: sessionID, ownerAccountID: ownerAccountID)
        task = Task { [weak self] in
            guard let self else { return }
            do {
                try await self.recovery.bind(
                    sessionID: sessionID,
                    brainstormID: receipt.id,
                    ownerAccountID: ownerAccountID
                )
                if receipt.schemaVersion == 1,
                   BrainstormSubmissionPolicy.shouldContinuePolling(status: receipt.status) {
                    await self.poll(
                        client: try self.makeClient(),
                        sessionID: sessionID,
                        brainstormID: receipt.id,
                        ownerAccountID: ownerAccountID
                    )
                }
            } catch is CancellationError {
                return
            } catch {
                self.isSubmitting = false
                self.errorMessage = self.localizedError(error)
            }
        }
    }

    /// Resumes the exact locally persisted idempotency key after relaunch.
    /// If Complete reached the server but its response was lost, list
    /// reconciliation finds the same receipt without creating new work.
    func recoverPendingIntent(ownerAccountID: String?) {
        guard let ownerAccountID = normalizedAccountID(ownerAccountID) else {
            scrubPrivateState()
            return
        }
        guard !isRecovering, !isSubmitting else { return }
        task?.cancel()
        if activeAccountID != nil, activeAccountID != ownerAccountID {
            scrubPrivateState()
        }
        activeAccountID = ownerAccountID
        isRecovering = true
        task = Task { [weak self] in
            guard let self else { return }
            defer { self.isRecovering = false }
            var recoverySessionID: String?
            do {
                guard let intent = try await self.recovery.all(
                    ownerAccountID: ownerAccountID
                ).last else { return }
                recoverySessionID = intent.sessionID
                self.activeSessionID = intent.sessionID
                self.outputLanguage = intent.request.outputLanguage
                let client = try self.makeClient()
                let recovered: BrainstormReceipt
                if let brainstormID = intent.brainstormID,
                   let exact = try? await client.brainstorm(
                       sessionID: intent.sessionID,
                       brainstormID: brainstormID
                   ) {
                    recovered = exact
                } else {
                    let response = try await client.brainstorms(sessionID: intent.sessionID)
                    if let exact = response.brainstorms.first(where: {
                        $0.clientRequestID == intent.request.clientRequestID
                    }) {
                        recovered = exact
                    } else {
                        recovered = try await client.createBrainstorm(
                            sessionID: intent.sessionID,
                            request: intent.request
                        )
                    }
                }
                try await self.recovery.bind(
                    sessionID: intent.sessionID,
                    brainstormID: recovered.id,
                    ownerAccountID: ownerAccountID
                )
                try Task.checkCancellation()
                guard self.activeAccountID == ownerAccountID else { return }
                self.apply(
                    recovered,
                    sessionID: intent.sessionID,
                    ownerAccountID: ownerAccountID
                )
                if recovered.schemaVersion == 1,
                   BrainstormSubmissionPolicy.shouldContinuePolling(status: recovered.status) {
                    await self.poll(
                        client: client,
                        sessionID: intent.sessionID,
                        brainstormID: recovered.id,
                        ownerAccountID: ownerAccountID
                    )
                }
            } catch is CancellationError {
                return
            } catch {
                guard self.activeAccountID == ownerAccountID else { return }
                self.isSubmitting = false
                if let recoverySessionID,
                   BrainstormRecoveryPolicy.shouldDiscardIntent(after: error) {
                    await self.discardStaleRecovery(
                        sessionID: recoverySessionID,
                        ownerAccountID: ownerAccountID
                    )
                } else if recoverySessionID != nil,
                          BrainstormRecoveryPolicy.shouldWaitForReconnect(after: error) {
                    // Keep the immutable intent untouched. A connected-state
                    // transition or an explicit refresh retries the exact ID.
                    self.errorMessage = ""
                    self.noticeMessage = self.localized(
                        "Die ausstehende Strukturierung wird nach der Verbindung wiederhergestellt.",
                        "The pending structuring run will resume after reconnecting."
                    )
                } else {
                    self.errorMessage = self.localizedError(error)
                }
            }
        }
    }

    func recover(sessionID: String, ownerAccountID: String?) {
        guard !sessionID.isEmpty,
              let ownerAccountID = normalizedAccountID(ownerAccountID)
        else { return }
        guard activeSessionID != sessionID || receipt == nil || receipt?.status == "pending" else {
            return
        }
        task?.cancel()
        if activeAccountID != nil, activeAccountID != ownerAccountID {
            scrubPrivateState()
        }
        activeAccountID = ownerAccountID
        activeSessionID = sessionID
        isRecovering = true
        errorMessage = ""
        task = Task { [weak self] in
            guard let self else { return }
            defer { self.isRecovering = false }
            do {
                let client = try self.makeClient()
                let response = try await client.brainstorms(sessionID: sessionID)
                try Task.checkCancellation()
                guard self.activeSessionID == sessionID,
                      self.activeAccountID == ownerAccountID
                else { return }
                let selected: BrainstormReceipt?
                if let intent = try await self.recovery.intent(
                    sessionID: sessionID,
                    ownerAccountID: ownerAccountID
                ) {
                    selected = response.brainstorms.first {
                        $0.clientRequestID == intent.request.clientRequestID
                    }
                } else {
                    selected = response.brainstorms.max { $0.createdAt < $1.createdAt }
                }
                guard let selected else { return }
                self.outputLanguage = selected.outputLanguage
                self.apply(
                    selected,
                    sessionID: sessionID,
                    ownerAccountID: ownerAccountID
                )
                if selected.schemaVersion == 1,
                   BrainstormSubmissionPolicy.shouldContinuePolling(status: selected.status) {
                    await self.poll(
                        client: client,
                        sessionID: sessionID,
                        brainstormID: selected.id,
                        ownerAccountID: ownerAccountID
                    )
                }
            } catch is CancellationError {
                return
            } catch {
                guard self.activeSessionID == sessionID,
                      self.activeAccountID == ownerAccountID
                else { return }
                if BrainstormRecoveryPolicy.shouldDiscardIntent(after: error) {
                    await self.discardStaleRecovery(
                        sessionID: sessionID,
                        ownerAccountID: ownerAccountID
                    )
                } else {
                    self.errorMessage = self.localizedError(error)
                }
            }
        }
    }

    /// Explicit re-structure uses a new durable request only after a terminal
    /// result. The caller must provide the server-confirmed transcript cutoff.
    func structure(
        sessionID: String,
        contextThroughSequence: Int,
        ownerAccountID: String?
    ) {
        guard !sessionID.isEmpty,
              contextThroughSequence >= 1,
              !isSubmitting,
              let ownerAccountID = normalizedAccountID(ownerAccountID)
        else { return }
        task?.cancel()
        if activeAccountID != nil, activeAccountID != ownerAccountID {
            scrubPrivateState()
        }
        activeAccountID = ownerAccountID
        activeSessionID = sessionID
        errorMessage = ""
        noticeMessage = localized(
            "Deine Gedanken werden geordnet. Du kannst dieses Fenster geöffnet lassen.",
            "Your thoughts are being organized. You can leave this window open."
        )
        isSubmitting = true
        let request = CreateBrainstormRequest(
            clientRequestID: UUID().uuidString.lowercased(),
            outputLanguage: outputLanguage,
            contextThroughSequence: contextThroughSequence
        )
        task = Task { [weak self] in
            guard let self else { return }
            do {
                if self.receipt?.status == "completed" || self.receipt?.status == "failed" {
                    try await self.recovery.remove(
                        sessionID: sessionID,
                        ownerAccountID: ownerAccountID
                    )
                }
                try await self.recovery.save(
                    RecoverableBrainstormIntent(
                        ownerAccountID: ownerAccountID,
                        sessionID: sessionID,
                        request: request,
                        brainstormID: nil,
                        createdAt: Date()
                    )
                )
                let client = try self.makeClient()
                let created: BrainstormReceipt
                do {
                    created = try await client.createBrainstorm(
                        sessionID: sessionID,
                        request: request
                    )
                } catch {
                    let response = try? await client.brainstorms(sessionID: sessionID)
                    if let recovered = response?.brainstorms.first(where: {
                        $0.clientRequestID == request.clientRequestID
                    }) {
                        created = recovered
                    } else {
                        throw error
                    }
                }
                try Task.checkCancellation()
                guard self.activeAccountID == ownerAccountID else { return }
                try await self.recovery.bind(
                    sessionID: sessionID,
                    brainstormID: created.id,
                    ownerAccountID: ownerAccountID
                )
                self.apply(
                    created,
                    sessionID: sessionID,
                    ownerAccountID: ownerAccountID
                )
                if created.schemaVersion == 1,
                   BrainstormSubmissionPolicy.shouldContinuePolling(status: created.status) {
                    await self.poll(
                        client: client,
                        sessionID: sessionID,
                        brainstormID: created.id,
                        ownerAccountID: ownerAccountID
                    )
                }
            } catch is CancellationError {
                return
            } catch {
                guard self.activeSessionID == sessionID,
                      self.activeAccountID == ownerAccountID
                else { return }
                self.isSubmitting = false
                self.noticeMessage = ""
                self.errorMessage = self.localizedError(error)
            }
        }
    }

    func retry(
        sessionID: String,
        contextThroughSequence: Int,
        ownerAccountID: String?
    ) {
        structure(
            sessionID: sessionID,
            contextThroughSequence: contextThroughSequence,
            ownerAccountID: ownerAccountID
        )
    }

    func refreshStatus() {
        guard let activeSessionID, let activeAccountID else { return }
        task?.cancel()
        task = nil
        recover(sessionID: activeSessionID, ownerAccountID: activeAccountID)
    }

    func cancelPending() {
        guard let sessionID = activeSessionID,
              let ownerAccountID = activeAccountID,
              let receipt,
              receipt.status == "pending",
              !isCancelling
        else { return }
        task?.cancel()
        isCancelling = true
        task = Task { [weak self] in
            guard let self else { return }
            defer { self.isCancelling = false }
            do {
                let cancelled = try await self.makeClient().cancelBrainstorm(
                    sessionID: sessionID,
                    brainstormID: receipt.id
                )
                try Task.checkCancellation()
                guard self.activeAccountID == ownerAccountID else { return }
                self.apply(
                    cancelled,
                    sessionID: sessionID,
                    ownerAccountID: ownerAccountID
                )
            } catch is CancellationError {
                return
            } catch {
                self.isSubmitting = false
                self.noticeMessage = ""
                self.errorMessage = self.localizedError(error)
            }
        }
    }

    private func poll(
        client: CompanionAPIClient,
        sessionID: String,
        brainstormID: String,
        ownerAccountID: String
    ) async {
        for attempt in 0 ..< 300 {
            guard !Task.isCancelled,
                  activeSessionID == sessionID,
                  activeAccountID == ownerAccountID
            else { return }
            if attempt > 0 {
                do { try await Task.sleep(for: .seconds(1)) } catch { return }
            }
            do {
                let current = try await client.brainstorm(
                    sessionID: sessionID,
                    brainstormID: brainstormID
                )
                try Task.checkCancellation()
                guard activeSessionID == sessionID,
                      activeAccountID == ownerAccountID
                else { return }
                apply(
                    current,
                    sessionID: sessionID,
                    ownerAccountID: ownerAccountID
                )
                guard BrainstormSubmissionPolicy.shouldContinuePolling(status: current.status) else {
                    return
                }
            } catch is CancellationError {
                return
            } catch {
                if BrainstormRecoveryPolicy.shouldDiscardIntent(after: error) {
                    await discardStaleRecovery(
                        sessionID: sessionID,
                        ownerAccountID: ownerAccountID
                    )
                    return
                }
                if isTerminalPollingError(error) {
                    isSubmitting = false
                    errorMessage = localizedError(error)
                    return
                }
            }
        }
        guard activeSessionID == sessionID,
              activeAccountID == ownerAccountID
        else { return }
        isSubmitting = false
        noticeMessage = localized(
            "Die Strukturierung läuft länger als erwartet. Mit „Status laden“ kannst du sie später fortsetzen.",
            "Structuring is taking longer than expected. Use “Refresh status” to resume it later."
        )
    }

    private func apply(
        _ value: BrainstormReceipt,
        sessionID: String,
        ownerAccountID: String
    ) {
        guard activeAccountID == ownerAccountID else { return }
        guard value.schemaVersion == 1 else {
            // Keep the bound receipt and durable intent so a future compatible
            // app can resume it. `result` remains fail-closed for unknown schemas.
            receipt = value
            isSubmitting = false
            isRecovering = false
            errorMessage = localized(
                "Diese Brainstorm-Struktur verwendet eine neuere, nicht unterstützte Version.",
                "This brainstorm uses a newer, unsupported structure version."
            )
            return
        }
        receipt = value
        outputLanguage = value.outputLanguage
        isRecovering = false
        switch value.status {
        case "completed":
            isSubmitting = false
            noticeMessage = localized(
                "Brainstorming strukturiert und sicher in SixSentences gespeichert.",
                "Brainstorm organized and saved securely in SixSentences."
            )
            errorMessage = ""
            Task {
                try? await recovery.remove(
                    sessionID: sessionID,
                    ownerAccountID: ownerAccountID
                )
            }
        case "failed":
            isSubmitting = false
            noticeMessage = ""
            errorMessage = BrainstormErrorCopy.localized(
                code: value.errorCode,
                usesGerman: SpeechLocaleResolver.usesGerman(language: "auto")
            )
            Task {
                try? await recovery.remove(
                    sessionID: sessionID,
                    ownerAccountID: ownerAccountID
                )
            }
        default:
            isSubmitting = true
            errorMessage = ""
        }
    }

    private func makeClient() throws -> CompanionAPIClient {
        try CompanionAPIClient(
            baseURLString: preferences.apiBaseURL,
            credentials: credentials
        )
    }

    private func normalizedAccountID(_ value: String?) -> String? {
        let clean = value?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return clean.isEmpty ? nil : clean
    }

    private func discardStaleRecovery(
        sessionID: String,
        ownerAccountID: String
    ) async {
        try? await recovery.remove(
            sessionID: sessionID,
            ownerAccountID: ownerAccountID
        )
        guard activeSessionID == sessionID,
              activeAccountID == ownerAccountID
        else { return }
        receipt = nil
        activeSessionID = nil
        isSubmitting = false
        isRecovering = false
        errorMessage = ""
        noticeMessage = localized(
            "Die gelöschte Brainstorm-Sitzung wurde aus der Wiederherstellung entfernt.",
            "The deleted brainstorm was removed from recovery."
        )
    }

    private func isTerminalPollingError(_ error: Error) -> Bool {
        guard let apiError = error as? APIClientError,
              case let .server(status, _, _) = apiError
        else { return false }
        return [400, 401, 403, 404, 409, 410, 422].contains(status)
    }

    private func localized(_ german: String, _ english: String) -> String {
        SpeechLocaleResolver.usesGerman(language: "auto") ? german : english
    }

    private func localizedError(_ error: Error) -> String {
        let usesGerman = SpeechLocaleResolver.usesGerman(language: "auto")
        if let apiError = error as? APIClientError,
           case let .server(status, code, _) = apiError {
            if let message = CompanionAccessErrorCopy.localized(status: status, code: code, usesGerman: usesGerman) {
                return message
            }
            return BrainstormErrorCopy.localized(code: code, usesGerman: usesGerman)
        }
        return usesGerman
            ? "Die Brainstorm-Anfrage konnte nicht abgeschlossen werden. Versuche es erneut."
            : "The brainstorm request could not be completed. Try again."
    }
}

enum BrainstormRecoveryPolicy {
    static func shouldRetryAfterConnectionChange(
        wasConnected: Bool,
        isConnected: Bool
    ) -> Bool {
        !wasConnected && isConnected
    }

    static func shouldDiscardIntent(after error: Error) -> Bool {
        guard let apiError = error as? APIClientError,
              case let .server(status, _, _) = apiError
        else { return false }
        return status == 404 || status == 410
    }

    static func shouldWaitForReconnect(after error: Error) -> Bool {
        if error is URLError { return true }
        guard let apiError = error as? APIClientError else {
            return !(error is BrainstormRecoveryError)
        }
        switch apiError {
        case .invalidResponse, .responseTooLarge:
            return true
        case let .server(status, _, _):
            return status == 401 || status == 403 || status == 408
                || status == 429 || status >= 500
        case .invalidBaseURL, .insecureBaseURL:
            return false
        }
    }
}
