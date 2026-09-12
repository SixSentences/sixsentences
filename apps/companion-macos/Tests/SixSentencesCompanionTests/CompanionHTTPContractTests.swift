import Foundation
import XCTest
@testable import SixSentencesCompanion

private struct HTTPTestCredential: CompanionCredentialProvider {
    func bearerToken() throws -> String { "companion-test-token" }
}

private final class CompanionURLProtocolSpy: URLProtocol, @unchecked Sendable {
    private static let lock = NSLock()
    private static var storedHandler: ((URLRequest) throws -> (Int, Data))?

    static func setHandler(_ handler: @escaping (URLRequest) throws -> (Int, Data)) {
        lock.lock()
        storedHandler = handler
        lock.unlock()
    }

    static func clearHandler() {
        lock.lock()
        storedHandler = nil
        lock.unlock()
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        Self.lock.lock()
        let handler = Self.storedHandler
        Self.lock.unlock()
        do {
            let (status, data) = try XCTUnwrap(handler)(request)
            let response = try XCTUnwrap(
                HTTPURLResponse(
                    url: try XCTUnwrap(request.url),
                    statusCode: status,
                    httpVersion: "HTTP/1.1",
                    headerFields: ["Content-Type": "application/json"]
                )
            )
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}
}

private actor ReceiptSequence {
    enum SequenceError: Error { case unavailable }
    private var values: [Result<[AskLiveSessionResponse], Error>]

    init(_ values: [Result<[AskLiveSessionResponse], Error>]) {
        self.values = values
    }

    func next() throws -> [AskLiveSessionResponse] {
        guard !values.isEmpty else { return [] }
        return try values.removeFirst().get()
    }
}

private actor TailEvents {
    private var items: [String] = []
    func append(_ item: String) { items.append(item) }
    func snapshot() -> [String] { items }
}

private final class LockedCounter: @unchecked Sendable {
    private let lock = NSLock()
    private var value = 0

    func increment() {
        lock.lock()
        value += 1
        lock.unlock()
    }

    func read() -> Int {
        lock.lock()
        defer { lock.unlock() }
        return value
    }
}

final class CompanionHTTPContractTests: XCTestCase {
    override func tearDown() {
        CompanionURLProtocolSpy.clearHandler()
        super.tearDown()
    }

    func testBrainstormSessionCreateSendsSelectedProjectWithoutConversationConsent() async throws {
        let expectation = expectation(description: "Brainstorm session create invoked")
        CompanionURLProtocolSpy.setHandler { request in
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(request.url?.path, "/interviews/live/sessions")
            let body = try XCTUnwrap(Self.bodyData(from: request))
            let json = try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String: Any])
            XCTAssertEqual(json["purpose"] as? String, "brainstorm")
            XCTAssertEqual(json["project_id"] as? Int, 11)
            XCTAssertNil(json["consent"])
            expectation.fulfill()
            return (201, Data(Self.brainstormSessionJSON.utf8))
        }

        let session = try await makeClient().createSession(CreateLiveSessionRequest(
            clientSessionID: "client_brain_project_1",
            title: "Thesis ideas",
            projectID: 11,
            language: "de",
            purpose: "brainstorm",
            consent: nil
        ))

        await fulfillment(of: [expectation], timeout: 2)
        XCTAssertEqual(session.projectID, 11)
        XCTAssertEqual(session.project?.name, "Master Thesis")
    }

    func testAskSubmitInvokesCanonicalAuthenticatedEndpoint() async throws {
        let expectation = expectation(description: "Ask endpoint invoked")
        CompanionURLProtocolSpy.setHandler { request in
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(
                request.url?.path,
                "/interviews/live/sessions/live_123/ask"
            )
            XCTAssertEqual(
                request.value(forHTTPHeaderField: "Authorization"),
                "Bearer companion-test-token"
            )
            let body = try XCTUnwrap(Self.bodyData(from: request))
            let json = try XCTUnwrap(
                JSONSerialization.jsonObject(with: body) as? [String: Any]
            )
            XCTAssertEqual(json["client_request_id"] as? String, "request_123")
            XCTAssertEqual(json["question"] as? String, "What did we decide?")
            XCTAssertEqual(json["context_through_sequence"] as? Int, 7)
            expectation.fulfill()
            return (202, Data(Self.askJSON.utf8))
        }
        let response = try await makeClient().ask(
            sessionID: "live_123",
            request: AskLiveSessionRequest(
                clientRequestID: "request_123",
                question: "What did we decide?",
                contextThroughSequence: 7
            )
        )

        await fulfillment(of: [expectation], timeout: 2)
        XCTAssertEqual(response.answer, "Use option B.")
        XCTAssertEqual(response.transcriptSources.first?.segmentID, "segment_1")
    }

    func testEveryRequestIdentifiesTheCompanionRelease() async throws {
        let expectation = expectation(description: "Config request includes client identity")
        CompanionURLProtocolSpy.setHandler { request in
            XCTAssertEqual(
                request.value(forHTTPHeaderField: "X-SixSentences-Client"),
                "companion-macos"
            )
            XCTAssertEqual(
                request.value(forHTTPHeaderField: "X-SixSentences-Client-Version"),
                "0.1.11"
            )
            XCTAssertEqual(
                request.value(forHTTPHeaderField: "X-SixSentences-Client-Build"),
                "11"
            )
            XCTAssertEqual(
                request.value(forHTTPHeaderField: "User-Agent"),
                "SixSentencesCompanion/0.1.11 (macOS; build 11)"
            )
            expectation.fulfill()
            return (200, Data(Self.liveConfigJSON.utf8))
        }

        _ = try await makeClient(clientVersion: "0.1.11", clientBuild: "11").config()
        await fulfillment(of: [expectation], timeout: 2)
    }

    func testAskSubmitPolicyRequiresLiveIdleConversationAndRealQuestion() {
        XCTAssertTrue(
            LiveAskSubmissionPolicy.canSubmit(
                isRecording: true,
                hasFinalTranscript: true,
                isAsking: false,
                question: "Why?"
            )
        )
        XCTAssertFalse(
            LiveAskSubmissionPolicy.canSubmit(
                isRecording: false,
                hasFinalTranscript: true,
                isAsking: false,
                question: "Why?"
            )
        )
        XCTAssertFalse(
            LiveAskSubmissionPolicy.canSubmit(
                isRecording: true,
                hasFinalTranscript: true,
                isAsking: true,
                question: "Why?"
            )
        )
        XCTAssertFalse(
            LiveAskSubmissionPolicy.canSubmit(
                isRecording: true,
                hasFinalTranscript: true,
                isAsking: false,
                question: " "
            )
        )
        XCTAssertFalse(
            LiveAskSubmissionPolicy.canSubmit(
                isRecording: true,
                hasFinalTranscript: false,
                isAsking: false,
                question: "Visible partial text is not durable yet"
            ),
            "A volatile/partial Apple Speech result must never enable Ask."
        )
    }

    func testAskCutoffFailsFastUntilFinalTextHasAServerSequence() throws {
        XCTAssertThrowsError(
            try LiveAskCutoffPolicy.confirmedSequence(
                hasFinalTranscript: false,
                lastEventSequence: nil
            )
        )
        XCTAssertThrowsError(
            try LiveAskCutoffPolicy.confirmedSequence(
                hasFinalTranscript: true,
                lastEventSequence: nil
            )
        )
        XCTAssertEqual(
            try LiveAskCutoffPolicy.confirmedSequence(
                hasFinalTranscript: true,
                lastEventSequence: 12
            ),
            12
        )
    }

    func testUploadDrainKeepsHighestConfirmedServerSequence() {
        var sequence: Int?
        sequence = LiveTranscriptSequence.highestConfirmed(current: sequence, response: 4)
        sequence = LiveTranscriptSequence.highestConfirmed(current: sequence, response: nil)
        sequence = LiveTranscriptSequence.highestConfirmed(current: sequence, response: 9)
        sequence = LiveTranscriptSequence.highestConfirmed(current: sequence, response: 7)

        XCTAssertEqual(sequence, 9)
    }

    func testTerminalNoTranscript409DoesNotEnterReconciliation() {
        let noTranscript = APIClientError.server(
            status: 409,
            code: "no_live_transcript",
            detail: "There is no live transcript to answer from yet."
        )
        let pending = APIClientError.server(
            status: 409,
            code: "ask_pending",
            detail: "This answer is still being prepared."
        )

        XCTAssertFalse(LiveAskRecoveryPolicy.shouldReconcile(after: noTranscript))
        XCTAssertTrue(LiveAskRecoveryPolicy.shouldReconcile(after: pending))
        XCTAssertTrue(LiveAskRecoveryPolicy.isExplicitPending(pending))
    }

    func testAskHistoryUsesCanonicalReadEndpoint() async throws {
        let expectation = expectation(description: "Ask history endpoint invoked")
        CompanionURLProtocolSpy.setHandler { request in
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(
                request.url?.path,
                "/interviews/live/sessions/live_123/asks"
            )
            expectation.fulfill()
            return (200, Data("[\(Self.askJSON)]".utf8))
        }

        let receipts = try await makeClient().asks(sessionID: "live_123")

        await fulfillment(of: [expectation], timeout: 2)
        XCTAssertEqual(receipts.map(\.id), ["ask_1"])
    }

    func testStructuredServerErrorPreservesItsUserFacingMessage() async throws {
        CompanionURLProtocolSpy.setHandler { _ in
            (
                402,
                Data(#"{"detail":{"code":"capacity_unavailable","message":"Capacity is unavailable."}}"#.utf8)
            )
        }

        do {
            _ = try await makeClient().ask(
                sessionID: "live_123",
                request: AskLiveSessionRequest(
                    clientRequestID: "request_123",
                    question: "What did we decide?",
                    contextThroughSequence: 7
                )
            )
            XCTFail("Expected the capacity error")
        } catch let error as APIClientError {
            XCTAssertEqual(
                error,
                .server(
                    status: 402,
                    code: "capacity_unavailable",
                    detail: "Capacity is unavailable."
                )
            )
        } catch {
            XCTFail("Unexpected error: \(error)")
        }
    }

    func testTimedOutSubmitReconcilesToCompletedReceipt() async {
        let pending = Self.receipt(status: "pending", answer: "")
        let completed = Self.receipt(status: "completed", answer: "Use option B.")
        let sequence = ReceiptSequence([
            .failure(URLError(.timedOut)),
            .success([pending]),
            .success([completed]),
        ])

        let outcome = await AskReconciler.reconcile(
            clientRequestID: completed.clientRequestID,
            maximumDuration: .seconds(1),
            backoff: [.zero, .milliseconds(1)],
            fetch: { try await sequence.next() }
        )

        XCTAssertEqual(outcome, .completed(completed))
    }

    func testPendingReceiptPollingNeverWaitsMoreThanOneSecond() {
        XCTAssertEqual(
            AskReconciler.defaultBackoff,
            [.zero, .milliseconds(250), .milliseconds(500), .seconds(1)]
        )
    }

    func testTimedOutSubmitReconcilesToFailedReceipt() async {
        let pending = Self.receipt(status: "pending", answer: "")
        let failed = Self.receipt(
            status: "failed",
            answer: "",
            error: "The grounded answer failed."
        )
        let sequence = ReceiptSequence([
            .success([pending]),
            .success([failed]),
        ])

        let outcome = await AskReconciler.reconcile(
            clientRequestID: failed.clientRequestID,
            maximumDuration: .seconds(1),
            backoff: [.zero, .milliseconds(1)],
            fetch: { try await sequence.next() }
        )

        XCTAssertEqual(outcome, .failed(failed))
    }

    func testReconciliationDeadlinePreservesLastPendingReceipt() async {
        let pending = Self.receipt(status: "pending", answer: "")
        let sequence = ReceiptSequence([.success([pending])])

        let outcome = await AskReconciler.reconcile(
            clientRequestID: pending.clientRequestID,
            maximumDuration: .milliseconds(20),
            backoff: [.zero, .milliseconds(5)],
            fetch: { try await sequence.next() }
        )

        XCTAssertEqual(outcome, .timedOut(lastReceipt: pending))
    }

    func testAmbiguousSubmitStopsQuicklyWhenNoReceiptExists() async {
        let sequence = ReceiptSequence([.success([]), .success([])])

        let outcome = await AskReconciler.reconcile(
            clientRequestID: "missing_request",
            maximumDuration: .seconds(1),
            backoff: [.zero],
            missingReceiptLimit: 2,
            fetch: { try await sequence.next() }
        )

        XCTAssertEqual(outcome, .notFound)
    }

    @MainActor
    func testSerialTailReleasesLaterFinalsAfterCutoffBeforeAnswerCompletes() async {
        let tail = CompanionSerialTail()
        let events = TailEvents()
        let cutoffGate = CompanionAsyncGate()
        let answerGate = CompanionAsyncGate()

        tail.append { await events.append("cutoff-final-persisted") }
        let cutoff = tail.snapshot()
        tail.append {
            await cutoffGate.wait()
            await events.append("post-click-final-persisted")
        }
        let pendingAnswer = Task { await answerGate.wait() }

        await cutoff?.value
        let beforeRelease = await events.snapshot()
        XCTAssertEqual(beforeRelease, ["cutoff-final-persisted"])
        cutoffGate.open()
        await tail.wait()
        let afterRelease = await events.snapshot()
        XCTAssertEqual(
            afterRelease,
            ["cutoff-final-persisted", "post-click-final-persisted"]
        )
        XCTAssertFalse(pendingAnswer.isCancelled)
        answerGate.open()
        await pendingAnswer.value
    }

    func testHungRecognitionFinalizationTimesOutAndCleansUpOnce() async {
        let forcedFinishes = LockedCounter()
        let cleanups = LockedCounter()
        let startedAt = ContinuousClock().now

        let outcome = await RecognitionFinalizationDeadline.run(
            timeout: .milliseconds(25),
            operation: {
                try? await Task.sleep(for: .seconds(5))
            },
            onForcedFinish: { forcedFinishes.increment() },
            cleanup: { cleanups.increment() }
        )

        XCTAssertEqual(outcome, .timedOut)
        XCTAssertEqual(forcedFinishes.read(), 1)
        XCTAssertEqual(cleanups.read(), 1)
        XCTAssertLessThan(startedAt.duration(to: ContinuousClock().now), .seconds(1))
    }

    func testBrainstormCompleteUsesConfirmedIdempotentPayload() async throws {
        let expectation = expectation(description: "Brainstorm complete invoked")
        CompanionURLProtocolSpy.setHandler { request in
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(request.url?.path, "/interviews/live/sessions/live_brain/complete")
            let body = try XCTUnwrap(Self.bodyData(from: request))
            let json = try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String: Any])
            XCTAssertEqual(json["client_request_id"] as? String, "brain_request_123")
            XCTAssertEqual(json["output_language"] as? String, "de")
            XCTAssertEqual(json["context_through_sequence"] as? Int, 9)
            XCTAssertEqual(json["schema_version"] as? Int, 1)
            expectation.fulfill()
            return (202, Data(Self.brainstormCompleteJSON.utf8))
        }

        let response = try await makeClient().complete(
            sessionID: "live_brain",
            brainstormRequest: CreateBrainstormRequest(
                clientRequestID: "brain_request_123",
                outputLanguage: .german,
                contextThroughSequence: 9
            )
        )

        await fulfillment(of: [expectation], timeout: 2)
        XCTAssertEqual(response.session.purpose, "brainstorm")
        XCTAssertNil(response.interviewID)
        XCTAssertEqual(response.brainstorm?.contextThroughSequence, 9)
    }

    func testConversationCompleteRemainsBodyless() async throws {
        CompanionURLProtocolSpy.setHandler { request in
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(request.url?.path, "/interviews/live/sessions/live_conversation/complete")
            XCTAssertNil(Self.bodyData(from: request))
            return (200, Data(Self.brainstormCompleteJSON.utf8))
        }

        _ = try await makeClient().complete(sessionID: "live_conversation")
    }

    func testBrainstormCancelStaysOnCreatorPrivateAlias() async throws {
        CompanionURLProtocolSpy.setHandler { request in
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(
                request.url?.path,
                "/interviews/live/sessions/live_brain/brainstorms/brain_1/cancel"
            )
            return (200, Data(Self.cancelledBrainstormJSON.utf8))
        }

        let response = try await makeClient().cancelBrainstorm(
            sessionID: "live_brain",
            brainstormID: "brain_1"
        )

        XCTAssertEqual(response.status, "failed")
        XCTAssertEqual(response.errorCode, "brainstorm_cancelled")
    }

    private func makeClient(
        clientVersion: String = CompanionApplicationVersion.current,
        clientBuild: String = CompanionApplicationVersion.currentBuild
    ) throws -> CompanionAPIClient {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [CompanionURLProtocolSpy.self]
        return try CompanionAPIClient(
            baseURLString: "https://api.sixsentences.test",
            credentials: HTTPTestCredential(),
            session: URLSession(configuration: configuration),
            clientVersion: clientVersion,
            clientBuild: clientBuild
        )
    }

    private static func bodyData(from request: URLRequest) -> Data? {
        if let body = request.httpBody { return body }
        guard let stream = request.httpBodyStream else { return nil }
        stream.open()
        defer { stream.close() }
        var data = Data()
        var buffer = [UInt8](repeating: 0, count: 4_096)
        while stream.hasBytesAvailable {
            let count = stream.read(&buffer, maxLength: buffer.count)
            guard count >= 0 else { return nil }
            if count == 0 { break }
            data.append(buffer, count: count)
        }
        return data
    }

    private static func receipt(
        status: String,
        answer: String,
        error: String? = nil
    ) -> AskLiveSessionResponse {
        AskLiveSessionResponse(
            id: "ask_1",
            clientRequestID: "request_123",
            question: "What did we decide?",
            answer: answer,
            transcriptSources: [],
            projectSources: [],
            createdAt: "2026-08-13T16:00:00+00:00",
            status: status,
            error: error
        )
    }

    private static let askJSON = #"""
    {
      "id": "ask_1",
      "client_request_id": "request_123",
      "question": "What did we decide?",
      "answer": "Use option B.",
      "status": "completed",
      "error": null,
      "transcript_sources": [{
        "segment_id": "segment_1",
        "speaker": "Others",
        "quote": "We should use option B.",
        "start_ms": 1000,
        "end_ms": 2000
      }],
      "project_sources": [],
      "created_at": "2026-08-13T16:00:00+00:00"
    }
    """#

    private static let liveConfigJSON = #"""
    {
      "account_id":"user:release-test","enabled":true,"projects":[],
      "desktop":{
        "status":"available","platforms":["macos"],
        "minimum_version":"0.1.11","download_url":null,"permissions":[]
      }
    }
    """#

    private static let brainstormSessionJSON = #"""
    {
      "id":"live_brain","title":"Thesis ideas","project_id":11,
      "project":{"id":11,"name":"Master Thesis"},
      "status":"recording","language":"de","purpose":"brainstorm",
      "started_at":"2026-08-14T00:00:00Z","ended_at":null,
      "duration_ms":0,"max_duration_ms":14400000,"consent":null,
      "segment_count":0,"ask_count":0,"interview_id":null,
      "web_url":"https://research.example.org/live/live_brain","revision":1,
      "last_segment_sequence":null,"completed_through_sequence":null,
      "transcript_char_count":0,"failure_reason":null,
      "created_at":"2026-08-14T00:00:00Z","updated_at":"2026-08-14T00:00:00Z"
    }
    """#

    private static let cancelledBrainstormJSON = #"""
    {
      "id":"brain_1","client_request_id":"brain_request_123","status":"failed",
      "output_language":"de","context_through_sequence":9,"schema_version":1,
      "result":null,"error":"Cancelled","error_code":"brainstorm_cancelled",
      "created_at":"2026-08-14T00:00:00Z"
    }
    """#

    private static let brainstormCompleteJSON = #"""
    {
      "session": {
        "id":"live_brain","title":"Brainstorm","project_id":null,"project":null,
        "status":"completed","language":"de","purpose":"brainstorm",
        "started_at":"2026-08-14T00:00:00Z","ended_at":"2026-08-14T00:10:00Z",
        "duration_ms":600000,"max_duration_ms":14400000,"consent":null,
        "segment_count":9,"ask_count":0,"interview_id":null,
        "web_url":"https://research.example.org/live/live_brain","revision":10,
        "last_segment_sequence":9,"completed_through_sequence":9,
        "transcript_char_count":1200,"failure_reason":null,
        "created_at":"2026-08-14T00:00:00Z","updated_at":"2026-08-14T00:10:00Z"
      },
      "brainstorm": {
        "id":"brain_1","client_request_id":"brain_request_123","status":"pending",
        "output_language":"de","context_through_sequence":9,"schema_version":1,
        "result":null,"error":null,"error_code":null,
        "created_at":"2026-08-14T00:10:00Z"
      }
    }
    """#
}
