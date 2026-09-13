import Foundation

enum CompanionSessionState: Equatable, Sendable {
    case idle
    case awaitingConsent
    case starting
    case recording(sessionID: String)
    case stopping(sessionID: String)
    case queuedOffline(sessionID: String)
    case completed(sessionID: String)
    case failed(message: String)
}

enum CompanionSessionEvent: Equatable, Sendable {
    case startRequested
    case consentDeclined
    case consentConfirmed
    case sessionCreated(id: String)
    case startFailed(message: String)
    case stopRequested
    case pendingUpload
    case completionSucceeded
    case completionFailed(message: String)
    case reset
}

struct CompanionSessionMachine: Sendable {
    private(set) var state: CompanionSessionState = .idle

    @discardableResult
    mutating func apply(_ event: CompanionSessionEvent) -> CompanionSessionState {
        switch (state, event) {
        case (.idle, .startRequested):
            state = .awaitingConsent
        case (.awaitingConsent, .consentDeclined), (_, .reset):
            state = .idle
        case (.awaitingConsent, .consentConfirmed):
            state = .starting
        case let (.starting, .sessionCreated(id)):
            state = .recording(sessionID: id)
        case let (.starting, .startFailed(message)):
            state = .failed(message: message)
        case let (.recording(id), .stopRequested):
            state = .stopping(sessionID: id)
        case let (.stopping(id), .pendingUpload):
            state = .queuedOffline(sessionID: id)
        case let (.stopping(id), .completionSucceeded),
             let (.queuedOffline(id), .completionSucceeded):
            state = .completed(sessionID: id)
        case let (.stopping, .completionFailed(message)),
             let (.queuedOffline, .completionFailed(message)):
            state = .failed(message: message)
        default:
            break
        }
        return state
    }
}
