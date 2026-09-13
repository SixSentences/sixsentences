import Foundation
import os

/// Privacy-safe performance receipts. Only durations, channel labels and
/// counts are logged; transcript text and questions never enter unified logs.
enum CompanionLatency {
    private static let logger = Logger(
        subsystem: "com.sixsentences.companion",
        category: "latency"
    )

    static func now() -> UInt64 {
        DispatchTime.now().uptimeNanoseconds
    }

    static func milliseconds(since startedAt: UInt64) -> Int64 {
        Int64((DispatchTime.now().uptimeNanoseconds - startedAt) / 1_000_000)
    }

    static func recordAnalyzerResult(
        channel: SegmentChannel,
        isFinal: Bool,
        lagMS: Int64
    ) {
        logger.debug(
            "analyzer_result channel=\(channel.rawValue, privacy: .public) final=\(isFinal, privacy: .public) lag_ms=\(lagMS, privacy: .public)"
        )
    }

    static func recordUpload(segmentCount: Int, durationMS: Int64) {
        logger.debug(
            "segment_upload count=\(segmentCount, privacy: .public) duration_ms=\(durationMS, privacy: .public)"
        )
    }

    static func recordAsk(durationMS: Int64, sourceCount: Int) {
        logger.debug(
            "grounded_ask duration_ms=\(durationMS, privacy: .public) sources=\(sourceCount, privacy: .public)"
        )
    }

    static func recordHistoryRefresh(durationMS: Int64, receiptCount: Int) {
        logger.debug(
            "ask_history duration_ms=\(durationMS, privacy: .public) receipts=\(receiptCount, privacy: .public)"
        )
    }

    static func recordStopFinalization(durationMS: Int64) {
        logger.debug(
            "speech_finalize duration_ms=\(durationMS, privacy: .public)"
        )
    }
}
