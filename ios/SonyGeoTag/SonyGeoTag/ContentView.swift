import SwiftUI
#if canImport(UIKit)
import UIKit
#endif

struct ContentView: View {
    @Environment(\.scenePhase) private var scenePhase
    @AppStorage("backgroundLinkEnabled") private var backgroundLinkEnabled = false
    @AppStorage("lowPowerModeEnabled") private var lowPowerModeEnabled = true

    @ObservedObject private var locationProvider: LocationProvider
    @ObservedObject private var cameraManager: CameraBLEManager
    @State private var didCopyDebugLog = false

    init(locationProvider: LocationProvider, cameraManager: CameraBLEManager) {
        self.locationProvider = locationProvider
        self.cameraManager = cameraManager
    }

    var body: some View {
        NavigationStack {
            Form {
                cameraSection
                locationSection
                backgroundSection
                controlsSection
                logSection
            }
            .navigationTitle("Sony GeoTag")
            .toolbar {
                ToolbarItem(placement: .automatic) {
                    statusBadge
                }
            }
            .onAppear {
                applyRuntimeSettings(autoResume: backgroundLinkEnabled)
            }
            .onChange(of: backgroundLinkEnabled) { _, newValue in
                applyRuntimeSettings(autoResume: newValue)
            }
            .onChange(of: lowPowerModeEnabled) { _, _ in
                applyRuntimeSettings(autoResume: false)
            }
            .onChange(of: scenePhase) { _, _ in
                applyRuntimeSettings(autoResume: backgroundLinkEnabled)
            }
            .onChange(of: locationProvider.currentLocation?.timestamp) { _, _ in
                cameraManager.sendLocationIfDue()
            }
        }
    }

    private var cameraSection: some View {
        Section("Camera") {
            LabeledContent("Target", value: cameraManager.targetName)
            LabeledContent("State", value: cameraManager.state.label)
            if let discoveredCameraName = cameraManager.discoveredCameraName {
                LabeledContent("Found", value: discoveredCameraName)
            }
            LabeledContent("Packets sent", value: String(cameraManager.packetsSent))
            if cameraManager.state == .linked && cameraManager.packetsSent == 0 {
                Text("Wait for Packets sent > 0 before taking a photo.")
                    .foregroundStyle(.orange)
            }
            LabeledContent("DD11 timezone", value: cameraManager.includeTimezone ? "95-byte packet" : "91-byte packet")
            if let dd21ConfigHex = cameraManager.dd21ConfigHex {
                LabeledContent("DD21", value: dd21ConfigHex)
                    .font(.caption)
            }
            LabeledContent("DD11 interval", value: "\(Int(cameraManager.updateInterval))s")
            if let rememberedPeripheralID = cameraManager.rememberedPeripheralID {
                LabeledContent("Remembered", value: String(rememberedPeripheralID.prefix(8)))
            }
            if let lastSentAt = cameraManager.lastSentAt {
                LabeledContent("Last sent", value: lastSentAt.formatted(date: .omitted, time: .standard))
            }
            if let lastError = cameraManager.lastError {
                Text(lastError)
                    .foregroundStyle(.red)
            }
        }
    }

    private var locationSection: some View {
        Section("iPhone GPS") {
            LabeledContent("Permission", value: locationProvider.statusLabel)
            LabeledContent("Mode", value: locationProvider.updateModeLabel)
            LabeledContent("Coordinate", value: locationProvider.coordinateLabel)
            LabeledContent("Accuracy", value: locationProvider.accuracyLabel)
            if let lastError = locationProvider.lastError {
                Text(lastError)
                    .foregroundStyle(.red)
            }
        }
    }

    private var backgroundSection: some View {
        Section("Background") {
            Toggle("Background Link", isOn: $backgroundLinkEnabled)
            Toggle("Low Power Mode", isOn: $lowPowerModeEnabled)

            if backgroundLinkEnabled {
                Text("Background Link needs Always Location permission and an initial successful camera connection. iOS may stop it after force-quit or throttle background scans.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            if lowPowerModeEnabled {
                Text("Low Power Mode lowers GPS accuracy and sends DD11 less often to reduce battery use.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private var controlsSection: some View {
        Section("Controls") {
            Button(backgroundLinkEnabled ? "Request Always Location Permission" : "Request Location Permission") {
                locationProvider.requestAuthorization(preferAlways: backgroundLinkEnabled)
            }

            Button("Start Location Link") {
                applyRuntimeSettings(autoResume: false)
                locationProvider.startUpdating()
                cameraManager.startLink {
                    locationProvider.currentLocation
                }
            }
            .disabled(!cameraManager.canStart)

            Button("Send Location Now") {
                cameraManager.sendLocationNow()
            }
            .disabled(cameraManager.state != .linked)

            Button("Stop Location Link", role: .destructive) {
                cameraManager.stopLink()
                locationProvider.stopUpdating()
            }
            .disabled(cameraManager.canStart)
        }
    }

    private var logSection: some View {
        Section("Debug Log") {
            Button(didCopyDebugLog ? "Copied Debug Log" : "Copy Debug Log") {
                copyDebugLog()
            }
            .disabled(cameraManager.logLines.isEmpty)

            if cameraManager.logLines.isEmpty {
                Text("No log yet")
                    .foregroundStyle(.secondary)
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 4) {
                        ForEach(Array(cameraManager.logLines.enumerated()), id: \.offset) { _, line in
                            Text(line)
                                .font(.caption.monospaced())
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                }
                .frame(minHeight: 160)
            }
        }
    }

    private var statusBadge: some View {
        Text(cameraManager.state == .linked ? "Linked" : cameraManager.state.label)
            .font(.caption)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(cameraManager.state == .linked ? Color.green.opacity(0.2) : Color.secondary.opacity(0.15))
            .clipShape(Capsule())
    }

    private func copyDebugLog() {
        let text = cameraManager.logLines.joined(separator: "\n")
        #if canImport(UIKit)
        UIPasteboard.general.string = text
        #endif
        didCopyDebugLog = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
            didCopyDebugLog = false
        }
    }

    private func applyRuntimeSettings(autoResume: Bool) {
        let isForeground = scenePhase == .active
        locationProvider.configure(
            backgroundLinkEnabled: backgroundLinkEnabled,
            lowPowerModeEnabled: lowPowerModeEnabled,
            isForeground: isForeground
        )
        cameraManager.configure(
            backgroundLinkEnabled: backgroundLinkEnabled,
            lowPowerModeEnabled: lowPowerModeEnabled
        )

        guard autoResume, backgroundLinkEnabled else { return }
        locationProvider.startUpdating()
        cameraManager.resumeBackgroundLink {
            locationProvider.currentLocation
        }
    }
}
