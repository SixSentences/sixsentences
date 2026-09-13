import Foundation

struct SpeechUpdate: Equatable, Sendable {
    let channel: SegmentChannel
    let text: String
    let isFinal: Bool
    let startMS: Int64
    let endMS: Int64
}

struct TranscriptReducer {
    private struct Fingerprint {
        let channel: SegmentChannel
        let normalized: String
        let endMS: Int64
    }

    private var pending: [SegmentChannel: FinalTranscriptSegment] = [:]
    private var recent: [Fingerprint] = []
    private var lastCommittedEnd: [SegmentChannel: Int64] = [:]
    private let makeID: () -> String

    init(makeID: @escaping () -> String = { UUID().uuidString.lowercased() }) {
        self.makeID = makeID
    }

    mutating func ingestFinal(_ update: SpeechUpdate) -> [FinalTranscriptSegment] {
        guard update.isFinal else { return [] }
        let clean = Self.clean(update.text)
        let normalized = Self.normalize(clean)
        guard !normalized.isEmpty else { return [] }

        recent.removeAll { update.endMS - $0.endMS > 15_000 }
        let pendingFingerprints = pending.values.map {
            Fingerprint(channel: $0.channel, normalized: Self.normalize($0.text), endMS: $0.endMS)
        }
        if (recent + pendingFingerprints).contains(where: {
            let echoWindow: Int64 = $0.channel == update.channel ? 15_000 : 2_500
            return abs(update.endMS - $0.endMS) <= echoWindow
                && Self.isEquivalent(normalized, $0.normalized)
        }) {
            return []
        }

        let floor = lastCommittedEnd[update.channel] ?? 0
        let start = max(floor, update.startMS)
        let end = max(start + 1, update.endMS)
        let segment = FinalTranscriptSegment(
            clientEventID: makeID(),
            channel: update.channel,
            speaker: update.channel.speakerLabel,
            startMS: start,
            endMS: end,
            text: clean,
            isFinal: true
        )

        if let existing = pending[update.channel] {
            if start - existing.endMS <= 900 {
                pending[update.channel] = FinalTranscriptSegment(
                    clientEventID: existing.clientEventID,
                    channel: existing.channel,
                    speaker: existing.speaker,
                    startMS: existing.startMS,
                    endMS: max(existing.endMS, end),
                    text: Self.clean(existing.text + " " + clean),
                    isFinal: true
                )
                return []
            }
            commit(existing)
            pending[update.channel] = segment
            return [existing]
        }
        pending[update.channel] = segment
        return []
    }

    mutating func drainReady(nowMS: Int64, quietPeriodMS: Int64 = 1_200) -> [FinalTranscriptSegment] {
        let channels = pending.compactMap { channel, segment in
            segment.endMS + quietPeriodMS <= nowMS ? channel : nil
        }
        return channels.compactMap { channel in
            guard let segment = pending.removeValue(forKey: channel) else { return nil }
            commit(segment)
            return segment
        }.sorted { $0.startMS < $1.startMS }
    }

    mutating func flushAll() -> [FinalTranscriptSegment] {
        let segments = pending.values.sorted { $0.startMS < $1.startMS }
        pending.removeAll()
        segments.forEach { commit($0) }
        return segments
    }

    private mutating func commit(_ segment: FinalTranscriptSegment) {
        lastCommittedEnd[segment.channel] = max(
            lastCommittedEnd[segment.channel] ?? 0,
            segment.endMS
        )
        recent.append(
            Fingerprint(
                channel: segment.channel,
                normalized: Self.normalize(segment.text),
                endMS: segment.endMS
            )
        )
    }

    private static func clean(_ text: String) -> String {
        text.split(whereSeparator: \Character.isWhitespace).joined(separator: " ")
    }

    private static func normalize(_ text: String) -> String {
        String(
            text.lowercased().unicodeScalars.filter {
                CharacterSet.alphanumerics.contains($0)
            }
        )
    }

    private static func isEquivalent(_ lhs: String, _ rhs: String) -> Bool {
        guard !lhs.isEmpty, !rhs.isEmpty else { return false }
        if lhs == rhs { return true }
        let shorter = lhs.count <= rhs.count ? lhs : rhs
        let longer = lhs.count > rhs.count ? lhs : rhs
        return shorter.count >= 12
            && Double(shorter.count) / Double(longer.count) >= 0.78
            && longer.contains(shorter)
    }
}
