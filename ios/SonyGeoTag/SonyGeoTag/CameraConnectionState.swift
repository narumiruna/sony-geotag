import Foundation

enum CameraConnectionState: String {
    case idle
    case bluetoothUnavailable
    case scanning
    case connecting
    case discovering
    case enablingLocation
    case linked
    case stopping
    case stopped
    case failed

    var label: String {
        switch self {
        case .idle:
            "Idle"
        case .bluetoothUnavailable:
            "Bluetooth unavailable"
        case .scanning:
            "Scanning"
        case .connecting:
            "Connecting"
        case .discovering:
            "Discovering services"
        case .enablingLocation:
            "Enabling location link"
        case .linked:
            "Location link active"
        case .stopping:
            "Stopping"
        case .stopped:
            "Stopped"
        case .failed:
            "Failed"
        }
    }
}
