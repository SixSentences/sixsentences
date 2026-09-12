import AppKit
import XCTest
@testable import SixSentencesCompanion

final class BrandTests: XCTestCase {
    func testNativeMarkUsesCanonicalWebGeometry() {
        XCTAssertEqual(
            SixSentencesBrand.markRects,
            [
                CGRect(x: 18, y: 15, width: 84, height: 10.5),
                CGRect(x: 18, y: 30.9, width: 66.36, height: 10.5),
                CGRect(x: 18, y: 46.8, width: 78.12, height: 10.5),
                CGRect(x: 18, y: 62.7, width: 53.76, height: 10.5),
                CGRect(x: 18, y: 78.6, width: 72.24, height: 10.5),
                CGRect(x: 18, y: 94.5, width: 30.24, height: 10.5),
                CGRect(x: 55.24, y: 94.5, width: 15, height: 10.5),
            ]
        )
    }

    func testMenuBarMarkIsAnAdaptiveTemplateAtNativeStatusItemSize() {
        let image = SixSentencesBrand.menuBarImage

        XCTAssertTrue(image.isTemplate)
        XCTAssertEqual(image.size, NSSize(width: 18, height: 18))
        XCTAssertFalse(image.representations.isEmpty)
    }

    func testMenuBarPresentationAddsBadgeAndLocalizedAccessibilityWhileRecording() {
        XCTAssertFalse(SixSentencesMenuBarPresentation.showsRecordingBadge(false))
        XCTAssertTrue(SixSentencesMenuBarPresentation.showsRecordingBadge(true))

        XCTAssertEqual(
            SixSentencesMenuBarPresentation.accessibilityLabel(
                isRecording: true,
                usesGerman: true
            ),
            "SixSentences Companion, Aufnahme läuft"
        )
        XCTAssertEqual(
            SixSentencesMenuBarPresentation.accessibilityValue(
                isRecording: true,
                usesGerman: false
            ),
            "Recording"
        )
        XCTAssertEqual(
            SixSentencesMenuBarPresentation.accessibilityLabel(
                isRecording: false,
                usesGerman: false
            ),
            "SixSentences Companion, idle"
        )
        XCTAssertEqual(
            SixSentencesMenuBarPresentation.accessibilityValue(
                isRecording: false,
                usesGerman: true
            ),
            "Bereit"
        )
    }
}
