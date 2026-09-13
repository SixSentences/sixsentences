import Combine
import Foundation
import Sparkle

enum CompanionApplicationVersion {
    static let release = "0.1.11"
    static let build = "11"
    static let channel = "Preview"

    static var current: String {
        let bundled = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String
        return bundled?.trimmingCharacters(in: .whitespacesAndNewlines).nonEmpty ?? release
    }

    static var currentBuild: String {
        let bundled = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String
        return bundled?.trimmingCharacters(in: .whitespacesAndNewlines).nonEmpty ?? build
    }

    static var userAgent: String {
        "SixSentencesCompanion/\(current) (macOS; build \(currentBuild))"
    }

    static var displayVersion: String {
        "\(channel) · \(current) (\(currentBuild))"
    }
}

struct CompanionSemanticVersion: Comparable, Equatable, Sendable {
    private let components: [Int]

    init?(_ rawValue: String) {
        let clean = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        let pieces = clean.split(separator: ".", omittingEmptySubsequences: false)
        guard (2 ... 4).contains(pieces.count) else { return nil }
        var parsed: [Int] = []
        for piece in pieces {
            guard !piece.isEmpty,
                  piece.allSatisfy(\.isNumber),
                  let value = Int(piece),
                  value >= 0
            else { return nil }
            parsed.append(value)
        }
        while parsed.count < 4 { parsed.append(0) }
        components = parsed
    }

    static func < (lhs: Self, rhs: Self) -> Bool {
        lhs.components.lexicographicallyPrecedes(rhs.components)
    }
}

enum CompanionCompatibilityError: LocalizedError, Equatable, Sendable {
    case invalidClientVersion(String)
    case invalidMinimumVersion(String)
    case updateRequired(current: String, minimum: String)

    var errorDescription: String? {
        switch self {
        case let .invalidClientVersion(version):
            "The Companion build has an invalid version (\(version)). Install a current release."
        case let .invalidMinimumVersion(version):
            "SixSentences returned an invalid minimum Companion version (\(version)). The app stopped to avoid using an unsupported client."
        case let .updateRequired(current, minimum):
            "SixSentences Companion \(minimum) or later is required. This Mac is running \(current)."
        }
    }
}

enum CompanionCompatibilityPolicy {
    static func validate(
        _ config: LiveConfig,
        currentVersion: String = CompanionApplicationVersion.current
    ) throws {
        guard let rawMinimum = config.desktop?.minimumVersion?
            .trimmingCharacters(in: .whitespacesAndNewlines),
              !rawMinimum.isEmpty
        else { return }
        guard let current = CompanionSemanticVersion(currentVersion) else {
            throw CompanionCompatibilityError.invalidClientVersion(currentVersion)
        }
        guard let minimum = CompanionSemanticVersion(rawMinimum) else {
            throw CompanionCompatibilityError.invalidMinimumVersion(rawMinimum)
        }
        guard current >= minimum else {
            throw CompanionCompatibilityError.updateRequired(
                current: currentVersion,
                minimum: rawMinimum
            )
        }
    }
}

struct CompanionUpdateConfiguration: Equatable, Sendable {
    let feedURL: URL
    let publicEDKey: String

    init(infoDictionary: [String: Any]) throws {
        guard let rawFeedURL = infoDictionary["SUFeedURL"] as? String,
              let feedURL = URL(string: rawFeedURL),
              feedURL.scheme?.lowercased() == "https",
              feedURL.host?.isEmpty == false,
              feedURL.user == nil,
              feedURL.password == nil,
              feedURL.query == nil,
              feedURL.fragment == nil
        else {
            throw CompanionUpdateConfigurationError.invalidHTTPSFeed
        }
        guard let publicEDKey = infoDictionary["SUPublicEDKey"] as? String,
              let decodedKey = Data(base64Encoded: publicEDKey),
              decodedKey.count == 32
        else {
            throw CompanionUpdateConfigurationError.invalidPublicKey
        }
        guard let sendsSystemProfile = infoDictionary["SUSendsSystemProfile"] as? Bool,
              sendsSystemProfile == false,
              let enablesSystemProfiling = infoDictionary["SUEnableSystemProfiling"] as? Bool,
              enablesSystemProfiling == false
        else {
            throw CompanionUpdateConfigurationError.systemProfileMustBeDisabled
        }
        guard let requiresSignedFeed = infoDictionary["SURequireSignedFeed"] as? Bool,
              requiresSignedFeed
        else {
            throw CompanionUpdateConfigurationError.signedFeedMustBeRequired
        }
        guard let verifiesBeforeExtraction = infoDictionary["SUVerifyUpdateBeforeExtraction"] as? Bool,
              verifiesBeforeExtraction
        else {
            throw CompanionUpdateConfigurationError.preExtractionVerificationMustBeRequired
        }
        guard let signedFeedExpiration = infoDictionary["SUSignedFeedFailureExpirationInterval"] as? Int,
              signedFeedExpiration == 0
        else {
            throw CompanionUpdateConfigurationError.signedFeedFallbackMustBeDisabled
        }
        self.feedURL = feedURL
        self.publicEDKey = publicEDKey
    }
}

enum CompanionUpdateConfigurationError: LocalizedError, Equatable, Sendable {
    case invalidHTTPSFeed
    case invalidPublicKey
    case systemProfileMustBeDisabled
    case signedFeedMustBeRequired
    case preExtractionVerificationMustBeRequired
    case signedFeedFallbackMustBeDisabled

    var errorDescription: String? {
        switch self {
        case .invalidHTTPSFeed:
            "Automatic updates are disabled because the signed HTTPS appcast is not configured."
        case .invalidPublicKey:
            "Automatic updates are disabled because the Sparkle EdDSA public key is missing or invalid."
        case .systemProfileMustBeDisabled:
            "Automatic updates are disabled because system-profile reporting was not explicitly disabled."
        case .signedFeedMustBeRequired:
            "Automatic updates are disabled because signed appcast verification is not required."
        case .preExtractionVerificationMustBeRequired:
            "Automatic updates are disabled because archives are not verified before extraction."
        case .signedFeedFallbackMustBeDisabled:
            "Automatic updates are disabled because signed-feed failures are allowed to expire."
        }
    }
}

@MainActor
final class CompanionUpdaterController: ObservableObject {
    @Published private(set) var isConfigured = false
    @Published private(set) var canCheckForUpdates = false
    @Published private(set) var automaticallyChecksForUpdates = false
    @Published private(set) var configurationNotice = ""

    private let controller: SPUStandardUpdaterController
    private var observations = Set<AnyCancellable>()

    init(bundle: Bundle = .main) {
        controller = SPUStandardUpdaterController(
            startingUpdater: false,
            updaterDelegate: nil,
            userDriverDelegate: nil
        )
        do {
            _ = try CompanionUpdateConfiguration(infoDictionary: bundle.infoDictionary ?? [:])
            controller.startUpdater()
            isConfigured = true
            controller.updater.publisher(
                for: \.canCheckForUpdates,
                options: [.initial, .new]
            )
            .receive(on: RunLoop.main)
            .sink { [weak self] value in self?.canCheckForUpdates = value }
            .store(in: &observations)
            controller.updater.publisher(
                for: \.automaticallyChecksForUpdates,
                options: [.initial, .new]
            )
            .receive(on: RunLoop.main)
            .sink { [weak self] value in self?.automaticallyChecksForUpdates = value }
            .store(in: &observations)
        } catch {
            isConfigured = false
            canCheckForUpdates = false
            automaticallyChecksForUpdates = false
            configurationNotice = error.localizedDescription
        }
    }

    func setAutomaticallyChecksForUpdates(_ enabled: Bool) {
        guard isConfigured else { return }
        controller.updater.automaticallyChecksForUpdates = enabled
        automaticallyChecksForUpdates = controller.updater.automaticallyChecksForUpdates
        canCheckForUpdates = controller.updater.canCheckForUpdates
    }

    func checkForUpdates() {
        guard isConfigured, controller.updater.canCheckForUpdates else { return }
        controller.checkForUpdates(nil)
        canCheckForUpdates = controller.updater.canCheckForUpdates
    }
}

private extension String {
    var nonEmpty: String? { isEmpty ? nil : self }
}
