import CoreLocation
import Foundation

enum LocationPermission: Equatable {
    case notDetermined
    case denied
    case restricted
    case whenInUse
    case always
    case unknown

    init(_ status: CLAuthorizationStatus) {
        switch status {
        case .notDetermined:
            self = .notDetermined
        case .denied:
            self = .denied
        case .restricted:
            self = .restricted
        case .authorizedAlways:
            self = .always
        #if os(iOS)
        case .authorizedWhenInUse:
            self = .whenInUse
        #endif
        @unknown default:
            self = .unknown
        }
    }

    var label: String {
        switch self {
        case .notDetermined:
            "Not requested"
        case .denied:
            "Denied"
        case .restricted:
            "Restricted"
        case .whenInUse:
            "While using app"
        case .always:
            "Always"
        case .unknown:
            "Unknown"
        }
    }

    var allowsForegroundLocation: Bool {
        self == .whenInUse || self == .always
    }
}

enum GeotaggingPhase: Equatable {
    case notConnected
    case requestingPermission
    case searching
    case connecting
    case preparing
    case waitingForLocation
    case sendingFirstLocation
    case ready
    case waitingInBackground
    case stopping
    case stopped
    case needsAttention
}

enum GeotaggingAction: Equatable {
    case start
    case cancel
    case retry
    case stop
    case sendNow
    case openSettings
    case requestBackgroundPermission
}

struct ReadinessItem: Identifiable, Equatable {
    let id: String
    let title: String
    let detail: String
    let symbolName: String
    let isReady: Bool
}

struct GeotaggingSnapshot: Equatable {
    var cameraState: CameraConnectionState
    var cameraName: String?
    var targetName: String
    var packetsSent: Int
    var lastSentAt: Date?
    var locationPermission: LocationPermission
    var hasLocation: Bool
    var horizontalAccuracy: CLLocationAccuracy?
    var backgroundEnabled: Bool
    var isForeground: Bool
    var pendingReconnectArmed: Bool
    var transientError: String?
    var isRequestingPermission = false
}

struct GeotaggingViewState: Equatable {
    var phase: GeotaggingPhase
    var title: String
    var message: String
    var readiness: [ReadinessItem]
    var primaryAction: GeotaggingAction?
    var primaryActionLabel: String?
    var secondaryAction: GeotaggingAction?
    var secondaryActionLabel: String?
    var showsProgress: Bool
    var lastUpdateText: String
    var notice: String?
    var noticeAction: GeotaggingAction?

    static func make(from snapshot: GeotaggingSnapshot, now: Date = Date()) -> GeotaggingViewState {
        let phase = phase(for: snapshot, now: now)
        let content = content(for: phase, snapshot: snapshot, now: now)
        let primary = primaryAction(for: phase, snapshot: snapshot)
        let lastUpdate = relativeUpdate(snapshot.lastSentAt, now: now)
        let needsBackgroundPermission = snapshot.backgroundEnabled
            && snapshot.locationPermission != .always
            && snapshot.locationPermission.allowsForegroundLocation

        return GeotaggingViewState(
            phase: phase,
            title: content.title,
            message: snapshot.transientError ?? content.message,
            readiness: readiness(for: snapshot, lastUpdate: lastUpdate),
            primaryAction: primary,
            primaryActionLabel: label(for: primary),
            secondaryAction: phase == .ready ? .sendNow : nil,
            secondaryActionLabel: phase == .ready ? "Send Current Location" : nil,
            showsProgress: [.requestingPermission, .searching, .connecting, .preparing, .sendingFirstLocation, .stopping].contains(phase),
            lastUpdateText: lastUpdate,
            notice: needsBackgroundPermission ? "Background Permission Needed" : nil,
            noticeAction: needsBackgroundPermission ? .requestBackgroundPermission : nil
        )
    }

    private static func phase(for snapshot: GeotaggingSnapshot, now: Date) -> GeotaggingPhase {
        if snapshot.isRequestingPermission {
            return .requestingPermission
        }
        if snapshot.locationPermission == .denied || snapshot.locationPermission == .restricted {
            return .needsAttention
        }
        if snapshot.backgroundEnabled,
           snapshot.pendingReconnectArmed,
           snapshot.cameraState == .connecting || snapshot.cameraState == .scanning {
            return .waitingInBackground
        }
        switch snapshot.cameraState {
        case .idle:
            return snapshot.locationPermission == .notDetermined ? .notConnected : .notConnected
        case .bluetoothUnavailable, .failed:
            return .needsAttention
        case .scanning:
            return .searching
        case .connecting:
            return .connecting
        case .discovering, .enablingLocation:
            return .preparing
        case .linked:
            guard snapshot.packetsSent > 0, let lastSentAt = snapshot.lastSentAt else {
                return snapshot.hasLocation ? .sendingFirstLocation : .waitingForLocation
            }
            if now.timeIntervalSince(lastSentAt) > 5 * 60 {
                return .needsAttention
            }
            return .ready
        case .stopping:
            return .stopping
        case .stopped:
            return .stopped
        }
    }

    private static func content(
        for phase: GeotaggingPhase,
        snapshot: GeotaggingSnapshot,
        now: Date
    ) -> (title: String, message: String) {
        switch phase {
        case .notConnected:
            return ("Not Connected", "Start when your camera is on and ready for its Bluetooth location link.")
        case .requestingPermission:
            return ("Location Permission", "Confirm location access to start geotagging.")
        case .searching:
            return ("Looking for Camera…", "Keep the camera nearby and ready for its Bluetooth location link.")
        case .connecting:
            return ("Connecting…", "Connecting securely to \(snapshot.cameraName ?? snapshot.targetName).")
        case .preparing:
            return ("Preparing Location…", "Setting up the camera to receive iPhone location updates.")
        case .waitingForLocation:
            return ("Waiting for iPhone Location", "The camera is connected. Move to an open area if a GPS fix takes too long.")
        case .sendingFirstLocation:
            return ("Sending First Location…", "Wait for confirmation before taking geotagged photos.")
        case .ready:
            return ("Ready to Geotag", "New photos can use the latest location sent from this iPhone.")
        case .waitingInBackground:
            return ("Waiting for Camera", "Camera GPS Link will reconnect when the remembered camera becomes available.")
        case .stopping:
            return ("Stopping…", "Closing the camera location link safely.")
        case .stopped:
            return ("Stopped", "Location updates are off. Start again whenever you are ready.")
        case .needsAttention:
            if snapshot.locationPermission == .denied || snapshot.locationPermission == .restricted {
                return ("Location Access Needed", "Location access is off. Review permission in iOS Settings, then retry.")
            }
            if snapshot.cameraState == .bluetoothUnavailable {
                return ("Bluetooth Unavailable", "Turn on Bluetooth and keep Camera GPS Link open, then retry.")
            }
            if snapshot.cameraState == .linked, let lastSentAt = snapshot.lastSentAt,
               now.timeIntervalSince(lastSentAt) > 5 * 60 {
                return ("Location Update Delayed", "The camera’s last location is out of date. Send again or reconnect.")
            }
            return ("Connection Needs Attention", "Check the camera and try connecting again.")
        }
    }

    private static func primaryAction(
        for phase: GeotaggingPhase,
        snapshot: GeotaggingSnapshot
    ) -> GeotaggingAction? {
        switch phase {
        case .notConnected, .stopped:
            return .start
        case .requestingPermission, .searching, .connecting, .preparing:
            return .cancel
        case .waitingForLocation, .sendingFirstLocation, .ready:
            return .stop
        case .needsAttention:
            if snapshot.locationPermission == .denied || snapshot.locationPermission == .restricted {
                return .openSettings
            }
            return .retry
        case .waitingInBackground, .stopping:
            return nil
        }
    }

    private static func label(for action: GeotaggingAction?) -> String? {
        switch action {
        case .start:
            "Start Geotagging"
        case .cancel:
            "Cancel"
        case .retry:
            "Retry"
        case .stop:
            "Stop Geotagging"
        case .sendNow:
            "Send Current Location"
        case .openSettings:
            "Review Location Permission"
        case .requestBackgroundPermission:
            "Allow Background Location"
        case nil:
            nil
        }
    }

    private static func readiness(for snapshot: GeotaggingSnapshot, lastUpdate: String) -> [ReadinessItem] {
        let cameraReady = snapshot.cameraState == .linked
        let cameraDetail = cameraReady
            ? "Connected · \(snapshot.cameraName ?? snapshot.targetName)"
            : cameraStatus(snapshot.cameraState)
        let locationReady = snapshot.hasLocation && snapshot.locationPermission.allowsForegroundLocation
        let accuracy = snapshot.horizontalAccuracy.map { " · ±\(Int($0.rounded())) m" } ?? ""
        let locationDetail = locationReady ? "Ready\(accuracy)" : snapshot.locationPermission.label
        let sent = snapshot.packetsSent > 0 && snapshot.lastSentAt != nil

        return [
            ReadinessItem(
                id: "camera",
                title: "Camera",
                detail: cameraDetail,
                symbolName: cameraReady ? "camera.fill" : "camera",
                isReady: cameraReady
            ),
            ReadinessItem(
                id: "location",
                title: "iPhone Location",
                detail: locationDetail,
                symbolName: locationReady ? "location.fill" : "location",
                isReady: locationReady
            ),
            ReadinessItem(
                id: "update",
                title: "Last Camera Update",
                detail: sent ? lastUpdate : "Not sent yet",
                symbolName: sent ? "checkmark.circle.fill" : "clock",
                isReady: sent
            ),
        ]
    }

    private static func cameraStatus(_ state: CameraConnectionState) -> String {
        switch state {
        case .idle, .stopped:
            "Not connected"
        case .bluetoothUnavailable:
            "Bluetooth unavailable"
        case .scanning:
            "Searching"
        case .connecting:
            "Connecting"
        case .discovering, .enablingLocation:
            "Preparing"
        case .linked:
            "Connected"
        case .stopping:
            "Stopping"
        case .failed:
            "Needs attention"
        }
    }

    private static func relativeUpdate(_ date: Date?, now: Date) -> String {
        guard let date else { return "Never" }
        let seconds = max(0, Int(now.timeIntervalSince(date)))
        if seconds < 5 { return "Just now" }
        if seconds < 60 { return "\(seconds) seconds ago" }
        let minutes = seconds / 60
        if minutes < 60 { return "\(minutes) minute\(minutes == 1 ? "" : "s") ago" }
        let hours = minutes / 60
        return "\(hours) hour\(hours == 1 ? "" : "s") ago"
    }
}
