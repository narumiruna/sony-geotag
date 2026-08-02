import SwiftUI

@main
struct SonyGeoTagApp: App {
    @Environment(\.scenePhase) private var scenePhase
    @StateObject private var appModel: SonyGeoTagAppModel

    init() {
        _appModel = StateObject(wrappedValue: SonyGeoTagAppModel.makeForCurrentProcess())
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
