import SwiftUI

@main
struct CameraGPSLinkApp: App {
    @Environment(\.scenePhase) private var scenePhase
    @StateObject private var appModel: CameraGPSLinkAppModel

    init() {
        _appModel = StateObject(wrappedValue: CameraGPSLinkAppModel.makeForCurrentProcess())
    }

    var body: some Scene {
        WindowGroup {
            ContentView(appModel: appModel)
                .onAppear {
                    appModel.handleScenePhase(isForeground: true)
                }
                .onChange(of: scenePhase) { _, newPhase in
                    let foreground = newPhase == .active
                    appModel.handleScenePhase(isForeground: foreground)
                    if !foreground {
                        appModel.scheduleBackgroundRefresh()
                    }
                }
        }
    }
}
