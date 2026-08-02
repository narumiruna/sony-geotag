import SwiftUI
#if canImport(UIKit)
import UIKit
#endif

struct GeotaggingHomeView<Diagnostics: View>: View {
    let state: GeotaggingViewState
    let settings: LinkSettings
    let perform: (GeotaggingAction) -> Void
    let showSettings: () -> Void
    @ViewBuilder let diagnostics: () -> Diagnostics

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                statusSection
                readinessSection
                preferencesSection
                diagnosticsLink
            }
            .frame(maxWidth: 680, alignment: .leading)
            .padding(.horizontal)
            .padding(.bottom, 28)
        }
        .background(pageBackgroundColor)
    }

    private var statusSection: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Image(systemName: statusSymbol)
                    .font(.title2.weight(.semibold))
                    .foregroundStyle(statusColor)
                    .accessibilityHidden(true)
                Text(state.title)
                    .font(.title2.bold())
                    .fixedSize(horizontal: false, vertical: true)
            }

            Text(state.message)
                .font(.body)
                .foregroundStyle(.primary)
                .fixedSize(horizontal: false, vertical: true)

            if state.showsProgress {
                ProgressView()
                    .accessibilityLabel(state.title)
                    .accessibilityIdentifier("connection-progress")
            }

            if let notice = state.notice {
                VStack(alignment: .leading, spacing: 8) {
                    Label(notice, systemImage: "exclamationmark.triangle.fill")
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(.orange)
                    Text("Foreground geotagging still works. Allow Always Location for background updates.")
                        .font(.footnote)
                        .foregroundStyle(.primary)
                    if let action = state.noticeAction {
                        Button("Allow Background Location") {
                            perform(action)
                        }
                        .buttonStyle(.bordered)
                        .accessibilityIdentifier("background-permission-action")
                    }
                }
                .padding(12)
                .background(Color.orange.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))
            }

            actionButtons
        }
        .sectionSurface()
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("geotagging-status")
    }

    @ViewBuilder
    private var actionButtons: some View {
        if let action = state.primaryAction, let label = state.primaryActionLabel {
            Button {
                perform(action)
            } label: {
                Text(label)
                    .frame(maxWidth: .infinity)
                    .frame(minHeight: 44)
            }
            .buttonStyle(.borderedProminent)
            .tint(.primary)
            .keyboardShortcut(.defaultAction)
            .accessibilityIdentifier("primary-action")
            .focusable()
        }

        if let action = state.secondaryAction, let label = state.secondaryActionLabel {
            Button(label) {
                perform(action)
            }
            .buttonStyle(.bordered)
            .tint(.primary)
            .frame(minHeight: 44)
            .accessibilityIdentifier("secondary-action")
            .focusable()
        }
    }

    private var readinessSection: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("Readiness")
                .font(.headline)
                .padding(.bottom, 6)

            ForEach(Array(state.readiness.enumerated()), id: \.element.id) { index, item in
                readinessRow(item)
                if index < state.readiness.count - 1 {
                    Divider().padding(.leading, 34)
                }
            }
        }
        .sectionSurface()
        .accessibilityIdentifier("readiness")
    }

    private func readinessRow(_ item: ReadinessItem) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: item.symbolName)
                .frame(width: 22, height: 22)
                .foregroundStyle(item.isReady ? Color.green : Color.secondary)
                .accessibilityHidden(true)
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .firstTextBaseline) {
                    Text(item.title)
                    Spacer(minLength: 16)
                    Text(item.detail)
                        .foregroundStyle(.primary)
                        .multilineTextAlignment(.trailing)
                }
                VStack(alignment: .leading, spacing: 3) {
                    Text(item.title)
                    Text(item.detail)
                        .foregroundStyle(.primary)
                }
            }
            .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.vertical, 12)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(item.title), \(item.detail)")
        .accessibilityIdentifier("readiness-\(item.id)")
    }

    private var preferencesSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            Button(action: showSettings) {
                HStack(alignment: .center, spacing: 12) {
                    Image(systemName: "slider.horizontal.3")
                        .frame(width: 22)
                        .accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: 3) {
                        Text("Link Settings")
                            .foregroundStyle(.primary)
                        Text(settings.summary)
                            .font(.subheadline)
                            .foregroundStyle(.primary)
                    }
                    Spacer()
                    Image(systemName: "chevron.right")
                        .font(.caption.bold())
                        .foregroundStyle(.tertiary)
                        .accessibilityHidden(true)
                }
                .contentShape(Rectangle())
                .frame(minHeight: 44)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Link Settings, \(settings.summary)")
            .accessibilityHint("Opens settings with a preview before applying changes")
            .accessibilityIdentifier("link-settings")
            .focusable()
        }
        .sectionSurface()
    }

    private var diagnosticsLink: some View {
        NavigationLink(destination: diagnostics) {
            HStack(spacing: 12) {
                Image(systemName: "stethoscope")
                    .frame(width: 22)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 3) {
                    Text("Diagnostics")
                        .foregroundStyle(.primary)
                    Text("Connection details and debug log")
                        .font(.subheadline)
                        .foregroundStyle(.primary)
                }
                Spacer()
                Image(systemName: "chevron.right")
                    .font(.caption.bold())
                    .foregroundStyle(.tertiary)
                    .accessibilityHidden(true)
            }
            .contentShape(Rectangle())
            .frame(minHeight: 44)
        }
        .buttonStyle(.plain)
        .sectionSurface()
        .accessibilityIdentifier("diagnostics-link")
        .focusable()
    }

    private var statusSymbol: String {
        switch state.phase {
        case .ready:
            "checkmark.circle.fill"
        case .needsAttention:
            "exclamationmark.triangle.fill"
        case .searching, .connecting, .preparing, .sendingFirstLocation, .requestingPermission, .stopping:
            "arrow.triangle.2.circlepath"
        case .waitingInBackground, .waitingForLocation:
            "clock.fill"
        case .notConnected, .stopped:
            "camera"
        }
    }

    private var statusColor: Color {
        switch state.phase {
        case .ready:
            .green
        case .needsAttention:
            .orange
        default:
            .accentColor
        }
    }
}

private var pageBackgroundColor: Color {
    #if canImport(UIKit)
    Color(uiColor: .systemGroupedBackground)
    #else
    Color.secondary.opacity(0.08)
    #endif
}

private var sectionBackgroundColor: Color {
    #if canImport(UIKit)
    Color(uiColor: .secondarySystemGroupedBackground)
    #else
    Color.primary.opacity(0.05)
    #endif
}

private extension View {
    func sectionSurface() -> some View {
        padding(16)
            .background(sectionBackgroundColor, in: RoundedRectangle(cornerRadius: 16))
    }
}
