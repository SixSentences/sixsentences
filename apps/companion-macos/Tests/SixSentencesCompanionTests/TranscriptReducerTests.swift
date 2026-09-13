import XCTest
@testable import SixSentencesCompanion

final class TranscriptReducerTests: XCTestCase {
    func testPartialResultIsNeverUploaded() {
        var reducer = TranscriptReducer(makeID: { "event_123" })
        let output = reducer.ingestFinal(
            SpeechUpdate(
                channel: .microphone,
                text: "partial words",
                isFinal: false,
                startMS: 0,
                endMS: 500
            )
        )
        XCTAssertTrue(output.isEmpty)
        XCTAssertTrue(reducer.flushAll().isEmpty)
    }

    func testAdjacentFinalResultsCoalesceBeforeUpload() {
        var ids = ["event_first", "event_second"]
        var reducer = TranscriptReducer(makeID: { ids.removeFirst() })
        XCTAssertTrue(
            reducer.ingestFinal(
                SpeechUpdate(
                    channel: .microphone,
                    text: "The first thought",
                    isFinal: true,
                    startMS: 100,
                    endMS: 900
                )
            ).isEmpty
        )
        XCTAssertTrue(
            reducer.ingestFinal(
                SpeechUpdate(
                    channel: .microphone,
                    text: "continues here",
                    isFinal: true,
                    startMS: 1_200,
                    endMS: 1_800
                )
            ).isEmpty
        )

        let segment = reducer.flushAll().first
        XCTAssertEqual(segment?.clientEventID, "event_first")
        XCTAssertEqual(segment?.text, "The first thought continues here")
        XCTAssertEqual(segment?.startMS, 100)
        XCTAssertEqual(segment?.endMS, 1_800)
        XCTAssertEqual(segment?.isFinal, true)
    }

    func testCrossChannelEchoIsSuppressed() {
        var counter = 0
        var reducer = TranscriptReducer(makeID: {
            counter += 1
            return "event_\(counter)"
        })
        _ = reducer.ingestFinal(
            SpeechUpdate(
                channel: .system,
                text: "We should evaluate the second approach",
                isFinal: true,
                startMS: 1_000,
                endMS: 3_000
            )
        )
        let echoed = reducer.ingestFinal(
            SpeechUpdate(
                channel: .microphone,
                text: "We should evaluate the second approach",
                isFinal: true,
                startMS: 1_200,
                endMS: 3_200
            )
        )
        XCTAssertTrue(echoed.isEmpty)
        XCTAssertEqual(reducer.flushAll().count, 1)
    }

    func testTimecodesStayMonotonicPerChannel() {
        var counter = 0
        var reducer = TranscriptReducer(makeID: {
            counter += 1
            return "event_\(counter)"
        })
        _ = reducer.ingestFinal(
            SpeechUpdate(
                channel: .microphone,
                text: "A sufficiently unique first sentence",
                isFinal: true,
                startMS: 1_000,
                endMS: 2_000
            )
        )
        let first = reducer.drainReady(nowMS: 4_000)
        XCTAssertEqual(first.first?.endMS, 2_000)
        _ = reducer.ingestFinal(
            SpeechUpdate(
                channel: .microphone,
                text: "A different second sentence",
                isFinal: true,
                startMS: 1_500,
                endMS: 2_500
            )
        )
        let second = reducer.flushAll()
        XCTAssertEqual(second.first?.startMS, 2_000)
        XCTAssertEqual(second.first?.endMS, 2_500)
    }
}
