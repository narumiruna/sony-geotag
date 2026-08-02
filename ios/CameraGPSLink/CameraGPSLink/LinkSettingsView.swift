import SwiftUI

struct LinkSettingsView: View {
    @Environment(\.dismiss) private var dismiss
    let current: LinkSettings
    let apply: (LinkSettings) -> Bool

    @State private var draft: LinkSettingsDraft
    @State private var applyError: String?
    @State private var isApplying = false

    init(current: LinkSettings, apply: @escaping (LinkSettings) -> Bool) {
        self.current = current
        self.apply = apply
        _draft = State(initialValue: LinkSettingsDraft(current: current))
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Picker("Connection Availability", selection: $draft.value.connectionAvailability) {
                        ForEach(ConnectionAvailability.allCases) { option in
                            Text(option.label).tag(option)
                        }
                    }
                    .pickerStyle(.inline)
                    .accessibilityIdentifier("connection-availability")
                    .focusable()
                } header: {
                    Text("Connection Availability")
                }

                Section {
                    Picker("Location Updates", selection: $draft.value.locationUpdates) {
                        ForEach(LocationUpdateMode.allCases) { option in
                            Text(option.label).tag(option)
                        }
                    }
                    .pickerStyle(.inline)
                    .accessibilityIdentifier("location-updates")
                    .focusable()
                } header: {
                    Text("Location Updates")
                }

                Section("Effect Preview") {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(draft.value.summary)
                            .font(.headline)
                        Text(draft.value.effectPreview)
                            .foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .accessibilityElement(children: .combine)
                    .accessibilityLabel("Effect preview. \(draft.value.summary). \(draft.value.effectPreview)")
                    .accessibilityIdentifier("settings-preview")
                }

                if let applyError {
                    Section {
                        Label(applyError, systemImage: "exclamationmark.triangle.fill")
                            .foregroundStyle(.red)
                            .fixedSize(horizontal: false, vertical: true)
                            .accessibilityIdentifier("settings-error")
                    }
                }
            }
            .navigationTitle("Link Settings")
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .interactiveDismissDisabled(isApplying)
            .onKeyPress(.escape) {
                guard !isApplying else { return .ignored }
                cancelDraft()
                return .handled
            }
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") {
                        cancelDraft()
                    }
                    .keyboardShortcut(.cancelAction)
                    .disabled(isApplying)
                    .accessibilityIdentifier("settings-cancel")
                    .focusable()
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Apply") {
                        applyDraft()
                    }
                    .keyboardShortcut(.defaultAction)
                    .disabled(!draft.hasChanges || isApplying)
                    .accessibilityIdentifier("settings-apply")
                    .focusable()
                }
            }
        }
    }

    private func cancelDraft() {
        draft.cancel()
        dismiss()
    }

    private func applyDraft() {
        isApplying = true
        applyError = nil
        if apply(draft.value) {
            dismiss()
        } else {
            applyError = "Changes couldn’t be applied. Your previous settings are still active."
            isApplying = false
        }
    }
}
