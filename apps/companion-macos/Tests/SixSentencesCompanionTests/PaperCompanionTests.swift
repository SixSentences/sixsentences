import Foundation
import XCTest
@testable import SixSentencesCompanion

private struct PaperTestCredential: CompanionCredentialProvider {
    func bearerToken() throws -> String { "paper-companion-token" }
}

private final class PaperURLProtocolSpy: URLProtocol, @unchecked Sendable {
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

final class PaperCompanionTests: XCTestCase {
    override func tearDown() {
        PaperURLProtocolSpy.clearHandler()
        super.tearDown()
    }

    func testPDFValidationChecksMagicSizeFilenameAndChecksum() throws {
        XCTAssertEqual(PaperCompanionLimits.maximumPDFBytes, 25 * 1024 * 1024)
        let data = Data("%PDF-1.7\nsmall fixture".utf8)
        let validated = try PaperFileValidator.validate(data: data, filename: "/tmp/Paper.PDF")
        XCTAssertEqual(validated.filename, "Paper.PDF")
        XCTAssertEqual(validated.data, data)
        XCTAssertEqual(validated.sha256.count, 64)

        XCTAssertThrowsError(
            try PaperFileValidator.validate(data: Data("not a pdf".utf8), filename: "paper.pdf")
        ) { XCTAssertEqual($0 as? PaperCompanionError, .pdfOnly) }
        XCTAssertThrowsError(
            try PaperFileValidator.validate(data: data, filename: "paper.txt")
        ) { XCTAssertEqual($0 as? PaperCompanionError, .pdfOnly) }
        XCTAssertThrowsError(
            try PaperFileValidator.validate(data: Data(), filename: "paper.pdf")
        ) { XCTAssertEqual($0 as? PaperCompanionError, .emptyPDF) }
        XCTAssertThrowsError(
            try PaperFileValidator.read(url: URL(string: "https://example.test/paper.pdf")!)
        ) { XCTAssertEqual($0 as? PaperCompanionError, .fileOnly) }
    }

    func testPaperRequestsUseDedicatedLongResourceTimeout() {
        let regular = CompanionAPIClient.sessionConfiguration(forPaper: false)
        let paper = CompanionAPIClient.sessionConfiguration(forPaper: true)
        XCTAssertEqual(regular.timeoutIntervalForRequest, 20)
        XCTAssertEqual(regular.timeoutIntervalForResource, 30)
        XCTAssertEqual(paper.timeoutIntervalForRequest, 320)
        XCTAssertEqual(paper.timeoutIntervalForResource, 320)
        XCTAssertNil(paper.httpCookieStorage)
        XCTAssertNil(paper.urlCredentialStorage)
    }

    func testPaperBrowserLinkRequiresTheConfiguredOrigin() throws {
        let origin = try CommunityOrigin("https://research.example.org")
        XCTAssertEqual(
            PaperCompanionLinkPolicy.destination(
                rawValue: "https://research.example.org/papers/run_public_1",
                communityOrigin: origin
            )?.absoluteString,
            "https://research.example.org/papers/run_public_1"
        )
        XCTAssertNil(PaperCompanionLinkPolicy.destination(
            rawValue: "https://hosted.invalid/papers/run_public_1",
            communityOrigin: origin
        ))
        XCTAssertNil(PaperCompanionLinkPolicy.destination(
            rawValue: "http://research.example.org/papers/run_public_1",
            communityOrigin: origin
        ))
        XCTAssertNil(PaperCompanionLinkPolicy.destination(
            rawValue: "https://research.example.org.attacker.invalid/papers/run_public_1",
            communityOrigin: origin
        ))
    }

    @MainActor
    func testDisconnectScrubClearsPaperQuestionAndSelectionState() {
        let suite = "PaperCompanionTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let model = PaperCompanionViewModel(
            preferences: CompanionPreferences(
                defaults: defaults,
                communityOrigin: "https://research.example.org"
            )
        )
        model.question = "Private question"
        model.updateSelection(PaperChatSelection(page: 3, quote: "Private quote"))

        model.scrubPrivateState()

        XCTAssertEqual(model.workspaceState, .empty)
        XCTAssertEqual(model.question, "")
        XCTAssertNil(model.selection)
        XCTAssertTrue(model.history.isEmpty)
        XCTAssertEqual(model.streamingAnswer, "")
        XCTAssertEqual(model.streamingReasoning, "")
    }

    func testWorkspaceStateMachineAcceptsOnlyValidVerticalFlow() {
        var machine = PaperCompanionWorkspaceMachine()
        XCTAssertEqual(machine.state, .empty)
        XCTAssertEqual(machine.apply(.askStarted), .empty)
        XCTAssertEqual(machine.apply(.openRequested), .loading)
        XCTAssertEqual(machine.apply(.openSucceeded), .ready)
        XCTAssertEqual(machine.apply(.askStarted), .asking)
        XCTAssertEqual(machine.apply(.askFinished), .ready)
        XCTAssertEqual(machine.apply(.reset), .empty)
    }

    func testOpenRequestBrokerRetainsColdLaunchURLUntilConsumed() async {
        await MainActor.run {
            let broker = CompanionOpenRequestBroker()
            let paperURL = URL(fileURLWithPath: "/tmp/explicit-paper.pdf")
            broker.receive(paperURL)
            XCTAssertEqual(broker.pendingPaperURL, paperURL)

            broker.consumePaper(URL(fileURLWithPath: "/tmp/other.pdf"))
            XCTAssertEqual(broker.pendingPaperURL, paperURL)
            broker.consumePaper(paperURL)
            XCTAssertNil(broker.pendingPaperURL)

            let pairingURL = URL(string: "sixsentences://companion/callback?code=123")!
            broker.receive(pairingURL)
            XCTAssertEqual(broker.pendingPairingURL, pairingURL)
            broker.consumePairing(pairingURL)
            XCTAssertNil(broker.pendingPairingURL)
        }
    }

    func testSelectionMappingUsesOneBasedSinglePageAndBoundsText() throws {
        let selection = try XCTUnwrap(
            PaperSelectionMapper.make(quote: "  Selected evidence.  ", zeroBasedPageIndexes: [2])
        )
        XCTAssertEqual(selection.page, 3)
        XCTAssertEqual(selection.quote, "Selected evidence.")
        XCTAssertNil(try PaperSelectionMapper.make(quote: "  ", zeroBasedPageIndexes: []))
        XCTAssertThrowsError(
            try PaperSelectionMapper.make(quote: "Cross page", zeroBasedPageIndexes: [0, 1])
        ) { XCTAssertEqual($0 as? PaperCompanionError, .selectionSpansPages) }
    }

    func testSSEParserRejectsDuplicatesMismatchesAndPartialFrames() throws {
        var parser = PaperChatSSEParser(turnID: "turn_123")
        let first = """
        id: 1
        event: answer.delta
        data: {"id":1,"event":"answer.delta","turn_id":"turn_123","delta":"Hello"}


        """
        let bytes = Data(first.utf8)
        XCTAssertTrue(parser.append(bytes.prefix(17)).isEmpty)
        let events = parser.append(bytes.dropFirst(17))
        XCTAssertEqual(events.map(\.delta), ["Hello"])
        XCTAssertEqual(parser.lastEventID, 1)
        XCTAssertTrue(parser.append(bytes).isEmpty, "Duplicate event ids must be ignored")

        let wrongTurn = """
        id: 2
        event: turn.completed
        data: {"id":2,"event":"turn.completed","turn_id":"other","answer":{"answer":"No","citations":[],"evidence":[]}}


        """
        XCTAssertTrue(parser.append(Data(wrongTurn.utf8)).isEmpty)
        XCTAssertEqual(parser.lastEventID, 1)
    }

    func testSSEParserReturnsTerminalAnswer() throws {
        var parser = PaperChatSSEParser(turnID: "turn_123", afterEventID: 4)
        let frame = """
        id: 5
        event: turn.completed
        data: {"id":5,"event":"turn.completed","turn_id":"turn_123","answer":{"answer":"Grounded result","citations":[],"evidence":[]}}


        """
        let event = try XCTUnwrap(parser.append(Data(frame.utf8)).first)
        XCTAssertTrue(event.isTerminal)
        XCTAssertEqual(event.answer?.answer, "Grounded result")
        XCTAssertEqual(parser.lastEventID, 5)
    }

    func testCreatePaperChatUsesNarrowAuthenticatedContract() async throws {
        let expectation = expectation(description: "paper chat request")
        PaperURLProtocolSpy.setHandler { request in
            XCTAssertEqual(request.httpMethod, "POST")
            XCTAssertEqual(request.url?.path, "/companion/paper-chats")
            XCTAssertEqual(
                request.value(forHTTPHeaderField: "Authorization"),
                "Bearer paper-companion-token"
            )
            let body = try XCTUnwrap(Self.bodyData(from: request))
            let object = try XCTUnwrap(
                JSONSerialization.jsonObject(with: body) as? [String: Any]
            )
            XCTAssertEqual(object["client_request_id"] as? String, "paper_request_123")
            XCTAssertEqual(object["filename"] as? String, "paper.pdf")
            XCTAssertEqual(object["sha256"] as? String, "abc123")
            XCTAssertEqual(object["project_id"] as? Int, 7)
            expectation.fulfill()
            return (201, Data(Self.summaryJSON.utf8))
        }

        let response = try await makeClient().createPaperChat(
            CreatePaperChatRequest(
                clientRequestID: "paper_request_123",
                filename: "paper.pdf",
                contentBase64: "JVBERi0=",
                sha256: "abc123",
                projectID: 7
            )
        )
        await fulfillment(of: [expectation], timeout: 2)
        XCTAssertEqual(response.paper.libraryDocumentID, 11)
        XCTAssertEqual(response.paper.chatDocumentID, 12)
        XCTAssertEqual(response.chat.id, "run_public_1")
    }

    func testHistoryAndStopStayInsidePaperChatAlias() async throws {
        var call = 0
        PaperURLProtocolSpy.setHandler { request in
            call += 1
            if call == 1 {
                XCTAssertEqual(request.url?.path, "/companion/paper-chats/run_public_1/history")
                return (
                    200,
                    Data(#"[{"id":1,"role":"user","content":"Why?","citations":[],"created_at":"2026-08-14T10:00:00Z"}]"#.utf8)
                )
            }
            XCTAssertEqual(
                request.url?.path,
                "/companion/paper-chats/run_public_1/turns/turn_123/stop"
            )
            return (200, Data(#"{"turn_id":"turn_123","status":"cancel_requested"}"#.utf8))
        }

        let client = makeClient()
        let history = try await client.paperChatHistory(chatID: "run_public_1")
        let stopped = try await client.stopPaperChatTurn(
            chatID: "run_public_1",
            turnID: "turn_123"
        )
        XCTAssertEqual(history.first?.content, "Why?")
        XCTAssertEqual(stopped.status, "cancel_requested")
    }

    private func makeClient() -> CompanionAPIClient {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [PaperURLProtocolSpy.self]
        return try! CompanionAPIClient(
            baseURLString: "https://api.sixsentences.test",
            credentials: PaperTestCredential(),
            session: URLSession(configuration: configuration)
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

    private static let summaryJSON = #"""
    {
      "client_request_id":"paper_request_123",
      "status":"created",
      "paper":{
        "library_document_id":11,"chat_document_id":12,"title":"Paper",
        "work_id":"W1","verified":true,"text_status":"ready","byte_size":123,
        "metadata_fields_added":["doi"],"warnings":[]
      },
      "chat":{"id":"run_public_1","title":"Paper","web_url":"https://research.example.org/r/run_public_1"}
    }
    """#
}
