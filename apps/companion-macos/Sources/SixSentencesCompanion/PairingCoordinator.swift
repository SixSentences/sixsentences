import CryptoKit
import Foundation
import Security

struct PendingPairing: Codable, Equatable, Sendable {
    let verifier: String
    let state: String
    let createdAt: Date
}

enum PairingError: LocalizedError {
    case noPendingPairing
    case stateMismatch
    case expired
    case invalidCallback

    var errorDescription: String? {
        switch self {
        case .noPendingPairing: "Start the connection from this Mac first."
        case .stateMismatch: "The connection response did not match this Mac."
        case .expired: "The connection request expired. Start again."
        case .invalidCallback: "The connection response is invalid."
        }
    }
}

protocol CompanionPairingStore: Sendable {
    func begin(webBaseURL: String) throws -> URL
    func consumeCallback(_ url: URL) throws -> (code: String, verifier: String)
    func clear() throws
}

/// Desktop-initiated PKCE state. The verifier is short lived and protected in
/// Keychain; the browser sees only its SHA-256 challenge and random state.
final class PairingCoordinator: CompanionPairingStore, @unchecked Sendable {
    static let shared = PairingCoordinator()

    private let service = "com.sixsentences.companion.pairing"
    private let account = "pending-pkce"
    private let encoder = JSONEncoder()
    private let decoder = JSONDecoder()

    func begin(webBaseURL: String) throws -> URL {
        let baseURL = try CompanionAPIClient.validateBaseURL(webBaseURL)
        let verifier = Self.randomBase64URL(byteCount: 48)
        let state = Self.randomBase64URL(byteCount: 32)
        let digest = SHA256.hash(data: Data(verifier.utf8))
        let challenge = Self.base64URL(Data(digest))
        try save(PendingPairing(verifier: verifier, state: state, createdAt: Date()))

        return try Self.browserURL(
            webBaseURL: baseURL.absoluteString,
            state: state,
            codeChallenge: challenge
        )
    }

    static func browserURL(
        webBaseURL: String,
        state: String,
        codeChallenge: String
    ) throws -> URL {
        let baseURL = try CompanionAPIClient.validateBaseURL(webBaseURL)
        var components = URLComponents(
            url: baseURL.appendingPathComponent("interviews"),
            resolvingAgainstBaseURL: false
        )
        components?.queryItems = [
            URLQueryItem(name: "tab", value: "live"),
            URLQueryItem(name: "companion_pair", value: "1"),
            URLQueryItem(name: "state", value: state),
            URLQueryItem(name: "code_challenge", value: codeChallenge),
            URLQueryItem(name: "code_challenge_method", value: "S256"),
        ]
        guard let url = components?.url else { throw APIClientError.invalidBaseURL }
        return url
    }

    func consumeCallback(_ url: URL) throws -> (code: String, verifier: String) {
        guard
            url.scheme?.lowercased() == "sixsentences",
            url.host?.lowercased() == "companion",
            url.path == "/pair",
            let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
            let code = components.queryItems?.first(where: { $0.name == "code" })?.value,
            let returnedState = components.queryItems?.first(where: { $0.name == "state" })?.value,
            code.count >= 20,
            code.count <= 200
        else { throw PairingError.invalidCallback }
        let pending = try load()
        do {
            try Self.validateCallback(pending: pending, returnedState: returnedState)
        } catch PairingError.stateMismatch {
            try? clear()
            throw PairingError.stateMismatch
        } catch PairingError.expired {
            try? clear()
            throw PairingError.expired
        } catch {
            throw error
        }
        // Retain the verifier only until the exchange succeeds, allowing a
        // transient network failure to retry without restarting the browser.
        return (code, pending.verifier)
    }

    func clear() throws {
        let status = SecItemDelete(baseQuery as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw CredentialError.keychain(status)
        }
    }

    static func validateCallback(
        pending: PendingPairing,
        returnedState: String,
        now: Date = Date()
    ) throws {
        guard pending.state.constantTimeEquals(returnedState) else {
            throw PairingError.stateMismatch
        }
        guard now.timeIntervalSince(pending.createdAt) <= 10 * 60 else {
            throw PairingError.expired
        }
    }

    private func save(_ pending: PendingPairing) throws {
        try clear()
        var query = baseQuery
        query[kSecValueData as String] = try encoder.encode(pending)
        query[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        let status = SecItemAdd(query as CFDictionary, nil)
        guard status == errSecSuccess else { throw CredentialError.keychain(status) }
    }

    private func load() throws -> PendingPairing {
        var query = baseQuery
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        guard status != errSecItemNotFound else { throw PairingError.noPendingPairing }
        guard status == errSecSuccess, let data = result as? Data else {
            throw CredentialError.keychain(status)
        }
        return try decoder.decode(PendingPairing.self, from: data)
    }

    private var baseQuery: [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
    }

    private static func randomBase64URL(byteCount: Int) -> String {
        var data = Data(count: byteCount)
        let status = data.withUnsafeMutableBytes { bytes in
            SecRandomCopyBytes(kSecRandomDefault, byteCount, bytes.baseAddress!)
        }
        precondition(status == errSecSuccess, "Secure random generation failed")
        return base64URL(data)
    }

    private static func base64URL(_ data: Data) -> String {
        data.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
}

private extension String {
    func constantTimeEquals(_ other: String) -> Bool {
        let lhs = Array(utf8)
        let rhs = Array(other.utf8)
        guard lhs.count == rhs.count else { return false }
        var difference: UInt8 = 0
        for index in lhs.indices { difference |= lhs[index] ^ rhs[index] }
        return difference == 0
    }
}
