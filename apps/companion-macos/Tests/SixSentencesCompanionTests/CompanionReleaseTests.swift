import Foundation
import XCTest
@testable import SixSentencesCompanion

final class CompanionReleaseTests: XCTestCase {
    func testApplicationReleaseVersionMatchesStableBundleContract() {
        XCTAssertEqual(CompanionApplicationVersion.release, "0.1.11")
        XCTAssertEqual(CompanionApplicationVersion.build, "11")
        XCTAssertEqual(CompanionApplicationVersion.channel, "Preview")
        XCTAssertTrue(CompanionApplicationVersion.displayVersion.hasPrefix("Preview · "))

        let packageRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        let infoURL = packageRoot.appendingPathComponent("AppBundle/Info.plist")
        let info = NSDictionary(contentsOf: infoURL) as? [String: Any]
        XCTAssertEqual(
            info?["CFBundleShortVersionString"] as? String,
            CompanionApplicationVersion.release
        )
        XCTAssertEqual(
            info?["CFBundleVersion"] as? String,
            CompanionApplicationVersion.build
        )
    }

    func testSemanticVersionsCompareNumerically() throws {
        let old = try XCTUnwrap(CompanionSemanticVersion("0.1.9"))
        let current = try XCTUnwrap(CompanionSemanticVersion("0.1.11"))
        let padded = try XCTUnwrap(CompanionSemanticVersion("0.1.11.0"))

        XCTAssertLessThan(old, current)
        XCTAssertEqual(current, padded)
        XCTAssertNil(CompanionSemanticVersion("0.1.beta"))
        XCTAssertNil(CompanionSemanticVersion("1"))
    }

    func testMinimumVersionAllowsCurrentAndRejectsOlderClient() throws {
        let config = try makeConfig(minimumVersion: "0.1.11")

        XCTAssertNoThrow(try CompanionCompatibilityPolicy.validate(
            config,
            currentVersion: "0.1.11"
        ))
        XCTAssertThrowsError(try CompanionCompatibilityPolicy.validate(
            config,
            currentVersion: "0.1.10"
        )) { error in
            XCTAssertEqual(
                error as? CompanionCompatibilityError,
                .updateRequired(
                    current: "0.1.10",
                    minimum: "0.1.11"
                )
            )
        }
    }

    func testMinimumVersionFailsClosedForMalformedServerValue() throws {
        let config = try makeConfig(minimumVersion: "latest")

        XCTAssertThrowsError(try CompanionCompatibilityPolicy.validate(
            config,
            currentVersion: "0.1.11"
        )) { error in
            XCTAssertEqual(
                error as? CompanionCompatibilityError,
                .invalidMinimumVersion("latest")
            )
        }
    }

    func testUpdaterRequiresSignedHTTPSFeedAndNoSystemProfile() throws {
        let publicKey = Data(repeating: 0x5a, count: 32).base64EncodedString()
        let validDictionary: [String: Any] = [
            "SUFeedURL": "https://updates.example.org/companion/appcast.xml",
            "SUPublicEDKey": publicKey,
            "SUSendsSystemProfile": false,
            "SUEnableSystemProfiling": false,
            "SURequireSignedFeed": true,
            "SUVerifyUpdateBeforeExtraction": true,
            "SUSignedFeedFailureExpirationInterval": 0,
        ]
        let valid = try CompanionUpdateConfiguration(infoDictionary: validDictionary)
        XCTAssertEqual(valid.feedURL.scheme, "https")

        func assertInvalid(
            _ key: String,
            value: Any,
            expected: CompanionUpdateConfigurationError,
            file: StaticString = #filePath,
            line: UInt = #line
        ) {
            var dictionary = validDictionary
            dictionary[key] = value
            XCTAssertThrowsError(
                try CompanionUpdateConfiguration(infoDictionary: dictionary),
                file: file,
                line: line
            ) { error in
                XCTAssertEqual(
                    error as? CompanionUpdateConfigurationError,
                    expected,
                    file: file,
                    line: line
                )
            }
        }

        assertInvalid(
            "SUFeedURL",
            value: "http://updates.example.org/companion/appcast.xml",
            expected: .invalidHTTPSFeed
        )
        assertInvalid(
            "SUPublicEDKey",
            value: "not-a-32-byte-key",
            expected: .invalidPublicKey
        )
        assertInvalid(
            "SUSendsSystemProfile",
            value: true,
            expected: .systemProfileMustBeDisabled
        )
        assertInvalid(
            "SUEnableSystemProfiling",
            value: true,
            expected: .systemProfileMustBeDisabled
        )
        assertInvalid(
            "SURequireSignedFeed",
            value: false,
            expected: .signedFeedMustBeRequired
        )
        assertInvalid(
            "SUVerifyUpdateBeforeExtraction",
            value: false,
            expected: .preExtractionVerificationMustBeRequired
        )
        assertInvalid(
            "SUSignedFeedFailureExpirationInterval",
            value: 86_400,
            expected: .signedFeedFallbackMustBeDisabled
        )

        var credentialedFeed = validDictionary
        credentialedFeed["SUFeedURL"] = "https://user:password@updates.example.org/appcast.xml"
        XCTAssertThrowsError(try CompanionUpdateConfiguration(infoDictionary: credentialedFeed)) { error in
            XCTAssertEqual(
                error as? CompanionUpdateConfigurationError,
                .invalidHTTPSFeed
            )
        }
    }

    private func makeConfig(minimumVersion: String) throws -> LiveConfig {
        let json = #"""
        {
          "account_id": "user:release-test",
          "enabled": true,
          "projects": [],
          "desktop": {
            "status": "available",
            "platforms": ["macos"],
            "download_url": "https://research.example.org/downloads/companion",
            "minimum_version": "\#(minimumVersion)",
            "permissions": ["microphone", "system_audio", "speech_recognition"]
          }
        }
        """#
        return try JSONDecoder().decode(LiveConfig.self, from: Data(json.utf8))
    }
}
