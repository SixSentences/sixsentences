import AppKit
import SwiftUI

enum CompanionPalette {
    static let canvas = Color(red: 0.045, green: 0.055, blue: 0.052)
    static let panel = Color(red: 0.075, green: 0.09, blue: 0.085)
    static let panelRaised = Color(red: 0.105, green: 0.125, blue: 0.116)
    static let line = Color.white.opacity(0.10)
    static let mint = Color(red: 0.48, green: 0.75, blue: 0.67)
    static let ink = Color(red: 0.95, green: 0.96, blue: 0.94)
    static let secondary = Color.white.opacity(0.60)
    static let recording = Color(red: 0.96, green: 0.29, blue: 0.28)
}

private enum CompanionLegalNotices {
    static var bundledURL: URL? {
        Bundle.main.url(forResource: "ThirdPartyNotices", withExtension: "txt")
    }

    @discardableResult
    static func open() -> Bool {
        guard let bundledURL else { return false }
        return NSWorkspace.shared.open(bundledURL)
    }
}

@main
struct SixSentencesCompanionApp: App {
    @NSApplicationDelegateAdaptor(CompanionApplicationDelegate.self) private var appDelegate
    @StateObject private var model = CompanionViewModel()
    @StateObject private var paperModel = PaperCompanionViewModel()
    @StateObject private var brainstormModel = BrainstormViewModel()
    @StateObject private var openRequests = CompanionOpenRequestBroker.shared
    @StateObject private var updater = CompanionUpdaterController()
    @State private var isExpanded = true

    var body: some Scene {
        WindowGroup("SixSentences Companion", id: "companion") {
            CompanionOverlayView(
                model: model,
                paperModel: paperModel,
                brainstormModel: brainstormModel,
                openRequests: openRequests,
                isExpanded: $isExpanded
            )
                .background(WindowConfigurator())
                .onAppear { model.launch() }
        }
        .defaultSize(width: 410, height: 610)
        .windowStyle(.hiddenTitleBar)
        .windowResizability(.contentSize)

        Window("SixSentences Conversation", id: "conversation-chat") {
            CompanionChatWindow(model: model)
                .background(ChatWindowConfigurator())
                .onAppear { model.refreshAskHistory() }
        }
        .defaultSize(width: 640, height: 700)
        .windowResizability(.contentMinSize)

        Window("SixSentences Paper Companion", id: "paper-companion") {
            PaperCompanionView(model: paperModel)
                .background(ChatWindowConfigurator())
        }
        .defaultSize(width: 1_160, height: 760)
        .windowResizability(.contentMinSize)

        MenuBarExtra {
            CompanionMenu(
                model: model,
                paperModel: paperModel,
                updater: updater
            )
        } label: {
            ZStack(alignment: .bottomTrailing) {
                Image(nsImage: SixSentencesBrand.menuBarImage)
                if SixSentencesMenuBarPresentation.showsRecordingBadge(model.isRecording) {
                    Circle()
                        .fill(CompanionPalette.recording)
                        .overlay(Circle().stroke(Color.primary, lineWidth: 1))
                        .frame(
                            width: SixSentencesMenuBarPresentation.badgeDiameter,
                            height: SixSentencesMenuBarPresentation.badgeDiameter
                        )
                }
            }
            .frame(
                width: SixSentencesBrand.menuBarPointSize.width,
                height: SixSentencesBrand.menuBarPointSize.height
            )
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(SixSentencesMenuBarPresentation.accessibilityLabel(
                isRecording: model.isRecording,
                usesGerman: model.usesGerman
            ))
            .accessibilityValue(SixSentencesMenuBarPresentation.accessibilityValue(
                isRecording: model.isRecording,
                usesGerman: model.usesGerman
            ))
        }
        .menuBarExtraStyle(.menu)

        Settings {
            CompanionSettingsView(
                model: model,
                paperModel: paperModel,
                brainstormModel: brainstormModel,
                updater: updater
            )
                .frame(width: 520, height: 590)
        }
    }
}

@MainActor
private final class CompanionApplicationDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApplication.shared.setActivationPolicy(.regular)
        NSApplication.shared.activate(ignoringOtherApps: true)
    }

    func applicationShouldHandleReopen(
        _ sender: NSApplication,
        hasVisibleWindows flag: Bool
    ) -> Bool {
        WindowRegistry.showAndActivate()
        return true
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }

    func application(_ application: NSApplication, open urls: [URL]) {
        urls.forEach(CompanionOpenRequestBroker.shared.receive)
        NSApplication.shared.activate(ignoringOtherApps: true)
        WindowRegistry.showAndActivate()
    }
}

private struct CompanionOverlayView: View {
    @ObservedObject var model: CompanionViewModel
    @ObservedObject var paperModel: PaperCompanionViewModel
    @ObservedObject var brainstormModel: BrainstormViewModel
    @ObservedObject var openRequests: CompanionOpenRequestBroker
    @Binding var isExpanded: Bool
    @State private var experienceMode: CompanionExperienceMode = .conversation
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        VStack(spacing: 0) {
            header
            if isExpanded {
                Divider().overlay(CompanionPalette.line)
                ScrollView {
                    VStack(spacing: 14) {
                        if model.isConnected {
                            experienceModeControl
                            recordingCard
                            if !model.latestTranscript.isEmpty {
                                transcriptCard
                            }
                            if experienceMode != .brainstorm {
                                askCard
                            }
                        } else {
                            onboardingCard
                        }
                        notices
                    }
                    .padding(16)
                }
                Divider().overlay(CompanionPalette.line)
                if model.isConnected {
                    footer
                } else {
                    onboardingFooter
                }
            } else {
                compactBar
            }
        }
        .frame(width: 410, height: isExpanded ? 610 : 122)
        .background(CompanionPalette.canvas)
        .foregroundStyle(CompanionPalette.ink)
        .sheet(isPresented: $model.showsConsent) {
            ConsentView(model: model)
        }
        .onAppear {
            consumePendingOpenRequests()
            brainstormModel.recoverPendingIntent(ownerAccountID: model.accountIdentity)
        }
        .onChange(of: openRequests.pendingPaperURL) { _, _ in consumePendingOpenRequests() }
        .onChange(of: openRequests.pendingPairingURL) { _, _ in consumePendingOpenRequests() }
        .onChange(of: BrainstormRecoveryContext(
            accountID: model.accountIdentity,
            isConnected: model.isConnected
        )) { previous, current in
            if BrainstormAccountTransitionPolicy.shouldScrub(
                previous: previous,
                current: current
            ) {
                brainstormModel.scrubPrivateState()
            }
            if let accountID = BrainstormAccountTransitionPolicy.recoveryAccountID(
                for: current
            ) {
                brainstormModel.recoverPendingIntent(ownerAccountID: accountID)
            }
        }
        .onChange(of: model.activeExperienceMode) { _, mode in
            if let mode { experienceMode = mode }
        }
        .onChange(of: model.completedBrainstormReceipt) { _, receipt in
            guard let receipt, let sessionID = model.session?.id else { return }
            brainstormModel.acceptCompletionReceipt(
                sessionID: sessionID,
                receipt: receipt,
                ownerAccountID: model.accountIdentity
            )
        }
        .onChange(of: brainstormModel.receipt) { previous, receipt in
            guard previous == nil, receipt != nil else { return }
            experienceMode = .brainstorm
        }
        .onDisappear {
            // Closing the window never hides active or pending capture. A
            // recording stops; an in-flight start is invalidated.
            model.windowWillHide()
        }
    }

    private var header: some View {
        HStack(spacing: 11) {
            ZStack(alignment: .leading) {
                WindowDragHandle()
                HStack(spacing: 11) {
                    CanonicalSixSentencesMark()
                        .frame(width: 29, height: 29)
                    VStack(alignment: .leading, spacing: 1) {
                        Text("SIXSENTENCES_")
                            .font(.system(size: 11, weight: .semibold, design: .monospaced))
                            .tracking(2.1)
                        Text("Live Companion · Preview")
                            .font(.system(size: 11))
                            .foregroundStyle(CompanionPalette.secondary)
                    }
                    Spacer(minLength: 0)
                }
                .allowsHitTesting(false)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            Button(action: openPaperCompanion) {
                Image(systemName: "doc.text.magnifyingglass")
                    .font(.system(size: 13, weight: .medium))
                    .frame(width: 28, height: 28)
                    .background(Circle().fill(Color.white.opacity(0.06)))
            }
            .buttonStyle(.plain)
            .help(model.copy("Lokales PDF öffnen", "Open local PDF"))
            Button { isExpanded.toggle() } label: {
                Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                    .font(.system(size: 12, weight: .semibold))
                    .frame(width: 28, height: 28)
                    .background(Circle().fill(Color.white.opacity(0.06)))
            }
            .buttonStyle(.plain)
            .help(isExpanded ? "Collapse" : "Expand")
            SettingsLink {
                Image(systemName: "gearshape")
                    .font(.system(size: 13, weight: .medium))
                    .frame(width: 28, height: 28)
                    .background(Circle().fill(Color.white.opacity(0.06)))
            }
            .buttonStyle(.plain)
            .help("Settings")
        }
        .padding(.horizontal, 16)
        .frame(height: 58)
    }

    private var compactBar: some View {
        HStack(spacing: 12) {
            if model.isConnected {
                StatusPill(model: model)
                Text(formatDuration(model.elapsedMS))
                    .font(.system(size: 16, weight: .medium, design: .monospaced))
                    .monospacedDigit()
                Spacer()
                Button {
                    model.isRecording ? model.stop() : startOrReset()
                } label: {
                    Image(systemName: model.isRecording ? "stop.fill" : "record.circle")
                        .font(.system(size: 13, weight: .semibold))
                        .frame(width: 34, height: 34)
                        .background(Circle().fill(model.isRecording ? CompanionPalette.recording : CompanionPalette.ink))
                        .foregroundStyle(model.isRecording ? Color.white : CompanionPalette.canvas)
                }
                .buttonStyle(.plain)
                .disabled(
                    model.isBusy
                        || (experienceMode == .brainstorm
                            && (brainstormModel.isSubmitting || brainstormModel.isRecovering))
                )
                Button(action: openConversationChat) {
                    Image(systemName: "bubble.left.and.text.bubble.right")
                        .font(.system(size: 13, weight: .medium))
                        .frame(width: 34, height: 34)
                        .background(Circle().fill(CompanionPalette.panelRaised))
                        .foregroundStyle(CompanionPalette.mint)
                }
                .buttonStyle(.plain)
                .help(model.copy("KI-Chat öffnen", "Open AI chat"))
                .accessibilityLabel(model.copy("KI-Chat öffnen", "Open AI chat"))
            } else {
                Label(
                    model.copy("Nicht verbunden", "Not connected"),
                    systemImage: "person.crop.circle.badge.exclamationmark"
                )
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(CompanionPalette.secondary)
                Spacer()
                Button {
                    isExpanded = true
                    model.connectThroughBrowser()
                } label: {
                    Label(model.copy("Verbinden", "Connect"), systemImage: "safari")
                        .font(.system(size: 11, weight: .semibold))
                        .padding(.horizontal, 12)
                        .frame(height: 34)
                        .background(Capsule().fill(CompanionPalette.ink))
                        .foregroundStyle(CompanionPalette.canvas)
                }
                .buttonStyle(.plain)
                .disabled(model.isConnecting)
            }
        }
        .padding(.horizontal, 16)
        .frame(height: 64)
    }

    private var onboardingCard: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(alignment: .center, spacing: 13) {
                ZStack {
                    RoundedRectangle(cornerRadius: 13, style: .continuous)
                        .fill(Color(red: 0.047, green: 0.114, blue: 0.098))
                        .stroke(CompanionPalette.mint.opacity(0.30))
                    CanonicalSixSentencesMark()
                        .frame(width: 35, height: 35)
                }
                .frame(width: 50, height: 50)
                VStack(alignment: .leading, spacing: 4) {
                    Text(model.copy("Live Companion verbinden", "Connect Live Companion"))
                        .font(.system(size: 18, weight: .semibold))
                    Text(connectionEyebrow)
                        .font(.system(size: 10, weight: .semibold, design: .monospaced))
                        .tracking(1.1)
                        .foregroundStyle(connectionColor)
                }
            }

            Text(
                model.copy(
                    "Melde dich sicher im Browser bei SixSentences an. Dein Passwort bleibt dort; der Companion erhält nur einen begrenzten Zugang für Live-Sitzungen.",
                    "Sign in to SixSentences securely in your browser. Your password stays there; the companion receives only limited access for live sessions."
                )
            )
            .font(.system(size: 13.5))
            .foregroundStyle(CompanionPalette.secondary)
            .fixedSize(horizontal: false, vertical: true)

            VStack(alignment: .leading, spacing: 10) {
                OnboardingPoint(
                    icon: "checkmark.shield",
                    text: model.copy(
                        "PKCE-S256 bindet die einmalige Rückgabe an diesen Mac.",
                        "PKCE S256 binds the one-time callback to this Mac."
                    )
                )
                OnboardingPoint(
                    icon: "key.fill",
                    text: model.copy(
                        "Der Companion-Zugang wird im macOS-Schlüsselbund gespeichert.",
                        "Companion access is stored in macOS Keychain."
                    )
                )
                OnboardingPoint(
                    icon: "mic.slash",
                    text: model.copy(
                        "Vor Verbindung und Einwilligung wird kein Audio erfasst.",
                        "No audio is captured before connection and consent."
                    )
                )
            }

            Button(action: model.connectThroughBrowser) {
                HStack(spacing: 9) {
                    if model.connectionState == .validating {
                        ProgressView()
                            .controlSize(.small)
                            .tint(CompanionPalette.canvas)
                    } else {
                        Image(systemName: "safari")
                    }
                    Text(connectionButtonTitle)
                }
                .font(.system(size: 14, weight: .semibold))
                .frame(maxWidth: .infinity)
                .frame(height: 42)
                .background(
                    RoundedRectangle(cornerRadius: 11, style: .continuous)
                        .fill(CompanionPalette.ink)
                )
                .foregroundStyle(CompanionPalette.canvas)
            }
            .buttonStyle(.plain)
            .disabled(model.connectionState == .validating)

            if model.hasCredential, !model.isConnected {
                Label(
                    model.copy(
                        "Ein gespeicherter Zugang wurde gefunden, konnte aber noch nicht validiert werden.",
                        "A stored credential was found but has not been validated yet."
                    ),
                    systemImage: "key.horizontal"
                )
                .font(.system(size: 11))
                .foregroundStyle(CompanionPalette.secondary)
            }
        }
        .padding(18)
        .background(cardBackground)
    }

    private var onboardingFooter: some View {
        HStack(spacing: 7) {
            Image(systemName: "lock.shield")
            Text(
                model.copy(
                    "Browser-Anmeldung · PKCE S256 · macOS-Schlüsselbund",
                    "Browser sign-in · PKCE S256 · macOS Keychain"
                )
            )
        }
        .font(.system(size: 10.5))
        .foregroundStyle(CompanionPalette.secondary)
        .frame(maxWidth: .infinity)
        .frame(height: 48)
    }

    private var connectionEyebrow: String {
        switch model.connectionState {
        case .disconnected: model.copy("ERSTE EINRICHTUNG", "FIRST-TIME SETUP")
        case .awaitingBrowser: model.copy("BROWSER GEÖFFNET", "BROWSER OPEN")
        case .validating: model.copy("VERBINDUNG WIRD GEPRÜFT", "VERIFYING CONNECTION")
        case .connected: model.copy("VERBUNDEN", "CONNECTED")
        case .failed: model.copy("VERBINDUNG ERFORDERLICH", "CONNECTION REQUIRED")
        }
    }

    private var connectionButtonTitle: String {
        switch model.connectionState {
        case .awaitingBrowser:
            model.copy("Browser erneut öffnen", "Open browser again")
        case .validating:
            model.copy("Verbindung wird geprüft …", "Verifying connection …")
        case .failed:
            model.copy("Erneut mit SixSentences verbinden", "Reconnect with SixSentences")
        default:
            model.copy("Mit SixSentences verbinden", "Connect with SixSentences")
        }
    }

    private var connectionColor: Color {
        switch model.connectionState {
        case .failed: CompanionPalette.recording
        case .awaitingBrowser, .validating: .orange
        default: CompanionPalette.mint
        }
    }

    private var experienceModeControl: some View {
        VStack(alignment: .leading, spacing: 9) {
            HStack {
                Text(model.copy("MODUS", "MODE"))
                    .font(.system(size: 10, weight: .semibold, design: .monospaced))
                    .tracking(1.2)
                    .foregroundStyle(CompanionPalette.mint)
                Spacer()
                if experienceMode == .brainstorm {
                    Label(
                        model.copy("Nur dein Mikrofon", "Your microphone only"),
                        systemImage: "mic.fill"
                    )
                    .font(.system(size: 10.5, weight: .medium))
                    .foregroundStyle(CompanionPalette.secondary)
                }
            }
            Picker(model.copy("Companion-Modus", "Companion mode"), selection: $experienceMode) {
                Label(model.copy("Gespräch", "Conversation"), systemImage: "person.2.wave.2")
                    .tag(CompanionExperienceMode.conversation)
                Label("Brainstorm", systemImage: "brain.head.profile")
                    .tag(CompanionExperienceMode.brainstorm)
            }
            .labelsHidden()
            .pickerStyle(.segmented)
            .disabled(isExperienceModeLocked)
            .accessibilityLabel(model.copy("Companion-Modus", "Companion mode"))
            Text(experienceMode == .brainstorm
                ? model.copy(
                    "Sprich frei. Lokale Spracherkennung macht daraus einen finalen Gedankenstrom; SixSentences strukturiert erst beim Stoppen.",
                    "Speak freely. Local speech recognition creates a final thought stream; SixSentences structures it only when you stop."
                )
                : model.copy(
                    "Transkribiert dich und andere Gesprächsteilnehmende nach sichtbarer Einwilligung.",
                    "Transcribes you and other participants after visible consent."
                ))
                .font(.system(size: 11.5))
                .foregroundStyle(CompanionPalette.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(14)
        .background(cardBackground)
    }

    private var recordingCard: some View {
        VStack(spacing: 13) {
            HStack {
                StatusPill(model: model)
                Spacer()
                Text(formatDuration(model.elapsedMS))
                    .font(.system(size: 23, weight: .medium, design: .monospaced))
                    .monospacedDigit()
            }
            TextField(
                experienceMode == .brainstorm
                    ? model.copy("Worum kreisen deine Gedanken?", "What are you thinking through?")
                    : model.copy("Titel der Unterhaltung", "Conversation title"),
                text: $model.sessionTitle
            )
                .textFieldStyle(.plain)
                .font(.system(size: 15, weight: .semibold))
                .disabled(model.isRecording || model.isBusy)
            TranscriptionLanguageControl(model: model)
            if experienceMode == .brainstorm {
                BrainstormProjectControl(model: model)
            }
            HStack(spacing: 8) {
                ChannelBadge(icon: "mic.fill", label: "You", active: model.isRecording)
                if experienceMode == .conversation {
                    ChannelBadge(icon: "waveform", label: "Others", active: model.isRecording)
                }
                Spacer()
                if model.queuedSegmentCount > 0 {
                    Label("\(model.queuedSegmentCount)", systemImage: "arrow.triangle.2.circlepath")
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(CompanionPalette.secondary)
                }
            }
            Button {
                model.isRecording ? model.stop() : startOrReset()
            } label: {
                HStack(spacing: 8) {
                    Image(systemName: model.isRecording ? "stop.fill" : "record.circle")
                    Text(model.isRecording
                        ? (experienceMode == .brainstorm
                            ? model.copy("Stoppen & strukturieren", "Stop & organize")
                            : model.copy("Stoppen & speichern", "Stop & save"))
                        : startButtonLabel)
                }
                .font(.system(size: 14, weight: .semibold))
                .frame(maxWidth: .infinity)
                .frame(height: 40)
                .background(
                    RoundedRectangle(cornerRadius: 11, style: .continuous)
                        .fill(model.isRecording ? CompanionPalette.recording : CompanionPalette.ink)
                )
                .foregroundStyle(model.isRecording ? Color.white : CompanionPalette.canvas)
            }
            .buttonStyle(.plain)
            .disabled(
                model.isBusy
                    || (experienceMode == .brainstorm
                        && (brainstormModel.isSubmitting || brainstormModel.isRecovering))
            )
        }
        .padding(14)
        .background(cardBackground)
    }

    private var transcriptCard: some View {
        VStack(alignment: .leading, spacing: 9) {
            Label(
                experienceMode == .brainstorm
                    ? model.copy("LIVE-GEDANKENSTROM", "LIVE THOUGHT STREAM")
                    : model.copy("LETZTES TRANSKRIPT", "LATEST TRANSCRIPT"),
                systemImage: experienceMode == .brainstorm ? "waveform.and.mic" : "quote.bubble"
            )
                .font(.system(size: 10, weight: .semibold, design: .monospaced))
                .tracking(1.2)
                .foregroundStyle(CompanionPalette.mint)
            Text(model.latestTranscript)
                .font(.system(size: 13.5))
                .foregroundStyle(CompanionPalette.ink)
                .lineLimit(4)
                .frame(maxWidth: .infinity, alignment: .topLeading)
        }
        .padding(14)
        .background(cardBackground)
    }

    private var askCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Label(
                    model.copy("UNTERHALTUNG FRAGEN", "ASK THE CONVERSATION"),
                    systemImage: "bubble.left.and.text.bubble.right"
                )
                .font(.system(size: 10, weight: .semibold, design: .monospaced))
                .tracking(1.2)
                .foregroundStyle(CompanionPalette.mint)
                Spacer()
                Button(action: openConversationChat) {
                    Label(model.copy("Öffnen", "Open"), systemImage: "macwindow.on.rectangle")
                        .font(.system(size: 10.5, weight: .semibold))
                        .foregroundStyle(CompanionPalette.secondary)
                }
                .buttonStyle(.plain)
                .help(model.copy("Chat in eigenem Fenster öffnen", "Open chat in its own window"))
                .accessibilityLabel(model.copy("Chatfenster öffnen", "Open chat window"))
            }
            HStack(spacing: 8) {
                TextField(model.copy("Was haben wir entschieden über …?", "What did we decide about…?"), text: $model.question)
                    .textFieldStyle(.plain)
                    .font(.system(size: 13))
                    .onSubmit { if model.canAsk { model.ask() } }
                    .disabled(!model.isRecording || model.isAsking)
                Button(action: model.ask) {
                    Group {
                        if model.isAsking {
                            ProgressView().controlSize(.small)
                        } else {
                            Image(systemName: "arrow.up")
                                .font(.system(size: 12, weight: .bold))
                        }
                    }
                    .frame(width: 28, height: 28)
                    .background(Circle().fill(model.canAsk ? CompanionPalette.mint : Color.white.opacity(0.08)))
                    .foregroundStyle(model.canAsk ? CompanionPalette.canvas : CompanionPalette.secondary)
                }
                .buttonStyle(.plain)
                .disabled(!model.canAsk)
                .accessibilityLabel(model.copy("Frage senden", "Send question"))
                .accessibilityHint(model.hasFinalTranscript
                    ? model.copy("Sendet die Frage mit dem bestätigten Transkriptstand.", "Sends the question using the confirmed transcript so far.")
                    : model.copy("Du kannst fragen, sobald der erste Satz sicher transkribiert ist.", "You can ask as soon as the first sentence is ready."))
            }
            .padding(.leading, 11)
            .padding(.trailing, 6)
            .frame(height: 40)
            .background(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .fill(Color.black.opacity(0.22))
                    .stroke(CompanionPalette.line)
            )
            if !model.answer.isEmpty {
                Text(model.answer)
                    .font(.system(size: 13))
                    .lineLimit(6)
                    .textSelection(.enabled)
                    .padding(.top, 2)
            }
            if !model.askErrorMessage.isEmpty {
                Label(model.askErrorMessage, systemImage: "exclamationmark.triangle")
                    .font(.system(size: 11))
                    .foregroundStyle(CompanionPalette.recording)
            } else if model.isRecording, !model.hasFinalTranscript {
                Text(model.copy(
                    "Sobald der erste Satz sicher transkribiert ist, kannst du fragen.",
                    "You can ask as soon as the first sentence is ready."
                ))
                    .font(.system(size: 11))
                    .foregroundStyle(CompanionPalette.secondary)
            } else if !model.isRecording {
                Text(model.copy("Der Chat ist während einer laufenden Sitzung verfügbar.", "Chat is available during a live session."))
                    .font(.system(size: 11))
                    .foregroundStyle(CompanionPalette.secondary)
            }
        }
        .padding(14)
        .background(cardBackground)
    }

    @ViewBuilder
    private var notices: some View {
        if !model.connectionNotice.isEmpty {
            NoticeView(
                text: model.connectionNotice,
                color: CompanionPalette.mint,
                icon: "checkmark.circle"
            )
        }
        if !model.warningMessage.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                NoticeView(text: model.warningMessage, color: .orange, icon: "wifi.slash")
                if model.interruptedSessionID != nil {
                    HStack {
                        Button(model.copy("Sichern & abschließen", "Save & complete")) {
                            model.completeInterruptedSession()
                        }
                        Button(model.copy("Abbrechen & lokal löschen", "Cancel & delete local"), role: .destructive) {
                            model.cancelInterruptedSession()
                        }
                    }
                    .font(.system(size: 11, weight: .semibold))
                }
            }
        }
        if !model.errorMessage.isEmpty {
            NoticeView(text: model.errorMessage, color: CompanionPalette.recording, icon: "exclamationmark.triangle")
        }
    }

    private var footer: some View {
        HStack {
            Label(model.copy("Nur finaler Text", "Final text only"), systemImage: "lock.shield")
                .font(.system(size: 11))
                .foregroundStyle(CompanionPalette.secondary)
            Spacer()
            Button(action: model.openInSixSentences) {
                HStack(spacing: 6) {
                    Text(model.copy("In SixSentences öffnen", "Open in SixSentences"))
                    Image(systemName: "arrow.up.right")
                }
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(CompanionPalette.mint)
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 16)
        .frame(height: 48)
    }

    private var cardBackground: some View {
        RoundedRectangle(cornerRadius: 15, style: .continuous)
            .fill(CompanionPalette.panel)
            .stroke(CompanionPalette.line)
    }

    private var startButtonLabel: String {
        switch model.state {
        case .completed, .failed:
            experienceMode == .brainstorm
                ? model.copy("Neuen Brain-Dump starten", "Start a new brain dump")
                : model.copy("Neue Sitzung", "New session")
        default:
            experienceMode == .brainstorm
                ? model.copy("Brain-Dump starten", "Start brain dump")
                : model.copy("Companion starten", "Start companion")
        }
    }

    private var isExperienceModeLocked: Bool {
        switch model.state {
        case .idle, .completed, .failed: false
        default: true
        }
    }

    private func startOrReset() {
        switch model.state {
        case .completed, .failed:
            model.resetAfterCompletion()
            if experienceMode == .brainstorm { brainstormModel.prepareForNewSession() }
            model.requestStart(mode: experienceMode)
        default:
            model.requestStart(mode: experienceMode)
        }
    }

    private func openConversationChat() {
        openWindow(id: "conversation-chat")
        NSApplication.shared.activate(ignoringOtherApps: true)
    }

    private func openPaperCompanion() {
        guard let url = PaperCompanionOpenPanel.choosePDF() else { return }
        paperModel.open(url)
        openWindow(id: "paper-companion")
        NSApplication.shared.activate(ignoringOtherApps: true)
    }

    private func consumePendingOpenRequests() {
        if let url = openRequests.pendingPaperURL {
            paperModel.open(url)
            openWindow(id: "paper-companion")
            openRequests.consumePaper(url)
            NSApplication.shared.activate(ignoringOtherApps: true)
        }
        if let url = openRequests.pendingPairingURL {
            model.handleIncomingURL(url)
            openRequests.consumePairing(url)
        }
    }

    private func formatDuration(_ milliseconds: Int64) -> String {
        let totalSeconds = max(0, milliseconds / 1_000)
        return String(format: "%02d:%02d", totalSeconds / 60, totalSeconds % 60)
    }
}

private struct CompanionChatWindow: View {
    @ObservedObject var model: CompanionViewModel

    var body: some View {
        VStack(spacing: 0) {
            chatHeader
            Divider().overlay(CompanionPalette.line)
            conversation
            Divider().overlay(CompanionPalette.line)
            composer
        }
        .frame(minWidth: 540, minHeight: 520)
        .background(CompanionPalette.canvas)
        .foregroundStyle(CompanionPalette.ink)
    }

    private var chatHeader: some View {
        HStack(spacing: 12) {
            ZStack {
                RoundedRectangle(cornerRadius: 11, style: .continuous)
                    .fill(CompanionPalette.panelRaised)
                Image(systemName: "bubble.left.and.text.bubble.right")
                    .font(.system(size: 17, weight: .medium))
                    .foregroundStyle(CompanionPalette.mint)
            }
            .frame(width: 40, height: 40)
            VStack(alignment: .leading, spacing: 3) {
                Text(model.copy("Conversation Chat", "Conversation Chat"))
                    .font(.system(size: 16, weight: .semibold))
                Text(model.session?.title ?? model.copy("Keine aktive Sitzung", "No active session"))
                    .font(.system(size: 11.5))
                    .foregroundStyle(CompanionPalette.secondary)
                    .lineLimit(1)
            }
            Spacer()
            StatusPill(model: model)
            Button(action: model.refreshAskHistory) {
                Image(systemName: "arrow.clockwise")
                    .font(.system(size: 12, weight: .semibold))
                    .frame(width: 30, height: 30)
                    .background(Circle().fill(Color.white.opacity(0.06)))
            }
            .buttonStyle(.plain)
            .help(model.copy("Verlauf aktualisieren", "Refresh history"))
            .accessibilityLabel(model.copy("Chatverlauf aktualisieren", "Refresh chat history"))
        }
        .padding(.horizontal, 18)
        .frame(height: 68)
    }

    private var conversation: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(spacing: 18) {
                    if model.askHistory.isEmpty, !model.isAsking {
                        VStack(spacing: 12) {
                            Image(systemName: "quote.bubble")
                                .font(.system(size: 28, weight: .light))
                                .foregroundStyle(CompanionPalette.mint)
                            Text(model.copy("Frag direkt aus dem Gespräch", "Ask directly from the conversation"))
                                .font(.system(size: 16, weight: .semibold))
                            Text(model.copy(
                                "Antworten stützen sich auf das bisher sicher transkribierte Gespräch und den verknüpften Projektkontext.",
                                "Answers use the conversation captured so far and linked project context."
                            ))
                            .font(.system(size: 12.5))
                            .foregroundStyle(CompanionPalette.secondary)
                            .multilineTextAlignment(.center)
                            .frame(maxWidth: 390)
                        }
                        .frame(maxWidth: .infinity, minHeight: 280)
                    }
                    ForEach(model.askHistory) { receipt in
                        AskConversationTurn(receipt: receipt, model: model)
                            .id(receipt.id)
                    }
                    if model.showsOptimisticPendingAsk {
                        PendingAskTurn(question: model.activeAskQuestion, model: model)
                            .id("pending-ask")
                    }
                }
                .padding(20)
            }
            .onChange(of: model.askHistory.count) { _, _ in
                scrollToLatest(proxy)
            }
            .onChange(of: model.isAsking) { _, _ in
                scrollToLatest(proxy)
            }
        }
    }

    private var composer: some View {
        VStack(alignment: .leading, spacing: 9) {
            if !model.askErrorMessage.isEmpty {
                Label(model.askErrorMessage, systemImage: "exclamationmark.triangle")
                    .font(.system(size: 11.5))
                    .foregroundStyle(CompanionPalette.recording)
            }
            HStack(alignment: .bottom, spacing: 10) {
                TextField(
                    model.copy("Frage zum laufenden Gespräch …", "Ask about the live conversation …"),
                    text: $model.question,
                    axis: .vertical
                )
                .textFieldStyle(.plain)
                .font(.system(size: 13.5))
                .lineLimit(1 ... 4)
                .onSubmit { if model.canAsk { model.ask() } }
                .disabled(!model.isRecording || model.isAsking)
                Button(action: model.ask) {
                    Group {
                        if model.isAsking {
                            ProgressView().controlSize(.small)
                        } else {
                            Image(systemName: "arrow.up")
                                .font(.system(size: 13, weight: .bold))
                        }
                    }
                    .frame(width: 34, height: 34)
                    .background(Circle().fill(model.canAsk ? CompanionPalette.mint : Color.white.opacity(0.08)))
                    .foregroundStyle(model.canAsk ? CompanionPalette.canvas : CompanionPalette.secondary)
                }
                .buttonStyle(.plain)
                .disabled(!model.canAsk)
                .accessibilityLabel(model.copy("Frage senden", "Send question"))
                .accessibilityHint(model.hasFinalTranscript
                    ? model.copy("Sendet die Frage mit dem bestätigten Transkriptstand.", "Sends the question using the confirmed transcript so far.")
                    : model.copy("Du kannst fragen, sobald der erste Satz sicher transkribiert ist.", "You can ask as soon as the first sentence is ready."))
            }
            .padding(.leading, 13)
            .padding(.trailing, 7)
            .padding(.vertical, 7)
            .background(
                RoundedRectangle(cornerRadius: 13, style: .continuous)
                    .fill(CompanionPalette.panel)
                    .stroke(CompanionPalette.line)
            )
            Text(model.isRecording
                ? (model.hasFinalTranscript
                    ? model.copy("Antworten enthalten überprüfbare Quellenbelege.", "Answers include verifiable source receipts.")
                    : model.copy("Du kannst fragen, sobald der erste Satz bereit ist.", "You can ask as soon as the first sentence is ready."))
                : model.copy("Starte eine Sitzung, um den Live-Chat zu nutzen.", "Start a session to use live chat."))
                .font(.system(size: 10.5))
                .foregroundStyle(CompanionPalette.secondary)
        }
        .padding(16)
    }

    private func scrollToLatest(_ proxy: ScrollViewProxy) {
        DispatchQueue.main.async {
            if model.showsOptimisticPendingAsk {
                proxy.scrollTo("pending-ask", anchor: .bottom)
            } else if let id = model.askHistory.last?.id {
                proxy.scrollTo(id, anchor: .bottom)
            }
        }
    }
}

private struct AskConversationTurn: View {
    let receipt: AskLiveSessionResponse
    @ObservedObject var model: CompanionViewModel

    var body: some View {
        VStack(spacing: 10) {
            HStack {
                Spacer(minLength: 80)
                Text(receipt.question)
                    .font(.system(size: 13.5))
                    .textSelection(.enabled)
                    .padding(.horizontal, 13)
                    .padding(.vertical, 10)
                    .background(
                        RoundedRectangle(cornerRadius: 14, style: .continuous)
                            .fill(CompanionPalette.mint.opacity(0.18))
                    )
            }
            HStack(alignment: .top, spacing: 10) {
                CanonicalSixSentencesMark()
                    .frame(width: 25, height: 25)
                    .padding(6)
                    .background(Circle().fill(CompanionPalette.panelRaised))
                VStack(alignment: .leading, spacing: 10) {
                    if receipt.status == "pending" {
                        HStack(spacing: 8) {
                            ProgressView().controlSize(.small)
                            Text(model.copy(
                                "Antwort wird vorbereitet …",
                                "Preparing answer …"
                            ))
                            .foregroundStyle(CompanionPalette.secondary)
                        }
                    } else if receipt.status == "failed" {
                        Label(
                            model.copy("Antwort fehlgeschlagen", "Answer failed"),
                            systemImage: "exclamationmark.triangle"
                        )
                        .foregroundStyle(CompanionPalette.recording)
                    } else {
                        Text(receipt.answer)
                            .textSelection(.enabled)
                    }
                    if !receipt.transcriptSources.isEmpty || !receipt.projectSources.isEmpty {
                        DisclosureGroup {
                            VStack(alignment: .leading, spacing: 8) {
                                ForEach(receipt.transcriptSources) { source in
                                    TranscriptReceiptView(source: source, model: model)
                                }
                                ForEach(Array(receipt.projectSources.enumerated()), id: \.offset) { _, source in
                                    ProjectReceiptView(source: source, model: model)
                                }
                            }
                            .padding(.top, 8)
                        } label: {
                            Label(
                                model.copy(
                                    "\(sourceCount) Beleg\(sourceCount == 1 ? "" : "e")",
                                    "\(sourceCount) source\(sourceCount == 1 ? "" : "s")"
                                ),
                                systemImage: "checkmark.shield"
                            )
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundStyle(CompanionPalette.mint)
                        }
                    }
                }
                .font(.system(size: 13.5))
                .padding(14)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(
                    RoundedRectangle(cornerRadius: 15, style: .continuous)
                        .fill(CompanionPalette.panel)
                        .stroke(CompanionPalette.line)
                )
            }
        }
    }

    private var sourceCount: Int {
        receipt.transcriptSources.count + receipt.projectSources.count
    }
}

private struct PendingAskTurn: View {
    let question: String
    @ObservedObject var model: CompanionViewModel

    var body: some View {
        VStack(spacing: 10) {
            HStack {
                Spacer(minLength: 80)
                Text(question)
                    .font(.system(size: 13.5))
                    .padding(.horizontal, 13)
                    .padding(.vertical, 10)
                    .background(
                        RoundedRectangle(cornerRadius: 14, style: .continuous)
                            .fill(CompanionPalette.mint.opacity(0.18))
                    )
            }
            HStack(spacing: 10) {
                ProgressView().controlSize(.small)
                Text(model.copy("Antwort wird vorbereitet …", "Preparing answer …"))
                    .font(.system(size: 12.5))
                    .foregroundStyle(CompanionPalette.secondary)
                Spacer()
            }
            .padding(14)
            .background(
                RoundedRectangle(cornerRadius: 15, style: .continuous)
                    .fill(CompanionPalette.panel)
                    .stroke(CompanionPalette.line)
            )
        }
    }
}

private struct TranscriptReceiptView: View {
    let source: TranscriptSourceReceipt
    @ObservedObject var model: CompanionViewModel

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack {
                Label(source.speaker, systemImage: "quote.bubble")
                Spacer()
                if let startMS = source.startMS {
                    Text(timestamp(startMS))
                }
            }
            .font(.system(size: 10.5, weight: .semibold))
            .foregroundStyle(CompanionPalette.mint)
            if let quote = source.quote, !quote.isEmpty {
                Text("“\(quote)”")
                    .font(.system(size: 11.5))
                    .foregroundStyle(CompanionPalette.secondary)
                    .textSelection(.enabled)
            }
        }
        .padding(10)
        .background(RoundedRectangle(cornerRadius: 10).fill(Color.black.opacity(0.18)))
    }

    private func timestamp(_ milliseconds: Int64) -> String {
        let seconds = max(0, milliseconds / 1_000)
        return String(format: "%02d:%02d", seconds / 60, seconds % 60)
    }
}

private struct ProjectReceiptView: View {
    let source: ProjectSourceReceipt
    @ObservedObject var model: CompanionViewModel

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Label(
                source.title ?? model.copy("Projektkontext", "Project context"),
                systemImage: "folder"
            )
            .font(.system(size: 10.5, weight: .semibold))
            .foregroundStyle(CompanionPalette.mint)
            if let locator = source.locator, !locator.isEmpty {
                Text(locator)
                    .font(.system(size: 10))
                    .foregroundStyle(CompanionPalette.secondary)
            }
            if let quote = source.quote, !quote.isEmpty {
                Text("“\(quote)”")
                    .font(.system(size: 11.5))
                    .foregroundStyle(CompanionPalette.secondary)
                    .textSelection(.enabled)
            }
        }
        .padding(10)
        .background(RoundedRectangle(cornerRadius: 10).fill(Color.black.opacity(0.18)))
    }
}

private struct ConsentView: View {
    @ObservedObject var model: CompanionViewModel
    @State private var participantsInformed = false

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack {
                Image(systemName: "person.2.badge.gearshape")
                    .font(.system(size: 24))
                    .foregroundStyle(CompanionPalette.mint)
            Text(model.copy("Bevor du startest", "Before you start"))
                    .font(.system(size: 20, weight: .semibold))
            }
            Text(model.copy("Informiere alle Teilnehmenden, dass SixSentences Mikrofon- und Systemaudio transkribiert. Das Overlay bleibt beim Bildschirmteilen sichtbar.", "Tell everyone in the conversation that SixSentences will transcribe microphone and system audio. The overlay stays visible during screen sharing."))
                .font(.system(size: 13.5))
                .foregroundStyle(CompanionPalette.secondary)
                .fixedSize(horizontal: false, vertical: true)
            VStack(alignment: .leading, spacing: 8) {
                Label(model.copy("Apple Speech läuft lokal auf diesem Mac", "Apple Speech runs locally on this Mac"), systemImage: "checkmark.shield")
                Label(
                    model.copy(
                        "Transkription: \(model.transcriptionLanguageLabel)",
                        "Transcription: \(model.transcriptionLanguageLabel)"
                    ),
                    systemImage: "character.bubble"
                )
                Label(model.copy("Nur finaler Text wird hochgeladen", "Only finalized text is uploaded"), systemImage: "text.badge.checkmark")
                Label(model.copy("Kein Audio, Video oder Screenshot wird gespeichert", "No audio, video, or screenshots are stored"), systemImage: "nosign")
            }
            .font(.system(size: 12.5))
            Toggle(model.copy("Ich habe alle Teilnehmenden informiert", "I have informed all participants"), isOn: $participantsInformed)
                .toggleStyle(.checkbox)
                .font(.system(size: 13, weight: .medium))
            HStack {
                Button(model.copy("Abbrechen", "Cancel"), action: model.declineConsent)
                    .buttonStyle(.plain)
                    .foregroundStyle(CompanionPalette.secondary)
                Spacer()
                Button(model.copy("Sichtbare Aufnahme starten", "Start visible recording"), action: model.confirmConsent)
                    .buttonStyle(.borderedProminent)
                    .tint(CompanionPalette.mint)
                    .foregroundStyle(CompanionPalette.canvas)
                    .disabled(!participantsInformed)
            }
        }
        .padding(24)
        .frame(width: 430)
        .background(CompanionPalette.canvas)
        .foregroundStyle(CompanionPalette.ink)
        .onAppear { participantsInformed = false }
        .onDisappear { participantsInformed = false }
    }
}

private struct CompanionSettingsView: View {
    @ObservedObject var model: CompanionViewModel
    @ObservedObject var paperModel: PaperCompanionViewModel
    @ObservedObject var brainstormModel: BrainstormViewModel
    @ObservedObject var updater: CompanionUpdaterController
    @ObservedObject private var preferences: CompanionPreferences
    @State private var tokenStatus = ""

    init(
        model: CompanionViewModel,
        paperModel: PaperCompanionViewModel,
        brainstormModel: BrainstormViewModel,
        updater: CompanionUpdaterController
    ) {
        self.model = model
        self.paperModel = paperModel
        self.brainstormModel = brainstormModel
        self.updater = updater
        preferences = model.preferences
    }

    var body: some View {
        Form {
            Section("Connection") {
                LabeledContent("Status") {
                    Label(
                        model.isConnected ? "Connected" : "Not connected",
                        systemImage: model.isConnected ? "checkmark.circle.fill" : "person.crop.circle.badge.exclamationmark"
                    )
                    .foregroundStyle(model.isConnected ? CompanionPalette.mint : .secondary)
                }
                Button(model.isConnected ? "Reconnect in browser" : "Connect with SixSentences in browser") {
                    model.connectThroughBrowser()
                }
                .buttonStyle(.borderedProminent)
                .disabled(!model.canChangeAccount)
                if model.hasCredential {
                    Button("Disconnect this Mac", role: .destructive) {
                        Task {
                            do {
                                try await model.removeToken()
                                brainstormModel.scrubPrivateState()
                                paperModel.scrubPrivateState()
                                tokenStatus = "This Mac is disconnected and its local recovery data was deleted."
                            } catch { tokenStatus = error.localizedDescription }
                        }
                    }
                    .disabled(!model.canChangeAccount)
                }
                if !tokenStatus.isEmpty {
                    Text(tokenStatus).font(.caption).foregroundStyle(.secondary)
                }
            }
            Section("Session defaults") {
                Picker(
                    model.copy("Transkriptionssprache", "Transcription language"),
                    selection: Binding(
                        get: { preferences.language },
                        set: model.selectTranscriptionLanguage
                    )
                ) {
                    Text(model.copy("Automatisch", "Automatic")).tag("auto")
                    Text("Deutsch").tag("de")
                    Text("English").tag("en")
                }
                .disabled(model.isTranscriptionLanguageLocked)
                if model.isTranscriptionLanguageLocked {
                    Text(model.copy(
                        "Die Sprache ist für die laufende Sitzung fixiert.",
                        "Language is fixed for the active session."
                    ))
                    .font(.caption)
                    .foregroundStyle(.secondary)
                }
                Picker(
                    model.copy("Standardprojekt für Gespräche", "Default conversation project"),
                    selection: $preferences.projectID
                ) {
                    Text(model.copy("Ohne Projekt", "No project")).tag(Int?.none)
                    ForEach(model.config?.projects ?? []) { project in
                        Text(project.name).tag(Optional(project.id))
                    }
                }
                Toggle("Open the web session automatically", isOn: $preferences.autoOpenWebSession)
            }
            Section("Privacy") {
                Text("The app captures audio only while the red recording indicator is visible. Apple Speech transcribes locally; only finalized text segments are uploaded. Raw audio, video frames, and screenshots are never sent or stored.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Section("Legal") {
                Button(model.copy("Hinweise zu Drittanbietern …", "Third-party notices…")) {
                    CompanionLegalNotices.open()
                }
                .disabled(CompanionLegalNotices.bundledURL == nil)
                .accessibilityHint(model.copy(
                    "Öffnet die mit der App ausgelieferten Lizenzhinweise.",
                    "Opens the license notices bundled with the app."
                ))
            }
            Section("Updates") {
                LabeledContent("Version") {
                    Text(CompanionApplicationVersion.displayVersion)
                        .monospacedDigit()
                }
                Toggle(
                    "Automatically check for updates",
                    isOn: Binding(
                        get: { updater.automaticallyChecksForUpdates },
                        set: updater.setAutomaticallyChecksForUpdates
                    )
                )
                .disabled(!updater.isConfigured)
                Button("Check for Updates…", action: updater.checkForUpdates)
                    .disabled(!updater.canCheckForUpdates)
                if !updater.configurationNotice.isEmpty {
                    Text(updater.configurationNotice)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
        .formStyle(.grouped)
        .padding(8)
    }
}

private struct BrainstormProjectControl: View {
    @ObservedObject var model: CompanionViewModel

    var body: some View {
        HStack(spacing: 12) {
            Label(
                BrainstormProjectCopy.label(usesGerman: model.usesGerman),
                systemImage: "folder"
            )
            .font(.system(size: 11, weight: .medium))
            .foregroundStyle(CompanionPalette.secondary)
            Spacer(minLength: 4)
            Picker(
                BrainstormProjectCopy.label(usesGerman: model.usesGerman),
                selection: Binding(
                    get: { model.brainstormProjectID },
                    set: model.selectBrainstormProject
                )
            ) {
                Text(BrainstormProjectCopy.noProject(usesGerman: model.usesGerman)).tag(Int?.none)
                ForEach(model.config?.projects ?? []) { project in
                    Text(project.name).tag(Optional(project.id))
                }
            }
            .labelsHidden()
            .pickerStyle(.menu)
            .frame(maxWidth: 190, alignment: .trailing)
            .disabled(model.isTranscriptionLanguageLocked)
            .accessibilityLabel(BrainstormProjectCopy.label(usesGerman: model.usesGerman))
            .help(BrainstormProjectCopy.help(usesGerman: model.usesGerman))
        }
    }
}

private struct TranscriptionLanguageControl: View {
    @ObservedObject var model: CompanionViewModel
    @ObservedObject private var preferences: CompanionPreferences

    init(model: CompanionViewModel) {
        self.model = model
        preferences = model.preferences
    }

    var body: some View {
        HStack(spacing: 12) {
            Label(model.copy("Transkription", "Transcription"), systemImage: "character.bubble")
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(CompanionPalette.secondary)
            Spacer(minLength: 4)
            Picker(
                model.copy("Transkriptionssprache", "Transcription language"),
                selection: Binding(
                    get: { preferences.language },
                    set: model.selectTranscriptionLanguage
                )
            ) {
                Text(model.copy("Auto", "Auto"))
                    .tag("auto")
                    .accessibilityLabel(model.copy("Automatisch", "Automatic"))
                Text("DE")
                    .tag("de")
                    .accessibilityLabel("Deutsch")
                Text("EN")
                    .tag("en")
                    .accessibilityLabel("English")
            }
            .labelsHidden()
            .pickerStyle(.segmented)
            .frame(width: 174)
            .disabled(model.isTranscriptionLanguageLocked)
            .accessibilityLabel(model.copy("Transkriptionssprache", "Transcription language"))
            .help(model.copy(
                "Gilt für die nächste Sitzung und wird während der Aufnahme fixiert.",
                "Applies to the next session and is locked while recording."
            ))
        }
    }
}

private struct CompanionMenu: View {
    @ObservedObject var model: CompanionViewModel
    @ObservedObject var paperModel: PaperCompanionViewModel
    @ObservedObject var updater: CompanionUpdaterController
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        Text(model.statusLabel)
        if !model.isConnected {
            Button(model.copy("Mit SixSentences verbinden", "Connect with SixSentences")) {
                showCompanion()
                model.connectThroughBrowser()
            }
            .disabled(model.connectionState == .validating)
        } else if model.isRecording {
            Button("Stop & save", action: model.stop)
        } else {
            Button("Start companion") {
                showCompanion()
                model.requestStart()
            }
        }
        Divider()
        Button("Show Companion", action: showCompanion)
        Button(model.copy("Lokales PDF öffnen …", "Open local PDF…")) {
            guard let url = PaperCompanionOpenPanel.choosePDF() else { return }
            paperModel.open(url)
            openWindow(id: "paper-companion")
            NSApplication.shared.activate(ignoringOtherApps: true)
        }
        if model.isConnected {
            Button(model.copy("Conversation Chat öffnen", "Open conversation chat")) {
                openWindow(id: "conversation-chat")
                NSApplication.shared.activate(ignoringOtherApps: true)
            }
        }
        SettingsLink { Text("Settings…") }
        Button(model.copy("Hinweise zu Drittanbietern …", "Third-party notices…")) {
            CompanionLegalNotices.open()
        }
        .disabled(CompanionLegalNotices.bundledURL == nil)
        Button("Check for Updates…", action: updater.checkForUpdates)
            .disabled(!updater.canCheckForUpdates)
        Text(CompanionApplicationVersion.displayVersion)
        Divider()
        Button("Quit SixSentences Companion") { NSApplication.shared.terminate(nil) }
    }

    private func showCompanion() {
        openWindow(id: "companion")
        WindowRegistry.showAndActivate()
    }
}

private struct WindowConfigurator: NSViewRepresentable {
    func makeNSView(context: Context) -> NSView {
        let view = NSView()
        DispatchQueue.main.async { configure(view.window) }
        return view
    }

    func updateNSView(_ nsView: NSView, context: Context) {
        DispatchQueue.main.async { configure(nsView.window) }
    }

    private func configure(_ window: NSWindow?) {
        guard let window else { return }
        window.level = .floating
        window.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        window.isMovable = true
        window.isMovableByWindowBackground = true
        window.hidesOnDeactivate = false
        window.isExcludedFromWindowsMenu = false
        window.isReleasedWhenClosed = false
        window.titlebarAppearsTransparent = true
        if let contentView = window.contentView {
            let target = contentView.fittingSize
            if target.width > 0, target.height > 0 {
                window.setContentSize(target)
            }
        }
        WindowRegistry.register(window)
        // Intentionally do not set NSWindow.sharingType: the visible overlay
        // remains visible in screen shares as an honest recording indicator.
    }
}

private struct ChatWindowConfigurator: NSViewRepresentable {
    func makeNSView(context: Context) -> NSView {
        let view = NSView()
        DispatchQueue.main.async { configure(view.window) }
        return view
    }

    func updateNSView(_ nsView: NSView, context: Context) {
        DispatchQueue.main.async { configure(nsView.window) }
    }

    private func configure(_ window: NSWindow?) {
        guard let window else { return }
        window.level = .floating
        window.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        window.isMovable = true
        window.hidesOnDeactivate = false
        window.isReleasedWhenClosed = false
        window.isRestorable = false
        window.restorationClass = nil
        window.minSize = NSSize(width: 540, height: 520)
    }
}

private struct WindowDragHandle: NSViewRepresentable {
    func makeNSView(context: Context) -> NSView {
        CompanionWindowDragView()
    }

    func updateNSView(_ nsView: NSView, context: Context) {}
}

private final class CompanionWindowDragView: NSView {
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool {
        true
    }

    override func mouseDown(with event: NSEvent) {
        window?.performDrag(with: event)
    }
}

@MainActor
private enum WindowRegistry {
    static var window: NSWindow?
    private static var observedWindow: ObjectIdentifier?
    private static var observerTokens: [NSObjectProtocol] = []
    private static let savedOriginXKey = "companion.window.origin.x"
    private static let savedOriginYKey = "companion.window.origin.y"

    static func register(_ window: NSWindow) {
        self.window = window
        let identifier = ObjectIdentifier(window)
        guard observedWindow != identifier else { return }
        observerTokens.forEach(NotificationCenter.default.removeObserver)
        observerTokens.removeAll()
        observedWindow = identifier
        restoreOrPlace(window)
        let center = NotificationCenter.default
        observerTokens.append(
            center.addObserver(
                forName: NSWindow.didMoveNotification,
                object: window,
                queue: .main
            ) { _ in
                Task { @MainActor in persistOrigin(window.frame.origin) }
            }
        )
        observerTokens.append(
            center.addObserver(
                forName: NSWindow.didChangeScreenNotification,
                object: window,
                queue: .main
            ) { _ in
                Task { @MainActor in keepVisible(window) }
            }
        )
        observerTokens.append(
            center.addObserver(
                forName: NSWindow.didResizeNotification,
                object: window,
                queue: .main
            ) { _ in
                Task { @MainActor in keepVisible(window) }
            }
        )
        observerTokens.append(
            center.addObserver(
                forName: NSApplication.didChangeScreenParametersNotification,
                object: nil,
                queue: .main
            ) { _ in
                Task { @MainActor in
                    if let registered = self.window { keepVisible(registered) }
                }
            }
        )
    }

    static func showAndActivate() {
        NSApplication.shared.setActivationPolicy(.regular)
        NSApplication.shared.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
        if let window { keepVisible(window) }
    }

    static func restoreOrPlace(_ window: NSWindow) {
        let defaults = UserDefaults.standard
        if defaults.object(forKey: savedOriginXKey) != nil,
           defaults.object(forKey: savedOriginYKey) != nil {
            let origin = CGPoint(
                x: defaults.double(forKey: savedOriginXKey),
                y: defaults.double(forKey: savedOriginYKey)
            )
            window.setFrameOrigin(origin)
            keepVisible(window)
            return
        }
        anchorAtTopCenter(window)
    }

    static func keepVisible(_ window: NSWindow) {
        let screen = NSScreen.screens.first { $0.frame.intersects(window.frame) }
            ?? window.screen
            ?? NSScreen.main
            ?? NSScreen.screens.first
        guard let visibleFrame = screen?.visibleFrame else { return }
        let origin = CompanionWindowBehavior.constrainedOrigin(
            window.frame.origin,
            visibleFrame: visibleFrame,
            windowSize: window.frame.size
        )
        if window.frame.origin != origin {
            window.setFrameOrigin(origin)
        }
        persistOrigin(origin)
    }

    static func anchorAtTopCenter(_ window: NSWindow) {
        let screen = window.screen ?? NSScreen.main ?? NSScreen.screens.first
        guard let visibleFrame = screen?.visibleFrame else { return }
        let origin = CompanionWindowBehavior.anchoredOrigin(
            visibleFrame: visibleFrame,
            windowSize: window.frame.size
        )
        window.setFrameOrigin(origin)
        persistOrigin(origin)
    }

    static func persistOrigin(_ origin: CGPoint) {
        let defaults = UserDefaults.standard
        defaults.set(origin.x, forKey: savedOriginXKey)
        defaults.set(origin.y, forKey: savedOriginYKey)
    }
}

private struct StatusPill: View {
    @ObservedObject var model: CompanionViewModel

    var body: some View {
        HStack(spacing: 7) {
            Circle()
                .fill(model.isRecording ? CompanionPalette.recording : CompanionPalette.mint)
                .frame(width: 8, height: 8)
                .shadow(color: model.isRecording ? CompanionPalette.recording.opacity(0.65) : .clear, radius: 5)
            Text(model.statusLabel)
                .font(.system(size: 10, weight: .bold, design: .monospaced))
                .tracking(1)
        }
        .padding(.horizontal, 10)
        .frame(height: 27)
        .background(Capsule().fill(Color.white.opacity(0.06)).stroke(CompanionPalette.line))
    }
}

private struct ChannelBadge: View {
    let icon: String
    let label: String
    let active: Bool

    var body: some View {
        Label(label, systemImage: icon)
            .font(.system(size: 10, weight: .medium))
            .foregroundStyle(active ? CompanionPalette.mint : CompanionPalette.secondary)
            .padding(.horizontal, 8)
            .frame(height: 25)
            .background(Capsule().fill(Color.white.opacity(0.05)))
    }
}

private struct NoticeView: View {
    let text: String
    let color: Color
    let icon: String

    var body: some View {
        Label(text, systemImage: icon)
            .font(.system(size: 11.5))
            .foregroundStyle(color)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(10)
            .background(RoundedRectangle(cornerRadius: 10).fill(color.opacity(0.09)))
    }
}

private struct OnboardingPoint: View {
    let icon: String
    let text: String

    var body: some View {
        HStack(alignment: .top, spacing: 9) {
            Image(systemName: icon)
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(CompanionPalette.mint)
                .frame(width: 16)
            Text(text)
                .font(.system(size: 12))
                .foregroundStyle(CompanionPalette.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

/// Exact native rendering of `src/components/brand/six-mark.tsx` and
/// `src/app/icon.svg`: the six canonical bar rectangles plus cursor.
struct CanonicalSixSentencesMark: View {
    var body: some View {
        Canvas { context, size in
            let scale = min(size.width, size.height) / 120
            let xOffset = (size.width - 120 * scale) / 2
            let yOffset = (size.height - 120 * scale) / 2
            for rect in SixSentencesBrand.markRects {
                let scaled = CGRect(
                    x: xOffset + rect.origin.x * scale,
                    y: yOffset + rect.origin.y * scale,
                    width: rect.width * scale,
                    height: rect.height * scale
                )
                let radius = min(scaled.height / 2, 5.25 * scale)
                context.fill(
                    Path(roundedRect: scaled, cornerRadius: radius),
                    with: .color(CompanionPalette.ink)
                )
            }
        }
        .accessibilityLabel("SixSentences")
    }
}
