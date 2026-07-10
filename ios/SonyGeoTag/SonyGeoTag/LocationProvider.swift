import CoreLocation
import Foundation

enum LocationAuthorizationScope {
    case notDetermined
    case restricted
    case denied
    case whenInUse
    case always
    case unknown
}

enum LocationBackgroundPolicy {
    static func allowsBackgroundLocationUpdates(
        backgroundLinkEnabled: Bool,
        authorizationScope: LocationAuthorizationScope
    ) -> Bool {
        backgroundLinkEnabled && authorizationScope == .always
    }

    static func canRunLocationServices(
        isForeground: Bool,
        allowsBackgroundLocationUpdates: Bool
    ) -> Bool {
        isForeground || allowsBackgroundLocationUpdates
    }

    static func requiresAlwaysAuthorizationWarning(
        backgroundLinkEnabled: Bool,
        authorizationScope: LocationAuthorizationScope
    ) -> Bool {
        backgroundLinkEnabled && authorizationScope == .whenInUse
    }
}

final class LocationProvider: NSObject, ObservableObject, CLLocationManagerDelegate {
    @Published private(set) var authorizationStatus: CLAuthorizationStatus
    @Published private(set) var currentLocation: CLLocation?
    @Published private(set) var lastError: String?
    @Published private(set) var isUpdating = false
    @Published private(set) var updateModeLabel = "Stopped"

    var onLocationUpdate: ((CLLocation) -> Void)?

    private static let backgroundLinkNeedsAlwaysMessage = "Background Link needs Always Location permission for reliable background updates."

    private let manager = CLLocationManager()
    private var backgroundLinkEnabled = false
    private var lowPowerModeEnabled = true
    private var isForeground = true

    override init() {
        authorizationStatus = manager.authorizationStatus
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyBest
        manager.distanceFilter = 5
        manager.pausesLocationUpdatesAutomatically = false
        #if os(iOS)
        manager.allowsBackgroundLocationUpdates = false
        manager.showsBackgroundLocationIndicator = false
        #endif
    }

    var statusLabel: String {
        switch authorizationStatus {
        case .notDetermined:
            "Not requested"
        case .restricted:
            "Restricted"
        case .denied:
            "Denied"
        case .authorizedAlways:
            "Always allowed"
        #if os(iOS)
        case .authorizedWhenInUse:
            "When-in-use allowed"
        #endif
        @unknown default:
            "Unknown"
        }
    }

    var coordinateLabel: String {
        guard let coordinate = currentLocation?.coordinate else {
            return "No fix yet"
        }
        return String(format: "%.7f, %.7f", coordinate.latitude, coordinate.longitude)
    }

    var accuracyLabel: String {
        guard let currentLocation else {
            return "—"
        }
        return String(format: "±%.0f m", currentLocation.horizontalAccuracy)
    }

    private var authorizationScope: LocationAuthorizationScope {
        switch authorizationStatus {
        case .notDetermined:
            .notDetermined
        case .restricted:
            .restricted
        case .denied:
            .denied
        case .authorizedAlways:
            .always
        #if os(iOS)
        case .authorizedWhenInUse:
            .whenInUse
        #endif
        @unknown default:
            .unknown
        }
    }

    private var isLocationAuthorized: Bool {
        authorizationScope == .always || authorizationScope == .whenInUse
    }

    private var allowsBackgroundLocationUpdates: Bool {
        LocationBackgroundPolicy.allowsBackgroundLocationUpdates(
            backgroundLinkEnabled: backgroundLinkEnabled,
            authorizationScope: authorizationScope
        )
    }

    private var backgroundLinkPermissionWarning: String? {
        guard LocationBackgroundPolicy.requiresAlwaysAuthorizationWarning(
            backgroundLinkEnabled: backgroundLinkEnabled,
            authorizationScope: authorizationScope
        ) else {
            return nil
        }
        return Self.backgroundLinkNeedsAlwaysMessage
    }

    func configure(backgroundLinkEnabled: Bool, lowPowerModeEnabled: Bool, isForeground: Bool) {
        self.backgroundLinkEnabled = backgroundLinkEnabled
        self.lowPowerModeEnabled = lowPowerModeEnabled
        self.isForeground = isForeground
        applyLocationSettings()
        if isUpdating {
            startLocationServices()
        }
        refreshBackgroundPermissionWarning()
    }

    func requestAuthorization(preferAlways: Bool = false) {
        switch authorizationStatus {
        case .notDetermined:
            #if os(iOS)
            manager.requestWhenInUseAuthorization()
            #else
            manager.requestAlwaysAuthorization()
            #endif
        #if os(iOS)
        case .authorizedWhenInUse:
            if preferAlways || backgroundLinkEnabled {
                manager.requestAlwaysAuthorization()
            } else {
                startUpdating()
            }
        #endif
        case .authorizedAlways:
            startUpdating()
        case .denied, .restricted:
            lastError = "Enable Location permission in iOS Settings."
        @unknown default:
            lastError = "Unknown location authorization status."
        }
    }

    func startUpdating() {
        switch authorizationStatus {
        case .authorizedAlways:
            isUpdating = true
            lastError = nil
            applyLocationSettings()
            startLocationServices()
        #if os(iOS)
        case .authorizedWhenInUse:
            isUpdating = true
            lastError = backgroundLinkPermissionWarning
            applyLocationSettings()
            startLocationServices()
        #endif
        case .notDetermined:
            requestAuthorization(preferAlways: backgroundLinkEnabled)
        case .denied, .restricted:
            lastError = "Location permission is not available."
        @unknown default:
            lastError = "Unknown location authorization status."
        }
    }

    func stopUpdating() {
        isUpdating = false
        updateModeLabel = "Stopped"
        manager.stopUpdatingLocation()
        manager.stopMonitoringSignificantLocationChanges()
        #if os(iOS)
        manager.allowsBackgroundLocationUpdates = false
        manager.showsBackgroundLocationIndicator = false
        #endif
    }

    private func applyLocationSettings() {
        let allowBackgroundUpdates = allowsBackgroundLocationUpdates

        #if os(iOS)
        // Only Always authorization can hide the background-location blue indicator
        // reliably. With When-In-Use authorization, keep updates foreground-only.
        manager.allowsBackgroundLocationUpdates = allowBackgroundUpdates
        manager.showsBackgroundLocationIndicator = false
        #endif

        if isUpdating,
           !LocationBackgroundPolicy.canRunLocationServices(
               isForeground: isForeground,
               allowsBackgroundLocationUpdates: allowBackgroundUpdates
           ) {
            manager.desiredAccuracy = kCLLocationAccuracyHundredMeters
            manager.distanceFilter = 50
            updateModeLabel = "Paused in background"
        } else if lowPowerModeEnabled || !isForeground {
            manager.desiredAccuracy = kCLLocationAccuracyHundredMeters
            manager.distanceFilter = 50
            updateModeLabel = isUpdating ? "Low power / significant changes" : "Low power ready"
        } else {
            manager.desiredAccuracy = kCLLocationAccuracyBest
            manager.distanceFilter = 5
            updateModeLabel = isUpdating ? "High accuracy" : "High accuracy ready"
        }
    }

    private func startLocationServices() {
        let allowBackgroundUpdates = allowsBackgroundLocationUpdates
        guard LocationBackgroundPolicy.canRunLocationServices(
            isForeground: isForeground,
            allowsBackgroundLocationUpdates: allowBackgroundUpdates
        ) else {
            manager.stopUpdatingLocation()
            manager.stopMonitoringSignificantLocationChanges()
            updateModeLabel = "Paused in background"
            return
        }

        if allowBackgroundUpdates || lowPowerModeEnabled {
            manager.startMonitoringSignificantLocationChanges()
        } else {
            manager.stopMonitoringSignificantLocationChanges()
        }
        manager.startUpdatingLocation()
    }

    private func refreshBackgroundPermissionWarning(clearResolvedErrors: Bool = false) {
        if let warning = backgroundLinkPermissionWarning {
            lastError = warning
        } else if clearResolvedErrors || lastError == Self.backgroundLinkNeedsAlwaysMessage {
            lastError = nil
        }
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        authorizationStatus = manager.authorizationStatus
        #if os(iOS)
        if backgroundLinkEnabled, authorizationStatus == .authorizedWhenInUse {
            manager.requestAlwaysAuthorization()
        }
        #endif
        if isLocationAuthorized {
            startUpdating()
        } else if authorizationStatus == .denied || authorizationStatus == .restricted {
            stopUpdating()
            lastError = "Location permission is not available."
        } else {
            applyLocationSettings()
        }
    }

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let location = locations.last else { return }
        currentLocation = location
        refreshBackgroundPermissionWarning(clearResolvedErrors: true)
        onLocationUpdate?(location)
    }

    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        lastError = error.localizedDescription
    }
}
