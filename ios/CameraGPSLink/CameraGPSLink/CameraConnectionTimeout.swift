import Foundation

enum ForegroundConnectionStage: Equatable {
    case scanning
    case connecting
    case discovering
    case preparing

    var userFacingName: String {
        switch self {
        case .scanning:
            "Camera search"
        case .connecting:
            "Camera connection"
        case .discovering:
            "Service discovery"
        case .preparing:
            "Location setup"
        }
    }
}

struct ForegroundConnectionTimeoutPolicy: Equatable {
    var scanTimeout: TimeInterval = 15
    var connectTimeout: TimeInterval = 15
    var discoveryTimeout: TimeInterval = 15
    var preparationTimeout: TimeInterval = 45

    func timeout(for stage: ForegroundConnectionStage) -> TimeInterval {
        switch stage {
        case .scanning:
            scanTimeout
        case .connecting:
            connectTimeout
        case .discovering:
            discoveryTimeout
        case .preparing:
            preparationTimeout
        }
    }
}

final class ForegroundConnectionTimeoutSession {
    private let policy: ForegroundConnectionTimeoutPolicy
    private let scheduler: ConnectionTimeoutScheduler
    private let onTimeout: (ForegroundConnectionStage) -> Void
    private var token: ConnectionTimeoutCancellable?
    private var attemptID: UUID?
    private var scheduledTimeoutID: UUID?

    init(
        policy: ForegroundConnectionTimeoutPolicy,
        scheduler: ConnectionTimeoutScheduler,
        onTimeout: @escaping (ForegroundConnectionStage) -> Void
    ) {
        self.policy = policy
        self.scheduler = scheduler
        self.onTimeout = onTimeout
    }

    var isActive: Bool { attemptID != nil }

    func begin() {
        end()
        attemptID = UUID()
    }

    func transition(to stage: ForegroundConnectionStage) {
        guard let attemptID else { return }
        token?.cancel()
        let timeoutID = UUID()
        scheduledTimeoutID = timeoutID
        token = scheduler.schedule(policy.timeout(for: stage)) { [weak self] in
            guard let self,
                  self.attemptID == attemptID,
                  self.scheduledTimeoutID == timeoutID
            else { return }
            self.token = nil
            self.attemptID = nil
            self.scheduledTimeoutID = nil
            self.onTimeout(stage)
        }
    }

    func end() {
        attemptID = nil
        scheduledTimeoutID = nil
        token?.cancel()
        token = nil
    }
}

protocol ConnectionTimeoutCancellable: AnyObject {
    func cancel()
}

struct ConnectionTimeoutScheduler {
    var schedule: (_ delay: TimeInterval, _ action: @escaping () -> Void) -> ConnectionTimeoutCancellable

    static let live = ConnectionTimeoutScheduler { delay, action in
        TimerConnectionTimeoutToken(delay: delay, action: action)
    }
}

private final class TimerConnectionTimeoutToken: ConnectionTimeoutCancellable {
    private var timer: Timer?

    init(delay: TimeInterval, action: @escaping () -> Void) {
        timer = Timer.scheduledTimer(withTimeInterval: delay, repeats: false) { [weak self] _ in
            self?.timer = nil
            action()
        }
    }

    func cancel() {
        timer?.invalidate()
        timer = nil
    }

    deinit {
        cancel()
    }
}
