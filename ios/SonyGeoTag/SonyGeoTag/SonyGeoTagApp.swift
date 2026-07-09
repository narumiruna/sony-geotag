#if os(iOS)
import BackgroundTasks
#endif
import SwiftUI

@main
struct SonyGeoTagApp: App {
    @Environment(\.scenePhase) private var scenePhase
    @StateObject private var appModel = SonyGeoTagAppModel()

    var body: some Scene {
        WindowGroup {
            ContentView(
                locationProvider: appModel.locationProvider,
                cameraManager: appModel.cameraManager
            )
            .onAppear {
                appModel.prepareRuntimeHooks()
                appModel.bootstrapBackgroundLinkIfNeeded(isForeground: true)
            }
            .onChange(of: scenePhase) { _, newPhase in
                if newPhase != .active {
                    appModel.scheduleBackgroundRefresh()
                }
            }
        }
    }
}

@MainActor
final class SonyGeoTagAppModel: ObservableObject {
    let locationProvider = LocationProvider()
    let cameraManager = CameraBLEManager()

    private let backgroundRefreshIdentifier = "com.narumi.SonyGeoTag.refresh"
    private var didRegisterBackgroundTasks = false
    private var backgroundTaskCompletion: DispatchWorkItem?

    init() {
        registerBackgroundTasks()
        prepareRuntimeHooks()
        bootstrapBackgroundLinkIfNeeded(isForeground: false)
    }

    func prepareRuntimeHooks() {
        locationProvider.onLocationUpdate = { [weak cameraManager] _ in
            cameraManager?.sendLocationIfDue()
        }
        cameraManager.setLocationProvider { [weak locationProvider] in
            locationProvider?.currentLocation
        }
    }

    func bootstrapBackgroundLinkIfNeeded(isForeground: Bool) {
        guard cameraManager.backgroundLinkEnabled else { return }
        locationProvider.configure(
            backgroundLinkEnabled: true,
            lowPowerModeEnabled: cameraManager.lowPowerModeEnabled,
            isForeground: isForeground
        )
        locationProvider.startUpdating()
        cameraManager.resumeBackgroundLink { [weak locationProvider] in
            locationProvider?.currentLocation
        }
        scheduleBackgroundRefresh()
    }

    func scheduleBackgroundRefresh() {
        #if os(iOS)
        guard cameraManager.backgroundLinkEnabled else { return }

        let request = BGAppRefreshTaskRequest(identifier: backgroundRefreshIdentifier)
        request.earliestBeginDate = Date(timeIntervalSinceNow: cameraManager.lowPowerModeEnabled ? 15 * 60 : 5 * 60)

        do {
            try BGTaskScheduler.shared.submit(request)
        } catch {
            print("Failed to schedule background refresh: \(error.localizedDescription)")
        }
        #endif
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

        locationProvider.configure(
            backgroundLinkEnabled: cameraManager.backgroundLinkEnabled,
            lowPowerModeEnabled: cameraManager.lowPowerModeEnabled,
            isForeground: false
        )
        locationProvider.startUpdating()
        cameraManager.resumeBackgroundLink { [weak locationProvider] in
            locationProvider?.currentLocation
        }
        cameraManager.sendLocationIfDue()

        let completion = DispatchWorkItem {
            task.setTaskCompleted(success: true)
        }
        backgroundTaskCompletion = completion
        DispatchQueue.main.asyncAfter(deadline: .now() + 20, execute: completion)
    }
    #endif
}
