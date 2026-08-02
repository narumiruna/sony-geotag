import SwiftUI
#if canImport(UIKit)
import UIKit
#endif

struct ContentView: View {
    @ObservedObject var appModel: CameraGPSLinkAppModel
    @State private var showsSettings = false

    var body: some View {
        NavigationStack {
            GeotaggingHomeView(
                state: appModel.viewState,
                settings: appModel.settings,
                perform: appModel.perform,
                showSettings: { showsSettings = true },
                diagnostics: {
                    DiagnosticsView(appModel: appModel)
                }
            )
            .navigationTitle("Camera GPS Link")
            .sheet(isPresented: $showsSettings) {
                LinkSettingsView(current: appModel.settings) { settings in
                    appModel.applySettings(settings)
                }
            }
            .onChange(of: appModel.viewState.phase) { _, phase in
                announce(phase)
            }
        }
    }

    private func announce(_ phase: GeotaggingPhase) {
        #if canImport(UIKit)
        guard UIAccessibility.isVoiceOverRunning else { return }
        switch phase {
        case .ready, .needsAttention, .stopped:
            UIAccessibility.post(notification: .announcement, argument: appModel.viewState.title)
        default:
            break
        }
        #endif
    }
}
