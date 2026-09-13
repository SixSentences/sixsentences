import CoreGraphics
import XCTest
@testable import SixSentencesCompanion

final class WindowBehaviorTests: XCTestCase {
    func testOverlayAnchorsTopCenterInsideVisibleFrame() {
        let visibleFrame = CGRect(x: 100, y: 50, width: 1_400, height: 900)
        let origin = CompanionWindowBehavior.anchoredOrigin(
            visibleFrame: visibleFrame,
            windowSize: CGSize(width: 410, height: 610)
        )

        XCTAssertEqual(origin.x, 595)
        XCTAssertEqual(origin.y, 326)
    }

    func testCollapsedOverlayKeepsSameTopEdge() {
        let visibleFrame = CGRect(x: 0, y: 0, width: 1_000, height: 800)
        let expanded = CompanionWindowBehavior.anchoredOrigin(
            visibleFrame: visibleFrame,
            windowSize: CGSize(width: 410, height: 610)
        )
        let collapsed = CompanionWindowBehavior.anchoredOrigin(
            visibleFrame: visibleFrame,
            windowSize: CGSize(width: 410, height: 122)
        )

        XCTAssertEqual(expanded.y + 610, collapsed.y + 122)
        XCTAssertEqual(expanded.x, collapsed.x)
    }

    func testRestoredOriginIsClampedIntoVisibleFrame() {
        let visibleFrame = CGRect(x: 100, y: 50, width: 1_000, height: 700)
        let origin = CompanionWindowBehavior.constrainedOrigin(
            CGPoint(x: 1_500, y: -200),
            visibleFrame: visibleFrame,
            windowSize: CGSize(width: 410, height: 610)
        )

        XCTAssertEqual(origin.x, 690)
        XCTAssertEqual(origin.y, 50)
    }
}
