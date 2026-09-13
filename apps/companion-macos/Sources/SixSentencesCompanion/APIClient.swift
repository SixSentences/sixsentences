import Foundation

enum APIClientError: LocalizedError, Equatable {
    case invalidBaseURL
    case insecureBaseURL
    case invalidResponse
    case responseTooLarge
    case server(status: Int, code: String?, detail: String)

    var errorDescription: String? {
        switch self {
        case .invalidBaseURL:
            "The API base URL is invalid."
        case .insecureBaseURL:
            "Use HTTPS. HTTP is accepted only for localhost development."
        case .invalidResponse:
            "SixSentences returned an invalid response."
        case .responseTooLarge:
            "SixSentences returned an unexpectedly large response."
        case let .server(status, code, _):
            CompanionAccessErrorCopy.localized(status: status, code: code, usesGerman: false)
                ?? "SixSentences request failed (HTTP \(status))."
        }
    }
}

/// Stable local copy for access boundaries; server diagnostics are never displayed.
enum CompanionAccessErrorCopy {
    static func localized(status: Int, code: String?, usesGerman: Bool) -> String? {
        if status == 401 {
            return usesGerman
                ? "Die Verbindung muss erneuert werden. Verbinde den Companion erneut mit SixSentences."
                : "Your connection needs to be renewed. Reconnect Companion to SixSentences."
        }
        if status == 403 {
            return usesGerman
                ? "Dieses Konto hat keine Berechtigung für diese Aktion."
                : "This account does not have permission to perform this action."
        }
        guard status == 402 else { return nil }
        return operatorLimit(usesGerman: usesGerman)
    }

    static func operatorLimit(usesGerman: Bool) -> String {
        usesGerman
            ? "Diese Aktion überschreitet eine vom Betreiber konfigurierte Funktions- oder Kapazitätsgrenze. Verkleinere den Umfang oder versuche es später erneut."
            : "This action exceeds an operator-configured feature or capacity limit. Reduce the request or try again later."
    }
}

enum AskReconciliationOutcome: Equatable, Sendable {
    case completed(AskLiveSessionResponse)
    case failed(AskLiveSessionResponse)
    case notFound
    case timedOut(lastReceipt: AskLiveSessionResponse?)
}

private final class AskReconciliationResolution: @unchecked Sendable {
    private let lock = NSLock()
    private var outcome: AskReconciliationOutcome?
    private var lastReceipt: AskLiveSessionResponse?
    private var continuation: CheckedContinuation<AskReconciliationOutcome, Never>?

    func note(_ receipt: AskLiveSessionResponse) {
        lock.lock()
        lastReceipt = receipt
        lock.unlock()
    }

    func wait() async -> AskReconciliationOutcome {
        await withCheckedContinuation { continuation in
            lock.lock()
            if let outcome {
                lock.unlock()
                continuation.resume(returning: outcome)
                return
            }
            self.continuation = continuation
            lock.unlock()
        }
    }

    @discardableResult
    func resolve(_ value: AskReconciliationOutcome) -> Bool {
        lock.lock()
        guard outcome == nil else {
            lock.unlock()
            return false
        }
        outcome = value
        let waiter = continuation
        continuation = nil
        lock.unlock()
        waiter?.resume(returning: value)
        return true
    }

    @discardableResult
    func resolveTimeout() -> Bool {
        lock.lock()
        guard outcome == nil else {
            lock.unlock()
            return false
        }
        let value = AskReconciliationOutcome.timedOut(lastReceipt: lastReceipt)
        outcome = value
        let waiter = continuation
        continuation = nil
        lock.unlock()
        waiter?.resume(returning: value)
        return true
    }
}

enum AskReconciler {
    static let defaultMaximumDuration: Duration = .seconds(300)
    static let defaultBackoff: [Duration] = [
        .zero,
        .milliseconds(250),
        .milliseconds(500),
        .seconds(1),
    ]

    /// Reconciles an indeterminate POST with its durable server receipt. The
    /// same client request ID is used throughout, so polling never creates or
    /// charges a second answer.
    static func reconcile(
        clientRequestID: String,
        maximumDuration: Duration = defaultMaximumDuration,
        backoff: [Duration] = defaultBackoff,
        initialReceipt: AskLiveSessionResponse? = nil,
        missingReceiptLimit: Int = 2,
        onReceipt: @MainActor @escaping (AskLiveSessionResponse) -> Void = { _ in },
        fetch: @escaping @Sendable () async throws -> [AskLiveSessionResponse]
    ) async -> AskReconciliationOutcome {
        let resolution = AskReconciliationResolution()
        if let initialReceipt { resolution.note(initialReceipt) }
        let pollingTask = Task {
            var attempt = 0
            var hasSeenReceipt = initialReceipt != nil
            var missingReceiptCount = 0
            while !Task.isCancelled {
                let delay = backoff.isEmpty
                    ? Duration.seconds(1)
                    : backoff[min(attempt, backoff.count - 1)]
                if delay > .zero {
                    do {
                        try await Task.sleep(for: delay)
                    } catch {
                        return
                    }
                }
                guard !Task.isCancelled else { return }
                attempt += 1
                do {
                    let receipts = try await fetch()
                    guard !Task.isCancelled else { return }
                    let receipt = receipts.last(where: {
                        $0.clientRequestID == clientRequestID
                    })
                    guard let receipt else {
                        if !hasSeenReceipt {
                            missingReceiptCount += 1
                            if missingReceiptCount >= max(1, missingReceiptLimit) {
                                resolution.resolve(.notFound)
                                return
                            }
                        }
                        continue
                    }
                    hasSeenReceipt = true
                    resolution.note(receipt)
                    await onReceipt(receipt)
                    switch receipt.status {
                    case "completed":
                        resolution.resolve(.completed(receipt))
                        return
                    case "failed":
                        resolution.resolve(.failed(receipt))
                        return
                    default:
                        continue
                    }
                } catch is CancellationError {
                    return
                } catch {
                    // A GET failure says nothing about the already-submitted
                    // POST. Retry without changing its idempotency key.
                    continue
                }
            }
        }
        let timeoutTask = Task {
            do {
                try await Task.sleep(for: maximumDuration)
            } catch {
                return
            }
            resolution.resolveTimeout()
        }
        let outcome = await withTaskCancellationHandler {
            await resolution.wait()
        } onCancel: {
            resolution.resolveTimeout()
        }
        pollingTask.cancel()
        timeoutTask.cancel()
        return outcome
    }
}

private final class NoRedirectDelegate: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest,
        completionHandler: @escaping (URLRequest?) -> Void
    ) {
        // A redirect could forward the Authorization header to an unexpected
        // origin. API deployments must expose their canonical URL directly.
        completionHandler(nil)
    }
}

actor CompanionAPIClient {
    static let maximumResponseBytes = 2_000_000
    static let paperResourceTimeout: TimeInterval = 320

    private let baseURL: URL
    private let credentials: CompanionCredentialProvider
    private let session: URLSession
    private let paperSession: URLSession
    private let encoder: JSONEncoder
    private let decoder: JSONDecoder
    private let clientVersion: String
    private let clientBuild: String

    init(
        baseURLString: String,
        credentials: CompanionCredentialProvider = KeychainCredentialProvider.shared,
        session injectedSession: URLSession? = nil,
        clientVersion: String = CompanionApplicationVersion.current,
        clientBuild: String = CompanionApplicationVersion.currentBuild
    ) throws {
        baseURL = try Self.validateBaseURL(baseURLString)
        self.credentials = credentials
        self.clientVersion = clientVersion
        self.clientBuild = clientBuild
        if let injectedSession {
            session = injectedSession
            paperSession = injectedSession
        } else {
            let configuration = Self.sessionConfiguration(forPaper: false)
            session = URLSession(
                configuration: configuration,
                delegate: NoRedirectDelegate(),
                delegateQueue: nil
            )
            let paperConfiguration = Self.sessionConfiguration(forPaper: true)
            paperSession = URLSession(
                configuration: paperConfiguration,
                delegate: NoRedirectDelegate(),
                delegateQueue: nil
            )
        }
        encoder = JSONEncoder()
        decoder = JSONDecoder()
    }

    static func sessionConfiguration(forPaper: Bool) -> URLSessionConfiguration {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.waitsForConnectivity = false
        configuration.timeoutIntervalForRequest = forPaper ? paperResourceTimeout : 20
        configuration.timeoutIntervalForResource = forPaper ? paperResourceTimeout : 30
        configuration.httpCookieStorage = nil
        configuration.urlCredentialStorage = nil
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        return configuration
    }

    static func validateBaseURL(_ rawValue: String) throws -> URL {
        let clean = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard
            let url = URL(string: clean),
            let scheme = url.scheme?.lowercased(),
            let host = url.host?.lowercased(),
            !host.isEmpty,
            url.user == nil,
            url.password == nil,
            url.query == nil,
            url.fragment == nil
        else {
            throw APIClientError.invalidBaseURL
        }
        let localHosts = ["localhost", "127.0.0.1", "::1"]
        #if DEBUG
        let permitsLoopbackHTTP = scheme == "http" && localHosts.contains(host)
        #else
        let permitsLoopbackHTTP = false
        #endif
        guard scheme == "https" || permitsLoopbackHTTP else {
            throw APIClientError.insecureBaseURL
        }
        return url
    }

    func config() async throws -> LiveConfig {
        try await send(method: "GET", components: ["interviews", "live", "config"])
    }

    static func exchangePairingCode(
        baseURLString: String,
        code: String,
        codeVerifier: String
    ) async throws -> PairExchangeResponse {
        let unauthenticated = StaticCredentialProvider(token: "pairing-exchange")
        let client = try CompanionAPIClient(
            baseURLString: baseURLString,
            credentials: unauthenticated
        )
        return try await client.send(
            method: "POST",
            components: ["interviews", "live", "pair", "exchange"],
            body: PairExchangeRequest(code: code, codeVerifier: codeVerifier),
            includeAuthorization: false
        )
    }

    func createSession(_ body: CreateLiveSessionRequest) async throws -> LiveSession {
        try await send(
            method: "POST",
            components: ["interviews", "live", "sessions"],
            body: body
        )
    }

    func getSession(sessionID: String) async throws -> LiveSession {
        try await send(
            method: "GET",
            components: ["interviews", "live", "sessions", sessionID]
        )
    }

    func uploadSegments(
        sessionID: String,
        segments: [FinalTranscriptSegment]
    ) async throws -> SegmentBatchResponse {
        try await send(
            method: "POST",
            components: ["interviews", "live", "sessions", sessionID, "segments"],
            body: SegmentBatchRequest(segments: segments)
        )
    }

    func ask(
        sessionID: String,
        request: AskLiveSessionRequest
    ) async throws -> AskLiveSessionResponse {
        try await send(
            method: "POST",
            components: ["interviews", "live", "sessions", sessionID, "ask"],
            body: request
        )
    }

    func asks(sessionID: String) async throws -> [AskLiveSessionResponse] {
        try await send(
            method: "GET",
            components: ["interviews", "live", "sessions", sessionID, "asks"]
        )
    }

    func complete(
        sessionID: String,
        brainstormRequest: CreateBrainstormRequest? = nil
    ) async throws -> CompleteLiveSessionResponse {
        if let brainstormRequest {
            return try await send(
                method: "POST",
                components: ["interviews", "live", "sessions", sessionID, "complete"],
                body: brainstormRequest
            )
        }
        return try await send(
            method: "POST",
            components: ["interviews", "live", "sessions", sessionID, "complete"]
        )
    }

    func cancel(sessionID: String) async throws -> LiveSession {
        try await send(
            method: "POST",
            components: ["interviews", "live", "sessions", sessionID, "cancel"]
        )
    }

    func createBrainstorm(
        sessionID: String,
        request: CreateBrainstormRequest
    ) async throws -> BrainstormReceipt {
        try await send(
            method: "POST",
            components: ["interviews", "live", "sessions", sessionID, "brainstorms"],
            body: request
        )
    }

    func brainstorms(sessionID: String) async throws -> BrainstormListResponse {
        try await send(
            method: "GET",
            components: ["interviews", "live", "sessions", sessionID, "brainstorms"]
        )
    }

    func brainstorm(sessionID: String, brainstormID: String) async throws -> BrainstormReceipt {
        try await send(
            method: "GET",
            components: [
                "interviews", "live", "sessions", sessionID, "brainstorms", brainstormID,
            ]
        )
    }

    func cancelBrainstorm(
        sessionID: String,
        brainstormID: String
    ) async throws -> BrainstormReceipt {
        try await send(
            method: "POST",
            components: [
                "interviews", "live", "sessions", sessionID,
                "brainstorms", brainstormID, "cancel",
            ]
        )
    }

    func createPaperChat(_ body: CreatePaperChatRequest) async throws -> PaperChatSummary {
        try await send(
            method: "POST",
            components: ["companion", "paper-chats"],
            body: body,
            timeoutInterval: 300
        )
    }

    func paperChat(chatID: String) async throws -> PaperChatSummary {
        try await send(
            method: "GET",
            components: ["companion", "paper-chats", chatID]
        )
    }

    func paperChatHistory(chatID: String) async throws -> [PaperChatHistoryMessage] {
        try await send(
            method: "GET",
            components: ["companion", "paper-chats", chatID, "history"]
        )
    }

    func activePaperChatTurn(chatID: String) async throws -> PaperChatTurnState? {
        try await send(
            method: "GET",
            components: ["companion", "paper-chats", chatID, "turns", "active"]
        )
    }

    func latestPaperChatTurn(chatID: String) async throws -> PaperChatTurnState? {
        try await send(
            method: "GET",
            components: ["companion", "paper-chats", chatID, "turns", "latest"]
        )
    }

    func paperChatTurn(chatID: String, turnID: String) async throws -> PaperChatTurnState {
        try await send(
            method: "GET",
            components: ["companion", "paper-chats", chatID, "turns", turnID]
        )
    }

    func stopPaperChatTurn(
        chatID: String,
        turnID: String
    ) async throws -> PaperChatTurnStopResponse {
        try await send(
            method: "POST",
            components: ["companion", "paper-chats", chatID, "turns", turnID, "stop"]
        )
    }

    func streamPaperChatTurn(
        chatID: String,
        request body: PaperChatTurnRequest,
        onEvent: @MainActor @Sendable @escaping (PaperChatStreamEvent) -> Void
    ) async throws -> PaperChatStreamResult {
        try await paperChatEventStream(
            method: "POST",
            components: ["companion", "paper-chats", chatID, "turns", "stream"],
            turnID: body.turnID,
            afterEventID: 0,
            body: body,
            onEvent: onEvent
        )
    }

    func followPaperChatTurn(
        chatID: String,
        turnID: String,
        afterEventID: Int,
        onEvent: @MainActor @Sendable @escaping (PaperChatStreamEvent) -> Void
    ) async throws -> PaperChatStreamResult {
        try await paperChatEventStream(
            method: "GET",
            components: [
                "companion", "paper-chats", chatID, "turns", turnID, "events", "stream",
            ],
            turnID: turnID,
            afterEventID: afterEventID,
            body: Optional<PaperChatTurnRequest>.none,
            onEvent: onEvent
        )
    }

    private func send<Response: Decodable>(
        method: String,
        components: [String],
        body: (some Encodable)? = Optional<String>.none,
        includeAuthorization: Bool = true,
        timeoutInterval: TimeInterval? = nil
    ) async throws -> Response {
        var url = baseURL
        for component in components {
            url.appendPathComponent(component)
        }
        var request = URLRequest(url: url)
        request.httpMethod = method
        if let timeoutInterval { request.timeoutInterval = timeoutInterval }
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        applyClientIdentity(to: &request)
        if includeAuthorization {
            request.setValue(
                "Bearer \(try credentials.bearerToken())",
                forHTTPHeaderField: "Authorization"
            )
        }
        if let body {
            request.httpBody = try encoder.encode(body)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }

        let requestSession = components.starts(with: ["companion", "paper-chats"])
            ? paperSession
            : session
        let (data, response) = try await requestSession.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw APIClientError.invalidResponse
        }
        guard data.count <= Self.maximumResponseBytes else {
            throw APIClientError.responseTooLarge
        }
        guard (200 ... 299).contains(http.statusCode) else {
            let error = Self.errorPayload(from: data)
            throw APIClientError.server(
                status: http.statusCode,
                code: error.code,
                detail: error.message
            )
        }
        do {
            return try decoder.decode(Response.self, from: data)
        } catch {
            throw APIClientError.invalidResponse
        }
    }

    private func paperChatEventStream<Body: Encodable>(
        method: String,
        components: [String],
        turnID: String,
        afterEventID: Int,
        body: Body?,
        onEvent: @MainActor @Sendable @escaping (PaperChatStreamEvent) -> Void
    ) async throws -> PaperChatStreamResult {
        var url = baseURL
        for component in components { url.appendPathComponent(component) }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = 310
        request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        applyClientIdentity(to: &request)
        request.setValue(
            "Bearer \(try credentials.bearerToken())",
            forHTTPHeaderField: "Authorization"
        )
        if afterEventID > 0 {
            request.setValue(String(afterEventID), forHTTPHeaderField: "Last-Event-ID")
        }
        if let body {
            request.httpBody = try encoder.encode(body)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }

        let (bytes, response) = try await paperSession.bytes(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw APIClientError.invalidResponse
        }
        guard (200 ... 299).contains(http.statusCode) else {
            var errorData = Data()
            for try await byte in bytes {
                guard errorData.count < Self.maximumResponseBytes else {
                    throw APIClientError.responseTooLarge
                }
                errorData.append(byte)
            }
            let error = Self.errorPayload(from: errorData)
            throw APIClientError.server(
                status: http.statusCode,
                code: error.code,
                detail: error.message
            )
        }

        var parser = PaperChatSSEParser(turnID: turnID, afterEventID: afterEventID)
        var terminal: PaperChatStreamEvent?
        for try await line in bytes.lines {
            try Task.checkCancellation()
            let chunk = Data((line + "\n").utf8)
            for event in parser.append(chunk) {
                await onEvent(event)
                if event.isTerminal { terminal = event }
            }
        }
        for event in parser.append(Data(), final: true) {
            await onEvent(event)
            if event.isTerminal { terminal = event }
        }
        return PaperChatStreamResult(
            lastEventID: parser.lastEventID,
            terminalEvent: terminal
        )
    }

    private func applyClientIdentity(to request: inout URLRequest) {
        request.setValue("companion-macos", forHTTPHeaderField: "X-SixSentences-Client")
        request.setValue(clientVersion, forHTTPHeaderField: "X-SixSentences-Client-Version")
        request.setValue(clientBuild, forHTTPHeaderField: "X-SixSentences-Client-Build")
        request.setValue(
            "SixSentencesCompanion/\(clientVersion) (macOS; build \(clientBuild))",
            forHTTPHeaderField: "User-Agent"
        )
    }

    private static func errorPayload(from data: Data) -> (code: String?, message: String) {
        guard
            let object = try? JSONSerialization.jsonObject(with: data),
            let envelope = object as? [String: Any],
            let detail = envelope["detail"]
        else { return (nil, "") }
        if let text = detail as? String { return (nil, text) }
        if let payload = detail as? [String: Any] {
            return (
                payload["code"] as? String,
                (payload["message"] as? String)
                    ?? (payload["detail"] as? String)
                    ?? ""
            )
        }
        return (nil, "")
    }
}

private struct StaticCredentialProvider: CompanionCredentialProvider {
    let token: String
    func bearerToken() throws -> String { token }
}
