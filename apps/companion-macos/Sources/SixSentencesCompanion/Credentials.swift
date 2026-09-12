import Foundation
import Security

protocol CompanionCredentialProvider: Sendable {
    func bearerToken() throws -> String
}

protocol CompanionCredentialStore: CompanionCredentialProvider {
    func save(token: String) throws
    func delete() throws
    var hasToken: Bool { get }
}

enum CredentialError: LocalizedError {
    case missing
    case invalid
    case keychain(OSStatus)

    var errorDescription: String? {
        switch self {
        case .missing:
            "Connect this Mac to SixSentences in your browser."
        case .invalid:
            "The browser connection did not return valid access. Please reconnect."
        case let .keychain(status):
            "Keychain access failed (\(status))."
        }
    }
}

/// Stores only the bearer credential returned by the one-use browser pairing
/// flow. Non-secret preferences remain in a separate settings store.
final class KeychainCredentialProvider: CompanionCredentialStore, @unchecked Sendable {
    static let shared = KeychainCredentialProvider()

    private let service = "com.sixsentences.companion.api"
    private let account = "companion-bearer-token"

    func bearerToken() throws -> String {
        var query = baseQuery
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        guard status != errSecItemNotFound else { throw CredentialError.missing }
        guard status == errSecSuccess else { throw CredentialError.keychain(status) }
        guard
            let data = result as? Data,
            let token = String(data: data, encoding: .utf8),
            !token.isEmpty
        else {
            throw CredentialError.missing
        }
        return token
    }

    func save(token: String) throws {
        let clean = token.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty else { throw CredentialError.invalid }
        let data = Data(clean.utf8)
        let attributes: [String: Any] = [kSecValueData as String: data]
        let updateStatus = SecItemUpdate(baseQuery as CFDictionary, attributes as CFDictionary)
        if updateStatus == errSecItemNotFound {
            var query = baseQuery
            query[kSecValueData as String] = data
            query[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
            let addStatus = SecItemAdd(query as CFDictionary, nil)
            guard addStatus == errSecSuccess else { throw CredentialError.keychain(addStatus) }
            return
        }
        guard updateStatus == errSecSuccess else { throw CredentialError.keychain(updateStatus) }
    }

    func delete() throws {
        let status = SecItemDelete(baseQuery as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw CredentialError.keychain(status)
        }
    }

    var hasToken: Bool {
        (try? bearerToken()) != nil
    }

    private var baseQuery: [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
    }
}
