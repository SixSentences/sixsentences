import CoreGraphics

enum CompanionWindowBehavior {
    static let topInset: CGFloat = 14

    static func anchoredOrigin(
        visibleFrame: CGRect,
        windowSize: CGSize,
        topInset: CGFloat = topInset
    ) -> CGPoint {
        CGPoint(
            x: visibleFrame.midX - windowSize.width / 2,
            y: visibleFrame.maxY - windowSize.height - topInset
        )
    }

    static func constrainedOrigin(
        _ origin: CGPoint,
        visibleFrame: CGRect,
        windowSize: CGSize
    ) -> CGPoint {
        let maximumX = max(visibleFrame.minX, visibleFrame.maxX - windowSize.width)
        let maximumY = max(visibleFrame.minY, visibleFrame.maxY - windowSize.height)
        return CGPoint(
            x: min(max(origin.x, visibleFrame.minX), maximumX),
            y: min(max(origin.y, visibleFrame.minY), maximumY)
        )
    }
}
