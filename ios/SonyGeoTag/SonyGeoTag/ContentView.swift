import SwiftUI
#if canImport(UIKit)
import UIKit
#endif

struct ContentView: View {
    @StateObject private var locationProvider = LocationProvider()
    @StateObject private var cameraManager = CameraBLEManager()
    @State private var didCopyDebugLog = false

    var body: some View {
        NavigationStack {
            Form {
                cameraSection
                locationSection
                controlsSection
                logSection
            }
            .navigationTitle("Sony GeoTag")
            .toolbar {
                ToolbarItem(placement: .automatic) {
                    statusBadge
                }
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
            LabeledContent("Coordinate", value: locationProvider.coordinateLabel)
            LabeledContent("Accuracy", value: locationProvider.accuracyLabel)
            if let lastError = locationProvider.lastError {
                Text(lastError)
                    .foregroundStyle(.red)
            }
        }
    }

    private var controlsSection: some View {
        Section("Controls") {
            Button("Request Location Permission") {
                locationProvider.requestAuthorization()
            }

            Button("Start Location Link") {
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
}
