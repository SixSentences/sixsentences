import AVFoundation
import CoreGraphics
import CoreMedia
import Foundation
import ScreenCaptureKit
import Speech

enum CaptureError: LocalizedError {
    case microphoneDenied
    case speechDenied
    case screenAudioDenied
    case speechUnavailable(String)
    case speechModelUnavailable(String)
    case noDisplay

    var errorDescription: String? {
        switch self {
        case .microphoneDenied:
            "Microphone permission is required."
        case .speechDenied:
            "Speech Recognition permission is required."
        case .screenAudioDenied:
            "Screen & System Audio Recording permission is required. No video frames are captured."
        case let .speechUnavailable(language):
            "Local Apple Speech does not support \(language) on this Mac. Choose another language in Companion Settings."
        case let .speechModelUnavailable(language):
            "The local Apple Speech model for \(language) is unavailable. Connect to the internet once so macOS can install it, then try again."
        case .noDisplay:
            "No display is available for system-audio capture."
        }
    }
}

enum AudioCaptureMode: Equatable, Sendable {
    case conversation
    case microphoneOnly

    var includesSystemAudio: Bool { self == .conversation }
}

enum AudioCapturePermissionPlan {
    static func requiresScreenCapture(for mode: AudioCaptureMode) -> Bool {
        mode.includesSystemAudio
    }
}

private enum BufferedSpeechAudio {
    case pcm(AVAudioPCMBuffer)
    case sample(CMSampleBuffer)
}

/// One bounded Apple Speech task. Audio is never written to disk or sent to
/// SixSentences; only `isFinal` transcription results leave this class.
private final class LegacySpeechChannelRecognizer: SpeechRecognizingChannel, @unchecked Sendable {
    private let channel: SegmentChannel
    private let locale: Locale
    private let elapsedMS: () -> Int64
    private let onUpdate: (SpeechUpdate) -> Void
    private let onWarning: (String) -> Void
    private let usesGermanWarnings: Bool
    private let contextualStrings: [String]
    private let queue: DispatchQueue
    private var recognizer: SFSpeechRecognizer?
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private var bufferedAudio: [BufferedSpeechAudio] = []
    private var taskAnchorMS: Int64 = 0
    private var lastPartialText = ""
    private var lastPartialChange = DispatchTime.now()
    private var finishing = false
    private var enabled = false
    private var timer: DispatchSourceTimer?
    private var finishContinuation: CheckedContinuation<Void, Never>?

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
        queue = DispatchQueue(label: "com.sixsentences.companion.speech.\(channel.rawValue)")
    }

    func start() async throws {
        guard let recognizer = SFSpeechRecognizer(locale: locale), recognizer.isAvailable else {
            throw CaptureError.speechUnavailable(locale.identifier)
        }
        guard recognizer.supportsOnDeviceRecognition else {
            throw CaptureError.speechUnavailable(locale.identifier)
        }
        self.recognizer = recognizer
        queue.sync {
            enabled = true
            beginTask()
            startTimer()
        }
    }

    func append(_ buffer: AVAudioPCMBuffer) {
        queue.async { [weak self] in
            guard let self, self.enabled else { return }
            if self.finishing || self.request == nil {
                if let copy = Self.copy(buffer), self.bufferedAudio.count < 128 {
                    self.bufferedAudio.append(.pcm(copy))
                }
                return
            }
            self.request?.append(buffer)
        }
    }

    func append(_ sampleBuffer: CMSampleBuffer) {
        queue.async { [weak self] in
            guard let self, self.enabled else { return }
            if self.finishing || self.request == nil {
                if self.bufferedAudio.count < 128 {
                    self.bufferedAudio.append(.sample(sampleBuffer))
                }
                return
            }
            self.request?.appendAudioSampleBuffer(sampleBuffer)
        }
    }

    func finishRecognition() async {
        await finishRecognition(timeout: .seconds(2))
    }

    private func finishRecognition(timeout: Duration) async {
        await withCheckedContinuation { continuation in
            queue.async { [weak self] in
                guard let self else {
                    continuation.resume()
                    return
                }
                self.enabled = false
                self.finishing = true
                self.timer?.cancel()
                self.timer = nil
                guard self.task != nil else {
                    continuation.resume()
                    return
                }
                self.finishContinuation = continuation
                self.request?.endAudio()
                self.queue.asyncAfter(deadline: .now() + timeout.timeInterval) { [weak self] in
                    guard let self, let continuation = self.finishContinuation else { return }
                    self.finishContinuation = nil
                    self.task?.cancel()
                    continuation.resume()
                }
            }
        }
    }

    func cancelRecognition() {
        queue.async { [weak self] in
            guard let self else { return }
            self.enabled = false
            self.timer?.cancel()
            self.timer = nil
            self.task?.cancel()
            self.task = nil
            self.request = nil
            self.bufferedAudio.removeAll(keepingCapacity: false)
            if let continuation = self.finishContinuation {
                self.finishContinuation = nil
                continuation.resume()
            }
        }
    }

    private func beginTask() {
        guard enabled, task == nil, let recognizer else { return }
        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        request.requiresOnDeviceRecognition = true
        request.taskHint = .dictation
        request.addsPunctuation = true
        request.contextualStrings = contextualStrings
        self.request = request
        taskAnchorMS = elapsedMS()
        lastPartialText = ""
        lastPartialChange = .now()
        finishing = false

        task = recognizer.recognitionTask(with: request) { [weak self] result, error in
            guard let self else { return }
            self.queue.async {
                if let result {
                    self.consume(result)
                }
                if result?.isFinal == true || error != nil {
                    let shouldRestart = self.enabled
                    self.task = nil
                    self.request = nil
                    self.finishing = false
                    if let error, shouldRestart {
                        let source = self.channel == .microphone ? "Mikrofon" : "Systemaudio"
                        self.onWarning(
                            self.usesGermanWarnings
                                ? "\(source)-Transkription wurde neu gestartet: \(error.localizedDescription)"
                                : "\(self.channel.speakerLabel) transcription restarted: \(error.localizedDescription)"
                        )
                    }
                    if shouldRestart {
                        self.beginTask()
                        self.drainBufferedAudio()
                    } else {
                        self.bufferedAudio.removeAll(keepingCapacity: false)
                        if let continuation = self.finishContinuation {
                            self.finishContinuation = nil
                            continuation.resume()
                        }
                    }
                }
            }
        }
    }

    private func consume(_ result: SFSpeechRecognitionResult) {
        let text = result.bestTranscription.formattedString
        if text != lastPartialText {
            lastPartialText = text
            lastPartialChange = .now()
        }
        let segments = result.bestTranscription.segments
        let start = segments.first.map { taskAnchorMS + Int64($0.timestamp * 1_000) }
            ?? taskAnchorMS
        let end = segments.last.map {
            taskAnchorMS + Int64(($0.timestamp + $0.duration) * 1_000)
        } ?? elapsedMS()
        onUpdate(
            SpeechUpdate(
                channel: channel,
                text: text,
                isFinal: result.isFinal,
                startMS: max(0, start),
                endMS: max(start + 1, end)
            )
        )
    }

    private func startTimer() {
        let timer = DispatchSource.makeTimerSource(queue: queue)
        timer.schedule(deadline: .now() + .milliseconds(500), repeating: .milliseconds(500))
        timer.setEventHandler { [weak self] in
            guard let self, self.enabled, !self.finishing else { return }
            let taskAge = self.elapsedMS() - self.taskAnchorMS
            let quietNanos = DispatchTime.now().uptimeNanoseconds
                - self.lastPartialChange.uptimeNanoseconds
            let quietLongEnough = !self.lastPartialText.isEmpty && quietNanos >= 1_300_000_000
            if quietLongEnough || taskAge >= 45_000 {
                self.finishing = true
                self.request?.endAudio()
            }
        }
        self.timer = timer
        timer.resume()
    }

    private func drainBufferedAudio() {
        let audio = bufferedAudio
        bufferedAudio.removeAll(keepingCapacity: true)
        for item in audio {
            switch item {
            case let .pcm(buffer):
                request?.append(buffer)
            case let .sample(sampleBuffer):
                request?.appendAudioSampleBuffer(sampleBuffer)
            }
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
            guard
                let sourceData = sourceBuffers[index].mData,
                let destinationData = destinationBuffers[index].mData
            else { continue }
            memcpy(destinationData, sourceData, Int(sourceBuffers[index].mDataByteSize))
            destinationBuffers[index].mDataByteSize = sourceBuffers[index].mDataByteSize
        }
        return copy
    }
}

private extension Duration {
    var timeInterval: TimeInterval {
        let components = self.components
        return TimeInterval(components.seconds)
            + TimeInterval(components.attoseconds) / 1_000_000_000_000_000_000
    }
}

private final class MicrophoneCapture {
    private let engine = AVAudioEngine()
    private let recognizer: any SpeechRecognizingChannel
    private var running = false

    init(recognizer: any SpeechRecognizingChannel) {
        self.recognizer = recognizer
    }

    func start() throws {
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        guard format.sampleRate > 0, format.channelCount > 0 else {
            throw CaptureError.microphoneDenied
        }
        input.installTap(onBus: 0, bufferSize: 2_048, format: format) { [recognizer] buffer, _ in
            recognizer.append(buffer)
        }
        engine.prepare()
        try engine.start()
        running = true
    }

    func stop() {
        guard running else { return }
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        running = false
    }
}

private final class SystemAudioCapture: NSObject, SCStreamOutput, SCStreamDelegate {
    private let recognizer: any SpeechRecognizingChannel
    private let queue = DispatchQueue(label: "com.sixsentences.companion.system-audio")
    private let onWarning: (String) -> Void
    private let usesGermanWarnings: Bool
    private var stream: SCStream?

    init(
        recognizer: any SpeechRecognizingChannel,
        onWarning: @escaping (String) -> Void,
        usesGermanWarnings: Bool
    ) {
        self.recognizer = recognizer
        self.onWarning = onWarning
        self.usesGermanWarnings = usesGermanWarnings
    }

    func start() async throws {
        let content: SCShareableContent
        do {
            content = try await SCShareableContent.excludingDesktopWindows(
                false,
                onScreenWindowsOnly: false
            )
        } catch {
            throw CaptureError.screenAudioDenied
        }
        guard let display = content.displays.first else { throw CaptureError.noDisplay }
        let ownApplications = content.applications.filter {
            $0.processID == ProcessInfo.processInfo.processIdentifier
        }
        let filter = SCContentFilter(
            display: display,
            excludingApplications: ownApplications,
            exceptingWindows: []
        )
        let configuration = SCStreamConfiguration()
        configuration.capturesAudio = true
        configuration.excludesCurrentProcessAudio = true
        configuration.sampleRate = 16_000
        configuration.channelCount = 1
        configuration.width = 2
        configuration.height = 2
        configuration.showsCursor = false
        configuration.minimumFrameInterval = CMTime(value: 1, timescale: 1)

        let stream = SCStream(filter: filter, configuration: configuration, delegate: self)
        // Deliberately register only the audio output. No screen/video output is
        // requested, received, processed, or persisted.
        try stream.addStreamOutput(self, type: .audio, sampleHandlerQueue: queue)
        try await stream.startCapture()
        self.stream = stream
    }

    func stop() async {
        guard let stream else { return }
        try? await stream.stopCapture()
        try? stream.removeStreamOutput(self, type: .audio)
        self.stream = nil
    }

    func stream(
        _ stream: SCStream,
        didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
        of outputType: SCStreamOutputType
    ) {
        guard outputType == .audio, CMSampleBufferDataIsReady(sampleBuffer) else { return }
        recognizer.append(sampleBuffer)
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        onWarning(
            usesGermanWarnings
                ? "Systemaudio wurde beendet: \(error.localizedDescription)"
                : "System audio stopped: \(error.localizedDescription)"
        )
    }
}

final class AudioCaptureCoordinator {
    private let microphoneRecognizer: any SpeechRecognizingChannel
    private let systemRecognizer: (any SpeechRecognizingChannel)?
    private let microphone: MicrophoneCapture
    private let systemAudio: SystemAudioCapture?

    init(
        plan: SpeechRecognitionPlan,
        mode: AudioCaptureMode = .conversation,
        elapsedMS: @escaping () -> Int64,
        onUpdate: @escaping (SpeechUpdate) -> Void,
        onWarning: @escaping (String) -> Void
    ) {
        // SpeechAnalyzer ships in the macOS 26 SDK. The compiler guard keeps
        // macOS 14/15 builds on the existing on-device recognizer when the
        // deployment runner uses an older SDK where those types do not exist.
        #if compiler(>=6.2)
        if #available(macOS 26.0, *), plan.backend == .analyzer {
            microphoneRecognizer = AnalyzerSpeechChannelRecognizer(
                channel: .microphone,
                locale: plan.locale,
                elapsedMS: elapsedMS,
                onUpdate: onUpdate,
                onWarning: onWarning,
                usesGermanWarnings: plan.usesGermanMessages,
                contextualStrings: plan.contextualStrings
            )
            if mode.includesSystemAudio {
                systemRecognizer = AnalyzerSpeechChannelRecognizer(
                    channel: .system,
                    locale: plan.locale,
                    elapsedMS: elapsedMS,
                    onUpdate: onUpdate,
                    onWarning: onWarning,
                    usesGermanWarnings: plan.usesGermanMessages,
                    contextualStrings: plan.contextualStrings
                )
            } else {
                systemRecognizer = nil
            }
        } else {
            microphoneRecognizer = LegacySpeechChannelRecognizer(
                channel: .microphone,
                locale: plan.locale,
                elapsedMS: elapsedMS,
                onUpdate: onUpdate,
                onWarning: onWarning,
                usesGermanWarnings: plan.usesGermanMessages,
                contextualStrings: plan.contextualStrings
            )
            if mode.includesSystemAudio {
                systemRecognizer = LegacySpeechChannelRecognizer(
                    channel: .system,
                    locale: plan.locale,
                    elapsedMS: elapsedMS,
                    onUpdate: onUpdate,
                    onWarning: onWarning,
                    usesGermanWarnings: plan.usesGermanMessages,
                    contextualStrings: plan.contextualStrings
                )
            } else {
                systemRecognizer = nil
            }
        }
        #else
        microphoneRecognizer = LegacySpeechChannelRecognizer(
            channel: .microphone,
            locale: plan.locale,
            elapsedMS: elapsedMS,
            onUpdate: onUpdate,
            onWarning: onWarning,
            usesGermanWarnings: plan.usesGermanMessages,
            contextualStrings: plan.contextualStrings
        )
        if mode.includesSystemAudio {
            systemRecognizer = LegacySpeechChannelRecognizer(
                channel: .system,
                locale: plan.locale,
                elapsedMS: elapsedMS,
                onUpdate: onUpdate,
                onWarning: onWarning,
                usesGermanWarnings: plan.usesGermanMessages,
                contextualStrings: plan.contextualStrings
            )
        } else {
            systemRecognizer = nil
        }
        #endif
        microphone = MicrophoneCapture(recognizer: microphoneRecognizer)
        if let systemRecognizer {
            systemAudio = SystemAudioCapture(
                recognizer: systemRecognizer,
                onWarning: onWarning,
                usesGermanWarnings: plan.usesGermanMessages
            )
        } else {
            systemAudio = nil
        }
    }

    static func prepareSpeech(
        language: String,
        contextualStrings: [String] = []
    ) async throws -> SpeechRecognitionPlan {
        try await SpeechRecognitionPreflight.prepare(
            language: language,
            additionalContextualStrings: contextualStrings
        )
    }

    static func requestPermissions(mode: AudioCaptureMode = .conversation) async throws {
        guard await requestMicrophonePermission() else {
            throw CaptureError.microphoneDenied
        }
        guard await requestSpeechPermission() else {
            throw CaptureError.speechDenied
        }
        if AudioCapturePermissionPlan.requiresScreenCapture(for: mode),
           !CGPreflightScreenCaptureAccess(), !CGRequestScreenCaptureAccess() {
            throw CaptureError.screenAudioDenied
        }
    }

    /// Starts capture after the caller has completed the explicit permission
    /// and consent flow. This method never triggers a second permission prompt.
    func startAuthorized() async throws {
        try await microphoneRecognizer.start()
        do {
            try await systemRecognizer?.start()
            try microphone.start()
            try await systemAudio?.start()
        } catch {
            microphone.stop()
            await systemAudio?.stop()
            microphoneRecognizer.cancelRecognition()
            systemRecognizer?.cancelRecognition()
            throw error
        }
    }

    func stop() async {
        // Stop both capture sources before waiting for Apple Speech to finalize.
        microphone.stop()
        await systemAudio?.stop()
        if let systemRecognizer {
            async let microphoneFinalized: Void = microphoneRecognizer.finishRecognition()
            async let systemFinalized: Void = systemRecognizer.finishRecognition()
            _ = await (microphoneFinalized, systemFinalized)
        } else {
            await microphoneRecognizer.finishRecognition()
        }
        microphoneRecognizer.cancelRecognition()
        systemRecognizer?.cancelRecognition()
    }

    private static func requestMicrophonePermission() async -> Bool {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized:
            true
        case .notDetermined:
            await AVCaptureDevice.requestAccess(for: .audio)
        default:
            false
        }
    }

    private static func requestSpeechPermission() async -> Bool {
        let status: SFSpeechRecognizerAuthorizationStatus = await withCheckedContinuation { continuation in
            SFSpeechRecognizer.requestAuthorization { continuation.resume(returning: $0) }
        }
        return status == .authorized
    }
}
