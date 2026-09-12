import AVFoundation
import CoreMedia
import Foundation
import os
import Speech

enum SpeechRecognitionBackend: Equatable, Sendable {
    case analyzer
    case legacy
}

struct SpeechRecognitionPlan: Equatable, Sendable {
    let locale: Locale
    let backend: SpeechRecognitionBackend
    let usesGermanMessages: Bool
    let contextualStrings: [String]
}

enum AcademicSpeechVocabulary {
    static let maximumTerms = 100

    private static let terms = [
        "SixSentences", "ResearchGate", "arXiv", "DOI", "OpenAlex", "Zotero",
        "GitHub", "OpenRouter", "PyTorch", "scikit-learn", "Terraform",
        "Infrastructure as Code", "IaC", "systematic review", "literature review",
        "SLR", "research question", "related work", "peer review", "preprint",
        "citation", "metadata", "Introduction", "Methodology", "Results", "Discussion",
        "Einleitung", "Methodik", "Ergebnisse", "Diskussion", "hypothesis", "benchmark", "dataset",
        "machine learning", "deep learning", "large language model", "LLM",
        "retrieval augmented", "RAG", "embedding", "transformer", "attention",
        "fine-tuning", "prompt", "token", "context window", "hallucination",
        "evaluation", "reproducibility", "appendix", "abstract", "experiment",
    ]

    static func contextualStrings(
        for locale: Locale,
        additionalTerms: [String] = []
    ) -> [String] {
        guard ["de", "en"].contains(SpeechLocaleResolver.languageCode(locale.identifier))
        else { return [] }
        var seen = Set<String>()
        return (terms + additionalTerms).compactMap { term -> String? in
            let clean = term.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !clean.isEmpty, clean.count <= 64,
                  seen.insert(clean.folding(
                    options: [.caseInsensitive, .diacriticInsensitive],
                    locale: Locale(identifier: "en_US_POSIX")
                  )).inserted
            else { return nil }
            return clean
        }.prefix(maximumTerms).map(\.self)
    }
}

enum SpeechLocaleResolver {
    static func requestedLocale(
        language: String,
        preferredLanguages: [String] = Locale.preferredLanguages
    ) -> Locale {
        let identifier: String
        switch language {
        case "de": identifier = "de-DE"
        case "en": identifier = "en-US"
        default: identifier = preferredLanguages.first ?? "en-US"
        }
        return Locale(identifier: canonicalIdentifier(identifier))
    }

    static func resolve(
        language: String,
        preferredLanguages: [String] = Locale.preferredLanguages,
        supportedLocales: [Locale]
    ) -> Locale? {
        let requested = requestedLocale(
            language: language,
            preferredLanguages: preferredLanguages
        )
        let requestedIdentifier = canonicalIdentifier(requested.identifier)
        let requestedLanguage = languageCode(requestedIdentifier)
        let supported = supportedLocales.reduce(into: [String: Locale]()) { result, locale in
            result[canonicalIdentifier(locale.identifier)] = locale
        }

        if let exact = supported[requestedIdentifier] {
            return Locale(identifier: canonicalIdentifier(exact.identifier))
        }

        let preferredFallbacks: [String]
        switch requestedLanguage {
        case "de": preferredFallbacks = ["de-DE", "de-AT", "de-CH"]
        case "en": preferredFallbacks = ["en-US", "en-GB", "en-CA", "en-AU"]
        default: preferredFallbacks = []
        }
        for identifier in preferredFallbacks {
            if let locale = supported[identifier] { return Locale(identifier: locale.identifier) }
        }

        return supported
            .filter { languageCode($0.key) == requestedLanguage }
            .sorted { $0.key < $1.key }
            .first
            .map { Locale(identifier: canonicalIdentifier($0.value.identifier)) }
    }

    static func usesGerman(
        language: String,
        preferredLanguages: [String] = Locale.preferredLanguages
    ) -> Bool {
        languageCode(requestedLocale(
            language: language,
            preferredLanguages: preferredLanguages
        ).identifier) == "de"
    }

    static func canonicalIdentifier(_ identifier: String) -> String {
        Locale(identifier: identifier.replacingOccurrences(of: "_", with: "-"))
            .identifier(.bcp47)
    }

    static func languageCode(_ identifier: String) -> String {
        canonicalIdentifier(identifier)
            .split(separator: "-", maxSplits: 1)
            .first
            .map(String.init)?
            .lowercased() ?? ""
    }
}

enum SpeechRecognitionPreflight {
    static func prepare(
        language: String,
        preferredLanguages: [String] = Locale.preferredLanguages,
        additionalContextualStrings: [String] = []
    ) async throws -> SpeechRecognitionPlan {
        #if compiler(>=6.2)
        if #available(macOS 26.0, *) {
            return try await prepareAnalyzer(
                language: language,
                preferredLanguages: preferredLanguages,
                additionalContextualStrings: additionalContextualStrings
            )
        }
        #endif
        return try prepareLegacy(
            language: language,
            preferredLanguages: preferredLanguages,
            additionalContextualStrings: additionalContextualStrings
        )
    }

    #if compiler(>=6.2)
    @available(macOS 26.0, *)
    private static func prepareAnalyzer(
        language: String,
        preferredLanguages: [String],
        additionalContextualStrings: [String]
    ) async throws -> SpeechRecognitionPlan {
        let requested = SpeechLocaleResolver.requestedLocale(
            language: language,
            preferredLanguages: preferredLanguages
        )
        guard SpeechTranscriber.isAvailable else {
            throw CaptureError.speechUnavailable(requested.identifier(.bcp47))
        }

        let supportedLocales = await SpeechTranscriber.supportedLocales
        let appleEquivalent = await SpeechTranscriber.supportedLocale(equivalentTo: requested)
        let sameLanguageEquivalent = appleEquivalent.flatMap { equivalent -> Locale? in
            let equivalentIdentifier = SpeechLocaleResolver.canonicalIdentifier(equivalent.identifier)
            guard SpeechLocaleResolver.languageCode(equivalent.identifier)
                    == SpeechLocaleResolver.languageCode(requested.identifier),
                  supportedLocales.contains(where: {
                      SpeechLocaleResolver.canonicalIdentifier($0.identifier) == equivalentIdentifier
                  })
            else { return nil }
            return equivalent
        }
        let resolved = sameLanguageEquivalent ?? SpeechLocaleResolver.resolve(
            language: language,
            preferredLanguages: preferredLanguages,
            supportedLocales: supportedLocales
        )
        guard let resolved else {
            throw CaptureError.speechUnavailable(requested.identifier(.bcp47))
        }

        let canonicalLocale = Locale(
            identifier: SpeechLocaleResolver.canonicalIdentifier(resolved.identifier)
        )
        let transcribers = AnalyzerTranscriberPair(locale: canonicalLocale)
        do {
            let isReserved = await AssetInventory.reservedLocales.contains {
                SpeechLocaleResolver.canonicalIdentifier($0.identifier)
                    == SpeechLocaleResolver.canonicalIdentifier(canonicalLocale.identifier)
            }
            if !isReserved {
                _ = try await AssetInventory.reserve(locale: canonicalLocale)
            }
            if let installer = try await AssetInventory.assetInstallationRequest(
                supporting: transcribers.modules
            ) {
                try await installer.downloadAndInstall()
            }
            guard await AssetInventory.status(forModules: transcribers.modules) == .installed else {
                throw CaptureError.speechModelUnavailable(canonicalLocale.identifier(.bcp47))
            }
        } catch let error as CaptureError {
            throw error
        } catch {
            throw CaptureError.speechModelUnavailable(canonicalLocale.identifier(.bcp47))
        }

        return SpeechRecognitionPlan(
            locale: canonicalLocale,
            backend: .analyzer,
            usesGermanMessages: SpeechLocaleResolver.usesGerman(
                language: "auto",
                preferredLanguages: preferredLanguages
            ),
            contextualStrings: AcademicSpeechVocabulary.contextualStrings(
                for: canonicalLocale,
                additionalTerms: additionalContextualStrings
            )
        )
    }
    #endif

    private static func prepareLegacy(
        language: String,
        preferredLanguages: [String],
        additionalContextualStrings: [String]
    ) throws -> SpeechRecognitionPlan {
        let requested = SpeechLocaleResolver.requestedLocale(
            language: language,
            preferredLanguages: preferredLanguages
        )
        guard let locale = SpeechLocaleResolver.resolve(
            language: language,
            preferredLanguages: preferredLanguages,
            supportedLocales: Array(SFSpeechRecognizer.supportedLocales())
        ), let recognizer = SFSpeechRecognizer(locale: locale), recognizer.isAvailable,
        recognizer.supportsOnDeviceRecognition
        else {
            throw CaptureError.speechUnavailable(requested.identifier(.bcp47))
        }
        return SpeechRecognitionPlan(
            locale: locale,
            backend: .legacy,
            usesGermanMessages: SpeechLocaleResolver.usesGerman(
                language: "auto",
                preferredLanguages: preferredLanguages
            ),
            contextualStrings: AcademicSpeechVocabulary.contextualStrings(
                for: locale,
                additionalTerms: additionalContextualStrings
            )
        )
    }
}

#if compiler(>=6.2)
@available(macOS 26.0, *)
private struct AnalyzerTranscriberPair {
    let preview: SpeechTranscriber
    let durable: SpeechTranscriber

    init(locale: Locale) {
        // Apple doesn't guarantee that an unchanged volatile result is emitted
        // again with isFinal=true. Keep the low-latency stream UI-only and use a
        // separate final-only module as the sole source of durable segments.
        preview = SpeechTranscriber(
            locale: locale,
            transcriptionOptions: [],
            reportingOptions: [.volatileResults, .fastResults],
            attributeOptions: [.audioTimeRange]
        )
        durable = SpeechTranscriber(
            locale: locale,
            transcriptionOptions: [],
            reportingOptions: [],
            attributeOptions: [.audioTimeRange]
        )
    }

    var modules: [SpeechTranscriber] { [preview, durable] }
}
#endif

private final class SpeechAudioBufferConverter {
    enum ConversionError: Error {
        case unavailable
        case allocationFailed
        case conversionFailed
    }

    private var converter: AVAudioConverter?

    func convert(_ buffer: AVAudioPCMBuffer, to format: AVAudioFormat) throws -> AVAudioPCMBuffer {
        guard buffer.format != format else { return buffer }
        if converter?.inputFormat != buffer.format || converter?.outputFormat != format {
            converter = AVAudioConverter(from: buffer.format, to: format)
            converter?.primeMethod = .none
        }
        guard let converter else { throw ConversionError.unavailable }
        let ratio = format.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount((Double(buffer.frameLength) * ratio).rounded(.up))
        guard let output = AVAudioPCMBuffer(
            pcmFormat: format,
            frameCapacity: max(1, capacity)
        ) else { throw ConversionError.allocationFailed }

        var conversionError: NSError?
        let state = OSAllocatedUnfairLock(initialState: false)
        let status = converter.convert(to: output, error: &conversionError) { _, inputStatus in
            let alreadyProvided = state.withLock { provided in
                let previous = provided
                provided = true
                return previous
            }
            inputStatus.pointee = alreadyProvided ? .noDataNow : .haveData
            return alreadyProvided ? nil : buffer
        }
        guard status != .error else { throw ConversionError.conversionFailed }
        return output
    }
}

protocol SpeechRecognizingChannel: AnyObject {
    func start() async throws
    func append(_ buffer: AVAudioPCMBuffer)
    func append(_ sampleBuffer: CMSampleBuffer)
    func finishRecognition() async
    func cancelRecognition()
}

enum RecognitionFinalizationOutcome: Equatable, Sendable {
    case completed
    case timedOut
    case cancelled
}

private final class RecognitionFinalizationResolution: @unchecked Sendable {
    private let lock = NSLock()
    private var outcome: RecognitionFinalizationOutcome?
    private var continuation: CheckedContinuation<RecognitionFinalizationOutcome, Never>?

    func wait() async -> RecognitionFinalizationOutcome {
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
    func resolve(_ value: RecognitionFinalizationOutcome) -> Bool {
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
}

enum RecognitionFinalizationDeadline {
    /// Runs finalization without allowing a wedged Apple result stream to
    /// block Stop indefinitely. Timeout/cancellation requests Apple's
    /// immediate finish path and cleanup is invoked exactly once by this call.
    static func run(
        timeout: Duration,
        operation: @escaping @Sendable () async -> Void,
        onForcedFinish: @escaping @Sendable () -> Void,
        cleanup: @escaping @Sendable () -> Void
    ) async -> RecognitionFinalizationOutcome {
        let resolution = RecognitionFinalizationResolution()
        let operationTask = Task {
            await operation()
            resolution.resolve(.completed)
        }
        let timeoutTask = Task {
            do {
                try await Task.sleep(for: timeout)
            } catch {
                return
            }
            resolution.resolve(.timedOut)
        }
        let outcome = await withTaskCancellationHandler {
            await resolution.wait()
        } onCancel: {
            if resolution.resolve(.cancelled) {
                timeoutTask.cancel()
            }
        }
        timeoutTask.cancel()
        if outcome != .completed {
            operationTask.cancel()
            onForcedFinish()
        }
        cleanup()
        return outcome
    }
}

enum SpeechAnalyzerResultMapper {
    static func update(
        channel: SegmentChannel,
        text: String,
        isFinal: Bool,
        range: CMTimeRange,
        anchorMS: Int64
    ) -> SpeechUpdate? {
        let normalizedText = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !normalizedText.isEmpty else { return nil }
        let start = anchorMS + milliseconds(range.start, minimum: 0)
        let duration = milliseconds(range.duration, minimum: 1)
        return SpeechUpdate(
            channel: channel,
            text: normalizedText,
            isFinal: isFinal,
            startMS: start,
            endMS: start + duration
        )
    }

    private static func milliseconds(_ time: CMTime, minimum: Int64) -> Int64 {
        let seconds = CMTimeGetSeconds(time)
        guard seconds.isFinite else { return minimum }
        return max(minimum, Int64(seconds * 1_000))
    }
}

enum SpeechAnalyzerResultRoute {
    static func previewUpdate(
        channel: SegmentChannel,
        text: String,
        resultIsFinal _: Bool,
        range: CMTimeRange,
        anchorMS: Int64
    ) -> SpeechUpdate? {
        SpeechAnalyzerResultMapper.update(
            channel: channel,
            text: text,
            isFinal: false,
            range: range,
            anchorMS: anchorMS
        )
    }

    static func durableUpdate(
        channel: SegmentChannel,
        text: String,
        resultIsFinal: Bool,
        range: CMTimeRange,
        anchorMS: Int64
    ) -> SpeechUpdate? {
        guard resultIsFinal else { return nil }
        return SpeechAnalyzerResultMapper.update(
            channel: channel,
            text: text,
            isFinal: true,
            range: range,
            anchorMS: anchorMS
        )
    }
}

#if compiler(>=6.2)
@available(macOS 26.0, *)
final class AnalyzerSpeechChannelRecognizer: SpeechRecognizingChannel, @unchecked Sendable {
    private let channel: SegmentChannel
    private let locale: Locale
    private let elapsedMS: () -> Int64
    private let onUpdate: (SpeechUpdate) -> Void
    private let onWarning: (String) -> Void
    private let usesGermanWarnings: Bool
    private let contextualStrings: [String]
    private let queue: DispatchQueue
    private let converter = SpeechAudioBufferConverter()
    private var analyzer: SpeechAnalyzer?
    private var previewTranscriber: SpeechTranscriber?
    private var durableTranscriber: SpeechTranscriber?
    private var analyzerFormat: AVAudioFormat?
    private var inputContinuation: AsyncStream<AnalyzerInput>.Continuation?
    private var previewResultTask: Task<Void, Never>?
    private var durableResultTask: Task<Void, Never>?
    private var enabled = false
    private var taskAnchorMS: Int64 = 0
    private var didWarnAboutAudio = false
    private var didCleanup = true

    init(
        channel: SegmentChannel,
        locale: Locale,
        elapsedMS: @escaping () -> Int64,
        onUpdate: @escaping (SpeechUpdate) -> Void,
        onWarning: @escaping (String) -> Void,
        usesGermanWarnings: Bool,
        contextualStrings: [String]
    ) {
        self.channel = channel
        self.locale = locale
        self.elapsedMS = elapsedMS
        self.onUpdate = onUpdate
        self.onWarning = onWarning
        self.usesGermanWarnings = usesGermanWarnings
        self.contextualStrings = Array(contextualStrings.prefix(AcademicSpeechVocabulary.maximumTerms))
        queue = DispatchQueue(label: "com.sixsentences.companion.analyzer.\(channel.rawValue)")
    }

    func start() async throws {
        let transcribers = AnalyzerTranscriberPair(locale: locale)
        guard await AssetInventory.status(forModules: transcribers.modules) == .installed,
              let analyzerFormat = await SpeechAnalyzer.bestAvailableAudioFormat(
                  compatibleWith: transcribers.modules
              )
        else { throw CaptureError.speechModelUnavailable(locale.identifier(.bcp47)) }

        let analyzer = SpeechAnalyzer(modules: transcribers.modules)
        let analysisContext = AnalysisContext()
        if !contextualStrings.isEmpty {
            analysisContext.contextualStrings[.general] = contextualStrings
        }
        try await analyzer.setContext(analysisContext)
        let (inputSequence, continuation) = AsyncStream<AnalyzerInput>.makeStream(
            bufferingPolicy: .bufferingNewest(256)
        )
        previewTranscriber = transcribers.preview
        durableTranscriber = transcribers.durable
        self.analyzer = analyzer
        self.analyzerFormat = analyzerFormat
        taskAnchorMS = elapsedMS()
        previewResultTask = Task { [weak self] in
            do {
                for try await result in transcribers.preview.results {
                    self?.consumePreview(result)
                }
            } catch is CancellationError {
                return
            } catch {
                self?.warnAboutRecognition(error)
            }
        }
        durableResultTask = Task { [weak self] in
            do {
                for try await result in transcribers.durable.results {
                    self?.consumeDurable(result)
                }
            } catch is CancellationError {
                return
            } catch {
                self?.warnAboutRecognition(error)
            }
        }
        queue.sync {
            inputContinuation = continuation
            enabled = true
            didWarnAboutAudio = false
            didCleanup = false
        }
        do {
            try await analyzer.start(inputSequence: inputSequence)
        } catch {
            queue.sync {
                enabled = false
                inputContinuation?.finish()
                inputContinuation = nil
            }
            previewResultTask?.cancel()
            durableResultTask?.cancel()
            cleanup()
            throw error
        }
    }

    func append(_ buffer: AVAudioPCMBuffer) {
        guard let copy = Self.copy(buffer) else { return }
        enqueue(copy)
    }

    func append(_ sampleBuffer: CMSampleBuffer) {
        guard let buffer = Self.pcmBuffer(from: sampleBuffer) else {
            warnAboutAudio(nil)
            return
        }
        enqueue(buffer)
    }

    func finishRecognition() async {
        let (analyzer, previewResultTask, durableResultTask) = queue.sync {
            () -> (SpeechAnalyzer?, Task<Void, Never>?, Task<Void, Never>?) in
            enabled = false
            inputContinuation?.finish()
            inputContinuation = nil
            return (self.analyzer, self.previewResultTask, self.durableResultTask)
        }
        guard let analyzer else {
            cleanup()
            return
        }
        _ = await RecognitionFinalizationDeadline.run(
            timeout: .milliseconds(2_500),
            operation: { [weak self] in
                do {
                    try await analyzer.finalizeAndFinishThroughEndOfInput()
                } catch is CancellationError {
                    return
                } catch {
                    guard !Task.isCancelled else { return }
                    self?.warnAboutRecognition(error)
                }
                guard !Task.isCancelled else { return }
                await previewResultTask?.value
                await durableResultTask?.value
            },
            onForcedFinish: {
                previewResultTask?.cancel()
                durableResultTask?.cancel()
                Task { await analyzer.cancelAndFinishNow() }
            },
            cleanup: { [weak self] in self?.cleanup() }
        )
    }

    func cancelRecognition() {
        let analyzer = queue.sync { () -> SpeechAnalyzer? in
            enabled = false
            inputContinuation?.finish()
            inputContinuation = nil
            return self.analyzer
        }
        previewResultTask?.cancel()
        durableResultTask?.cancel()
        Task { await analyzer?.cancelAndFinishNow() }
        cleanup()
    }

    private func enqueue(_ buffer: AVAudioPCMBuffer) {
        queue.async { [weak self] in
            guard let self, self.enabled, let format = self.analyzerFormat,
                  let continuation = self.inputContinuation
            else { return }
            do {
                let converted = try self.converter.convert(buffer, to: format)
                continuation.yield(AnalyzerInput(buffer: converted))
            } catch {
                self.warnAboutAudio(error)
            }
        }
    }

    private func consumePreview(_ result: SpeechTranscriber.Result) {
        guard let update = SpeechAnalyzerResultRoute.previewUpdate(
            channel: channel,
            text: String(result.text.characters),
            resultIsFinal: result.isFinal,
            range: result.range,
            anchorMS: taskAnchorMS
        ) else { return }
        deliver(update)
    }

    private func consumeDurable(_ result: SpeechTranscriber.Result) {
        guard let update = SpeechAnalyzerResultRoute.durableUpdate(
            channel: channel,
            text: String(result.text.characters),
            resultIsFinal: result.isFinal,
            range: result.range,
            anchorMS: taskAnchorMS
        ) else { return }
        deliver(update)
    }

    private func deliver(_ update: SpeechUpdate) {
        CompanionLatency.recordAnalyzerResult(
            channel: channel,
            isFinal: update.isFinal,
            lagMS: max(0, elapsedMS() - update.endMS)
        )
        onUpdate(update)
    }

    private func warnAboutRecognition(_ error: Error) {
        let source = channel == .microphone ? "Mikrofon" : "Systemaudio"
        onWarning(
            usesGermanWarnings
                ? "\(source)-Transkription wurde beendet: \(error.localizedDescription)"
                : "\(channel.speakerLabel) transcription stopped: \(error.localizedDescription)"
        )
    }

    private func warnAboutAudio(_ error: Error?) {
        queue.async { [weak self] in
            guard let self, !self.didWarnAboutAudio else { return }
            self.didWarnAboutAudio = true
            let detail = error.map { ": \($0.localizedDescription)" } ?? ""
            self.onWarning(
                self.usesGermanWarnings
                    ? "Ein Audiopuffer konnte nicht lokal transkribiert werden\(detail)."
                    : "An audio buffer could not be transcribed locally\(detail)."
            )
        }
    }

    private func cleanup() {
        queue.sync {
            guard !didCleanup else { return }
            didCleanup = true
            enabled = false
            inputContinuation = nil
            analyzerFormat = nil
            analyzer = nil
            previewTranscriber = nil
            durableTranscriber = nil
            previewResultTask = nil
            durableResultTask = nil
        }
    }

    private static func copy(_ source: AVAudioPCMBuffer) -> AVAudioPCMBuffer? {
        guard let copy = AVAudioPCMBuffer(
            pcmFormat: source.format,
            frameCapacity: source.frameLength
        ) else { return nil }
        copy.frameLength = source.frameLength
        let sourceBuffers = UnsafeMutableAudioBufferListPointer(source.mutableAudioBufferList)
        let destinationBuffers = UnsafeMutableAudioBufferListPointer(copy.mutableAudioBufferList)
        guard sourceBuffers.count == destinationBuffers.count else { return nil }
        for index in sourceBuffers.indices {
            guard let sourceData = sourceBuffers[index].mData,
                  let destinationData = destinationBuffers[index].mData
            else { continue }
            let size = min(
                Int(sourceBuffers[index].mDataByteSize),
                Int(destinationBuffers[index].mDataByteSize)
            )
            memcpy(destinationData, sourceData, size)
            destinationBuffers[index].mDataByteSize = UInt32(size)
        }
        return copy
    }

    private static func pcmBuffer(from sampleBuffer: CMSampleBuffer) -> AVAudioPCMBuffer? {
        guard CMSampleBufferDataIsReady(sampleBuffer),
              let description = CMSampleBufferGetFormatDescription(sampleBuffer),
              let streamDescription = CMAudioFormatDescriptionGetStreamBasicDescription(description),
              let format = AVAudioFormat(streamDescription: streamDescription)
        else { return nil }

        var sizeNeeded = 0
        guard CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sampleBuffer,
            bufferListSizeNeededOut: &sizeNeeded,
            bufferListOut: nil,
            bufferListSize: 0,
            blockBufferAllocator: nil,
            blockBufferMemoryAllocator: nil,
            flags: 0,
            blockBufferOut: nil
        ) == noErr, sizeNeeded > 0 else { return nil }

        let rawList = UnsafeMutableRawPointer.allocate(
            byteCount: sizeNeeded,
            alignment: MemoryLayout<AudioBufferList>.alignment
        )
        defer { rawList.deallocate() }
        let audioBufferList = rawList.bindMemory(to: AudioBufferList.self, capacity: 1)
        var retainedBlockBuffer: CMBlockBuffer?
        guard CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sampleBuffer,
            bufferListSizeNeededOut: nil,
            bufferListOut: audioBufferList,
            bufferListSize: sizeNeeded,
            blockBufferAllocator: kCFAllocatorDefault,
            blockBufferMemoryAllocator: kCFAllocatorDefault,
            flags: 0,
            blockBufferOut: &retainedBlockBuffer
        ) == noErr else { return nil }

        let frameCount = AVAudioFrameCount(CMSampleBufferGetNumSamples(sampleBuffer))
        guard frameCount > 0,
              let output = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frameCount)
        else { return nil }
        output.frameLength = frameCount
        let sourceBuffers = UnsafeMutableAudioBufferListPointer(audioBufferList)
        let destinationBuffers = UnsafeMutableAudioBufferListPointer(output.mutableAudioBufferList)
        guard sourceBuffers.count == destinationBuffers.count else { return nil }
        for index in sourceBuffers.indices {
            guard let sourceData = sourceBuffers[index].mData,
                  let destinationData = destinationBuffers[index].mData
            else { continue }
            let size = min(
                Int(sourceBuffers[index].mDataByteSize),
                Int(destinationBuffers[index].mDataByteSize)
            )
            memcpy(destinationData, sourceData, size)
            destinationBuffers[index].mDataByteSize = UInt32(size)
        }
        return output
    }
}
#endif
