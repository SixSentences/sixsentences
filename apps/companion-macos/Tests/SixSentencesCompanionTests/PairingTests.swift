import XCTest
@testable import SixSentencesCompanion

final class PairingTests: XCTestCase {
    func testPairingStateMustMatchExactly() {
        let now = Date()
        let pending = PendingPairing(verifier: "verifier", state: "expected_state_123", createdAt: now)
        XCTAssertNoThrow(
            try PairingCoordinator.validateCallback(
                pending: pending,
                returnedState: "expected_state_123",
                now: now
            )
        )
        XCTAssertThrowsError(
            try PairingCoordinator.validateCallback(
                pending: pending,
                returnedState: "attacker_state_123",
                now: now
            )
        ) { error in
            guard case PairingError.stateMismatch = error else {
                return XCTFail("Expected stateMismatch, got \(error)")
            }
        }
    }

    func testPairingExpiresAfterTenMinutes() {
        let now = Date()
        let pending = PendingPairing(
            verifier: "verifier",
            state: "expected_state_123",
            createdAt: now.addingTimeInterval(-601)
        )
        XCTAssertThrowsError(
            try PairingCoordinator.validateCallback(
                pending: pending,
                returnedState: pending.state,
                now: now
            )
        ) { error in
            guard case PairingError.expired = error else {
                return XCTFail("Expected expired, got \(error)")
            }
        }
    }

    func testBrowserHandoffNeverIncludesAHostOrDeviceName() throws {
        let url = try PairingCoordinator.browserURL(
            webBaseURL: "https://research.example.org",
            state: "state_12345678901234567890",
            codeChallenge: String(repeating: "c", count: 43)
        )
        let names = Set(try XCTUnwrap(
            URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems
        ).map(\.name))

        XCTAssertFalse(names.contains("device_name"))
        XCTAssertFalse(url.absoluteString.localizedCaseInsensitiveContains("host"))
    }
}
