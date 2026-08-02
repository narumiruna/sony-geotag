import BackgroundTasks
import Combine
import CoreLocation
import Foundation
#if canImport(UIKit)
import UIKit
#endif

struct CameraServiceSnapshot: Equatable {
    var state: CameraConnectionState
    var discoveredCameraName: String?
    var targetName: String
    var packetsSent: Int
    var lastSentAt: Date?
    var includeTimezone: Bool
    var dd21ConfigHex: String?
    var lastError: String?
    var pendingReconnectArmed: Bool
    var rememberedPeripheralID: String?
    var updateInterval: TimeInterval
}

struct LocationServiceSnapshot {
    var permission: LocationPermission
    var currentLocation: CLLocation?
    var isUpdating: Bool
    var lastError: String?
    var updateModeLabel = "Stopped"
}

@MainActor
protocol CameraLinkServicing: AnyObject {
    var onChange: (() -> Void)? { get set }
    var snapshot: CameraServiceSnapshot { get }

    func configure(settings: LinkSettings)
    func setLocationProvider(_ provider: @escaping () -> CLLocation?)
    func startForegroundLink()
    func resumeBackgroundLink()
    func cancelCurrentAttempt()
    func stopLink()
    func sendLocationNow()
    func sendLocationIfDue()
}

@MainActor
protocol LocationServicing: AnyObject {
    var onChange: (() -> Void)? { get set }
    var snapshot: LocationServiceSnapshot { get }

    func configure(settings: LinkSettings, isForeground: Bool)
    func requestWhenInUseAuthorization()
    func requestAlwaysAuthorization()
    func startUpdating()
    func stopUpdating()
}

@MainActor
final class CameraBLEServiceAdapter: CameraLinkServicing {
    var onChange: (() -> Void)?
    let manager: CameraBLEManager
    private var changeToken: AnyCancellable?
    private var locationProvider: (() -> CLLocation?) = { nil }

    init(manager: CameraBLEManager) {
        self.manager = manager
        changeToken = manager.objectWillChange.sink { [weak self] _ in
            DispatchQueue.main.async {
                self?.onChange?()
            }
        }
    }

    var snapshot: CameraServiceSnapshot {
        CameraServiceSnapshot(
            state: manager.state,
            discoveredCameraName: manager.discoveredCameraName,
            targetName: manager.targetName,
            packetsSent: manager.packetsSent,
            lastSentAt: manager.lastSentAt,
            includeTimezone: manager.includeTimezone,
            dd21ConfigHex: manager.dd21ConfigHex,
            lastError: manager.lastError,
            pendingReconnectArmed: manager.pendingReconnectArmed,
            rememberedPeripheralID: manager.rememberedPeripheralID,
            updateInterval: manager.updateInterval
        )
    }

    func configure(settings: LinkSettings) {
        manager.configure(
            backgroundLinkEnabled: settings.backgroundLinkEnabled,
            lowPowerModeEnabled: settings.lowPowerModeEnabled
        )
    }

    func setLocationProvider(_ provider: @escaping () -> CLLocation?) {
        locationProvider = provider
        manager.setLocationProvider(provider)
    }

    func startForegroundLink() {
        manager.startLink(locationProvider: locationProvider)
    }

    func resumeBackgroundLink() {
        manager.resumeBackgroundLink(locationProvider: locationProvider)
    }

    func cancelCurrentAttempt() {
        manager.cancelCurrentAttempt()
    }

    func stopLink() {
        manager.stopLink()
    }

    func sendLocationNow() {
        manager.sendLocationNow()
    }

    func sendLocationIfDue() {
        manager.sendLocationIfDue()
    }
}

@MainActor
final class CoreLocationServiceAdapter: LocationServicing {
    var onChange: (() -> Void)?
    let provider: LocationProvider
    private var changeToken: AnyCancellable?

    init(provider: LocationProvider) {
        self.provider = provider
        changeToken = provider.objectWillChange.sink { [weak self] _ in
            DispatchQueue.main.async {
                self?.onChange?()
            }
        }
    }

    var snapshot: LocationServiceSnapshot {
        LocationServiceSnapshot(
            permission: LocationPermission(provider.authorizationStatus),
            currentLocation: provider.currentLocation,
            isUpdating: provider.isUpdating,
            lastError: provider.lastError,
            updateModeLabel: provider.updateModeLabel
        )
    }

    func configure(settings: LinkSettings, isForeground: Bool) {
        provider.configure(
            backgroundLinkEnabled: settings.backgroundLinkEnabled,
            lowPowerModeEnabled: settings.lowPowerModeEnabled,
            isForeground: isForeground
        )
    }

    func requestWhenInUseAuthorization() {
        provider.requestAuthorization(preferAlways: false)
    }

    func requestAlwaysAuthorization() {
        provider.requestAuthorization(preferAlways: true)
    }

    func startUpdating() {
        provider.startUpdating()
    }

    func stopUpdating() {
        provider.stopUpdating()
    }
}

@MainActor
final class CameraGPSLinkAppModel: ObservableObject {
    static func makeForCurrentProcess() -> CameraGPSLinkAppModel {
        #if DEBUG
        if let fixture = UITestAppModelFactory.makeFromEnvironment() {
            return fixture
        }
        #endif
        return CameraGPSLinkAppModel()
    }

    @Published private(set) var settings: LinkSettings
    @Published private(set) var viewState: GeotaggingViewState

    let diagnosticsStore: DiagnosticsLogStore

    private let cameraService: CameraLinkServicing
    private let locationService: LocationServicing
    private let settingsStore: LinkSettingsStoring
    private let now: () -> Date
    private let openSettingsAction: () -> Void
    private let backgroundRefreshIdentifier = "dev.narumi.cameragpslink.refresh"
    private var pendingStart = false
    private var isForeground = true
    private var lastHandledForeground: Bool?
    private var transientError: String?
    private var didRegisterBackgroundTasks = false
    private var backgroundTaskCompletion: DispatchWorkItem?
    private var isProductionRuntime = false

    var cameraSnapshot: CameraServiceSnapshot { cameraService.snapshot }
    var locationSnapshot: LocationServiceSnapshot { locationService.snapshot }

    convenience init() {
        let diagnostics = DiagnosticsLogStore()
        let locationProvider = LocationProvider()
        let cameraManager = CameraBLEManager(diagnosticsStore: diagnostics)
        let locationAdapter = CoreLocationServiceAdapter(provider: locationProvider)
        let cameraAdapter = CameraBLEServiceAdapter(manager: cameraManager)
        self.init(
            cameraService: cameraAdapter,
            locationService: locationAdapter,
            settingsStore: UserDefaultsLinkSettingsStore(),
            diagnosticsStore: diagnostics,
            now: Date.init,
            openSettings: {
                #if canImport(UIKit)
                guard let url = URL(string: UIApplication.openSettingsURLString) else { return }
                UIApplication.shared.open(url)
                #endif
            }
        )
        isProductionRuntime = true
        registerBackgroundTasks()
    }

    init(
        cameraService: CameraLinkServicing,
        locationService: LocationServicing,
        settingsStore: LinkSettingsStoring,
        diagnosticsStore: DiagnosticsLogStore,
        now: @escaping () -> Date,
        openSettings: @escaping () -> Void
    ) {
        self.cameraService = cameraService
        self.locationService = locationService
        self.settingsStore = settingsStore
        self.diagnosticsStore = diagnosticsStore
        self.now = now
        self.openSettingsAction = openSettings

        let loadedSettings: LinkSettings
        let initialError: String?
        do {
            loadedSettings = try settingsStore.load()
            initialError = nil
        } catch {
            loadedSettings = .default
            initialError = "Saved link settings couldn’t be loaded. Default settings are active."
        }
        settings = loadedSettings
        transientError = initialError
        viewState = GeotaggingViewState.make(
            from: Self.makeSnapshot(
                camera: cameraService.snapshot,
                location: locationService.snapshot,
                settings: loadedSettings,
                isForeground: true,
                pendingStart: false,
                transientError: initialError
            ),
            now: now()
        )

        cameraService.onChange = { [weak self] in
            self?.serviceDidChange()
        }
        locationService.onChange = { [weak self] in
            self?.serviceDidChange()
        }
        cameraService.setLocationProvider { [weak locationService] in
            locationService?.snapshot.currentLocation
        }
        cameraService.configure(settings: settings)
        locationService.configure(settings: settings, isForeground: true)
        refreshViewState()
    }

    func handleScenePhase(isForeground: Bool) {
        guard lastHandledForeground != isForeground else { return }
        lastHandledForeground = isForeground
        self.isForeground = isForeground
        locationService.configure(settings: settings, isForeground: isForeground)

        if settings.backgroundLinkEnabled {
            if locationService.snapshot.permission.allowsForegroundLocation {
                locationService.startUpdating()
                cameraService.resumeBackgroundLink()
            }
            scheduleBackgroundRefresh()
        }
        refreshViewState()
    }

    func perform(_ action: GeotaggingAction) {
        switch action {
        case .start:
            startGeotagging()
        case .cancel:
            cancelCurrentAttempt()
        case .retry:
            retry()
        case .stop:
            stopGeotagging()
        case .sendNow:
            sendLocationNow()
        case .openSettings:
            openSettings()
        case .requestBackgroundPermission:
            requestBackgroundPermission()
        }
    }

    func startGeotagging() {
        transientError = nil
        switch locationService.snapshot.permission {
        case .notDetermined:
            pendingStart = true
            refreshViewState()
            locationService.requestWhenInUseAuthorization()
        case .whenInUse, .always:
            beginForegroundLink()
        case .denied, .restricted:
            pendingStart = false
            transientError = "Location access is off. Review permission in iOS Settings, then retry."
            refreshViewState()
        case .unknown:
            pendingStart = false
            transientError = "Location permission is unavailable. Try again or review iOS Settings."
            refreshViewState()
        }
    }

    func cancelCurrentAttempt() {
        pendingStart = false
        transientError = nil
        cameraService.cancelCurrentAttempt()
        locationService.stopUpdating()
        refreshViewState()
    }

    func stopGeotagging() {
        pendingStart = false
        transientError = nil
        cameraService.stopLink()
        locationService.stopUpdating()
        refreshViewState()
    }

    func retry() {
        startGeotagging()
    }

    func sendLocationNow() {
        transientError = nil
        cameraService.sendLocationNow()
        refreshViewState()
    }

    func openSettings() {
        openSettingsAction()
    }

    func requestBackgroundPermission() {
        locationService.requestAlwaysAuthorization()
    }

    @discardableResult
    func applySettings(_ newSettings: LinkSettings) -> Bool {
        guard newSettings != settings else { return true }
        let previous = settings
        do {
            try settingsStore.save(newSettings)
        } catch {
            transientError = "Link settings couldn’t be applied. Your previous settings are still active."
            refreshViewState()
            return false
        }

        settings = newSettings
        transientError = nil
        cameraService.configure(settings: newSettings)
        locationService.configure(settings: newSettings, isForeground: isForeground)

        if newSettings.backgroundLinkEnabled,
           !previous.backgroundLinkEnabled,
           locationService.snapshot.permission.allowsForegroundLocation {
            locationService.startUpdating()
            cameraService.resumeBackgroundLink()
        }
        if !newSettings.backgroundLinkEnabled, !isForeground {
            locationService.stopUpdating()
        }
        scheduleBackgroundRefresh()
        refreshViewState()
        return true
    }

    func scheduleBackgroundRefresh() {
        #if os(iOS)
        guard isProductionRuntime, settings.backgroundLinkEnabled else { return }
        let request = BGAppRefreshTaskRequest(identifier: backgroundRefreshIdentifier)
        request.earliestBeginDate = Date(
            timeIntervalSinceNow: settings.lowPowerModeEnabled ? 15 * 60 : 5 * 60
        )
        do {
            try BGTaskScheduler.shared.submit(request)
        } catch {
            print("Failed to schedule background refresh: \(error.localizedDescription)")
        }
        #endif
    }

    private func beginForegroundLink() {
        guard !pendingStart || locationService.snapshot.permission.allowsForegroundLocation else { return }
        pendingStart = false
        transientError = nil
        locationService.startUpdating()
        cameraService.startForegroundLink()
        refreshViewState()
    }

    private func serviceDidChange() {
        let permission = locationService.snapshot.permission
        if pendingStart, permission.allowsForegroundLocation {
            beginForegroundLink()
            return
        }
        if pendingStart, permission == .denied || permission == .restricted {
            pendingStart = false
            transientError = "Location access is off. Review permission in iOS Settings, then retry."
        } else if permission.allowsForegroundLocation,
                  transientError?.contains("Location access is off") == true {
            transientError = nil
        }
        cameraService.sendLocationIfDue()
        refreshViewState()
    }

    private func refreshViewState() {
        viewState = GeotaggingViewState.make(
            from: Self.makeSnapshot(
                camera: cameraService.snapshot,
                location: locationService.snapshot,
                settings: settings,
                isForeground: isForeground,
                pendingStart: pendingStart,
                transientError: transientError
            ),
            now: now()
        )
    }

    private static func makeSnapshot(
        camera: CameraServiceSnapshot,
        location: LocationServiceSnapshot,
        settings: LinkSettings,
        isForeground: Bool,
        pendingStart: Bool,
        transientError: String?
    ) -> GeotaggingSnapshot {
        GeotaggingSnapshot(
            cameraState: camera.state,
            cameraName: camera.discoveredCameraName,
            targetName: camera.targetName,
            packetsSent: camera.packetsSent,
            lastSentAt: camera.lastSentAt,
            locationPermission: location.permission,
            hasLocation: location.currentLocation != nil,
            horizontalAccuracy: location.currentLocation?.horizontalAccuracy,
            backgroundEnabled: settings.backgroundLinkEnabled,
            isForeground: isForeground,
            pendingReconnectArmed: camera.pendingReconnectArmed,
            transientError: transientError ?? camera.lastError ?? location.lastError,
            isRequestingPermission: pendingStart
        )
    }

    private func registerBackgroundTasks() {
        #if os(iOS)
        guard !didRegisterBackgroundTasks else { return }
        didRegisterBackgroundTasks = BGTaskScheduler.shared.register(
            forTaskWithIdentifier: backgroundRefreshIdentifier,
            using: nil
        ) { [weak self] task in
            guard let refreshTask = task as? BGAppRefreshTask else {
                task.setTaskCompleted(success: false)
                return
            }
            Task { @MainActor in
                self?.handleBackgroundRefresh(refreshTask)
            }
        }
        #endif
    }

    #if os(iOS)
    private func handleBackgroundRefresh(_ task: BGAppRefreshTask) {
        scheduleBackgroundRefresh()
        task.expirationHandler = { [weak self] in
            DispatchQueue.main.async {
                self?.backgroundTaskCompletion?.cancel()
                task.setTaskCompleted(success: false)
            }
        }

        isForeground = false
        locationService.configure(settings: settings, isForeground: false)
        if locationService.snapshot.permission == .always {
            locationService.startUpdating()
            cameraService.resumeBackgroundLink()
            cameraService.sendLocationIfDue()
        }

        let completion = DispatchWorkItem {
            task.setTaskCompleted(success: true)
        }
        backgroundTaskCompletion = completion
        DispatchQueue.main.asyncAfter(deadline: .now() + 20, execute: completion)
    }
    #endif
}
