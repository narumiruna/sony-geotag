import CoreLocation
import Foundation

final class LocationProvider: NSObject, ObservableObject, CLLocationManagerDelegate {
    @Published private(set) var authorizationStatus: CLAuthorizationStatus
    @Published private(set) var currentLocation: CLLocation?
    @Published private(set) var lastError: String?
    @Published private(set) var isUpdating = false

    private let manager = CLLocationManager()

    override init() {
        authorizationStatus = manager.authorizationStatus
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyBest
        manager.distanceFilter = 5
        manager.pausesLocationUpdatesAutomatically = false
        #if os(iOS)
        manager.allowsBackgroundLocationUpdates = true
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

    func requestAuthorization() {
        switch authorizationStatus {
        case .notDetermined:
            #if os(iOS)
            manager.requestWhenInUseAuthorization()
            #else
            manager.requestAlwaysAuthorization()
            #endif
        #if os(iOS)
        case .authorizedWhenInUse:
            manager.requestAlwaysAuthorization()
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
            manager.startUpdatingLocation()
        #if os(iOS)
        case .authorizedWhenInUse:
            isUpdating = true
            lastError = nil
            manager.startUpdatingLocation()
        #endif
        case .notDetermined:
            requestAuthorization()
        case .denied, .restricted:
            lastError = "Location permission is not available."
        @unknown default:
            lastError = "Unknown location authorization status."
        }
    }

    func stopUpdating() {
        isUpdating = false
        manager.stopUpdatingLocation()
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        authorizationStatus = manager.authorizationStatus
        if isLocationAuthorized {
            startUpdating()
        }
    }

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let location = locations.last else { return }
        currentLocation = location
        lastError = nil
    }

    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        lastError = error.localizedDescription
    }
}
