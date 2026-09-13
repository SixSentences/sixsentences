import AppKit

enum SixSentencesBrand {
    static let coordinateSize = CGSize(width: 120, height: 120)
    static let menuBarPointSize = CGSize(width: 18, height: 18)

    /// Canonical geometry from `src/components/brand/six-mark.tsx`.
    static let markRects: [CGRect] = [
        CGRect(x: 18, y: 15, width: 84, height: 10.5),
        CGRect(x: 18, y: 30.9, width: 66.36, height: 10.5),
        CGRect(x: 18, y: 46.8, width: 78.12, height: 10.5),
        CGRect(x: 18, y: 62.7, width: 53.76, height: 10.5),
        CGRect(x: 18, y: 78.6, width: 72.24, height: 10.5),
        CGRect(x: 18, y: 94.5, width: 30.24, height: 10.5),
        CGRect(x: 55.24, y: 94.5, width: 15, height: 10.5),
    ]

    /// A vector-backed template image so macOS applies the correct status-bar
    /// color in light, dark, inactive, and highlighted states.
    static let menuBarImage: NSImage = {
        let image = NSImage(size: menuBarPointSize, flipped: true) { destination in
            let scale = min(
                destination.width / coordinateSize.width,
                destination.height / coordinateSize.height
            )
            let xOffset = (destination.width - coordinateSize.width * scale) / 2
            let yOffset = (destination.height - coordinateSize.height * scale) / 2

            NSColor.black.setFill()
            for rect in markRects {
                let scaled = CGRect(
                    x: xOffset + rect.origin.x * scale,
                    y: yOffset + rect.origin.y * scale,
                    width: rect.width * scale,
                    height: rect.height * scale
                )
                NSBezierPath(
                    roundedRect: scaled,
                    xRadius: min(scaled.height / 2, 5.25 * scale),
                    yRadius: min(scaled.height / 2, 5.25 * scale)
                ).fill()
            }
            return true
        }
        image.isTemplate = true
        return image
    }()
}

enum SixSentencesMenuBarPresentation {
    static let badgeDiameter: CGFloat = 6

    static func showsRecordingBadge(_ isRecording: Bool) -> Bool {
        isRecording
    }

    static func accessibilityLabel(isRecording: Bool, usesGerman: Bool) -> String {
        if usesGerman {
            return isRecording
                ? "SixSentences Companion, Aufnahme läuft"
                : "SixSentences Companion, bereit"
        }
        return isRecording
            ? "SixSentences Companion, recording"
            : "SixSentences Companion, idle"
    }

    static func accessibilityValue(isRecording: Bool, usesGerman: Bool) -> String {
        if usesGerman {
            return isRecording ? "Aufnahme läuft" : "Bereit"
        }
        return isRecording ? "Recording" : "Idle"
    }
}
