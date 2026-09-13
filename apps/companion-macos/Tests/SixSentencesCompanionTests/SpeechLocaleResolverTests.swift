import Foundation
import CoreMedia
import XCTest
@testable import SixSentencesCompanion

final class SpeechLocaleResolverTests: XCTestCase {
    func testAutomaticGermanUsesPreferredLanguageInsteadOfProcessLocaleMixing() throws {
        let locale = try XCTUnwrap(
            SpeechLocaleResolver.resolve(
                language: "auto",
                preferredLanguages: ["de-DE"],
                supportedLocales: [Locale(identifier: "de-DE"), Locale(identifier: "en-US")]
            )
        )

        XCTAssertEqual(locale.identifier(.bcp47), "de-DE")
        XCTAssertTrue(SpeechLocaleResolver.usesGerman(
            language: "auto",
            preferredLanguages: ["de-DE"]
        ))
    }

    func testAutomaticEnglishKeepsEnglishMessages() {
        XCTAssertFalse(SpeechLocaleResolver.usesGerman(
            language: "auto",
            preferredLanguages: ["en-DE"]
        ))
    }

    func testInvalidEnglishRegionFallsBackWithinEnglish() throws {
        let locale = try XCTUnwrap(
            SpeechLocaleResolver.resolve(
                language: "auto",
                preferredLanguages: ["en_DE"],
                supportedLocales: [Locale(identifier: "de-DE"), Locale(identifier: "en-US")]
            )
        )

        XCTAssertEqual(locale.identifier(.bcp47), "en-US")
        XCTAssertNotEqual(locale.identifier, "en_DE")
    }

    func testExplicitEnglishIgnoresGermanSystemPreference() throws {
        let locale = try XCTUnwrap(
            SpeechLocaleResolver.resolve(
                language: "en",
                preferredLanguages: ["de-DE"],
                supportedLocales: [Locale(identifier: "de-DE"), Locale(identifier: "en-GB")]
            )
        )

        XCTAssertEqual(locale.identifier(.bcp47), "en-GB")
        XCTAssertFalse(SpeechLocaleResolver.usesGerman(
            language: "en",
            preferredLanguages: ["de-DE"]
        ))
    }

    func testExplicitGermanIgnoresEnglishSystemPreference() throws {
        let locale = try XCTUnwrap(
            SpeechLocaleResolver.resolve(
                language: "de",
                preferredLanguages: ["en-US"],
                supportedLocales: [Locale(identifier: "de-DE"), Locale(identifier: "en-US")]
            )
        )

        XCTAssertEqual(locale.identifier(.bcp47), "de-DE")
    }

    func testInterfaceLanguageRemainsIndependentFromTranscriptionChoice() {
        XCTAssertTrue(SpeechLocaleResolver.usesGerman(
            language: "auto",
            preferredLanguages: ["de-DE"]
        ))
        XCTAssertFalse(SpeechLocaleResolver.usesGerman(
            language: "auto",
            preferredLanguages: ["en-US"]
        ))
    }

    func testResolverNeverFallsBackAcrossLanguages() {
        XCTAssertNil(
            SpeechLocaleResolver.resolve(
                language: "de",
                preferredLanguages: ["de-DE"],
                supportedLocales: [Locale(identifier: "en-US"), Locale(identifier: "fr-FR")]
            )
        )
    }

    func testGermanFallbackOrderIsDeterministic() throws {
        let locale = try XCTUnwrap(
            SpeechLocaleResolver.resolve(
                language: "de",
                supportedLocales: [Locale(identifier: "de-CH"), Locale(identifier: "de-AT")]
            )
        )

        XCTAssertEqual(locale.identifier(.bcp47), "de-AT")
    }

    func testCanonicalIdentifierNormalizesUnderscore() {
        XCTAssertEqual(SpeechLocaleResolver.canonicalIdentifier("en_DE"), "en-DE")
        XCTAssertEqual(SpeechLocaleResolver.canonicalIdentifier("de_DE"), "de-DE")
    }

    func testUnknownAutomaticLanguageUsesOnlySameLanguageCandidate() throws {
        let locale = try XCTUnwrap(
            SpeechLocaleResolver.resolve(
                language: "auto",
                preferredLanguages: ["fr-CH"],
                supportedLocales: [Locale(identifier: "en-US"), Locale(identifier: "fr-FR")]
            )
        )

        XCTAssertEqual(locale.identifier(.bcp47), "fr-FR")
    }

    func testAnalyzerMapperExposesNonFinalTextForLocalPreview() throws {
        let update = try XCTUnwrap(SpeechAnalyzerResultMapper.update(
            channel: .microphone,
            text: "still changing",
            isFinal: false,
            range: CMTimeRange(start: .zero, duration: CMTime(seconds: 1, preferredTimescale: 1_000)),
            anchorMS: 200
        ))

        XCTAssertEqual(update.text, "still changing")
        XCTAssertFalse(update.isFinal)
    }

    func testAnalyzerMapperProducesFinalMonotonicTimeRange() throws {
        let update = try XCTUnwrap(
            SpeechAnalyzerResultMapper.update(
                channel: .system,
                text: "  final result  ",
                isFinal: true,
                range: CMTimeRange(
                    start: CMTime(seconds: 1.25, preferredTimescale: 1_000),
                    duration: CMTime(seconds: 0.75, preferredTimescale: 1_000)
                ),
                anchorMS: 500
            )
        )

        XCTAssertEqual(update.text, "final result")
        XCTAssertEqual(update.startMS, 1_750)
        XCTAssertEqual(update.endMS, 2_500)
        XCTAssertTrue(update.isFinal)
    }

    func testAnalyzerMapperHandlesInvalidCoreMediaTimeWithoutTrapping() throws {
        let update = try XCTUnwrap(
            SpeechAnalyzerResultMapper.update(
                channel: .microphone,
                text: "final",
                isFinal: true,
                range: CMTimeRange(start: .invalid, duration: .invalid),
                anchorMS: 300
            )
        )

        XCTAssertEqual(update.startMS, 300)
        XCTAssertEqual(update.endMS, 301)
    }

    func testVolatilePreviewNeverEntersDurableTranscriptReducer() throws {
        let range = CMTimeRange(
            start: .zero,
            duration: CMTime(seconds: 1, preferredTimescale: 1_000)
        )
        let preview = try XCTUnwrap(SpeechAnalyzerResultRoute.previewUpdate(
            channel: .microphone,
            text: "visible preview",
            resultIsFinal: true,
            range: range,
            anchorMS: 0
        ))
        var reducer = TranscriptReducer(makeID: { "durable_1" })

        XCTAssertFalse(preview.isFinal)
        XCTAssertTrue(reducer.ingestFinal(preview).isEmpty)
        XCTAssertTrue(reducer.flushAll().isEmpty)
    }

    func testOnlyFinalOnlyAnalyzerResultBecomesDurable() throws {
        let range = CMTimeRange(
            start: .zero,
            duration: CMTime(seconds: 1, preferredTimescale: 1_000)
        )
        XCTAssertNil(SpeechAnalyzerResultRoute.durableUpdate(
            channel: .system,
            text: "not final",
            resultIsFinal: false,
            range: range,
            anchorMS: 0
        ))

        let durable = try XCTUnwrap(SpeechAnalyzerResultRoute.durableUpdate(
            channel: .system,
            text: "durable sentence",
            resultIsFinal: true,
            range: range,
            anchorMS: 0
        ))
        var reducer = TranscriptReducer(makeID: { "durable_1" })
        XCTAssertTrue(durable.isFinal)
        XCTAssertTrue(reducer.ingestFinal(durable).isEmpty)
        XCTAssertEqual(reducer.flushAll().map(\.text), ["durable sentence"])
    }

    func testGermanAcademicVocabularyKeepsEnglishResearchTermsBounded() {
        let terms = AcademicSpeechVocabulary.contextualStrings(
            for: Locale(identifier: "de-DE")
        )

        XCTAssertLessThanOrEqual(terms.count, AcademicSpeechVocabulary.maximumTerms)
        XCTAssertTrue(terms.contains("large language model"))
        XCTAssertTrue(terms.contains("PyTorch"))
        XCTAssertTrue(terms.contains("Terraform"))
        XCTAssertTrue(terms.contains("Introduction"))
        XCTAssertTrue(terms.contains("Methodology"))
        XCTAssertTrue(terms.contains("Results"))
        XCTAssertTrue(terms.contains("Discussion"))
        XCTAssertTrue(terms.contains("Einleitung"))
        XCTAssertTrue(terms.contains("Methodik"))
        XCTAssertTrue(terms.contains("Ergebnisse"))
        XCTAssertTrue(terms.contains("Diskussion"))
        XCTAssertTrue(terms.allSatisfy { !$0.isEmpty && $0.count <= 64 })
        XCTAssertEqual(Set(terms.map { $0.lowercased() }).count, terms.count)
    }

    func testProjectNameIsBoundedLocalContextWithoutDisplacingAcademicTerms() {
        let terms = AcademicSpeechVocabulary.contextualStrings(
            for: Locale(identifier: "de-DE"),
            additionalTerms: ["discussion", "Master Thesis IaC", String(repeating: "x", count: 65)]
        )

        XCTAssertTrue(terms.contains("Master Thesis IaC"))
        XCTAssertFalse(terms.contains(String(repeating: "x", count: 65)))
        XCTAssertTrue(terms.contains("Introduction"))
        XCTAssertTrue(terms.contains("Discussion"))
        XCTAssertFalse(terms.contains("discussion"))
        XCTAssertLessThanOrEqual(terms.count, AcademicSpeechVocabulary.maximumTerms)
    }

    func testAcademicVocabularyFailsClosedForUnsupportedLanguage() {
        XCTAssertTrue(AcademicSpeechVocabulary.contextualStrings(
            for: Locale(identifier: "fr-FR")
        ).isEmpty)
    }

    func testRecognizedMixedLanguageTextIsNotPostProcessed() throws {
        let raw = "Terraform und PyTorch bleiben im LLM Benchmark"
        let update = try XCTUnwrap(SpeechAnalyzerResultMapper.update(
            channel: .microphone,
            text: raw,
            isFinal: true,
            range: CMTimeRange(start: .zero, duration: CMTime(seconds: 1, preferredTimescale: 1_000)),
            anchorMS: 0
        ))

        XCTAssertEqual(update.text, raw)
    }
}
