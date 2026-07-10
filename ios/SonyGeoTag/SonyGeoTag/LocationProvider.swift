import CoreLocation
import Foundation

final class LocationProvider: NSObject, ObservableObject, CLLocationManagerDelegate {
    @Published private(set) var authorizationStatus: CLAuthorizationStatus
    @Published private(set) var currentLocation: CLLocation?
    @Published private(set) var lastError: String?
    @Published private(set) var isUpdating = false
    @Published private(set) var updateModeLabel = "Stopped"

    var onLocationUpdate: ((CLLocation) -> Void)?

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

    private var isLocationAuthorized: Bool {
        #if os(iOS)
        authorizationStatus == .authorizedAlways || authorizationStatus == .authorizedWhenInUse
        #else
        authorizationStatus == .authorizedAlways
        #endif
    }

    func configure(backgroundLinkEnabled: Bool, lowPowerModeEnabled: Bool, isForeground: Bool) {
        self.backgroundLinkEnabled = backgroundLinkEnabled
        self.lowPowerModeEnabled = lowPowerModeEnabled
        self.isForeground = isForeground
        applyLocationSettings()
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
            lastError = backgroundLinkEnabled
                ? "Background Link needs Always Location permission for reliable background updates."
                : nil
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
    }

    private func applyLocationSettings() {
        #if os(iOS)
        manager.allowsBackgroundLocationUpdates = backgroundLinkEnabled
        // Keep the iOS background-location blue indicator hidden while still allowing
        // background updates when Background Link is enabled.
        manager.showsBackgroundLocationIndicator = false
        #endif

        if lowPowerModeEnabled || !isForeground {
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
        if backgroundLinkEnabled || lowPowerModeEnabled {
            manager.startMonitoringSignificantLocationChanges()
        } else {
            manager.stopMonitoringSignificantLocationChanges()
        }
        manager.startUpdatingLocation()
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
        }
    }

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let location = locations.last else { return }
        currentLocation = location
        lastError = nil
        onLocationUpdate?(location)
    }

    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        lastError = error.localizedDescription
    }
}
