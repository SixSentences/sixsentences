import XCTest
@testable import SixSentencesCompanion

final class APIModelsTests: XCTestCase {
    func testStableAccessErrorsUseOperatorNeutralCopyWithoutDiagnostics() throws {
        for code in ["capacity_exhausted", "concurrency_limit", "future_unknown"] {
            let error = APIClientError.server(
                status: 402,
                code: code,
                detail: "Synthetic private diagnostics"
            )
            let message = try XCTUnwrap(error.errorDescription)
            XCTAssertTrue(message.contains("operator-configured"))
            XCTAssertFalse(message.contains("private"))
            XCTAssertTrue(try XCTUnwrap(CompanionAccessErrorCopy.localized(
                status: 402, code: code, usesGerman: true
            )).contains("Betreiber"))
        }
        XCTAssertEqual(
            CompanionAccessErrorCopy.localized(status: 403, code: "forbidden", usesGerman: false),
            "This account does not have permission to perform this action."
        )
        XCTAssertEqual(
            CompanionAccessErrorCopy.localized(status: 401, code: "capacity_exhausted", usesGerman: false),
            "Your connection needs to be renewed. Reconnect Companion to SixSentences."
        )
        XCTAssertNil(CompanionAccessErrorCopy.localized(status: 500, code: "internal", usesGerman: false))
        XCTAssertEqual(
            APIClientError.server(
                status: 500,
                code: "internal",
                detail: "database host and query must stay private"
            ).errorDescription,
            "SixSentences request failed (HTTP 500)."
        )
    }

    func testCreateRequestUsesCanonicalSnakeCaseContract() throws {
        let request = CreateLiveSessionRequest(
            clientSessionID: "client_session_123",
            title: "Research sync",
            projectID: 42,
            language: "de",
            consent: ConsentPayload(participantsNotified: true, noticeText: "Everyone was informed.")
        )

        let object = try XCTUnwrap(
            JSONSerialization.jsonObject(with: JSONEncoder().encode(request)) as? [String: Any]
        )
        XCTAssertEqual(object["client_session_id"] as? String, "client_session_123")
        XCTAssertEqual(object["project_id"] as? Int, 42)
        XCTAssertEqual(object["language"] as? String, "de")
        let consent = try XCTUnwrap(object["consent"] as? [String: Any])
        XCTAssertEqual(consent["participants_notified"] as? Bool, true)
    }

    func testConfigDecodesBackendContract() throws {
        let json = #"""
        {
          "account_id": "user:account-a",
          "enabled": true,
          "max_session_minutes": 240,
          "max_batch_segments": 100,
          "max_segment_chars": 4000,
          "poll_interval_ms": 1000,
          "projects": [{"id": 7, "name": "Thesis"}],
          "desktop": {
            "status": "available",
            "platforms": ["macos"],
            "download_url": "https://research.example.org/downloads/companion",
            "minimum_version": "1.0.0",
            "permissions": ["microphone", "system_audio", "speech_recognition"]
          }
        }
        """#

        let config = try JSONDecoder().decode(LiveConfig.self, from: Data(json.utf8))
        XCTAssertEqual(config.accountID, "user:account-a")
        XCTAssertTrue(config.enabled)
        XCTAssertEqual(config.maxSessionMinutes, 240)
        XCTAssertEqual(config.maxBatchSegments, 100)
        XCTAssertEqual(config.maxSegmentCharacters, 4_000)
        XCTAssertEqual(config.projects.first?.name, "Thesis")
        XCTAssertEqual(config.desktop?.minimumVersion, "1.0.0")
    }

    func testAskReceiptsDecodeWithoutFlatteningSources() throws {
        let json = #"""
        {
          "id": "ask_1",
          "client_request_id": "request_123",
          "question": "What did we decide?",
          "answer": "The transcript supports option B.",
          "transcript_sources": [{
            "segment_id": "segment_1", "start_ms": 1200, "end_ms": 2700,
            "speaker": "Others", "quote": "Let us use option B."
          }],
          "project_sources": [{
            "type": "project", "id": "7", "title": "Thesis",
            "locator": "project context", "quote": "Option evaluation"
          }],
          "created_at": "2026-08-13T10:00:00+00:00"
        }
        """#

        let response = try JSONDecoder().decode(AskLiveSessionResponse.self, from: Data(json.utf8))
        XCTAssertEqual(response.clientRequestID, "request_123")
        XCTAssertEqual(response.transcriptSources.first?.segmentID, "segment_1")
        XCTAssertEqual(response.projectSources.first?.id, "7")
        XCTAssertEqual(response.projectSources.first?.quote, "Option evaluation")
    }

    func testCompleteResponseWrapsCanonicalSession() throws {
        let json = #"""
        {
          "session": {
            "id": "live_1", "title": "Research sync", "project_id": null,
            "project": null, "status": "completed", "language": "auto",
            "started_at": "2026-08-13T10:00:00+00:00",
            "ended_at": "2026-08-13T10:10:00+00:00", "duration_ms": 600000,
            "max_duration_ms": 14400000,
            "consent": {"participants_notified": true, "notice_text": "Informed"},
            "segment_count": 12, "ask_count": 1, "interview_id": "interview_1",
            "web_url": "https://research.example.org/interviews?tab=live&session=live_1",
            "revision": 14, "failure_reason": null,
            "created_at": "2026-08-13T10:00:00+00:00",
            "updated_at": "2026-08-13T10:10:00+00:00"
          },
          "interview_id": "interview_1",
          "analysis_status": "queued"
        }
        """#

        let response = try JSONDecoder().decode(
            CompleteLiveSessionResponse.self,
            from: Data(json.utf8)
        )
        XCTAssertEqual(response.session.status, "completed")
        XCTAssertEqual(response.interviewID, "interview_1")
        XCTAssertEqual(response.analysisStatus, "queued")
    }
}
