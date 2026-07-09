import SwiftUI

@main
struct SonyGeoTagApp: App {
    @StateObject private var locationProvider = LocationProvider()
    @StateObject private var cameraManager = CameraBLEManager()

    var body: some Scene {
        WindowGroup {
            ContentView(locationProvider: locationProvider, cameraManager: cameraManager)
                .onAppear {
                    locationProvider.onLocationUpdate = { _ in
                        cameraManager.sendLocationIfDue()
                    }
                }
        }
    }
}
