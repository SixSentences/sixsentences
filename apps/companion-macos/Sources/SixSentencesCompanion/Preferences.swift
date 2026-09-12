import CryptoKit
import Foundation

enum CommunityOriginError: LocalizedError, Equatable {
    case missing
    case invalid
    case insecure

    var errorDescription: String? {
        switch self {
        case .missing:
            "This Companion build has no community origin. Rebuild it with SIX_COMMUNITY_ORIGIN."
        case .invalid:
            "The configured community origin is invalid."
        case .insecure:
            "The community origin must use HTTPS. HTTP is accepted only for loopback development."
        }
    }
}

/// One deployment-owned web origin. The API is always derived as `/api` so a
/// packaged client cannot mix authentication and data between deployments.
struct CommunityOrigin: Equatable, Sendable {
    private static let loopbackHosts = ["localhost", "127.0.0.1", "::1"]

    let webURL: URL

    init(
        _ rawValue: String,
        allowInsecureLoopback: Bool = _isDebugAssertConfiguration()
    ) throws {
        let clean = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty else { throw CommunityOriginError.missing }
        guard var components = URLComponents(string: clean),
              let scheme = components.scheme?.lowercased(),
              let host = components.host?.lowercased(),
              !host.isEmpty,
              components.user == nil,
              components.password == nil,
              components.query == nil,
              components.fragment == nil,
              components.path.isEmpty || components.path == "/"
        else { throw CommunityOriginError.invalid }
        guard scheme == "https" || (
            allowInsecureLoopback
                && scheme == "http"
                && Self.loopbackHosts.contains(host)
        ) else {
            throw CommunityOriginError.insecure
        }
        components.scheme = scheme
        components.host = host
        components.path = ""
        guard let canonical = components.url else { throw CommunityOriginError.invalid }
        webURL = canonical
    }

    var apiURL: URL {
        webURL.appendingPathComponent("api", isDirectory: false)
    }

    func validatedWebURL(_ rawValue: String) -> URL? {
        guard let candidate = URL(string: rawValue),
              candidate.user == nil,
              candidate.password == nil,
              candidate.scheme?.lowercased() == webURL.scheme?.lowercased(),
              candidate.host?.lowercased() == webURL.host?.lowercased(),
              Self.effectivePort(candidate) == Self.effectivePort(webURL)
        else { return nil }
        return candidate
    }

    private static func effectivePort(_ url: URL) -> Int? {
        if let port = url.port { return port }
        return switch url.scheme?.lowercased() {
        case "https": 443
        case "http": 80
        default: nil
        }
    }
}

enum CompanionTranscriptionLanguage: String, CaseIterable, Sendable {
    case automatic = "auto"
    case german = "de"
    case english = "en"

    static func validated(_ value: String?) -> Self {
        guard let value, let language = Self(rawValue: value) else { return .automatic }
        return language
    }
}

@MainActor
final class CompanionPreferences: ObservableObject {
    static let shared = CompanionPreferences()

    private enum Key {
        static let language = "companion.language"
        static let projectID = "companion.projectID"
        static let autoOpen = "companion.autoOpen"
        static let brainstormOutputLanguage = "companion.brainstorm.outputLanguage"
        static let brainstormProjectID = "companion.brainstorm.projectID"
        static let brainstormProjectOwner = "companion.brainstorm.projectOwner"
    }

    private let defaults: UserDefaults

    let communityOrigin: CommunityOrigin?
    let apiBaseURL: String
    let webBaseURL: String
    @Published private(set) var language: String
    @Published private(set) var brainstormOutputLanguage: String
    @Published var projectID: Int? {
        didSet {
            if let projectID { defaults.set(projectID, forKey: Key.projectID) }
            else { defaults.removeObject(forKey: Key.projectID) }
        }
    }
    @Published var autoOpenWebSession: Bool {
        didSet { defaults.set(autoOpenWebSession, forKey: Key.autoOpen) }
    }

    init(
        defaults: UserDefaults = .standard,
        communityOrigin configuredOrigin: String? = nil,
        bundle: Bundle = .main
    ) {
        self.defaults = defaults
        let bundledOrigin = bundle.object(forInfoDictionaryKey: "SixSentencesCommunityOrigin") as? String
        let explicitOrigin = configuredOrigin ?? bundledOrigin
        #if DEBUG
        let rawOrigin: String? = explicitOrigin ?? "http://localhost"
        #else
        let rawOrigin: String? = explicitOrigin
        #endif
        // Release bundles fail closed when the build did not inject a valid
        // deployment origin. Debug-only loopback keeps local source testing
        // possible without creating a hidden production fallback.
        let origin = rawOrigin.flatMap { try? CommunityOrigin($0) }
        communityOrigin = origin
        apiBaseURL = origin?.apiURL.absoluteString ?? ""
        webBaseURL = origin?.webURL.absoluteString ?? ""
        let storedLanguage = defaults.string(forKey: Key.language)
        let validatedLanguage = CompanionTranscriptionLanguage.validated(storedLanguage).rawValue
        language = validatedLanguage
        let defaultBrainstormLanguage = SpeechLocaleResolver.usesGerman(language: "auto")
            ? BrainstormOutputLanguage.german.rawValue
            : BrainstormOutputLanguage.english.rawValue
        let storedBrainstormLanguage = defaults.string(forKey: Key.brainstormOutputLanguage)
        brainstormOutputLanguage = BrainstormOutputLanguage(rawValue: storedBrainstormLanguage ?? "")?.rawValue
            ?? defaultBrainstormLanguage
        projectID = defaults.object(forKey: Key.projectID) as? Int
        autoOpenWebSession = defaults.object(forKey: Key.autoOpen) as? Bool ?? true
        if storedLanguage != validatedLanguage {
            defaults.set(validatedLanguage, forKey: Key.language)
        }
        if storedBrainstormLanguage != brainstormOutputLanguage {
            defaults.set(brainstormOutputLanguage, forKey: Key.brainstormOutputLanguage)
        }
    }

    func setLanguage(_ value: String) {
        let validated = CompanionTranscriptionLanguage.validated(value).rawValue
        guard language != validated else { return }
        language = validated
        defaults.set(validated, forKey: Key.language)
    }

    func setBrainstormOutputLanguage(_ value: BrainstormOutputLanguage) {
        guard brainstormOutputLanguage != value.rawValue else { return }
        brainstormOutputLanguage = value.rawValue
        defaults.set(value.rawValue, forKey: Key.brainstormOutputLanguage)
    }

    /// Returns a Brainstorm-only selection after binding it to the verified
    /// creator/account identity and the server-provided project allowlist.
    func brainstormProjectID(
        ownerAccountID: String,
        availableProjects: [LiveProjectSummary]
    ) -> Int? {
        let owner = ownerAccountID.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !owner.isEmpty else {
            clearBrainstormProjectSelection()
            return nil
        }
        let ownerFingerprint = Self.ownerFingerprint(owner)
        let storedOwner = defaults.string(forKey: Key.brainstormProjectOwner)
        guard let storedOwner else {
            defaults.removeObject(forKey: Key.brainstormProjectID)
            defaults.set(ownerFingerprint, forKey: Key.brainstormProjectOwner)
            return nil
        }
        guard storedOwner == ownerFingerprint else {
            clearBrainstormProjectSelection()
            defaults.set(ownerFingerprint, forKey: Key.brainstormProjectOwner)
            return nil
        }
        defaults.set(ownerFingerprint, forKey: Key.brainstormProjectOwner)
        guard let selected = defaults.object(forKey: Key.brainstormProjectID) as? Int,
              availableProjects.contains(where: { $0.id == selected })
        else {
            defaults.removeObject(forKey: Key.brainstormProjectID)
            return nil
        }
        return selected
    }

    func setBrainstormProjectID(
        _ projectID: Int?,
        ownerAccountID: String,
        availableProjects: [LiveProjectSummary]
    ) -> Int? {
        let owner = ownerAccountID.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !owner.isEmpty else {
            clearBrainstormProjectSelection()
            return nil
        }
        let ownerFingerprint = Self.ownerFingerprint(owner)
        if defaults.string(forKey: Key.brainstormProjectOwner) != ownerFingerprint {
            clearBrainstormProjectSelection()
        }
        defaults.set(ownerFingerprint, forKey: Key.brainstormProjectOwner)
        guard let projectID else {
            defaults.removeObject(forKey: Key.brainstormProjectID)
            return nil
        }
        guard availableProjects.contains(where: { $0.id == projectID }) else {
            defaults.removeObject(forKey: Key.brainstormProjectID)
            return nil
        }
        defaults.set(projectID, forKey: Key.brainstormProjectID)
        return projectID
    }

    func clearBrainstormProjectSelection() {
        defaults.removeObject(forKey: Key.brainstormProjectID)
        defaults.removeObject(forKey: Key.brainstormProjectOwner)
    }

    func clearAccountState() {
        projectID = nil
        clearBrainstormProjectSelection()
    }

    func validatedWebURL(_ rawValue: String) -> URL? {
        communityOrigin?.validatedWebURL(rawValue)
    }

    private static func ownerFingerprint(_ owner: String) -> String {
        SHA256.hash(data: Data(owner.utf8)).map {
            String(format: "%02x", $0)
        }.joined()
    }
}
