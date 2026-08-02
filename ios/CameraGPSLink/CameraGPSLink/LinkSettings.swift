import Foundation

enum ConnectionAvailability: String, CaseIterable, Identifiable {
    case whileAppIsOpen
    case continueInBackground

    var id: Self { self }

    var label: String {
        switch self {
        case .whileAppIsOpen:
            "While App Is Open"
        case .continueInBackground:
            "Continue in Background"
        }
    }
}

enum LocationUpdateMode: String, CaseIterable, Identifiable {
    case batterySaver
    case bestAccuracy

    var id: Self { self }

    var label: String {
        switch self {
        case .batterySaver:
            "Battery Saver"
        case .bestAccuracy:
            "Best Accuracy"
        }
    }
}

struct LinkSettings: Equatable {
    var connectionAvailability: ConnectionAvailability
    var locationUpdates: LocationUpdateMode

    static let `default` = LinkSettings(
        connectionAvailability: .whileAppIsOpen,
        locationUpdates: .batterySaver
    )

    var backgroundLinkEnabled: Bool {
        connectionAvailability == .continueInBackground
    }

    var lowPowerModeEnabled: Bool {
        locationUpdates == .batterySaver
    }

    var summary: String {
        let availability = backgroundLinkEnabled ? "Background" : "While Open"
        return "\(availability) · \(locationUpdates.label)"
    }

    var effectPreview: String {
        let delivery = switch connectionAvailability {
        case .whileAppIsOpen:
            "Runs only while Camera GPS Link is open."
        case .continueInBackground:
            "Keeps reconnecting when possible and requires Always Location permission. iOS may pause it after force-quit."
        }
        let updates = switch locationUpdates {
        case .batterySaver:
            "Uses approximate 100 m location and sends about every 2 minutes."
        case .bestAccuracy:
            "Uses the best available GPS accuracy and sends about every 30 seconds, using more battery."
        }
        return "\(delivery) \(updates)"
    }
}

struct LinkSettingsDraft: Equatable {
    let original: LinkSettings
    var value: LinkSettings

    init(current: LinkSettings) {
        original = current
        value = current
    }

    var hasChanges: Bool {
        value != original
    }

    mutating func cancel() {
        value = original
    }
}

enum LinkSettingsKeys {
    static let backgroundLinkEnabled = "backgroundLinkEnabled"
    static let lowPowerModeEnabled = "lowPowerModeEnabled"
}

protocol LinkSettingsStoring {
    func load() throws -> LinkSettings
    func save(_ settings: LinkSettings) throws
}

struct UserDefaultsLinkSettingsStore: LinkSettingsStoring {
    let defaults: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    func load() throws -> LinkSettings {
        let backgroundEnabled = defaults.object(forKey: LinkSettingsKeys.backgroundLinkEnabled) as? Bool ?? false
        let lowPowerEnabled = defaults.object(forKey: LinkSettingsKeys.lowPowerModeEnabled) as? Bool ?? true
        return LinkSettings(
            connectionAvailability: backgroundEnabled ? .continueInBackground : .whileAppIsOpen,
            locationUpdates: lowPowerEnabled ? .batterySaver : .bestAccuracy
        )
    }

    func save(_ settings: LinkSettings) throws {
        defaults.set(settings.backgroundLinkEnabled, forKey: LinkSettingsKeys.backgroundLinkEnabled)
        defaults.set(settings.lowPowerModeEnabled, forKey: LinkSettingsKeys.lowPowerModeEnabled)
    }
}
