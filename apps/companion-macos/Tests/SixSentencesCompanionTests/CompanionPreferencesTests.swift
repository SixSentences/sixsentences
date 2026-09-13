import Foundation
import XCTest
@testable import SixSentencesCompanion

@MainActor
final class CompanionPreferencesTests: XCTestCase {
    func testCommunityOriginDerivesSameOriginAPIAndRejectsMixedDeployments() throws {
        let origin = try CommunityOrigin("https://research.example.org")

        XCTAssertEqual(origin.webURL.absoluteString, "https://research.example.org")
        XCTAssertEqual(origin.apiURL.absoluteString, "https://research.example.org/api")
        XCTAssertNotNil(origin.validatedWebURL("https://research.example.org/interviews/42"))
        XCTAssertNil(origin.validatedWebURL("https://api.research.example.org/interviews/42"))
        XCTAssertNil(origin.validatedWebURL("https://research.example.org:444/interviews/42"))
    }

    func testCommunityOriginAllowsOnlyHTTPSOrDebugLoopbackHTTP() {
        XCTAssertNoThrow(try CommunityOrigin("https://research.example.org"))
        XCTAssertNoThrow(try CommunityOrigin("http://localhost:8080"))
        XCTAssertNoThrow(try CommunityOrigin("http://127.0.0.1"))
        XCTAssertThrowsError(try CommunityOrigin("http://research.example.org"))
        XCTAssertThrowsError(try CommunityOrigin(
            "http://127.0.0.1:3000",
            allowInsecureLoopback: false
        ))
        XCTAssertThrowsError(try CommunityOrigin("https://user:secret@research.example.org"))
        XCTAssertThrowsError(try CommunityOrigin("https://research.example.org/workspace"))
        XCTAssertThrowsError(try CommunityOrigin("https://research.example.org?token=secret"))
    }

    func testPreferencesUseOnlyTheExplicitCommunityOrigin() {
        let suite = "SixSentencesCompanionTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }

        let preferences = CompanionPreferences(
            defaults: defaults,
            communityOrigin: "https://research.example.org"
        )
        XCTAssertEqual(preferences.webBaseURL, "https://research.example.org")
        XCTAssertEqual(preferences.apiBaseURL, "https://research.example.org/api")
        XCTAssertNil(preferences.validatedWebURL("https://other.example.org/interviews"))
    }

    func testLanguageDefaultsToAutomaticAndPersistsExplicitChoices() {
        let suite = "SixSentencesCompanionTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }

        let preferences = CompanionPreferences(defaults: defaults)
        XCTAssertEqual(preferences.language, "auto")

        preferences.setLanguage("de")
        XCTAssertEqual(CompanionPreferences(defaults: defaults).language, "de")

        preferences.setLanguage("en")
        XCTAssertEqual(CompanionPreferences(defaults: defaults).language, "en")
    }

    func testInvalidStoredOrAssignedLanguageFailsClosedToAutomatic() {
        let suite = "SixSentencesCompanionTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        defaults.set("fr", forKey: "companion.language")

        let preferences = CompanionPreferences(defaults: defaults)
        XCTAssertEqual(preferences.language, "auto")
        XCTAssertEqual(defaults.string(forKey: "companion.language"), "auto")

        preferences.setLanguage("unexpected")
        XCTAssertEqual(preferences.language, "auto")
    }

    func testBrainstormProjectPersistsOnlyForTheVerifiedAccountAndAllowlist() {
        let suite = "SixSentencesCompanionTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let projects = [LiveProjectSummary(id: 7, name: "Thesis")]
        let preferences = CompanionPreferences(defaults: defaults)

        XCTAssertEqual(preferences.setBrainstormProjectID(
            7,
            ownerAccountID: "org-a:user-a",
            availableProjects: projects
        ), 7)
        let persistedOwner = defaults.string(forKey: "companion.brainstorm.projectOwner")
        XCTAssertEqual(persistedOwner?.count, 64)
        XCTAssertNotEqual(persistedOwner, "org-a:user-a")
        XCTAssertEqual(CompanionPreferences(defaults: defaults).brainstormProjectID(
            ownerAccountID: "org-a:user-a",
            availableProjects: projects
        ), 7)
        XCTAssertNil(CompanionPreferences(defaults: defaults).brainstormProjectID(
            ownerAccountID: "org-b:user-a",
            availableProjects: projects
        ))
        XCTAssertNil(CompanionPreferences(defaults: defaults).brainstormProjectID(
            ownerAccountID: "org-a:user-a",
            availableProjects: projects
        ), "Switching identity must scrub the previous creator's selection.")
    }

    func testBrainstormProjectRejectsStaleOrForeignProjectAndSupportsNoProject() {
        let suite = "SixSentencesCompanionTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let preferences = CompanionPreferences(defaults: defaults)
        let projects = [LiveProjectSummary(id: 7, name: "Thesis")]

        XCTAssertNil(preferences.setBrainstormProjectID(
            99,
            ownerAccountID: "org-a:user-a",
            availableProjects: projects
        ))
        XCTAssertNil(preferences.setBrainstormProjectID(
            nil,
            ownerAccountID: "org-a:user-a",
            availableProjects: projects
        ))
        XCTAssertNil(preferences.brainstormProjectID(
            ownerAccountID: "org-a:user-a",
            availableProjects: projects
        ))
    }

    func testOrphanedBrainstormProjectWithoutOwnerFailsClosed() {
        let suite = "SixSentencesCompanionTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        defaults.set(7, forKey: "companion.brainstorm.projectID")
        let preferences = CompanionPreferences(defaults: defaults)

        XCTAssertNil(preferences.brainstormProjectID(
            ownerAccountID: "org-a:user-a",
            availableProjects: [LiveProjectSummary(id: 7, name: "Thesis")]
        ))
        XCTAssertNil(defaults.object(forKey: "companion.brainstorm.projectID"))
    }
}
