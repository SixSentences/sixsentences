import AppKit
import PDFKit
import SwiftUI
import UniformTypeIdentifiers

@MainActor
final class CompanionOpenRequestBroker: ObservableObject {
    static let shared = CompanionOpenRequestBroker()

    @Published private(set) var pendingPaperURL: URL?
    @Published private(set) var pendingPairingURL: URL?

    func receive(_ url: URL) {
        if url.isFileURL, url.pathExtension.caseInsensitiveCompare("pdf") == .orderedSame {
            pendingPaperURL = url
        } else if url.scheme?.caseInsensitiveCompare("sixsentences") == .orderedSame {
            pendingPairingURL = url
        }
    }

    func consumePaper(_ url: URL) {
        if pendingPaperURL == url { pendingPaperURL = nil }
    }

    func consumePairing(_ url: URL) {
        if pendingPairingURL == url { pendingPairingURL = nil }
    }
}

enum PaperCompanionOpenPanel {
    @MainActor
    static func choosePDF() -> URL? {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.pdf]
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.resolvesAliases = true
        panel.title = "Open a paper in SixSentences"
        panel.prompt = "Open PDF"
        return panel.runModal() == .OK ? panel.url : nil
    }
}

private enum PaperPalette {
    static let canvas = Color(red: 0.045, green: 0.055, blue: 0.052)
    static let panel = Color(red: 0.075, green: 0.09, blue: 0.085)
    static let raised = Color(red: 0.105, green: 0.125, blue: 0.116)
    static let line = Color.white.opacity(0.10)
    static let mint = Color(red: 0.48, green: 0.75, blue: 0.67)
    static let ink = Color(red: 0.95, green: 0.96, blue: 0.94)
    static let secondary = Color.white.opacity(0.60)
    static let danger = Color(red: 0.96, green: 0.29, blue: 0.28)
}

struct PaperCompanionView: View {
    @ObservedObject var model: PaperCompanionViewModel
    @State private var isDropTargeted = false

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider().overlay(PaperPalette.line)
            if let document = model.document {
                HSplitView {
                    PaperPDFView(document: document) { selection, error in
                        model.updateSelection(selection, error: error)
                    }
                    .frame(minWidth: 470, idealWidth: 700)
                    chatPane
                        .frame(minWidth: 330, idealWidth: 390, maxWidth: 500)
                }
            } else {
                emptyState
            }
        }
        .frame(minWidth: 880, minHeight: 620)
        .background(PaperPalette.canvas)
        .foregroundStyle(PaperPalette.ink)
        .dropDestination(for: URL.self) { urls, _ in
            guard let url = urls.first(where: { $0.isFileURL }) else { return false }
            model.open(url)
            return true
        } isTargeted: { isDropTargeted = $0 }
    }

    private var header: some View {
        HStack(spacing: 12) {
            CanonicalSixSentencesMark()
                .frame(width: 31, height: 31)
            VStack(alignment: .leading, spacing: 2) {
                Text("SIXSENTENCES_")
                    .font(.system(size: 11, weight: .semibold, design: .monospaced))
                    .tracking(2)
                Text("Paper Companion")
                    .font(.system(size: 11.5))
                    .foregroundStyle(PaperPalette.secondary)
            }
            Divider().frame(height: 30).overlay(PaperPalette.line)
            VStack(alignment: .leading, spacing: 2) {
                Text(model.displayTitle)
                    .font(.system(size: 14, weight: .semibold))
                    .lineLimit(1)
                HStack(spacing: 6) {
                    Circle()
                        .fill(model.workspaceState == .failed ? PaperPalette.danger : PaperPalette.mint)
                        .frame(width: 6, height: 6)
                    Text(model.statusLabel)
                        .font(.system(size: 10.5, weight: .medium))
                        .foregroundStyle(PaperPalette.secondary)
                }
            }
            Spacer()
            if model.isLoading {
                ProgressView().controlSize(.small)
            }
            Button(action: choosePDF) {
                Label(model.copy("Anderes PDF", "Open another PDF"), systemImage: "doc.badge.plus")
                    .font(.system(size: 11.5, weight: .semibold))
                    .padding(.horizontal, 12)
                    .frame(height: 32)
                    .background(Capsule().fill(PaperPalette.raised))
            }
            .buttonStyle(.plain)
            if model.summary != nil {
                Button(action: model.openInSixSentences) {
                    Image(systemName: "arrow.up.right")
                        .font(.system(size: 12, weight: .semibold))
                        .frame(width: 32, height: 32)
                        .background(Circle().fill(PaperPalette.raised))
                }
                .buttonStyle(.plain)
                .help(model.copy("In SixSentences öffnen", "Open in SixSentences"))
            }
        }
        .padding(.horizontal, 18)
        .frame(height: 64)
        .background(PaperPalette.canvas)
    }

    private var emptyState: some View {
        VStack(spacing: 18) {
            ZStack {
                RoundedRectangle(cornerRadius: 24, style: .continuous)
                    .fill(PaperPalette.raised)
                    .stroke(isDropTargeted ? PaperPalette.mint : PaperPalette.line, lineWidth: 1.5)
                Image(systemName: "doc.text.magnifyingglass")
                    .font(.system(size: 44, weight: .light))
                    .foregroundStyle(PaperPalette.mint)
            }
            .frame(width: 104, height: 104)
            Text(model.copy("Arbeite direkt mit einem lokalen Paper", "Work directly with a local paper"))
                .font(.system(size: 23, weight: .semibold))
            Text(model.copy(
                "Öffne oder ziehe bewusst ein PDF hierher. Markiere anschließend eine Stelle und stelle eine geerdete Frage. SixSentences überwacht keine Ordner und speichert keinen lokalen Dateipfad.",
                "Explicitly open or drop a PDF here. Then select a passage and ask a grounded question. SixSentences does not watch folders or retain the local file path."
            ))
            .font(.system(size: 13.5))
            .foregroundStyle(PaperPalette.secondary)
            .multilineTextAlignment(.center)
            .frame(maxWidth: 540)
            Button(action: choosePDF) {
                Label(model.copy("PDF öffnen", "Open PDF"), systemImage: "folder")
                    .font(.system(size: 14, weight: .semibold))
                    .padding(.horizontal, 20)
                    .frame(height: 42)
                    .background(Capsule().fill(PaperPalette.ink))
                    .foregroundStyle(PaperPalette.canvas)
            }
            .buttonStyle(.plain)
            if !model.errorMessage.isEmpty {
                Label(model.errorMessage, systemImage: "exclamationmark.triangle")
                    .font(.system(size: 12))
                    .foregroundStyle(PaperPalette.danger)
                if model.workspaceState == .failed, model.canRetryUpload {
                    Button(model.copy("Erneut versuchen", "Try again"), action: model.retryUpload)
                        .buttonStyle(.link)
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(40)
        .overlay {
            if isDropTargeted {
                RoundedRectangle(cornerRadius: 24, style: .continuous)
                    .strokeBorder(PaperPalette.mint, style: StrokeStyle(lineWidth: 2, dash: [8]))
                    .padding(24)
                    .allowsHitTesting(false)
            }
        }
    }

    private var chatPane: some View {
        VStack(spacing: 0) {
            HStack {
                Label(model.copy("PAPER CHAT", "PAPER CHAT"), systemImage: "sparkles")
                    .font(.system(size: 10.5, weight: .semibold, design: .monospaced))
                    .tracking(1.25)
                    .foregroundStyle(PaperPalette.mint)
                Spacer()
                if model.isAsking {
                    Button(model.copy("Stoppen", "Stop"), action: model.stopAnswer)
                        .font(.system(size: 10.5, weight: .semibold))
                        .buttonStyle(.plain)
                        .foregroundStyle(PaperPalette.danger)
                }
            }
            .padding(.horizontal, 16)
            .frame(height: 48)
            Divider().overlay(PaperPalette.line)

            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(spacing: 14) {
                        if model.history.isEmpty, model.streamingAnswer.isEmpty, !model.isAsking {
                            VStack(spacing: 10) {
                                Image(systemName: "text.quote")
                                    .font(.system(size: 25, weight: .light))
                                    .foregroundStyle(PaperPalette.mint)
                                Text(model.copy(
                                    "Frag das ganze Paper oder markiere zuerst eine Stelle.",
                                    "Ask about the whole paper or select a passage first."
                                ))
                                .font(.system(size: 12.5))
                                .foregroundStyle(PaperPalette.secondary)
                                .multilineTextAlignment(.center)
                            }
                            .frame(maxWidth: .infinity, minHeight: 190)
                        }
                        ForEach(model.history) { message in
                            PaperChatBubble(message: message)
                        }
                        if !model.pendingQuestion.isEmpty {
                            PaperQuestionBubble(text: model.pendingQuestion)
                        }
                        if model.isAsking || !model.streamingAnswer.isEmpty {
                            PaperAnswerBubble(
                                text: model.streamingAnswer,
                                activity: model.activityLabel,
                                isLoading: model.isAsking
                            )
                            .id("live-answer")
                        }
                    }
                    .padding(16)
                }
                .onChange(of: model.streamingAnswer) { _, _ in
                    proxy.scrollTo("live-answer", anchor: .bottom)
                }
            }

            Divider().overlay(PaperPalette.line)
            composer
        }
        .background(PaperPalette.canvas)
    }

    private var composer: some View {
        VStack(alignment: .leading, spacing: 9) {
            if let selection = model.selection {
                HStack(alignment: .top, spacing: 8) {
                    Image(systemName: "selection.pin.in.out")
                        .foregroundStyle(PaperPalette.mint)
                    VStack(alignment: .leading, spacing: 3) {
                        Text(model.copy("MARKIERUNG · SEITE \(selection.page)", "SELECTION · PAGE \(selection.page)"))
                            .font(.system(size: 9.5, weight: .semibold, design: .monospaced))
                            .tracking(0.8)
                            .foregroundStyle(PaperPalette.mint)
                        Text("“\(selection.quote)”")
                            .font(.system(size: 11.5))
                            .foregroundStyle(PaperPalette.secondary)
                            .lineLimit(3)
                    }
                    Spacer()
                    Button {
                        model.updateSelection(nil)
                    } label: {
                        Image(systemName: "xmark")
                    }
                    .buttonStyle(.plain)
                }
                .padding(10)
                .background(RoundedRectangle(cornerRadius: 11).fill(PaperPalette.raised))
            } else if !model.selectionMessage.isEmpty {
                Label(model.selectionMessage, systemImage: "exclamationmark.triangle")
                    .font(.system(size: 10.5))
                    .foregroundStyle(.orange)
            }

            if !model.errorMessage.isEmpty {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Label(model.errorMessage, systemImage: "exclamationmark.triangle")
                        .font(.system(size: 11))
                        .foregroundStyle(PaperPalette.danger)
                    Spacer(minLength: 8)
                    if model.workspaceState == .failed, model.canRetryUpload {
                        Button(model.copy("Erneut versuchen", "Try again"), action: model.retryUpload)
                            .font(.system(size: 10.5, weight: .semibold))
                            .buttonStyle(.plain)
                            .foregroundStyle(PaperPalette.mint)
                    } else if model.activeTurnID != nil, !model.isAsking {
                        Button(model.copy("Antwort fortsetzen", "Resume answer"), action: model.resumeActiveTurn)
                            .font(.system(size: 10.5, weight: .semibold))
                            .buttonStyle(.plain)
                            .foregroundStyle(PaperPalette.mint)
                    }
                }
            }

            HStack(alignment: .bottom, spacing: 8) {
                TextField(
                    model.copy("Frage zu diesem Paper …", "Ask about this paper…"),
                    text: $model.question,
                    axis: .vertical
                )
                .textFieldStyle(.plain)
                .font(.system(size: 13))
                .lineLimit(1 ... 4)
                .onSubmit { if model.canAsk { model.ask() } }
                .disabled(model.isAsking || model.summary == nil)
                Button(action: model.ask) {
                    Group {
                        if model.isAsking {
                            ProgressView().controlSize(.small)
                        } else {
                            Image(systemName: "arrow.up")
                                .font(.system(size: 12, weight: .bold))
                        }
                    }
                    .frame(width: 32, height: 32)
                    .background(Circle().fill(model.canAsk ? PaperPalette.mint : Color.white.opacity(0.08)))
                    .foregroundStyle(model.canAsk ? PaperPalette.canvas : PaperPalette.secondary)
                }
                .buttonStyle(.plain)
                .disabled(!model.canAsk)
            }
            .padding(.leading, 12)
            .padding(.trailing, 7)
            .padding(.vertical, 7)
            .background(
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .fill(PaperPalette.panel)
                    .stroke(PaperPalette.line)
            )
            Text(model.copy(
                "Die PDF wird nur nach diesem bewussten Öffnen verarbeitet.",
                "The PDF is processed only after this explicit open action."
            ))
            .font(.system(size: 9.5))
            .foregroundStyle(PaperPalette.secondary)
        }
        .padding(14)
    }

    private func choosePDF() {
        guard let url = PaperCompanionOpenPanel.choosePDF() else { return }
        model.open(url)
    }
}

private struct PaperPDFView: NSViewRepresentable {
    let document: PDFDocument
    let onSelection: @MainActor (PaperChatSelection?, Error?) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(onSelection: onSelection) }

    func makeNSView(context: Context) -> PDFView {
        let view = PDFView()
        view.autoScales = true
        view.displayMode = .singlePageContinuous
        view.displayDirection = .vertical
        view.displaysPageBreaks = true
        view.backgroundColor = NSColor(red: 0.045, green: 0.055, blue: 0.052, alpha: 1)
        view.document = document
        context.coordinator.observe(view)
        return view
    }

    func updateNSView(_ view: PDFView, context: Context) {
        if view.document !== document {
            view.document = document
            view.autoScales = true
            context.coordinator.emitSelection(from: view)
        }
    }

    static func dismantleNSView(_ view: PDFView, coordinator: Coordinator) {
        coordinator.stopObserving()
        view.document = nil
    }

    @MainActor
    final class Coordinator {
        private let onSelection: @MainActor (PaperChatSelection?, Error?) -> Void
        private weak var view: PDFView?
        private var token: NSObjectProtocol?

        init(onSelection: @escaping @MainActor (PaperChatSelection?, Error?) -> Void) {
            self.onSelection = onSelection
        }

        func observe(_ view: PDFView) {
            self.view = view
            token = NotificationCenter.default.addObserver(
                forName: .PDFViewSelectionChanged,
                object: view,
                queue: .main
            ) { [weak self] _ in
                Task { @MainActor in self?.emitSelection(from: view) }
            }
        }

        func stopObserving() {
            if let token { NotificationCenter.default.removeObserver(token) }
            token = nil
        }

        func emitSelection(from view: PDFView) {
            guard let pdfSelection = view.currentSelection,
                  let document = view.document
            else {
                onSelection(nil, nil)
                return
            }
            let indexes = pdfSelection.pages.map { document.index(for: $0) }
            do {
                onSelection(
                    try PaperSelectionMapper.make(
                        quote: pdfSelection.string,
                        zeroBasedPageIndexes: indexes
                    ),
                    nil
                )
            } catch {
                onSelection(nil, error)
            }
        }
    }
}

private struct PaperChatBubble: View {
    let message: PaperChatHistoryMessage

    var body: some View {
        HStack(alignment: .top) {
            if message.role == "user" { Spacer(minLength: 42) }
            Text(message.content)
                .font(.system(size: 12.5))
                .textSelection(.enabled)
                .padding(.horizontal, 12)
                .padding(.vertical, 10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(
                    RoundedRectangle(cornerRadius: 13, style: .continuous)
                        .fill(message.role == "user" ? PaperPalette.mint.opacity(0.18) : PaperPalette.panel)
                        .stroke(message.role == "user" ? Color.clear : PaperPalette.line)
                )
            if message.role != "user" { Spacer(minLength: 24) }
        }
    }
}

private struct PaperQuestionBubble: View {
    let text: String

    var body: some View {
        HStack {
            Spacer(minLength: 42)
            Text(text)
                .font(.system(size: 12.5))
                .padding(.horizontal, 12)
                .padding(.vertical, 10)
                .background(RoundedRectangle(cornerRadius: 13).fill(PaperPalette.mint.opacity(0.18)))
        }
    }
}

private struct PaperAnswerBubble: View {
    let text: String
    let activity: String
    let isLoading: Bool

    var body: some View {
        HStack(alignment: .top, spacing: 9) {
            CanonicalSixSentencesMark()
                .frame(width: 22, height: 22)
                .padding(5)
                .background(Circle().fill(PaperPalette.raised))
            VStack(alignment: .leading, spacing: 8) {
                if !text.isEmpty {
                    Text(text)
                        .font(.system(size: 12.5))
                        .textSelection(.enabled)
                }
                if isLoading {
                    HStack(spacing: 8) {
                        ProgressView().controlSize(.small)
                        Text(activity.isEmpty ? "Reading the paper…" : activity)
                            .font(.system(size: 10.5))
                            .foregroundStyle(PaperPalette.secondary)
                    }
                }
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 13).fill(PaperPalette.panel).stroke(PaperPalette.line))
        }
    }
}
